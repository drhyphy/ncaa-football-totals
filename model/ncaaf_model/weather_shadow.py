"""Frozen forecast-weather research flag, without an asserted betting edge.

Previous Runs GFS day2 gives documented fixed 48-hour forecast lead provenance,
not an original dissemination timestamp. Live receipts are separately archived.
Never use reanalysis or hindcasts as if they were contemporaneous forecasts.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path

import requests


PREVIOUS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
LIVE_URL = "https://api.open-meteo.com/v1/forecast"
DOCS_URL = "https://open-meteo.com/en/docs/previous-runs-api"
PAPER_URL = "https://doi.org/10.1080/13504851.2022.2146651"
MODEL = "gfs_global"
LEAD_HOURS = 48
PUBLICATION_BUFFER_HOURS = 6
VARIABLES = ("temperature_2m", "relative_humidity_2m", "wind_speed_10m")
THRESHOLDS = {"wind_mph_above": 7.78, "temperature_f_below": 64.81, "relative_humidity_percent_above": 56.8}


def _time(value):
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


@dataclass(frozen=True)
class Venue:
    venue_id: str
    name: str
    latitude: float
    longitude: float
    outdoor_verified: bool
    metadata_source: str
    coordinates_source: str
    # Date bounds are explicit: current roof metadata cannot silently prove a
    # different historical year's roof configuration.
    valid_from: str
    valid_through: str

    def __post_init__(self):
        if not (math.isfinite(self.latitude) and math.isfinite(self.longitude) and -90 <= self.latitude <= 90 and -180 <= self.longitude <= 180):
            raise ValueError("Invalid venue coordinates")
        if not self.venue_id or not self.metadata_source or not self.coordinates_source:
            raise ValueError("Explicit venue identity and provenance required")
        if not self.valid_from <= self.valid_through:
            raise ValueError("Invalid venue metadata date interval")


def venue_eligibility(game: dict, venue: Venue) -> tuple[bool, list[str]]:
    flags = []
    kickoff = _time(game.get("kickoff"))
    if str(game.get("venue_id", "")) != venue.venue_id:
        flags.append("actual_game_venue_id_unverified")
    if not venue.outdoor_verified or game.get("indoor") is not False:
        flags.append("outdoor_roof_status_unverified")
    if game.get("neutral_site") is not False:
        flags.append("neutral_site_or_neutral_status_unknown")
    if kickoff is None or not venue.valid_from <= kickoff.date().isoformat() <= venue.valid_through:
        flags.append("venue_metadata_not_valid_for_game_date")
    return not flags, flags


def fetch_forecast(venue: Venue, kickoff: datetime, *, mode: str = "previous_day2", session=None,
                   timeout: float = 20.) -> dict:
    """Fetch a fixed GFS forecast product; no automatic model or archive fallback."""
    if mode not in {"previous_day2", "live"}:
        raise ValueError("Only fixed-lead previous runs or actually observed live forecasts are supported")
    kickoff = _time(kickoff)
    if kickoff is None:
        raise ValueError("Timezone-aware kickoff required")
    suffix = "_previous_day2" if mode == "previous_day2" else ""
    params = {"latitude": venue.latitude, "longitude": venue.longitude,
        "start_date": kickoff.date().isoformat(), "end_date": (kickoff + timedelta(hours=3)).date().isoformat(),
        "hourly": ",".join(name + suffix for name in VARIABLES), "models": MODEL,
        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "timezone": "GMT"}
    url = PREVIOUS_URL if mode == "previous_day2" else LIVE_URL
    response = (session or requests.Session()).get(url, params=params, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise ValueError("Forecast provider returned an error")
    return {"schema_version": "weather-shadow-v1", "source_url": url, "parameters": params,
        "mode": mode, "model": MODEL, "observed_at": datetime.now(timezone.utc).isoformat(),
        "venue": asdict(venue), "payload": payload}


def archive_forecast(envelope: dict, directory: Path) -> Path:
    """Write an immutable response; content hash includes request and receipt."""
    encoded = json.dumps(envelope, sort_keys=True, allow_nan=False).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"forecast-{digest}.json.gz"
    if path.exists():
        with gzip.open(path, "rb") as handle:
            if handle.read() != encoded:
                raise ValueError("Existing forecast archive content mismatch")
        return path
    with gzip.open(path, "xb") as handle:
        handle.write(encoded)
    return path


def weather_flag(game: dict, venue: Venue, envelope: dict, decision_time: datetime,
                 *, allow_historical_lead_proxy: bool = False) -> dict:
    """One fixed weather criterion; unknowns remain missing, never false.

    For half-hour kickoffs the hour at/before kickoff is used. This prespecified
    transformation avoids searching favorable windows. Wind is the mean of
    kickoff hour and the next three hours: a fixed pregame duration proxy for
    the paper's average over actual game duration. Temperature/humidity use the
    kickoff hour. No realized end time is used to choose forecast inputs.
    Historical lead provenance is permitted only by an explicit research flag.
    A live receipt after the decision time cannot be replayed into that decision.
    """
    eligible, flags = venue_eligibility(game, venue)
    kickoff, decision = _time(game.get("kickoff")), _time(decision_time)
    mode = envelope.get("mode")
    suffix = "_previous_day2" if mode == "previous_day2" else ""
    result = {"candidate": "weather_under_shadow_v1", "game_id": str(game.get("game_id", "")),
        "status": "unavailable", "shadow_under_flag": None, "win_probability": None,
        "expected_value": None, "bet_eligible": False, "thresholds": THRESHOLDS.copy(),
        "flags": flags, "source_url": envelope.get("source_url"), "model": envelope.get("model"),
        "forecast_mode": mode,
        "observed_at": envelope.get("observed_at"), "rule_source": PAPER_URL,
        "interpretation": "Frozen research criterion only; published historical win rate is not a probability for this matchup."}
    if not eligible or kickoff is None or decision is None:
        return result
    if decision >= kickoff:
        flags.append("decision_not_pregame")
    if mode not in {"previous_day2", "live"} or envelope.get("model") != MODEL:
        flags.append("forecast_product_not_prespecified")
    if envelope.get("source_url") != (PREVIOUS_URL if mode == "previous_day2" else LIVE_URL):
        flags.append("forecast_endpoint_not_prespecified")
    # The requested point is the verified stadium; returned point may be the
    # nearest model grid cell and is recorded separately as a spatial limitation.
    parameters = envelope.get("parameters", {})
    if parameters.get("models") != MODEL or any(parameters.get(key) != value for key, value in (("latitude", venue.latitude), ("longitude", venue.longitude))):
        flags.append("forecast_request_location_or_model_mismatch")
    observed = _time(envelope.get("observed_at"))
    prospective = observed is not None and observed <= decision
    valid = kickoff.replace(minute=0, second=0, microsecond=0)
    reference = valid - timedelta(hours=LEAD_HOURS)
    latest_reference = reference + timedelta(hours=3)
    if mode == "previous_day2":
        result.update(nominal_forecast_reference_time=reference.isoformat(), forecast_lead_hours=LEAD_HOURS,
            latest_nominal_reference_time=latest_reference.isoformat(),
            publication_timestamp_verified=False, publication_delay_buffer_hours=PUBLICATION_BUFFER_HOURS)
        if latest_reference + timedelta(hours=PUBLICATION_BUFFER_HOURS) > decision:
            flags.append("fixed_lead_forecast_not_yet_available_under_buffer")
        if not prospective and not allow_historical_lead_proxy:
            flags.append("archive_received_after_decision_no_prospective_evidence")
    elif not prospective:
        flags.append("live_forecast_received_after_decision")
    result["availability_evidence"] = "prospectively_archived_receipt" if prospective else "fixed_lead_archive_proxy_not_original_publication_timestamp"
    payload = envelope.get("payload", {})
    if payload.get("utc_offset_seconds") != 0:
        flags.append("forecast_times_not_utc")
    units = payload.get("hourly_units", {})
    expected_units = {"temperature_2m": {"°F"}, "relative_humidity_2m": {"%"}, "wind_speed_10m": {"mp/h", "mph"}}
    if any(units.get(name + suffix) not in allowed for name, allowed in expected_units.items()):
        flags.append("weather_units_invalid")
    hourly = payload.get("hourly", {})
    stamp = valid.strftime("%Y-%m-%dT%H:%M")
    times = hourly.get("time", [])
    required_stamps = [(valid + timedelta(hours=offset)).strftime("%Y-%m-%dT%H:%M") for offset in range(4)]
    if any(times.count(hour) != 1 for hour in required_stamps):
        flags.append("kickoff_forecast_hour_missing_or_duplicated")
    if flags:
        return result
    index = times.index(stamp)
    try:
        temperature, humidity = [float(hourly[name + suffix][index]) for name in VARIABLES[:2]]
        wind_values = [float(hourly["wind_speed_10m" + suffix][times.index(hour)]) for hour in required_stamps]
        wind = sum(wind_values) / len(wind_values)
    except (TypeError, ValueError, KeyError, IndexError):
        flags.append("forecast_variables_missing")
        return result
    if not all(math.isfinite(x) for x in (temperature, humidity, *wind_values)) or not 0 <= humidity <= 100 or min(wind_values) < 0:
        flags.append("forecast_variables_invalid")
        return result
    result.update(status="shadow_only", temperature_f=temperature, relative_humidity_percent=humidity,
        wind_mph=wind, forecast_valid_time=valid.isoformat(),
        wind_aggregation="Mean of forecast kickoff hour and next three hours; fixed pregame duration proxy",
        grid_latitude=payload.get("latitude"), grid_longitude=payload.get("longitude"),
        shadow_under_flag=wind > 7.78 and temperature < 64.81 and humidity > 56.8)
    return result
