#!/usr/bin/env python3
"""Independent raw-data verification; does not import the weather evaluator."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


EXPECTED_PLAN = "30a34892cd467ddab0dfa1436608ed69ca73364869043949e2e893dd291d8277"
NY = ZoneInfo("America/New_York")


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def dt(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def decimal(price):
    return 1 + (price / 100 if price > 0 else 100 / abs(price))


def first_captures(quotes, cohort):
    q = quotes.copy()
    q["observed_at"] = pd.to_datetime(q.observed_at, utc=True)
    q["kickoff"] = pd.to_datetime(q.kickoff, utc=True)
    seen, kick = q.observed_at.dt.tz_convert(NY), q.kickoff.dt.tz_convert(NY)
    valid = (q.season.eq(2026) & q.receipt_hash_matches.eq(True) & q.observed_at.lt(q.kickoff)
        & seen.dt.date.eq(kick.dt.date) & (seen.dt.hour * 60 + seen.dt.minute).ge(390)
        & np.isfinite(q.line) & q.line.gt(0) & (q.line * 2).eq(np.floor(q.line * 2))
        & np.isfinite(q.over_price) & np.isfinite(q.under_price) & q.over_price.abs().ge(100) & q.under_price.abs().ge(100))
    if cohort == "two_book_the_odds_api":
        required = {"draftkings", "fanduel"}
        age = (q.observed_at - pd.to_datetime(q.market_updated_at, utc=True)).dt.total_seconds()
        valid &= q.source.eq("the_odds_api") & q.book.isin(required) & age.between(0, 3600)
    else:
        required = {"draftkings"}
        valid &= q.source.eq("espn_scoreboard") & q.book.eq("draftkings") & q.source_prestate.str.startswith("explicit_espn_pre;")
    q = q.loc[valid]
    groups = []
    for key, group in q.groupby(["game_id", "observed_at", "source_sha256", "archived_event_id"], sort=True):
        if set(group.book) != required:
            continue
        if any(len(book[["line", "over_price", "under_price", "market_updated_at", "kickoff", "home_id", "away_id"]].drop_duplicates()) != 1
               for _, book in group.groupby("book")):
            continue
        if len(group[["kickoff", "home_id", "away_id"]].drop_duplicates()) != 1:
            continue
        groups.append((key, group.drop_duplicates("book")))
    first = {}
    for key, group in sorted(groups, key=lambda item: tuple(str(v) for v in item[0])):
        first.setdefault(int(key[0]), (key, group))
    return first


def audit(root):
    report_path = root / "reports/weather_2026_replay_results.json"
    if not report_path.exists():
        raise ValueError("Run the frozen evaluator before this independent audit")
    report = json.loads(report_path.read_text())
    plan = json.loads((root / "reports/weather_2026_replay_plan.json").read_text())
    claimed = plan.pop("plan_sha256")
    assert claimed == digest(plan) == EXPECTED_PLAN == report["plan_sha256"]
    assert plan["thresholds"] == {"wind_mph_above": 7.78, "temperature_f_below": 64.81, "relative_humidity_percent_above": 56.8}
    for name, path in {"quotes": "data/raw/alternative/archived_2026_paired_quotes.parquet",
                       "quote_manifest": "data/raw/alternative/archived_2026_quote_manifest.json",
                       "venue_catalog": "data/models/weather_venues_v1.json",
                       "venue_context": "data/raw/weather_replay_2026/venue_context.json"}.items():
        assert hashlib.sha256((root / path).read_bytes()).hexdigest() == plan["source_hashes"][name]
    source_path = root / report["outcome_source"]
    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == report["outcome_source_sha256"]
    outcomes = pd.read_parquet(source_path, columns=["game_id", "home_score", "away_score", "status"]).set_index("game_id")
    assert outcomes.index.is_unique
    computed = pd.read_parquet(root / "data/raw/weather_replay_2026/game_results.parquet").set_index(["cohort", "game_id"])
    assert computed.index.is_unique
    raw_quotes = pd.read_parquet(root / "data/raw/alternative/archived_2026_paired_quotes.parquet")
    first = {cohort: first_captures(raw_quotes, cohort) for cohort in report["cohorts"]}
    payloads, request_count = {}, 0
    for request in plan["requests"]:
        path = root / "data/raw/weather_replay_2026/batches" / (request["request_id"] + ".json.gz")
        with gzip.open(path, "rt") as handle:
            archive = json.load(handle)
        assert archive["request_id"] == request["request_id"]
        assert archive["source_url"] == request["source_url"] == "https://previous-runs-api.open-meteo.com/v1/forecast"
        assert archive["parameters"] == request["parameters"]
        assert archive["payload_sha256"] == digest(archive["payload"])
        assert request["parameters"]["models"] == "gfs_global"
        assert request["parameters"]["hourly"] == "temperature_2m_previous_day2,relative_humidity_2m_previous_day2,wind_speed_10m_previous_day2"
        array = archive["payload"] if isinstance(archive["payload"], list) else [archive["payload"]]
        assert len(array) == len(request["game_ids"])
        for index, (game_id, body) in enumerate(zip(request["game_ids"], array)):
            assert int(body.get("location_id", index)) == index
            assert game_id not in payloads
            payloads[game_id] = body, archive["observed_at"]
        request_count += 1
    rows, flags_checked, temporal_proxies = [], 0, 0
    for game in plan["games"]:
        key, source = first[game["cohort"]][int(game["game_id"])]
        assert pd.Timestamp(game["decision_time"]) == key[1]
        assert game["source_sha256"] == key[2]
        reference = median(source.line)
        options = [(r.line, decimal(r.under_price), r.book, r.under_price) for r in source.itertuples()
                   if r.line >= reference and decimal(r.under_price) + 1e-12 >= 1 + 100 / 110]
        chosen = max(options) if options else None
        assert game["price_eligible"] == (chosen is not None)
        assert game["reference_total"] == reference
        if chosen:
            saved = game["selected_quote"]
            assert (saved["line"], saved["under_decimal_odds"], saved["book"], saved["under_american_odds"]) == chosen
        kickoff, decision = dt(game["kickoff"]), dt(game["decision_time"])
        assert dt(game["venue_evidence"]["observed_at"]) <= decision < kickoff
        assert dt(game["roof_evidence"]["observed_at"]) <= decision
        assert game["venue_evidence"]["state"] == "pre"
        assert game["venue_evidence"]["neutral_site"] is False and game["roof_evidence"]["indoor"] is False
        assert game["venue_evidence"]["venue_id"] == game["venue"]["venue_id"] == game["roof_evidence"]["venue_id"]
        hour = kickoff.replace(minute=0, second=0, microsecond=0)
        cutoff = datetime.combine(kickoff.astimezone(NY).date(), datetime.min.time(), NY).replace(hour=6, minute=30)
        assert hour + timedelta(hours=3 - 48 + 6) <= cutoff <= decision
        body, observed = payloads[game["game_id"]]
        temporal_proxies += int(dt(observed) > decision)
        assert body["utc_offset_seconds"] == 0
        assert abs(body["latitude"] - game["venue"]["latitude"]) <= .5
        assert abs(body["longitude"] - game["venue"]["longitude"]) <= .5
        hourly, units = body["hourly"], body["hourly_units"]
        tn, hn, wn = [name + "_previous_day2" for name in ("temperature_2m", "relative_humidity_2m", "wind_speed_10m")]
        assert units[tn] == "°F" and units[hn] == "%" and units[wn] in {"mph", "mp/h"}
        indices = [hourly["time"].index((hour + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M")) for h in range(4)]
        temperature, humidity = float(hourly[tn][indices[0]]), float(hourly[hn][indices[0]])
        winds = [float(hourly[wn][i]) for i in indices]
        assert all(math.isfinite(v) for v in [temperature, humidity, *winds])
        assert 0 <= humidity <= 100 and min(winds) >= 0
        wind = sum(winds) / 4
        flag = wind > 7.78 and temperature < 64.81 and humidity > 56.8
        evaluated = computed.loc[(game["cohort"], int(game["game_id"]))]
        assert evaluated.status == "shadow_only" and bool(evaluated.shadow_under_flag) == flag
        for field, value in [("temperature_f", temperature), ("relative_humidity_percent", humidity), ("wind_mph", wind)]:
            assert math.isclose(evaluated[field], value, abs_tol=1e-10)
        flags_checked += 1
        if not chosen:
            continue
        outcome = outcomes.loc[int(game["game_id"])]
        assert outcome.status == "STATUS_FINAL"
        actual = float(outcome.home_score) + float(outcome.away_score)
        assert actual == evaluated.actual_total and chosen[0] == evaluated.line and math.isclose(chosen[1], evaluated.decimal_odds)
        profit = chosen[1] - 1 if actual < chosen[0] else -1. if actual > chosen[0] else 0.
        rows.append({"cohort": game["cohort"], "game_id": game["game_id"], "selected": flag, "profit": profit})
    stats = {}
    for cohort in report["cohorts"]:
        sample = [r for r in rows if r["cohort"] == cohort]
        subsets = {"rule": [r for r in sample if r["selected"]], "all_under_same_price_and_weather_coverage": sample}
        stats[cohort] = {}
        for label, group in subsets.items():
            values = [r["profit"] for r in group]
            result = {"bets": len(values), "wins": sum(v > 0 for v in values), "losses": sum(v < 0 for v in values),
                      "pushes": sum(v == 0 for v in values), "profit_units": sum(values), "roi": sum(values) / len(values) if values else None}
            for field, value in result.items():
                saved = report["cohorts"][cohort][label][field]
                assert value == saved if value is None or isinstance(value, int) else math.isclose(value, saved, abs_tol=1e-10)
            stats[cohort][label] = result
    audit_report = {"status": "passed", "audited_at": datetime.now(timezone.utc).isoformat(), "plan_sha256": EXPECTED_PLAN,
        "forecast_requests_checked": request_count, "unique_game_forecasts_checked": len(payloads),
        "cohort_weather_rows_checked": flags_checked, "price_eligible_settlements_checked": len(rows),
        "later_price_substitutions": 0, "forecast_rows_using_historical_availability_proxy": temporal_proxies,
        "cohorts": stats, "method": "Independent script, no weather-evaluator imports: raw archive fields, fixed comparisons, original quote first-capture chronology, raw final scores and actual-price settlement.",
        "limitations": "Hash/consistency agreement does not independently attest old quote or forecast publication time. Zero rule bets cannot estimate rule profitability."}
    destination = root / "reports/weather_2026_replay_audit.json"
    destination.write_text(json.dumps(audit_report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(audit_report, indent=2))
    return audit_report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1] / "model")
    audit(parser.parse_args().root)
