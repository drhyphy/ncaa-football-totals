from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from ncaaf_model.weather_shadow import (
    LIVE_URL, MODEL, PREVIOUS_URL, Venue, archive_forecast, venue_eligibility, weather_flag,
)


KICK = datetime(2026, 9, 12, 19, 30, tzinfo=timezone.utc)
DECISION = datetime(2026, 9, 12, 10, 30, tzinfo=timezone.utc)
VENUE = Venue("1", "Stadium", 35., -78., True, "https://venue", "https://coordinates", "2026-01-01", "2026-12-31")
GAME = {"game_id": "g", "venue_id": "1", "kickoff": KICK.isoformat(), "indoor": False, "neutral_site": False}


def envelope(mode="previous_day2", observed=DECISION, temperature=60., humidity=60., wind=10.):
    suffix = "_previous_day2" if mode == "previous_day2" else ""
    fields = {"temperature_2m": (temperature, "°F"), "relative_humidity_2m": (humidity, "%"), "wind_speed_10m": (wind, "mp/h")}
    hourly = {"time": [(KICK.replace(minute=0) + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(4)]}
    hourly.update({key + suffix: [value] * 4 for key, (value, _) in fields.items()})
    return {"mode": mode, "source_url": PREVIOUS_URL if mode == "previous_day2" else LIVE_URL,
        "model": MODEL, "observed_at": observed.isoformat(),
        "parameters": {"latitude": VENUE.latitude, "longitude": VENUE.longitude, "models": MODEL},
        "payload": {"utc_offset_seconds": 0, "hourly": hourly,
            "hourly_units": {key + suffix: unit for key, (_, unit) in fields.items()}}}


def test_flag_is_shadow_only_with_no_borrowed_probability():
    result = weather_flag(GAME, VENUE, envelope(), DECISION)
    assert result["shadow_under_flag"] is True
    assert result["status"] == "shadow_only"
    assert result["win_probability"] is None and result["expected_value"] is None and not result["bet_eligible"]
    assert result["forecast_valid_time"] == KICK.replace(minute=0).isoformat()
    assert result["publication_timestamp_verified"] is False


@pytest.mark.parametrize("kwargs", [{"wind": 7.78}, {"temperature": 64.81}, {"humidity": 56.8}])
def test_thresholds_are_strict_and_never_reoptimized(kwargs):
    assert weather_flag(GAME, VENUE, envelope(**kwargs), DECISION)["shadow_under_flag"] is False


def test_wind_uses_fixed_four_hour_mean_not_favorable_or_realized_window():
    data = envelope()
    data["payload"]["hourly"]["wind_speed_10m_previous_day2"] = [20., 1., 1., 1.]
    result = weather_flag(GAME, VENUE, data, DECISION)
    assert result["wind_mph"] == 5.75 and result["shadow_under_flag"] is False


@pytest.mark.parametrize("game_changes", [{"venue_id": "wrong"}, {"indoor": True}, {"indoor": None}, {"neutral_site": True}, {"neutral_site": None}])
def test_actual_venue_roof_and_neutral_status_must_be_verified(game_changes):
    result = weather_flag({**GAME, **game_changes}, VENUE, envelope(), DECISION)
    assert result["status"] == "unavailable" and result["shadow_under_flag"] is None


def test_current_venue_metadata_cannot_validate_a_past_roof_configuration():
    game = {**GAME, "kickoff": KICK.replace(year=2024).isoformat()}
    assert not venue_eligibility(game, VENUE)[0]
    assert not venue_eligibility(GAME, replace(VENUE, outdoor_verified=False))[0]


def test_archived_forecast_cannot_become_prospective_by_parsing_it_again():
    later = envelope(observed=DECISION + timedelta(days=1))
    unavailable = weather_flag(GAME, VENUE, later, DECISION)
    assert unavailable["shadow_under_flag"] is None
    historical = weather_flag(GAME, VENUE, later, DECISION, allow_historical_lead_proxy=True)
    assert historical["shadow_under_flag"] is True
    assert "proxy" in historical["availability_evidence"]
    assert not historical["publication_timestamp_verified"]
    # Explicit historical permission never rescues a post-decision live forecast.
    live = envelope("live", observed=DECISION + timedelta(seconds=1))
    assert weather_flag(GAME, VENUE, live, DECISION, allow_historical_lead_proxy=True)["shadow_under_flag"] is None


def test_every_wind_hour_must_be_available_before_decision_with_buffer():
    early = KICK.replace(minute=0) - timedelta(hours=48) + timedelta(hours=8)
    result = weather_flag(GAME, VENUE, envelope(observed=early), early)
    assert "fixed_lead_forecast_not_yet_available_under_buffer" in result["flags"]


@pytest.mark.parametrize("mutation", ["nan", "missing_hour", "wrong_units", "wrong_endpoint", "wrong_model", "wrong_location", "wrong_timezone"])
def test_bad_weather_or_unapproved_forecast_provenance_fails_closed(mutation):
    data = envelope()
    if mutation == "nan": data["payload"]["hourly"]["wind_speed_10m_previous_day2"][0] = float("nan")
    elif mutation == "missing_hour": data["payload"]["hourly"]["time"].pop()
    elif mutation == "wrong_units": data["payload"]["hourly_units"]["wind_speed_10m_previous_day2"] = "km/h"
    elif mutation == "wrong_endpoint": data["source_url"] = "https://archive-api.open-meteo.com/v1/archive"
    elif mutation == "wrong_model": data["model"] = "ecmwf_ifs"
    elif mutation == "wrong_location": data["parameters"]["latitude"] = 0
    elif mutation == "wrong_timezone": data["payload"]["utc_offset_seconds"] = -14400
    result = weather_flag(GAME, VENUE, data, DECISION)
    assert result["shadow_under_flag"] is None


def test_forecast_archive_is_content_identified_immutable_and_idempotent(tmp_path):
    first = envelope()
    p = archive_forecast(first, tmp_path)
    assert p == archive_forecast(first, tmp_path)
    later = deepcopy(first)
    later["observed_at"] = (DECISION + timedelta(minutes=1)).isoformat()
    assert archive_forecast(later, tmp_path) != p


def test_live_receipt_is_useable_only_before_game_and_distinct_from_fixed_lead():
    result = weather_flag(GAME, VENUE, envelope("live"), DECISION)
    assert result["shadow_under_flag"] is True
    assert "forecast_lead_hours" not in result
    assert result["availability_evidence"] == "prospectively_archived_receipt"
    assert weather_flag(GAME, VENUE, envelope("live"), KICK)["shadow_under_flag"] is None
