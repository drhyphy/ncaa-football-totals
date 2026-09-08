from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from .config import Settings
from .storage import atomic_write_bytes, atomic_write_json, sha256_bytes, utc_now
from .teams import normalize_team


ODDS_BASE_URL = "https://api.the-odds-api.com/v4"
ODDS_SPORT = "americanfootball_ncaaf"
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"


def _atomic_write_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".parquet", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        frame.to_parquet(temporary, index=False)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def merge_schedule_frames(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    """Retain the full schedule while allowing refreshed rows to update results."""
    if existing.empty:
        return incoming.copy()
    if incoming.empty:
        return existing.copy()
    columns = list(dict.fromkeys([*existing.columns, *incoming.columns]))
    merged = pd.concat(
        [existing.reindex(columns=columns), incoming.reindex(columns=columns)],
        ignore_index=True,
    )
    return merged.drop_duplicates("game_id", keep="last").sort_values(["game_date", "game_id"]).reset_index(drop=True)


def parse_espn_scoreboard(payload: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for event in payload.get("events", []):
        competitions = event.get("competitions") or []
        if not competitions:
            continue
        competition = competitions[0]
        competitors = {item.get("homeAway"): item for item in competition.get("competitors", [])}
        home = competitors.get("home")
        away = competitors.get("away")
        if not home or not away:
            continue
        home_team = home.get("team", {})
        away_team = away.get("team", {})
        status = (competition.get("status", {}).get("type", {}).get("name")
                  or event.get("status", {}).get("type", {}).get("name"))

        def score(item: dict[str, Any]) -> float | None:
            value = item.get("score")
            if value in (None, ""):
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        rows.append(
            {
                "game_id": int(event["id"]),
                "season": int(event.get("season", {}).get("year")),
                "week": int(event.get("week", {}).get("number", 0)),
                "season_type": int(event.get("season", {}).get("type", 2)),
                "game_date": event.get("date") or competition.get("date"),
                "neutral_site": bool(competition.get("neutralSite", False)),
                "conference_competition": bool(competition.get("conferenceCompetition", False)),
                "home_id": int(home_team["id"]),
                "away_id": int(away_team["id"]),
                "home_team": home_team.get("displayName"),
                "away_team": away_team.get("displayName"),
                "home_abbreviation": home_team.get("abbreviation"),
                "away_abbreviation": away_team.get("abbreviation"),
                "home_score": score(home),
                "away_score": score(away),
                "home_winner": bool(home.get("winner", False)),
                "away_winner": bool(away.get("winner", False)),
                "venue": competition.get("venue", {}).get("fullName"),
                "attendance": competition.get("attendance"),
                "status": status,
            }
        )
    return pd.DataFrame(rows)


def _week_window(value: date) -> tuple[date, date]:
    start = value - timedelta(days=value.weekday())
    return start, start + timedelta(days=6)


def _teams_compatible(left: str, right: str) -> bool:
    a, b = normalize_team(left), normalize_team(right)
    return a == b or (min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)))


def _event_has_schedule_match(event: dict[str, Any], schedule: pd.DataFrame) -> bool:
    if schedule.empty:
        return False
    kickoff = pd.to_datetime(event.get("commence_time"), utc=True, errors="coerce")
    if pd.isna(kickoff):
        return False
    scheduled = pd.to_datetime(schedule["game_date"], utc=True, errors="coerce")
    window = schedule.loc[(scheduled - kickoff).abs().le(pd.Timedelta(hours=36))]
    return bool(
        window.apply(
            lambda row: _teams_compatible(str(event.get("home_team", "")), str(row["home_team"]))
            and _teams_compatible(str(event.get("away_team", "")), str(row["away_team"])),
            axis=1,
        ).any()
    )


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def find_odds_api_key(settings: Settings) -> str:
    load_dotenv(settings.root.parent / ".env")
    value = os.getenv("ODDS_API_KEY", "").strip()
    if not value:
        raise RuntimeError("ODDS_API_KEY is not set; credential values are never logged")
    return value


