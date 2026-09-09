"""Prospective features for the frozen weather paper experiment.

This module supplies forecast/venue evidence only. It never assigns an
individual-game probability, EV or bet eligibility. Runtime owns price policy.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from .teams import normalize_team
from .weather_shadow import MODEL, THRESHOLDS, Venue, archive_forecast, fetch_forecast, weather_flag


NY = ZoneInfo("America/New_York")
CANDIDATE = "weather_published_rule_paper_v1"
METADATA_MAX_AGE_SECONDS = 3600
SUMMARY_BASE = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/summary?event="
VENUE_BASE = "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/venues/"


def _time(value):
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def _game_id(game):
    value = game.get("espn_game_id", game.get("game_id", ""))
    try:
        number = float(value)
        return str(int(number)) if math.isfinite(number) and number > 0 and number == int(number) else ""
    except (ValueError, TypeError, OverflowError):
        return ""


def _kickoff(game):
    return _time(game.get("kickoff", game.get("commence_time", game.get("game_date"))))


def _base(game, now):
    kickoff = _kickoff(game)
    return {"candidate": CANDIDATE, "candidate_model_namespace": "weather-under-v1-20260908",
        "game_id": _game_id(game), "home_team": game.get("home_team"), "away_team": game.get("away_team"),
        "kickoff": kickoff.isoformat() if kickoff else None, "evaluated_at": now.isoformat(),
        "feature_status": "unavailable", "weather_rule_match": None, "flags": [],
        "win_probability": None, "expected_value": None, "bet_eligible": False,
        "thresholds": THRESHOLDS.copy(), "forecast_policy": "gfs_global previous_day2; fixed four-hour wind mean; kickoff temperature/humidity",
        "interpretation": "Frozen weather criterion for paper tracking. Historical group evidence is not an individual-game probability."}


def target_flags(game, now):
    flags = []
    kickoff = _kickoff(game)
    if not _game_id(game):
        flags.append("espn_game_id_missing")
    if kickoff is None:
        return flags + ["kickoff_missing"]
    if not now < kickoff <= now + timedelta(hours=24):
        flags.append("not_upcoming_within_24_hours")
    cutoff = datetime.combine(kickoff.astimezone(NY).date(), datetime.min.time(), NY).replace(hour=6, minute=30).astimezone(timezone.utc)
    if cutoff >= kickoff:
        flags.append("kickoff_not_after_gameday_0630_Eastern")
    if now < cutoff:
        flags.append("before_game_day_0630_Eastern")
    # The last of four wind hours has the most recent nominal reference.
    latest_reference = kickoff.replace(minute=0, second=0, microsecond=0) + timedelta(hours=3 - 48)
    if latest_reference + timedelta(hours=6) > cutoff:
        flags.append("fixed_lead_unavailable_at_gameday_cutoff")
    return flags


def build_venue_catalog(plan: dict) -> dict:
    venues = {}
    for game in plan["games"]:
        venue = game["venue"]
        point = {"latitude": float(venue["latitude"]), "longitude": float(venue["longitude"])}
        venue_id = str(venue["venue_id"])
        if venue_id in venues and venues[venue_id] != point:
            raise ValueError("Frozen venue ID has conflicting coordinates")
        venues[venue_id] = point
    return {"schema_version": "weather-venues-v1", "source_plan_sha256": plan["plan_sha256"],
        "coordinates_source": plan["coordinate_url"], "venues": dict(sorted(venues.items())),
        "policy": "Coordinates only. Current game identity, roof status and neutral status must be separately verified."}


def _fresh(envelope, expected_url, now):
    observed = _time((envelope or {}).get("observed_at"))
    return bool(envelope and envelope.get("source_url") == expected_url and observed is not None
        and 0 <= (now - observed).total_seconds() <= METADATA_MAX_AGE_SECONDS)


def validate_current_venue(game, event_envelope, venue_envelope, catalog, now):
    """Return only the actual current outdoor, non-neutral venue in the catalog."""
    flags = target_flags(game, now)
    game_id, kickoff = _game_id(game), _kickoff(game)
    if not _fresh(event_envelope, SUMMARY_BASE + game_id, now):
        flags.append("current_event_receipt_missing_stale_or_unofficial")
    payload = (event_envelope or {}).get("payload") or {}
    header = payload.get("header") or {}
    competitions = header.get("competitions") or []
    competition = competitions[0] if len(competitions) == 1 else {}
    if str(header.get("id", "")) != game_id or str(competition.get("id", "")) != game_id:
        flags.append("current_event_identity_mismatch")
    current_kickoff = _time(competition.get("date"))
    if kickoff is None or current_kickoff is None or current_kickoff != kickoff:
        flags.append("current_kickoff_changed_or_unknown")
    if competition.get("neutralSite") is not False:
        flags.append("current_neutral_or_unknown_neutral_status")
    if ((competition.get("status") or {}).get("type") or {}).get("state") != "pre":
        flags.append("current_event_not_confirmed_pregame")
    if competition.get("dateValid") is False:
        flags.append("current_kickoff_not_confirmed")
    competitors = competition.get("competitors") or []
    for side in ("home", "away"):
        teams = [row.get("team") or {} for row in competitors if row.get("homeAway") == side]
        if len(teams) != 1 or normalize_team(teams[0].get("displayName", "")) != normalize_team(game.get(side + "_team", "")):
            flags.append("current_" + side + "_team_identity_mismatch")
    current = (payload.get("gameInfo") or {}).get("venue") or {}
    venue_id = str(current.get("id", ""))
    if not venue_id or venue_id not in catalog.get("venues", {}):
        flags.append("actual_current_venue_not_in_frozen_coordinates")
    if not _fresh(venue_envelope, VENUE_BASE + venue_id + "?lang=en&region=us", now):
        flags.append("current_roof_receipt_missing_stale_or_unofficial")
    roof = (venue_envelope or {}).get("payload") or {}
    if str(roof.get("id", "")) != venue_id:
        flags.append("current_roof_venue_identity_mismatch")
    if roof.get("indoor") is not False:
        flags.append("current_roof_indoor_or_unknown")
    if flags:
        return None, list(dict.fromkeys(flags))
    point = catalog["venues"][venue_id]
    venue = Venue(venue_id, roof.get("fullName", current.get("fullName", "")), point["latitude"], point["longitude"], True,
        venue_envelope["source_url"], catalog["coordinates_source"], kickoff.date().isoformat(), kickoff.date().isoformat())
    return venue, []


def prepare_weather_feature(game: dict, event_envelope: dict, venue_envelope: dict,
                            forecast_envelope: dict, catalog: dict, now: datetime) -> dict:
    """Pure prospective validation; retries retain fixed forecast cutoff semantics."""
    now = _time(now)
    if now is None:
        raise ValueError("Timezone-aware evaluation time required")
    result = _base(game, now)
    venue, flags = validate_current_venue(game, event_envelope, venue_envelope, catalog, now)
    result["flags"] = flags
    result["catalog_plan_sha256"] = catalog.get("source_plan_sha256")
    result["current_event_observed_at"] = (event_envelope or {}).get("observed_at")
    result["current_roof_observed_at"] = (venue_envelope or {}).get("observed_at")
    if venue is None:
        return result
    if forecast_envelope.get("mode") != "previous_day2":
        result["flags"].append("forecast_lead_policy_mismatch")
        return result
    forecast_received = _time(forecast_envelope.get("observed_at"))
    mature_after = _kickoff(game).replace(minute=0, second=0, microsecond=0) + timedelta(hours=3 - 48 + 6)
    if forecast_received is None or forecast_received < mature_after:
        # The provider can return a longer-lead provisional value for future
        # day2 hours. An early cached response cannot later become a mature
        # fixed-lead archive simply because the decision clock has advanced.
        result["flags"].append("forecast_captured_before_fixed_lead_product_matured")
        return result
    cutoff = datetime.combine(_kickoff(game).astimezone(NY).date(), datetime.min.time(), NY).replace(hour=6, minute=30).astimezone(timezone.utc)
    # The six-hour forecast-availability bound was tested against the fixed
    # morning cutoff above. Actual receipts must precede this actual decision;
    # a 06:45 retry is not misrepresented as a 06:30 captured response.
    weather_game = {"game_id": _game_id(game), "kickoff": _kickoff(game).isoformat(),
        "venue_id": venue.venue_id, "indoor": False, "neutral_site": False}
    weather = weather_flag(weather_game, venue, forecast_envelope, now)
    result.update(weather=weather, fixed_feature_cutoff=cutoff.isoformat(), actual_decision_time=now.isoformat(),
        current_venue_id=venue.venue_id, current_venue_name=venue.name,
        current_event_source=event_envelope["source_url"], current_roof_source=venue_envelope["source_url"])
    if weather["status"] != "shadow_only":
        result["flags"].extend(weather["flags"])
        return result
    result.update(feature_status="available", weather_rule_match=weather["shadow_under_flag"])
    return result


def _fetch_context(session, url):
    response = session.get(url, timeout=20)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Official venue context response must be an object")
    return {"source_url": url, "observed_at": datetime.now(timezone.utc).isoformat(), "payload": payload,
        "response_sha256": hashlib.sha256(response.content).hexdigest()}


def _archive_context(envelope, directory):
    encoded = json.dumps(envelope, sort_keys=True, allow_nan=False).encode()
    path = directory / ("espn-" + hashlib.sha256(encoded).hexdigest() + ".json.gz")
    directory.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with gzip.open(path, "xb") as handle:
            handle.write(encoded)
    return path


def collect_weather_features(root: Path, games: list[dict], now: datetime | None = None, session=None) -> list[dict]:
    """Read-only network collection; call from runtime after fresh game matching.

    No bet prices, positions, training, account settings or board files touched.
    Dated source evidence is archived below data/runtime/weather/ for publication.
    Out-of-window games never cause a network request.
    """
    now = _time(now or datetime.now(timezone.utc))
    if now is None:
        raise ValueError("Timezone-aware evaluation time required")
    catalog = json.loads((root / "data/models/weather_venues_v1.json").read_text())
    if catalog.get("schema_version") != "weather-venues-v1":
        raise ValueError("Unknown weather venue catalog")
    directory = root / "data/runtime/weather"
    session = session or requests.Session()
    output, seen = [], set()
    for game in games:
        game_id = _game_id(game)
        if game_id in seen:
            continue
        seen.add(game_id)
        flags = target_flags(game, now)
        if flags:
            output.append({**_base(game, now), "flags": flags})
            continue
        paths = []
        try:
            event = _fetch_context(session, SUMMARY_BASE + game_id)
            paths.append(str(_archive_context(event, directory).relative_to(root)))
            venue_id = str((event["payload"].get("gameInfo") or {}).get("venue", {}).get("id", ""))
            if venue_id not in catalog["venues"]:
                output.append({**_base(game, datetime.now(timezone.utc)), "flags": ["actual_current_venue_not_in_frozen_coordinates"],
                    "current_event_observed_at": event["observed_at"], "source_archives": paths})
                continue
            roof = _fetch_context(session, VENUE_BASE + venue_id + "?lang=en&region=us")
            paths.append(str(_archive_context(roof, directory).relative_to(root)))
            evaluated = datetime.now(timezone.utc)
            venue, flags = validate_current_venue(game, event, roof, catalog, evaluated)
            if venue is None:
                output.append({**_base(game, evaluated), "flags": flags, "source_archives": paths})
                continue
            forecast = fetch_forecast(venue, _kickoff(game), mode="previous_day2", session=session)
            paths.append(str(archive_forecast(forecast, directory).relative_to(root)))
            row = prepare_weather_feature(game, event, roof, forecast, catalog, datetime.now(timezone.utc))
            row["source_archives"] = paths
            output.append(row)
        except (requests.RequestException, ValueError, KeyError, OSError) as exc:
            output.append({**_base(game, datetime.now(timezone.utc)),
                "flags": ["weather_collection_failed_" + type(exc).__name__], "source_archives": paths})
    return output
