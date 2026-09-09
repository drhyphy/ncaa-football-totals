"""Synthetic state/temporal/weight invariants; no historical files are read."""
import json

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import Ridge

from ncaaf_model import pbp_state_ratings as ratings


def games():
    return pd.DataFrame([dict(game_id=900, season=2025, week=3,
        game_date='2025-09-13T18:00:00Z', home_id=1, away_id=2)])


def rows():
    observations = []
    for game_id, season, week, at, teams in [
        (100, 2024, 12, '2024-11-17T00:00:00Z', (1, 3)),
        (101, 2025, 2, '2025-09-07T00:00:00Z', (1, 4)),
        (102, 2025, 2, '2025-09-07T01:00:00Z', (2, 3)),
    ]:
        for team, opponent, home in [(teams[0], teams[1], 1), (teams[1], teams[0], 0)]:
            for play in range(8 if team == 1 else 5):
                observations.append(dict(game_id=game_id, season=season, week=week,
                    team_id=team, opponent_id=opponent, available_at=at, is_home=home,
                    neutral_site=game_id == 100, down=1+play % 4, distance=2+play,
                    yards_to_endzone=20+7*play, score_margin=team*4-8,
                    period=1+play % 4, half_seconds_remaining=1500-play*50,
                    pass_play=play % 2, clock_seconds=10+team*3+play,
                    conversion=float((play+team) % 3 == 0), play_id=f'{team}-{play}'))
    return pd.DataFrame(observations)


def feature_values(query=None, history=None, **kwargs):
    return ratings.query_pbp_features(games() if query is None else query,
        rows() if history is None else history, **kwargs)[0][ratings.FEATURES]


def weight_records(evidence):
    assert evidence['team_game_weights_format'] == 'aligned_columns_v1'
    data = evidence['team_game_weights']
    return [dict(zip(data, values)) for values in zip(*data.values())]


def test_fixed_basis_hand_calculation_and_unknown_pass_is_not_rush():
    train = rows().iloc[[0, 1, 2]].copy()
    train['down'] = [1, 2, 4]
    train['distance'] = [1, 20, 100]
    train['yards_to_endzone'] = [100, 50, 1]
    train['score_margin'] = [-100, 14, 100]
    train['half_seconds_remaining'] = [1800, 900, 0]
    train['period'] = [1, 2, 4]
    train['pass_play'] = [0., 1., np.nan]
    train['season'] = [2022, 2023, 2024]
    x = pd.DataFrame(ratings._state_basis(train), columns=ratings.STATE_COLUMNS)
    assert len(ratings.STATE_COLUMNS) == 18
    np.testing.assert_allclose(x.log_distance, np.log1p([1, 20, 100])/np.log(101))
    np.testing.assert_allclose(x.distance_capped20, [.05, 1, 1])
    np.testing.assert_allclose(x.yards_to_endzone_squared, [1, .25, .0001])
    np.testing.assert_allclose(x.score_margin, [-1, .5, 1])
    np.testing.assert_allclose(x.absolute_score_margin, [1, .5, 1])
    np.testing.assert_allclose(x.half_seconds_squared, [1, .25, 0])
    np.testing.assert_allclose(x[['pass_play', 'pass_unknown']], [[0, 0], [1, 0], [0, 1]])
    np.testing.assert_allclose(x[['clock_rule_2023', 'two_minute_rule_2024']], [[0, 0], [1, 0], [1, 1]])


