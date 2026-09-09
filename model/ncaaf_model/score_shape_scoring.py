"""Pure discrete-total scoring and paired week summaries; no fitting or I/O.

Every supplied PMF has strictly positive mass on integer scores 0..250.
Conditional Over scores exclude observed pushes; unconditional three-outcome
log loss is primary and retains every game. No probability clipping, alternate
line rounding, or metric-based row selection is performed.
"""
from __future__ import annotations

from numbers import Real

import numpy as np
import pandas as pd

SUPPORT = np.arange(251, dtype=float)
IDENTITY_COLUMNS = ("game_id", "season", "game_date", "market_source", "actual_total", "market_total")
METRICS = ("three_outcome_nll", "conditional_log_loss", "conditional_brier",
           "exact_score_nll", "discrete_crps", "absolute_mean_error")
PRIMARY_METRIC = METRICS[0]
PROBABILITY_COLUMNS = ("under_probability", "over_probability", "push_probability", "conditional_over")
RELIABILITY_EDGES = (0., .4, .45, .5, .55, .6, 1.)
BOOTSTRAP_DRAWS = 10_000
BOOTSTRAP_SEED = 20260909
PMF_SUM_TOLERANCE = 1e-12


def _numeric(series: pd.Series, name: str) -> np.ndarray:
    if any(isinstance(value, (bool, np.bool_)) or not isinstance(value, Real) for value in series):
        raise ValueError(f"{name} must contain real numeric values without coercion")
    values = series.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError(f"{name} must be finite")
    return values


def _utc_dates(series: pd.Series, name: str) -> pd.DatetimeIndex:
    values = []
    for value in series:
        try:
            stamp = pd.Timestamp(value)
            if pd.isna(stamp) or stamp.tzinfo is None or stamp.utcoffset() is None:
                raise ValueError("missing timezone")
            values.append(stamp.tz_convert("UTC"))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} requires an explicit timezone on every value") from exc
    return pd.DatetimeIndex(values, tz="UTC")


def _extra_identity(frame: pd.DataFrame) -> tuple[str, ...]:
    return tuple(sorted(name for name in frame.columns if name == "week" or "cutoff" in name.lower()))


def _validate_identity(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    if not isinstance(frame, pd.DataFrame) or frame.columns.has_duplicates:
        raise ValueError("A DataFrame with unique column names is required")
    if not all(isinstance(name, str) for name in frame.columns) or not set(IDENTITY_COLUMNS).issubset(frame):
        raise ValueError("Required identity columns are missing")
    ids = frame.game_id
    if any(isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer, str))
           or isinstance(value, str) and not value.strip() for value in ids):
        raise ValueError("game_id must contain exact nonempty string or integer identities")
    if ids.duplicated().any():
        raise ValueError("Duplicate game_id values are not permitted")
    season = _numeric(frame.season, "season")
    if np.any(season != np.floor(season)):
        raise ValueError("season must be integer-valued")
    if any(not isinstance(value, str) or not value.strip() for value in frame.market_source):
        raise ValueError("market_source must be an explicit nonempty label")
    _utc_dates(frame.game_date, "game_date")
    actual = _numeric(frame.actual_total, "actual_total")
    if np.any((actual < 0) | (actual > 250) | (actual != np.floor(actual))):
        raise ValueError("actual_total must be an integer from 0 through 250")
    line = _numeric(frame.market_total, "market_total")
    if np.any((line <= 0) | (np.remainder(line, .5) != 0)):
        raise ValueError("market_total must be a positive integer or half-point without rounding")
    for name in _extra_identity(frame):
        if frame[name].isna().any():
            raise ValueError(f"Identity column {name} cannot be missing")
        if name == "week":
            week = _numeric(frame[name], name)
            if np.any(week != np.floor(week)):
                raise ValueError("week must be integer-valued")
    return actual.astype(np.int64), line


