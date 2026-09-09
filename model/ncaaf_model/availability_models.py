"""Unfitted ACC availability probability experiment; numerical utilities only.

The caller must verify report, roster, participation and quote receipts before
constructing these inputs. These functions do not authorize collection, fit on
real observations, create forecasts retroactively, or establish a betting edge.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit

VERSION = "availability-probability-v1-unvalidated"
PENALTY, CLIP = 0.1, 5.0
MIN_TRAIN_GAMES, MIN_TRAIN_WEEKS = 20, 2
BASE_FEATURES = ("market_total", "log_hours_to_kickoff", "market_total_change",
                 "phase_gameday", "missing_previous_report", "missing_previous_quote")
AVAILABILITY_FEATURES = ("qb_out_burden", "qb_out_burden_change")
ALL_FEATURES = BASE_FEATURES + AVAILABILITY_FEATURES
BINARY_FEATURES = ("phase_gameday", "missing_previous_report", "missing_previous_quote")


class ModelFitError(RuntimeError):
    """An unavailable fit cannot supply fallback probability or paper entries."""


def _vector(values, name, size=None, *, binary=False):
    raw = np.asarray(values)
    if raw.ndim != 1 or not len(raw) or raw.dtype.kind not in ("biuf" if binary else "iuf"):
        raise ValueError(name + " must be a nonempty numeric vector")
    result = np.asarray(raw, dtype=float)
    if not np.isfinite(result).all() or (size is not None and len(result) != size):
        raise ValueError(name + " has nonfinite values or incompatible length")
    if binary and not np.isin(result, [0., 1.]).all():
        raise ValueError(name + " must be binary")
    return result


def _readonly(value):
    result = np.asarray(value, dtype=float).copy()
    result.setflags(write=False)
    return result


def _offset(q, size=None):
    q = _vector(q, "under-price reference", size)
    if not ((q > 0) & (q < 1)).all():
        raise ValueError("Under-price references must be strictly between zero and one")
    return np.log(q) - np.log1p(-q)


def _game_id(value):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (str, int, np.integer)):
        raise ValueError("Canonical positive game IDs required")
    text = str(value)
    if not text.isascii() or not text.isdigit() or text.startswith("0"):
        raise ValueError("Canonical positive game IDs required")
    return text


def game_weights(game_ids, phase_gameday):
    """One unit per game, split across its one or two distinct opportunities."""
    if game_ids is None or isinstance(game_ids, (str, bytes, Mapping)):
        raise ValueError("A sequence of game IDs is required")
    ids = [_game_id(g) for g in game_ids]
    phases = _vector(phase_gameday, "phase_gameday", len(ids), binary=True)
    keys = list(zip(ids, phases.tolist()))
    if len(set(keys)) != len(keys):
        raise ValueError("Duplicate initial/game-day opportunity")
    counts = Counter(ids)
    return np.asarray([1. / counts[g] for g in ids]), len(counts)


def _weights(values, size):
    weights = _vector(values, "game weights", size)
    if not (weights > 0).all() or not np.isfinite(weights.sum()):
        raise ValueError("Positive finite game weights required")
    return weights / weights.sum()


def _matrix(features, names):
    if not isinstance(features, Mapping) or set(features) - set(ALL_FEATURES) or set(names) - set(features):
        raise ValueError("Explicit prescribed feature names required")
    columns = [_vector(features[name], name, binary=name in BINARY_FEATURES) for name in names]
    if len({len(c) for c in columns}) != 1:
        raise ValueError("Feature lengths differ")
    return np.column_stack(columns)


def _contract(features, *, with_availability):
    names = ALL_FEATURES if with_availability else BASE_FEATURES
    _matrix(features, names)
    lines = _vector(features["market_total"], "market_total")
    if not ((lines > 0) & (lines % 1 == .5)).all():
        raise ValueError("Exact positive half-point totals are required")
    if not (_vector(features["log_hours_to_kickoff"], "log_hours_to_kickoff") >= 0).all():
        raise ValueError("log_hours_to_kickoff is log1p of nonnegative hours")
    missing_quote = _vector(features["missing_previous_quote"], "missing_previous_quote", binary=True)
    change = _vector(features["market_total_change"], "market_total_change")
    if not (change[missing_quote == 1] == 0).all():
        raise ValueError("Unknown preceding quotes require a zero change placeholder and missing indicator")
    if with_availability:
        burden = _vector(features["qb_out_burden"], "qb_out_burden")
        change = _vector(features["qb_out_burden_change"], "qb_out_burden_change")
        missing = _vector(features["missing_previous_report"], "missing_previous_report", binary=True)
        if not ((burden >= 0) & (burden <= 2)).all() or not ((change >= -2) & (change <= 2)).all():
            raise ValueError("Two-team attempt-share burden must be in [0,2] and its change in [-2,2]")
        if not (change[missing == 1] == 0).all():
            raise ValueError("Unknown prior burden requires a zero change placeholder and missing indicator")
        previous = burden[missing == 0] - change[missing == 0]
        if not ((previous >= -1e-12) & (previous <= 2 + 1e-12)).all():
            raise ValueError("Known previous burden is inconsistent with the current burden and change")


@dataclass(frozen=True)
class Standardizer:
    names: tuple[str, ...]
    mean: np.ndarray
    scale: np.ndarray

    @classmethod
    def fit(cls, features, weights, *, with_availability):
        if type(with_availability) is not bool:
            raise ValueError("with_availability must be Boolean")
        names = ALL_FEATURES if with_availability else BASE_FEATURES
        _contract(features, with_availability=with_availability)
        x = _matrix(features, names)
        w = _weights(weights, len(x))
        mean = w @ x
        constant = np.all(x == x[0], axis=0)
        mean = np.where(constant, x[0], mean)
        with np.errstate(over="ignore", invalid="ignore"):
            scale = np.sqrt(w @ ((x - mean) ** 2))
        scale = np.where(constant | (scale == 0), 1., scale)
        if not np.isfinite(mean).all() or not np.isfinite(scale).all():
            raise ValueError("Training transformation overflowed")
        return cls(names, _readonly(mean), _readonly(scale))

    def transform(self, features):
        _contract(features, with_availability=self.names == ALL_FEATURES)
        x = _matrix(features, self.names)
        with np.errstate(over="ignore"):
            return np.clip((x - self.mean) / self.scale, -CLIP, CLIP)


def weighted_log_loss(logits, labels, weights):
    eta = _vector(logits, "logits")
    y = _vector(labels, "labels", len(eta), binary=True)
    w = _weights(weights, len(eta))
    return float(w @ np.logaddexp(0., np.where(y == 1, -eta, eta)))


def offset_log_loss_gradient(coefficients, transformed_features, q_under, labels, weights):
    """Game-weighted mean log loss + .1/2 ||theta||², including intercept."""
    x = np.asarray(transformed_features, dtype=float)
    if x.ndim != 2 or not len(x) or not np.isfinite(x).all():
        raise ValueError("Finite two-dimensional transformed features required")
    theta = _vector(coefficients, "coefficients", x.shape[1] + 1)
    y = _vector(labels, "labels", len(x), binary=True)
    w, offset = _weights(weights, len(x)), _offset(q_under, len(x))
    design = np.column_stack([np.ones(len(x)), x])
    eta = offset + design @ theta
    objective = weighted_log_loss(eta, y, w) + PENALTY / 2 * float(theta @ theta)
    gradient = design.T @ (w * (expit(eta) - y)) + PENALTY * theta
    return objective, gradient


@dataclass(frozen=True)
class ProbabilityModel:
    standardizer: Standardizer
    coefficients: np.ndarray
    training_rows: int
    training_games: int
    optimizer_iterations: int

    def predict_logits(self, features, q_under):
        x = self.standardizer.transform(features)
        return _offset(q_under, len(x)) + self.coefficients[0] + x @ self.coefficients[1:]

    def predict_under(self, features, q_under):
        return expit(self.predict_logits(features, q_under))


def fit_probability(features, q_under, labels, game_ids, *, with_availability):
    """Prepared numerical arrays only; the runner must enforce source/time rules."""
    if not isinstance(features, Mapping) or "phase_gameday" not in features:
        raise ValueError("Named features including phase_gameday are required")
    weights, games = game_weights(game_ids, features["phase_gameday"])
    transform = Standardizer.fit(features, weights, with_availability=with_availability)
    x = transform.transform(features)
    y = _vector(labels, "labels", len(x), binary=True)
    _offset(q_under, len(x))
    try:
        fitted = minimize(offset_log_loss_gradient, np.zeros(x.shape[1] + 1),
            args=(x, q_under, y, weights), jac=True, method="L-BFGS-B",
            options={"maxiter": 1000, "gtol": 1e-9, "ftol": 1e-12, "maxls": 50})
        if not (fitted.success and np.isfinite(fitted.fun) and np.isfinite(fitted.x).all()
                and np.isfinite(fitted.jac).all() and np.max(np.abs(fitted.jac)) < 1e-5):
            raise ModelFitError("Probability optimizer did not converge to a finite solution")
    except (ValueError, FloatingPointError, OverflowError) as exc:
        raise ModelFitError("Availability probability fit is unavailable") from exc
    return ProbabilityModel(transform, _readonly(fitted.x), len(x), games, int(fitted.nit))


def _utc(value):
    try:
        value = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid chronological timestamp") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timezone-aware timestamps required")
    return value.astimezone(timezone.utc)


def _week(kickoff):
    day = kickoff.astimezone(ZoneInfo("America/New_York")).date()
    return day - timedelta(days=day.weekday())


def validate_chronological_split(training: Sequence[Mapping], testing: Sequence[Mapping], *, cutoff):
    """Metadata-only guard: two opportunities/game are grouped, never split.

    Records need game_id, phase, current observed kickoff, decision_at and the
    fixed group_kickoff from that game's first designation. Training records
    also need the final label's final_kickoff and target_available_at. The runner verifies sources/labels and
    artifact availability separately; this function cannot infer those facts.
    """
    as_of = _utc(cutoff)
    local = as_of.astimezone(ZoneInfo("America/New_York"))
    if local.weekday() != 0 or (local.hour, local.minute, local.second, local.microsecond) != (0, 0, 0, 0):
        raise ValueError("Fixed Monday 00:00 Eastern training cutoff required")
    if not training or not testing:
        raise ValueError("Both training and testing metadata are required")
    groups = []
    for rows, is_training in ((training, True), (testing, False)):
        ids, keys, anchors, weeks = set(), set(), {}, set()
        for row in rows:
            gid = _game_id(row["game_id"])
            phase = row["phase"]
            if phase not in ("initial", "gameday") or (gid, phase) in keys:
                raise ValueError("Unique initial/game-day opportunity per game required")
            kickoff, decision = _utc(row["kickoff"]), _utc(row["decision_at"])
            anchor = _utc(row["group_kickoff"])
            if gid in anchors and anchors[gid] != anchor:
                raise ValueError("One game's fixed week anchor changed")
            if decision >= kickoff:
                raise ValueError("Every designated decision must precede kickoff")
            if is_training:
                available = _utc(row["target_available_at"])
                final_kickoff = _utc(row["final_kickoff"])
                if not decision < final_kickoff < available < as_of or _week(anchor) + timedelta(days=7) > local.date():
                    raise ValueError("Training requires prior completed weeks and final-label receipts before cutoff")
            elif decision < as_of or kickoff < as_of:
                raise ValueError("Test decisions and kickoff must follow the training cutoff")
            ids.add(gid)
            keys.add((gid, phase))
            anchors[gid] = anchor
            weeks.add(_week(anchor))
        groups.append((ids, weeks))
    if groups[0][0] & groups[1][0]:
        raise ValueError("No game may appear in both training and testing")
    if len(groups[0][0]) < MIN_TRAIN_GAMES or len(groups[0][1]) < MIN_TRAIN_WEEKS:
        raise ValueError("Experimental fitting needs 20 distinct games across two completed weeks")
    return {"training_games": len(groups[0][0]), "training_weeks": len(groups[0][1]),
            "training_rows": len(training), "testing_games": len(groups[1][0]), "testing_rows": len(testing)}
