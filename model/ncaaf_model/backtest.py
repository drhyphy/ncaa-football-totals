from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.special import expit, logit
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss

from .config import Settings
from .storage import atomic_write_csv, atomic_write_json, utc_now


@dataclass
class MarketWinModel:
    model: LogisticRegression
    total_median: float

    @staticmethod
    def features(frame: pd.DataFrame, total_fallback: float) -> np.ndarray:
        margin = pd.to_numeric(frame["market_home_margin"], errors="coerce").to_numpy(float)
        total = pd.to_numeric(frame["total"], errors="coerce").fillna(total_fallback).to_numpy(float)
        return np.column_stack([margin, margin / np.sqrt(np.maximum(total, 1.0))])

    @classmethod
    def fit(cls, frame: pd.DataFrame) -> "MarketWinModel":
        total_median = float(frame["total"].median())
        features = cls.features(frame, total_median)
        model = LogisticRegression(C=0.1, max_iter=2000, random_state=0)
        model.fit(features, frame["home_win"].to_numpy(int))
        return cls(model=model, total_median=total_median)

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return self.model.predict_proba(self.features(frame, self.total_median))[:, 1]

    def metadata(self) -> dict[str, Any]:
        return {
            "intercept": float(self.model.intercept_[0]),
            "coefficients": [float(value) for value in self.model.coef_[0]],
            "features": ["market_home_margin", "market_home_margin/sqrt(total)"],
            "total_median": self.total_median,
            "C": 0.1,
        }


def prepare_history(settings: Settings, seasons: range | list[int]) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    root = settings.raw_dir / "sportsdataverse"
    for season in seasons:
        schedule = pd.read_parquet(root / f"cfb_schedule_{season}.parquet")
        betting = pd.read_parquet(root / f"betting_{season}.parquet")
        power = pd.read_parquet(root / f"power_index_{season}.parquet")
        home_power = power.rename(
            columns={"team_id": "home_id", "gameprojection": "fpi_home_probability", "teampredptdiff": "fpi_home_margin"}
        )[["game_id", "home_id", "fpi_home_probability", "fpi_home_margin"]]
        frame = schedule.merge(betting, on=["game_id", "season", "week"], how="inner")
        frame = frame.merge(home_power, on=["game_id", "home_id"], how="left")
        frame = frame.loc[
            frame["status"].eq("STATUS_FINAL")
            & frame["home_score"].notna()
            & frame["away_score"].notna()
            & frame["home_team_spread"].notna()
            & frame["over_under"].notna()
            & frame["home_score"].ne(frame["away_score"])
        ].copy()
        frame["home_win"] = frame["home_score"].gt(frame["away_score"]).astype(int)
        frame["market_home_margin"] = -pd.to_numeric(frame["home_team_spread"])
        frame["total"] = pd.to_numeric(frame["over_under"])
        frame["fpi_home_probability"] = pd.to_numeric(frame["fpi_home_probability"], errors="coerce") / 100.0
        rows.append(
            frame[
                [
                    "game_id",
                    "season",
                    "week",
                    "game_date",
                    "home_team",
                    "away_team",
                    "home_score",
                    "away_score",
                    "home_win",
                    "market_home_margin",
                    "total",
                    "fpi_home_probability",
                    "fpi_home_margin",
                ]
            ]
        )
    return pd.concat(rows, ignore_index=True).sort_values(["season", "week", "game_date"]).reset_index(drop=True)


def tune_alpha(outcomes: np.ndarray, market: np.ndarray, external: np.ndarray, step: float) -> float:
    valid = np.isfinite(external)
    if valid.sum() < 50:
        return 0.0
    market_logits = logit(np.clip(market[valid], 0.005, 0.995))
    external_logits = logit(np.clip(external[valid], 0.005, 0.995))
    grid = np.arange(0.0, 1.0 + step / 2.0, step)
    losses = [log_loss(outcomes[valid], expit(market_logits + alpha * (external_logits - market_logits))) for alpha in grid]
    return float(grid[int(np.argmin(losses))])


def _metrics(outcome: np.ndarray, probability: np.ndarray) -> dict[str, float]:
    p = np.clip(probability, 0.005, 0.995)
    return {
        "log_loss": float(log_loss(outcome, p)),
        "brier": float(brier_score_loss(outcome, p)),
        "accuracy": float(accuracy_score(outcome, p >= 0.5)),
    }