def test_fit_matches_independent_dense_ridge_and_contributions_exclude_state_intercept_home():
    history = rows()
    features, metadata = ratings.query_pbp_features(games(), history)
    for response, label in ratings.RESPONSES.items():
        evidence = metadata['cutoff_fits'][0]['responses'][response]
        teams = sorted(set(history.team_id) | set(history.opponent_id))
        mapping = {t: i for i, t in enumerate(teams)}
        x = np.zeros((len(history), 2*len(teams)))
        for i, row in history.iterrows():
            x[i, mapping[row.team_id]] = 1
            x[i, len(teams)+mapping[row.opponent_id]] = 1
        # Synthetic basis values are checked separately against hand calculations.
        x = np.column_stack([x, history.is_home*(1-history.neutral_site), ratings._state_basis(history)])
        count = history.groupby(['game_id', 'team_id']).game_id.transform('size')
        years = 2025-history.season
        weights = .92**np.maximum(0, years*18+3-history.week) * .65**years/count
        model = Ridge(alpha=4., fit_intercept=True, solver='lsqr', tol=1e-7).fit(
            x, history[response], sample_weight=weights)
        np.testing.assert_allclose(evidence['coefficients'], model.coef_, atol=1e-8)
        expected = .5*sum(model.coef_[mapping[t]+offset] for t in [1, 2] for offset in [0, len(teams)])
        assert features[f'pbp_{label}_rating'].iloc[0] == pytest.approx(expected, abs=1e-8)
        assert evidence['intercept'] == pytest.approx(model.intercept_)
        assert evidence['fit_iterations'] == model.n_iter_.tolist()
    json.dumps(metadata, allow_nan=False)


def test_response_weights_sum_to_declared_team_game_decay_despite_play_count():
    history = rows()
    history.loc[history.index[:3], 'clock_seconds'] = np.nan
    _, metadata = ratings.query_pbp_features(games(), history)
    responses = metadata['cutoff_fits'][0]['responses']
    assert responses['clock_seconds']['training_rows'] == responses['conversion']['training_rows']-3
    for evidence in metadata['cutoff_fits'][0]['responses'].values():
        for record in weight_records(evidence):
            years = 2025-record['season']
            expected = .92**max(0, years*18+3-record['week']) * .65**years
            assert record['weight_sum'] == pytest.approx(expected)
            assert record['row_weight']*record['eligible_rows'] == pytest.approx(expected)
        assert evidence['weight_sum'] == pytest.approx(sum(r['weight_sum'] for r in weight_records(evidence)))


def test_columnar_weight_evidence_exactly_reconstructs_original_group_records():
    history = rows()
    for response in ratings.RESPONSES:
        eligible = history.loc[history[response].notna()]
        weights = ratings._response_weights(eligible, 2025, 3)
        original = []
        for (game_id, team_id), group in eligible.assign(_weight=weights).groupby(['game_id', 'team_id'], sort=True):
            original.append(dict(game_id=int(game_id), team_id=int(team_id),
                season=int(group.season.iloc[0]), week=int(group.week.iloc[0]),
                eligible_rows=len(group), weight_sum=float(group._weight.sum()),
                row_weight=float(group._weight.iloc[0])))
        compact = ratings._weight_evidence(eligible, weights)
        assert [dict(zip(compact, values)) for values in zip(*compact.values())] == original


def test_grouped_coverage_matches_original_per_team_queries_with_missing_responses():
    history = rows()
    history.loc[history.index[:3], 'clock_seconds'] = np.nan
    for response in ratings.RESPONSES:
        eligible = history.loc[history[response].notna()]
        grouped = ratings._coverage_by_team(eligible)
        for team in sorted(set(eligible.team_id) | set(eligible.opponent_id)):
            expected = {}
            for role, column in (('offense', 'team_id'), ('defense', 'opponent_id')):
                subset = eligible.loc[eligible[column].eq(team)]
                expected[role+'_rows'] = len(subset)
                expected[role+'_games'] = int(subset.game_id.nunique())
            assert grouped[team] == expected


def test_future_rows_at_cutoff_and_unused_outcome_poison_cannot_change_earlier_fit():
    query, history = games(), rows()
    expected, evidence = ratings.query_pbp_features(query, history)
    poison = history.iloc[[0]].copy()
    poison['available_at'] = '2025-09-08T04:00:00Z'  # equal is excluded
    poison['season'] = 2099
    poison['clock_seconds'] = poison['conversion'] = poison['distance'] = np.inf
    later = poison.copy()
    later['available_at'] = '2099-01-01T00:00:00Z'
    history = pd.concat([history, poison, later], ignore_index=True)
    query['actual_total'] = -999999
    for column in ('actual_total', 'home_score', 'away_score', 'EPA', 'wpa', 'winner'):
        history[column] = np.inf
    actual, after_evidence = ratings.query_pbp_features(query, history)
    pd.testing.assert_frame_equal(expected[ratings.FEATURES], actual[ratings.FEATURES])
    assert evidence == after_evidence


