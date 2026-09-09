"""Unvalidated weather-revision candidates: pure numerical utilities only.

Implements WEATHER_REVISION_MODEL_DESIGN.md, without ingestion, real-data fits,
policy publication, staking, or selection. The low-level fitters accept prepared
numbers; a research runner MUST validate chronological metadata and the frozen
observation contract before fitting. No fitted parameter proves positive EV.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit

VERSION = 'weather-revision-models-v1-unvalidated'
PENALTY = 0.1
CLIP = 5.0
BASE_FEATURES = ('market_total', 'hours_to_kickoff', 'wind_mph', 'temperature_f',
                 'relative_humidity_percent', 'market_total_change',
                 'price_balance_logit_change', 'peer_total_difference')
REVISION_FEATURES = ('wind_revision_mph', 'temperature_revision_f', 'humidity_revision_percent')
ALL_FEATURES = BASE_FEATURES + REVISION_FEATURES
MIN_TRAIN_GAMES, MIN_TRAIN_WEEKS = 60, 2


class ModelFitError(RuntimeError):
    """A failed fit is unavailable; it must not generate fallback paper bets."""


def _vector(values, name, size=None):
    raw = np.asarray(values)
    if raw.ndim != 1 or raw.dtype.kind not in 'iuf' or not len(raw):
        raise ValueError(name + ' must be a nonempty numeric vector')
    result = np.asarray(raw, dtype=float)
    if not np.isfinite(result).all() or (size is not None and len(result) != size):
        raise ValueError(name + ' has missing/nonfinite values or incompatible length')
    return result


def _labels(values, size):
    # Boolean labels are valid; Boolean continuous features are not.
    values = np.asarray(values)
    if values.dtype.kind == 'b':
        values = values.astype(float)
    y = _vector(values, 'binary labels', size)
    if not np.isin(y, [0., 1.]).all():
        raise ValueError('Labels must be binary; push outcomes are not supported')
    return y


def _offset(q_under, size=None):
    q = _vector(q_under, 'under-price reference', size)
    if not ((q > 0) & (q < 1)).all():
        raise ValueError('Under-price reference must lie strictly between zero and one')
    return np.log(q) - np.log1p(-q)


def _matrix(features, names):
    if not isinstance(features, Mapping):
        raise ValueError('Features must be an explicit named mapping')
    if set(features) - set(ALL_FEATURES) or set(names) - set(features):
        raise ValueError('Missing prescribed features or unexpected feature names')
    columns = [_vector(features[name], name) for name in names]
    if len({len(c) for c in columns}) != 1:
        raise ValueError('Feature lengths differ')
    return np.column_stack(columns)


def _readonly(values):
    result = np.asarray(values, dtype=float).copy()
    result.setflags(write=False)
    return result


def _half_point_contract(features):
    lines = _vector(features['market_total'], 'reference lines')
    if not ((lines > 0) & (lines % 1 == .5)).all():
        raise ValueError('Probability candidate requires half-point reference lines; pushes are unavailable')


@dataclass(frozen=True)
class Standardizer:
    names: tuple[str, ...]
    mean: np.ndarray
    scale: np.ndarray

    @classmethod
    def fit(cls, features, *, with_revisions):
        if type(with_revisions) is not bool:
            raise ValueError('with_revisions must be Boolean')
        names = ALL_FEATURES if with_revisions else BASE_FEATURES
        x = _matrix(features, names)
        mean, scale = x.mean(axis=0), x.std(axis=0, ddof=0)
        scale = np.where(scale == 0, 1., scale)
        if not np.isfinite(mean).all() or not np.isfinite(scale).all():
            raise ValueError('Training transform overflowed')
        return cls(names, _readonly(mean), _readonly(scale))

    def transform(self, features):
        x = _matrix(features, self.names)
        with np.errstate(over='ignore'):
            return np.clip((x - self.mean) / self.scale, -CLIP, CLIP)


def binary_log_loss_from_logits(logits, labels):
    eta = _vector(logits, 'logits')
    y = _labels(labels, len(eta))
    return float(np.mean(np.logaddexp(0., np.where(y == 1, -eta, eta))))


def offset_log_loss_gradient(coefficients, transformed_features, q_under, labels):
    """Mean log loss +0.1/2 * ||theta||²; intercept is theta[0], penalized.

    The price-reference logit is an offset with coefficient exactly one.
    This function accepts an already training-transformed matrix for auditing.
    """
    x = np.asarray(transformed_features, dtype=float)
    if x.ndim != 2 or not len(x) or not np.isfinite(x).all():
        raise ValueError('Finite two-dimensional transformed design required')
    theta = _vector(coefficients, 'coefficients', x.shape[1] + 1)
    y, offset = _labels(labels, len(x)), _offset(q_under, len(x))
    design = np.column_stack([np.ones(len(x)), x])
    eta = offset + design @ theta
    objective = binary_log_loss_from_logits(eta, y) + PENALTY / 2 * float(theta @ theta)
    gradient = design.T @ (expit(eta) - y) / len(x) + PENALTY * theta
    return objective, gradient


@dataclass(frozen=True)
class ProbabilityModel:
    standardizer: Standardizer
    coefficients: np.ndarray
    training_rows: int
    optimizer_iterations: int

    def predict_logits(self, features, q_under):
        x = self.standardizer.transform(features)
        _half_point_contract(features)
        return _offset(q_under, len(x)) + self.coefficients[0] + x @ self.coefficients[1:]

    def predict_under(self, features, q_under):
        return expit(self.predict_logits(features, q_under))


def fit_probability(features, q_under, labels, *, with_revisions):
    """Low-level fit on caller-supplied arrays; metadata guard is separate."""
    transform = Standardizer.fit(features, with_revisions=with_revisions)
    _half_point_contract(features)
    x = transform.transform(features)
    y = _labels(labels, len(x))
    _offset(q_under, len(x))
    try:
        fitted = minimize(offset_log_loss_gradient, np.zeros(x.shape[1] + 1),
                          args=(x, q_under, y), jac=True, method='L-BFGS-B',
                          options={'maxiter': 1000, 'gtol': 1e-9, 'ftol': 1e-12, 'maxls': 50})
        valid = (fitted.success and np.isfinite(fitted.fun) and np.isfinite(fitted.x).all()
                 and np.isfinite(fitted.jac).all() and np.max(np.abs(fitted.jac)) < 1e-5)
        if not valid:
            raise ModelFitError('Probability optimizer did not converge to a finite solution')
    except (ValueError, FloatingPointError, OverflowError) as exc:
        raise ModelFitError('Probability fit is unavailable') from exc
    return ProbabilityModel(transform, _readonly(fitted.x), len(x), int(fitted.nit))


@dataclass(frozen=True)
class MovementModel:
    standardizer: Standardizer
    coefficients: np.ndarray  # intercept followed by slopes, standardized target
    target_mean: float
    target_scale: float
    training_rows: int

    def predict(self, features):
        x = self.standardizer.transform(features)
        if self.target_scale == 0:
            return np.full(len(x), self.target_mean)
        return self.target_mean + self.target_scale * (self.coefficients[0] + x @ self.coefficients[1:])


def fit_movement(features, movement_points, *, with_revisions):
    """Mean squared standardized error +0.1/2 * ||slopes||².

    The movement intercept is unpenalized. This normalization makes the ridge
    normal-equation addition 0.05, not 0.1 or an unscaled sum-loss penalty.
    """
    transform = Standardizer.fit(features, with_revisions=with_revisions)
    x = transform.transform(features)
    y = _vector(movement_points, 'movement target', len(x))
    mean, scale = float(y.mean()), float(y.std(ddof=0))
    if not np.isfinite(mean) or not np.isfinite(scale):
        raise ModelFitError('Movement target standardization overflowed')
    coefficients = np.zeros(x.shape[1] + 1)
    if scale != 0:
        standardized = (y - mean) / scale
        x_mean, y_mean = x.mean(axis=0), standardized.mean()
        centered = x - x_mean
        try:
            slopes = np.linalg.solve(centered.T @ centered / len(x) + PENALTY / 2 * np.eye(x.shape[1]),
                                     centered.T @ (standardized - y_mean) / len(x))
        except np.linalg.LinAlgError as exc:
            raise ModelFitError('Movement fit is unavailable') from exc
        coefficients[1:] = slopes
        coefficients[0] = y_mean - x_mean @ slopes
    if not np.isfinite(coefficients).all():
        raise ModelFitError('Movement coefficients are nonfinite')
    return MovementModel(transform, _readonly(coefficients), mean, scale, len(x))


def proportional_under_probability(over_decimal, under_decimal):
    """Margin-removed same-line price reference, not a true-probability claim."""
    over = _vector(over_decimal, 'Over decimal prices')
    under = _vector(under_decimal, 'Under decimal prices', len(over))
    if not ((over > 1) & (under > 1)).all():
        raise ValueError('Decimal prices must exceed one')
    return (1 / under) / (1 / under + 1 / over)


def same_line_ev(p_under, *, reference_line, offered_line, over_decimal, under_decimal):
    """Unit-risk modeled EV only at the exact fitted half-point contract.

    No cross-line probability transport, push estimate, staking or pick choice.
    """
    values = (p_under, reference_line, offered_line, over_decimal, under_decimal)
    if any(isinstance(v, (bool, np.bool_)) or not isinstance(v, (int, float, np.number)) or not np.isfinite(v) for v in values):
        raise ValueError('Finite scalar probabilities, lines and prices required')
    if not 0 <= p_under <= 1 or min(over_decimal, under_decimal) <= 1:
        raise ValueError('Invalid probability or decimal price')
    if reference_line <= 0 or reference_line % 1 != .5 or offered_line != reference_line:
        raise ValueError('Exact same half-point line required; pushes and other lines are unavailable')
    return {'under_ev': float(under_decimal * p_under - 1),
            'over_ev': float(over_decimal * (1 - p_under) - 1), 'push_probability': 0.}


def _utc(value):
    try:
        date = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError) as exc:
        raise ValueError('Invalid metadata timestamp') from exc
    if date.tzinfo is None or date.utcoffset() is None:
        raise ValueError('Timezone-aware metadata required')
    return date.astimezone(timezone.utc)


def _week(kickoff):
    date = kickoff.astimezone(ZoneInfo('America/New_York')).date()
    return date - timedelta(days=date.weekday())


def validate_chronological_split(training: Sequence[Mapping], testing: Sequence[Mapping], *, cutoff, target_kind):
    """Guard metadata only; never reads scores or market-movement outcomes.

    Each record has game_id, kickoff, decision_at. Training also requires the
    actual target_available_at receipt. One designated decision/game is allowed.
    The runner still owns official context, six-hour runs, scheduled slots,
    same-line quotes and feature-time provenance; this guard cannot infer them.
    """
    if target_kind not in ('final_total', 'market_movement'):
        raise ValueError('Unknown fixed target kind')
    if not training or not testing:
        raise ValueError('Both training and testing metadata are required')
    as_of = _utc(cutoff)
    groups = []
    for records, is_training in ((training, True), (testing, False)):
        ids, weeks = set(), set()
        for row in records:
            game_id = str(row['game_id'])
            if not game_id.isdigit() or int(game_id) <= 0:
                raise ValueError('Canonical positive game identity required')
            game_id = str(int(game_id))
            if game_id in ids:
                raise ValueError('Repeated game decisions are not independent training/test rows')
            ids.add(game_id)
            kickoff, decision = _utc(row['kickoff']), _utc(row['decision_at'])
            if decision >= kickoff:
                raise ValueError('Decision must be pregame')
            weeks.add(_week(kickoff))
            if is_training:
                available = _utc(row['target_available_at'])
                if not decision < available < as_of:
                    raise ValueError('Training target was not available strictly before cutoff')
                if target_kind == 'final_total' and available < kickoff:
                    raise ValueError('Final-total target cannot precede kickoff')
                if target_kind == 'market_movement' and not available < kickoff:
                    raise ValueError('Movement target must remain pregame')
            elif decision < as_of:
                raise ValueError('Test decision predates training cutoff')
        groups.append((ids, weeks))
    train_ids, train_weeks = groups[0]
    test_ids, test_weeks = groups[1]
    if train_ids & test_ids:
        raise ValueError('Game crosses training/test split')
    if train_weeks & test_weeks or max(train_weeks) >= min(test_weeks):
        raise ValueError('Whole Eastern weeks must remain separate and chronological')
    for week in train_weeks:
        next_monday = week + timedelta(days=7)
        week_end = datetime(next_monday.year, next_monday.month, next_monday.day,
                            tzinfo=ZoneInfo('America/New_York')).astimezone(timezone.utc)
        if week_end > as_of:
            raise ValueError('Training weeks must be completed before the cutoff')
    if len(train_ids) < MIN_TRAIN_GAMES or len(train_weeks) < MIN_TRAIN_WEEKS:
        raise ValueError('Experimental design requires at least 60 distinct training games and 2 completed weeks; this is not evidence of an edge')
    return {'training_games': len(train_ids), 'training_weeks': len(train_weeks),
            'testing_games': len(test_ids), 'testing_weeks': len(test_weeks),
            'cutoff': as_of.isoformat(), 'target_kind': target_kind}
