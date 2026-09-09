from __future__ import annotations

import json
import os
import re
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


SCHEDULE_DERIVED_SCHEMA = "schedule-derived-artifact-v1"
_RAW_VERIFICATION = {"receipt_hash_matches_input_bytes", "unverified_legacy_receipt_hash_mismatch",
                     "unverified_legacy_derived_receipt"}


def _aware_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def _raw_download_receipt(value: Any) -> dict | None:
    """Retain known receipt fields only; malformed metadata is not a receipt."""
    if (not isinstance(value, dict) or not isinstance(value.get("source_url"), str)
            or not value["source_url"].startswith(("https://", "http://"))
            or not _aware_timestamp(value.get("retrieved_at"))
            or not isinstance(value.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"])
            or any(value.get(key) is not None and not isinstance(value[key], str) for key in ("content_type", "last_modified"))):
        return None
    return {key: value.get(key) for key in ("source_url", "retrieved_at", "sha256", "content_type", "last_modified")}


def schedule_artifact_input(path: Path, role: str) -> dict:
    """Describe actual pre-write bytes without recursively embedding sidecars."""
    digest = sha256_bytes(path.read_bytes()) if path.exists() else None
    sidecar = path.with_suffix(path.suffix + ".meta.json")
    try:
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        metadata = None
    result = {"role": role, "sha256": digest, "artifact_type": "unknown", "metadata_status": "missing_or_invalid",
              "generated_at": None, "raw_download": None}
    if not isinstance(metadata, dict):
        return result
    if metadata.get("schema_version") == SCHEDULE_DERIVED_SCHEMA:
        if (metadata.get("artifact_type") != "derived_schedule" or not _aware_timestamp(metadata.get("generated_at"))
                or type(metadata.get("merged_rows")) is not int or metadata["merged_rows"] < 0
                or not isinstance(metadata.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", metadata["sha256"])):
            return result
        result.update(artifact_type="derived_schedule", generated_at=metadata["generated_at"],
                      metadata_status="matches_input_bytes" if digest == metadata["sha256"] else "hash_mismatch")
        raw = metadata.get("raw_download")
        if (isinstance(raw, dict) and _raw_download_receipt(raw.get("receipt")) is not None
                and isinstance(raw.get("verification"), str) and raw["verification"] in _RAW_VERIFICATION
                and raw.get("original_bytes") == ("not_separately_preserved" if raw["verification"] == "receipt_hash_matches_input_bytes" else "unrecovered")):
            # Inherited uncertainty survives even if the derived artifact's own
            # hash matches. A new Parquet write cannot authenticate old bytes.
            result["raw_download"] = {"receipt": _raw_download_receipt(raw["receipt"]),
                                      "verification": raw["verification"], "original_bytes": raw["original_bytes"]}
    elif "schema_version" not in metadata and "artifact_type" not in metadata:
        receipt = _raw_download_receipt(metadata)
        if receipt is not None:
            matches = receipt["sha256"] == digest
            derived = any(key in metadata for key in ("merge_policy", "merged_rows", "incoming_rows", "previous_rows"))
            verification = ("unverified_legacy_derived_receipt" if derived else "receipt_hash_matches_input_bytes" if matches
                            else "unverified_legacy_receipt_hash_mismatch")
            result.update(artifact_type="legacy_derived_schedule" if derived else "download_receipt_claim",
                          metadata_status="matches_input_bytes" if matches else "hash_mismatch",
                          raw_download={"receipt": receipt, "verification": verification,
                                        "original_bytes": "not_separately_preserved" if verification == "receipt_hash_matches_input_bytes" else "unrecovered"})
    return result


def write_derived_schedule(path: Path, frame: pd.DataFrame, *, operation: str, inputs: list[dict], counts: dict[str, int] | None = None) -> None:
    """Write the unchanged derived frame and a sidecar for its current bytes."""
    if not 1 <= len(inputs) <= 2:
        raise ValueError("Schedule derivation needs one or two direct input descriptors")
    _atomic_write_parquet(path, frame)
    # Only direct summaries from schedule_artifact_input are carried forward;
    # earlier inputs/sidecars are never nested recursively.
    fields = ("role", "sha256", "artifact_type", "metadata_status", "generated_at", "raw_download")
    direct = [{key: row[key] for key in fields} for row in inputs]
    metadata = {"schema_version": SCHEDULE_DERIVED_SCHEMA, "artifact_type": "derived_schedule",
                "sha256": sha256_bytes(path.read_bytes()), "generated_at": utc_now(), "operation": operation,
                "merge_policy": "game_id union; refreshed row wins", "merged_rows": int(len(frame)),
                "inputs": direct, "raw_download": direct[-1]["raw_download"],
                "raw_receipt_note": "A retained download receipt describes its earlier claimed response, not this derived Parquet. Hash agreement alone does not certify original publication time; no original download bytes are recovered by this write.",
                "generation_note": "generated_at is this local materialization time, not when any retained row became available. The current Parquet hash is not a hash of an original ESPN response.",
                "lineage_scope": "inputs contains bounded schedule-parent summaries, not complete raw-input lineage. Runtime ESPN response originals are not archived here; ensure_schedule_for_odds archives them separately without linking them in this sidecar."}
    metadata.update(counts or {})
    atomic_write_json(path.with_suffix(path.suffix + ".meta.json"), metadata)


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
            previous_input = schedule_artifact_input(path, "previous_schedule") if not previous.empty else None
            paths[source] = self._download(url, path, refresh)
            if source == "schedule" and refresh and not previous.empty:
                incoming = pd.read_parquet(path)
                merged = merge_schedule_frames(previous, incoming)
                write_derived_schedule(path, merged, operation="archive_season_refresh",
                    inputs=[previous_input, schedule_artifact_input(path, "downloaded_schedule")],
                    counts={"incoming_rows": int(len(incoming)), "previous_rows": int(len(previous))})
        return paths

    def ensure_schedule_for_odds(self, odds_path: Path) -> dict[str, int]:
        """Supplement partial release assets with ESPN rows for unmatched odds weeks."""
        payload = load_json(odds_path)
        schedule_path = self.settings.raw_dir / "sportsdataverse" / f"cfb_schedule_{self.settings.season}.parquet"
        schedule = pd.read_parquet(schedule_path) if schedule_path.exists() else pd.DataFrame()
        schedule_input = schedule_artifact_input(schedule_path, "schedule_before_scoreboard_merge")
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
            write_derived_schedule(schedule_path, schedule, operation="ensure_schedule_for_odds_scoreboard_merge",
                inputs=[schedule_input], counts={"incoming_rows": int(len(incoming))})
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
