#!/usr/bin/env python3
"""Independent calibration-run audit; imports no model/research module."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import ndtr

EXPECTED_PLAN = "bbe38aeaef6fa287da0f4b7d15548f604fa85980d90c8ef1e9e4154851884d32"
BASES = ("market_only", "opponent_adjusted_ridge")
METHODS = ("raw", "recalibrated", "conditional_variance")
METRICS = ("market_nll", "conditional_log_loss", "brier", "score_nll", "crps", "absolute_error")
FEATURES = ["adjusted_score_minus_market", "adjusted_drive_minus_market", "adjusted_clock_minus_market",
    "adjusted_pass_yards", "adjusted_rush_yards", "adjusted_pass_share", "market_total", "abs_spread", "week",
    "clock_rule_2023", "two_minute_rule_2024"]


def close(actual, expected, tol=1e-10):
    assert np.allclose(actual, expected, atol=tol, rtol=0, equal_nan=True), np.max(np.abs(np.asarray(actual) - np.asarray(expected)))


def mean_design(frame):
    return np.column_stack([np.ones(len(frame)), (frame.projected_total - frame.market_total) / 4,
        (frame.market_total - 55) / 10, (frame.abs_spread - 14) / 14])


def variance_design(frame):
    return np.column_stack([np.ones(len(frame)), (frame.market_total - 55) / 10, (frame.abs_spread - 14) / 14])


def independent_scores(frame):
    k = np.arange(251, dtype=float)
    mu, sigma = frame.mu.to_numpy(), frame.sigma.to_numpy()
    upper = ndtr((k[None, :] + .5 - mu[:, None]) / sigma[:, None])
    lower = ndtr((k[None, :] - .5 - mu[:, None]) / sigma[:, None])
    p = np.maximum(upper - lower, 1e-15)
    p[:, -1] += np.maximum(1 - upper[:, -1], 0)
    p /= p.sum(axis=1, keepdims=True)
    y, line = frame.actual_total.to_numpy(int), frame.market_total.to_numpy()
    over = (p * (k[None, :] > line[:, None])).sum(axis=1)
    under = (p * (k[None, :] < line[:, None])).sum(axis=1)
    push = (p * (k[None, :] == line[:, None])).sum(axis=1)
    close(over + under + push, 1.)
    event = np.select([y > line, y < line], [over, under], default=push)
    q = np.clip(over / (over + under), 1e-12, 1 - 1e-12)
    binary = y > line
    decided = y != line
    # CRPS via the independent energy identity, not the study's squared-CDF sum.
    cdf_before = np.cumsum(p, axis=1) - p
    first_moment_before = np.cumsum(p * k, axis=1) - p * k
    half_pair_distance = (p * (k * cdf_before - first_moment_before)).sum(axis=1)
    crps = (p * abs(k[None, :] - y[:, None])).sum(axis=1) - half_pair_distance
    result = frame[["configuration", "game_id", "season", "game_date", "market_source"]].copy()
    result["market_nll"] = -np.log(np.maximum(event, 1e-15))
    result["conditional_log_loss"] = np.where(decided, -np.where(binary, np.log(q), np.log1p(-q)), np.nan)
    result["brier"] = np.where(decided, (q - binary) ** 2, np.nan)
    result["score_nll"] = -np.log(np.maximum(p[np.arange(len(p)), y], 1e-15))
    result["crps"] = crps
    result["absolute_error"] = abs(y - p @ k)
    result["predicted_push"] = push
    return result


def interval(frame, value):
    dates = pd.to_datetime(frame.game_date, utc=True).dt.tz_convert("America/New_York")
    start = dates.dt.tz_localize(None).dt.normalize() - pd.to_timedelta(dates.dt.weekday, unit="D")
    grouped = pd.DataFrame({"week": start.to_numpy(), "value": np.asarray(value)}).groupby("week").value.agg(["sum", "count"]).to_numpy()
    rng = np.random.default_rng(20260909)
    draws = grouped[rng.integers(0, len(grouped), (10000, len(grouped)))].sum(axis=1)
    averages = draws[:, 0] / draws[:, 1]
    return {"weeks": len(grouped), "interval_95": np.quantile(averages, [.025, .975]).tolist(),
        "interval_eight": np.quantile(averages, [.05 / 16, 1 - .05 / 16]).tolist()}


def audit(root):
    plan = json.loads((root / "reports/calibration_research_plan.json").read_text())
    claimed = plan.pop("plan_sha256")
    assert claimed == EXPECTED_PLAN == hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    for path, expected in plan["input_sha256"].items():
        assert hashlib.sha256((root / path).read_bytes()).hexdigest() == expected
    report = json.loads((root / "reports/calibration_research_results.json").read_text())
    assert report["plan_sha256"] == EXPECTED_PLAN
    original = pd.read_csv(root / "reports/opponent_adjusted_predictions.csv")
    original = original.loc[original.candidate.isin(BASES)].copy()
    saved = pd.read_parquet(root / "data/normalized/calibration_research_predictions.parquet")
    assert set(original.season) == {2021, 2022, 2023, 2024, 2025}
    assert set(saved.season) == {2022, 2023, 2024, 2025}
    assert not original.duplicated(["candidate", "game_id"]).any()
    assert not saved.duplicated(["configuration", "game_id"]).any()
    assert (pd.to_datetime(original.ratings_cutoff, utc=True) < pd.to_datetime(original.game_date, utc=True)).all()
    features = pd.read_parquet(root / "data/normalized/opponent_features_verified_cf764f803b38ecfd.parquet")
    assert features.season.max() <= 2025
    base_checks = []
    for year in range(2021, 2026):
        prior = features.loc[features.season < year]
        train = prior.loc[prior.actual_total.notna() & prior.market_total.notna() & prior.adjusted_history_games.ge(5)]
        test = features.loc[features.season.eq(year) & features.adjusted_history_games.ge(5)].sort_values("game_id")
        x = train[FEATURES].to_numpy(float)
        med = np.nanmedian(x, axis=0)
        x = np.where(np.isfinite(x), x, med)
        center, scale = x.mean(axis=0), np.maximum(x.std(axis=0), 1.)
        matrix = np.column_stack([np.ones(len(x)), (x - center) / scale])
        coef = np.linalg.solve(matrix.T @ matrix + 200 * np.eye(matrix.shape[1]), matrix.T @ (train.actual_total - train.market_total))
        xt = np.where(np.isfinite(test[FEATURES]), test[FEATURES], med)
        predicted = test.market_total.to_numpy() + np.clip(np.column_stack([np.ones(len(test)), (xt - center) / scale]) @ coef, -10, 10)
        sigma = np.sqrt(np.mean((prior.actual_total - prior.market_total) ** 2))
        for base in BASES:
            historical = original.loc[original.candidate.eq(base) & original.season.eq(year)].sort_values("game_id")
            assert historical.game_id.tolist() == test.game_id.tolist()
            mu = test.market_total if base == "market_only" else predicted
            close(historical.projected_total, mu)
            close(historical.residual_sigma, sigma)
        base_checks.append({"year": year, "training_max_season": int(train.season.max()), "test_games": len(test)})
    fit_checks = []
    for fitted in report["fits"]:
        base, year = fitted["base"], fitted["test_year"]
        prior = original.loc[original.candidate.eq(base) & original.season.lt(year)].sort_values(["season", "game_date", "game_id"])
        test = original.loc[original.candidate.eq(base) & original.season.eq(year)].sort_values("game_id")
        assert fitted["training_rows"] == len(prior) and fitted["training_max_season"] == prior.season.max() < year
        x = mean_design(prior)
        errors = (prior.actual_total - prior.projected_total).to_numpy()
        beta = np.linalg.solve(x.T @ x + 200 * np.eye(4), x.T @ errors)
        r = errors - np.clip(x @ beta, -4, 4)
        v0 = np.clip(np.mean(r ** 2), 100, 576)
        close(beta, fitted["beta"])
        close(v0, fitted["variance"])
        gamma = np.array(fitted["gamma"])
        assert np.max(np.abs(gamma)) <= 1
        z = variance_design(prior)
        def objective(g):
            v = np.clip(v0 * np.exp(z @ g), 100, 576)
            return .5 * np.sum(np.log(v) + r ** 2 / v) + 100 * (g @ g)
        verified = minimize(objective, np.zeros(3), method="L-BFGS-B", bounds=[(-1, 1)] * 3,
                            options={"ftol": 1e-13, "gtol": 1e-8, "maxiter": 500})
        gap = objective(gamma) - verified.fun
        if fitted["optimizer_success"]:
            assert gap < 1e-5, gap
        else:
            close(gamma, np.zeros(3))
        centers = []
        for method in METHODS:
            f = saved.loc[saved.configuration.eq(base + ":" + method) & saved.season.eq(year)].sort_values("game_id")
            assert f.game_id.tolist() == test.game_id.tolist()
            close(f.actual_total, test.actual_total)
            close(f.market_total, test.market_total)
            mu = test.projected_total.to_numpy()
            sigma = np.clip(test.residual_sigma.to_numpy(), 6, 30)
            if method != "raw":
                mu = mu + np.clip(mean_design(test) @ beta, -4, 4)
                variance = np.full(len(test), v0)
                if method == "conditional_variance":
                    variance = np.clip(variance * np.exp(variance_design(test) @ gamma), 100, 576)
                sigma = np.sqrt(variance)
                centers.append(f.mu.to_numpy())
            close(f.mu, mu)
            close(f.sigma, sigma)
        close(centers[0], centers[1])
        fit_checks.append({"base": base, "year": year, "prior_rows": len(prior), "training_max_season": int(prior.season.max()),
            "optimizer_success": fitted["optimizer_success"], "independent_objective_gap": float(gap)})
    checked = independent_scores(saved)
    errors = {}
    for metric in METRICS:
        close(checked[metric], saved[metric], 1e-10)
        errors[metric] = float(np.nanmax(abs(checked[metric] - saved[metric])))
    periods = {"selection_2022_2024": checked.season.lt(2025), "reused_2025": checked.season.eq(2025)}
    order = [base + ":" + method for method in METHODS for base in BASES]
    means = checked.loc[checked.season.lt(2025)].groupby("configuration").market_nll.mean()
    selected = min(order, key=lambda name: means[name])
    assert selected == report["selected_configuration"]
    for period, mask in periods.items():
        for name, f in checked.loc[mask].groupby("configuration"):
            target = report["periods"][period][name]
            assert len(f) == target["games"]
            for metric in METRICS:
                close(f[metric].mean(), target[metric])
            close(f.predicted_push.sum(), target["predicted_pushes"])
    pairs = [(base + ":recalibrated", base + ":raw") for base in BASES]
    pairs += [(base + ":conditional_variance", base + ":recalibrated") for base in BASES]
    pairs += [(BASES[1] + ":" + method, BASES[0] + ":" + method) for method in METHODS]
    pairs += [(BASES[1] + ":conditional_variance", BASES[1] + ":raw")]
    contrasts = []
    for period, mask in periods.items():
        recorded = [c for c in report["paired_contrasts"] if c["period"] == period]
        assert {(c["candidate"], c["comparator"]) for c in recorded} == set(pairs) and len(recorded) == 8
        for c in recorded:
            one = checked.loc[mask & checked.configuration.eq(c["candidate"])].set_index("game_id").sort_index()
            other = checked.loc[mask & checked.configuration.eq(c["comparator"])].set_index("game_id").sort_index()
            assert one.index.equals(other.index)
            for metric in METRICS:
                close((one[metric] - other[metric]).mean(), c["differences"][metric])
            delta = one.market_nll - other.market_nll
            uncertainty = interval(one, delta)
            close(uncertainty["interval_95"], c["market_nll_delta_week_interval_95"])
            close(uncertainty["interval_eight"], c["market_nll_delta_eight_comparison_interval"])
            contrasts.append({"period": period, "candidate": c["candidate"], "comparator": c["comparator"],
                "mean_nll_delta": float(delta.mean()), **uncertainty})
    result = {"status": "passed", "audited_at": datetime.now(timezone.utc).isoformat(), "plan_sha256": EXPECTED_PLAN,
        "independent_implementation": "No project model/research module imported; earlier-season ridge fits, calibration equations, PMF scoring, energy-identity CRPS, selection and paired bootstrap recomputed.",
        "prediction_rows_checked": len(saved), "base_oof_folds_checked": base_checks, "calibration_fits_checked": fit_checks,
        "metric_max_absolute_errors": errors, "selected_configuration": selected, "paired_contrasts": contrasts,
        "no_2026_data": True, "active_model_modified": False}
    (root / "reports/calibration_research_audit.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({k: result[k] for k in ["status", "prediction_rows_checked", "metric_max_absolute_errors", "selected_configuration"]}, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1] / "model")
    audit(parser.parse_args().root)
