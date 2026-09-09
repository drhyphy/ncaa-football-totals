"""Four fixed, unvalidated binary Under classifiers; numerical utilities only.

The caller supplies a feature-only DataFrame and binary labels (Under=1) for
the exact half-point market_total contract. The caller owns chronological data
selection. No outcomes, prices, alternative-line CDFs, files or network sources
are accessed here. A successful fit is not evidence of profitable predictions.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import scipy
from scipy.optimize import minimize
from scipy.special import expit
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier

from .opponent_model import FEATURES as OPPONENT_FEATURE_NAMES

VERSION = "direct-probability-models-v1"
CONTEXT_FEATURES = ("market_total", "abs_spread", "week", "clock_rule_2023", "two_minute_rule_2024")
OPPONENT_FEATURES = tuple(OPPONENT_FEATURE_NAMES)
FEATURES = {"context_logit": CONTEXT_FEATURES, "opponent_logit": OPPONENT_FEATURES,
            "context_hgb": CONTEXT_FEATURES, "opponent_hgb": OPPONENT_FEATURES}
MIN_TRAINING_ROWS = 100
PENALTY = 0.1
STANDARDIZED_CLIP = 5.0
LOGIT_OPTIONS = {"maxiter": 1000, "ftol": 1e-12, "gtol": 1e-8}
HGB_PARAMETERS = {"loss": "log_loss", "learning_rate": .025, "max_iter": 220,
                  "max_leaf_nodes": 10, "min_samples_leaf": 45, "l2_regularization": 20.,
                  "random_state": 2026, "early_stopping": False}


class ModelFitError(RuntimeError):
    """A failed numerical fit or invalid fitted output is unavailable."""


def columns_for_config(config: str) -> tuple[str, ...]:
    if not isinstance(config, str) or config not in FEATURES:
        raise ValueError("Unknown direct-probability configuration")
    return FEATURES[config]


def _matrix(features: pd.DataFrame, names: tuple[str, ...]) -> np.ndarray:
    if not isinstance(features, pd.DataFrame):
        raise ValueError("Features must be a feature-only pandas DataFrame")
    if features.columns.has_duplicates or set(features.columns) != set(names):
        raise ValueError("DataFrame columns must exactly match the prescribed feature set")
    # Reject string coercion, categorical values, complex numbers and hidden
    # target columns; only these prescribed columns are ever selected.
    for name in names:
        if features[name].dtype.kind not in "biuf":
            raise ValueError("Feature columns must have real numeric dtypes")
    try:
        x = features.loc[:, list(names)].to_numpy(dtype=float, na_value=np.nan, copy=True)
    except (TypeError, ValueError) as error:
        raise ValueError("Features could not be represented as finite numbers") from error
    if not np.isfinite(x).all():
        raise ValueError("Every prescribed feature must be finite; no imputation is allowed")
    lines = x[:, names.index("market_total")]
    if not ((lines > 0) & (lines % 1 == .5)).all():
        raise ValueError("market_total must be a positive half-point reference contract")
    return x


def _labels(labels, features: pd.DataFrame) -> np.ndarray:
    if isinstance(labels, pd.Series) and not labels.index.equals(features.index):
        raise ValueError("Label Series index must equal the feature DataFrame index")
    raw = np.asarray(labels)
    if raw.ndim != 1 or raw.dtype.kind not in "biuf" or len(raw) != len(features):
        raise ValueError("Under labels must be a matching one-dimensional binary vector")
    y = np.asarray(raw, dtype=float)
    if not np.isfinite(y).all() or not np.isin(y, [0., 1.]).all():
        raise ValueError("Labels must be binary: Under=1, Over=0; pushes are unsupported")
    return y


def _readonly(values):
    value = np.asarray(values, dtype=float).copy()
    value.setflags(write=False)
    return value


@dataclass(frozen=True)
class PopulationTransform:
    """Frozen training mean and population SD; intercept is added afterward."""

    mean: np.ndarray
    scale: np.ndarray

    @classmethod
    def fit(cls, x):
        with np.errstate(over="ignore", invalid="ignore"):
            mean = x.mean(axis=0)
            scale = x.std(axis=0, ddof=0)
        scale = np.where(scale == 0, 1., scale)
        if not np.isfinite(mean).all() or not np.isfinite(scale).all():
            raise ModelFitError("Training standardization overflowed")
        return cls(_readonly(mean), _readonly(scale))

    def apply(self, x):
        with np.errstate(over="ignore", invalid="ignore"):
            z = np.clip((x - self.mean) / self.scale, -STANDARDIZED_CLIP, STANDARDIZED_CLIP)
        if not np.isfinite(z).all():
            raise ModelFitError("Standardized features are unavailable")
        return z


def binary_log_loss_from_logits(logits, labels) -> float:
    """Stable binary log loss without clipping the model's raw probabilities."""
    eta, y = np.asarray(logits), np.asarray(labels)
    if eta.ndim != 1 or y.ndim != 1 or eta.dtype.kind not in "biuf" or y.dtype.kind not in "biuf" or not len(eta) or len(eta) != len(y):
        raise ValueError("Matching nonempty numeric logits and binary labels required")
    eta, y = eta.astype(float), y.astype(float)
    if not np.isfinite(eta).all() or not np.isfinite(y).all() or not np.isin(y, [0., 1.]).all():
        raise ValueError("Finite logits and binary labels required")
    return float(np.mean(np.logaddexp(0., np.where(y == 1, -eta, eta))))


