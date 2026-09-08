from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from html.parser import HTMLParser
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd


COVERS_ODDS_URL = "https://www.covers.com/sport/football/ncaaf/odds"

_TABLE_MARKETS = {
    "moneyline-table": "h2h",
    "spread-table": "spreads",
    "total-table": "totals",
}

_BOOK_KEYS = {
    "BetMGM": "betmgm",
    "bet365": "bet365",
    "DraftKings": "draftkings",
    "FanDuel": "fanduel",
    "Fanatics Sportsbook": "fanatics",
    "BetRivers": "betrivers",
    "Caesars": "caesars",
    "theScore Bet": "thescorebet",
    "Hard Rock Bet": "hardrockbet",
}

_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}

EASTERN = ZoneInfo("America/New_York")


def _classes(attributes: dict[str, str]) -> set[str]:
    return set(attributes.get("class", "").split())


def _american_price(value: str) -> int | None:
    cleaned = html.unescape(value).replace("\xa0", " ").strip().lower()
    if cleaned in {"even", "ev", "evens"}:
        return 100
    match = re.search(r"[+-]?\d+", cleaned)
    if not match:
        return None
    price = int(match.group())
    return price if abs(price) >= 100 else None


def _market_point(market: str, value: str) -> float | None:
    cleaned = html.unescape(value).replace("\xa0", " ").strip().lower()
    if market == "spreads":
        match = re.search(r"[+-]\s*\d+(?:\.\d+)?", cleaned)
    elif market == "totals":
        match = re.search(r"[ou]\s*(\d+(?:\.\d+)?)", cleaned)
    else:
        return None
    if not match:
        return None
    numeric = match.group(1) if market == "totals" else match.group().replace(" ", "")
    return float(numeric)


