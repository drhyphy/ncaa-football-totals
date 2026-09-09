"""Unvalidated, fixed integer-score shape candidate; no I/O or corpus fitting.

The learned ratio changes score shape. An exponential tilt then minimizes KL
distance to that reweighted distribution subject to the ORIGINAL discrete
mean and variance. It does not learn a new mean, variance, probability cutoff,
tail family or betting rule. Score 250 is the existing final/overflow bin.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.special import logsumexp

VERSION = "score-shape-two-moment-v1"
SCORES = np.arange(251, dtype=float)
PRIOR_GAMES = 800.
RATIO_BOUNDS = (.6, 1.6)
MOMENT_TOLERANCE = 1e-11
NORMALIZATION_TOLERANCE = 1e-10
MAX_ITERATIONS = 100
MAX_BACKTRACKS = 50
ARMIJO = 1e-4


class ShapeConvergenceError(ValueError):
    """The fixed correction failed; no silent baseline substitution is allowed."""


def _array(value, name):
    raw = np.asarray(value)
    if raw.dtype.kind not in "iuf" or raw.dtype.kind == "b":
        raise ValueError(name + " must be finite numeric values")
    array = np.array(raw, dtype=float, copy=True)
    if not np.isfinite(array).all():
        raise ValueError(name + " must be finite")
    return array


def _pmfs(value):
    matrix = _array(value, "PMFs")
    if matrix.ndim != 2 or matrix.shape[1] != len(SCORES) or not len(matrix):
        raise ValueError("Nonempty PMFs must have exactly 251 score bins")
    if np.any(matrix <= 0):
        raise ValueError("PMFs must be strictly positive on all score bins")
    sums = matrix.sum(axis=1)
    if np.any(np.abs(sums-1.) > NORMALIZATION_TOLERANCE):
        raise ValueError("PMFs must already be normalized")
    return matrix/sums[:, None]


def _seasons(value, count):
    seasons = _array(value, "Seasons")
    if seasons.ndim != 1 or len(seasons) != count or np.any(seasons < 1) or np.any(seasons != np.floor(seasons)):
        raise ValueError("One positive integer season per row is required")
    return seasons


def _ratios(value):
    ratio = _array(value, "Score ratios")
    if ratio.shape != (len(SCORES),) or np.any(ratio < RATIO_BOUNDS[0]) or np.any(ratio > RATIO_BOUNDS[1]):
        raise ValueError("Score ratios must have 251 values within the fixed [.6,1.6] bounds")
    return ratio


def _dual(theta, log_weights, basis):
    """Dual, gradient, covariance Hessian and normalized mass.

The target basis moments are (0,1), so the linear dual term is -theta[1].
"""
    logits = log_weights+basis @ theta
    normalizer = logsumexp(logits)
    mass = np.exp(logits-normalizer)
    moments = mass @ basis
    centered = basis-moments
    hessian = centered.T @ (mass[:, None]*centered)
    gradient = moments-np.array([0., 1.])
    objective = float(normalizer-theta[1])
    if not (np.isfinite(objective) and np.isfinite(gradient).all() and np.isfinite(hessian).all() and np.isfinite(mass).all()):
        raise ShapeConvergenceError("Nonfinite moment-correction arithmetic")
    return objective, gradient, hessian, mass


def moment_correct(pmf, ratios):
    """Correct one positive PMF, retaining its original discrete two moments.