def logistic_objective_gradient(coefficients, standardized_features, labels):
    """Mean binary log loss + .05*||theta||², including intercept theta[0]."""
    x, theta, y = (np.asarray(value) for value in (standardized_features, coefficients, labels))
    if x.ndim != 2 or not len(x) or x.dtype.kind not in "biuf" or not np.isfinite(x).all():
        raise ValueError("Finite, nonempty standardized feature matrix required")
    if theta.ndim != 1 or theta.dtype.kind not in "biuf" or len(theta) != x.shape[1] + 1 or not np.isfinite(theta).all():
        raise ValueError("Finite intercept-plus-coefficient vector required")
    if y.ndim != 1 or y.dtype.kind not in "biuf" or len(y) != len(x) or not np.isfinite(y).all() or not np.isin(y, [0, 1]).all():
        raise ValueError("Matching binary Under labels required")
    x, theta, y = x.astype(float), theta.astype(float), y.astype(float)
    design = np.column_stack((np.ones(len(x)), x))
    with np.errstate(over="ignore", invalid="ignore"):
        eta = design @ theta
        objective = binary_log_loss_from_logits(eta, y) + PENALTY / 2 * float(theta @ theta)
        gradient = design.T @ (expit(eta) - y) / len(x) + PENALTY * theta
    if not np.isfinite(objective) or not np.isfinite(gradient).all():
        raise ValueError("Logistic objective overflowed")
    return objective, gradient


def _tree_logits(estimator, x):
    # Public APIs only. The positive class must remain Under=1, and public
    # predict_proba must agree with the expit of the public decision function.
    if not np.array_equal(estimator.classes_, np.array([0, 1])):
        raise ModelFitError("Tree classifier has an unsupported target direction")
    logits = np.asarray(estimator.decision_function(x), dtype=float)
    probabilities = np.asarray(estimator.predict_proba(x), dtype=float)
    if logits.shape != (len(x),) or probabilities.shape != (len(x), 2) or not np.isfinite(logits).all() or not np.isfinite(probabilities).all():
        raise ModelFitError("Tree classifier returned invalid binary predictions")
    under = expit(logits)
    if not (np.allclose(probabilities[:, 1], under, rtol=1e-12, atol=1e-12)
            and np.allclose(probabilities[:, 0], 1 - under, rtol=1e-12, atol=1e-12)):
        raise ModelFitError("Public tree probabilities disagree with binary Under logits")
    return logits


