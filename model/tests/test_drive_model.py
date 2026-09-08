import json

import numpy as np
import pandas as pd

from ncaaf_model.drive_model import (
    FEATURES, PRIORS, _state, aggregate_drive_team_games, build_drive_features,
    drive_probabilities, drive_projections, fit_drive_artifact,
)


def raw_game(game_id=1, when="2025-09-01T12:00Z"):
    schedule = pd.DataFrame([{"game_id": game_id, "season": 2025, "week": 1,
                              "home_id": 1, "away_id": 2, "home_score": 24,
                              "away_score": 17, "game_date": when, "status": "STATUS_FINAL"}])
    rows = []
    for team in (1, 2):
        for i, result in enumerate(("TD", "FG", "PUNT", "PUNT", "INT", "PUNT", "END OF HALF", "TD")):
            rows.append({"game_id": game_id, "drive_id": f"{game_id}-{team}-{i}", "team_id": team,
                         "result": result, "start_period": 5 if i == 7 else 1,
                         "end_period": 5 if i == 7 else 1, "offensive_plays": 5,
                         "time_elapsed": "2:30", "start_yard_line": 20 if team == 1 else 80})
    return pd.DataFrame(rows), schedule


def history_row(game_id, team_id, when, season=2025, multiplier=1.):
    row = {"game_id": game_id, "team_id": team_id, "season": season,
           "game_date": pd.Timestamp(when), "available_at": pd.Timestamp(when) + pd.Timedelta(hours=6)}
    row.update({prefix + k: v * multiplier for prefix in ("", "def_") for k, v in PRIORS.items()})
    return row


def test_regulation_offensive_scoring_excludes_overtime_and_terminal_possessions():
    drives, schedule = raw_game()
    result = aggregate_drive_team_games(drives, schedule)
    assert len(result) == 2
    assert (result["drives"] == 6).all()
    assert np.allclose(result["td_rate"], 1 / 6)
    assert np.allclose(result["fg_rate"], 1 / 6)
    assert np.allclose(result["seconds_per_drive"], 7 * 150 / 6)
    assert (result["terminal_drives"] == 1).all()
    assert "start_yard_line" not in result


def test_unfinished_game_and_one_sided_incomplete_coverage_do_not_become_history():
    drives, schedule = raw_game()
    live = schedule.assign(status="STATUS_IN_PROGRESS")
    assert aggregate_drive_team_games(drives, live).empty
    incomplete = drives.loc[drives["team_id"] == 1]
    assert aggregate_drive_team_games(incomplete, schedule).empty


def test_features_cannot_see_current_future_or_not_yet_available_results():
    games = pd.DataFrame([{"game_id": 2, "season": 2025, "week": 2,
                           "home_id": 1, "away_id": 2, "game_date": "2025-09-08T12:00Z",
                           "market_total": 50.5, "market_home_spread": -3.}])
    rows = [history_row(1, t, "2025-09-01T12:00Z") for t in (1, 2)]
    base = build_drive_features(games, pd.DataFrame(rows))
    rows += [history_row(i, t, time, multiplier=10.) for i, time in
             [(2, "2025-09-08T12:00Z"), (3, "2025-09-09T12:00Z"), (4, "2025-09-08T09:00Z")]
             for t in (1, 2)]
    augmented = build_drive_features(games, pd.DataFrame(rows))
    pd.testing.assert_frame_equal(base, augmented)


def test_live_snapshot_caps_history_even_when_matchup_is_a_week_away():
    games = pd.DataFrame([{"game_id": 3, "season": 2025, "week": 3, "home_id": 1, "away_id": 2,
                           "commence_time": "2025-09-15T12:00Z", "market_total": 50.5}])
    rows = [history_row(1, t, "2025-09-01T12:00Z") for t in (1, 2)]
    rows += [history_row(2, t, "2025-09-08T12:00Z", multiplier=2.) for t in (1, 2)]
    result = build_drive_features(games, pd.DataFrame(rows), as_of="2025-09-08T17:59Z")
    assert result.loc[0, "home_drive_history_games"] == 1
    later = build_drive_features(games, pd.DataFrame(rows), as_of="2025-09-08T18:01Z")
    assert later.loc[0, "home_drive_history_games"] == 2


def test_offseason_turnover_shrinks_old_information_toward_prior():
    history = pd.DataFrame([history_row(1, 1, "2024-11-01T12:00Z", season=2024, multiplier=2.)])
    prior = PRIORS["td_rate"]
    assert prior < _state(history, 2025)["td_rate"] < _state(history, 2024)["td_rate"]
    assert _state(pd.DataFrame(), 2025)["td_rate"] == prior


def test_push_probabilities_and_non_half_integer_support_are_consistent():
    over, under, push = drive_probabilities(50., 50., 15.)
    assert push > 0
    assert np.isclose(over, under)
    assert np.isclose(over + under + push, 1.)
    over_half, under_half, push_half = drive_probabilities(50., 50.5, 15.)
    assert push_half == 0
    assert np.isclose(over_half + under_half, 1.)
    assert np.isclose(over_half, over)
    assert np.isclose(drive_probabilities(50., 50.2, 15.)[0], over_half)


def test_portable_artifact_roundtrip_and_sparse_history_abstention():
    rng = np.random.default_rng(4)
    frame = pd.DataFrame(rng.normal(size=(120, len(FEATURES))), columns=FEATURES)
    frame["market_total"] = 50.5
    frame["actual_total"] = rng.integers(20, 81, size=120)
    frame["season"] = 2022
    frame["drive_history_eligible"] = True
    artifact = fit_drive_artifact(frame)
    restored = json.loads(json.dumps(artifact, allow_nan=False))
    a, b = drive_projections(frame, artifact), drive_projections(frame, restored)
    for name in a:
        np.testing.assert_allclose(a[name], b[name])
    sparse = drive_projections(frame.assign(drive_history_eligible=False), restored)
    for values in sparse.values():
        np.testing.assert_allclose(values, frame["market_total"])
    assert artifact["bet_eligible"] is False


def test_empty_daily_slate_returns_empty_candidate_predictions():
    frame = build_drive_features(pd.DataFrame(), pd.DataFrame())
    assert all(len(values) == 0 for values in drive_projections(frame, {}).values())
