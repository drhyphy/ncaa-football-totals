"""Frozen probability recalibration on repaired, chronological OOF forecasts.

Research only: never writes active artifacts, forward ledgers, or live policy.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version as package_version
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from .conditional_distribution import SCORES, normal_pmf, market_probabilities
from .opponent_model import fit_artifact, projections

VERSION = "repaired-oof-calibration-v1"
BASES = ("market_only", "opponent_adjusted_ridge")
METHODS = ("raw", "recalibrated", "conditional_variance")
TEST_YEARS = (2022, 2023, 2024, 2025)
METRICS = ("market_nll", "conditional_log_loss", "brier", "score_nll", "crps", "absolute_error")
CONFIGURATION_ORDER = [base+":"+method for method in METHODS for base in BASES]
FEATURE_CACHE = "data/normalized/opponent_features_verified_cf764f803b38ecfd.parquet"
PARAMETERS = {"ridge_penalty": 200., "mean_cap": 4., "variance_bounds": [100., 576.],
              "gamma_bounds": [-1., 1.], "minimum_calibration_rows": 100,
              "selection_years": [2022, 2023, 2024], "bootstrap_draws": 10000,
              "reliability_bins": [0., .4, .45, .5, .55, .6, 1.]}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def safe(value):
    if isinstance(value, dict):
        return {str(k): safe(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [safe(v) for v in value]
    if isinstance(value, np.ndarray):
        return safe(value.tolist())
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    return value


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(safe(value), indent=2, allow_nan=False) + "\n")


def load_oof(root):
    frame = pd.read_csv(root / "reports/opponent_adjusted_predictions.csv")
    frame = frame.loc[frame.candidate.isin(BASES)].copy()
    if set(frame.candidate) != set(BASES) or set(frame.season) != set(range(2021, 2026)):
        raise ValueError("Expected repaired 2021–2025 OOF predictions only")
    if not frame.source_verified_pregame.eq(True).all() or not frame.adjusted_history_games.ge(5).all():
        raise ValueError("Unverified market role or insufficient prior history")
    if frame.duplicated(["candidate", "game_id"]).any():
        raise ValueError("Duplicate OOF identity")
    if not (pd.to_datetime(frame.ratings_cutoff, utc=True) < pd.to_datetime(frame.game_date, utc=True)).all():
        raise ValueError("Invalid pregame ratings cutoff")
    columns = ["season", "week", "game_date", "actual_total", "market_total", "abs_spread", "residual_sigma"]
    pair = [frame.loc[frame.candidate.eq(base)].set_index("game_id").sort_index() for base in BASES]
    if not pair[0][columns].equals(pair[1][columns]):
        raise ValueError("Baselines do not share identical games, prices, outcomes and fold scales")
    if pair[0].groupby("season").size().to_dict() != {2021:734, 2022:734, 2023:795, 2024:798, 2025:852}:
        raise ValueError("Frozen OOF game coverage changed")
    if not np.isfinite(frame[["projected_total", "actual_total", "market_total", "abs_spread", "residual_sigma"]]).all().all():
        raise ValueError("Nonfinite OOF input")
    if not frame.actual_total.between(0, 250).all() or not frame.actual_total.mod(1).eq(0).all():
        raise ValueError("Invalid observed score support")
    return frame.sort_values(["candidate", "season", "game_date", "game_id"]).reset_index(drop=True)


def verify_oof(root, frame):
    features = pd.read_parquet(root/FEATURE_CACHE)
    differences = []
    for year in range(2021, 2026):
        train = features.loc[features.season.lt(year)]
        test = features.loc[features.season.eq(year) & features.adjusted_history_games.ge(5)].sort_values("game_id")
        artifact = fit_artifact(train)
        if max(artifact["training_seasons"]) >= year:
            raise ValueError("Base model chronology failed")
        predicted = projections(test, artifact)
        sigma = float(np.sqrt(np.mean((train.actual_total-train.market_total)**2)))
        for base in BASES:
            saved = frame.loc[frame.candidate.eq(base) & frame.season.eq(year)].sort_values("game_id")
            if not np.array_equal(saved.game_id, test.game_id):
                raise ValueError("OOF reconstruction game IDs differ")
            mu = test.market_total.to_numpy() if base == "market_only" else predicted[base]
            if not np.allclose(saved.projected_total, mu, atol=1e-10, rtol=0) or not np.allclose(saved.residual_sigma, sigma, atol=1e-10, rtol=0):
                raise ValueError("Saved OOF means or scales differ from strict earlier-season reconstruction")
            differences.append({"base":base, "year":year, "max_mean_difference":float(np.max(np.abs(saved.projected_total-mu))),
                                "training_seasons":artifact["training_seasons"]})
    return differences


def design(frame):
    return np.column_stack([np.ones(len(frame)), (frame.projected_total-frame.market_total)/4,
                            (frame.market_total-55)/10, (frame.abs_spread-14)/14])


def variance_design(frame):
    return np.column_stack([np.ones(len(frame)), (frame.market_total-55)/10, (frame.abs_spread-14)/14])


@dataclass
class Recalibration:
    beta: np.ndarray
    variance: float
    gamma: np.ndarray
    training_max_season: int
    training_rows: int
    optimizer_success: bool
    optimizer_message: str

    @classmethod
    def fit(cls, train):
        if len(train) < PARAMETERS["minimum_calibration_rows"]:
            raise ValueError("Insufficient earlier OOF calibration rows")
        x = design(train)
        residual = (train.actual_total-train.projected_total).to_numpy(float)
        beta = np.linalg.solve(x.T@x + PARAMETERS["ridge_penalty"]*np.eye(x.shape[1]), x.T@residual)
        errors = residual - np.clip(x@beta, -PARAMETERS["mean_cap"], PARAMETERS["mean_cap"])
        variance = float(np.clip(np.mean(errors**2), *PARAMETERS["variance_bounds"]))
        z = variance_design(train)
        def objective(gamma):
            values = np.clip(variance*np.exp(z@gamma), *PARAMETERS["variance_bounds"])
            return .5*np.sum(np.log(values)+errors**2/values) + .5*PARAMETERS["ridge_penalty"]*np.sum(gamma**2)
        try:
            fitted = minimize(objective, np.zeros(z.shape[1]), method="L-BFGS-B",
                              bounds=[tuple(PARAMETERS["gamma_bounds"])]*z.shape[1])
            success = bool(fitted.success and np.isfinite(fitted.fun) and np.isfinite(fitted.x).all())
            gamma, message = fitted.x if success else np.zeros(z.shape[1]), str(fitted.message)
        except (ValueError, RuntimeError, FloatingPointError, OverflowError) as exc:
            success, gamma, message = False, np.zeros(z.shape[1]), type(exc).__name__+": "+str(exc)
        return cls(beta, variance, gamma, int(train.season.max()), len(train), success, message)

    def parameters(self, test, method):
        if method not in METHODS:
            raise ValueError("Unknown fixed method")
        if len(test) and int(test.season.min()) <= self.training_max_season:
            raise ValueError("Evaluation season must follow every calibration season")
        mu = test.projected_total.to_numpy(float)
        if method == "raw":
            return mu, np.clip(test.residual_sigma.to_numpy(float), 6., 30.)
        mu = mu + np.clip(design(test)@self.beta, -PARAMETERS["mean_cap"], PARAMETERS["mean_cap"])
        variance = np.full(len(test), self.variance)
        if method == "conditional_variance":
            variance = np.clip(variance*np.exp(variance_design(test)@self.gamma), *PARAMETERS["variance_bounds"])
        return mu, np.sqrt(variance)


def score(test, mu, sigma):
    pmf = normal_pmf(mu, sigma)
    actual, lines = test.actual_total.to_numpy(int), test.market_total.to_numpy(float)
    over, under, push = market_probabilities(pmf, lines)
    decided = actual != lines
    conditional = np.clip(over / (over+under), 1e-12, 1-1e-12)
    observed_over = actual > lines
    outcome_probability = np.where(observed_over, over, np.where(actual < lines, under, push))
    conditional_loss = -(observed_over*np.log(conditional)+(~observed_over)*np.log1p(-conditional))
    crps = np.sum((np.cumsum(pmf, axis=1)-(SCORES[None, :] >= actual[:, None]))**2, axis=1)
    frame = test[["game_id", "season", "week", "game_date", "actual_total", "market_total", "market_source"]].copy()
    frame["market_nll"] = -np.log(np.maximum(outcome_probability, 1e-15))
    frame["conditional_log_loss"] = np.where(decided, conditional_loss, np.nan)
    frame["brier"] = np.where(decided, (conditional-observed_over)**2, np.nan)
    frame["score_nll"] = -np.log(np.maximum(pmf[np.arange(len(test)), actual], 1e-15))
    frame["crps"], frame["absolute_error"] = crps, np.abs(actual-pmf@SCORES)
    frame["predicted_over"], frame["predicted_push"], frame["observed_push"] = conditional, push, ~decided
    frame["mu"], frame["sigma"] = mu, sigma
    return frame


def week_blocks(frame):
    dates = pd.to_datetime(frame.game_date, utc=True).dt.tz_convert("America/New_York").dt.tz_localize(None).dt.normalize()
    return (dates-pd.to_timedelta(dates.dt.weekday, unit="D")).dt.strftime("%Y-%m-%d")


def paired_interval(frame, values, alpha=.05):
    valid = np.isfinite(values)
    blocks = pd.DataFrame({"week": week_blocks(frame).to_numpy()[valid], "value": np.asarray(values)[valid]})
    groups = blocks.groupby("week").value.agg(["sum", "count"]).to_numpy()
    if len(groups) < 8:
        return None
    rng = np.random.default_rng(20260909)
    draws = groups[rng.integers(0, len(groups), (PARAMETERS["bootstrap_draws"], len(groups)))].sum(axis=1)
    return np.quantile(draws[:, 0]/draws[:, 1], [alpha/2, 1-alpha/2]).tolist()


def metrics(frame):
    return {"games": len(frame), **{m: float(frame[m].mean()) for m in METRICS},
            "posted_integer_lines": int(frame.market_total.mod(1).eq(0).sum()),
            "predicted_pushes": float(frame.predicted_push.sum()), "observed_pushes": int(frame.observed_push.sum())}


def make_plan(root):
    path = root / "reports/calibration_research_plan.json"
    if path.exists():
        raise ValueError("Frozen calibration plan already exists")
    inputs = ["reports/opponent_adjusted_predictions.csv", "reports/opponent_adjusted_development.json",
              "reports/CALIBRATION_RESEARCH_PLAN.md", "ncaaf_model/calibration_research.py",
              "ncaaf_model/conditional_distribution.py", "ncaaf_model/opponent_model.py", FEATURE_CACHE]
    frame = load_oof(root)
    oof_verification = verify_oof(root, frame)
    plan = {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "input_sha256": {name: digest(root/name) for name in inputs}, "parameters": PARAMETERS,
            "bases": BASES, "methods": METHODS, "test_years": TEST_YEARS,
            "library_versions":{name:package_version(name) for name in ("numpy","pandas","scipy","scikit-learn")},
            "original_oof_reconstruction": oof_verification,
            "games_by_year": frame.loc[frame.candidate.eq(BASES[0])].groupby("season").size().to_dict(),
            "criterion": "Minimum pooled three-outcome market log loss on2022–24;2025reported separately as reused development. No ROI selection or parameter search.",
            "status": "reused_historical_development_not_prospective_or_executable_evidence"}
    plan = safe(plan)
    plan["plan_sha256"] = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    write(path, plan)
    print(json.dumps(plan, indent=2))
    return plan


def run(root):
    plan = json.loads((root/"reports/calibration_research_plan.json").read_text())
    claimed = plan.pop("plan_sha256")
    if hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest() != claimed:
        raise ValueError("Calibration plan changed")
    if any(digest(root/name) != expected for name, expected in plan["input_sha256"].items()):
        raise ValueError("Frozen calibration source changed")
    frame, rows, fits = load_oof(root), [], []
    for base in BASES:
        source = frame.loc[frame.candidate.eq(base)]
        for year in TEST_YEARS:
            train, test = source.loc[source.season.lt(year)], source.loc[source.season.eq(year)]
            fitted = Recalibration.fit(train)
            fits.append({"base": base, "test_year": year, "training_max_season": fitted.training_max_season,
                         "training_rows": fitted.training_rows, "beta": fitted.beta, "variance": fitted.variance, "gamma": fitted.gamma,
                         "optimizer_success":fitted.optimizer_success, "optimizer_message":fitted.optimizer_message,
                         "gamma_boundary_hits":int(np.isclose(np.abs(fitted.gamma), 1.).sum())})
            for method in METHODS:
                scored = score(test, *fitted.parameters(test, method))
                scored["configuration"] = base+":"+method
                rows.append(scored)
    predictions = pd.concat(rows, ignore_index=True)
    periods = {"selection_2022_2024": predictions.season.lt(2025), "reused_2025": predictions.season.eq(2025)}
    summaries, contrasts, reliability, source_contrasts, annual_contrasts = {}, [], [], [], []
    for period, mask in periods.items():
        part = predictions.loc[mask]
        summaries[period] = {name: metrics(group) for name, group in part.groupby("configuration")}
        if period == "selection_2022_2024":
            selected = min(CONFIGURATION_ORDER, key=lambda key: summaries[period][key]["market_nll"])
        pairs = [(base+":recalibrated", base+":raw") for base in BASES]
        pairs += [(base+":conditional_variance", base+":recalibrated") for base in BASES]
        pairs += [(BASES[1]+":"+method, BASES[0]+":"+method) for method in METHODS]
        pairs += [(BASES[1]+":conditional_variance", BASES[1]+":raw")]
        for candidate, comparator in pairs:
            one = part.loc[part.configuration.eq(candidate)].set_index("game_id").sort_index()
            other = part.loc[part.configuration.eq(comparator)].set_index("game_id").sort_index()
            if not one.index.equals(other.index):
                raise ValueError("Unpaired evaluation comparison")
            differences = {m: float((one[m]-other[m]).mean()) for m in METRICS}
            delta = (one.market_nll-other.market_nll).to_numpy()
            contrasts.append({"period": period, "candidate": candidate, "comparator": comparator, "differences": differences,
                "market_nll_delta_week_interval_95": paired_interval(one, delta),
                "market_nll_delta_eight_comparison_interval": paired_interval(one, delta, .05/8)})
            for source_name, subset in one.groupby("market_source"):
                source_contrasts.append({"period":period,"candidate":candidate,"comparator":comparator,
                    "source":source_name,"games":len(subset),
                    "differences":{m:float((subset[m]-other.loc[subset.index,m]).mean()) for m in METRICS}})
            for year, subset in one.groupby("season"):
                annual_contrasts.append({"candidate":candidate,"comparator":comparator,"season":int(year),"games":len(subset),
                    "differences":{m:float((subset[m]-other.loc[subset.index,m]).mean()) for m in METRICS}})
        for name, group in part.groupby("configuration"):
            group = group.loc[~group.observed_push]
            for bucket, sample in group.groupby(pd.cut(group.predicted_over, PARAMETERS["reliability_bins"], include_lowest=True), observed=True):
                reliability.append({"period":period, "configuration":name, "bin":str(bucket), "games":len(sample),
                                    "predicted":float(sample.predicted_over.mean()), "observed":float((sample.actual_total>sample.market_total).mean())})
    report = {"version": VERSION, "plan_sha256": claimed, "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "status": "reused_historical_development_only", "selected_configuration": selected,
        "selection_rule": plan["criterion"], "parameters": PARAMETERS, "periods": summaries, "paired_contrasts": contrasts,
        "library_versions":plan["library_versions"],"source_contrasts":source_contrasts,"annual_contrasts":annual_contrasts,
        "by_season": [{"configuration": name, "season": int(year), **metrics(group)} for (name, year), group in predictions.groupby(["configuration", "season"])],
        "by_source": [{"configuration":name,"source":source,"season":int(year), **metrics(group)} for (name,source,year),group in predictions.groupby(["configuration","market_source","season"])],
        "reliability": reliability,
        "fits": fits, "credible_new_betting_edge": False,
        "limitations": ["All2021–25outcomes were previously used in research; chronological fitting does not make this a pristine holdout.",
            "Verified pregame-provider role does not certify exact entry time or prices; no betting returns or EV-based selections are calculated.",
            "2024and2025have no posted integer lines in this common evaluation sample; their posted-push calibration is untested.",
            "Raw comparators use saved fold means/scales in the same nonnegative discrete-normal family; this is not a byte-for-byte runtime replay.",
            "Eight-comparison intervals are descriptive on reused data and do not account for the entire earlier research search.",
            "The inherited absolute-spread feature maps a missing original spread to zero; no new missing-data treatment is introduced.",
            "No2026outcomes used; no active model artifact, registered forward policy or ledger changed."]}
    write(root/"reports/calibration_research_results.json", report)
    output = root/"data/normalized/calibration_research_predictions.parquet"
    output.parent.mkdir(parents=True, exist_ok=True)
    predictions.to_parquet(output, index=False)
    lines = ["# Repaired-data probability calibration experiment", "", "This is chronological research on reused data, not prospective profitability evidence.", "",
             "| Period | Configuration | Games | Market log loss | Conditional log loss | Brier | CRPS |", "|---|---|---:|---:|---:|---:|---:|"]
    for period, values in summaries.items():
        for name, m in values.items():
            lines.append(f"| {period} | {name} | {m['games']} | {m['market_nll']:.6f} | {m['conditional_log_loss']:.6f} | {m['brier']:.6f} | {m['crps']:.4f} |")
    lines += ["", f"The fixed2022–24selection rule chooses **{selected}**. All2025configurations remain visible in the JSON report. Lower loss is better.", "",
              "All paired comparisons, reliability bins and source-specific metrics are in the machine-readable report. No returns or EV-based selections were calculated.", "", "## Limitations", "", *["- "+s for s in report["limitations"]]]
    (root/"reports/calibration_research_results.md").write_text("\n".join(lines)+"\n")
    print(json.dumps(safe({"selected": selected, "periods": summaries}), indent=2), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    stage = parser.add_mutually_exclusive_group(required=True)
    stage.add_argument("--plan", action="store_true")
    stage.add_argument("--evaluate", action="store_true")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    make_plan(args.root) if args.plan else run(args.root)


if __name__ == "__main__":
    main()