def test_same_week_queries_and_as_of_cutoff_are_equivalent():
    query = pd.concat([games(), games().assign(game_id=901, game_date='2025-09-14T18:00:00Z')], ignore_index=True)
    historical = feature_values(query)
    pd.testing.assert_frame_equal(historical, feature_values(query, as_of='2025-09-08T04:00:00Z'))
    for i in query.index:
        np.testing.assert_allclose(historical.iloc[i], feature_values(query.iloc[[i]]).iloc[0])
    _, meta = ratings.query_pbp_features(query, rows())
    assert len(meta['cutoff_fits']) == 1
    assert meta['cutoff_fits'][0]['cutoff'] == '2025-09-08T04:00:00+00:00'


def test_earlier_as_of_and_exact_three_year_boundary():
    query, history = games(), rows().iloc[[0]].copy()
    cutoff = pd.Timestamp('2025-09-06T00:00:00Z')
    history['season'] = 2022
    history['available_at'] = cutoff-pd.Timedelta(days=3*366)
    _, meta = ratings.query_pbp_features(query, history, as_of=cutoff)
    assert meta['cutoff_fits'][0]['admitted_rows'] == 1
    history['available_at'] -= pd.Timedelta(nanoseconds=1)
    features, meta = ratings.query_pbp_features(query, history, as_of=cutoff)
    assert meta['cutoff_fits'][0]['admitted_rows'] == 0
    assert (features[ratings.FEATURES] == 0).all().all()


def test_team_swap_is_symmetric_and_query_home_neutral_terms_are_excluded():
    original = feature_values()
    swapped = games().assign(home_id=2, away_id=1, neutral_site=True, home_score=1000)
    pd.testing.assert_frame_equal(original, feature_values(swapped))


def test_missing_teams_empty_history_and_empty_queries_return_zero():
    absent = games().assign(home_id=990, away_id=991)
    assert (feature_values(absent) == 0).all().all()
    out, meta = ratings.query_pbp_features(games(), pd.DataFrame())
    assert (out[ratings.FEATURES] == 0).all().all()
    assert meta['cutoff_fits'][0]['responses']['clock_seconds']['status'] == 'no_history'
    assert out.pbp_clock_history_games.iloc[0] == 0
    empty, metadata = ratings.query_pbp_features(games().iloc[:0], rows())
    assert empty.empty and not metadata['cutoff_fits']


def test_one_unknown_team_zeroes_only_its_contributions():
    _, meta = ratings.query_pbp_features(games(), rows())
    out, _ = ratings.query_pbp_features(games().assign(away_id=999), rows())
    for response, label in ratings.RESPONSES.items():
        fit = meta['cutoff_fits'][0]['responses'][response]
        idx = fit['team_map']['1']
        expected = .5*(fit['coefficients'][idx]+fit['coefficients'][len(fit['team_map'])+idx])
        assert out[f'pbp_{label}_rating'].iloc[0] == pytest.approx(expected)


def test_separate_response_missingness_changes_only_its_fit_and_weights():
    history = rows()
    before, original = ratings.query_pbp_features(games(), history)
    history.loc[history.team_id.eq(1), 'clock_seconds'] = np.nan
    history.loc[history.index[0], 'pass_play'] = np.nan
    # The unknown indicator must retain the observation, not discard it.
    _, with_unknown = ratings.query_pbp_features(games(), history)
    assert with_unknown['cutoff_fits'][0]['responses']['conversion']['training_rows'] == len(history)
    history.loc[history.index[0], 'pass_play'] = rows().pass_play.iloc[0]
    after, modified = ratings.query_pbp_features(games(), history)
    assert original['cutoff_fits'][0]['responses']['conversion'] == modified['cutoff_fits'][0]['responses']['conversion']
    assert before.pbp_conversion_rating.iloc[0] == after.pbp_conversion_rating.iloc[0]
    assert modified['cutoff_fits'][0]['responses']['clock_seconds']['training_rows'] == int(history.clock_seconds.notna().sum())
    assert modified['game_coverage'][0]['responses']['clock_seconds']['home']['offense_rows'] == 0
    history['clock_seconds'] = np.nan
    out, _ = ratings.query_pbp_features(games(), history)
    assert out.pbp_clock_rating.iloc[0] == 0


