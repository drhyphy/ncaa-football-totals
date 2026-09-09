import json
from types import SimpleNamespace

from ncaaf_model.board_metadata import attach_tracking, POLICIES


def test_zero_entry_policies_are_all_visible_and_legacy_forecasts_are_separate(tmp_path):
    board = {}
    metrics = [{"candidate": "opponent_adjusted_ridge", "model_version": "old", "games": 80},
               {"candidate": "opponent_adjusted_ridge", "model_version": "totals-v4-20260908", "games": 0, "pending": 85}]
    attach_tracking(board, SimpleNamespace(ledger_dir=tmp_path), [], metrics)
    assert board["primary_model"]["candidate"] == "opponent_adjusted_ridge"
    assert [r["candidate"] for r in board["candidate_tracking"]] == [p[0] for p in POLICIES]
    assert all(r["performance"]["bets"] == 0 for r in board["candidate_tracking"])
    assert board["candidate_tracking"][0]["forecast_performance"]["pending"] == 85
    assert board["candidate_tracking"][1]["forecast_performance"] is None
    assert board["candidate_tracking"][3]["forecast_performance"] is None
    assert board["registered_evaluation"]["formal_evaluation_at"] == "2027-02-08T12:00:00Z"


def test_weather_ledger_survives_live_feed_failure_without_pooling(tmp_path):
    weather = {"candidate": "published_weather_under", "model_version": "weather-under-v1-20260908",
               "game_id": "w", "result": "pending"}
    (tmp_path / "weather_positions.json").write_text(json.dumps([weather]))
    positions = [{"candidate": "opponent_adjusted_structural", "model_version": "totals-v4-20260908",
                  "game_id": "s", "result": "pending"}]
    board = {"status": "unavailable"}
    attach_tracking(board, SimpleNamespace(ledger_dir=tmp_path), positions, [])
    tracking = {row["candidate"]: row for row in board["candidate_tracking"]}
    assert tracking["published_weather_under"]["performance"]["pending"] == 1
    assert tracking["opponent_adjusted_structural"]["performance"]["pending"] == 1
    assert tracking["opponent_adjusted_ridge"]["performance"]["pending"] == 0
    assert board["status"] == "unavailable"


def test_voids_are_visible_without_entering_return_or_accuracy_denominators():
    from ncaaf_model.runtime import PRIMARY, VERSION, performance, forecast_performance
    void = {"candidate": PRIMARY, "model_version": VERSION, "game_id": "1", "result": "void",
            "profit_units": None, "eligible": True}
    pending = {**void, "game_id": "2", "result": "pending"}
    paper = performance([void, pending])
    assert paper["bets"] == 0 and paper["pending"] == 1 and paper["voids"] == 1
    assert paper["roi"] is None and paper["profit_units"] == 0
    accuracy = forecast_performance([void, pending])[0]
    assert accuracy["games"] == 0 and accuracy["pending"] == 1 and accuracy["voids"] == 1
    assert accuracy["forecast_entries"] == 2 and accuracy["brier"] is None
