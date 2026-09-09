"""Lightweight, append-only pregame quote capture; no model fitting or wagers.

Run ``python -m ncaaf_model.closing_collector`` from model/. The daily board
supplies identities for the next 24 hours. Only authoritative full-state IO
receipts are recorded; a near-kickoff observation is not an exact closing line.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
import os
from pathlib import Path

import requests

from .distribution import infer_center, probabilities
from .market_opportunities import TotalQuote, quote_observation
from .odds_api_io import fetch_odds_api_io
from .sources import load_dotenv
from .teams import normalize_team


def _time(value):
    try:
        value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return value.astimezone(timezone.utc) if value.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def capture_observations(events: list[dict], now: datetime, snapshot: str) -> list[dict]:
    """Capture complete paired markets only, preserving exact prices/times."""
    result = []
    for event in events:
        kickoff = _time(event.get("commence_time"))
        if kickoff is None or kickoff <= now:
            continue
        for book in event.get("bookmakers", []):
            if book.get("source") != "odds_api_io":
                continue
            for market in book.get("markets", []):
                if market.get("key") != "totals":
                    continue
                paired = {}
                for outcome in market.get("outcomes", []):
                    try:
                        quote = TotalQuote(str(event["id"]), book["key"], outcome["name"].lower(),
                            float(outcome["point"]), float(outcome["decimal_price"]),
                            market_updated_at=market.get("last_update"), observed_at=market.get("observed_at"),
                            observation_kind=market.get("observation_kind"), source=book["source"],
                            provider_event_id=book.get("source_event_id", event.get("source_event_id")))
                    except (KeyError, ValueError, TypeError):
                        continue
                    if not quote_observation(quote, now)["recent_full_state_observation"]:
                        continue
                    paired.setdefault(quote.line, {}).setdefault(quote.side, []).append(quote)
                for line, sides in paired.items():
                    if any(len(sides.get(side, [])) != 1 for side in ("over", "under")):
                        continue
                    over, under = sides["over"][0], sides["under"][0]
                    observed = _time(over.observed_at)
                    if observed is None or observed >= kickoff or observed > now + timedelta(seconds=5):
                        continue
                    row = {"event_id": over.event_id, "provider_event_id": over.provider_event_id,
                        "home_team": event["home_team"], "away_team": event["away_team"],
                        "kickoff": event["commence_time"], "sportsbook": over.book, "line": line,
                        "over_decimal_odds": over.decimal_odds, "under_decimal_odds": under.decimal_odds,
                        "observed_at": over.observed_at, "market_updated_at": over.market_updated_at,
                        "source": "odds_api_io", "observation_kind": "provider_full_state", "snapshot": snapshot}
                    key = [row[k] for k in ("event_id", "sportsbook", "line", "observed_at", "over_decimal_odds", "under_decimal_odds")]
                    row["observation_id"] = hashlib.sha256(json.dumps(key).encode()).hexdigest()[:24]
                    result.append(row)
    return result


def append_observations(existing: list[dict], new: list[dict]) -> list[dict]:
    output = list(existing)
    known = {row["observation_id"] for row in existing}
    for row in new:
        if row["observation_id"] not in known:
            output.append(row)
            known.add(row["observation_id"])
    return output


def attach_clv(positions: list[dict], observations: list[dict], now: datetime,
               max_lead_minutes: float = 30., sigma: float = 16.) -> tuple[list[dict], dict]:
    """Use the last observed paired quote from the SAME book before kickoff.

    Line selection within a multi-line book snapshot uses the least imbalanced
    paired implied prices, explicitly a proxy for the main line. The optional
    EV diagnostic assumes proportional no-vig prices and a normal integer-score
    distribution with fixed sigma=16, not a fitted fair-probability claim.
    """
    updated = []
    summaries = []
    for position in positions:
        row = dict(position)
        kickoff, entry = _time(row.get("kickoff")), _time(row.get("recorded_at"))
        candidates = []
        if kickoff is not None and entry is not None and row.get("side") in {"over", "under"}:
            for obs in observations:
                observed, obs_kickoff = _time(obs.get("observed_at")), _time(obs.get("kickoff"))
                if observed is None or obs_kickoff is None or obs.get("source") != "odds_api_io" or obs.get("observation_kind") != "provider_full_state":
                    continue
                if obs.get("sportsbook") != row.get("sportsbook") or not entry < observed < kickoff or observed > now:
                    continue
                if abs((obs_kickoff - kickoff).total_seconds()) > 60 or not 0 < (kickoff - observed).total_seconds() <= max_lead_minutes * 60:
                    continue
                if any(normalize_team(str(obs.get(key, ""))) != normalize_team(str(row.get(key, ""))) for key in ("home_team", "away_team")):
                    continue
                try:
                    line, over, under = map(float, (obs["line"], obs["over_decimal_odds"], obs["under_decimal_odds"]))
                    if not all(math.isfinite(x) for x in (line, over, under)) or min(over, under) <= 1 or line < 0 or 2 * line != round(2 * line):
                        continue
                    fair_over = (1 / over) / (1 / over + 1 / under)
                    candidates.append((observed, -abs(fair_over - .5), -abs(line - float(row["line"])), obs, fair_over))
                except (KeyError, TypeError, ValueError):
                    continue
        if candidates:
            _, _, _, close, fair_over = max(candidates, key=lambda item: item[:3])
            old_observed = _time((row.get("clv") or {}).get("observed_at")) if isinstance(row.get("clv"), dict) else None
            if old_observed is None or _time(close["observed_at"]) > old_observed:
                close_line, entry_line = float(close["line"]), float(row["line"])
                close_decimal = close[f"{row['side']}_decimal_odds"]
                entry_decimal = float(row["decimal_odds"])
                center = infer_center(close_line, fair_over, sigma, "normal")
                over, under, push = probabilities(center, entry_line, sigma, "normal")
                modeled_ev = entry_decimal * (over if row["side"] == "over" else under) + push - 1
                row["clv"] = {"observed_at": close["observed_at"],
                    "lead_minutes": (kickoff - _time(close["observed_at"])).total_seconds() / 60,
                    "line": close_line, "decimal_odds": close_decimal,
                    "points": close_line - entry_line if row["side"] == "over" else entry_line - close_line,
                    "closing_fair_ev_at_entry": modeled_ev, "market_updated_at": close.get("market_updated_at"),
                    "observation_id": close["observation_id"], "snapshot": close["snapshot"],
                    "status": "last_observed_pregame_proxy_not_exact_close",
                    "price_model_assumption": f"Proportional no-vig paired closing prices; normal integer-score distribution; fixed sigma={sigma:g}; not a verified fair probability",
                    "line_selection": "Least paired-price imbalance in the latest observed same-book snapshot"}
        updated.append(row)
        if row.get("clv"):
            summaries.append({"position_id": row.get("position_id"), "game_id": row.get("game_id"),
                "candidate": row.get("candidate"), "sportsbook": row.get("sportsbook"), "clv": row["clv"]})
    return updated, {"as_of": now.isoformat(), "positions_with_clv": len(summaries), "positions": summaries,
        "status": "near_kickoff_observation_proxy", "max_lead_minutes": max_lead_minutes,
        "interpretation": "Same-book last observed pregame quotes within 30 minutes; not exact closes or proof of positive EV."}


def collect(root: Path, now: datetime | None = None, session=None) -> dict:
    now = now or datetime.now(timezone.utc)
    load_dotenv(root.parents[1] / ".env")
    ledger = root / "ledger"
    positions_path = ledger / "positions.json"
    observations_path = ledger / "closing_observations.json"
    positions = json.loads(positions_path.read_text()) if positions_path.exists() else []
    existing = json.loads(observations_path.read_text()) if observations_path.exists() else []
    board_path = root.parent / "site/data/board.json"
    board = json.loads(board_path.read_text()) if board_path.exists() else {}
    targets = [row for row in positions + board.get("forecasts", [])
        if _time(row.get("kickoff")) is not None and now < _time(row["kickoff"]) <= now + timedelta(hours=24)]
    events, metadata = [], {"status": "no_targets_next_24_hours", "event_count": 0}
    if targets:
        events, metadata = fetch_odds_api_io(session or requests.Session(), os.getenv("ODDS_API_IO_KEY", ""), now,
            days_ahead=2, matchups=targets, max_events=150)
    received = datetime.now(timezone.utc) if targets else now
    stamp = received.strftime("%Y%m%dT%H%M%S%fZ")
    relative = f"data/runtime/closing/quotes-{stamp}.json.gz"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "xt") as handle:
        json.dump({"as_of": received.isoformat(), "events": events, "metadata": metadata}, handle, allow_nan=False)
    observations = append_observations(existing, capture_observations(events, received, relative))
    positions, summary = attach_clv(positions, observations, received)
    summary.update(provider_metadata=metadata, observation_count=len(observations), snapshot=relative)
    _write(observations_path, observations)
    _write(positions_path, positions)
    _write(ledger / "closing_summary.json", summary)
    _write(root.parent / "site/data/closing-summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        summary = collect(args.root)
    except (RuntimeError, OSError, ValueError) as exc:
        # IO errors already redact credential-bearing request URLs.
        raise SystemExit(f"Closing quote capture failed: {type(exc).__name__}") from None
    print(json.dumps({key: summary[key] for key in ("as_of", "positions_with_clv", "observation_count", "snapshot")}))


if __name__ == "__main__":
    main()