@pytest.mark.parametrize('column,value', [
    ('down', 0), ('down', 1.5), ('distance', 0), ('distance', 101),
    ('yards_to_endzone', 0), ('yards_to_endzone', 101), ('period', 5),
    ('half_seconds_remaining', -1), ('half_seconds_remaining', 1801),
    ('clock_seconds', -1), ('clock_seconds', 61), ('clock_seconds', np.inf),
    ('conversion', .5), ('conversion', np.inf), ('score_margin', np.nan),
    ('pass_play', 2), ('neutral_site', np.nan), ('is_home', 'false'),
    ('season', 2026), ('available_at', None),
])
def test_invalid_admitted_rows_fail_closed(column, value):
    history = rows()
    history[column] = history[column].astype(object)
    history.loc[0, column] = value
    with pytest.raises(ValueError):
        ratings.query_pbp_features(games(), history)


def test_duplicate_play_ids_and_query_self_history_are_rejected():
    history = rows()
    with pytest.raises(ValueError, match='Duplicate play_id'):
        ratings.query_pbp_features(games(), pd.concat([history, history.iloc[[0]]], ignore_index=True))
    history.loc[0, 'game_id'] = 900
    with pytest.raises(ValueError, match='Query game'):
        ratings.query_pbp_features(games(), history)


def test_inputs_not_mutated_and_numerical_id_strings_equivalent():
    query, history = games(), rows()
    qcopy, hcopy = query.copy(deep=True), history.copy(deep=True)
    expected = feature_values(query, history)
    pd.testing.assert_frame_equal(query, qcopy)
    pd.testing.assert_frame_equal(history, hcopy)
    for frame, columns in ((query, ('game_id', 'home_id', 'away_id', 'season', 'week')),
                           (history, ('game_id', 'team_id', 'opponent_id', 'season', 'week'))):
        for column in columns:
            frame[column] = frame[column].astype(str)
    pd.testing.assert_frame_equal(expected, feature_values(query, history))


def test_exact_int64_ids_above_float_precision_preserve_integrated_features_and_identity():
    query, history = games(), rows()
    expected = feature_values(query, history)
    offset = 2**53+41
    for frame, columns in ((query, ('game_id', 'home_id', 'away_id')),
                           (history, ('game_id', 'team_id', 'opponent_id'))):
        for column in columns:
            frame[column] = (frame[column]+offset).astype('Int64')
    actual, metadata = ratings.query_pbp_features(query, history)
    pd.testing.assert_frame_equal(expected, actual[ratings.FEATURES])
    assert actual.game_id.iloc[0] == 900+offset
    assert metadata['game_coverage'][0]['game_id'] == 900+offset
    for fit in metadata['cutoff_fits'][0]['responses'].values():
        assert sorted(map(int, fit['team_map'])) == [offset+t for t in (1,2,3,4)]
        assert set(fit['team_game_weights']['game_id']) == {offset+g for g in (100,101,102)}
    for frame, columns in ((query, ('game_id', 'home_id', 'away_id')),
                           (history, ('game_id', 'team_id', 'opponent_id'))):
        for column in columns:
            frame[column] = frame[column].astype(str)
    pd.testing.assert_frame_equal(expected, feature_values(query, history))


@pytest.mark.parametrize('column,source', [('game_id','history'), ('team_id','history'),
    ('opponent_id','history'), ('game_id','query'), ('home_id','query'), ('away_id','query')])
def test_floating_canonical_ids_are_rejected_even_when_small_and_integral(column, source):
    query, history = games(), rows()
    target = query if source == 'query' else history
    target[column] = target[column].astype(float)
    with pytest.raises(ValueError, match='floating IDs'):
        ratings.query_pbp_features(query, history)


def test_id_validation_accepts_int64_maximum_but_rejects_overflow_or_missing():
    query = games().assign(game_id=np.iinfo(np.int64).max)
    result, _ = ratings.query_pbp_features(query, rows())
    assert result.game_id.iloc[0] == np.iinfo(np.int64).max
    for invalid in (str(2**63), None, 0, True):
        query['game_id'] = pd.Series([invalid], dtype=object)
        with pytest.raises(ValueError, match='game_id'):
            ratings.query_pbp_features(query, rows())
