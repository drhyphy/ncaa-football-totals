from __future__ import annotations

import math
from typing import Any

import pandas as pd

from .config import Settings
from .storage import append_csv, utc_now


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def grade_totals_ledger(settings: Settings) -> dict[str, Any]:
    path = settings.ledger_dir / "totals_predictions.csv"
    if not path.exists():
        return {"graded": 0, "message": "totals prediction ledger does not exist"}
    predictions = pd.read_csv(path)
    schedule = pd.read_parquet(settings.raw_dir / "sportsdataverse" / f"cfb_schedule_{settings.season}.parquet")
    finals = schedule.loc[
        schedule["status"].eq("STATUS_FINAL") & schedule["home_score"].notna() & schedule["away_score"].notna()
    ].set_index("game_id")
    grades_path = settings.ledger_dir / "totals_grades.csv"
    existing = set(pd.read_csv(grades_path)["prediction_id"].astype(str)) if grades_path.exists() else set()
    predictions["snapshot_dt"] = pd.to_datetime(predictions["snapshot_time"], utc=True)
    predictions["kickoff_dt"] = pd.to_datetime(predictions["commence_time"], utc=True)
    rows: list[dict[str, Any]] = []
    for _, prediction in predictions.iterrows():
        prediction_id = str(prediction["prediction_id"])
        if prediction_id in existing or pd.isna(prediction["espn_game_id"]):
            continue
        game_id = int(float(prediction["espn_game_id"]))
        if game_id not in finals.index:
            continue
        game = finals.loc[game_id]
        if isinstance(game, pd.DataFrame):
            game = game.iloc[0]
        actual_total = float(game["home_score"]) + float(game["away_score"])
        entry_line = float(prediction["line"])
        side = str(prediction["side"])
        if actual_total == entry_line:
            result = "push"
            profit = 0.0
        else:
            won = actual_total > entry_line if side == "over" else actual_total < entry_line
            result = "win" if won else "loss"
            price = float(prediction["american_odds"])
            profit = (price / 100.0 if price > 0 else 100.0 / abs(price)) if won else -1.0
        close_pool = predictions.loc[
            predictions["event_id"].eq(prediction["event_id"])
            & predictions["candidate"].eq(prediction["candidate"])
            & predictions["side"].eq(side)
            & predictions["snapshot_dt"].lt(prediction["kickoff_dt"])
        ].sort_values("snapshot_dt")
        close = close_pool.iloc[-1] if not close_pool.empty else prediction
        closing_line = float(close["line"])
        line_clv = closing_line - entry_line if side == "over" else entry_line - closing_line
        entry_decimal = float(prediction["decimal_odds"])
        closing_decimal = float(close["decimal_odds"])
        rows.append(
            {
                "prediction_id": prediction_id,
                "graded_at": utc_now(),
                "event_id": prediction["event_id"],
                "espn_game_id": game_id,
                "candidate": prediction["candidate"],
                "side": side,
                "paper_bet": _as_bool(prediction["paper_bet"]),
                "home_score": game["home_score"],
                "away_score": game["away_score"],
                "actual_total": actual_total,
                "entry_line": entry_line,
                "closing_line": closing_line,
                "line_clv_points": line_clv,
                "entry_decimal": entry_decimal,
                "closing_decimal": closing_decimal,
                "price_clv": entry_decimal / closing_decimal - 1.0 if closing_decimal > 1.0 else math.nan,
                "result": result,
                "flat_profit_units": profit,
                "bankroll_profit_fraction": profit * float(prediction["stake_bankroll_fraction"]),
            }
        )
    if not rows:
        return {"graded": 0, "message": "no new completed totals predictions"}
    grades = pd.DataFrame(rows)
    append_csv(grades_path, grades, dedupe_key="prediction_id")
    paper = grades.loc[grades["paper_bet"]]
    return {
        "graded": int(len(grades)),
        "paper_bets_graded": int(len(paper)),
        "paper_profit_units": float(paper["flat_profit_units"].sum()) if len(paper) else 0.0,
        "mean_line_clv_points": float(paper["line_clv_points"].mean()) if len(paper) else None,
    }