class DataClient:
    def __init__(self, settings: Settings, timeout: float = 45.0) -> None:
        self.settings = settings
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "ncaaf-moneyline-research/0.1"

    def _download(self, url: str, path: Path, refresh: bool = False) -> Path:
        if path.exists() and not refresh:
            return path
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        content = response.content
        atomic_write_bytes(path, content)
        atomic_write_json(
            path.with_suffix(path.suffix + ".meta.json"),
            {
                "source_url": url,
                "retrieved_at": utc_now(),
                "sha256": sha256_bytes(content),
                "content_type": response.headers.get("content-type"),
                "last_modified": response.headers.get("last-modified"),
            },
        )
        return path

    def archive_season(self, season: int, refresh: bool = False) -> dict[str, Path]:
        paths: dict[str, Path] = {}
        for source in ("schedule", "betting", "power_index"):
            url = self.settings.data_urls[source].format(season=season)
            name = {
                "schedule": f"cfb_schedule_{season}.parquet",
                "betting": f"betting_{season}.parquet",
                "power_index": f"power_index_{season}.parquet",
            }[source]
            path = self.settings.raw_dir / "sportsdataverse" / name
            previous = pd.read_parquet(path) if source == "schedule" and refresh and path.exists() else pd.DataFrame()
            paths[source] = self._download(url, path, refresh)
            if source == "schedule" and refresh and not previous.empty:
                incoming = pd.read_parquet(path)
                merged = merge_schedule_frames(previous, incoming)
                _atomic_write_parquet(path, merged)
                metadata_path = path.with_suffix(path.suffix + ".meta.json")
                metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
                metadata.update(
                    {
                        "incoming_rows": int(len(incoming)),
                        "previous_rows": int(len(previous)),
                        "merged_rows": int(len(merged)),
                        "merge_policy": "game_id union; refreshed row wins",
                    }
                )
                atomic_write_json(metadata_path, metadata)
        return paths

    def ensure_schedule_for_odds(self, odds_path: Path) -> dict[str, int]:
        """Supplement partial release assets with ESPN rows for unmatched odds weeks."""
        payload = load_json(odds_path)
        schedule_path = self.settings.raw_dir / "sportsdataverse" / f"cfb_schedule_{self.settings.season}.parquet"
        schedule = pd.read_parquet(schedule_path) if schedule_path.exists() else pd.DataFrame()
        unmatched = [event for event in payload if not _event_has_schedule_match(event, schedule)]
        windows: set[tuple[date, date]] = set()
        for event in unmatched:
            kickoff = pd.to_datetime(event.get("commence_time"), utc=True, errors="coerce")
            if pd.notna(kickoff):
                windows.add(_week_window(kickoff.date()))
        incoming_frames: list[pd.DataFrame] = []
        archive_dir = self.settings.raw_dir / "espn_scoreboard"
        archive_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        for start, end in sorted(windows):
            for group in (80, 81):
                response = self.session.get(
                    ESPN_SCOREBOARD_URL,
                    params={
                        "dates": f"{start:%Y%m%d}-{end:%Y%m%d}",
                        "limit": 1000,
                        "groups": group,
                    },
                    headers={
                        # ESPN's edge currently rejects the project-level UA while
                        # serving the same public JSON to standard HTTP clients.
                        "User-Agent": "curl/8.7.1",
                        "Accept": "application/json,text/plain,*/*",
                    },
                    timeout=self.timeout,
                )
                response.raise_for_status()
                content = response.content
                raw_path = archive_dir / f"scoreboard_g{group}_{start:%Y%m%d}_{end:%Y%m%d}_{stamp}.json"
                atomic_write_bytes(raw_path, content)
                atomic_write_json(
                    raw_path.with_suffix(".meta.json"),
                    {
                        "source_url": response.url,
                        "retrieved_at": utc_now(),
                        "sha256": sha256_bytes(content),
                        "group": group,
                    },
                )
                frame = parse_espn_scoreboard(response.json())
                if not frame.empty:
                    incoming_frames.append(frame)
        if incoming_frames:
            incoming = pd.concat(incoming_frames, ignore_index=True).drop_duplicates("game_id", keep="last")
            schedule = merge_schedule_frames(schedule, incoming)
            _atomic_write_parquet(schedule_path, schedule)
        remaining = sum(not _event_has_schedule_match(event, schedule) for event in payload)
        return {
            "odds_events": len(payload),
            "initially_unmatched": len(unmatched),
            "refreshed_windows": len(windows),
            "schedule_rows": len(schedule),
            "remaining_unmatched": int(remaining),
        }

    def archive_totals_season(self, season: int, refresh: bool = False) -> dict[str, Path]:
        """Archive final-game inputs used to construct strictly lagged totals features."""
        paths: dict[str, Path] = {}
        for source in ("adv_team_gamelog", "drives"):
            url = self.settings.data_urls[source].format(season=season)
            name = {
                "adv_team_gamelog": f"adv_team_gamelog_{season}.parquet",
                "drives": f"drives_{season}.parquet",
            }[source]
            paths[source] = self._download(url, self.settings.raw_dir / "sportsdataverse" / name, refresh)
        return paths

    def archive_public_models_season(
        self, season: int, refresh: bool = False, include_fpi: bool = True
    ) -> dict[str, Path]:
        """Archive point-in-time public ratings and preseason roster priors."""
        sources = ["ratings_weekly", "summaries_weekly", "returning_production", "team_talent"]
        if include_fpi:
            sources.append("fpi_weekly")
        paths: dict[str, Path] = {}
        for source in sources:
            url = self.settings.data_urls[source].format(season=season)
            name = {
                "ratings_weekly": f"ratings_weekly_{season}.parquet",
                "summaries_weekly": f"summaries_weekly_{season}.parquet",
                "returning_production": f"returning_production_{season}.parquet",
                "team_talent": f"team_talent_{season}.parquet",
                "fpi_weekly": f"fpi_weekly_{season}.parquet",
            }[source]
            paths[source] = self._download(url, self.settings.raw_dir / "sportsdataverse" / name, refresh)
        if season > self.settings.historical_start_season:
            prior = season - 1
            url = self.settings.data_urls["ratings_final"].format(season=prior)
            path = self.settings.raw_dir / "sportsdataverse" / f"ratings_final_{prior}.parquet"
            paths["prior_final_ratings"] = self._download(url, path, refresh)
        return paths

    def ratings(self, season: int, refresh: bool = False) -> Path:
        url = self.settings.data_urls["ratings"].format(season=season)
        return self._download(url, self.settings.raw_dir / "cfbtxt" / f"ratings_preseason_{season}.csv", refresh)

    def current_odds(self, refresh: bool = True) -> tuple[Path, dict[str, str]]:
        odds_dir = self.settings.raw_dir / "the_odds_api"
        if not refresh:
            cached = sorted(path for path in odds_dir.glob("ncaaf_*.json") if not path.name.endswith(".meta.json"))
            if cached:
                return cached[-1], {}
        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y%m%dT%H%M%SZ")
        path = odds_dir / f"ncaaf_{timestamp}.json"
        response = self.session.get(
            f"{ODDS_BASE_URL}/sports/{ODDS_SPORT}/odds",
            params={
                "apiKey": find_odds_api_key(self.settings),
                "regions": "us",
                "markets": "h2h,spreads,totals",
                "oddsFormat": "american",
                "dateFormat": "iso",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        content = response.content
        atomic_write_bytes(path, content)
        quota = {key.lower(): value for key, value in response.headers.items() if key.lower().startswith("x-requests-")}
        atomic_write_json(
            path.with_suffix(".meta.json"),
            {
                "source": "the_odds_api",
                "retrieved_at": utc_now(),
                "sha256": sha256_bytes(content),
                "quota_headers": quota,
                "event_count": len(response.json()),
            },
        )
        return path, quota


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
