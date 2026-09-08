"""Read-only Odds-API.io adapter; validated against September 8, 2026 payloads.

Docs: https://docs.odds-api.io/guides/fetching-odds
No bookmaker selection or subscription mutations are performed. Missing or old
market timestamps stay missing/old; fetching does not establish quote freshness.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from .teams import normalize_team

BASE_URL = "https://api.odds-api.io/v3"
BOOK_KEYS = {"DraftKings": "draftkings", "FanDuel": "fanduel", "BetMGM": "betmgm",
             "Caesars": "caesars", "BetRivers": "betrivers", "Fanatics": "fanatics",
             "Pinnacle": "pinnacle", "BetOnline.ag": "betonlineag", "Bovada": "bovada"}


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _price(value: Any) -> tuple[float, float] | None:
    try:
        decimal = float(value)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(decimal) or decimal <= 1.:
        return None
    # Preserve equivalent float American odds; only the UI may round display.
    american = (decimal - 1) * 100 if decimal >= 2 else -100 / (decimal - 1)
    return float(american), decimal


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    return result if math.isfinite(result) else None


def parse_odds_api_io(payload: Any, now: datetime, allowed_books: tuple[str, ...] = ("draftkings", "fanduel")) -> list[dict[str, Any]]:
    """Convert paired full-game markets into the engine's event contract."""
    events = payload if isinstance(payload, list) else [payload]
    output = []
    for event in events:
        if not isinstance(event, dict) or event.get("status") not in {"pending", "upcoming", "scheduled", "prematch"}:
            continue
        start = _timestamp(event.get("date"))
        if not start or datetime.fromisoformat(start.replace("Z", "+00:00")) <= now:
            continue
        league = str(event.get("league", {})).lower()
        if not ("college" in league or "ncaa" in league):
            continue
        home, away = event.get("home"), event.get("away")
        if not home or not away:
            continue
        books = []
        for title, raw_markets in (event.get("bookmakers") or {}).items():
            book_key = BOOK_KEYS.get(title)
            if book_key not in allowed_books or not isinstance(raw_markets, list):
                continue
            markets = []
            for raw in raw_markets:
                name = raw.get("name")
                key = {"Totals": "totals", "Spread": "spreads", "ML": "h2h"}.get(name)
                if key is None:
                    continue
                stamp = _timestamp(raw.get("updatedAt"))
                outcomes = []
                seen_lines = set()
                for row in raw.get("odds", []) or []:
                    if not isinstance(row, dict):
                        continue
                    left, right = ("over", "under") if key == "totals" else ("home", "away")
                    prices = [_price(row.get(left)), _price(row.get(right))]
                    if any(p is None for p in prices):
                        continue
                    line = _number(row.get("hdp")) if key != "h2h" else None
                    if key != "h2h" and line is None:
                        continue
                    if key == "totals" and (line <= 0 or line * 2 != round(line * 2)):
                        continue  # Split Asian lines are outside engine settlement.
                    if line in seen_lines:
                        continue
                    seen_lines.add(line)
                    names = ("Over", "Under") if key == "totals" else (home, away)
                    for index, side_name in enumerate(names):
                        outcome = {"name": side_name, "price": prices[index][0],
                                   "decimal_price": prices[index][1], "last_update": stamp}
                        if key != "h2h":
                            outcome["point"] = -line if key == "spreads" and index == 1 else line
                        outcomes.append(outcome)
                if outcomes:
                    markets.append({"key": key, "last_update": stamp, "outcomes": outcomes})
            if markets:
                # No invented bookmaker-wide timestamp; use market timestamps.
                books.append({"key": book_key, "title": title, "last_update": None,
                              "markets": markets, "source": "odds_api_io"})
        if any(any(m["key"] == "totals" for m in book["markets"]) for book in books):
            output.append({"id": f"oddsio-{event['id']}", "commence_time": start,
                           "home_team": home, "away_team": away, "bookmakers": books,
                           "source": "odds_api_io", "source_event_id": str(event["id"])})
    return output


