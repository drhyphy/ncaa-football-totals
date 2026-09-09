from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from ncaaf_model import weather_candidate as wc
from ncaaf_model.weather_shadow import MODEL, PREVIOUS_URL


NOW = datetime(2026, 9, 12, 10, 30, tzinfo=timezone.utc)
KICK = datetime(2026, 9, 12, 19, 30, tzinfo=timezone.utc)
GAME = {"game_id": "401", "home_team": "Home", "away_team": "Away", "kickoff": KICK.isoformat()}
CATALOG = {"schema_version": "weather-venues-v1", "source_plan_sha256": "frozen", "coordinates_source": "https://coordinates",
    "venues": {"3923": {"latitude": 38., "longitude": -78.}}}


def envelopes():
    event = {"source_url": wc.SUMMARY_BASE + "401", "observed_at": (NOW - timedelta(seconds=20)).isoformat(),
        "payload": {"header": {"id": "401", "competitions": [{"id": "401", "date": KICK.isoformat(),
            "neutralSite": False, "status": {"type": {"state": "pre"}}, "dateValid": True,
            "competitors": [{"homeAway": "home", "team": {"displayName": "Home"}},
                {"homeAway": "away", "team": {"displayName": "Away"}}]}]}, "gameInfo": {"venue": {"id": "3923", "fullName": "Stadium"}}}}
    venue = {"source_url": wc.VENUE_BASE + "3923?lang=en&region=us", "observed_at": (NOW - timedelta(seconds=10)).isoformat(),
        "payload": {"id": "3923", "fullName": "Stadium", "indoor": False}}
    suffix = "_previous_day2"
    hourly = {"time": [(KICK.replace(minute=0) + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(4)],
        "temperature_2m" + suffix: [60.] * 4, "relative_humidity_2m" + suffix: [70.] * 4, "wind_speed_10m" + suffix: [10.] * 4}
    forecast = {"source_url": PREVIOUS_URL, "mode": "previous_day2", "model": MODEL,
        "observed_at": (NOW - timedelta(seconds=1)).isoformat(), "parameters": {"models": MODEL, "latitude": 38., "longitude": -78.},
        "payload": {"utc_offset_seconds": 0, "hourly": hourly, "hourly_units": {
            "temperature_2m" + suffix: "°F", "relative_humidity_2m" + suffix: "%", "wind_speed_10m" + suffix: "mp/h"}}}
    return event, venue, forecast


def test_valid_feature_has_separate_paper_namespace_and_no_individual_probability():
    result = wc.prepare_weather_feature(GAME, *envelopes(), CATALOG, NOW)
    assert result["feature_status"] == "available" and result["weather_rule_match"] is True
    assert result["candidate_model_namespace"] == "weather-under-v1-20260908"
    assert result["flags"] == []
    assert result["win_probability"] is None and result["expected_value"] is None
    assert not result["bet_eligible"]
    assert result["fixed_feature_cutoff"] == NOW.isoformat()


@pytest.mark.parametrize("mutation", ["wrong_event", "wrong_team", "kickoff_changed", "neutral", "unknown_neutral", "started", "unconfirmed_date", "unknown_venue", "wrong_roof_id", "indoor", "unknown_roof", "unofficial_event", "unofficial_roof", "stale_event", "future_roof"])
def test_current_official_game_and_roof_identity_fail_closed(mutation):
    event, venue, forecast = envelopes()
    competition = event["payload"]["header"]["competitions"][0]
    if mutation == "wrong_event": event["payload"]["header"]["id"] = "402"
    elif mutation == "wrong_team": competition["competitors"][0]["team"]["displayName"] = "Other"
    elif mutation == "kickoff_changed": competition["date"] = (KICK + timedelta(minutes=30)).isoformat()
    elif mutation == "neutral": competition["neutralSite"] = True
    elif mutation == "unknown_neutral": competition.pop("neutralSite")
    elif mutation == "started": competition["status"]["type"]["state"] = "in"
    elif mutation == "unconfirmed_date": competition["dateValid"] = False
    elif mutation == "unknown_venue": event["payload"]["gameInfo"]["venue"]["id"] = "999"
    elif mutation == "wrong_roof_id": venue["payload"]["id"] = "999"
    elif mutation == "indoor": venue["payload"]["indoor"] = True
    elif mutation == "unknown_roof": venue["payload"].pop("indoor")
    elif mutation == "unofficial_event": event["source_url"] = "https://unofficial/401"
    elif mutation == "unofficial_roof": venue["source_url"] = "https://unofficial/3923"
    elif mutation == "stale_event": event["observed_at"] = (NOW - timedelta(hours=2)).isoformat()
    elif mutation == "future_roof": venue["observed_at"] = (NOW + timedelta(seconds=1)).isoformat()
    result = wc.prepare_weather_feature(GAME, event, venue, forecast, CATALOG, NOW)
    assert result["feature_status"] == "unavailable" and result["weather_rule_match"] is None
    assert result["flags"]


def test_early_provisional_day2_cache_cannot_become_mature_when_clock_advances():
    event, venue, forecast = envelopes()
    forecast["observed_at"] = (KICK - timedelta(days=4)).isoformat()
    result = wc.prepare_weather_feature(GAME, event, venue, forecast, CATALOG, NOW)
    assert "forecast_captured_before_fixed_lead_product_matured" in result["flags"]
    assert result["weather_rule_match"] is None


def test_retry_uses_actual_receipts_without_claiming_they_were_captured_at_0630():
    retry = NOW + timedelta(minutes=20)
    event, venue, forecast = envelopes()
    for item in (event, venue, forecast): item["observed_at"] = (retry - timedelta(seconds=1)).isoformat()
    result = wc.prepare_weather_feature(GAME, event, venue, forecast, CATALOG, retry)
    assert result["weather_rule_match"] is True
    assert result["actual_decision_time"] == retry.isoformat()
    assert result["fixed_feature_cutoff"] == NOW.isoformat()


def test_live_forecast_never_substitutes_for_the_frozen_day2_policy():
    event, venue, forecast = envelopes()
    forecast["mode"] = "live"
    result = wc.prepare_weather_feature(GAME, event, venue, forecast, CATALOG, NOW)
    assert result["flags"] == ["forecast_lead_policy_mismatch"]


def test_compact_catalog_rejects_a_reused_id_with_conflicting_coordinates():
    plan = {"games": [{"venue": {"venue_id": "1", "latitude": 40., "longitude": -90.}}],
        "plan_sha256": "frozen", "coordinate_url": "https://coords"}
    assert wc.build_venue_catalog(plan)["venues"] == {"1": {"latitude": 40., "longitude": -90.}}
    plan["games"].append({"venue": {"venue_id": "1", "latitude": 41., "longitude": -90.}})
    with pytest.raises(ValueError): wc.build_venue_catalog(plan)


def test_out_of_window_collection_never_requests_weather_or_creates_positions(tmp_path):
    root = tmp_path / "model"
    (root / "data/models").mkdir(parents=True)
    (root / "data/models/weather_venues_v1.json").write_text(json.dumps(CATALOG))
    class NoNetwork:
        def get(self, *args, **kwargs): raise AssertionError("Out-of-window request")
    future = {**GAME, "kickoff": (NOW + timedelta(days=3)).isoformat()}
    rows = wc.collect_weather_features(root, [future], NOW, NoNetwork())
    assert "not_upcoming_within_24_hours" in rows[0]["flags"]
    assert not (root / "ledger").exists()


def test_daytime_cutoff_prevents_previous_day_or_overnight_policy_drift():
    assert "before_game_day_0630_Eastern" in wc.target_flags(GAME, NOW - timedelta(minutes=1))
    late_night = {**GAME, "kickoff": "2026-09-13T05:30:00Z"}  # 01:30 Eastern, before that date's 06:30.
    assert "kickoff_not_after_gameday_0630_Eastern" in wc.target_flags(late_night, NOW)
