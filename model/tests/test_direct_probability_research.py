"""Synthetic chronology, honest selection and scoring tests; no real outcomes."""
from copy import deepcopy
import json

import numpy as np
import pandas as pd
import pytest
from scipy.special import expit

from ncaaf_model import direct_probability_research as research
from ncaaf_model.opponent_model import FEATURES


def games(year, n=120):
    rng = np.random.default_rng(year)
    f = pd.DataFrame(rng.normal(size=(n, len(FEATURES))), columns=FEATURES)
    f['market_total'] = rng.integers(40, 65, n) + .5
    f['abs_spread'] = rng.integers(0, 30, n)
    f['week'] = np.arange(n) % 12 + 1
    f['clock_rule_2023'], f['two_minute_rule_2024'] = float(year >= 2023), float(year >= 2024)
    y = (f.adjusted_score_minus_market < 0).to_numpy()
    f['actual_total'] = np.where(y, np.floor(f.market_total)-7, np.ceil(f.market_total)+7)
    f['projected_total'] = f.market_total + 2*f.adjusted_score_minus_market
    f['residual_sigma'] = 16.
    f['adjusted_history_games'] = 5
    f['game_id'], f['season'] = year*1000+np.arange(n), year
    f['game_date'] = pd.Timestamp(f'{year}-09-01T16:00:00Z')+pd.to_timedelta(np.arange(n) % 70, unit='D')
    f['market_source'] = 'synthetic'
    return f


def forecast(year, n=20):
    f = games(year, n)
    f['label_under'] = f.actual_total.lt(f.market_total).astype(int)
    for name in research.CONFIGS:
        f[name + '_logit'] = 0.
    f['opponent_logit_logit'] = np.where(f.label_under.eq(1), .6, -.6)
    return f


def test_future_outcomes_and_postgame_poison_cannot_change_any_forecast():
    train, test = games(2020), games(2021, 12)
    original = deepcopy(train)
    first, metadata = research.fit_fold(train, test)
    test.actual_total = 150 - test.actual_total
    test['unshifted_epa'] = np.inf
    second, _ = research.fit_fold(train, test)
    for name in research.CONFIGS:
        np.testing.assert_array_equal(first[name + '_logit'], second[name + '_logit'])
    pd.testing.assert_frame_equal(train, original)
    assert metadata['training_seasons'] == [2020]
    assert all(x['training_rows'] == 120 for x in metadata['models'].values())
    assert not first.label_under.equals(second.label_under)


@pytest.mark.parametrize('violation', ['same_season', 'overlapping_game', 'integer_line'])
def test_fitting_refuses_leakage_or_changed_contract(violation):
    train, test = games(2020), games(2021, 10)
    if violation == 'same_season':
        test['season'] = 2020
    elif violation == 'overlapping_game':
        test.loc[0, 'game_id'] = train.game_id.iloc[0]
    else:
        test.loc[0, 'market_total'] = 50.
    with pytest.raises(ValueError):
        research.fit_fold(train, test)


def test_under_label_and_stable_log_loss_preserve_extreme_errors():
    f = forecast(2021, 3)
    f['label_under'] = [0, 1, 1]
    f['raw50_logit'] = [1000., -1000., 0.]
    loss, brier = research.metric_arrays(f, 'raw50')
    np.testing.assert_allclose(loss, [1000., 1000., np.log(2)])
    np.testing.assert_allclose(brier, [1., 1., .25])
    f['raw50_logit'] = np.log(.8/.2)
    f['label_under'] = 1
    assert research.metrics(f, 'raw50')['log_loss'] == pytest.approx(-np.log(.8))


def test_reliability_retains_empty_bins_and_includes_probability_one():
    f = forecast(2021, 3)
    f['raw50_logit'] = [-1000., 0., 1000.]
    r = research.metrics(f, 'raw50')['reliability']
    assert len(r) == 10 and sum(x['games'] for x in r) == 3
    assert r[0]['games'] == r[5]['games'] == r[-1]['games'] == 1
    assert r[1]['games'] == 0 and r[1]['observed_under_rate'] is None