@dataclass(frozen=True)
class FittedBinaryModel:
    config: str
    features: tuple[str, ...]
    training_rows: int
    standardizer: PopulationTransform | None = None
    coefficients: np.ndarray | None = None
    estimator: HistGradientBoostingClassifier | None = None
    optimizer_iterations: int | None = None
    optimizer_success: bool | None = None
    optimizer_objective: float | None = None
    optimizer_max_abs_gradient: float | None = None

    @property
    def metadata(self) -> dict:
        value = {"version": VERSION, "config": self.config, "features": list(self.features),
                "training_rows": self.training_rows, "minimum_training_rows": MIN_TRAINING_ROWS,
                "minimum_is_confidence_claim": False, "target": "under_at_market_total_half_point",
                "positive_class": 1, "negative_class": 0, "alternative_line_probabilities_supported": False,
                "loss": "binary_log_loss", "chronology_enforced_by": "caller",
                "parameters": ({"penalty": PENALTY, "intercept_penalized": True,
                                "standardization": "training_population_mean_sd_constant_sd_one",
                                "standardized_clip": STANDARDIZED_CLIP, "method": "L-BFGS-B",
                                **LOGIT_OPTIONS, "maximum_accepted_gradient_norm": 1e-5}
                               if self.config.endswith("_logit") else dict(HGB_PARAMETERS)),
                "optimizer_iterations": self.optimizer_iterations,
                "software": {"numpy": np.__version__, "pandas": pd.__version__,
                             "scipy": scipy.__version__, "scikit_learn": sklearn.__version__}}
        if self.config.endswith("_logit"):
            value.update(optimizer_success=self.optimizer_success,
                         optimizer_objective=self.optimizer_objective,
                         optimizer_max_abs_gradient=self.optimizer_max_abs_gradient)
            value["fitted_parameters"] = {"intercept": float(self.coefficients[0]),
                                          "coefficients": self.coefficients[1:].tolist(),
                                          "mean": self.standardizer.mean.tolist(),
                                          "scale": self.standardizer.scale.tolist()}
        else:
            value["tree_iterations"] = int(self.estimator.n_iter_)
        return value

    def predict_logits(self, features: pd.DataFrame) -> np.ndarray:
        x = _matrix(features, self.features)
        if not len(x):
            return np.empty(0, dtype=float)
        if self.config.endswith("_logit"):
            z = self.standardizer.apply(x)
            logits = self.coefficients[0] + z @ self.coefficients[1:]
            if not np.isfinite(logits).all():
                raise ModelFitError("Logistic predictions are nonfinite")
            return logits
        return _tree_logits(self.estimator, x)

    def predict_under(self, features: pd.DataFrame) -> np.ndarray:
        return expit(self.predict_logits(features))


def fit(trainfeatures: pd.DataFrame, labels, config: str) -> FittedBinaryModel:
    """Fit one prescribed classifier; caller selects earlier-season inputs only.

    A Series of labels must align exactly with the DataFrame index. Other label
    vectors are positional. Fewer than 100 rows or a missing class is unavailable,
    not permission to substitute another candidate or a guessed probability.
    """
    names = columns_for_config(config)
    x = _matrix(trainfeatures, names)
    y = _labels(labels, trainfeatures)
    if len(x) < MIN_TRAINING_ROWS or set(y) != {0., 1.}:
        raise ModelFitError("At least 100 training rows with both binary classes are required")
    if config.endswith("_logit"):
        transform = PopulationTransform.fit(x)
        z = transform.apply(x)
        try:
            result = minimize(logistic_objective_gradient, np.zeros(len(names) + 1), args=(z, y), jac=True,
                              method="L-BFGS-B", options=dict(LOGIT_OPTIONS))
            valid = (result.success and np.isfinite(result.fun) and np.isfinite(result.x).all()
                     and np.isfinite(result.jac).all() and np.max(np.abs(result.jac)) <= 1e-5)
            if not valid:
                raise ModelFitError("Logistic optimizer did not converge to a finite solution")
        except (ValueError, FloatingPointError, OverflowError) as error:
            raise ModelFitError("Logistic fit is unavailable") from error
        return FittedBinaryModel(config, names, len(x), transform, _readonly(result.x),
                                 optimizer_iterations=int(result.nit), optimizer_success=True,
                                 optimizer_objective=float(result.fun),
                                 optimizer_max_abs_gradient=float(np.max(np.abs(result.jac))))
    try:
        estimator = HistGradientBoostingClassifier(**HGB_PARAMETERS)
        estimator.fit(x, y.astype(int))
        _tree_logits(estimator, x)
    except (ValueError, FloatingPointError, OverflowError) as error:
        raise ModelFitError("Tree fit is unavailable") from error
    return FittedBinaryModel(config, names, len(x), estimator=estimator)
