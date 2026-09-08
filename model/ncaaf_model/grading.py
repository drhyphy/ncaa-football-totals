from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pandas as pd

from .config import Settings
from .storage import append_csv, utc_now


def _profit_units(won: bool, american: float) -> float:
    if not won:
        return -1.0
    return american / 100.0 if american > 0 else 100.0 / abs(american)


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def grade_ledger(settings: Settings) -> dict[str, Any]:
    predictions_path = settings.ledger_dir / "predictions.csv"
    if not predictions_path.exists():
        return {"graded": 0, "message": "prediction ledger does not exist"}
    predictions = pd.read_csv(predictions_path)
    schedule = pd.read_parquet(settings.raw_dir / "sportsdataverse" / f"cfb_schedule_{settings.season}.parquet")
    finals = schedule.loc[
        schedule["status"].eq("STATUS_FINAL") & schedule["home_score"].notna() & schedule["away_score"].notna()
    ].set_index("game_id")
    existing_ids: set[str] = set()
    grades_path = settings.ledger_dir / "grades.csv"
    if grades_path.exists():
        existing_ids = set(pd.read_csv(grades_path)["prediction_id"].astype(str))
    predictions["snapshot_dt"] = pd.to_datetime(predictions["snapshot_time"], utc=True)
    predictions["kickoff_dt"] = pd.to_datetime(predictions["commence_time"], utc=True)
    rows: list[dict[str, Any]] = []
    for _, prediction in predictions.iterrows():
        prediction_id = str(prediction["prediction_id"])
        if prediction_id in existing_ids or pd.isna(prediction["espn_game_id"]):
            continue
        game_id = int(float(prediction["espn_game_id"]))
        if game_id not in finals.index:
            continue
        game = finals.loc[game_id]
        if isinstance(game, pd.DataFrame):
            game = game.iloc[0]
        home_score = float(game["home_score"])
        away_score = float(game["away_score"])
        if home_score == away_score:
            result, won, profit = "push", False, 0.0
        else:
            home_won = home_score > away_score
            won = home_won if prediction["side"] == "home" else not home_won
            result = "win" if won else "loss"
            profit = _profit_units(won, float(prediction["american_odds"]))
        close_pool = predictions.loc[
            predictions["event_id"].eq(prediction["event_id"])
            & predictions["candidate"].eq(prediction["candidate"])
            & predictions["side"].eq(prediction["side"])
            & predictions["snapshot_dt"].lt(prediction["kickoff_dt"])
        ].sort_values("snapshot_dt")
        close = close_pool.iloc[-1] if not close_pool.empty else prediction
        closing_decimal = float(close["decimal_odds"])
        entry_decimal = float(prediction["decimal_odds"])
        rows.append(
            {
                "prediction_id": prediction_id,
                "graded_at": utc_now(),
                "event_id": prediction["event_id"],
                "espn_game_id": game_id,
                "candidate": prediction["candidate"],
                "side": prediction["side"],
                "paper_bet": _as_bool(prediction["paper_bet"]),
                "home_score": home_score,
                "away_score": away_score,
                "result": result,
                "flat_profit_units": profit,
                "bankroll_profit_fraction": profit * float(prediction["stake_bankroll_fraction"]),
                "entry_decimal": entry_decimal,
                "closing_decimal": closing_decimal,
                "price_clv": entry_decimal / closing_decimal - 1.0 if closing_decimal > 1.0 else math.nan,
                "entry_market_probability": prediction["market_probability"],
                "closing_market_probability": close["market_probability"],
                "probability_clv": float(close["market_probability"]) - float(prediction["market_probability"]),
            }
        )
    if not rows:
        return {"graded": 0, "message": "no new completed predictions"}
    grades = pd.DataFrame(rows)
    append_csv(grades_path, grades, dedupe_key="prediction_id")
    paper = grades.loc[grades["paper_bet"]]
    return {
        "graded": int(len(grades)),
        "paper_bets_graded": int(len(paper)),
        "paper_profit_units": float(paper["flat_profit_units"].sum()) if not paper.empty else 0.0,
        "mean_price_clv": float(paper["price_clv"].mean()) if not paper.empty else None,
    }
