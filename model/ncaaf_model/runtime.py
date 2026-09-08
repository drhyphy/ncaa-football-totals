"""Portable morning runner: fetch public inputs, forecast, lock paper positions.

No wagers are transmitted. A missing feed publishes an unavailable board and
returns a failure, so a stale successful board can never masquerade as today's.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import traceback
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd
import requests

from .config import load_settings
from .distribution import expected_value, infer_center
from .math import devig_pair
from .sources import DataClient, ESPN_SCOREBOARD_URL, load_dotenv, merge_schedule_frames, parse_espn_scoreboard
from .storage import atomic_write_bytes
from .totals_scoring import attach_totals_schedule
from .teams import normalize_team

TZ = ZoneInfo("America/New_York")
VERSION = "totals-v3-20260908"
PRIMARY = "market_consensus_loo"
MAX_QUOTE_MINUTES = 60
SOURCES = [
    {"name": "SportsDataverse public archives", "url": "https://cfbfastr.sportsdataverse.org/"},
    {"name": "ESPN college football", "url": "https://www.espn.com/college-football/scoreboard"},
    {"name": "The Odds API", "url": "https://the-odds-api.com/sports/ncaaf-odds.html"},
    {"name": "Action Network public odds", "url": "https://www.actionnetwork.com/ncaaf/odds"},
    {"name": "Odds-API.io timestamped prices", "url": "https://docs.odds-api.io/guides/fetching-odds"},
]
LIMITATIONS = [
    "No candidate has established high-confidence profitability. All positions are prospective paper research.",
    "Historical tests use resolved closing totals and assumed -110 prices, not executable morning quotes. The 2019–2025 data are reused development data.",
    "Modeled EV and stressed EV are estimates, not confidence bounds or proof that a quoted price remains available.",
    "Public weekly EPA/FEI are reconstructed through-week statistics; original publication times are not fully auditable. Annual current-season roster snapshots are excluded.",
    "Morning-only snapshots do not establish closing line value. CLV stays unavailable unless a separate valid pre-kickoff close is captured.",
    "Weather, quarterback availability, and late roster news are not separately modeled; much of this information enters through sportsbook prices.",
]


def clean_json(value):
    if isinstance(value, dict):
        return {str(k): clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(v) for v in value]
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    if value is pd.NA or value is pd.NaT:
        return None
    return value


def write_json(path: Path, payload) -> None:
    atomic_write_bytes(path, (json.dumps(clean_json(payload), indent=2, allow_nan=False) + "\n").encode())


def stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_failure(error: Exception) -> str:
    # Request exceptions may contain a URL with apiKey. Never serialize str(error).
    response = getattr(error, "response", None)
    code = getattr(response, "status_code", None)
    return type(error).__name__ + (f" HTTP {code}" if code else "")


def refresh_inputs(settings, now: datetime) -> tuple[pd.DataFrame, dict]:
    client = DataClient(settings, timeout=35)
    diagnostics = {"input_failures": {}, "refreshed_inputs": []}
    root = settings.raw_dir / "sportsdataverse"
    sources = {"schedule": f"cfb_schedule_{settings.season}",
               "adv_team_gamelog": f"adv_team_gamelog_{settings.season}",
               "drives": f"drives_{settings.season}",
               "fpi_weekly": f"fpi_weekly_{settings.season}",
               "ratings_weekly": f"ratings_weekly_{settings.season}",
               "summaries_weekly": f"summaries_weekly_{settings.season}",
               "ratings_final": f"ratings_final_{settings.season-1}"}
    def fetch(source, name):
        season = settings.season - 1 if source == "ratings_final" else settings.season
        client._download(settings.data_urls[source].format(season=season), root / f"{name}.parquet", True)
        return source
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch, k, v): k for k, v in sources.items()}
        for future in as_completed(futures):
            key = futures[future]
            try:
                diagnostics["refreshed_inputs"].append(future.result())
            except Exception as exc:
                diagnostics["input_failures"][key] = safe_failure(exc)
    path = root / f"cfb_schedule_{settings.season}.parquet"
    schedule = pd.read_parquet(path) if path.exists() else pd.DataFrame()
    # Include the full season for grading old positions, and FBS/FCS current slate.
    start = now.astimezone(TZ).date() - timedelta(days=8)
    end = start + timedelta(days=22)
    for group in (80, 81):
        try:
            response = client.session.get(ESPN_SCOREBOARD_URL, params={"dates": f"{start:%Y%m%d}-{end:%Y%m%d}",
                                           "groups": group, "limit": 1000},
                                           headers={"User-Agent": "curl/8.7.1", "Accept": "application/json,text/plain,*/*"}, timeout=35)
            response.raise_for_status()
            fresh = parse_espn_scoreboard(response.json())
            schedule = merge_schedule_frames(schedule, fresh)
            diagnostics[f"espn_group_{group}_games"] = len(fresh)
        except Exception as exc:
            diagnostics["input_failures"][f"espn_{group}"] = safe_failure(exc)
    if schedule.empty:
        raise RuntimeError("No schedule available")
    path.parent.mkdir(parents=True, exist_ok=True)
    schedule.to_parquet(path, index=False)
    diagnostics["schedule_fresh"] = "schedule" in diagnostics["refreshed_inputs"] or any(
        diagnostics.get(f"espn_group_{group}_games", 0) > 0 for group in (80, 81))
    return schedule, diagnostics


def fetch_odds(settings, now: datetime) -> tuple[list, dict]:
    session = requests.Session()
    session.headers["User-Agent"] = "NCAA-totals-public-research/3.0"
    # Local execution can reuse the user's existing feed subscription without
    # copying credentials into the repository. CI receives a repository secret.
    load_dotenv(settings.root.parents[1] / ".env")
    key = os.getenv("ODDS_API_KEY", "").strip()
    failures = {}
    events = []
    coverage = []
    if key:
        for sport in ("americanfootball_ncaaf", "americanfootball_ncaaf_fcs"):
            try:
                response = session.get(f"https://api.the-odds-api.com/v4/sports/{sport}/odds",
                    params={"apiKey": key, "regions": "us", "markets": "totals,spreads", "oddsFormat": "american"}, timeout=35)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list):
                    raise ValueError("Invalid odds payload")
                for event in payload:
                    event["source"] = "the_odds_api"
                events.extend(payload)
                coverage.append(sport)
            except Exception as exc:
                failures[sport] = safe_failure(exc)
    if not events:
        try:
            from .actionnetwork_odds import fetch_actionnetwork_odds
            events, _ = fetch_actionnetwork_odds(session, settings.allowed_books, timeout=35)
            for event in events:
                event["source"] = "actionnetwork_public"
            coverage.append("actionnetwork_public_ncaaf")
        except Exception as exc:
            failures["actionnetwork_public"] = safe_failure(exc)
    io_diagnostics = {}
    io_key = os.getenv("ODDS_API_IO_KEY", "").strip()
    if io_key:
        try:
            from .odds_api_io import fetch_odds_api_io
            io_events, io_diagnostics = fetch_odds_api_io(session, io_key, now,
                allowed_books=settings.allowed_books, matchups=events or None, max_events=150, days_ahead=8)
            if io_events:
                events = merge_provider_events(events, io_events)
                coverage.append("odds_api_io_selected_books")
        except Exception as exc:
            failures["odds_api_io"] = safe_failure(exc)
    if not events:
        raise RuntimeError("No live totals feed available; " + json.dumps(failures))
    # The same event can occur in both feed categories. Merge by actual matchup/time.
    unique = {}
    for event in events:
        match = (event["home_team"], event["away_team"], event["commence_time"])
        if match not in unique or len(event.get("bookmakers", [])) > len(unique[match].get("bookmakers", [])):
            unique[match] = event
    return list(unique.values()), {"odds_coverage": coverage, "odds_failures": failures,
                                 "odds_api_io": io_diagnostics,
                                 "fcs_feed_available": "americanfootball_ncaaf_fcs" in coverage}


def merge_provider_events(existing: list, incoming: list) -> list:
    """Keep one contribution per actual book, despite multiple aggregators."""
    result = json.loads(json.dumps(existing))
    def totals_updated(book):
        values = [pd.to_datetime(m.get("last_update") or book.get("last_update"), utc=True, errors="coerce")
                  for m in book.get("markets", []) if m.get("key") == "totals"]
        values = [value for value in values if pd.notna(value)]
        return max(values) if values else pd.Timestamp.min.tz_localize("UTC")
    for event in incoming:
        candidates = [row for row in result if normalize_team(row["home_team"]) == normalize_team(event["home_team"])
            and normalize_team(row["away_team"]) == normalize_team(event["away_team"])
            and abs(pd.to_datetime(row["commence_time"], utc=True) - pd.to_datetime(event["commence_time"], utc=True)) <= pd.Timedelta(hours=4)]
        if len(candidates) != 1:
            result.append(event)
            continue
        target = candidates[0]
        books = {book["key"]: book for book in target.get("bookmakers", [])}
        for book in event.get("bookmakers", []):
            prior = books.get(book["key"])
            if prior is None or totals_updated(book) > totals_updated(prior):
                books[book["key"]] = book
        target["bookmakers"] = list(books.values())
        target["source"] = "+".join(sorted(set(str(target.get("source", "unknown")).split("+")) | {event.get("source", "unknown")}))
    return result


def quotes_from_events(events: list, settings, now: datetime) -> pd.DataFrame:
    rows = []
    for event in events:
        kickoff = pd.to_datetime(event.get("commence_time"), utc=True, errors="coerce")
        if pd.isna(kickoff) or kickoff <= now or kickoff > now + timedelta(days=14):
            continue
        quotes, spreads = [], []
        for book in event.get("bookmakers", []):
            if book.get("key") not in settings.allowed_books:
                continue
            for market in book.get("markets", []):
                if market.get("key") == "spreads":
                    for outcome in market.get("outcomes", []):
                        if outcome.get("name") == event["home_team"] and outcome.get("point") is not None:
                            spreads.append(float(outcome["point"]))
                if market.get("key") != "totals":
                    continue
                updated = market.get("last_update") or book.get("last_update")
                dt = pd.to_datetime(updated, utc=True, errors="coerce")
                age = (now - dt).total_seconds() / 60 if pd.notna(dt) else None
                valid_time = age is not None and -5 <= age <= MAX_QUOTE_MINUTES
                pairs = {}
                for outcome in market.get("outcomes", []):
                    side = str(outcome.get("name", "")).lower()
                    try:
                        line, price = float(outcome["point"]), float(outcome["price"])
                    except (ValueError, TypeError, KeyError):
                        continue
                    if not math.isfinite(line) or not math.isfinite(price):
                        continue
                    if side not in {"over", "under"} or not (10 <= line <= 120) or abs(price) < 100 or abs(price) > 1000:
                        continue
                    if abs(line * 2 - round(line * 2)) > 1e-8:
                        continue
                    pairs.setdefault(line, {})[side] = price
                for line, pair in pairs.items():
                    if set(pair) != {"over", "under"}:
                        continue
                    fair_over, _ = devig_pair(pair["over"], pair["under"])
                    quotes.append({"book": book["key"], "line": line, "over_price": pair["over"],
                                   "under_price": pair["under"], "fair_over": fair_over,
                                   "quote_time": updated, "fresh": valid_time, "age_minutes": age})
        if not quotes:
            continue
        # Main market only; a book contributes one reference to avoid weighting
        # duplicated rows or alternative totals as independent opinions.
        deduped = {}
        for quote in quotes:
            previous = deduped.get(quote["book"])
            if previous is None or (quote["fresh"] and not previous["fresh"]):
                deduped[quote["book"]] = quote
        quotes = list(deduped.values())
        fresh = [q for q in quotes if q["fresh"]]
        center_quotes = fresh or quotes
        rows.append({"event_id": str(event["id"]), "commence_time": stamp(kickoff),
                     "home_team": event["home_team"], "away_team": event["away_team"],
                     "market_total": float(np.median([q["line"] for q in center_quotes])),
                     "market_home_spread": float(np.median(spreads)) if spreads else np.nan,
                     "quotes": quotes, "source": event.get("source", "unknown")})
    return pd.DataFrame(rows)


def score_games(games: pd.DataFrame, projections: dict, distribution: dict, now: datetime,
                diagnostics: dict | None = None) -> list[dict]:
    """One best side/book per candidate/game, with independent peer consensus."""
    records = []
    sigma, family = distribution["sigma"], distribution["family"]
    for index, game in games.reset_index(drop=True).iterrows():
        quote_centers = {q["book"]: infer_center(q["line"], q["fair_over"], sigma, family) for q in game.quotes}
        for candidate, values in {PRIMARY: None, **projections}.items():
            choices = []
            for quote in game.quotes:
                peers = [q for q in game.quotes if q["book"] != quote["book"] and q["fresh"]]
                centers = [quote_centers[q["book"]] for q in peers]
                consensus = float(np.median(centers)) if centers else float(game.market_total)
                projection = consensus if candidate == PRIMARY else float(values[index])
                if not math.isfinite(projection):
                    continue
                for side in ("over", "under"):
                    price = quote[f"{side}_price"]
                    ev, win, push = expected_value(projection, quote["line"], price, side, sigma, family)
                    # Fixed sensitivity envelope; explicitly not a statistical CI.
                    adverse = projection - 1 if side == "over" else projection + 1
                    robust = min(expected_value(adverse, quote["line"], price, side, sigma * factor, f)[0]
                                 for factor in (.85, 1.15) for f in ("normal", "student_t7"))
                    flags = []
                    if not bool(game.get("schedule_match", False)):
                        flags.append("schedule_unmatched")
                    if not quote["fresh"]:
                        flags.append("quote_timestamp_missing_or_stale")
                    if len(peers) < 3:
                        flags.append("fewer_than_three_other_fresh_books")
                    dispersion = max(centers) - min(centers) if centers else None
                    if dispersion is not None and dispersion > 3:
                        flags.append("peer_dispersion_high")
                    if peers and max(q["line"] for q in peers) - min(q["line"] for q in peers) > 2:
                        flags.append("peer_line_dispersion_high")
                    if ev < .03:
                        flags.append("modeled_ev_below_3pct")
                    if robust < .01:
                        flags.append("stressed_ev_below_1pct")
                    line_edge = projection - quote["line"] if side == "over" else quote["line"] - projection
                    if candidate == PRIMARY and line_edge < 1:
                        flags.append("line_value_below_one_point")
                    if candidate != PRIMARY:
                        if abs(projection - float(game.market_total)) < 6:
                            flags.append("challenger_edge_below_six_points")
                        history = np.array([game.get("home_prior_games", 0), game.get("away_prior_games", 0)], dtype=float)
                        if not np.isfinite(history).all() or history.min() < 5:
                            flags.append("team_history_sparse")
                        failures = (diagnostics or {}).get("input_failures", {})
                        if any(k in failures for k in ("adv_team_gamelog", "drives", "current_team_history", "current_drive_history")):
                            flags.append("current_form_refresh_incomplete")
                        if candidate.startswith("public_") and (any(k in failures for k in ("fpi_weekly", "ratings_weekly", "summaries_weekly", "ratings_final")) or not game.get(f"{candidate}_data_available", True)):
                            flags.append("public_inputs_incomplete")
                    if diagnostics is not None and not diagnostics.get("schedule_fresh", True):
                        flags.append("schedule_refresh_failed")
                    if (now.year if now.month >= 3 else now.year - 1) != 2026:
                        flags.append("model_requires_new_season_validation")
                    if pd.to_datetime(game.commence_time, utc=True) <= now:
                        flags.append("game_started")
                    eligible = not flags
                    # Small paper exposure chosen before prospective evaluation.
                    bankroll_fraction = min(.0025, max(0., robust) * .10) if eligible and candidate == PRIMARY else 0.
                    choices.append({"game_id": str(int(game.espn_game_id)) if pd.notna(game.get("espn_game_id")) else game.event_id,
                        "event_id": game.event_id, "away_team": game.away_team, "home_team": game.home_team,
                        "kickoff": game.commence_time, "candidate": candidate, "side": side,
                        "line": quote["line"], "american_odds": int(round(price)),
                        "decimal_odds": 1 + (price / 100 if price > 0 else 100 / abs(price)), "sportsbook": quote["book"],
                        "projected_total": projection, "consensus_total": consensus,
                        "win_probability": win, "push_probability": push, "expected_value": ev,
                        "robust_ev": robust, "quote_time": quote["quote_time"], "confidence": "experimental",
                        "paper_stake_fraction": bankroll_fraction, "eligible": eligible, "flags": flags,
                        "reference_books": [q["book"] for q in peers], "peer_dispersion": dispersion,
                        "model_version": VERSION})
            if choices:
                # A bad stale outlier cannot hide a valid executable alternative.
                records.append(max(choices, key=lambda row: (row["eligible"], row["robust_ev"], row["expected_value"])))
    return clean_json(records)


def candidate_projections(settings, matched: pd.DataFrame, now: datetime, diagnostics: dict) -> tuple[pd.DataFrame, dict]:
    from .totals_features import latest_team_states, live_game_features, load_team_games
    from .totals_backtest import candidate_projections as core_projections
    history = pd.read_parquet(settings.models_dir / "team_games_history.parquet")
    try:
        current = load_team_games(settings, [settings.season])
        history = pd.concat([history, current], ignore_index=True).drop_duplicates(["game_id", "team_id"], keep="last")
        diagnostics["current_team_game_rows"] = len(current)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        diagnostics["input_failures"]["current_team_history"] = safe_failure(exc)
    states = latest_team_states(history, season=settings.season, as_of=now)
    games = live_game_features(matched, states)
    models = joblib.load(settings.models_dir / "totals_models_latest.joblib")
    artifact = json.loads((settings.models_dir / "totals_artifact_latest.json").read_text())
    projections = core_projections(games, models, artifact["feature_columns"], settings)
    projections.pop("market_only", None)
    projections.pop("market_consensus_shop", None)
    from .public_features import attach_public_features
    from .public_ensemble import load_live_public_projection
    games["game_date"] = games["commence_time"]
    current_completed = history.loc[history.season.eq(settings.season) &
        (pd.to_datetime(history.start_date, utc=True) + pd.Timedelta(hours=6)).lt(now)]
    latest_week = int(current_completed.week.max()) if not current_completed.empty else 0
    public_games = attach_public_features(games, settings, [settings.season], live_latest_week=latest_week, as_of=now)
    base, public_projection, _ = load_live_public_projection(public_games, settings)
    for name, family_name in (("public_fpi", "fpi"), ("public_fei_epa", "fei_epa"), ("public_summary", "summary")):
        games[f"{name}_data_available"] = public_games[f"coverage_{family_name}"].ge(.99).to_numpy()
    prior_columns = [column for column in public_games if column.startswith(("home_prior_", "away_prior_")) and column.endswith(("epa", "fei_off", "fei_def", "off_pace", "net_z"))]
    games["public_roster_prior_data_available"] = public_games[prior_columns].notna().all(axis=1).to_numpy() if prior_columns else False
    games["public_full_hgb_data_available"] = games[["public_fpi_data_available", "public_fei_epa_data_available", "public_summary_data_available"]].all(axis=1)
    games["public_superensemble_v2_data_available"] = games["public_full_hgb_data_available"]
    for name in base:
        projections[f"public_{name}"] = games.market_total.to_numpy(float) + base[name].to_numpy(float)
    projections["public_superensemble_v2"] = public_projection
    from .drive_model import load_drive_team_games, build_drive_features, drive_projections
    drive_history = pd.read_parquet(settings.models_dir / "drive_history.parquet")
    try:
        drive_current = load_drive_team_games(settings.raw_dir / "sportsdataverse", [settings.season])
        drive_history = pd.concat([drive_history, drive_current], ignore_index=True).drop_duplicates(["game_id", "team_id"], keep="last")
        diagnostics["current_drive_team_rows"] = len(drive_current)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        diagnostics["input_failures"]["current_drive_history"] = safe_failure(exc)
    drive_games = games.assign(game_id=games.espn_game_id)
    drive_features = build_drive_features(drive_games, drive_history, as_of=now)
    drive_artifact = json.loads((settings.models_dir / "drive_clock_v1.json").read_text())
    projections.update(drive_projections(drive_features, drive_artifact))
    diagnostics["feature_snapshot"] = {"core_and_public": clean_json(public_games.drop(columns=["quotes"], errors="ignore").to_dict("records")),
                                       "drive": clean_json(drive_features.drop(columns=["quotes"], errors="ignore").to_dict("records"))}
    return games, projections


def record_positions(existing: list, forecasts: list, now: datetime) -> list:
    """First eligible entry per version/candidate/game, permanently locked."""
    output = list(existing)
    keys = {(p["model_version"], p["candidate"], str(p["game_id"])) for p in existing}
    for row in forecasts:
        key = (row["model_version"], row["candidate"], str(row["game_id"]))
        if row["eligible"] and key not in keys:
            position = {**row, "recorded_at": stamp(now), "result": "pending", "profit_units": None, "clv": None}
            position["position_id"] = hashlib.sha256("|".join(key).encode()).hexdigest()[:24]
            output.append(position)
            keys.add(key)
    return output


def record_forecasts(existing: list, forecasts: list, now: datetime) -> list:
    """Lock the first scheduled pregame forecast, including abstentions."""
    output = list(existing)
    keys = {(r["model_version"], r["candidate"], str(r["game_id"])) for r in existing}
    for row in forecasts:
        key = (row["model_version"], row["candidate"], str(row["game_id"]))
        if key not in keys and "schedule_unmatched" not in row["flags"] and pd.to_datetime(row["kickoff"], utc=True) > now:
            output.append({**row, "recorded_at": stamp(now), "result": "pending", "profit_units": None})
            keys.add(key)
    return output


def forecast_performance(entries: list) -> list:
    result = []
    for candidate in sorted({r["candidate"] for r in entries}):
        sample = [r for r in entries if r["candidate"] == candidate and r["result"] != "pending"]
        decided = [r for r in sample if r["result"] != "push"]
        probs = np.array([r["win_probability"] / (1 - r["push_probability"]) for r in decided])
        actual = np.array([r["result"] == "win" for r in decided], dtype=float)
        errors = np.array([r["actual_total"] - r["projected_total"] for r in sample])
        probs = np.clip(probs, 1e-9, 1 - 1e-9)
        bins = []
        for lower, upper in zip((0., .4, .5, .6, .7), (.4, .5, .6, .7, 1.00001)):
            keep = (probs >= lower) & (probs < upper)
            if keep.any():
                bins.append({"lower": lower, "upper": min(1., upper), "games": int(keep.sum()),
                             "predicted_win_rate": float(probs[keep].mean()), "observed_win_rate": float(actual[keep].mean())})
        result.append({"candidate": candidate, "games": len(sample),
            "pending": sum(r["candidate"] == candidate and r["result"] == "pending" for r in entries),
            "mae": float(abs(errors).mean()) if len(sample) else None,
            "rmse": float(np.sqrt((errors**2).mean())) if len(sample) else None,
            "brier": float(((probs - actual)**2).mean()) if len(decided) else None,
            "log_loss": float(-(actual * np.log(probs) + (1 - actual) * np.log(1 - probs)).mean()) if len(decided) else None,
            "probability_scoring_games": len(decided), "pushes": len(sample) - len(decided),
            "forecast_entries": sum(r["candidate"] == candidate for r in entries),
            "abstentions": sum(r["candidate"] == candidate and not r["eligible"] for r in entries),
            "calibration_bins": bins,
            "definition": "First archived pregame forecast per model/game, including abstentions; conditional win probabilities exclude pushes. No bet ROI inferred."})
    return result


def grade_positions(positions: list, schedule: pd.DataFrame) -> list:
    final = schedule.loc[schedule.status.eq("STATUS_FINAL")].drop_duplicates("game_id", keep="last")
    finals = {str(int(row.game_id)): row for row in final.itertuples()}
    output = []
    for position in positions:
        row = dict(position)
        game = finals.get(str(row["game_id"]))
        if game is not None and pd.notna(game.home_score) and pd.notna(game.away_score):
            actual = float(game.home_score) + float(game.away_score)
            # Score corrections update grading without changing the locked entry.
            won = actual > row["line"] if row["side"] == "over" else actual < row["line"]
            row["actual_total"] = actual
            row["result"] = "push" if actual == row["line"] else "win" if won else "loss"
            payout = row.get("decimal_odds", 1 + (row["american_odds"] / 100 if row["american_odds"] > 0 else 100 / abs(row["american_odds"]))) - 1
            row["profit_units"] = 0. if row["result"] == "push" else payout if won else -1.
        output.append(row)
    return output


def performance(positions: list, candidate: str = PRIMARY) -> dict:
    rows = [p for p in positions if p["candidate"] == candidate and p["result"] != "pending"]
    profits = np.array([p["profit_units"] for p in rows], float)
    low = high = None
    weeks = {}
    for row in rows:
        dt = datetime.fromisoformat(row["kickoff"].replace("Z", "+00:00")).astimezone(TZ)
        group = dt.strftime("%G-%V")
        weeks.setdefault(group, []).append(row["profit_units"])
    if len(rows) >= 50 and len(weeks) >= 8:
        totals = np.array([[sum(p), len(p)] for p in weeks.values()])
        draws = np.random.default_rng(20260908).integers(0, len(totals), (5000, len(totals)))
        sampled = totals[draws].sum(axis=1)
        low, high = np.quantile(sampled[:, 0] / sampled[:, 1], [.025, .975]).tolist()
    return {"bets": len(rows), "pending": sum(p["candidate"] == candidate and p["result"] == "pending" for p in positions),
            "wins": sum(p["result"] == "win" for p in rows), "losses": sum(p["result"] == "loss" for p in rows),
            "pushes": sum(p["result"] == "push" for p in rows), "profit_units": float(profits.sum()),
            "roi": float(profits.mean()) if len(rows) else None, "roi_95_low": low, "roi_95_high": high,
            "week_clusters": len(weeks), "confidence_method": "week-block bootstrap; minimum 50 settled bets and 8 weeks",
            "clv": None, "status": "research_only"}


def evidence(settings) -> list:
    result = []
    report_specs = [("totals_backtest_summary.json", "primary_period"),
                    ("public_superensemble_v2_summary.json", "metrics")]
    for filename, key in report_specs:
        path = settings.reports_dir / filename
        if path.exists():
            report = json.loads(path.read_text())
            for metric in report.get(key, []):
                row = {**metric, "roi": metric.get("flat_roi", metric.get("roi")), "status": "retrospective development; not validated profitable"}
                if row.get("candidate") == "market_consensus_shop":
                    continue
                result.append(row)
    path = settings.reports_dir / "drive_clock_development_summary.json"
    if path.exists():
        for candidate, metric in json.loads(path.read_text()).get("metrics", {}).items():
            if candidate == "market_only":
                continue
            ci = metric.get("roi_week_bootstrap_95") or [None, None]
            result.append({**metric, "candidate": candidate, "roi": metric.get("assumed_minus110_roi"),
                           "roi_95_low": ci[0], "roi_95_high": ci[1], "brier": metric.get("brier"),
                           "status": "prospective shadow; no established profit"})
    return result


def daily(settings=None, now: datetime | None = None) -> dict:
    settings = settings or load_settings()
    now = now or datetime.now(timezone.utc)
    season = now.year if now.month >= 3 else now.year - 1
    settings = replace(settings, season=season)
    site_data = settings.root.parent / "site/data"
    board = {"schema_version": 1, "generated_at": stamp(now), "date": now.astimezone(TZ).date().isoformat(),
             "timezone": "America/New_York", "status": "unavailable", "message": "Live data unavailable; no current picks.",
             "model_version": VERSION, "evidence_status": "research_only", "today_picks": [], "upcoming_picks": [],
             "forecasts": [], "candidates": evidence(settings), "sources": SOURCES, "limitations": LIMITATIONS, "diagnostics": {}}
    board["reports"] = [{"name": "Historical evaluation", "url": "data/research.json"}]
    positions_path = settings.ledger_dir / "positions.json"
    positions = json.loads(positions_path.read_text()) if positions_path.exists() else []
    forecasts_path = settings.ledger_dir / "forecast_entries.json"
    forecast_entries = json.loads(forecasts_path.read_text()) if forecasts_path.exists() else []
    diagnostics = {}
    try:
        schedule, diagnostics = refresh_inputs(settings, now)
        positions = grade_positions(positions, schedule)
        forecast_entries = grade_positions(forecast_entries, schedule)
        events, odds_diagnostics = fetch_odds(settings, now)
        diagnostics.update(odds_diagnostics)
        atomic_write_bytes(settings.root / "data/runtime" / f"odds-{now:%Y%m%dT%H%M%SZ}.json.gz",
                           gzip.compress(json.dumps({"as_of": stamp(now), "events": events}, separators=(",", ":")).encode(), mtime=0))
        odds = quotes_from_events(events, settings, now)
        diagnostics["events_with_totals"] = len(odds)
        diagnostics["fresh_total_quotes"] = sum(q["fresh"] for qs in odds.get("quotes", []) for q in qs)
        diagnostics["fresh_sportsbooks"] = sorted({q["book"] for qs in odds.get("quotes", []) for q in qs if q["fresh"]})
        forecasts = []
        if not odds.empty:
            matched = attach_totals_schedule(odds, schedule)
            diagnostics["schedule_matches"] = int(matched.schedule_match.sum())
            games, projections = candidate_projections(settings, matched, now, diagnostics)
            distribution = json.loads((settings.models_dir / "score_distribution_v1.json").read_text())
            forecasts = score_games(games, projections, distribution, now, diagnostics)
        current_picks = [r for r in forecasts if r["eligible"] and r["candidate"] == PRIMARY]
        current_picks.sort(key=lambda r: r["robust_ev"], reverse=True)
        for row in current_picks:
            day = datetime.fromisoformat(row["kickoff"].replace("Z", "+00:00")).astimezone(TZ).date().isoformat()
            board["today_picks" if day == board["date"] else "upcoming_picks"].append(row)
        snapshot = {"as_of": stamp(now), "model_version": VERSION, "events": events, "forecasts": forecasts,
                    "features": diagnostics.pop("feature_snapshot", {}),
                    "code_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')},
                    "git_commit": os.getenv("GITHUB_SHA"),
                    "model_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in settings.models_dir.glob('*') if p.is_file()}}
        encoded = json.dumps(clean_json(snapshot), allow_nan=False, separators=(",", ":")).encode()
        snapshot_path = settings.root / "data/runtime" / f"snapshot-{now:%Y%m%dT%H%M%SZ}.json.gz"
        compressed = gzip.compress(encoded, mtime=0)
        if snapshot_path.exists() and snapshot_path.read_bytes() != compressed:
            raise RuntimeError("Snapshot collision")
        atomic_write_bytes(snapshot_path, compressed)
        diagnostics["snapshot_sha256"] = hashlib.sha256(encoded).hexdigest()
        positions = record_positions(positions, forecasts, now)
        forecast_entries = record_forecasts(forecast_entries, forecasts, now)
        board.update(status="ok", forecasts=forecasts,
                     message=f"{len(board['today_picks'])} qualifying experimental pick(s) for today. No high-confidence profitable model is established.")
        if len(diagnostics["fresh_sportsbooks"]) < 4:
            board["message"] += f" Only {len(diagnostics['fresh_sportsbooks'])} sportsbooks have verified recent quotes; main picks require four including the execution book."
    except Exception as exc:
        board.update(status="unavailable", today_picks=[], upcoming_picks=[], forecasts=[])
        diagnostics.pop("feature_snapshot", None)
        diagnostics["run_failure"] = safe_failure(exc)
        # Keep no exception URL or credential in public output.
        print(f"Daily refresh unavailable: {safe_failure(exc)}", file=sys.stderr)
        for location in traceback.extract_tb(exc.__traceback__):
            print(f"  {Path(location.filename).name}:{location.lineno} in {location.name}", file=sys.stderr)
    board["diagnostics"] = diagnostics
    board["performance"] = performance(positions)
    board["candidate_performance"] = {name: performance(positions, name) for name in sorted({p["candidate"] for p in positions})}
    board["results"] = positions
    board["forecast_performance"] = forecast_performance(forecast_entries)
    write_json(positions_path, positions)
    write_json(forecasts_path, forecast_entries)
    write_json(site_data / "forecast-performance.json", board["forecast_performance"])
    write_json(site_data / "board.json", board)
    write_json(site_data / "results.json", positions)
    if board["status"] == "ok":
        write_json(site_data / "history" / f"{board['date']}.json", board)
    return clean_json(board)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["daily"])
    parser.parse_args()
    result = daily()
    print(json.dumps({"status": result["status"], "date": result["date"], "today_picks": len(result["today_picks"]),
                      "upcoming_picks": len(result["upcoming_picks"]), "diagnostics": result["diagnostics"]}))
    if result["status"] != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
