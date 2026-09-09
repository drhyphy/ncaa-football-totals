"""One frozen, published weather hypothesis on repaired 2024–25 market games.

Stages are intentionally separate: --plan (no weather requests or results),
--fetch (bounded, resumable batch archive), --evaluate (fixed thresholds only).
Current roof metadata requires an explicit historical-stability assumption.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

from .teams import normalize_team
from .weather_shadow import MODEL, PREVIOUS_URL, THRESHOLDS, VARIABLES, Venue, weather_flag

NY = ZoneInfo("America/New_York")
SOURCE_COORDINATES = "https://github.com/gboeing/data-visualization/blob/main/ncaa-football-stadiums/data/stadiums-geocoded.csv"
VERSION = "weather-published-hypothesis-2024-25-v1"
ROOF_ASSUMPTION = "Current ESPN actual-venue indoor=false; outdoor roof configuration assumed unchanged in 2024–25, not historically proved"
# This is an identity whitelist, not fuzzy matching. New stadiums replacing old
# sites (Canvas, Allegiant, Snapdragon, Hancock Whitney, Protective) are absent.
# Value is (source dataset stadium, source dataset team for disambiguation).
ALIASES = {
    "Memorial Stadium (Norman, OK)": ("Gaylord Family Oklahoma Memorial Stadium", "Oklahoma"),
    "Memorial Stadium (Clemson, SC)": ("Memorial Stadium", "Clemson"),
    "Memorial Stadium (Bloomington, IN)": ("Memorial Stadium", "Indiana"),
    "Memorial Stadium (Lincoln, NE)": ("Memorial Stadium", "Nebraska"),
    "Alumni Stadium (Chestnut Hill, MA)": ("Alumni Stadium", "Boston College"),
    "University Stadium (NM)": ("University Stadium", "New Mexico"),
    "Tiger Stadium (LA)": ("Tiger Stadium", "LSU"),
    "Veterans Memorial Stadium (AL)": ("Veterans Memorial Stadium", "Troy"),
    "DKR-Texas Memorial Stadium": ("Darrell K Royal–Texas Memorial Stadium", "Texas"),
    "Doak Campbell Stadium": ("Bobby Bowden Field at Doak Campbell Stadium", "Florida State"),
    "Milan Puskar Stadium": ("Mountaineer Field at Milan Puskar Stadium", "West Virginia"),
    "Kenan Stadium": ("Kenan Memorial Stadium", "North Carolina"),
    "Yulman Stadium": ("Benson Field at Yulman Stadium", "Tulane"),
    "Sun Bowl": ("Sun Bowl Stadium", "UTEP"),
    "Bobby Dodd Stadium": ("Bobby Dodd Stadium at Historic Grant Field", "Georgia Tech"),
    "Bill Snyder Family Stadium": ("Bill Snyder Family Football Stadium", "Kansas State"),
    "H. A. Chapman Stadium": ("Skelly Field at H. A. Chapman Stadium", "Tulsa"),
    "Doyt L. Perry Stadium": ("Doyt Perry Stadium", "Bowling Green"),
    "Allen E. Paulson Stadium": ("Paulson Stadium", "Georgia Southern"),
    "InfoCision Stadium": ("Summa Field at InfoCision Stadium", "Akron"),
    "Kroger Field": ("Commonwealth Stadium", "Kentucky"),
    "Mountain America Stadium": ("Sun Devil Stadium Frank Kush Field", "Arizona State"),
}
ALIAS_SOURCES = {
    "Kroger Field": "https://ukathletics.com/facilities/kroger-field/",
    "Mountain America Stadium": "https://thesundevils.com/facilities-venues/mountain-america-stadium",
    "other_aliases": "Explicit formal-name expansions or city/state disambiguations of the same named stadium; no inferred relocation or fuzzy matching",
}


def normalize_name(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")


def current_roof_catalog(root: Path) -> dict:
    rows = defaultdict(list)
    for path in sorted((root / "data/raw/espn_scoreboard").glob("*.json")):
        if ".meta." in path.name:
            continue
        body = json.loads(path.read_text())
        for event in body.get("events", []):
            for comp in event.get("competitions", []):
                venue = comp.get("venue") or {}
                if not venue.get("id") or not venue.get("fullName"):
                    continue
                home = next((x.get("team", {}).get("displayName") for x in comp.get("competitors", []) if x.get("homeAway") == "home"), "")
                key = (normalize_name(venue["fullName"]), normalize_team(home))
                row = {"id": str(venue["id"]), "name": venue["fullName"], "indoor": venue.get("indoor"),
                    "state": venue.get("address", {}).get("state"), "city": venue.get("address", {}).get("city"),
                    "source": str(path.relative_to(root))}
                if row not in rows[key]:
                    rows[key].append(row)
    return rows


def match_venue(game: dict, coordinates: list[dict], roof_catalog: dict) -> tuple[dict | None, str]:
    """Exact actual-venue identity, explicit alias, known outdoor status only."""
    if game.get("neutral_site") is not False:
        return None, "neutral_or_unknown_neutral"
    name = str(game.get("venue", ""))
    roof = roof_catalog.get((normalize_name(name), normalize_team(game.get("home_team", ""))), [])
    if not roof:
        # A uniquely identified stadium can occur with an alternate home label.
        roof = [row for (venue_name, _), records in roof_catalog.items() if venue_name == normalize_name(name) for row in records]
    if not roof or len({row["id"] for row in roof}) != 1:
        return None, "actual_venue_roof_identity_unknown_or_ambiguous"
    if any(row.get("indoor") is not False for row in roof):
        return None, "indoor_or_roof_status_unknown"
    states = {row.get("state") for row in roof}
    target_name, target_team = ALIASES.get(name, (name, None))
    options = [row for row in coordinates if normalize_name(row["stadium"]) == normalize_name(target_name)]
    if target_team:
        options = [row for row in options if row["team"] == target_team]
    if len(options) > 1:
        options = [row for row in options if row.get("state") in states]
    if len(options) != 1:
        return None, "stadium_coordinates_unknown_or_ambiguous"
    point = options[0]
    # Standard-state labels give a hard rejection of a same-name wrong state.
    if len(str(point.get("state", ""))) == 2 and len(states) == 1 and next(iter(states)) != point["state"]:
        return None, "stadium_state_mismatch"
    venue = {"venue_id": roof[0]["id"], "name": name, "latitude": float(point["latitude"]),
        "longitude": float(point["longitude"]), "outdoor_verified": True,
        "metadata_source": f"https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/venues/{roof[0]['id']}",
        "coordinates_source": SOURCE_COORDINATES, "valid_from": "2024-01-01", "valid_through": "2026-12-31"}
    return {"venue": venue, "roof_evidence": roof[0], "roof_history_status": ROOF_ASSUMPTION,
        "coordinate_source_name": point["stadium"], "coordinate_source_team": point["team"],
        "match_method": "explicit_identity_alias" if name in ALIASES else "exact_name_and_unique_state"}, "included"


def load_market_games(root: Path) -> pd.DataFrame:
    frame = pd.read_parquet(root / "data/raw/alternative/espn_verified_pregame_games.parquet")
    frame = frame.loc[frame.season.isin([2024, 2025]) & frame.role_verified.eq(True)].copy()
    if frame.game_id.duplicated().any():
        raise ValueError("Duplicate repaired market game")
    schedule = pd.concat([pd.read_parquet(root / f"data/raw/sportsdataverse/cfb_schedule_{year}.parquet") for year in (2024, 2025)])
    return frame.merge(schedule[["game_id", "venue"]], on="game_id", how="left", validate="one_to_one").sort_values(["game_date", "game_id"])


def build_plan(root: Path, stadiums: Path, *, allow_current_roof_assumption: bool = False, batch_size: int = 10) -> dict:
    if not allow_current_roof_assumption:
        raise ValueError("Explicit --allow-current-roof-assumption required; current metadata does not prove historical roof status")
    coordinates = list(csv.DictReader(stadiums.open()))
    raw = root / "data/raw/weather_research"
    raw.mkdir(parents=True, exist_ok=True)
    raw.joinpath("stadiums-geocoded.csv").write_bytes(stadiums.read_bytes())
    roofs, frame = current_roof_catalog(root), load_market_games(root)
    included, excluded = [], []
    # Selection uses only game identity/date, venue, provider-role eligibility;
    # no market total, final score, win/loss or forecast value enters the plan.
    for game in frame.to_dict("records"):
        match, reason = match_venue(game, coordinates, roofs)
        if match is None:
            excluded.append({"game_id": int(game["game_id"]), "season": int(game["season"]), "venue": str(game.get("venue")), "reason": reason})
            continue
        kickoff = pd.Timestamp(game["game_date"]).to_pydatetime().astimezone(timezone.utc)
        local_date = kickoff.astimezone(NY).date()
        decision = datetime.combine(local_date, datetime.min.time(), NY).replace(hour=6, minute=30).astimezone(timezone.utc)
        if decision >= kickoff:
            excluded.append({"game_id": int(game["game_id"]), "season": int(game["season"]), "venue": str(game.get("venue")), "reason": "kickoff_not_after_gameday0630Eastern"})
            continue
        item = {"game_id": str(game["game_id"]), "season": int(game["season"]), "week": int(game["week"]),
            "kickoff": kickoff.isoformat(), "decision_time": decision.isoformat(), "venue_id": match["venue"]["venue_id"],
            "indoor": False, "neutral_site": False, **match}
        item["start_date"] = kickoff.date().isoformat()
        item["end_date"] = (kickoff + timedelta(hours=3)).date().isoformat()
        included.append(item)
    grouped = defaultdict(list)
    for item in included:
        grouped[(item["start_date"], item["end_date"])].append(item)
    batches = []
    for (start, end), group in sorted(grouped.items()):
        for offset in range(0, len(group), batch_size):
            games = group[offset:offset + batch_size]
            params = {"latitude": ",".join(str(row["venue"]["latitude"]) for row in games),
                "longitude": ",".join(str(row["venue"]["longitude"]) for row in games),
                "start_date": start, "end_date": end, "hourly": ",".join(name + "_previous_day2" for name in VARIABLES),
                "models": MODEL, "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "timezone": "GMT"}
            batches.append({"request_id": _digest(params)[:24], "source_url": PREVIOUS_URL, "parameters": params,
                "game_ids": [row["game_id"] for row in games]})
    plan = {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(), "thresholds": THRESHOLDS,
        "forecast_model": MODEL, "lead_hours": 48, "publication_buffer_hours": 6, "wind_hours": 4,
        "decision_policy": "06:30 America/New_York on kickoff's Eastern calendar date", "assumed_american_price": -110,
        "roof_assumption": ROOF_ASSUMPTION, "market_role": "Fixed verified non-live provider; quote/publication/close time unavailable",
        "source_hashes": {"repaired_games": hashlib.sha256((root / "data/raw/alternative/espn_verified_pregame_games.parquet").read_bytes()).hexdigest(),
            "coordinates": hashlib.sha256(stadiums.read_bytes()).hexdigest()},
        "coordinate_url": SOURCE_COORDINATES, "aliases": ALIASES, "alias_sources": ALIAS_SOURCES,
        "input_games": len(frame), "included_games": len(included), "http_batches": len(batches),
        "exclusion_counts": dict(Counter(row["reason"] for row in excluded)), "excluded": excluded,
        "games": included, "requests": batches, "outcome_data_used_for_plan": False}
    plan["plan_sha256"] = _digest(plan)
    _write(root / "reports/weather_request_plan.json", plan)
    print(json.dumps({key: plan[key] for key in ("plan_sha256", "input_games", "included_games", "http_batches", "exclusion_counts")}, indent=2), flush=True)
    return plan


def read_plan(root: Path) -> dict:
    plan = json.loads((root / "reports/weather_request_plan.json").read_text())
    digest = plan.pop("plan_sha256")
    if digest != _digest(plan) or plan["thresholds"] != THRESHOLDS or plan["version"] != VERSION:
        raise ValueError("Request plan hash or frozen hypothesis changed")
    plan["plan_sha256"] = digest
    return plan


def normalize_batch(payload, batch: dict, games: dict, received: str) -> list[dict]:
    items = payload if isinstance(payload, list) else [payload]
    if len(items) != len(batch["game_ids"]):
        raise ValueError("Multiple-location response count mismatch")
    envelopes = []
    for index, (game_id, data) in enumerate(zip(batch["game_ids"], items)):
        venue = games[game_id]["venue"]
        if not isinstance(data, dict) or data.get("error"):
            raise ValueError("Invalid location forecast payload")
        if "location_id" in data and int(data["location_id"]) != index:
            raise ValueError("Multiple-location index mismatch")
        # GFS returns a nearby grid point. Large deviations imply wrong mapping.
        if abs(float(data.get("latitude", 1000)) - venue["latitude"]) > .5 or abs(float(data.get("longitude", 1000)) - venue["longitude"]) > .5:
            raise ValueError("Forecast grid is not near the requested stadium")
        params = {**batch["parameters"], "latitude": venue["latitude"], "longitude": venue["longitude"]}
        envelopes.append({"game_id": game_id, "mode": "previous_day2", "source_url": PREVIOUS_URL,
            "model": MODEL, "observed_at": received, "parameters": params, "payload": data})
    return envelopes


def read_batch(path: Path, batch: dict) -> dict:
    with gzip.open(path, "rt") as handle:
        archive = json.load(handle)
    if archive.get("request_id") != batch["request_id"] or archive.get("source_url") != batch["source_url"] or archive.get("parameters") != batch["parameters"]:
        raise ValueError("Cached response does not match the locked forecast request")
    if archive.get("payload_sha256") is not None and archive["payload_sha256"] != _digest(archive["payload"]):
        raise ValueError("Cached forecast payload hash mismatch")
    return archive


def fetch_plan(root: Path, *, limit: int | None = None, delay_per_location: float = .35, session=None) -> dict:
    plan = read_plan(root)
    directory = root / "data/raw/weather_research/batches"
    directory.mkdir(parents=True, exist_ok=True)
    session = session or requests.Session()
    games = {row["game_id"]: row for row in plan["games"]}
    counts = Counter()
    for index, batch in enumerate(plan["requests"][:limit]):
        path = directory / f"{batch['request_id']}.json.gz"
        if path.exists():
            archive = read_batch(path, batch)
            normalize_batch(archive["payload"], batch, games, archive["observed_at"])
            counts["cached"] += 1
            continue
        failure = None
        for attempt in range(3):
            try:
                response = session.get(batch["source_url"], params=batch["parameters"], timeout=30)
                if response.status_code == 429:
                    # Stop at quota limits; preserve successful immutable work.
                    counts["rate_limit_stop"] += 1
                    _write(root / "reports/weather_fetch_status.json", {"plan_sha256": plan["plan_sha256"], "counts": dict(counts)})
                    return dict(counts)
                response.raise_for_status()
                received = datetime.now(timezone.utc).isoformat()
                payload = response.json()
                normalize_batch(payload, batch, games, received)
                archive = {"request_id": batch["request_id"], "source_url": batch["source_url"],
                    "parameters": batch["parameters"], "observed_at": received, "payload": payload,
                    "payload_sha256": _digest(payload),
                    "response_sha256": hashlib.sha256(response.content).hexdigest()}
                with gzip.open(path, "xt") as handle:
                    json.dump(archive, handle, allow_nan=False)
                counts["fetched"] += 1
                failure = None
                break
            except (requests.RequestException, ValueError) as exc:
                failure = type(exc).__name__
                if attempt < 2:
                    time.sleep(2 ** attempt)
        if failure:
            counts["failed"] += 1
            print(f"Batch {batch['request_id']} failed: {failure}", flush=True)
        if index % 10 == 0 or index == len(plan["requests"]) - 1:
            print(f"{index+1}/{len(plan['requests'])} batches; {dict(counts)}", flush=True)
        time.sleep(max(0., delay_per_location) * len(batch["game_ids"]))
    _write(root / "reports/weather_fetch_status.json", {"plan_sha256": plan["plan_sha256"], "counts": dict(counts)})
    return dict(counts)


def settle_under(actual, line):
    actual, line = np.asarray(actual, float), np.asarray(line, float)
    return np.where(actual < line, 100 / 110, np.where(actual > line, -1., 0.))


def summarize(frame: pd.DataFrame, *, bootstrap_draws: int = 5000, seed: int = 84621) -> dict:
    """Resample complete Eastern calendar weeks; selected-vs-all paired contrast."""
    if frame.empty:
        return {"games": 0, "weather_rule_bets": 0, "status": "no_covered_games"}
    selected = frame.shadow_under_flag.to_numpy(bool)
    profit = settle_under(frame.actual_total, frame.market_total)
    dates = pd.to_datetime(frame.kickoff, utc=True).dt.tz_convert(NY)
    # Drop timezone only after selecting Eastern date, avoiding DST-hour shifts.
    local_midnight = dates.dt.tz_localize(None).dt.normalize()
    week = (local_midnight - pd.to_timedelta(dates.dt.weekday, unit="D")).dt.strftime("%Y-%m-%d")
    table = pd.DataFrame({"week": week, "all_n": 1., "all_profit": profit,
        "selected_n": selected.astype(float), "selected_profit": profit * selected})
    blocks = table.groupby("week", sort=True)[["all_n", "all_profit", "selected_n", "selected_profit"]].sum().to_numpy()
    rng = np.random.default_rng(seed)
    sums = blocks[rng.integers(0, len(blocks), size=(bootstrap_draws, len(blocks)))].sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        draws_selected = sums[:, 3] / sums[:, 2]
        draws_all = sums[:, 1] / sums[:, 0]
    valid = np.isfinite(draws_selected)
    def interval(values):
        return [float(x) for x in np.quantile(values, [.025, .975])] if len(blocks) >= 8 and len(values) else None
    def metrics(mask):
        values = profit[mask]
        wins, losses = int((values > 0).sum()), int((values < 0).sum())
        return {"bets": int(len(values)), "wins": wins, "losses": losses, "pushes": int((values == 0).sum()),
            "win_rate_excluding_pushes": wins / (wins + losses) if wins + losses else None,
            "roi": float(values.mean()) if len(values) else None, "profit_units": float(values.sum())}
    return {"games": len(frame), "calendar_week_blocks": len(blocks), "weather_rule": metrics(selected),
        "all_under_same_weather_coverage": metrics(np.ones(len(frame), bool)), "rule_coverage_rate": float(selected.mean()),
        "weather_rule_roi_95_week_bootstrap": interval(draws_selected[valid]),
        "all_under_roi_95_week_bootstrap": interval(draws_all),
        "rule_minus_all_under_roi_95_paired_week_bootstrap": interval((draws_selected - draws_all)[valid]),
        "bootstrap_draws": bootstrap_draws, "bootstrap_draws_with_no_rule_bets": int((~valid).sum()),
        "price_assumption": "Every bet risks one unit at -110; pushes refund; actual historical prices unavailable"}


def evaluate_plan(root: Path, *, require_complete: bool = True) -> dict:
    plan = read_plan(root)
    repaired_path = root / "data/raw/alternative/espn_verified_pregame_games.parquet"
    if hashlib.sha256(repaired_path.read_bytes()).hexdigest() != plan["source_hashes"]["repaired_games"]:
        raise ValueError("Market dataset changed after request plan was locked")
    games = {row["game_id"]: row for row in plan["games"]}
    evaluated, missing = [], []
    for batch in plan["requests"]:
        path = root / f"data/raw/weather_research/batches/{batch['request_id']}.json.gz"
        if not path.exists():
            missing.extend(batch["game_ids"])
            continue
        archive = read_batch(path, batch)
        for envelope in normalize_batch(archive["payload"], batch, games, archive["observed_at"]):
            game = games[envelope["game_id"]]
            result = weather_flag(game, Venue(**game["venue"]), envelope,
                datetime.fromisoformat(game["decision_time"]), allow_historical_lead_proxy=True)
            evaluated.append({"game_id": int(game["game_id"]), "season": game["season"], "kickoff": game["kickoff"],
                "venue_name": game["venue"]["name"], "roof_history_status": game["roof_history_status"], **result})
    if missing and require_complete:
        raise ValueError(f"{len(missing)} planned game forecasts are unfetched; rerun --fetch before final evaluation")
    # Outcomes first enter here, after weather features and request eligibility.
    frame = pd.DataFrame(evaluated)
    if frame.empty:
        raise ValueError("No forecast responses available")
    frame["game_id"] = pd.to_numeric(frame.game_id).astype(int)
    markets = load_market_games(root)
    frame = frame.merge(markets[["game_id", "actual_total", "market_total", "provider_id", "market_source"]], on="game_id", validate="one_to_one")
    covered = frame.loc[frame.status.eq("shadow_only")].copy()
    report = {"version": VERSION, "plan_sha256": plan["plan_sha256"], "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "partial": bool(missing), "missing_game_ids": missing, "repaired_market_games": plan["input_games"],
        "venue_eligible_games": plan["included_games"], "weather_covered_games": len(covered),
        "weather_missing_reasons": dict(Counter(flag for flags in frame.loc[~frame.status.eq("shadow_only"), "flags"] for flag in flags)),
        "thresholds": THRESHOLDS, "pooled": summarize(covered),
        "by_season": {str(year): summarize(covered.loc[covered.season.eq(year)]) for year in (2024, 2025)},
        "limitations": [ROOF_ASSUMPTION, "Forecast fixed-lead provenance is not original public dissemination-time proof.",
            "Verified non-live bookmaker role; archived line not proven to be exact close or game-day06:30 price. Prices assumed-110.",
            "One externally published criterion, fixed before this weather download. No weather thresholds or score windows selected on these outcomes.",
            "2024–25 outcomes are reused overall development data. This is an independent-hypothesis adaptation, not a new untouched project holdout.",
            "Selected-vs-all under contrast reflects selection, not a causal weather effect; geographic/season/market differences may confound it.",
            "Week bootstrap measures sampling uncertainty conditional on these data/assumptions, not familywise evidence for promoting a profitable strategy."]}
    frame.to_parquet(root / "data/raw/weather_research/game_results.parquet", index=False)
    _write(root / "reports/weather_published_hypothesis_results.json", report)
    lines = ["# Frozen weather hypothesis — 2024–25 development replication", "",
        f"Weather coverage: {len(covered)} of {plan['included_games']} planned games; repaired source universe {plan['input_games']}. Partial: {bool(missing)}.", "",
        "| Period | Covered | Rule bets | Rule win rate (no pushes) | Rule ROI | Week bootstrap95% ROI | All-under ROI, same coverage |", "|---|---:|---:|---:|---:|---|---:|"]
    for period, metrics in [("2024–25", report["pooled"]), *report["by_season"].items()]:
        if not metrics["games"]:
            continue
        rule, base = metrics["weather_rule"], metrics["all_under_same_weather_coverage"]
        f = lambda value: "unavailable" if value is None else f"{value:.2%}"
        interval = metrics["weather_rule_roi_95_week_bootstrap"]
        lines.append(f"| {period} | {metrics['games']} | {rule['bets']} | {f(rule['win_rate_excluding_pushes'])} | {f(rule['roi'])} | {' to '.join(f(x) for x in interval) if interval else 'unavailable'} | {f(base['roi'])} |")
    lines += ["", "All stakes are hypothetical one-unit risk at-110; pushes refund. Thresholds remain wind>7.78mph,temperature<64.81°F,RH>56.8%. Wind uses a fixed four-hour forecast average; temperature and RH use kickoff hour.", "", "## Limitations", ""]
    lines += ["- " + value for value in report["limitations"]]
    (root / "reports/weather_published_hypothesis_results.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({key: report[key] for key in ("partial", "weather_covered_games", "pooled", "by_season")}, indent=2), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    stage = parser.add_mutually_exclusive_group(required=True)
    stage.add_argument("--plan", action="store_true")
    stage.add_argument("--fetch", action="store_true")
    stage.add_argument("--evaluate", action="store_true")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--stadiums", type=Path)
    parser.add_argument("--allow-current-roof-assumption", action="store_true")
    parser.add_argument("--limit", type=int, help="Limit fetch batches for a bounded probe")
    parser.add_argument("--delay-per-location", type=float, default=.35)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    if args.plan:
        if not args.stadiums:
            parser.error("--plan requires the archived --stadiums CSV")
        build_plan(args.root, args.stadiums, allow_current_roof_assumption=args.allow_current_roof_assumption)
    elif args.fetch:
        fetch_plan(args.root, limit=args.limit, delay_per_location=args.delay_per_location)
    else:
        evaluate_plan(args.root, require_complete=not args.allow_partial)


if __name__ == "__main__":
    main()