def walk_forward(history: pd.DataFrame, settings: Settings) -> tuple[pd.DataFrame, pd.DataFrame]:
    predictions: list[pd.DataFrame] = []
    metric_rows: list[dict[str, Any]] = []
    seasons = sorted(int(value) for value in history["season"].unique())
    for test_season in seasons[2:]:
        train = history.loc[history["season"].lt(test_season)].copy()
        test = history.loc[history["season"].eq(test_season)].copy()
        if len(train) < 100 or test.empty:
            continue
        market_model = MarketWinModel.fit(train)
        train_market = market_model.predict(train)
        test_market = market_model.predict(test)
        fpi_train = test_fpi = None
        fpi_train = train["fpi_home_probability"].to_numpy(float)
        fpi_test = test["fpi_home_probability"].to_numpy(float)
        alpha = tune_alpha(train["home_win"].to_numpy(int), train_market, fpi_train, settings.alpha_grid_step)
        valid = np.isfinite(fpi_test)
        hybrid = np.full(len(test), np.nan)
        hybrid[valid] = expit(
            logit(np.clip(test_market[valid], 0.005, 0.995))
            + alpha
            * (
                logit(np.clip(fpi_test[valid], 0.005, 0.995))
                - logit(np.clip(test_market[valid], 0.005, 0.995))
            )
        )
        output = test.copy()
        output["market_probability"] = test_market
        output["fpi_probability"] = fpi_test
        output["hybrid_probability"] = hybrid
        output["selected_alpha"] = alpha
        output["training_max_season"] = test_season - 1
        predictions.append(output)
        for name, probabilities, mask in (
            ("market_spread_total", test_market, np.ones(len(test), dtype=bool)),
            ("fpi_game_projection", fpi_test, valid),
            ("market_fpi_residual", hybrid, valid),
        ):
            metrics = _metrics(test["home_win"].to_numpy(int)[mask], probabilities[mask])
            metrics.update(
                {
                    "test_season": test_season,
                    "model": name,
                    "n": int(mask.sum()),
                    "selected_alpha": alpha if name == "market_fpi_residual" else np.nan,
                    "full_coverage_regime": test_season >= settings.historical_full_coverage_start + 1,
                }
            )
            metric_rows.append(metrics)
    return pd.concat(predictions, ignore_index=True), pd.DataFrame(metric_rows)


def pooled_metrics(predictions: pd.DataFrame, minimum_season: int) -> list[dict[str, Any]]:
    frame = predictions.loc[predictions["season"].ge(minimum_season)]
    rows: list[dict[str, Any]] = []
    fpi_coverage = frame["fpi_probability"].notna()
    for name, column, coverage in (
        ("market_spread_total_all", "market_probability", frame["market_probability"].notna()),
        ("market_spread_total_paired", "market_probability", fpi_coverage),
        ("fpi_game_projection", "fpi_probability", fpi_coverage),
        ("market_fpi_residual", "hybrid_probability", fpi_coverage),
    ):
        valid = coverage & frame[column].notna()
        metrics = _metrics(frame.loc[valid, "home_win"].to_numpy(int), frame.loc[valid, column].to_numpy(float))
        metrics.update({"model": name, "n": int(valid.sum()), "minimum_season": minimum_season})
        rows.append(metrics)
    return rows


def fit_live_artifact(history: pd.DataFrame, settings: Settings) -> dict[str, Any]:
    complete = history.loc[history["season"].ge(settings.historical_full_coverage_start)].copy()
    market_model = MarketWinModel.fit(complete)
    market = market_model.predict(complete)
    outcomes = complete["home_win"].to_numpy(int)
    external = complete["fpi_home_probability"].to_numpy(float)
    early = complete["week"].le(4).to_numpy()
    late = ~early
    raw_early = tune_alpha(outcomes[early], market[early], external[early], settings.alpha_grid_step)
    raw_late = tune_alpha(outcomes[late], market[late], external[late], settings.alpha_grid_step)
    return {
        "artifact_version": "ncaaf-market-residual-v2-alpha75",
        "created_at": utc_now(),
        "training_seasons": sorted(int(value) for value in complete["season"].unique()),
        "training_games": int(len(complete)),
        "market_win_model": market_model.metadata(),
        "raw_alpha": {"weeks_0_4": raw_early, "weeks_5_plus": raw_late},
        "live_alpha_shrink": settings.live_alpha_shrink,
        "specification_status": "leading_candidate",
        "live_alpha": {
            "weeks_0_4": raw_early * settings.live_alpha_shrink,
            "weeks_5_plus": raw_late * settings.live_alpha_shrink,
        },
        "eligibility": {
            "bet_eligible_model": "market_fpi_residual",
            "challengers": ["market_public_ensemble", "fpi_only", "ratings_only", "market_only"],
            "historical_moneyline_roi_available": False,
        },
    }


def run_backtest(settings: Settings) -> dict[str, Any]:
    seasons = range(settings.historical_start_season, settings.season)
    history = prepare_history(settings, seasons)
    predictions, season_metrics = walk_forward(history, settings)
    all_pooled = pooled_metrics(predictions, int(predictions["season"].min()))
    primary_pooled = pooled_metrics(predictions, settings.historical_full_coverage_start + 1)
    artifact = fit_live_artifact(history, settings)
    artifact["walk_forward_metrics"] = {
        "all_available": all_pooled,
        "full_coverage_primary": primary_pooled,
    }
    settings.models_dir.mkdir(parents=True, exist_ok=True)
    settings.normalized_dir.mkdir(parents=True, exist_ok=True)
    predictions.to_parquet(settings.normalized_dir / "walk_forward_predictions.parquet", index=False)
    atomic_write_csv(settings.reports_dir / "backtest_by_season.csv", season_metrics)
    atomic_write_json(settings.models_dir / "market_residual_latest.json", artifact)
    summary = {
        "history_rows": len(history),
        "walk_forward_rows": len(predictions),
        "season_metrics": season_metrics.to_dict(orient="records"),
        "pooled_all_available": all_pooled,
        "pooled_full_coverage_primary": primary_pooled,
        "artifact": artifact,
        "limitations": [
            "SportsDataverse resolved historical betting files contain closing spread and total, not moneyline prices.",
            "The backtest validates probability quality only; forward CLV and realized ROI remain unproven.",
            "ESPN historical game projections are treated as frozen pregame values but do not expose original observation timestamps.",
        ],
    }
    atomic_write_json(settings.reports_dir / "backtest_summary.json", summary)
    return summary
