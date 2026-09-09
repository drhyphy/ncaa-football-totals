"""Synthetic numerical and temporal checks; no archived weather or outcomes."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.optimize import minimize

from ncaaf_model import weather_revision_models as models


def features(n=360, seed=321):
    rng = np.random.default_rng(seed)
    result = {name: rng.normal(size=n) for name in models.ALL_FEATURES}
    result['market_total'] = rng.integers(30, 80, size=n) + .5
    return result


def test_exact_fixed_feature_blocks_and_penalty():
    assert len(models.BASE_FEATURES) == 8
    assert models.REVISION_FEATURES == ('wind_revision_mph', 'temperature_revision_f', 'humidity_revision_percent')
    assert models.PENALTY == .1 and models.CLIP == 5


def test_offset_loss_gradient_matches_independent_central_differences():
    rng = np.random.default_rng(72)
    x = rng.normal(size=(101, 11))
    theta = rng.normal(size=12) / 4
    q = rng.uniform(.2, .8, size=len(x))
    y = rng.integers(0, 2, size=len(x))
    objective, gradient = models.offset_log_loss_gradient(theta, x, q, y)
    eta = np.log(q/(1-q)) + theta[0] + x @ theta[1:]
    losses = np.where(y == 1, np.log1p(np.exp(-eta)), np.log1p(np.exp(eta)))
    assert objective == pytest.approx(losses.mean() + .05 * np.dot(theta, theta))
    step = 1e-6
    numerical = []
    for i in range(len(theta)):
        delta = np.eye(len(theta))[i] * step
        plus = models.offset_log_loss_gradient(theta + delta, x, q, y)[0]
        minus = models.offset_log_loss_gradient(theta - delta, x, q, y)[0]
        numerical.append((plus - minus)/(2*step))
    np.testing.assert_allclose(gradient, numerical, atol=3e-9, rtol=1e-7)


def test_logloss_stays_finite_for_extreme_logits_and_boolean_labels():
    assert models.binary_log_loss_from_logits([1000., -1000.], [True, False]) == 0
    assert models.binary_log_loss_from_logits([1000., -1000.], [False, True]) == 1000
    with pytest.raises(ValueError, match='binary'):
        models.binary_log_loss_from_logits([0., 0.], [1., .5])


def test_zero_coefficients_keep_price_offset_exactly_one():
    f = features(30)
    transform = models.Standardizer.fit(f, with_revisions=True)
    candidate = models.ProbabilityModel(transform, np.zeros(12), 30, 0)
    q = np.linspace(.1, .9, 30)
    np.testing.assert_allclose(candidate.predict_under(f, q), q, rtol=1e-14, atol=1e-14)


def test_probability_fit_has_stationary_penalized_objective_and_is_deterministic():
    f = features()
    q = np.linspace(.3, .7, 360)
    y = np.random.default_rng(143).integers(0, 2, 360)
    a = models.fit_probability(f, q, y, with_revisions=True)
    b = models.fit_probability(f, q, y, with_revisions=True)
    np.testing.assert_array_equal(a.coefficients, b.coefficients)
    x = a.standardizer.transform(f)
    objective, gradient = models.offset_log_loss_gradient(a.coefficients, x, q, y)
    initial = models.offset_log_loss_gradient(np.zeros(12), x, q, y)[0]
    assert objective <= initial and np.max(np.abs(gradient)) < 1e-5
    assert np.isfinite(a.predict_under(f, q)).all()
    assert not a.coefficients.flags.writeable


def test_reference_cannot_accidentally_use_revision_features():
    f = features(100)
    q = np.full(100, .5)
    y = np.random.default_rng(98).integers(0, 2, 100)
    original = models.fit_probability(f, q, y, with_revisions=False)
    changed = deepcopy(f)
    for name in models.REVISION_FEATURES:
        changed[name] *= 1000
    refitted = models.fit_probability(changed, q, y, with_revisions=False)
    np.testing.assert_array_equal(original.coefficients, refitted.coefficients)
    np.testing.assert_array_equal(original.predict_under(f, q), original.predict_under(changed, q))
    assert original.standardizer.names == models.BASE_FEATURES


def test_train_only_population_transform_constant_scale_clipping_and_input_immutability():
    train = features(25)
    train['temperature_f'][:] = 67.
    before = deepcopy(train)
    transform = models.Standardizer.fit(train, with_revisions=True)
    for i, name in enumerate(models.ALL_FEATURES):
        assert transform.mean[i] == pytest.approx(train[name].mean())
        expected = train[name].std(ddof=0)
        assert transform.scale[i] == pytest.approx(expected if expected else 1.)
    test = features(2, 775)
    test['wind_mph'] = np.array([-1e100, 1e100])
    mean, scale = transform.mean.copy(), transform.scale.copy()
    transformed = transform.transform(test)
    wind = models.ALL_FEATURES.index('wind_mph')
    np.testing.assert_array_equal(transformed[:, wind], [-5., 5.])
    np.testing.assert_array_equal(transform.mean, mean)
    np.testing.assert_array_equal(transform.scale, scale)
    for key in train:
        np.testing.assert_array_equal(train[key], before[key])


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), None, True, '10'])
def test_no_imputation_of_missing_or_non_numeric_features(bad):
    f = features(10)
    f['wind_mph'] = [bad] * 10
    with pytest.raises(ValueError):
        models.Standardizer.fit(f, with_revisions=True)


def test_unprescribed_or_missing_feature_cannot_sneak_into_fit():
    f = features(10)
    f['final_score'] = np.zeros(10)
    with pytest.raises(ValueError, match='feature'):
        models.Standardizer.fit(f, with_revisions=True)
    del f['final_score']
    del f['wind_revision_mph']
    with pytest.raises(ValueError, match='feature'):
        models.Standardizer.fit(f, with_revisions=True)


def test_optimizer_failure_is_unavailable_not_price_reference_fallback(monkeypatch):
    monkeypatch.setattr(models, 'minimize', lambda *a, **k: SimpleNamespace(success=False))
    with pytest.raises(models.ModelFitError):
        models.fit_probability(features(10), np.full(10, .5), np.arange(10) % 2, with_revisions=True)


def test_probability_fit_and_prediction_reject_push_contracts():
    valid = features(30)
    q, y = np.full(30, .5), np.arange(30) % 2
    candidate = models.fit_probability(valid, q, y, with_revisions=True)
    invalid = deepcopy(valid)
    invalid['market_total'][0] = 50.
    with pytest.raises(ValueError, match='half-point'):
        models.fit_probability(invalid, q, y, with_revisions=True)
    with pytest.raises(ValueError, match='half-point'):
        candidate.predict_under(invalid, q)
    # The different movement target can legitimately include integer lines.
    movement = models.fit_movement(invalid, np.linspace(-1, 1, 30), with_revisions=True)
    assert np.isfinite(movement.predict(invalid)).all()


def test_movement_fit_matches_independent_objective_minimization_with_unpenalized_intercept():
    f = features(180)
    # Deliberately clipped asymmetric training values make transformed mean nonzero.
    f['wind_mph'][0] = 1e7
    y = 2.3 + .7*f['wind_revision_mph'] - .15*f['market_total_change']
    candidate = models.fit_movement(f, y, with_revisions=True)
    x = candidate.standardizer.transform(f)
    normalized = (y-y.mean())/y.std(ddof=0)
    def objective(theta):
        errors = theta[0] + x @ theta[1:] - normalized
        return np.mean(errors**2) + .05*np.sum(theta[1:]**2)
    independently = minimize(objective, np.zeros(12), method='BFGS', options={'gtol':1e-8})
    assert objective(candidate.coefficients) == pytest.approx(independently.fun, abs=1e-10)
    np.testing.assert_allclose(candidate.coefficients, independently.x, atol=1e-6)
    assert np.mean(candidate.predict(f) - y) == pytest.approx(0., abs=1e-12)
    assert candidate.target_scale == pytest.approx(y.std(ddof=0))


def test_constant_movement_target_returns_training_mean_on_new_features():
    candidate = models.fit_movement(features(50), np.full(50, 1.75), with_revisions=False)
    assert candidate.target_scale == 0
    np.testing.assert_array_equal(candidate.predict(features(17, 982)), np.full(17, 1.75))


def test_movement_prediction_does_not_refit_transform_or_target_scale():
    f = features(75)
    candidate = models.fit_movement(f, np.linspace(-2, 2, 75), with_revisions=True)
    before = candidate.coefficients.copy(), candidate.standardizer.mean.copy(), candidate.target_scale
    unusual = features(10, 831)
    unusual['wind_revision_mph'][:] = 1e100
    candidate.predict(unusual)
    np.testing.assert_array_equal(candidate.coefficients, before[0])
    np.testing.assert_array_equal(candidate.standardizer.mean, before[1])
    assert candidate.target_scale == before[2]


def test_price_reference_and_ev_use_correct_side_and_actual_payout():
    q = models.proportional_under_probability([2., 1.91], [1.8, 1.91])
    assert q[0] == pytest.approx((1/1.8)/(1/1.8+1/2))
    assert q[1] == .5
    result = models.same_line_ev(.56, reference_line=50.5, offered_line=50.5,
                                over_decimal=2.03, under_decimal=1.94)
    assert result['under_ev'] == pytest.approx(.56*.94 - .44)
    assert result['over_ev'] == pytest.approx(.44*1.03 - .56)
    assert result['push_probability'] == 0


@pytest.mark.parametrize('change', [
    {'reference_line':50.,'offered_line':50.}, {'offered_line':51.5}, {'reference_line':50.25},
    {'p_under':1.01}, {'p_under':float('nan')}, {'under_decimal':1.},
    {'over_decimal':float('inf')}, {'offered_line':True},
])
def test_ev_rejects_other_lines_push_contracts_and_invalid_numbers(change):
    values = dict(p_under=.55, reference_line=50.5, offered_line=50.5,
                  over_decimal=1.91, under_decimal=1.91)
    values.update(change)
    with pytest.raises(ValueError):
        models.same_line_ev(**values)


def metadata(kind='final_total'):
    first = datetime(2025,9,6,18,tzinfo=timezone.utc)
    training = []
    for week in range(12):
        for i in range(25):
            kickoff = first + timedelta(weeks=week, minutes=i)
            decision = kickoff - timedelta(hours=36)
            target = kickoff + timedelta(hours=6) if kind == 'final_total' else decision + timedelta(hours=6)
            training.append(dict(game_id=str(1+len(training)), kickoff=kickoff,
                                 decision_at=decision, target_available_at=target))
    cutoff = datetime(2025,12,1,tzinfo=timezone.utc)
    kickoff = datetime(2025,12,6,18,tzinfo=timezone.utc)
    testing = [dict(game_id='1000', kickoff=kickoff, decision_at=kickoff-timedelta(hours=36))]
    return training, testing, cutoff


@pytest.mark.parametrize('kind', ['final_total','market_movement'])
def test_metadata_guard_accepts_only_earlier_completed_week_training(kind):
    train, test, cutoff = metadata(kind)
    result = models.validate_chronological_split(train, test, cutoff=cutoff, target_kind=kind)
    assert result['training_games'] == 300 and result['training_weeks'] == 12
    assert result['testing_games'] == 1


@pytest.mark.parametrize('mutation,match', [
    ('cross_game','Game crosses'),('duplicate','Repeated game'),('late_target','strictly before'),
    ('test_before_cutoff','predates'),('naive','Timezone-aware'),('too_few','at least 300'),
    ('final_before_kickoff','cannot precede'),('pregame','pregame'),
])
def test_metadata_guard_rejects_leakage_and_insufficient_independent_rows(mutation,match):
    train, test, cutoff = metadata()
    if mutation == 'cross_game': test[0]['game_id'] = train[0]['game_id']
    elif mutation == 'duplicate': train[1]['game_id'] = '0001'
    elif mutation == 'late_target': train[0]['target_available_at'] = cutoff
    elif mutation == 'test_before_cutoff': test[0]['decision_at'] = cutoff-timedelta(seconds=1)
    elif mutation == 'naive': train[0]['decision_at'] = '2025-09-04T06:00:00'
    elif mutation == 'too_few': train.pop()
    elif mutation == 'final_before_kickoff': train[0]['target_available_at'] = train[0]['kickoff']-timedelta(hours=1)
    elif mutation == 'pregame': test[0]['decision_at'] = test[0]['kickoff']
    with pytest.raises(ValueError, match=match):
        models.validate_chronological_split(train, test, cutoff=cutoff, target_kind='final_total')


def test_eastern_week_guard_rejects_utc_monday_that_is_still_eastern_sunday():
    train, test, _ = metadata('market_movement')
    cutoff = datetime(2025,11,23,12,tzinfo=timezone.utc)
    test[0]['kickoff'] = datetime(2025,11,24,1,tzinfo=timezone.utc)  # Sunday Eastern
    test[0]['decision_at'] = cutoff
    with pytest.raises(ValueError, match='Whole Eastern weeks'):
        models.validate_chronological_split(train, test, cutoff=cutoff, target_kind='market_movement')


def test_movement_label_cannot_be_postgame_and_training_week_cannot_be_unfinished():
    train, test, cutoff = metadata('market_movement')
    train[0]['target_available_at'] = train[0]['kickoff']
    with pytest.raises(ValueError, match='remain pregame'):
        models.validate_chronological_split(train, test, cutoff=cutoff, target_kind='market_movement')
    train, test, _ = metadata('market_movement')
    with pytest.raises(ValueError, match='weeks must be completed'):
        models.validate_chronological_split(train, test, cutoff=datetime(2025,11,23,12,tzinfo=timezone.utc), target_kind='market_movement')
