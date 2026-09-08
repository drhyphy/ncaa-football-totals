import math

import pandas as pd

from ncaaf_model.timing import prepare_timing_dataset, summarize_timing, timing_bucket


def test_daily_timing_bucket_boundaries() -> None:
    assert timing_bucket(0) == "D0"
    assert timing_bucket(23.99) == "D0"
    assert timing_bucket(24) == "D1"
    assert timing_bucket(191.9) == "D7"
    assert timing_bucket(192) == "D8+"
    assert timing_bucket(-0.01) == "started"


def test_timing_dataset_deduplicates_same_game_side_bucket() -> None:
    kickoff = "2026-09-05T20:00:00Z"
    rows = []
    for prediction_id, snapshot, side, paper_bet, probability in [
        ("old-home", "2026-09-04T12:00:00Z", "home", True, 0.60),
        ("new-home", "2026-09-04T18:00:00Z", "home", True, 0.62),
        ("new-away", "2026-09-04T18:00:00Z", "away", False, 0.38),
    ]:
        rows.append(
            {
                "prediction_id": prediction_id,
                "snapshot_time": snapshot,
                "commence_time": kickoff,
                "espn_game_id": 1,
                "event_id": "event-1",
                "candidate": "market_fpi_residual",
                "side": side,
                "paper_bet": paper_bet,
                "model_probability": probability,
                "expected_value": 0.08 if paper_bet else -0.02,
                "decimal_odds": 2.0,
                "away_team": "Away",
                "home_team": "Home",
            }
        )
    grades = pd.DataFrame(
        [
            {
                "prediction_id": "new-home",
                "result": "win",
                "flat_profit_units": 1.0,
                "price_clv": 0.03,
                "probability_clv": 0.02,
                "home_score": 24,
                "away_score": 17,
            },
            {
                "prediction_id": "new-away",
                "result": "loss",
                "flat_profit_units": -1.0,
                "price_clv": -0.03,
                "probability_clv": -0.02,
                "home_score": 24,
                "away_score": 17,
            },
        ]
    )
    prepared = prepare_timing_dataset(pd.DataFrame(rows), grades)
    assert set(prepared["prediction_id"]) == {"new-home", "new-away"}
    summary, conclusion = summarize_timing(prepared, minimum_bets=2)
    d1 = summary.loc[summary["timing_bucket"].eq("D1")].iloc[0]
    assert d1["signals"] == 1
    assert d1["graded_signals"] == 1
    assert math.isclose(d1["flat_roi"], 1.0)
    assert conclusion["status"] == "collecting"
