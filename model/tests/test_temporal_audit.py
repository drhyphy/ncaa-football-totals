from dataclasses import replace

import numpy as np
import pandas as pd

from ncaaf_model.config import load_settings
from ncaaf_model.public_features import attach_public_features, load_public_season
from ncaaf_model.totals_features import FORM_STATS, add_pregame_forms, historical_game_features, latest_team_states


def _team_history():
    rows = []
    for i, (season, day, score) in enumerate([(2024, "2024-11-01", 10), (2025, "2025-09-01", 20), (2025, "2025-09-08", 90)]):
        rows.append({"team_id": 1, "team": "Team", "game_id": i + 1, "season": season,
                     "start_date": pd.Timestamp(day, tz="UTC"), **{field: float(score) for field in FORM_STATS}})
    return pd.DataFrame(rows)


def test_live_form_matches_historical_current_season_and_rejects_unavailable_game():
    history = _team_history()
    # Sept 8 midnight's game cannot be observed at 02:00 even though a later
    # downloaded archive already marks it final.
    live = latest_team_states(history, season=2025, as_of="2025-09-08T02:00:00Z")
    pregame = add_pregame_forms(history).iloc[-1]
    assert live.iloc[0]["prior_games"] == 2
    assert live.iloc[0]["season_games"] == 1
    for field in FORM_STATS:
        assert np.isclose(live.iloc[0][f"{field}_form"], pregame[f"{field}_form"])
    after = latest_team_states(history, season=2025, as_of="2025-09-09T02:00:00Z")
    assert after.iloc[0]["season_games"] == 2
    assert after.iloc[0]["points_for_form"] > live.iloc[0]["points_for_form"]


def test_fpi_rejects_season_contemporaneous_but_future_or_missing_timestamp(monkeypatch):
    public = pd.DataFrame({"team_id": [1, 2], "snapshot_week": [1, 1], "fpi_fpi": [90.0, -5.0],
                           "fpi_available_at": ["2025-12-15T00:00Z", None], "season": [2025, 2025]})
    monkeypatch.setattr("ncaaf_model.public_features.load_public_season", lambda settings, season: public)
    games = pd.DataFrame({"season": [2025], "week": [2], "home_id": [1], "away_id": [2],
                          "game_date": ["2025-09-08T00:00:00Z"]})
    result = attach_public_features(games, load_settings(), [2025])
    assert pd.isna(result.iloc[0]["home_fpi_fpi"])
    assert pd.isna(result.iloc[0]["away_fpi_fpi"])
    assert result.iloc[0]["coverage_fpi"] == 0


def test_fpi_is_unavailable_before_publication_even_for_future_kickoff(monkeypatch):
    public = pd.DataFrame({"team_id": [1, 2], "snapshot_week": [1, 1], "fpi_fpi": [10.0, -5.0],
                           "fpi_available_at": ["2025-09-03T00:00Z"] * 2, "season": [2025, 2025]})
    monkeypatch.setattr("ncaaf_model.public_features.load_public_season", lambda settings, season: public)
    games = pd.DataFrame({"season": [2025], "week": [2], "home_id": [1], "away_id": [2],
                          "commence_time": ["2025-09-08T00:00:00Z"]})
    result = attach_public_features(games, load_settings(), [2025], live_latest_week=1, as_of="2025-09-02T00:00Z")
    assert pd.isna(result.iloc[0]["home_fpi_fpi"])


def test_public_loader_rejects_out_of_sequence_and_unaudited_roster(monkeypatch):
    fpi = pd.DataFrame({"team_id": [1, 2], "week": [1, 1], "fpi": [99., 5.],
                        "snapshot_is_contemporaneous": [True, True], "snapshot_out_of_sequence": [True, False],
                        "last_updated": ["2025-12-01T00:00Z", "2025-09-01T00:00Z"],
                        "run_date_time_key": [202512010000, 202509010000]})
    def read(path):
        if path.name.startswith("fpi_weekly"):
            return fpi
        if path.name.startswith("returning_production"):
            raise AssertionError("Unaudited roster data must never enter scored features")
        return pd.DataFrame()
    monkeypatch.setattr("ncaaf_model.public_features._read_existing", read)
    result = load_public_season(load_settings(), 2025)
    assert result.team_id.tolist() == [2]
    assert not any(name.startswith("roster_") for name in result.columns)


def test_synthetic_default_odds_never_become_training_or_backtest_rows(tmp_path, monkeypatch):
    settings = replace(load_settings(), root=tmp_path)
    raw = settings.raw_dir / "sportsdataverse"
    raw.mkdir(parents=True)
    history = _team_history().iloc[1:].copy()
    opponent = history.copy()
    opponent["team_id"] = 2
    monkeypatch.setattr("ncaaf_model.totals_features.load_team_games", lambda settings, seasons: pd.concat([history, opponent]))
    games = pd.DataFrame({"game_id": [2, 3], "season": [2025, 2025], "week": [1, 2],
                          "status": ["STATUS_FINAL"] * 2, "home_score": [20, 90], "away_score": [10, 7],
                          "home_id": [1, 1], "away_id": [2, 2], "game_date": ["2025-09-01", "2025-09-08"]})
    games.to_parquet(raw / "cfb_schedule_2025.parquet")
    pd.DataFrame({"game_id": [2, 3], "season": [2025, 2025], "week": [1, 2],
                  "over_under": [55.5, 50.5], "home_team_spread": [-2.5, -3.5],
                  "odds_source": ["default", "core_odds_api"]}).to_parquet(raw / "betting_2025.parquet")
    result = historical_game_features(settings, [2025])
    assert result.game_id.tolist() == [3]