def score_pmf(testDataFrame: pd.DataFrame, pmfs) -> pd.DataFrame:
    """Score row-aligned 251-bin PMFs, retaining exact identity and cutoff fields.

    Rows sum to one within absolute tolerance 1e-12. Accepted PMFs are used as
    supplied (never repaired or renormalized). DataFrame PMFs additionally need
    the identical row index and exact ordered integer score columns 0..250.
    """
    actual, line = _validate_identity(testDataFrame)
    if isinstance(pmfs, pd.DataFrame):
        if (not pmfs.index.equals(testDataFrame.index) or list(pmfs.columns) != list(range(251))
                or any(isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)) for value in pmfs.columns)):
            raise ValueError("PMF DataFrame index/support must exactly match input order and 0..250")
        supplied = pmfs.to_numpy()
    else:
        supplied = np.asarray(pmfs)
    if supplied.dtype.kind not in "fiu" or supplied.shape != (len(testDataFrame), 251):
        raise ValueError("PMFs require a numeric matrix with exactly 251 bins per game")
    probability = supplied.astype(float, copy=True)
    if not np.isfinite(probability).all() or np.any(probability <= 0):
        raise ValueError("Every PMF bin must be finite and strictly positive")
    if not np.allclose(probability.sum(axis=1), 1., atol=PMF_SUM_TOLERANCE, rtol=0):
        raise ValueError("PMF rows must sum to one within 1e-12; no normalization is applied")
    under = np.sum(probability * (SUPPORT[None, :] < line[:, None]), axis=1)
    over = np.sum(probability * (SUPPORT[None, :] > line[:, None]), axis=1)
    push = np.sum(probability * (SUPPORT[None, :] == line[:, None]), axis=1)
    nonpush = actual != line
    conditional_over = over / (under + over)
    actual_over = actual > line
    selected_probability = np.where(actual_over, over, np.where(actual < line, under, push))
    conditional_nll = np.full(len(actual), np.nan)
    conditional_brier = np.full(len(actual), np.nan)
    # Log differences avoid rounding 1 - conditional_over to zero in the tails.
    side_probability = np.where(actual_over[nonpush], over[nonpush], under[nonpush])
    conditional_nll[nonpush] = -np.log(side_probability) + np.log(under[nonpush] + over[nonpush])
    conditional_brier[nonpush] = (conditional_over[nonpush] - actual_over[nonpush])**2
    mean = probability @ SUPPORT
    variance = np.sum(probability * (SUPPORT[None, :] - mean[:, None])**2, axis=1)
    # Integer-support CRPS: CDF differences on boundaries 0..249. Beyond the
    # support both CDFs are identically one, so there is no extra 250th term.
    cdf = np.cumsum(probability, axis=1)[:, :-1]
    observed_cdf = SUPPORT[None, :-1] >= actual[:, None]
    output = testDataFrame[list(IDENTITY_COLUMNS) + list(_extra_identity(testDataFrame))].copy(deep=True)
    output["under_probability"], output["over_probability"], output["push_probability"] = under, over, push
    output["conditional_over"] = conditional_over
    output["three_outcome_nll"] = -np.log(selected_probability)
    output["conditional_log_loss"], output["conditional_brier"] = conditional_nll, conditional_brier
    output["exact_score_nll"] = -np.log(probability[np.arange(len(actual)), actual])
    output["discrete_crps"] = np.sum((cdf - observed_cdf)**2, axis=1)
    output["absolute_mean_error"] = abs(mean - actual)
    output["mean"], output["variance"] = mean, variance
    _validate_scored(output)
    return output


