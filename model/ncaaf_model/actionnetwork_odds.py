from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests


ACTION_NETWORK_BASE_URL = "https://api.actionnetwork.com/web"
ACTION_NETWORK_BOOK_ALIASES = {
    "betmgm": {"betmgm", "playmgm", "njplaymgm"},
    "betrivers": {"betrivers", "sugarhouse", "rushstreet", "njsugarhouse"},
    "draftkings": {"draftkings", "dk"},
    "fanduel": {"fanduel"},
    "caesars": {"caesars", "williamhill", "willhill"},
    "fanatics": {"fanatics"},
}


def _token(value: Any) -> str:
    return "".join(char for char in str(value or "").lower() if char.isalnum())


def _american(value: Any) -> int | None:
    try:
        price = int(value)
    except (TypeError, ValueError):
        return None
    return price if price else None


def _decimal_to_american(value: Any) -> int | None:
    try:
        decimal = float(value)
    except (TypeError, ValueError):
        return None
    if decimal <= 1:
        return None
    if decimal >= 2:
        return int(round((decimal - 1) * 100))
    return int(round(-100 / (decimal - 1)))


def _book_key(book: dict[str, Any]) -> str:
    candidates = {
        _token(book.get("source_name")),
        _token(book.get("display_name")),
        _token(book.get("parent_name")),
    }
    for key, aliases in ACTION_NETWORK_BOOK_ALIASES.items():
        if any(candidate in aliases or any(alias in candidate for alias in aliases) for candidate in candidates):
            return key
    return ""


def _bookmakers(payload: dict[str, Any], allowed_books: tuple[str, ...], state: str) -> dict[int, tuple[str, str]]:
    selected: dict[int, tuple[str, str]] = {}
    allowed = set(allowed_books)
    for book in payload.get("books", []) or []:
        if not isinstance(book, dict):
            continue
        try:
            book_id = int(book.get("id"))
        except (TypeError, ValueError):
            continue
        key = _book_key(book)
        states = {str(item).upper() for item in (book.get("meta") or {}).get("states", []) or []}
        if book_id <= 0 or key not in allowed or (state and state.upper() not in states):
            continue
        title = str(book.get("parent_name") or book.get("display_name") or key).strip()
        selected[book_id] = (key, title)
    return selected


def _team_name(teams: dict[int, dict[str, Any]], team_id: Any) -> str:
    try:
        team = teams.get(int(team_id), {})
    except (TypeError, ValueError):
        team = {}
    return str(team.get("full_name") or team.get("display_name") or "").strip()


def _teams(game: dict[str, Any]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for team in game.get("teams", []) or []:
        if isinstance(team, dict):
            try:
                result[int(team["id"])] = team
            except (KeyError, TypeError, ValueError):
                pass
    return result


def _outcomes(rows: Any, market: str, home: str, away: str) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    output: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("is_live") or row.get("is_alt_market"):
            continue
        if str(row.get("line_status") or "normal").lower() not in {"", "normal"}:
            continue
        side = str(row.get("side") or "").lower()
        price = _american(row.get("odds")) or _decimal_to_american(row.get("odds"))
        if price is None:
            continue
        if market == "h2h":
            name = home if side == "home" else away if side == "away" else ""
            point = None
        elif market == "spreads":
            name = home if side == "home" else away if side == "away" else ""
            try:
                point = float(row.get("value"))
            except (TypeError, ValueError):
                continue
        elif market == "totals":
            name = side.title() if side in {"over", "under"} else ""
            try:
                point = float(row.get("value"))
            except (TypeError, ValueError):
                continue
        else:
            continue
        if not name:
            continue
        outcome = {"name": name, "price": price}
        if point is not None:
            outcome["point"] = point
        output.append(outcome)
    return output


def parse_scoreboard(payload: dict[str, Any], books: dict[int, tuple[str, str]], now: datetime | None = None) -> list[dict[str, Any]]:
    current = now or datetime.now(timezone.utc)
    events: list[dict[str, Any]] = []
    for game in payload.get("games", []) or []:
        if not isinstance(game, dict):
            continue
        start = str(game.get("start_time") or "")
        try:
            kickoff = datetime.fromisoformat(start.replace("Z", "+00:00"))
        except ValueError:
            continue
        if kickoff <= current or str(game.get("status") or game.get("real_status") or "").lower() not in {"", "scheduled", "pregame", "created"}:
            continue
        teams = _teams(game)
        home = _team_name(teams, game.get("home_team_id"))
        away = _team_name(teams, game.get("away_team_id"))
        if not home or not away:
            continue
        event = {"id": f"actionnetwork-{game.get('id')}", "commence_time": start, "home_team": home, "away_team": away, "bookmakers": []}
        markets_by_book = game.get("markets") or {}
        for book_id, (key, title) in books.items():
            book_payload = markets_by_book.get(str(book_id)) or markets_by_book.get(book_id) or {}
            raw = book_payload.get("event") or {}
            parsed = []
            for market, action_key in (("h2h", "moneyline"), ("spreads", "spread"), ("totals", "total")):
                outcomes = _outcomes(raw.get(action_key), market, home, away)
                if outcomes:
                    parsed.append({"key": market, "outcomes": outcomes})
            if parsed:
                event["bookmakers"].append({"key": key, "title": title, "last_update": None, "markets": parsed})
        if event["bookmakers"]:
            events.append(event)
    return events


def fetch_actionnetwork_odds(
    session: requests.Session,
    allowed_books: tuple[str, ...],
    timeout: float,
    state: str = "NY",
    base_url: str = ACTION_NETWORK_BASE_URL,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; ncaaf-moneyline-research/0.3)",
        "Origin": "https://www.actionnetwork.com",
        "Referer": "https://www.actionnetwork.com/ncaaf/odds",
        "Accept": "application/json,text/plain,*/*",
    }
    root = base_url.rstrip("/")
    books_response = session.get(f"{root}/v1/books", headers=headers, timeout=timeout)
    books_response.raise_for_status()
    books = _bookmakers(books_response.json(), allowed_books, state)
    if not books:
        raise RuntimeError("Action Network returned no allowed in-state sportsbooks")
    scoreboard = session.get(
        f"{root}/v2/scoreboard/ncaaf",
        params={"bookIds": ",".join(str(book_id) for book_id in sorted(books))},
        headers=headers,
        timeout=timeout,
    )
    scoreboard.raise_for_status()
    events = parse_scoreboard(scoreboard.json(), books)
    if not events:
        raise RuntimeError("Action Network returned no usable pregame NCAAF odds")
    return events, {"odds_source": "actionnetwork_public", "book_count": str(len(books)), "event_count": str(len(events))}
