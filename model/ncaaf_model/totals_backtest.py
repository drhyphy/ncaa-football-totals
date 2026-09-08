from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from scipy.stats import t as student_t
from sklearn.compose import TransformedTargetRegressor
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .config import Settings
from .storage import atomic_write_csv, atomic_write_json, utc_now
from .totals_features import (
    historical_game_features,
    latest_team_states,
    load_team_games,
    model_feature_columns,
)


TOTAL_CANDIDATES = (
    "market_only",
    "market_consensus_shop",
    "pace_efficiency",
    "market_residual_ridge",
    "market_residual_hgb",
    "market_residual_ensemble",
)
RECOMMENDED_TOTAL_MODEL = "market_consensus_shop"


def build_models() -> dict[str, Any]:
    ridge = make_pipeline(
        SimpleImputer(strategy="median", add_indicator=True),
        StandardScaler(),
        Ridge(alpha=80.0),
    )
    hgb = make_pipeline(
        SimpleImputer(strategy="median", add_indicator=True),
        HistGradientBoostingRegressor(
            loss="squared_error",
            learning_rate=0.04,
            max_iter=180,
            max_leaf_nodes=12,
            min_samples_leaf=35,
            l2_regularization=12.0,
            random_state=2026,
        ),
    )
    return {"ridge": ridge, "hgb": hgb}


def fit_models(train: pd.DataFrame, features: list[str]) -> dict[str, Any]:
    models = build_models()
    x = train[features].astype(float)
    residual = (train["actual_total"] - train["market_total"]).to_numpy(float)
    for model in models.values():
        model.fit(x, residual)
    return models


def candidate_projections(
    frame: pd.DataFrame, models: dict[str, Any], features: list[str], settings: Settings
) -> dict[str, np.ndarray]:
    market = frame["market_total"].to_numpy(float)
    x = frame[features].astype(float)
    ridge_residual = np.clip(models["ridge"].predict(x), -14.0, 14.0)
    hgb_residual = np.clip(models["hgb"].predict(x), -14.0, 14.0)
    ensemble_residual = settings.totals_ridge_weight * ridge_residual + settings.totals_hgb_weight * hgb_residual
    structural = frame["structural_total"].fillna(frame["market_total"]).to_numpy(float)
    # The structural candidate is partially market anchored because raw PPD is
    # especially unstable after roster/coaching turnover and in FBS-FCS games.
    pace_efficiency = market + 0.35 * np.clip(structural - market, -20.0, 20.0)
    return {
        "market_only": market,
        "market_consensus_shop": market,
        "pace_efficiency": pace_efficiency,
        "market_residual_ridge": market + ridge_residual,
        "market_residual_hgb": market + hgb_residual,
        "market_residual_ensemble": market + ensemble_residual,
    }


def probability_over(projection: np.ndarray | float, line: np.ndarray | float, sigma: float, df: int) -> np.ndarray:
    scale = max(float(sigma), 1.0) * math.sqrt((df - 2.0) / df)
    return student_t.cdf((np.asarray(projection, dtype=float) - np.asarray(line, dtype=float)) / scale, df=df)


def _profit(outcome: np.ndarray, line: np.ndarray, side_over: np.ndarray) -> np.ndarray:
    won = np.where(side_over, outcome > line, outcome < line)
    pushed = outcome == line
    return np.where(pushed, 0.0, np.where(won, 100.0 / 110.0, -1.0))


def add_bet_columns(frame: pd.DataFrame, settings: Settings, sigma: float) -> pd.DataFrame:
    output = frame.copy()
    edge = output["projected_total"] - output["market_total"]
    output["side"] = np.where(edge >= 0, "over", "under")
    p_over = probability_over(
        output["projected_total"].to_numpy(float),
        output["market_total"].to_numpy(float),
        sigma,
        settings.totals_student_df,
    )
    output["cover_probability"] = np.where(edge >= 0, p_over, 1.0 - p_over)
    output["ev_at_minus_110"] = output["cover_probability"] * (100.0 / 110.0) - (1.0 - output["cover_probability"])
    history_ok = (
        output["home_prior_games"].fillna(0).ge(settings.totals_min_team_history)
        & output["away_prior_games"].fillna(0).ge(settings.totals_min_team_history)
    )
    output["bet"] = (
        ~output["candidate"].isin(["market_only", "market_consensus_shop"])
        & edge.abs().ge(settings.totals_min_edge_points)
        & output["ev_at_minus_110"].ge(settings.totals_min_ev)
        & history_ok
    )
    output["profit_units"] = np.where(
        output["bet"],
        _profit(
            output["actual_total"].to_numpy(float),
            output["market_total"].to_numpy(float),
            output["side"].eq("over").to_numpy(),
        ),
        0.0,
    )
    output["total_error"] = output["actual_total"] - output["projected_total"]
    return output