class _CoversOddsParser(HTMLParser):
    """Read the three server-rendered odds tables without a browser dependency."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.table_id: str | None = None
        self.table_depth: int | None = None
        self.row: dict[str, Any] | None = None
        self.row_depth: int | None = None
        self.cell: dict[str, Any] | None = None
        self.cell_depth: int | None = None
        self.side: str | None = None
        self.side_depth: int | None = None
        self.side_text: list[str] = []
        self.american_depth: int | None = None
        self.american_text: list[str] = []
        self.strong_depth: int | None = None
        self.strong_text: list[str] = []
        self.game_time_depth: int | None = None
        self.game_time_text: list[str] = []
        self.rows: dict[str, list[dict[str, Any]]] = {value: [] for value in _TABLE_MARKETS.values()}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _VOID_TAGS:
            self.depth += 1
        attributes = {key: value or "" for key, value in attrs}
        if tag == "table" and attributes.get("id") in _TABLE_MARKETS:
            self.table_id = attributes["id"]
            self.table_depth = self.depth
            return
        if not self.table_id:
            return
        if tag == "tr" and "oddsGameRow" in _classes(attributes):
            self.row = {
                "game_id": None,
                "away_code": None,
                "home_code": None,
                "away_slug": None,
                "home_slug": None,
                "game_time": None,
                "books": {},
            }
            self.row_depth = self.depth
            return
        if self.row is None:
            return
        if tag == "div" and "game-time" in _classes(attributes):
            self.game_time_depth = self.depth
            self.game_time_text = []
            return
        if tag == "td" and "liveOddsCell" in _classes(attributes):
            game_id = attributes.get("data-game")
            if game_id:
                self.row["game_id"] = game_id
            self.cell = {
                "book": attributes.get("data-book", ""),
                "updated_epoch": attributes.get("data-date", ""),
                "away": {},
                "home": {},
            }
            self.cell_depth = self.depth
            return
        if tag == "div" and ({"away-cell", "home-cell"} & _classes(attributes)):
            self.side = "away" if "away-cell" in _classes(attributes) else "home"
            self.side_depth = self.depth
            self.side_text = []
            return
        if tag == "img" and not self.cell and self.side:
            source = attributes.get("src", "")
            match = re.search(r"/([^/]+)\.(?:svg|png)(?:\?|$)", source, flags=re.IGNORECASE)
            if match:
                self.row[f"{self.side}_slug"] = match.group(1)
            return
        if tag == "span" and "__american" in _classes(attributes) and self.cell and self.side:
            self.american_depth = self.depth
            self.american_text = []
            return
        if tag == "strong" and not self.cell and self.side:
            self.strong_depth = self.depth
            self.strong_text = []

    def handle_data(self, data: str) -> None:
        if self.american_depth is not None:
            self.american_text.append(data)
        elif self.strong_depth is not None:
            self.strong_text.append(data)
        elif self.game_time_depth is not None:
            self.game_time_text.append(data)
        elif self.cell and self.side:
            self.side_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS:
            return
        if tag == "span" and self.american_depth == self.depth:
            if self.cell and self.side:
                self.cell[self.side]["price"] = _american_price("".join(self.american_text))
            self.american_depth = None
            self.american_text = []
        elif tag == "strong" and self.strong_depth == self.depth:
            if self.row is not None and self.side:
                self.row[f"{self.side}_code"] = "".join(self.strong_text).strip()
            self.strong_depth = None
            self.strong_text = []
        elif tag == "div" and self.game_time_depth == self.depth:
            if self.row is not None:
                self.row["game_time"] = " ".join("".join(self.game_time_text).split())
            self.game_time_depth = None
            self.game_time_text = []
        elif tag == "div" and self.side_depth == self.depth:
            if self.cell and self.side and self.table_id:
                market = _TABLE_MARKETS[self.table_id]
                self.cell[self.side]["point"] = _market_point(market, " ".join(self.side_text))
            self.side = None
            self.side_depth = None
            self.side_text = []
        elif tag == "td" and self.cell_depth == self.depth:
            if self.row is not None and self.cell:
                self.row["books"][self.cell["book"]] = self.cell
            self.cell = None
            self.cell_depth = None
            self.side = None
            self.side_depth = None
        elif tag == "tr" and self.row_depth == self.depth:
            if self.row and self.row.get("game_id") and self.table_id:
                self.rows[_TABLE_MARKETS[self.table_id]].append(self.row)
            self.row = None
            self.row_depth = None
        elif tag == "table" and self.table_depth == self.depth:
            self.table_id = None
            self.table_depth = None
        self.depth -= 1


def _code(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _iso_from_epoch(values: list[str]) -> str | None:
    epochs: list[int] = []
    for value in values:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            epochs.append(parsed)
    if not epochs:
        return None
    return datetime.fromtimestamp(max(epochs), timezone.utc).isoformat().replace("+00:00", "Z")


def _covers_kickoff(value: Any, now: datetime) -> datetime | None:
    text = " ".join(str(value or "").replace(",", " ").split())
    local_now = now.astimezone(EASTERN)
    time_match = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    if not time_match:
        return None
    hour, minute = int(time_match.group(1)), int(time_match.group(2))
    lowered = text.lower()
    if lowered.startswith("today"):
        day = local_now.date()
    elif lowered.startswith("tomorrow"):
        day = local_now.date() + timedelta(days=1)
    else:
        date_match = re.search(
            r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2})\b",
            lowered,
        )
        if not date_match:
            return None
        month = datetime.strptime(date_match.group(1), "%b").month
        year = local_now.year
        day = datetime(year, month, int(date_match.group(2))).date()
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=EASTERN).astimezone(timezone.utc)


def _schedule_lookup(
    schedule: pd.DataFrame, now: datetime
) -> tuple[dict[tuple[str, str], list[pd.Series]], list[pd.Series]]:
    frame = schedule.copy()
    frame["_kickoff"] = pd.to_datetime(frame["game_date"], utc=True, errors="coerce")
    start = pd.Timestamp(now - timedelta(hours=18))
    end = pd.Timestamp(now + timedelta(days=15))
    frame = frame.loc[frame["_kickoff"].between(start, end)]
    lookup: dict[tuple[str, str], list[pd.Series]] = {}
    rows: list[pd.Series] = []
    for _, row in frame.iterrows():
        rows.append(row)
        key = (_code(row.get("away_abbreviation")), _code(row.get("home_abbreviation")))
        lookup.setdefault(key, []).append(row)
    return lookup, rows


def _code_similarity(value: Any, abbreviation: Any, team_name: Any) -> float:
    candidate = _code(value)
    if not candidate:
        return 0.0
    options = [_code(abbreviation), _code(team_name)]
    scores = [SequenceMatcher(None, candidate, option).ratio() for option in options if option]
    if any(candidate == option for option in options):
        return 1.0
    if any(len(candidate) >= 3 and (candidate in option or option in candidate) for option in options):
        scores.append(0.9)
    return max(scores, default=0.0)


def _choose_schedule_game(
    base: dict[str, Any],
    direct: dict[tuple[str, str], list[pd.Series]],
    schedule_rows: list[pd.Series],
    now: datetime,
) -> pd.Series | None:
    key = (_code(base.get("away_code")), _code(base.get("home_code")))
    exact = direct.get(key, [])
    expected = _covers_kickoff(base.get("game_time"), now)
    if len(exact) == 1:
        return exact[0]
    if exact and expected:
        return min(exact, key=lambda row: abs(pd.Timestamp(row["_kickoff"]) - pd.Timestamp(expected)))

    candidates = schedule_rows
    if expected:
        expected_day = expected.astimezone(EASTERN).date()
        dated = [
            row
            for row in schedule_rows
            if pd.Timestamp(row["_kickoff"]).to_pydatetime().astimezone(EASTERN).date() == expected_day
        ]
        if dated:
            candidates = dated
    ranked: list[tuple[float, float, float, pd.Series]] = []
    for row in candidates:
        away = max(
            _code_similarity(base.get("away_code"), row.get("away_abbreviation"), row.get("away_team")),
            _code_similarity(base.get("away_slug"), row.get("away_abbreviation"), row.get("away_team")),
        )
        home = max(
            _code_similarity(base.get("home_code"), row.get("home_abbreviation"), row.get("home_team")),
            _code_similarity(base.get("home_slug"), row.get("home_abbreviation"), row.get("home_team")),
        )
        kickoff_bonus = 0.0
        if expected:
            delta_hours = abs((pd.Timestamp(row["_kickoff"]).to_pydatetime() - expected).total_seconds()) / 3600.0
            kickoff_bonus = max(0.0, 1.0 - delta_hours / 6.0) * 0.25
        ranked.append((away + home + kickoff_bonus, away, home, row))
    ranked.sort(key=lambda value: value[0], reverse=True)
    if not ranked:
        return None
    best = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else -1.0
    # Require one strong identity match and a unique combined matchup. This
    # resolves common abbreviation differences (OKLA/OU, NCST/NCSU) without
    # guessing when both sides are ambiguous.
    if max(best[1], best[2]) < 0.72 or best[0] - second_score < 0.12:
        return None
    return best[3]


def parse_covers_odds(
    document: str,
    schedule: pd.DataFrame,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Normalize Covers' public comparison page to The Odds API event contract."""
    parser = _CoversOddsParser()
    parser.feed(document)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)
    schedule_by_codes, schedule_rows = _schedule_lookup(schedule, current)

    markets_by_game: dict[str, dict[str, dict[str, Any]]] = {}
    base_by_game: dict[str, dict[str, Any]] = {}
    for market, rows in parser.rows.items():
        for row in rows:
            game_id = str(row["game_id"])
            markets_by_game.setdefault(game_id, {})[market] = row["books"]
            base_by_game.setdefault(game_id, row)

    events: list[dict[str, Any]] = []
    unmatched: list[str] = []
    skipped_final = 0
    for game_id, base in base_by_game.items():
        if str(base.get("game_time", "")).strip().upper() == "FINAL":
            skipped_final += 1
            continue
        game = _choose_schedule_game(base, schedule_by_codes, schedule_rows, current)
        if game is None:
            unmatched.append(f"{base.get('away_code')}@{base.get('home_code')}")
            continue
        away_team = str(game["away_team"])
        home_team = str(game["home_team"])
        bookmakers: list[dict[str, Any]] = []
        all_books = {
            name
            for market_books in markets_by_game[game_id].values()
            for name in market_books
            if name in _BOOK_KEYS
        }
        for book_name in sorted(all_books):
            markets: list[dict[str, Any]] = []
            update_epochs: list[str] = []
            for market in ("h2h", "spreads", "totals"):
                cell = markets_by_game[game_id].get(market, {}).get(book_name)
                if not cell:
                    continue
                update_epochs.append(cell.get("updated_epoch", ""))
                away, home = cell.get("away", {}), cell.get("home", {})
                if away.get("price") is None or home.get("price") is None:
                    continue
                if market == "h2h":
                    outcomes = [
                        {"name": away_team, "price": away["price"]},
                        {"name": home_team, "price": home["price"]},
                    ]
                elif market == "spreads" and away.get("point") is not None and home.get("point") is not None:
                    outcomes = [
                        {"name": away_team, "price": away["price"], "point": away["point"]},
                        {"name": home_team, "price": home["price"], "point": home["point"]},
                    ]
                elif market == "totals" and away.get("point") is not None and home.get("point") is not None:
                    outcomes = [
                        {"name": "Over", "price": away["price"], "point": away["point"]},
                        {"name": "Under", "price": home["price"], "point": home["point"]},
                    ]
                else:
                    continue
                markets.append({"key": market, "outcomes": outcomes})
            if markets:
                bookmakers.append(
                    {
                        "key": _BOOK_KEYS[book_name],
                        "title": book_name,
                        "last_update": _iso_from_epoch(update_epochs),
                        "markets": markets,
                    }
                )
        events.append(
            {
                "id": f"covers-{game_id}",
                "commence_time": pd.Timestamp(game["game_date"]).isoformat().replace("+00:00", "Z"),
                "home_team": home_team,
                "away_team": away_team,
                "bookmakers": bookmakers,
            }
        )

    diagnostics = {
        "source": "covers_public_odds_page",
        "table_game_counts": {market: len(rows) for market, rows in parser.rows.items()},
        "raw_game_count": len(base_by_game),
        "skipped_final_game_count": skipped_final,
        "upcoming_or_live_game_count": len(base_by_game) - skipped_final,
        "schedule_window_rows": len(schedule_rows),
        "matched_event_count": len(events),
        "unmatched_game_count": len(unmatched),
        "unmatched_games_sample": unmatched[:20],
    }
    return events, diagnostics