Newton iterations begin at zero. Backtracking accepts Armijo descent or a
halving of the moment error within eight machine epsilons of objective roundoff.
Every accepted result must satisfy both standardized moment constraints.
"""
    supplied = _array(pmf, "PMF")
    if supplied.shape != (len(SCORES),):
        raise ValueError("Moment correction requires one 251-bin PMF")
    baseline = _pmfs(supplied[None, :])[0]
    ratio = _ratios(ratios)
    mean = float(baseline @ SCORES)
    variance = float(baseline @ (SCORES-mean)**2)
    if not np.isfinite(variance) or variance <= 0:
        raise ShapeConvergenceError("Positive finite baseline variance required")
    z = (SCORES-mean)/np.sqrt(variance)
    basis = np.column_stack((z, z*z))
    if not np.isfinite(basis).all():
        raise ShapeConvergenceError("Unrepresentable standardized score basis")
    log_weights = np.log(baseline)+np.log(ratio)
    theta = np.zeros(2)
    for iteration in range(MAX_ITERATIONS+1):
        objective, gradient, hessian, mass = _dual(theta, log_weights, basis)
        error = float(np.max(np.abs(gradient)))
        if error <= MOMENT_TOLERANCE:
            break
        if iteration == MAX_ITERATIONS:
            raise ShapeConvergenceError("Moment correction exceeded fixed iteration limit")
        try:
            direction = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError as exc:
            raise ShapeConvergenceError("Singular moment-correction Hessian") from exc
        descent = float(gradient @ direction)
        if not np.isfinite(direction).all() or not np.isfinite(descent) or descent <= 0:
            raise ShapeConvergenceError("Invalid Newton descent direction")
        step = 1.
        for _ in range(MAX_BACKTRACKS):
            proposed = theta-step*direction
            new_objective, new_gradient, _, _ = _dual(proposed, log_weights, basis)
            roundoff = 8*np.finfo(float).eps*max(1., abs(objective), abs(new_objective))
            armijo = new_objective <= objective-ARMIJO*step*descent
            improved_moments = (np.max(np.abs(new_gradient)) <= .5*error
                                and new_objective <= objective+roundoff)
            if armijo or improved_moments:
                theta = proposed
                break
            step *= .5
        else:
            raise ShapeConvergenceError("Moment correction failed fixed line search")
    # Renormalize floating roundoff and check actual returned moments, not only
    # the optimizer's state. Underflowed zero support is an explicit failure.
    mass = mass/mass.sum()
    achieved = mass @ basis
    if np.any(mass <= 0) or not np.isfinite(mass).all() or abs(mass.sum()-1.) > 1e-14:
        raise ShapeConvergenceError("Corrected PMF lost positive normalized support")
    if max(abs(achieved[0]), abs(achieved[1]-1.)) > MOMENT_TOLERANCE:
        raise ShapeConvergenceError("Returned PMF violates moment tolerance")
    new_mean = float(mass @ SCORES)
    new_variance = float(mass @ (SCORES-new_mean)**2)
    diagnostics = {"status": "converged", "iterations": iteration,
                   "theta": theta.tolist(), "dual_objective": objective,
                   "max_standardized_moment_error": float(max(abs(achieved[0]), abs(achieved[1]-1.))),
                   "baseline_mean": mean, "corrected_mean": new_mean,
                   "baseline_variance": variance, "corrected_variance": new_variance,
                   "mean_difference": new_mean-mean, "variance_difference": new_variance-variance}
    return mass, diagnostics


@dataclass(frozen=True)
class ScoreShape:
    """Earlier-season ratio estimate; an unvalidated research candidate."""
    ratios: np.ndarray
    raw_ratios: np.ndarray
    observed_counts: np.ndarray
    expected_counts: np.ndarray
    training_seasons: tuple[int, ...]
    training_games: int
    clipped_low_scores: int
    clipped_high_scores: int

    @classmethod
    def fit(cls, observed_scores, expected_pmfs, training_seasons):
        expected = _pmfs(expected_pmfs)
        actual = _array(observed_scores, "Observed scores")
        if actual.ndim != 1 or len(actual) != len(expected) or np.any(actual != np.floor(actual)) or np.any((actual < 0) | (actual > 250)):
            raise ValueError("One exact integer score in [0,250] per earlier PMF is required")
        seasons = _seasons(training_seasons, len(expected))
        observed_counts = np.bincount(actual.astype(int), minlength=len(SCORES)).astype(float)
        expected_counts = expected.sum(axis=0)
        prior = PRIOR_GAMES*expected_counts/len(expected)
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            raw_ratio = (observed_counts+prior)/(expected_counts+prior)
        if not np.isfinite(raw_ratio).all():
            raise ValueError("Raw score ratio cannot be represented finitely")
        ratios = np.clip(raw_ratio, *RATIO_BOUNDS)
        for value in (ratios, raw_ratio, observed_counts, expected_counts):
            value.setflags(write=False)
        return cls(ratios, raw_ratio, observed_counts, expected_counts,
                   tuple(sorted(set(map(int, seasons)))), len(expected),
                   int(np.sum(raw_ratio < RATIO_BOUNDS[0])), int(np.sum(raw_ratio > RATIO_BOUNDS[1])))

    @property
    def training_max_season(self):
        return max(self.training_seasons)

    def predict_pmf(self, base_pmfs, prediction_seasons):
        baseline = _pmfs(base_pmfs)
        seasons = _seasons(prediction_seasons, len(baseline))
        if np.any(seasons <= self.training_max_season):
            raise ValueError("Every prediction season must follow all fitted seasons")
        probabilities, diagnostics = [], []
        for row in baseline:
            mass, detail = moment_correct(row, self.ratios)
            probabilities.append(mass)
            diagnostics.append(detail)
        return np.stack(probabilities), diagnostics

    def metadata(self):
        return {"version": VERSION, "training_games": self.training_games,
                "training_seasons": list(self.training_seasons), "training_max_season": self.training_max_season,
                "prior_games": PRIOR_GAMES, "ratio_bounds": list(RATIO_BOUNDS),
                "moment_tolerance": MOMENT_TOLERANCE, "max_iterations": MAX_ITERATIONS,
                "max_backtracks": MAX_BACKTRACKS, "armijo": ARMIJO,
                "clipped_low_scores": self.clipped_low_scores, "clipped_high_scores": self.clipped_high_scores,
                "ratios": self.ratios.tolist(), "raw_ratios": self.raw_ratios.tolist(),
                "observed_counts": self.observed_counts.tolist(),
                "expected_counts": self.expected_counts.tolist(),
                "status": "unvalidated_research_candidate", "normalization_tolerance": NORMALIZATION_TOLERANCE,
                "solver": "damped_newton_analytic_gradient_covariance_hessian",
                "objective_roundoff_multiplier": 8, "moment_error_acceptance_factor": .5}