def _bootstrap_roi(profits: np.ndarray, blocks: np.ndarray | None = None) -> tuple[float | None, float | None]:
    if len(profits) < 20:
        return None, None
    rng = np.random.default_rng(20260819)
    if blocks is None:
        samples = rng.choice(profits, size=(2000, len(profits)), replace=True).mean(axis=1)
    else:
        grouped = pd.DataFrame({"profit": profits, "block": blocks}).groupby("block")["profit"].agg(["sum", "count"]).to_numpy()
        if len(grouped) < 8:
            return None, None
        selected = rng.integers(0, len(grouped), size=(3000, len(grouped)))
        totals = grouped[selected].sum(axis=1)
        samples = totals[:, 0] / totals[:, 1]
    low, high = np.quantile(samples, [0.025, 0.975])
    return float(low), float(high)


def summarize_predictions(frame: pd.DataFrame, minimum_season: int | None = None) -> list[dict[str, Any]]:
    sample = frame if minimum_season is None else frame.loc[frame["season"].ge(minimum_season)]
    rows: list[dict[str, Any]] = []
    for candidate, group in sample.groupby("candidate", sort=False):
        bets = group.loc[group["bet"]]
        decided = bets.loc[bets["actual_total"].ne(bets["market_total"])]
        wins = (
            (decided["side"].eq("over") & decided["actual_total"].gt(decided["market_total"]))
            | (decided["side"].eq("under") & decided["actual_total"].lt(decided["market_total"]))
        )
        roi_low, roi_high = _bootstrap_roi(bets["profit_units"].to_numpy(float), (bets["season"].astype(str) + ":" + bets["week"].astype(str)).to_numpy())
        rows.append(
            {
                "candidate": candidate,
                "minimum_season": minimum_season,
                "games": int(len(group)),
                "mae": float(mean_absolute_error(group["actual_total"], group["projected_total"])),
                "rmse": float(mean_squared_error(group["actual_total"], group["projected_total"]) ** 0.5),
                "bets": int(len(bets)),
                "wins": int(wins.sum()),
                "losses": int((~wins).sum()),
                "pushes": int(len(bets) - len(decided)),
                "win_rate_decided": float(wins.mean()) if len(wins) else None,
                "flat_roi": float(bets["profit_units"].mean()) if len(bets) else None,
                "profit_units": float(bets["profit_units"].sum()),
                "roi_95_low": roi_low,
                "roi_95_high": roi_high,
            }
        )
    return rows


def walk_forward(features_frame: pd.DataFrame, settings: Settings) -> pd.DataFrame:
    features = model_feature_columns()
    seasons = sorted(int(value) for value in features_frame["season"].unique())
    output: list[pd.DataFrame] = []
    for test_season in seasons[3:]:
        train = features_frame.loc[features_frame["season"].lt(test_season)].copy()
        test = features_frame.loc[features_frame["season"].eq(test_season)].copy()
        if len(train) < 250 or test.empty:
            continue
        models = fit_models(train, features)
        sigma = float(np.std(train["actual_total"] - train["market_total"], ddof=1))
        projections = candidate_projections(test, models, features, settings)
        for candidate, values in projections.items():
            candidate_frame = test[
                [
                    "game_id",
                    "season",
                    "week",
                    "game_date",
                    "home_team",
                    "away_team",
                    "home_score",
                    "away_score",
                    "actual_total",
                    "market_total",
                    "home_prior_games",
                    "away_prior_games",
                ]
            ].copy()
            candidate_frame["candidate"] = candidate
            candidate_frame["projected_total"] = values
            candidate_frame["training_max_season"] = test_season - 1
            candidate_frame["distribution_sigma"] = sigma
            output.append(add_bet_columns(candidate_frame, settings, sigma))
    if not output:
        raise RuntimeError("Not enough historical seasons to run the totals walk-forward backtest")
    return pd.concat(output, ignore_index=True)


