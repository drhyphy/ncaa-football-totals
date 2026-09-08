import math

import numpy as np
import pandas as pd

from ncaaf_model.totals_backtest import probability_over
from ncaaf_model.config import load_settings
from ncaaf_model.public_ensemble import PUBLIC_FAMILIES, fit_convex_stacker
from ncaaf_model.public_features import attach_public_features
from ncaaf_model.totals_features import add_pregame_forms
from ncaaf_model.totals_scoring import normalize_totals_odds


def test_probability_over_is_symmetric_and_moves_with_projection() -> None:
    assert math.isclose(float(probability_over(52.5, 52.5, 14.0, 7)), 0.5)
    assert float(probability_over(57.5, 52.5, 14.0, 7)) > 0.5
    assert float(probability_over(47.5, 52.5, 14.0, 7)) < 0.5


def test_pregame_form_is_shifted_and_cannot_see_current_result() -> None:
    rows = []
    for index, points in enumerate((10.0, 20.0, 99.0), start=1):
        row = {
            "team_id": 1,
            "season": 2025,
            "game_id": index,
            "start_date": pd.Timestamp(f"2025-09-{index:02d}", tz="UTC"),
        }
        for column in (
            "points_for",
            "points_against",
            "drives",
            "ppd_for",
            "ppd_against",
            "scrimmage_plays",
            "epa_per_play",
            "epa_allowed_per_play",
            "explosive_rate",
            "explosive_allowed",
            "yards_per_play",
            "yards_allowed_per_play",
            "pass_rate",
            "special_teams_epa",
        ):
            row[column] = points
        rows.append(row)
    forms = add_pregame_forms(pd.DataFrame(rows))
    assert np.isnan(forms.loc[0, "points_for_form"])
    assert forms.loc[1, "points_for_form"] == 10.0
    assert forms.loc[2, "points_for_form"] < 20.0
    assert forms.loc[2, "points_for_form"] != 99.0


def test_totals_normalization_pairs_prices_at_the_same_line() -> None:
    payload = [
        {
            "id": "event-1",
            "commence_time": "2099-09-01T00:00:00Z",
            "home_team": "Home",
            "away_team": "Away",
            "bookmakers": [
                {
                    "key": "fanduel",
                    "last_update": "2099-08-31T00:00:00Z",
                    "markets": [
                        {
                            "key": "totals",
                            "outcomes": [
                                {"name": "Over", "point": 51.5, "price": -105},
                                {"name": "Under", "point": 51.5, "price": -115},
                            ],
                        },
                        {
                            "key": "spreads",
                            "outcomes": [
                                {"name": "Home", "point": -3.0, "price": -110},
                                {"name": "Away", "point": 3.0, "price": -110},
                            ],
                        },
                    ],
                }
            ],
        }
    ]
    result = normalize_totals_odds(payload, ("fanduel",))
    assert len(result) == 1
    assert result.iloc[0]["market_total"] == 51.5
    assert result.iloc[0]["market_home_spread"] == -3.0
    assert result.iloc[0]["total_book_count"] == 1
    assert result.iloc[0]["quotes"][0]["fair_over"] < 0.5


def test_public_features_use_only_last_completed_week(monkeypatch) -> None:
    public = pd.DataFrame(
        {
            "team_id": pd.Series([1, 1, 2, 2], dtype="Int64"),
            "snapshot_week": [1, 2, 1, 2],
            "fpi_fpi": [10.0, 99.0, -5.0, -99.0],
            "fpi_available_at": pd.to_datetime(["2025-09-01T00:00Z", "2025-09-08T00:00Z"] * 2, utc=True),
            "season": [2025] * 4,
        }
    )
    monkeypatch.setattr("ncaaf_model.public_features.load_public_season", lambda settings, season: public)
    games = pd.DataFrame(
        {
            "season": [2025],
            "week": [2],
            "home_id": [1],
            "away_id": [2],
            "game_date": ["2025-09-06T12:00:00Z"],
        }
    )
    result = attach_public_features(games, load_settings(), [2025])
    assert result.loc[0, "snapshot_week"] == 1
    assert result.loc[0, "home_fpi_fpi"] == 10.0
    assert result.loc[0, "away_fpi_fpi"] == -5.0


def test_public_stacker_is_convex_and_market_anchored() -> None:
    rng = np.random.default_rng(7)
    base = pd.DataFrame({name: rng.normal(size=100) for name in PUBLIC_FAMILIES})
    fitted = fit_convex_stacker(base, rng.normal(size=100))
    weights = np.array(list(fitted["weights"].values()))
    assert np.all(weights >= -1e-10)
    assert weights.sum() <= 1.0 + 1e-8
