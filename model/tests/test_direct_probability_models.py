"""Synthetic numerical contract tests; no football data, files or network."""
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from scipy.special import expit

from ncaaf_model import direct_probability_models as direct
from ncaaf_model.opponent_model import FEATURES as OPPONENT_FEATURES


def frame(config="context_logit", size=180):
    random = np.random.default_rng(2026)
    values = {name: random.normal(size=size) for name in direct.columns_for_config(config)}
    values["market_total"] = random.integers(35, 75, size=size) + .5
    values["abs_spread"] = np.arange(size, dtype=float) / 10
    values["week"] = random.integers(1, 15, size=size)
    values["clock_rule_2023"] = np.ones(size)
    values["two_minute_rule_2024"] = np.zeros(size)
    return pd.DataFrame(values, columns=direct.columns_for_config(config))


def labels(size):
    return (np.arange(size) >= size // 2).astype(int)


def test_feature_sets_and_target_contract_are_exact():
    assert set(direct.FEATURES) == {"context_logit", "opponent_logit", "context_hgb", "opponent_hgb"}
    assert direct.columns_for_config("context_hgb") == ("market_total", "abs_spread", "week", "clock_rule_2023", "two_minute_rule_2024")
    assert direct.columns_for_config("opponent_logit") == tuple(OPPONENT_FEATURES)
    with pytest.raises(ValueError, match="Unknown"):
        direct.columns_for_config("adaptive_best")


@pytest.mark.parametrize("config", list(direct.FEATURES))
def test_requires_100_rows_and_both_classes_without_fallback(config):
    with pytest.raises(direct.ModelFitError, match="At least 100"):
        direct.fit(frame(config, 99), labels(99), config)
    with pytest.raises(direct.ModelFitError, match="both binary classes"):
        direct.fit(frame(config, 100), np.ones(100), config)


@pytest.mark.parametrize("change", ["target", "missing", "duplicate", "string", "nan", "infinity", "complex"])
def test_rejects_hidden_target_columns_missing_features_and_nonfinite_values(change):
    x = frame()
    if change == "target":
        x["actual_total"] = "THIS COLUMN MUST NEVER BE USED"
    elif change == "missing":
        x = x.drop(columns="week")
    elif change == "duplicate":
        x = pd.concat([x, x[["week"]]], axis=1)
    elif change == "string":
        x["week"] = x["week"].astype(str)
    elif change == "nan":
        x.loc[0, "abs_spread"] = np.nan
    elif change == "infinity":
        x.loc[0, "abs_spread"] = np.inf
    else:
        x["week"] = x["week"].astype(complex)
    with pytest.raises(ValueError):
        direct.fit(x, labels(len(x)), "context_logit")


@pytest.mark.parametrize("line", [0, -.5, 50., 50.25, np.nan, np.inf])
def test_reference_contract_is_positive_finite_halfpoint_at_fit_and_prediction(line):
    x = frame()
    model = direct.fit(x, labels(len(x)), "context_logit")
    x.loc[0, "market_total"] = line
    with pytest.raises(ValueError):
        model.predict_under(x)
    with pytest.raises(ValueError):
        direct.fit(x, labels(len(x)), "context_logit")


def test_label_length_values_and_series_alignment_are_explicit():
    x = frame()
    for y in [labels(len(x)-1), np.full(len(x), .4), np.full(len(x), np.nan), np.ones((len(x), 1)), ["under"] * len(x)]:
        with pytest.raises(ValueError):
            direct.fit(x, y, "context_logit")
    wrong_index = pd.Series(labels(len(x)), index=x.index[::-1])
    with pytest.raises(ValueError, match="index"):
        direct.fit(x, wrong_index, "context_logit")
    valid = direct.fit(x, pd.Series(labels(len(x)).astype(bool), index=x.index), "context_logit")
    assert valid.training_rows == len(x)


def test_logistic_gradient_matches_central_differences_and_penalizes_intercept():
    random = np.random.default_rng(5)
    x = random.normal(size=(30, 5))
    y = (random.uniform(size=30) > .4).astype(int)
    theta = random.normal(size=6) * .3
    value, gradient = direct.logistic_objective_gradient(theta, x, y)
    numerical = []
    for i in range(len(theta)):
        delta = np.zeros_like(theta)
        delta[i] = 1e-6
        numerical.append((direct.logistic_objective_gradient(theta + delta, x, y)[0]
                          - direct.logistic_objective_gradient(theta - delta, x, y)[0]) / 2e-6)
    np.testing.assert_allclose(gradient, numerical, rtol=1e-6, atol=2e-9)
    eta = theta[0] + x @ theta[1:]
    expected = np.mean(np.logaddexp(0, np.where(y == 1, -eta, eta))) + .05 * np.dot(theta, theta)
    assert value == pytest.approx(expected)
    assert gradient[0] == pytest.approx(np.mean(expit(eta) - y) + .1 * theta[0])
    assert value != pytest.approx(np.mean((y - expit(eta))**2) + .05 * np.dot(theta, theta))


def test_loss_is_mean_normalized_and_stable_at_extreme_logits():
    x = np.array([[1.], [-1.]])
    y = np.array([1., 0.])
    theta = np.array([.2, .8])
    original = direct.logistic_objective_gradient(theta, x, y)
    repeated = direct.logistic_objective_gradient(theta, np.tile(x, (20, 1)), np.tile(y, 20))
    assert original[0] == pytest.approx(repeated[0])
    np.testing.assert_allclose(original[1], repeated[1], atol=1e-15)
    assert direct.binary_log_loss_from_logits([10000., -10000.], [1, 0]) == 0
    assert direct.binary_log_loss_from_logits([10000., -10000.], [0, 1]) == 10000
    value, gradient = direct.logistic_objective_gradient([0., 10000.], x, y)
    assert np.isfinite(value) and np.isfinite(gradient).all()


def test_population_standardization_constant_columns_and_canonical_order():
    x = frame()
    model = direct.fit(x, labels(len(x)), "context_logit")
    numeric = x.loc[:, list(model.features)].to_numpy(float)
    np.testing.assert_allclose(model.standardizer.mean, numeric.mean(axis=0))
    expected_scale = numeric.std(axis=0, ddof=0)
    expected_scale[expected_scale == 0] = 1.
    np.testing.assert_allclose(model.standardizer.scale, expected_scale)
    assert model.standardizer.scale[-2:].tolist() == [1., 1.]
    np.testing.assert_allclose(model.predict_logits(x), model.predict_logits(x.iloc[:, ::-1]))
    assert not model.standardizer.mean.flags.writeable
    assert not model.standardizer.scale.flags.writeable
    assert not model.coefficients.flags.writeable
    assert model.metadata["parameters"]["intercept_penalized"] is True
    assert model.metadata["loss"] == "binary_log_loss"
    fitted = model.metadata["fitted_parameters"]
    z = np.clip((numeric - fitted["mean"]) / fitted["scale"], -5, 5)
    np.testing.assert_allclose(model.predict_logits(x), fitted["intercept"] + z @ np.asarray(fitted["coefficients"]))
    objective, gradient = direct.logistic_objective_gradient(model.coefficients, z, labels(len(x)))
    assert model.metadata["optimizer_success"] is True
    assert np.isfinite(model.metadata["optimizer_objective"])
    assert model.metadata["optimizer_objective"] == pytest.approx(objective, abs=1e-14)
    assert np.isfinite(model.metadata["optimizer_max_abs_gradient"])
    assert model.metadata["optimizer_max_abs_gradient"] <= 1e-5
    assert model.metadata["optimizer_max_abs_gradient"] == pytest.approx(np.max(np.abs(gradient)), abs=1e-14)
    # Returned review metadata cannot mutate the fitted numerical transform.
    fitted["mean"][0] = -1000
    assert model.metadata["fitted_parameters"]["mean"][0] != -1000


def test_future_rows_never_refit_training_transform_and_train_input_mutation_is_isolated():
    x = frame()
    model = direct.fit(x, labels(len(x)), "context_logit")
    probe = x.iloc[:3].copy()
    expected = model.predict_logits(probe)
    original_mean = model.standardizer.mean.copy()
    future = probe.copy()
    future["abs_spread"] = [1e300, -1e300, 10000.]
    z = model.standardizer.apply(direct._matrix(future, model.features))
    assert z[:, model.features.index("abs_spread")].tolist() == [5., -5., 5.]
    model.predict_logits(future)
    x.loc[:, "abs_spread"] = 1e200
    np.testing.assert_array_equal(model.standardizer.mean, original_mean)
    np.testing.assert_allclose(model.predict_logits(probe), expected)
    assert model.predict_under(probe).shape == (3,)
    assert model.predict_under(probe.iloc[:0]).shape == (0,)


@pytest.mark.parametrize("mode", ["failed", "large_gradient", "nonfinite_solution"])
def test_failed_optimizer_never_returns_a_fallback_model(mode):
    x = frame()
    result = SimpleNamespace(success=True, fun=.6, x=np.zeros(6), jac=np.zeros(6), nit=1)
    if mode == "failed":
        result.success = False
    elif mode == "large_gradient":
        result.jac[0] = .01
    else:
        result.x[0] = np.nan
    with patch.object(direct, "minimize", return_value=result) as optimizer:
        with pytest.raises(direct.ModelFitError, match="converge"):
            direct.fit(x, labels(len(x)), "context_logit")
    args, kwargs = optimizer.call_args
    np.testing.assert_array_equal(args[1], np.zeros(6))
    assert kwargs["method"] == "L-BFGS-B" and kwargs["jac"] is True
    assert kwargs["options"] == {"maxiter": 1000, "ftol": 1e-12, "gtol": 1e-8}


@pytest.mark.parametrize("config", list(direct.FEATURES))
def test_positive_class_is_under_not_over_and_opponent_features_remain_explicit(config):
    x = frame(config, 240)
    model = direct.fit(x, labels(len(x)), config)
    p = model.predict_under(x)
    np.testing.assert_allclose(p, expit(model.predict_logits(x)), rtol=1e-13, atol=1e-13)
    assert p[-60:].mean() > p[:60].mean() + .15
    assert np.isfinite(p).all() and ((p >= 0) & (p <= 1)).all()
    assert model.config == config and model.features == direct.columns_for_config(config)
    assert model.metadata["positive_class"] == 1
    assert model.metadata["alternative_line_probabilities_supported"] is False
    assert model.training_rows == 240


@pytest.mark.parametrize("config", ["context_hgb", "opponent_hgb"])
def test_hgb_fixed_classifier_uses_raw_inputs_public_logits_and_consistent_under_probabilities(config):
    x = frame(config, 240)
    model = direct.fit(x, labels(len(x)), config)
    assert model.standardizer is None and model.coefficients is None
    params = model.estimator.get_params()
    assert all(params[key] == value for key, value in direct.HGB_PARAMETERS.items())
    assert model.estimator.n_iter_ == 220
    assert model.metadata["tree_iterations"] == 220
    assert "fitted_parameters" not in model.metadata
    assert "feature_importance" not in model.metadata
    assert params["loss"] == "log_loss" and params["early_stopping"] is False
    raw = x.loc[:, list(model.features)].to_numpy(float)
    np.testing.assert_allclose(model.predict_logits(x), model.estimator.decision_function(raw))
    np.testing.assert_allclose(model.predict_under(x), model.estimator.predict_proba(raw)[:, 1])
    np.testing.assert_array_equal(model.estimator.classes_, [0, 1])
    expected = model.predict_under(x.iloc[:3])
    with patch.object(model.estimator, "predict_proba", return_value=np.ones((3, 2)) * .5):
        assert not np.allclose(expected, .5)
        with pytest.raises(direct.ModelFitError, match="disagree"):
            model.predict_under(x.iloc[:3])


def test_hgb_public_class_order_cannot_silently_reverse_under_direction():
    x = frame("context_hgb", 120)
    model = direct.fit(x, labels(len(x)), "context_hgb")
    with patch.object(model.estimator, "classes_", np.array([1, 0])):
        with pytest.raises(direct.ModelFitError, match="target direction"):
            model.predict_logits(x.iloc[:2])
