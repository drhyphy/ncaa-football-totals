"""Independent synthetic checks of the fixed grouped availability model.

No source archives, target-game results, network calls or real-data fits.
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.optimize import brentq, minimize
from scipy.special import expit

from ncaaf_model import availability_models as models


def sample(games=24, seed=317):
    rng = np.random.default_rng(seed)
    ids, phases = [], []
    for game in range(games):
        for phase in ((0, 1) if game % 3 == 0 else (0,)):
            ids.append(str(9007199254740993 + game))
            phases.append(phase)
    n = len(ids)
    missing_report = rng.integers(0, 2, n)
    missing_quote = rng.integers(0, 2, n)
    burden, previous = rng.uniform(0, 2, (2, n))
    features = {
        "market_total": rng.integers(30, 80, n) + .5,
        "log_hours_to_kickoff": np.log1p(rng.uniform(1, 80, n)),
        "market_total_change": np.where(missing_quote, 0., rng.normal(size=n)),
        "phase_gameday": np.asarray(phases),
        "missing_previous_report": missing_report,
        "missing_previous_quote": missing_quote,
        "qb_out_burden": burden,
        "qb_out_burden_change": np.where(missing_report, 0., burden - previous),
    }
    return features, ids, rng.uniform(.35, .65, n), rng.integers(0, 2, n)


def independent_weights(ids):
    counts = Counter(ids)
    unnormalized = np.array([1 / counts[gid] for gid in ids])
    return unnormalized / len(counts)


def independent_transform(features, names, ids):
    x = np.column_stack([features[name] for name in names])
    weights = independent_weights(ids)
    mean = np.array([sum(weights[i] * column[i] for i in range(len(x))) for column in x.T])
    scale = np.sqrt(np.array([sum(weights[i] * (column[i] - mean[j]) ** 2
                                  for i in range(len(x))) for j, column in enumerate(x.T)]))
    constant = np.all(x == x[0], axis=0)
    mean[constant], scale[constant] = x[0, constant], 1.
    return np.clip((x - mean) / scale, -5., 5.), mean, scale


def test_fixed_blocks_and_operational_minimum():
    assert models.BASE_FEATURES == (
        "market_total", "log_hours_to_kickoff", "market_total_change",
        "phase_gameday", "missing_previous_report", "missing_previous_quote")
    assert models.AVAILABILITY_FEATURES == ("qb_out_burden", "qb_out_burden_change")
    assert (models.PENALTY, models.CLIP) == (.1, 5.)
    assert (models.MIN_TRAIN_GAMES, models.MIN_TRAIN_WEEKS) == (20, 2)


def test_one_game_has_equal_mass_with_one_or_two_opportunities():
    weights, games = models.game_weights(["11", 11, "22"], [0, 1, 0])
    np.testing.assert_array_equal(weights, [.5, .5, 1.])
    assert games == 2
    logits = np.array([-2., 1., 3.])
    labels = np.array([1, 0, 1])
    losses = np.logaddexp(0., np.where(labels, -logits, logits))
    expected = ((losses[0] + losses[1]) / 2 + losses[2]) / 2
    assert models.weighted_log_loss(logits, labels, weights) == pytest.approx(expected)
    assert abs(expected - losses.mean()) > .01


def test_weighted_objective_and_gradient_match_independent_finite_differences():
    rng = np.random.default_rng(49)
    x, theta = rng.normal(size=(13, 8)), rng.normal(size=9) / 3
    q, labels = rng.uniform(.15, .85, 13), rng.integers(0, 2, 13)
    weights = np.array([.5] * 6 + [1.] * 7)
    normalized = weights / sum(weights)
    offset = np.log(q / (1 - q))

    def objective(value):
        logits = offset + value[0] + x @ value[1:]
        return sum(normalized * (np.logaddexp(0., logits) - labels * logits)) + .05 * sum(value ** 2)

    actual, gradient = models.offset_log_loss_gradient(theta, x, q, labels, weights)
    assert actual == pytest.approx(objective(theta), abs=5e-15)
    step = 1e-6
    numerical = [(objective(theta + np.eye(9)[j] * step) -
                  objective(theta - np.eye(9)[j] * step)) / (2 * step) for j in range(9)]
    np.testing.assert_allclose(gradient, numerical, atol=2e-9, rtol=2e-7)
    # Multiplying all supplied weights cannot alter a weighted mean objective.
    scaled = models.offset_log_loss_gradient(theta, x, q, labels, 7 * weights)
    assert scaled[0] == pytest.approx(actual, abs=5e-15)
    np.testing.assert_allclose(scaled[1], gradient, atol=5e-15)


@pytest.mark.parametrize("with_availability", [False, True])
def test_fit_matches_separate_trust_region_solution(with_availability):
    features, ids, q, labels = sample()
    saved = deepcopy((features, ids, q, labels))
    fitted = models.fit_probability(features, q, labels, ids, with_availability=with_availability)
    names = models.ALL_FEATURES if with_availability else models.BASE_FEATURES
    x, mean, scale = independent_transform(features, names, ids)
    design = np.column_stack([np.ones(len(ids)), x])
    weights = independent_weights(ids)
    offset = np.log(q / (1 - q))

    def objective(theta):
        eta = offset + design @ theta
        return weights @ (np.logaddexp(0., eta) - labels * eta) + .05 * (theta @ theta)

    def gradient(theta):
        return design.T @ (weights * (expit(offset + design @ theta) - labels)) + .1 * theta

    def hessian(theta):
        p = expit(offset + design @ theta)
        return design.T @ ((weights * p * (1 - p))[:, None] * design) + .1 * np.eye(len(theta))

    independent = minimize(objective, np.zeros(len(names) + 1), method="trust-exact",
                           jac=gradient, hess=hessian, options={"gtol": 1e-10, "maxiter": 100})
    assert np.max(np.abs(gradient(independent.x))) < 1e-7
    np.testing.assert_allclose(fitted.standardizer.mean, mean, rtol=2e-14, atol=2e-14)
    np.testing.assert_allclose(fitted.standardizer.scale, scale, rtol=2e-14, atol=2e-14)
    np.testing.assert_allclose(fitted.coefficients, independent.x, atol=2e-6, rtol=2e-6)
    assert objective(fitted.coefficients) == pytest.approx(independent.fun, abs=2e-12)
    # The penalized Hessian is bounded below by .1 I. The gradients therefore
    # bound coefficient error, and Cauchy-Schwarz bounds each logit difference.
    coefficient_bound = (np.linalg.norm(gradient(fitted.coefficients)) +
                         np.linalg.norm(gradient(independent.x))) / .1
    logit_errors = np.abs(fitted.predict_logits(features, q) - (offset + design @ independent.x))
    assert np.all(logit_errors <= np.linalg.norm(design, axis=1) * coefficient_bound + 1e-12)
    assert (fitted.training_games, fitted.training_rows) == (24, len(ids))
    assert not fitted.coefficients.flags.writeable
    for name in features:
        np.testing.assert_array_equal(features[name], saved[0][name])
    assert ids == saved[1]
    np.testing.assert_array_equal(q, saved[2])
    np.testing.assert_array_equal(labels, saved[3])


def test_transform_uses_game_weighted_population_moments_and_never_refits_at_prediction():
    features, ids, _, _ = sample(7)
    weights, _ = models.game_weights(ids, features["phase_gameday"])
    transformed = models.Standardizer.fit(features, weights, with_availability=True)
    expected, mean, scale = independent_transform(features, models.ALL_FEATURES, ids)
    np.testing.assert_allclose(transformed.mean, mean, atol=2e-14)
    np.testing.assert_allclose(transformed.scale, scale, atol=2e-14)
    np.testing.assert_allclose(transformed.transform(features), expected, atol=2e-14)
    assert abs(transformed.mean[0] - np.mean(features["market_total"])) > .01
    unusual, _, _, _ = sample(2)
    unusual["missing_previous_quote"][:] = 0
    unusual["market_total_change"][:] = 1e100
    before = transformed.mean.copy(), transformed.scale.copy()
    result = transformed.transform(unusual)
    np.testing.assert_array_equal(result[:, models.ALL_FEATURES.index("market_total_change")], 5.)
    np.testing.assert_array_equal(transformed.mean, before[0])
    np.testing.assert_array_equal(transformed.scale, before[1])


def test_exact_decimal_constant_column_is_zero_not_roundoff_amplified():
    features, ids, _, _ = sample(10)
    features["qb_out_burden"][:] = .1
    features["qb_out_burden_change"][:] = 0.
    weights, _ = models.game_weights(ids, features["phase_gameday"])
    result = models.Standardizer.fit(features, weights, with_availability=True)
    col = models.ALL_FEATURES.index("qb_out_burden")
    assert result.mean[col] == .1 and result.scale[col] == 1.
    np.testing.assert_array_equal(result.transform(features)[:, col], 0.)


def test_zero_availability_signal_reduces_to_reference_without_additional_gate():
    features, ids, q, labels = sample(9)
    features["qb_out_burden"][:] = 0.
    features["qb_out_burden_change"][:] = 0.
    reference = models.fit_probability(features, q, labels, ids, with_availability=False)
    challenger = models.fit_probability(features, q, labels, ids, with_availability=True)
    np.testing.assert_allclose(reference.coefficients, challenger.coefficients[:-2], atol=1e-12)
    np.testing.assert_array_equal(challenger.coefficients[-2:], 0.)
    np.testing.assert_allclose(reference.predict_under(features, q), challenger.predict_under(features, q), atol=1e-12)


@pytest.mark.parametrize("label", [0, 1])
def test_single_class_has_independent_unique_finite_penalized_optimum(label):
    # All predictors are constant, so train-only centering makes every slope
    # exactly zero. Only a penalized intercept remains, with a scalar optimum
    # that can be obtained independently without the model's loss/gradient.
    n = 20
    values = (55.5, np.log1p(48.), 0., 0, 1, 1, .5, 0.)
    features = {name: np.full(n, value) for name, value in zip(models.ALL_FEATURES, values)}
    ids = [str(5000 + i) for i in range(n)]
    q = np.linspace(.2, .8, n)
    labels = np.full(n, label)
    offset = np.log(q / (1 - q))

    def derivative(intercept):
        return np.mean(expit(offset + intercept) - label) + .1 * intercept

    # d²loss/dalpha² is at least .1: the root is unique. The penalty also
    # prevents an intercept running to infinity under complete separation.
    optimum = brentq(derivative, -10., 10., xtol=1e-13)
    assert abs(derivative(optimum)) < 1e-12
    assert (0 < optimum < 10) if label else (-10 < optimum < 0)
    fitted = models.fit_probability(features, q, labels, ids, with_availability=True)
    assert fitted.coefficients[0] == pytest.approx(optimum, abs=1e-7)
    np.testing.assert_array_equal(fitted.coefficients[1:], 0.)
    np.testing.assert_allclose(fitted.predict_under(features, q), expit(offset + optimum), atol=3e-8)
    assert np.isfinite(fitted.coefficients).all()
    assert fitted.training_games == 20


def test_only_one_paired_book_is_needed_and_offset_is_under_not_over():
    features, ids, _, _ = sample(4)
    under, over = np.full(len(ids), 1.8), np.full(len(ids), 2.1)
    q = (1 / under) / (1 / under + 1 / over)
    weights, _ = models.game_weights(ids, features["phase_gameday"])
    transform = models.Standardizer.fit(features, weights, with_availability=True)
    zero = models.ProbabilityModel(transform, np.zeros(9), len(ids), 4, 0)
    np.testing.assert_allclose(zero.predict_under(features, q), q, atol=1e-15)
    assert np.all(q > .5)
    logits = zero.predict_logits(features, q)
    assert models.weighted_log_loss(logits, np.ones(len(ids)), weights) < models.weighted_log_loss(logits, np.zeros(len(ids)), weights)
    # The pure fit accepts one-book q with no peer-book metadata or 20-game guard.
    result = models.fit_probability(features, q, np.arange(len(ids)) % 2, ids, with_availability=True)
    assert result.training_games == 4


def test_extreme_logit_loss_is_stable_and_labels_are_binary_under_indicators():
    assert models.weighted_log_loss([1000., -1000.], [True, False], [1., 1.]) == 0.
    assert models.weighted_log_loss([1000., -1000.], [False, True], [1., 1.]) == 1000.
    with pytest.raises(ValueError, match="binary"):
        models.weighted_log_loss([0., 0.], [.5, 1.], [1., 1.])


def test_reference_cannot_use_availability_values_or_test_labels():
    features, ids, q, labels = sample(8)
    reference = models.fit_probability(features, q, labels, ids, with_availability=False)
    changed = deepcopy(features)
    changed["qb_out_burden"][:] = np.nan
    changed["qb_out_burden_change"][:] = np.inf
    again = models.fit_probability(changed, q, labels, ids, with_availability=False)
    np.testing.assert_array_equal(reference.coefficients, again.coefficients)
    np.testing.assert_array_equal(reference.predict_logits(features, q), reference.predict_logits(changed, q))
    extra = deepcopy(features)
    extra["actual_total"] = np.zeros(len(ids))
    with pytest.raises(ValueError, match="feature"):
        reference.predict_logits(extra, q)


@pytest.mark.parametrize("name,bad", [
    ("market_total", 50.), ("market_total", -.5), ("market_total", True),
    ("log_hours_to_kickoff", -1.), ("qb_out_burden", np.nan),
    ("qb_out_burden", -1.), ("qb_out_burden", 2.1),
    ("phase_gameday", .5), ("missing_previous_quote", 2),
])
def test_malformed_contract_is_not_imputed(name, bad):
    features, ids, q, labels = sample(4)
    features[name] = [bad] * len(ids)
    with pytest.raises(ValueError):
        models.fit_probability(features, q, labels, ids, with_availability=True)


def test_missing_history_keeps_current_burden_but_requires_zero_change_placeholders():
    features, ids, q, labels = sample(4)
    features["missing_previous_report"][:] = 1
    features["missing_previous_quote"][:] = 1
    features["market_total_change"][:] = 0.
    features["qb_out_burden_change"][:] = 0.
    result = models.fit_probability(features, q, labels, ids, with_availability=True)
    assert result.training_rows == len(ids)
    for key in ("market_total_change", "qb_out_burden_change"):
        invalid = deepcopy(features)
        invalid[key][0] = .1
        with pytest.raises(ValueError, match="placeholder"):
            models.fit_probability(invalid, q, labels, ids, with_availability=True)
    invalid = deepcopy(features)
    invalid["missing_previous_report"][:] = 0
    invalid["qb_out_burden"][:] = .1
    invalid["qb_out_burden_change"][:] = 1.
    with pytest.raises(ValueError, match="previous burden"):
        models.fit_probability(invalid, q, labels, ids, with_availability=True)


@pytest.mark.parametrize("q", [0., 1., np.nan, np.inf, True])
def test_invalid_price_reference_is_not_a_probability(q):
    features, ids, _, labels = sample(4)
    with pytest.raises(ValueError):
        models.fit_probability(features, [q] * len(ids), labels, ids, with_availability=False)


@pytest.mark.parametrize("bad", [True, 1., "01", "0", "1.0", "", "١"])
def test_game_identity_is_not_float_rounded_or_silently_canonicalized(bad):
    with pytest.raises(ValueError):
        models.game_weights([bad], [0])


def test_exact_large_ids_remain_distinct_and_duplicate_phase_is_rejected():
    weights, games = models.game_weights([9007199254740992, "9007199254740993"], [0, 0])
    np.testing.assert_array_equal(weights, [1., 1.])
    assert games == 2
    with pytest.raises(ValueError, match="Duplicate"):
        models.game_weights(["10", 10], [0, 0])


def test_optimizer_failure_does_not_return_a_market_fallback(monkeypatch):
    features, ids, q, labels = sample(4)
    monkeypatch.setattr(models, "minimize", lambda *a, **k: SimpleNamespace(success=False))
    with pytest.raises(models.ModelFitError):
        models.fit_probability(features, q, labels, ids, with_availability=True)


def iso(value):
    return value.isoformat().replace("+00:00", "Z")


def split_fixture(games=20, one_week=False):
    cutoff = datetime(2026, 9, 21, 4, tzinfo=timezone.utc)  # Monday midnight EDT.
    train = []
    for i in range(games):
        kickoff = datetime(2026, 9, 5 if one_week or i < 10 else 12, 17, tzinfo=timezone.utc)
        train.append({"game_id": str(1000 + i), "phase": "initial", "kickoff": iso(kickoff),
                      "group_kickoff": iso(kickoff), "final_kickoff": iso(kickoff),
                      "decision_at": iso(kickoff - timedelta(days=3)),
                      "target_available_at": iso(kickoff + timedelta(hours=6))})
    starts = datetime(2026, 9, 26, 17, tzinfo=timezone.utc)
    test = [{"game_id": "2000", "phase": "initial", "kickoff": iso(starts),
             "group_kickoff": iso(starts), "decision_at": iso(starts - timedelta(days=3))}]
    return train, test, iso(cutoff)


def test_twenty_distinct_games_in_two_completed_weeks_pass_without_book_gate():
    train, test, cutoff = split_fixture()
    result = models.validate_chronological_split(train, test, cutoff=cutoff)
    assert (result["training_games"], result["training_weeks"], result["training_rows"]) == (20, 2, 20)
    assert result["testing_games"] == 1
    nineteen, _, _ = split_fixture(19)
    with pytest.raises(ValueError, match="20 distinct"):
        models.validate_chronological_split(nineteen, test, cutoff=cutoff)
    one_week, _, _ = split_fixture(one_week=True)
    with pytest.raises(ValueError, match="two completed"):
        models.validate_chronological_split(one_week, test, cutoff=cutoff)


def test_two_phases_of_one_game_do_not_count_as_an_extra_training_game():
    train, test, cutoff = split_fixture(19)
    other = deepcopy(train[0])
    other.update(phase="gameday", decision_at=iso(models._utc(other["kickoff"]) - timedelta(hours=2)))
    train.append(other)
    with pytest.raises(ValueError, match="20 distinct"):
        models.validate_chronological_split(train, test, cutoff=cutoff)
    full, test, cutoff = split_fixture()
    full.append(other)
    result = models.validate_chronological_split(full, test, cutoff=cutoff)
    assert (result["training_rows"], result["training_games"]) == (21, 20)


def test_duplicate_phase_and_train_test_game_overlap_are_rejected():
    train, test, cutoff = split_fixture()
    with pytest.raises(ValueError, match="Unique"):
        models.validate_chronological_split(train + [deepcopy(train[0])], test, cutoff=cutoff)
    test[0]["game_id"] = train[0]["game_id"]
    with pytest.raises(ValueError, match="both training and testing"):
        models.validate_chronological_split(train, test, cutoff=cutoff)


@pytest.mark.parametrize("seconds", [0, 1])
def test_target_received_at_or_after_cutoff_cannot_train(seconds):
    train, test, cutoff = split_fixture()
    train[0]["target_available_at"] = iso(models._utc(cutoff) + timedelta(seconds=seconds))
    with pytest.raises(ValueError):
        models.validate_chronological_split(train, test, cutoff=cutoff)


def test_fixed_group_anchor_allows_changed_observed_kickoff():
    train, test, cutoff = split_fixture()
    other = deepcopy(train[0])
    other.update(phase="gameday", kickoff=iso(models._utc(other["kickoff"]) + timedelta(hours=2)),
                 decision_at=iso(models._utc(other["final_kickoff"]) - timedelta(hours=1)))
    result = models.validate_chronological_split(train + [other], test, cutoff=cutoff)
    assert result["training_games"] == 20 and result["training_rows"] == 21
    other["group_kickoff"] = other["kickoff"]
    with pytest.raises(ValueError):
        models.validate_chronological_split(train + [other], test, cutoff=cutoff)


@pytest.mark.parametrize("shift_hours", [-24, 24])
def test_final_kickoff_can_move_before_or_after_original_observed_kickoff(shift_hours):
    train, test, cutoff = split_fixture()
    final = models._utc(train[0]["kickoff"]) + timedelta(hours=shift_hours)
    train[0]["final_kickoff"] = iso(final)
    train[0]["target_available_at"] = iso(final + timedelta(hours=6))
    result = models.validate_chronological_split(train, test, cutoff=cutoff)
    assert result["training_games"] == 20


@pytest.mark.parametrize("field", ["group_kickoff", "final_kickoff"])
def test_required_anchor_and_final_identity_times_cannot_be_inferred(field):
    train, test, cutoff = split_fixture()
    del train[0][field]
    with pytest.raises((ValueError, KeyError)):
        models.validate_chronological_split(train, test, cutoff=cutoff)


def test_final_label_cannot_precede_final_kickoff_or_designated_decision():
    train, test, cutoff = split_fixture()
    train[0]["target_available_at"] = train[0]["final_kickoff"]
    with pytest.raises(ValueError):
        models.validate_chronological_split(train, test, cutoff=cutoff)
    train, test, cutoff = split_fixture()
    train[0]["final_kickoff"] = train[0]["decision_at"]
    with pytest.raises(ValueError):
        models.validate_chronological_split(train, test, cutoff=cutoff)


@pytest.mark.parametrize("cutoff", ["2026-09-21T00:00:00Z", "2026-09-21T04:00:01Z", "2026-09-21T04:00:00"])
def test_cutoff_is_exact_monday_midnight_eastern_with_timezone(cutoff):
    train, test, _ = split_fixture()
    with pytest.raises(ValueError):
        models.validate_chronological_split(train, test, cutoff=cutoff)


def test_test_decision_cannot_predate_fit_cutoff_or_equal_kickoff():
    train, test, cutoff = split_fixture()
    test[0]["decision_at"] = iso(models._utc(cutoff) - timedelta(seconds=1))
    with pytest.raises(ValueError):
        models.validate_chronological_split(train, test, cutoff=cutoff)
    test[0]["decision_at"] = test[0]["kickoff"]
    with pytest.raises(ValueError):
        models.validate_chronological_split(train, test, cutoff=cutoff)
