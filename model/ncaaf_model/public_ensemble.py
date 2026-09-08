from __future__ import annotations

import math
import os
import json
import tempfile
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import norm
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .config import Settings
from .storage import atomic_write_csv, atomic_write_json, utc_now
from .totals_backtest import _bootstrap_roi, probability_over
from .totals_features import historical_game_features, model_feature_columns
from .public_features import attach_public_features, family_feature_columns, public_feature_columns


PUBLIC_FAMILIES = ("fpi", "fei_epa", "summary", "roster_prior", "full_hgb")
PUBLIC_CANDIDATES = tuple(f"public_{name}" for name in PUBLIC_FAMILIES) + ("public_superensemble_v2",)


def _context_columns() -> list[str]:
    return [
        "market_total",
        "market_home_spread",
        "abs_spread",
        "spread_total_interaction",
        "home_implied_points",
        "away_implied_points",
        "week",
        "neutral_site",
    ]


def _full_columns() -> list[str]:
    public = [f"{side}_{column}" for side in ("home", "away") for column in public_feature_columns()]
    return list(dict.fromkeys(model_feature_columns() + public + [f"coverage_{name}" for name in family_feature_columns()]))


def _ensure(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    output = frame.copy()
    for column in columns:
        if column not in output:
            output[column] = np.nan
    return output[columns].astype(float)


def _ridge() -> Any:
    return make_pipeline(
        SimpleImputer(strategy="median", add_indicator=True),
        StandardScaler(),
        Ridge(alpha=140.0),
    )


def build_family_models() -> dict[str, Any]:
    models = {name: _ridge() for name in ("fpi", "fei_epa", "summary", "roster_prior")}
    models["full_hgb"] = make_pipeline(
        SimpleImputer(strategy="median", add_indicator=True),
        HistGradientBoostingRegressor(
            learning_rate=0.025,
            max_iter=220,
            max_leaf_nodes=10,
            min_samples_leaf=45,
            l2_regularization=20.0,
            random_state=2026,
        ),
    )
    return models


def feature_contracts() -> dict[str, list[str]]:
    context = _context_columns()
    families = family_feature_columns()
    contracts = {
        name: context + columns + [f"coverage_{name}"]
        for name, columns in families.items()
    }
    contracts["full_hgb"] = _full_columns()
    return contracts


def fit_family_models(train: pd.DataFrame) -> dict[str, Any]:
    models = build_family_models()
    contracts = feature_contracts()
    target = (train["actual_total"] - train["market_total"]).to_numpy(float)
    for name, model in models.items():
        model.fit(_ensure(train, contracts[name]), target)
    return models


def predict_family_residuals(models: dict[str, Any], frame: pd.DataFrame) -> pd.DataFrame:
    contracts = feature_contracts()
    output = pd.DataFrame(index=frame.index)
    for name, model in models.items():
        residual = np.clip(model.predict(_ensure(frame, contracts[name])), -12.0, 12.0)
        if name in family_feature_columns():
            coverage = pd.to_numeric(frame.get(f"coverage_{name}", 0.0), errors="coerce").fillna(0).to_numpy()
            residual = np.where(coverage >= 0.20, residual, 0.0)
        output[name] = residual
    return output


def fit_convex_stacker(base: pd.DataFrame, target: np.ndarray) -> dict[str, Any]:
    matrix = base[list(PUBLIC_FAMILIES)].fillna(0.0).to_numpy(float)
    y = np.asarray(target, dtype=float)

    def objective(parameters: np.ndarray) -> float:
        intercept, weights = parameters[0], parameters[1:]
        error = y - (intercept + matrix @ weights)
        return float(np.mean(error**2) + 0.35 * np.sum(weights**2) + 0.10 * intercept**2)

    result = minimize(
        objective,
        x0=np.r_[0.0, np.repeat(0.12, len(PUBLIC_FAMILIES))],
        bounds=[(-3.0, 3.0)] + [(0.0, 1.0)] * len(PUBLIC_FAMILIES),
        constraints=[{"type": "ineq", "fun": lambda values: 1.0 - values[1:].sum()}],
        method="SLSQP",
    )
    parameters = result.x if result.success else np.r_[0.0, np.repeat(0.1, len(PUBLIC_FAMILIES))]
    return {
        "intercept": float(parameters[0]),
        "weights": {name: float(weight) for name, weight in zip(PUBLIC_FAMILIES, parameters[1:])},
        "optimizer_success": bool(result.success),
    }


def stacked_residual(base: pd.DataFrame, stacker: dict[str, Any]) -> np.ndarray:
    result = np.full(len(base), float(stacker["intercept"]), dtype=float)
    for name, weight in stacker["weights"].items():
        result += float(weight) * base[name].fillna(0.0).to_numpy(float)
    return np.clip(result, -10.0, 10.0)


def base_oof_predictions(train: pd.DataFrame) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    seasons = sorted(int(value) for value in train["season"].unique())
    for test_season in seasons[2:]:
        fit = train.loc[train["season"].lt(test_season)]
        test = train.loc[train["season"].eq(test_season)]
        if len(fit) < 200 or test.empty:
            continue
        models = fit_family_models(fit)
        predicted = predict_family_residuals(models, test).reset_index(drop=True)
        predicted["season"] = test_season
        predicted["week"] = test["week"].to_numpy()
        predicted["actual_residual"] = (test["actual_total"] - test["market_total"]).to_numpy(float)
        rows.append(predicted)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def nested_meta_predictions(base_oof: pd.DataFrame) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    seasons = sorted(int(value) for value in base_oof["season"].unique())
    for test_season in seasons[1:]:
        fit = base_oof.loc[base_oof["season"].lt(test_season)]
        test = base_oof.loc[base_oof["season"].eq(test_season)]
        if len(fit) < 75 or test.empty:
            continue
        stacker = fit_convex_stacker(fit, fit["actual_residual"].to_numpy(float))
        predicted = test[["season", "week", "actual_residual"]].copy()
        predicted["stacked_residual"] = stacked_residual(test, stacker)
        rows.append(predicted)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def choose_edge_threshold(nested: pd.DataFrame, prediction_column: str = "stacked_residual") -> dict[str, Any]:
    if nested.empty or prediction_column not in nested:
        return {
            "selected": {"threshold": None, "bets": 0, "roi": None, "selection_score": None},
            "candidates": [],
            "abstain": True,
        }
    candidates: list[dict[str, Any]] = []
    for threshold in (2.0, 3.0, 4.0, 5.0, 6.0):
        bets = nested.loc[nested[prediction_column].abs().ge(threshold)]
        if len(bets) < 100:
            continue
        won = np.where(
            bets[prediction_column].gt(0),
            bets["actual_residual"].gt(0),
            bets["actual_residual"].lt(0),
        )
        push = bets["actual_residual"].eq(0).to_numpy()
        profits = np.where(push, 0.0, np.where(won, 100.0 / 110.0, -1.0))
        mean = float(profits.mean())
        blocks = bets["season"].astype(str) + ":" + bets["week"].astype(str)
        centered = pd.DataFrame({"block": blocks.to_numpy(), "u": profits - mean}).groupby("block")["u"].sum()
        if len(centered) < 8:
            continue
        se = float(np.sqrt(len(centered) / (len(centered) - 1) * np.sum(centered**2)) / len(profits))
        # One-sided familywise 95% gate over five thresholds and six candidates.
        # This remains retrospective validation, not permission to wager.
        critical_value = float(norm.ppf(1.0 - 0.05 / (5 * len(PUBLIC_CANDIDATES))))
        candidates.append(
            {"threshold": threshold, "bets": int(len(profits)), "blocks": int(len(centered)), "roi": mean, "cluster_standard_error": se, "critical_value": critical_value, "selection_score": mean - critical_value * se}
        )
    best = max(candidates, key=lambda row: row["selection_score"]) if candidates else {
        "threshold": None,
        "bets": 0,
        "roi": None,
        "selection_score": None,
    }
    # Abstaining is an explicit candidate with zero expected return. A model
    # must clear the conservative validation score before it may emit bets.
    abstain = best["selection_score"] is None or best["selection_score"] <= 0.0
    selected = (
        {"threshold": None, "bets": 0, "roi": None, "selection_score": 0.0}
        if abstain
        else best
    )
    return {"selected": selected, "candidates": candidates, "abstain": abstain}


def _score_candidate(
    frame: pd.DataFrame, candidate: str, projection: np.ndarray, sigma: float, threshold: float, settings: Settings
) -> pd.DataFrame:
    output = frame[
        [
            "game_id",
            "season",
            "week",
            "game_date",
            "home_team",
            "away_team",
            "actual_total",
            "market_total",
            "home_prior_games",
            "away_prior_games",
        ]
    ].copy()
    output["candidate"] = candidate
    output["projected_total"] = projection
    edge = output["projected_total"] - output["market_total"]
    output["model_edge_points"] = edge
    output["side"] = np.where(edge.ge(0), "over", "under")
    p_over = probability_over(projection, output["market_total"].to_numpy(float), sigma, settings.totals_student_df)
    output["cover_probability"] = np.where(edge.ge(0), p_over, 1.0 - p_over)
    output["ev_at_minus_110"] = output["cover_probability"] * (100.0 / 110.0) - (1.0 - output["cover_probability"])
    history_ok = output["home_prior_games"].fillna(0).ge(5) & output["away_prior_games"].fillna(0).ge(5)
    output["selected_threshold"] = threshold
    output["bet"] = edge.abs().ge(threshold) & output["ev_at_minus_110"].ge(settings.totals_min_ev) & history_ok
    won = np.where(
        output["side"].eq("over"),
        output["actual_total"].gt(output["market_total"]),
        output["actual_total"].lt(output["market_total"]),
    )
    push = output["actual_total"].eq(output["market_total"])
    output["profit_units"] = np.where(output["bet"], np.where(push, 0.0, np.where(won, 100 / 110, -1)), 0.0)
    return output


def summarize_public(frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate, group in frame.groupby("candidate"):
        bets = group.loc[group["bet"]]
        decided = bets.loc[bets["actual_total"].ne(bets["market_total"])]
        wins = np.where(
            decided["side"].eq("over"),
            decided["actual_total"].gt(decided["market_total"]),
            decided["actual_total"].lt(decided["market_total"]),
        )
        low, high = _bootstrap_roi(bets["profit_units"].to_numpy(float), (bets["season"].astype(str) + ":" + bets["week"].astype(str)).to_numpy())
        resolved = group.loc[group["actual_total"].ne(group["market_total"])].copy()
        labels = np.where(resolved["side"].eq("over"), resolved["actual_total"].gt(resolved["market_total"]), resolved["actual_total"].lt(resolved["market_total"]))
        probability = np.clip(resolved["cover_probability"].to_numpy(float), 1e-6, 1.0 - 1e-6)
        rows.append(
            {
                "candidate": candidate,
                "games": int(len(group)),
                "mae": float(np.mean(np.abs(group["actual_total"] - group["projected_total"]))),
                "rmse": float(np.sqrt(np.mean((group["actual_total"] - group["projected_total"]) ** 2))),
                "brier": float(np.mean((probability - labels) ** 2)),
                "log_loss": float(-np.mean(labels * np.log(probability) + (1.0 - labels) * np.log1p(-probability))),
                "market_neutral_brier": 0.25,
                "uncertainty_method": "season-week cluster bootstrap; 3000 resamples",
                "bets": int(len(bets)),
                "win_rate_decided": float(np.mean(wins)) if len(wins) else None,
                "flat_roi": float(bets["profit_units"].mean()) if len(bets) else None,
                "profit_units": float(bets["profit_units"].sum()),
                "roi_95_low": low,
                "roi_95_high": high,
            }
        )
    return rows


def walk_forward_public(frame: pd.DataFrame, settings: Settings) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    outputs: list[pd.DataFrame] = []
    audit: list[dict[str, Any]] = []
    seasons = sorted(int(value) for value in frame["season"].unique())
    for test_season in seasons[4:]:
        train = frame.loc[frame["season"].lt(test_season)]
        test = frame.loc[frame["season"].eq(test_season)]
        if len(train) < 300 or test.empty:
            continue
        base_oof = base_oof_predictions(train)
        if base_oof.empty:
            continue
        stacker = fit_convex_stacker(base_oof, base_oof["actual_residual"].to_numpy(float))
        nested = nested_meta_predictions(base_oof)
        threshold_audit = choose_edge_threshold(nested)
        ensemble_value = threshold_audit["selected"]["threshold"]
        ensemble_threshold = float(ensemble_value) if ensemble_value is not None else math.inf
        family_threshold_audit = {
            family: choose_edge_threshold(base_oof, family) for family in PUBLIC_FAMILIES
        }
        models = fit_family_models(train)
        base = predict_family_residuals(models, test)
        sigma = float(np.std(train["actual_total"] - train["market_total"], ddof=1))
        for family in PUBLIC_FAMILIES:
            projection = test["market_total"].to_numpy(float) + base[family].to_numpy(float)
            family_value = family_threshold_audit[family]["selected"]["threshold"]
            family_threshold = float(family_value) if family_value is not None else math.inf
            outputs.append(_score_candidate(test, f"public_{family}", projection, sigma, family_threshold, settings))
        ensemble_projection = test["market_total"].to_numpy(float) + stacked_residual(base, stacker)
        outputs.append(
            _score_candidate(
                test,
                "public_superensemble_v2",
                ensemble_projection,
                sigma,
                ensemble_threshold,
                settings,
            )
        )
        audit.append(
            {
                "test_season": test_season,
                "training_games": int(len(train)),
                "base_oof_rows": int(len(base_oof)),
                "nested_meta_rows": int(len(nested)),
                "stacker": stacker,
                "family_threshold_selection": family_threshold_audit,
                "threshold_selection": threshold_audit,
            }
        )
    return pd.concat(outputs, ignore_index=True), audit


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


def run_public_backtest(settings: Settings) -> dict[str, Any]:
    seasons = list(range(settings.historical_start_season, settings.season))
    base = historical_game_features(settings, seasons)
    frame = attach_public_features(base, settings, seasons)
    frame = frame.loc[
        frame["market_total"].notna() & frame["market_home_spread"].notna() & frame["actual_total"].notna()
    ].copy()
    predictions, audit = walk_forward_public(frame, settings)
    metrics = summarize_public(predictions)

    live_train = frame.loc[frame["season"].ge(settings.historical_full_coverage_start)]
    live_models = fit_family_models(live_train)
    all_oof = base_oof_predictions(frame)
    live_stacker = fit_convex_stacker(all_oof, all_oof["actual_residual"].to_numpy(float))
    nested = nested_meta_predictions(all_oof)
    threshold = choose_edge_threshold(nested)
    family_thresholds = {
        family: choose_edge_threshold(all_oof, family) for family in PUBLIC_FAMILIES
    }
    sigma = float(np.std(live_train["actual_total"] - live_train["market_total"], ddof=1))

    model_path = settings.models_dir / "public_superensemble_v2.joblib"
    _atomic_joblib(model_path, {"family_models": live_models, "stacker": live_stacker})
    artifact = {
        "artifact_version": "ncaaf-public-superensemble-audited-v3",
        "created_at": utc_now(),
        "status": "shadow_forward_research",
        "families": list(PUBLIC_FAMILIES),
        "candidates": list(PUBLIC_CANDIDATES),
        "training_seasons": sorted(int(value) for value in live_train["season"].unique()),
        "training_games": int(len(live_train)),
        "stacker": live_stacker,
        "threshold_selection": threshold,
        "family_threshold_selection": family_thresholds,
        "distribution_sigma": sigma,
        "model_path": str(model_path.relative_to(settings.root)),
        "point_in_time_rule": "Weekly statistics through W-1; FPI must have a valid publication timestamp before kickoff/as-of and no out-of-sequence flag.",
        "excluded_sources": ["Unverified annual returning production", "Unverified annual team talent", "Default-source synthetic odds"],
        "uncertainty_policy": "Season-week cluster uncertainty, one-sided Bonferroni 95% threshold gate across 30 choices, at least 100 bets and 8 weeks",
        "evaluation_status": "Retrospective development; previous holdout seasons have been inspected. No demonstrated executable-price edge.",
        "unavailable_source": "Historical point-in-time SP+ was not available from configured no-key sources.",
    }
    atomic_write_json(settings.models_dir / "public_superensemble_v2.json", artifact)
    predictions.to_parquet(settings.normalized_dir / "public_superensemble_v2_walk_forward.parquet", index=False)
    atomic_write_csv(settings.reports_dir / "public_superensemble_v2_by_season.csv", pd.DataFrame(audit))
    summary = {
        "artifact": artifact,
        "walk_forward_games": int(predictions["game_id"].nunique()),
        "test_seasons": sorted(int(value) for value in predictions["season"].unique()),
        "metrics": metrics,
        "outer_fold_audit": audit,
    }
    atomic_write_json(settings.reports_dir / "public_superensemble_v2_summary.json", summary)
    return summary


def load_live_public_projection(frame: pd.DataFrame, settings: Settings) -> tuple[pd.DataFrame, np.ndarray, dict[str, Any]]:
    artifact = joblib.load(settings.models_dir / "public_superensemble_v2.joblib")
    metadata = json.loads((settings.models_dir / "public_superensemble_v2.json").read_text(encoding="utf-8"))
    base = predict_family_residuals(artifact["family_models"], frame)
    projection = frame["market_total"].to_numpy(float) + stacked_residual(base, artifact["stacker"])
    return base, projection, metadata