def _validate_scored(rows: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    actual, line = _validate_identity(rows)
    if not set((*METRICS, *PROBABILITY_COLUMNS, "mean", "variance")).issubset(rows):
        raise ValueError("Scored rows are missing required probability/metric columns")
    nonpush = actual != line
    for key in (*PROBABILITY_COLUMNS, "mean", "variance"):
        values = _numeric(rows[key], key)
        if np.any(values < 0) or key in PROBABILITY_COLUMNS and np.any(values > 1 + PMF_SUM_TOLERANCE):
            raise ValueError("Scored probabilities/moments are outside their valid range")
    under, over, push, conditional = (rows[key].to_numpy(float) for key in PROBABILITY_COLUMNS)
    if not np.allclose(under + over + push, 1., atol=PMF_SUM_TOLERANCE, rtol=0):
        raise ValueError("Over/Under/Push probabilities must conserve unit mass")
    if np.any(push[np.remainder(line, 1.) != 0] != 0) or np.any(under + over <= 0):
        raise ValueError("Push probability requires an integer line and nonpush mass must exist")
    if not np.allclose(conditional, over / (under + over), atol=1e-12, rtol=0):
        raise ValueError("Conditional Over probability disagrees with unconditional masses")
    for metric in METRICS:
        values = rows[metric].to_numpy(float)
        if metric.startswith("conditional_"):
            if not np.isnan(values[~nonpush]).all() or not np.isfinite(values[nonpush]).all():
                raise ValueError("Conditional metrics must be finite only for actual nonpush games")
        elif not np.isfinite(values).all():
            raise ValueError("Unconditional metrics must score every game")
        if np.any(values[np.isfinite(values)] < -PMF_SUM_TOLERANCE):
            raise ValueError("Loss metrics cannot be negative")
    return actual, line


def _weeks(rows: pd.DataFrame) -> np.ndarray:
    local = _utc_dates(rows.game_date, "game_date").tz_convert("America/New_York").tz_localize(None)
    return local.to_period("W-SUN").astype(str).to_numpy()


def summary_metrics(rows: pd.DataFrame) -> dict:
    """All-game losses and nonpush conditional scores; empty means remain None."""
    actual, line = _validate_scored(rows)
    nonpush = actual != line
    metrics, metric_games = {}, {}
    for name in METRICS:
        values = rows[name].to_numpy(float)
        valid = np.isfinite(values)
        metric_games[name] = int(valid.sum())
        metrics[name] = float(values[valid].mean()) if valid.any() else None
    predictions = rows.conditional_over.to_numpy(float)[nonpush]
    observed = (actual > line)[nonpush]
    reliability = []
    for i, (low, high) in enumerate(zip(RELIABILITY_EDGES[:-1], RELIABILITY_EDGES[1:])):
        last = i == len(RELIABILITY_EDGES) - 2
        selected = (predictions >= low) & ((predictions <= high) if last else (predictions < high))
        count = int(selected.sum())
        reliability.append({"lower": low, "upper": high, "upper_inclusive": last, "games": count,
                            "predicted_over": float(predictions[selected].mean()) if count else None,
                            "observed_over": float(observed[selected].mean()) if count else None})
    return {"games": len(rows), "metrics": metrics, "metric_games": metric_games,
            "posted_integer_lines": int(np.equal(line, np.floor(line)).sum()),
            "observed_pushes": int((~nonpush).sum()), "predicted_pushes": float(rows.push_probability.sum()),
            "reliability": reliability}


def paired_summary(candidateRows: pd.DataFrame, referenceRows: pd.DataFrame) -> dict:
    """Candidate-minus-reference losses after strict identity/cutoff alignment.

    Each metric resamples its contributing complete Eastern Monday–Sunday
    weeks. Conditional metrics omit actual pushes before week construction.
    Resetting PCG64 per metric pairs identical-cohort metrics on identical draws.
    Fewer than two contributing weeks produces no interval, including for a
    single game or an empty/only-push conditional cohort.
    """
    _validate_scored(candidateRows)
    _validate_scored(referenceRows)
    if len(candidateRows) != len(referenceRows) or set(candidateRows.game_id) != set(referenceRows.game_id):
        raise ValueError("Paired comparison requires exactly the same game IDs")
    if _extra_identity(candidateRows) != _extra_identity(referenceRows):
        raise ValueError("Paired cutoff/week identity columns differ")
    candidate = candidateRows.reset_index(drop=True)
    reference = referenceRows.set_index("game_id", drop=False).loc[candidate.game_id.tolist()].reset_index(drop=True)
    for name in (*IDENTITY_COLUMNS, *_extra_identity(candidate)):
        if name == "game_date":
            same = _utc_dates(candidate[name], name).equals(_utc_dates(reference[name], name))
        else:
            same = candidate[name].tolist() == reference[name].tolist()
        if not same:
            raise ValueError(f"Paired identity/outcome/line/cutoff mismatch: {name}")
    weeks = _weeks(candidate)
    metrics = {}
    for name in METRICS:
        first, second = candidate[name].to_numpy(float), reference[name].to_numpy(float)
        valid = np.isfinite(first) & np.isfinite(second)
        differences = first[valid] - second[valid]
        grouped = pd.DataFrame({"week": weeks[valid], "loss": differences, "games": np.ones(valid.sum(), dtype=int)}).groupby("week", sort=True).sum()
        entry = {"games": int(valid.sum()), "week_blocks": len(grouped),
                 "difference": float(differences.mean()) if len(differences) else None, "interval_95": None}
        if name == PRIMARY_METRIC:
            entry["interval_99"] = None
        if len(grouped) >= 2:
            values = grouped[["loss", "games"]].to_numpy(float)
            rng = np.random.Generator(np.random.PCG64(BOOTSTRAP_SEED))
            sampled = values[rng.integers(0, len(values), (BOOTSTRAP_DRAWS, len(values)))].sum(axis=1)
            bootstrapped = sampled[:, 0] / sampled[:, 1]
            entry["interval_95"] = np.quantile(bootstrapped, [.025, .975]).tolist()
            if name == PRIMARY_METRIC:
                entry["interval_99"] = np.quantile(bootstrapped, [.005, .995]).tolist()
        metrics[name] = entry
    return {"games": len(candidate), "draws": BOOTSTRAP_DRAWS, "seed": BOOTSTRAP_SEED,
            "difference_direction": "candidate_minus_reference", "metrics": metrics}
