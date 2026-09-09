from copy import deepcopy
from datetime import datetime, timezone
import gzip
import json

import numpy as np
import pandas as pd
import pytest

from ncaaf_model import weather_research as wr


def fixture_catalog():
    return {(wr.normalize_name("Memorial Stadium (Lincoln, NE)"), "nebraskacornhuskers"): [
        {"id": "4", "name": "Memorial Stadium (Lincoln, NE)", "indoor": False, "state": "NE", "source": "snapshot.json"}]}


def fixture_game():
    return {"venue": "Memorial Stadium (Lincoln, NE)", "home_team": "Nebraska Cornhuskers", "neutral_site": False}


def coordinates():
    return [{"stadium": "Memorial Stadium", "team": "Nebraska", "state": "NE", "latitude": "40.82", "longitude": "-96.70"},
            {"stadium": "Memorial Stadium", "team": "Indiana", "state": "IN", "latitude": "39.18", "longitude": "-86.53"}]


def test_explicit_city_alias_disambiguates_same_name_and_preserves_roof_assumption():
    venue, reason = wr.match_venue(fixture_game(), coordinates(), fixture_catalog())
    assert reason == "included" and venue["venue"]["latitude"] == 40.82
    assert venue["coordinate_source_team"] == "Nebraska"
    assert "not historically proved" in venue["roof_history_status"]


@pytest.mark.parametrize("change", ["neutral", "unknown_roof", "indoor", "wrong_state", "relocation", "ambiguous_id"])
def test_neutral_indoor_unknown_and_wrong_identity_are_excluded(change):
    game, catalog, points = fixture_game(), fixture_catalog(), coordinates()
    row = next(iter(catalog.values()))[0]
    if change == "neutral": game["neutral_site"] = True
    elif change == "unknown_roof": row["indoor"] = None
    elif change == "indoor": row["indoor"] = True
    elif change == "wrong_state": row["state"] = "CA"
    elif change == "relocation": game["venue"] = "Canvas Stadium"
    elif change == "ambiguous_id": next(iter(catalog.values())).append({**row, "id": "different"})
    assert wr.match_venue(game, points, catalog)[0] is None


def test_plan_does_not_select_using_results_or_prices(tmp_path, monkeypatch):
    root = tmp_path / "model"
    (root / "data/raw/alternative").mkdir(parents=True)
    (root / "data/raw/alternative/espn_verified_pregame_games.parquet").write_bytes(b"frozen-source")
    stadiums = tmp_path / "stadiums.csv"
    pd.DataFrame(coordinates()).to_csv(stadiums, index=False)
    base = {**fixture_game(), "game_id": 1, "season": 2024, "week": 2, "game_date": "2024-09-07T19:30Z", "actual_total": 17, "market_total": 60.5}
    data = pd.DataFrame([base])
    monkeypatch.setattr(wr, "load_market_games", lambda root: data)
    monkeypatch.setattr(wr, "current_roof_catalog", lambda root: fixture_catalog())
    with pytest.raises(ValueError):
        wr.build_plan(root, stadiums)
    first = wr.build_plan(root, stadiums, allow_current_roof_assumption=True)
    data.loc[0, ["actual_total", "market_total"]] = [95, 25.5]
    second = wr.build_plan(root, stadiums, allow_current_roof_assumption=True)
    assert first["games"] == second["games"] and first["requests"] == second["requests"]
    assert not first["outcome_data_used_for_plan"]
    assert "actual_total" not in json.dumps(first["games"])
    assert wr.read_plan(root)["included_games"] == 1
    corrupted = deepcopy(second)
    corrupted["thresholds"]["wind_mph_above"] = 5.
    (root / "reports/weather_request_plan.json").write_text(json.dumps(corrupted))
    with pytest.raises(ValueError):
        wr.read_plan(root)


def test_batch_response_is_not_silently_reassigned_between_locations():
    batch = {"game_ids": ["a", "b"], "parameters": {"models": wr.MODEL}}
    games = {"a": {"venue": {"latitude": 40., "longitude": -90.}}, "b": {"venue": {"latitude": 30., "longitude": -80.}}}
    payload = [{"latitude": 40.1, "longitude": -90.1, "location_id": 0}, {"latitude": 30.1, "longitude": -80.1, "location_id": 1}]
    rows = wr.normalize_batch(payload, batch, games, "2026-09-09T00:00Z")
    assert [row["game_id"] for row in rows] == ["a", "b"]
    with pytest.raises(ValueError): wr.normalize_batch(payload[::-1], batch, games, "2026-09-09T00:00Z")
    with pytest.raises(ValueError): wr.normalize_batch(payload[:1], batch, games, "2026-09-09T00:00Z")


def test_cached_response_must_match_original_request_and_payload_hash(tmp_path):
    batch = {"request_id": "id", "source_url": wr.PREVIOUS_URL, "parameters": {"models": wr.MODEL}}
    payload = {"latitude": 40., "longitude": -90.}
    archive = {**batch, "payload": payload, "payload_sha256": wr._digest(payload)}
    path = tmp_path / "batch.json.gz"
    with gzip.open(path, "wt") as handle: json.dump(archive, handle)
    assert wr.read_batch(path, batch)["payload"] == payload
    archive["payload"]["latitude"] = 30.
    with gzip.open(path, "wt") as handle: json.dump(archive, handle)
    with pytest.raises(ValueError): wr.read_batch(path, batch)


def test_under_settlement_risks_unit_and_refunds_integer_push():
    np.testing.assert_allclose(wr.settle_under([49, 50, 51], [50, 50, 50]), [100 / 110, 0, -1])


def test_bootstrap_samples_whole_weeks_and_baseline_has_matched_coverage():
    dates, actual, flags = [], [], []
    for week in range(12):
        # Each week has one selected winner and a non-selected loser. A whole
        # week bootstrap keeps baseline ROI fixed; independent bet resampling
        # would spuriously give it uncertainty.
        start = pd.Timestamp("2024-09-07T19:00Z") + pd.Timedelta(weeks=week)
        dates.extend([start.isoformat(), (start + pd.Timedelta(hours=6)).isoformat()])
        actual.extend([40, 60])
        flags.extend([True, False])
    frame = pd.DataFrame({"kickoff": dates, "actual_total": actual, "market_total": 50., "shadow_under_flag": flags})
    result = wr.summarize(frame, bootstrap_draws=300)
    assert result["calendar_week_blocks"] == 12
    assert result["weather_rule"]["bets"] == 12 and result["all_under_same_weather_coverage"]["bets"] == 24
    assert result["weather_rule_roi_95_week_bootstrap"] == pytest.approx([100 / 110, 100 / 110])
    assert result["all_under_roi_95_week_bootstrap"] == pytest.approx([-1 / 22, -1 / 22])


def test_missing_or_empty_rule_population_does_not_invent_an_interval():
    frame = pd.DataFrame({"kickoff": ["2024-09-07T19:00Z"], "actual_total": [50.], "market_total": [50.], "shadow_under_flag": [False]})
    result = wr.summarize(frame, bootstrap_draws=100)
    assert result["weather_rule"]["roi"] is None
    assert result["weather_rule_roi_95_week_bootstrap"] is None
    assert result["bootstrap_draws_with_no_rule_bets"] == 100