def _get(session: requests.Session, path: str, key: str, timeout: float, **params: Any) -> Any:
    try:
        response = session.get(BASE_URL + path, params={"apiKey": key, **params}, timeout=timeout)
    except requests.RequestException:
        # Requests exception text can contain the credential-bearing URL.
        raise RuntimeError("Odds-API.io network request failed") from None
    if response.status_code != 200:
        raise RuntimeError(f"Odds-API.io HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError:
        raise RuntimeError("Odds-API.io returned invalid JSON") from None


def fetch_odds_api_io(session: requests.Session, key: str, now: datetime,
                      timeout: float = 20., allowed_books: tuple[str, ...] = ("draftkings", "fanduel"),
                      max_events: int = 150, days_ahead: int = 8,
                      matchups: list[dict[str, Any]] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read selected books and a bounded NCAA slate, then batch ten events/call.

    Optional matchups (home_team/away_team or home/away) limits requests to a
    known scoreboard slate. No selected books are ever added or changed.
    """
    if not key:
        raise RuntimeError("ODDS_API_IO_KEY is not set")
    now = now.astimezone(timezone.utc)
    max_events = max(0, min(int(max_events), 150))
    selected = _get(session, "/bookmakers/selected", key, timeout)
    selected_names = selected.get("bookmakers", []) if isinstance(selected, dict) else selected
    books = [book for book in selected_names if BOOK_KEYS.get(book) in allowed_books]
    if not books:
        raise RuntimeError("Odds-API.io has no already-selected allowed bookmakers")
    events = _get(session, "/events", key, timeout, sport="american-football", league="usa-college",
                  status="pending", limit=500, **{"from": now.isoformat(), "to": (now + timedelta(days=days_ahead)).isoformat()})
    if not isinstance(events, list):
        raise RuntimeError("Odds-API.io events response is not a list")
    matchup_keys = None if matchups is None else {
        (normalize_team(str(x.get("home_team", x.get("home", "")))),
         normalize_team(str(x.get("away_team", x.get("away", ""))))) for x in matchups}
    filtered = []
    end = now + timedelta(days=days_ahead)
    for event in events:
        start = _timestamp(event.get("date"))
        if not start:
            continue
        date = datetime.fromisoformat(start.replace("Z", "+00:00"))
        if not now < date <= end or event.get("status") != "pending":
            continue
        if not any(token in str(event.get("league", {})).lower() for token in ("college", "ncaa")):
            continue
        pair = (normalize_team(event.get("home", "")), normalize_team(event.get("away", "")))
        if matchup_keys is not None and pair not in matchup_keys:
            continue
        filtered.append(event)
    filtered.sort(key=lambda event: (event["date"], event["id"]))
    chosen = filtered[:max_events]
    normalized, errors = [], []
    for offset in range(0, len(chosen), 10):
        try:
            payload = _get(session, "/odds/multi", key, timeout,
                           eventIds=",".join(str(x["id"]) for x in chosen[offset:offset + 10]),
                           bookmakers=",".join(books))
            normalized.extend(parse_odds_api_io(payload, now, allowed_books))
        except RuntimeError as exc:
            errors.append(str(exc))
            if "HTTP 401" in str(exc) or "HTTP 403" in str(exc) or "HTTP 429" in str(exc):
                break
    market_count = sum(m["key"] == "totals" for e in normalized for b in e["bookmakers"] for m in b["markets"])
    dated_count = sum(m["key"] == "totals" and bool(m["last_update"]) for e in normalized for b in e["bookmakers"] for m in b["markets"])
    return normalized, {"odds_source": "odds_api_io", "selected_bookmakers": books,
                        "available_events": len(filtered), "requested_events": len(chosen),
                        "truncated": len(filtered) > len(chosen), "event_count": len(normalized),
                        "total_markets": market_count, "timestamped_total_markets": dated_count,
                        "errors": errors, "american_rounding": "none; exact decimal_price and equivalent float American odds retained"}
