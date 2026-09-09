"""Prospective fixed weather-rule paper ledger, separate from score forecasts.

The historical conditional hit rate is not an individual-game probability.
No probability, expected value, score projection or Kelly stake is fabricated.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

import pandas as pd

VERSION = "weather-under-v1-20260908"
CANDIDATE = "published_weather_under"
MIN_DECIMAL = 1 + 100 / 110
ZONE = ZoneInfo("America/New_York")


def collect_inputs(settings, schedule, now):
    """Collect weather before odds so quote receipts remain recent at selection."""
    if not (settings.models_dir / "weather_venues_v1.json").exists():
        return {"status": "unavailable", "features": [], "message": "Weather venue catalog unavailable."}
    from .weather_candidate import collect_weather_features
    games = []
    for game in schedule.to_dict("records"):
        kickoff = pd.to_datetime(game.get("game_date"), utc=True, errors="coerce")
        if pd.isna(kickoff) or kickoff <= now or kickoff.tz_convert(ZONE).date() != now.astimezone(ZONE).date():
            continue
        if game.get("status") != "STATUS_SCHEDULED":
            continue
        games.append({"game_id": str(int(game["game_id"])), "kickoff": kickoff.isoformat(),
                      "home_team": game["home_team"], "away_team": game["away_team"]})
    try:
        features = collect_weather_features(settings.root, games, now=now)
        return {"status": "ok", "features": features, "message": f"{len(features)} game-day weather records inspected."}
    except Exception as exc:
        return {"status": "unavailable", "features": [], "message": f"Weather refresh unavailable ({type(exc).__name__})."}


def select_paper_weather(games, features, now):
    from .runtime import schedule_is_upcoming, stamp
    lookup = {str(row["game_id"]): row for row in features}
    rows = []
    for game in games.to_dict("records"):
        if pd.isna(game.get("game_id")):
            continue
        game_id = str(int(game["game_id"]))
        feature = lookup.get(game_id)
        if feature is None:
            continue
        row = {"game_id": game_id, "model_version": VERSION, "candidate": CANDIDATE,
               "home_team": game["home_team"], "away_team": game["away_team"],
               "kickoff": game.get("canonical_kickoff"), "side": "under", "eligible": False,
               "weather": feature, "win_probability": None, "expected_value": None,
               "paper_units": 1., "execution_confirmed": False, "flags": []}
        flags = row["flags"]
        kickoff = pd.to_datetime(row["kickoff"], utc=True, errors="coerce")
        if (not schedule_is_upcoming(game, now) or pd.isna(kickoff) or
                kickoff.tz_convert(ZONE).date() != now.astimezone(ZONE).date()):
            flags.append("not_official_future_game_today")
        if feature.get("weather_rule_match") is not True or feature.get("flags"):
            flags.append("weather_criterion_unmet_or_unavailable")
        quotes = [q for q in game.get("quotes", []) if q.get("fresh") and q.get("freshness_basis") == "provider_full_state_receipt"]
        # Recheck receipt age at selection, independently of a cached fresh flag.
        quotes = [q for q in quotes if pd.notna(pd.to_datetime(q.get("quote_time"), utc=True, errors="coerce")) and
                  -5 <= (now - pd.to_datetime(q["quote_time"], utc=True)).total_seconds() <= 120]
        by_book = {q["book"]: q for q in quotes}
        if len(by_book) < 2:
            flags.append("two_current_books_required")
        reference_line = median(q["line"] for q in by_book.values()) if by_book else None
        choices = []
        for q in by_book.values():
            price = float(q["under_price"])
            decimal = 1 + (price / 100 if price > 0 else 100 / abs(price))
            if math.isfinite(decimal) and decimal + 1e-12 >= MIN_DECIMAL and q["line"] >= reference_line:
                choices.append((q, decimal))
        if not choices:
            flags.append("no_under_price_at_least_minus110_at_or_above_book_median")
        else:
            quote, decimal = max(choices, key=lambda pair: (pair[0]["line"], pair[1], pair[0]["book"]))
            row.update(line=quote["line"], decimal_odds=decimal, american_odds=quote["under_price"],
                       sportsbook=quote["book"], quote_time=quote["quote_time"],
                       observed_at=quote.get("observed_at"), market_updated_at=quote.get("market_updated_at"),
                       reference_total=reference_line, reference_books=sorted(by_book),
                       freshness_basis="provider_full_state_receipt")
        row["eligible"] = not flags
        row["as_of"] = stamp(now)
        rows.append(row)
    return rows


def publish_weather(settings, schedule, games, state, now):
    from .runtime import grade_positions, performance, record_positions, write_json, stamp
    path = settings.ledger_dir / "weather_positions.json"
    positions = json.loads(path.read_text()) if path.exists() else []
    positions = grade_positions(positions, schedule, now=now)
    forecasts_path = settings.ledger_dir / "weather_forecasts.json"
    forecasts = json.loads(forecasts_path.read_text()) if forecasts_path.exists() else []
    rows = select_paper_weather(games, state.get("features", []), now) if state.get("status") == "ok" and not games.empty else []
    keys = {(r["model_version"], str(r["game_id"])) for r in forecasts}
    for row in rows:
        key = VERSION, row["game_id"]
        if key not in keys:
            forecasts.append({**row, "recorded_at": stamp(now)})
            keys.add(key)
    positions = record_positions(positions, rows, now)
    write_json(path, positions)
    write_json(forecasts_path, forecasts)
    # Full features include unavailable games, preserving the denominator.
    archive = {"version": VERSION, "as_of": stamp(now), "state": state, "forecasts": rows}
    digest = hashlib.sha256(json.dumps(archive, sort_keys=True, default=str).encode()).hexdigest()[:16]
    write_json(settings.root / f"data/runtime/weather/decision-{now:%Y%m%dT%H%M%SZ}-{digest}.json", archive)
    report_path = settings.reports_dir / "weather_published_hypothesis_results.json"
    evidence = {}
    if report_path.exists():
        report = json.loads(report_path.read_text())
        pooled = report["pooled"]
        ci = pooled["weather_rule_roi_95_week_bootstrap"]
        evidence = {**pooled["weather_rule"], "roi_95_low": ci[0], "roi_95_high": ci[1],
                    "price_assumption": -110, "status": "reused_development_only"}
    return {"version": VERSION, "status": state["status"], "message": state["message"],
            "as_of": stamp(now), "evidence": evidence,
            "today_picks": [r for r in rows if r["eligible"]],
            "forecast_count": len(state.get("features", [])),
            "qualifying_count": sum(r["eligible"] for r in rows),
            "performance": performance(positions, CANDIDATE, VERSION), "results": positions,
            "rule": "Wind >7.78 mph; kickoff temperature <64.81 F; relative humidity >56.8%; fixed GFS forecast lead of at least 48 hours.",
            "price_policy": "Two freshly observed books; Under at or above their median main line, with decimal payout at least 1.90909 (-110). Highest qualifying line, then best price.",
            "interpretation": "Fixed-rule paper experiment. Group historical returns are not individual win probabilities or a live EV estimate. No bets are placed."}