def _atomic_joblib(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(fd)
    try:
        joblib.dump(payload, temporary)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run_totals_backtest(settings: Settings) -> dict[str, Any]:
    seasons = list(range(settings.historical_start_season, settings.season))
    game_features = historical_game_features(settings, seasons)
    valid = game_features.loc[
        game_features["market_total"].notna()
        & game_features["market_home_spread"].notna()
        & game_features["actual_total"].notna()
    ].copy()
    predictions = walk_forward(valid, settings)
    primary_start = max(settings.historical_full_coverage_start, int(predictions["season"].min()))
    summary_all = summarize_predictions(predictions)
    summary_primary = summarize_predictions(predictions, primary_start)

    features = model_feature_columns()
    recent = valid.loc[valid["season"].ge(settings.historical_full_coverage_start)]
    # Sparse resolved-line coverage before 2023 is retained for audit/backtest
    # context but is not representative enough to train the live challengers.
    live_models = fit_models(recent, features)
    sigma = float(np.std(recent["actual_total"] - recent["market_total"], ddof=1))
    team_games = load_team_games(settings, seasons)
    team_states = latest_team_states(team_games, season=settings.season)
    settings.models_dir.mkdir(parents=True, exist_ok=True)
    team_games.to_parquet(settings.models_dir / "team_games_history.parquet", index=False)

    settings.models_dir.mkdir(parents=True, exist_ok=True)
    settings.normalized_dir.mkdir(parents=True, exist_ok=True)
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    model_path = settings.models_dir / "totals_models_latest.joblib"
    state_path = settings.models_dir / "totals_team_states_latest.parquet"
    _atomic_joblib(model_path, live_models)
    team_states.to_parquet(state_path, index=False)
    predictions.to_parquet(settings.normalized_dir / "totals_walk_forward_predictions.parquet", index=False)

    artifact = {
        "artifact_version": "ncaaf-totals-market-residual-audited-v3",
        "created_at": utc_now(),
        "recommended_candidate": RECOMMENDED_TOTAL_MODEL,
        "candidate_models": list(TOTAL_CANDIDATES),
        "training_seasons": sorted(int(value) for value in recent["season"].unique()),
        "training_games": int(len(recent)),
        "feature_columns": features,
        "distribution": {"family": "student_t", "df": settings.totals_student_df, "sigma": sigma},
        "ensemble_weights": {"ridge": settings.totals_ridge_weight, "hgb": settings.totals_hgb_weight},
        "gates": {
            "minimum_edge_points": settings.totals_min_edge_points,
            "minimum_line_value_points": settings.totals_min_line_value_points,
            "minimum_ev": settings.totals_min_ev,
            "minimum_team_history": settings.totals_min_team_history,
            "maximum_line_dispersion": settings.totals_max_line_dispersion,
            "minimum_books": settings.min_books,
        },
        "model_path": str(model_path.relative_to(settings.root)),
        "team_state_path": str(state_path.relative_to(settings.root)),
        "historical_price_assumption": "-110 on both sides; public archive has closing lines but no book prices",
    }
    atomic_write_json(settings.models_dir / "totals_artifact_latest.json", artifact)
    by_season = []
    for season, group in predictions.groupby("season"):
        for row in summarize_predictions(group):
            row["test_season"] = int(season)
            by_season.append(row)
    atomic_write_csv(settings.reports_dir / "totals_backtest_by_season.csv", pd.DataFrame(by_season))
    summary = {
        "artifact": artifact,
        "walk_forward_games": int(predictions["game_id"].nunique()),
        "walk_forward_rows": int(len(predictions)),
        "primary_start_season": primary_start,
        "all_walk_forward": summary_all,
        "primary_period": summary_primary,
        "limitations": [
            "All historical seasons have been inspected during model development; only frozen prospective predictions are an untouched test.",
            "Synthetic default-source odds are excluded. Confidence intervals resample season-week blocks, not independent bets.",
            "Historical odds are resolved closing totals without book-level prices or timestamps; ROI assumes -110.",
            "The recommended consensus-shopping candidate cannot be backtested from the resolved single-line archive; it is forward-only.",
            "The backtest is a close-horizon test and does not prove that an earlier posted number was executable.",
            "Weather, quarterback availability, transfers, and coordinator changes are primarily absorbed by the market prior in v1.",
            "FCS is covered by universal score/drive form, but sparse-history teams are forced to abstain.",
        ],
    }
    atomic_write_json(settings.reports_dir / "totals_backtest_summary.json", summary)
    return summary