def test_week_bootstrap_uses_ratio_of_sums_with_exact_fixed_quantiles():
    f = forecast(2021, 4)
    f['game_date'] = pd.to_datetime(['2021-09-04T22:00Z', *['2021-09-11T22:00Z']*3])
    first = research.metric_arrays(f, 'opponent_logit')[0]
    second = research.metric_arrays(f, 'raw50')[0]
    f.loc[0, 'opponent_logit_logit'] = -f.loc[0, 'opponent_logit_logit']
    d = research.metric_arrays(f, 'opponent_logit')[0]-second
    blocks = np.array([[d[0], 1], [d[1:].sum(), 3]])
    rng = np.random.Generator(np.random.PCG64(research.SEED))
    sums = blocks[rng.integers(0, 2, (10000, 2))].sum(axis=1)
    sampled = sums[:, 0]/sums[:, 1]
    actual = research.paired(f, 'opponent_logit', 'raw50')
    assert actual['log_loss_difference'] == pytest.approx(d.mean())
    np.testing.assert_array_equal(actual['interval_95'], np.quantile(sampled, [.025, .975], method='linear'))
    np.testing.assert_array_equal(actual['interval_98_75'], np.quantile(sampled, [.00625, .99375], method='linear'))
    f['game_date'] = pd.Timestamp('2021-09-04T22:00Z')
    assert research.paired(f, 'opponent_logit', 'raw50')['interval_95'] is None


def test_selection_cannot_include_2025_or_choose_by_one_favorable_year():
    f = pd.concat([forecast(y) for y in research.SELECTION_YEARS], ignore_index=True)
    assert research.choose(f) == 'opponent_logit'
    for name in research.CONFIGS:
        f[name + '_logit'] = 0.
    assert research.choose(f) == 'raw50'
    with pytest.raises(ValueError, match='without 2025'):
        research.choose(pd.concat([f, forecast(2025)], ignore_index=True))


def test_coverage_exclusions_do_not_double_count_or_use_outcomes():
    f = games(2020, 4)
    f['adjusted_history_games'] = [0, 5, 5, 5]
    f['market_total'] = [50., 50., 50.5, 51.5]
    before = research.coverage(f)
    f['actual_total'] = np.nan
    assert research.coverage(f) == before
    c = before['total']
    assert c == {'base_games': 4, 'history_eligible': 3, 'excluded_history': 1,
                 'excluded_nonhalfpoint_after_history': 1, 'eligible_halfpoint_games': 2}
    assert c['base_games'] == c['excluded_history']+c['excluded_nonhalfpoint_after_history']+c['eligible_halfpoint_games']


def test_selection_is_frozen_before_any_new_2025_prediction(tmp_path, monkeypatch):
    frame = pd.concat([games(y) for y in range(2020, 2026)], ignore_index=True)
    provenance = {'plan_sha256': 'a'*64}
    monkeypatch.setattr(research, 'verify_plan', lambda root: ({}, provenance))
    monkeypatch.setattr(research, 'load_inputs', lambda root: (frame, {}))
    visited = []
    def fold(train, test):
        year = int(test.season.iloc[0])
        visited.append(year)
        assert train.season.max() < year < 2025
        return forecast(year), {'synthetic': True}
    monkeypatch.setattr(research, 'fit_fold', fold)
    selected = research.select(tmp_path)
    assert visited == [2021, 2022, 2023, 2024]
    assert selected['2025_metrics_computed'] is False
    assert selected['selected_configuration'] == 'opponent_logit'
    recorded = (tmp_path / research.SELECTION).read_bytes()
    with pytest.raises(ValueError, match='already frozen'):
        research.select(tmp_path)
    assert (tmp_path / research.SELECTION).read_bytes() == recorded


def test_changed_selection_prediction_bytes_block_the_later_check(tmp_path, monkeypatch):
    f = pd.concat([forecast(y) for y in research.SELECTION_YEARS], ignore_index=True)
    predictions = research.save_predictions(tmp_path, 'selection', f)
    choice = {'phase': 'selection', '2025_metrics_computed': False, 'provenance': {'plan_sha256': 'x'},
              'selected_configuration': 'opponent_logit', 'predictions': predictions}
    research.immutable_json(tmp_path / research.SELECTION, choice)
    (tmp_path / predictions['path']).write_text('altered')
    monkeypatch.setattr(research, 'verify_plan', lambda root: ({}, {'plan_sha256': 'x'}))
    monkeypatch.setattr(research, 'load_inputs', lambda root: pytest.fail('2025 data must not be loaded'))
    with pytest.raises(ValueError, match='predictions changed'):
        research.check(tmp_path)
