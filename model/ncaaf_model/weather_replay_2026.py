"""Locked 2026 archived-price replay of the unchanged published weather rule.

--plan reads quote/venue identity only; --fetch retrieves the locked requests;
--evaluate is the first stage that reads forecast values and final scores.
This is retrospective validation, not a prospectively registered bet record.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
from statistics import median
import time

import numpy as np
import pandas as pd
import requests

from .weather_candidate import NY, target_flags
from .weather_research import _digest, _write, normalize_batch, read_batch
from .weather_shadow import MODEL, PREVIOUS_URL, THRESHOLDS, VARIABLES, Venue, weather_flag

VERSION = "weather-2026-archived-price-replay-v1"
PLAN_PATH = "reports/weather_2026_replay_plan.json"
RAW = "data/raw/weather_replay_2026"
QUOTES = "data/raw/alternative/archived_2026_paired_quotes.parquet"
QUOTE_MANIFEST = "data/raw/alternative/archived_2026_quote_manifest.json"
CATALOG = "data/models/weather_venues_v1.json"
PRIMARY = "two_book_the_odds_api"
SECONDARY = "single_draftkings_espn_sensitivity"
MIN_DECIMAL = 1 + 100 / 110
POLICY = {
    "capture": "First same-Eastern-game-day receipt at/after06:30 and strictly before kickoff with qualifying source/books; ties broken by archive hash, never price/weather/outcome. Do not replace a first capture that fails the Under price policy with a later capture.",
    "primary": "TheOddsAPI DraftKings+FanDuel in one archive and receipt; both paired full-game prices valid; each market update0–60minutes before receipt.",
    "secondary": "ESPN DraftKings100 paired totals in explicit pregame state; one book only; no market-update timestamp guarantee; sensitivity, never pooled with primary.",
    "price": "At first capture, Under line>=median of cohort books and decimal payout>=1+100/110; choose highest line, then highest payout, then sportsbook key.",
    "weather": "Unchanged GFS previous_day2, fixed four-hour wind mean, kickoff-hour temperature/humidity;6h publication buffer against game-day06:30Eastern. Actual quote receipt remains the decision timestamp.",
    "venue": "Exact ESPN event/team IDs/kickoff and actual venue ID in the frozen100-ID coordinate catalog; nonneutral and explicitly outdoor. Prefer latest pregame metadata received by quote time. A later same-venue roof value is an explicitly labeled stability assumption, never contemporaneous proof.",
    "stake": "Hypothetical flat one-unit risk at actual archived Under payout; integer pushes refund. No probability, EV, Kelly or execution claim.",
}
QUOTE_COLUMNS = ["book", "line", "over_price", "under_price", "market_updated_at", "archived_event_id",
    "source_prestate", "game_id", "season", "week", "observed_at", "kickoff", "home_team", "away_team",
    "home_id", "away_id", "source", "source_path", "source_sha256", "receipt_evidence", "receipt_hash_matches"]


def _timestamp(value):
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    return None if pd.isna(parsed) else parsed.to_pydatetime()


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _decimal(value):
    try:
        value = float(value)
        if not math.isfinite(value) or abs(value) < 100:
            return None
        return 1 + (value / 100 if value > 0 else 100 / abs(value))
    except (ValueError, TypeError):
        return None


def select_captures(frame: pd.DataFrame) -> tuple[list[dict], dict]:
    """No forecast/outcome input. Lock the earliest coherent quote capture."""
    groups, errors = defaultdict(list), Counter()
    for quote in frame.to_dict("records"):
        observed, kickoff = _timestamp(quote["observed_at"]), _timestamp(quote["kickoff"])
        if int(quote["season"]) != 2026 or observed is None or kickoff is None:
            continue
        local = observed.astimezone(NY)
        if observed >= kickoff or local.date() != kickoff.astimezone(NY).date() or (local.hour, local.minute) < (6, 30):
            continue
        if quote.get("receipt_hash_matches") is not True:
            errors["unverified_receipt_hash"] += 1
            continue
        source, book = quote["source"], quote["book"]
        if source == "the_odds_api" and book in {"draftkings", "fanduel"}:
            cohort = PRIMARY
            updated = _timestamp(quote["market_updated_at"])
            if updated is None or not 0 <= (observed - updated).total_seconds() <= 3600:
                errors["primary_missing_stale_or_future_market_update"] += 1
                continue
        elif source == "espn_scoreboard" and book == "draftkings" and str(quote["source_prestate"]).startswith("explicit_espn_pre;"):
            cohort, updated = SECONDARY, None
        else:
            continue
        over, under = _decimal(quote["over_price"]), _decimal(quote["under_price"])
        try:
            line = float(quote["line"])
        except (ValueError, TypeError):
            line = float("nan")
        if over is None or under is None or not math.isfinite(line) or line <= 0 or line * 2 != int(line * 2):
            errors["invalid_paired_price_or_line"] += 1
            continue
        row = {"game_id": str(int(quote["game_id"])), "season": 2026, "week": int(quote["week"]),
            "home_team": quote["home_team"], "away_team": quote["away_team"], "home_id": str(int(quote["home_id"])),
            "away_id": str(int(quote["away_id"])), "kickoff": kickoff.isoformat(), "observed_at": observed.isoformat(),
            "source": source, "source_archive": Path(quote["source_path"]).name, "source_sha256": quote["source_sha256"],
            "archived_event_id": str(quote["archived_event_id"]), "source_prestate": quote["source_prestate"],
            "receipt_evidence": quote["receipt_evidence"], "cohort": cohort, "book": book, "line": line,
            "over_decimal_odds": over, "under_decimal_odds": under, "under_american_odds": float(quote["under_price"]),
            "market_updated_at": updated.isoformat() if updated else None}
        key = cohort, row["game_id"], row["observed_at"], row["source_sha256"], row["archived_event_id"]
        groups[key].append(row)
    first = {}
    for key, rows in sorted(groups.items()):
        books = defaultdict(list)
        for row in rows:
            books[row["book"]].append(row)
        required = {"draftkings", "fanduel"} if key[0] == PRIMARY else {"draftkings"}
        if set(books) != required:
            errors["capture_missing_required_book"] += 1
            continue
        if any(len({_digest(row) for row in values}) != 1 for values in books.values()):
            errors["conflicting_same_book_capture"] += 1
            continue
        coherent = [books[book][0] for book in sorted(books)]
        if len({(r["kickoff"], r["home_id"], r["away_id"]) for r in coherent}) != 1:
            errors["capture_identity_conflict"] += 1
            continue
        pair = key[0], key[1]
        capture_order = key[2], key[3], key[4]
        if pair not in first or capture_order < first[pair][0]:
            first[pair] = capture_order, coherent
    selected = []
    for (cohort, game_id), (_, rows) in sorted(first.items()):
        base = rows[0]
        reference = median(row["line"] for row in rows)
        options = [row for row in rows if row["line"] >= reference and row["under_decimal_odds"] + 1e-12 >= MIN_DECIMAL]
        choice = max(options, key=lambda r: (r["line"], r["under_decimal_odds"], r["book"])) if options else None
        selected.append({key: base[key] for key in ["game_id", "season", "week", "home_team", "away_team", "home_id", "away_id",
            "kickoff", "observed_at", "source", "source_archive", "source_sha256", "receipt_evidence", "cohort"]} |
            {"quote_capture_id": _digest(rows), "quotes": rows, "reference_total": reference, "price_eligible": choice is not None,
             "selected_quote": choice, "decision_time": base["observed_at"],
             "actual_quote_time_eastern": _timestamp(base["observed_at"]).astimezone(NY).isoformat(),
             "hours_to_kickoff": (_timestamp(base["kickoff"]) - _timestamp(base["observed_at"])).total_seconds() / 3600})
    return selected, dict(errors)


def read_venue_context(source_root: Path, game_ids: set[str]) -> list[dict]:
    """Extract only identity/venue/status from existing raw scoreboards, no scores."""
    records = []
    for path in sorted((source_root / "data/raw/espn_scoreboard").glob("*.json")):
        if ".meta." in path.name:
            continue
        meta_path = path.with_name(path.stem + ".meta.json")
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text())
        observed = _timestamp(meta.get("retrieved_at"))
        if observed is None or observed.year != 2026 or meta.get("sha256") != _sha(path):
            continue
        body = json.loads(path.read_text())
        for event in body.get("events", []):
            if str(event.get("id")) not in game_ids:
                continue
            for comp in event.get("competitions", []):
                if str(comp.get("id")) != str(event.get("id")):
                    continue
                sides = {r.get("homeAway"): r.get("team", {}) for r in comp.get("competitors", [])}
                venue = comp.get("venue") or {}
                records.append({"game_id": str(event["id"]), "kickoff": comp.get("date"),
                    "home_id": str(sides.get("home", {}).get("id", "")), "away_id": str(sides.get("away", {}).get("id", "")),
                    "neutral_site": comp.get("neutralSite"), "state": (comp.get("status") or event.get("status") or {}).get("type", {}).get("state"),
                    "venue_id": str(venue.get("id", "")), "venue_name": venue.get("fullName"), "indoor": venue.get("indoor"),
                    "observed_at": observed.isoformat(), "source_archive": "espn_scoreboard/" + path.name,
                    "source_sha256": meta["sha256"], "source_url": meta.get("source_url"), "metadata_sha256": _sha(meta_path)})
    return records


def match_context(capture: dict, records: list[dict], catalog: dict) -> tuple[dict | None, str]:
    kickoff, decision = _timestamp(capture["kickoff"]), _timestamp(capture["decision_time"])
    rows = [r for r in records if r["game_id"] == capture["game_id"] and r["home_id"] == capture["home_id"]
        and r["away_id"] == capture["away_id"] and _timestamp(r["kickoff"]) == kickoff]
    if not rows:
        return None, "exact_game_venue_context_missing"
    # First prefer information already archived by the quote; otherwise use
    # the earliest later metadata with an explicit retrospective assumption.
    prior = [r for r in rows if _timestamp(r["observed_at"]) <= decision and r["state"] == "pre"]
    if prior:
        chosen = max(prior, key=lambda r: (r["observed_at"], r["source_archive"]))
        timing = "pregame_context_received_by_quote_time"
    else:
        chosen = min(rows, key=lambda r: (r["observed_at"], r["source_archive"]))
        timing = "later_context_venue_and_roof_stability_assumption_not_contemporaneous_proof"
    if chosen["neutral_site"] is not False:
        return None, "neutral_or_unknown_neutral"
    if chosen["venue_id"] not in catalog["venues"]:
        return None, "venue_not_in_frozen100_ID_catalog"
    roof = chosen
    if chosen["indoor"] is None:
        known = [r for r in rows if r["venue_id"] == chosen["venue_id"] and r["indoor"] is not None]
        if not known or any(r["indoor"] is not False for r in known):
            return None, "indoor_or_unknown_roof"
        roof = min(known, key=lambda r: (r["observed_at"], r["source_archive"]))
        timing = "separate_same_venue_roof_stability_assumption_not_contemporaneous_proof"
    if roof["indoor"] is not False:
        return None, "indoor_or_unknown_roof"
    point = catalog["venues"][chosen["venue_id"]]
    venue = {"venue_id": chosen["venue_id"], "name": chosen["venue_name"], **point, "outdoor_verified": True,
        "metadata_source": chosen["source_url"], "coordinates_source": catalog["coordinates_source"],
        "valid_from": kickoff.date().isoformat(), "valid_through": kickoff.date().isoformat()}
    return {"venue": venue, "venue_id": chosen["venue_id"], "venue_evidence": chosen,
        "roof_evidence": roof, "roof_history_status": timing, "indoor": False, "neutral_site": False}, "included"


def build_plan(root: Path, source_root: Path, batch_size: int = 10) -> dict:
    if (root / PLAN_PATH).exists():
        raise ValueError("A locked2026plan already exists; never overwrite it after inspecting weather/results")
    quote_manifest = json.loads((root / QUOTE_MANIFEST).read_text())
    if _sha(root / QUOTES) != quote_manifest["artifact_sha256"] or quote_manifest.get("no_outcomes_loaded") is not True:
        raise ValueError("Quote provenance manifest mismatch")
    catalog = json.loads((root / CATALOG).read_text())
    if catalog["schema_version"] != "weather-venues-v1" or len(catalog["venues"]) != 100:
        raise ValueError("Expected frozen100-venue catalog")
    frame = pd.read_parquet(root / QUOTES, columns=QUOTE_COLUMNS)
    captures, errors = select_captures(frame)
    context = read_venue_context(source_root, {r["game_id"] for r in captures})
    context_path = root / RAW / "venue_context.json"
    _write(context_path, {"outcomes_extracted": False, "records": context})
    included, excluded = [], []
    for capture in captures:
        match, reason = match_context(capture, context, catalog)
        flags = target_flags(capture, _timestamp(capture["decision_time"]))
        if match is None or flags:
            excluded.append({"game_id": capture["game_id"], "cohort": capture["cohort"], "price_eligible": capture["price_eligible"],
                "reason": reason if match is None else flags[0]})
            continue
        included.append({**capture, **match})
    # One weather request per game even when the two cohorts overlap.
    unique = {}
    for row in included:
        if row["game_id"] in unique and any(unique[row["game_id"]]["venue"][key] != row["venue"][key]
                for key in ("venue_id", "latitude", "longitude")):
            raise ValueError("Cohorts disagree on physical game/venue identity")
        unique[row["game_id"]] = row
    grouped = defaultdict(list)
    for row in unique.values():
        kickoff = _timestamp(row["kickoff"])
        grouped[(kickoff.date().isoformat(), (kickoff + timedelta(hours=3)).date().isoformat())].append(row)
    batches = []
    for (start, end), rows in sorted(grouped.items()):
        rows.sort(key=lambda r: r["game_id"])
        for offset in range(0, len(rows), batch_size):
            group = rows[offset:offset + batch_size]
            params = {"latitude": ",".join(str(r["venue"]["latitude"]) for r in group),
                "longitude": ",".join(str(r["venue"]["longitude"]) for r in group), "start_date": start, "end_date": end,
                "hourly": ",".join(v + "_previous_day2" for v in VARIABLES), "models": MODEL,
                "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "timezone": "GMT"}
            batches.append({"request_id": _digest(params)[:24], "source_url": PREVIOUS_URL,
                "parameters": params, "game_ids": [r["game_id"] for r in group]})
    plan = {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(), "thresholds": THRESHOLDS.copy(),
        "policy": POLICY, "forecast_model": MODEL, "lead_hours": 48, "publication_buffer_hours": 6, "wind_hours": 4,
        "outcomes_used": False, "weather_values_used": False, "source_hashes": {"quotes": _sha(root / QUOTES),
            "quote_manifest": _sha(root / QUOTE_MANIFEST), "venue_catalog": _sha(root / CATALOG), "venue_context": _sha(context_path)},
        "quote_row_exclusions": errors, "included_unique_games": len(unique), "http_batches": len(batches),
        "cohort_counts": {cohort: {"first_captures": sum(r["cohort"] == cohort for r in captures),
            "venue_eligible": sum(r["cohort"] == cohort for r in included),
            "venue_and_price_eligible": sum(r["cohort"] == cohort and r["price_eligible"] for r in included),
            "venue_context_received_by_quote": sum(r["cohort"] == cohort and r["roof_history_status"] == "pregame_context_received_by_quote_time" for r in included)}
            for cohort in (PRIMARY, SECONDARY)},
        "exclusion_counts": dict(Counter(r["reason"] for r in excluded)), "excluded": excluded,
        "games": included, "requests": batches,
        "interpretation": "New retrospective2026test of a subsequently frozen hypothesis. Local archived quote receipts are not independent timestamp attestation. Prices reflect actual receipt times, not fictitious06:30execution. No forecast/outcome search used to choose this plan."}
    plan["plan_sha256"] = _digest(plan)
    _write(root / PLAN_PATH, plan)
    print(json.dumps({k: plan[k] for k in ["plan_sha256", "included_unique_games", "http_batches", "cohort_counts", "exclusion_counts"]}, indent=2), flush=True)
    return plan


def read_plan(root):
    plan = json.loads((root / PLAN_PATH).read_text())
    digest = plan.pop("plan_sha256")
    if _digest(plan) != digest or plan["version"] != VERSION or plan["thresholds"] != THRESHOLDS or plan["policy"] != POLICY:
        raise ValueError("Frozen2026request plan/policy/hash changed")
    for key, path in {"quotes": QUOTES, "quote_manifest": QUOTE_MANIFEST, "venue_catalog": CATALOG, "venue_context": RAW + "/venue_context.json"}.items():
        if _sha(root / path) != plan["source_hashes"][key]:
            raise ValueError("Frozen2026input changed: " + key)
    plan["plan_sha256"] = digest
    return plan


def fetch_plan(root: Path, limit: int | None = None, session=None, delay_per_location=.35):
    plan = read_plan(root)
    directory = root / RAW / "batches"
    directory.mkdir(parents=True, exist_ok=True)
    games = {r["game_id"]: r for r in plan["games"]}
    session, counts = session or requests.Session(), Counter()
    for index, batch in enumerate(plan["requests"][:limit]):
        path = directory / (batch["request_id"] + ".json.gz")
        if path.exists():
            archive = read_batch(path, batch)
            normalize_batch(archive["payload"], batch, games, archive["observed_at"])
            counts["cached"] += 1
            continue
        for attempt in range(3):
            try:
                response = session.get(batch["source_url"], params=batch["parameters"], timeout=30)
                if response.status_code == 429:
                    counts["rate_limit_stop"] += 1
                    _write(root / "reports/weather_2026_replay_fetch_status.json", dict(counts))
                    return dict(counts)
                response.raise_for_status()
                received, payload = datetime.now(timezone.utc).isoformat(), response.json()
                normalize_batch(payload, batch, games, received)
                archive = {"request_id": batch["request_id"], "source_url": batch["source_url"], "parameters": batch["parameters"],
                    "observed_at": received, "payload": payload, "payload_sha256": _digest(payload), "response_sha256": hashlib.sha256(response.content).hexdigest()}
                with gzip.open(path, "xt") as handle:
                    json.dump(archive, handle, allow_nan=False)
                counts["fetched"] += 1
                break
            except (requests.RequestException, ValueError):
                if attempt == 2:
                    counts["failed"] += 1
                else:
                    time.sleep(2 ** attempt)
        print(f"{index + 1}/{len(plan['requests'])} batches: {dict(counts)}", flush=True)
        time.sleep(max(0, delay_per_location) * len(batch["game_ids"]))
    _write(root / "reports/weather_2026_replay_fetch_status.json", {"plan_sha256": plan["plan_sha256"], "counts": dict(counts)})
    return dict(counts)


def summarize(frame: pd.DataFrame):
    if frame.empty:
        return {"settled_price_eligible_covered_games": 0, "rule": {"bets": 0, "roi": None}}
    selected = frame.shadow_under_flag.to_numpy(bool)
    profit = np.where(frame.actual_total < frame.line, frame.decimal_odds - 1,
                      np.where(frame.actual_total > frame.line, -1., 0.))
    dates = pd.to_datetime(frame.kickoff, utc=True).dt.tz_convert(NY).dt.tz_localize(None).dt.normalize()
    weeks = (dates - pd.to_timedelta(dates.dt.weekday, unit="D")).dt.strftime("%Y-%m-%d")
    blocks = pd.DataFrame({"week": weeks, "n": 1., "profit": profit, "rule_n": selected.astype(float),
        "rule_profit": profit * selected}).groupby("week").sum().to_numpy()
    def metric(mask):
        values = profit[mask]
        return {"bets": int(len(values)), "wins": int((values > 0).sum()), "losses": int((values < 0).sum()),
            "pushes": int((values == 0).sum()), "profit_units": float(values.sum()), "roi": float(values.mean()) if len(values) else None}
    interval = None
    if len(blocks) >= 8 and selected.sum():
        rng = np.random.default_rng(20260908)
        sums = blocks[rng.integers(0, len(blocks), (5000, len(blocks)))].sum(axis=1)
        positive = sums[:, 2] > 0
        interval = np.quantile(sums[positive, 3] / sums[positive, 2], [.025, .975]).tolist()
    return {"settled_price_eligible_covered_games": len(frame), "calendar_week_blocks": len(blocks),
        "rule": metric(selected), "all_under_same_price_and_weather_coverage": metric(np.ones(len(frame), bool)),
        "rule_roi_95_week_bootstrap": interval, "interval_note": "Suppressed below8calendar weeks; conditional descriptive bootstrap otherwise. No familywise inference."}


def evaluate_plan(root: Path):
    plan = read_plan(root)
    games = {r["game_id"]: r for r in plan["games"]}
    envelopes = {}
    for batch in plan["requests"]:
        path = root / RAW / "batches" / (batch["request_id"] + ".json.gz")
        if not path.exists():
            raise ValueError("Incomplete forecast download; no partial evaluation")
        archive = read_batch(path, batch)
        for envelope in normalize_batch(archive["payload"], batch, games, archive["observed_at"]):
            envelopes[envelope["game_id"]] = envelope
    rows = []
    for game in plan["games"]:
        result = weather_flag(game, Venue(**game["venue"]), envelopes[game["game_id"]],
            _timestamp(game["decision_time"]), allow_historical_lead_proxy=True)
        quote = game["selected_quote"] or {}
        rows.append({"game_id": int(game["game_id"]), "cohort": game["cohort"], "kickoff": game["kickoff"],
            "decision_time": game["decision_time"], "quote_capture_id": game["quote_capture_id"],
            "home_team": game["home_team"], "away_team": game["away_team"], "venue_id": game["venue_id"],
            "roof_history_status": game["roof_history_status"], "price_eligible": game["price_eligible"],
            "line": quote.get("line"), "decimal_odds": quote.get("under_decimal_odds"), "sportsbook": quote.get("book"), **result})
    # This is the first read of any final-score columns in this pipeline.
    path = root / "data/raw/sportsdataverse/cfb_schedule_2026.parquet"
    outcomes = pd.read_parquet(path, columns=["game_id", "home_score", "away_score", "status"])
    if outcomes.game_id.duplicated().any():
        raise ValueError("Duplicate outcome IDs")
    outcomes["actual_total"] = outcomes.home_score + outcomes.away_score
    outcomes = outcomes.rename(columns={"status": "outcome_status"})
    features = pd.DataFrame(rows)
    # The common weather helper emits string IDs; normalize both join keys
    # after assembling its result so that it cannot overwrite this contract.
    features["game_id"] = pd.to_numeric(features["game_id"], errors="raise").astype("int64")
    outcomes["game_id"] = pd.to_numeric(outcomes["game_id"], errors="raise").astype("int64")
    frame = features.merge(outcomes[["game_id", "actual_total", "outcome_status"]], on="game_id", how="left", validate="many_to_one")
    covered = frame.loc[frame.status.eq("shadow_only") & frame.price_eligible & frame.outcome_status.eq("STATUS_FINAL")
        & np.isfinite(frame.actual_total)].copy()
    cohorts = {cohort: summarize(covered.loc[covered.cohort.eq(cohort)]) for cohort in (PRIMARY, SECONDARY)}
    report = {"version": VERSION, "plan_sha256": plan["plan_sha256"], "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "outcome_source": str(path.relative_to(root)), "outcome_source_sha256": _sha(path), "cohort_plan_counts": plan["cohort_counts"],
        "forecast_unavailable_rows": int((~frame.status.eq("shadow_only")).sum()),
        "cohorts": cohorts, "rule": THRESHOLDS, "policy": POLICY,
        "limitations": ["Retrospective2026replay; hypothesis frozen after the games, before this weather/outcome evaluation.",
            "Local quote metadata/hashes support but do not independently attest receipt time; no betting execution proved.",
            "Original forecast dissemination timestamps unverified; GFS fixed-lead historical availability proxy with6h buffer.",
            "Two-book primary and one-book ESPN sensitivity can overlap and are not independent or pooled.",
            "Small early-season sample and very few calendar weeks cannot establish high-confidence profitability.",
            "No individual-game probability or live EV inferred from this group result."]}
    frame.to_parquet(root / RAW / "game_results.parquet", index=False)
    _write(root / "reports/weather_2026_replay_results.json", report)
    print(json.dumps(report, indent=2, allow_nan=False), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    stage = parser.add_mutually_exclusive_group(required=True)
    for name in ("plan", "fetch", "evaluate"):
        stage.add_argument("--" + name, action="store_true")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--delay-per-location", type=float, default=.35)
    args = parser.parse_args()
    if args.plan:
        if args.source_root is None:
            parser.error("--plan requires --source-root for existing raw scoreboards")
        build_plan(args.root, args.source_root)
    elif args.fetch:
        fetch_plan(args.root, args.limit, delay_per_location=args.delay_per_location)
    else:
        evaluate_plan(args.root)


if __name__ == "__main__":
    main()
