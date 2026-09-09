"""Input and temporal invariants; no model fitting or outcome-based selection."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ncaaf_model import ordinary_features_research as ordinary


def prior_game(game_id, kickoff, home_id, away_id, points, season=2025, week=2):
    kickoff = pd.Timestamp(kickoff)
    schedule = dict(game_id=game_id, season=season, week=week, game_date=kickoff,
                    home_id=home_id, away_id=away_id, neutral_site=False, status='STATUS_FINAL')
    rows = []
    for team, opponent, home, score in ((home_id, away_id, True, points[0]),
                                        (away_id, home_id, False, points[1])):
        rows.append(dict(game_id=game_id, team_id=team, opponent_id=opponent,
            season=season, week=week, start_date=kickoff, is_home=home, neutral_site=False,
            available_at=kickoff+pd.Timedelta(hours=6), points_for=score, ppd=score/10,
            drives=10., seconds_per_drive=150., other_points=0., yards_per_pass=7.,
            yards_per_rush=4., passes_rate=.5))
    return rows, schedule


def sources(*extra):
    specs = [
        (101, '2024-11-16T18:00:00Z', 1, 3, (30., 10.), 2024, 12),
        (102, '2025-09-06T18:00:00Z', 1, 4, (50., 20.)),
        (103, '2025-09-06T19:00:00Z', 2, 5, (25., 15.)),
        *extra,
    ]
    rows, schedules = [], []
    for spec in specs:
        pair, schedule = prior_game(*spec)
        rows.extend(pair)
        schedules.append(schedule)
    return pd.DataFrame(rows), pd.DataFrame(schedules)


def query(game_id=900, kickoff='2025-09-13T18:00:00Z'):
    return pd.DataFrame([dict(game_id=game_id, season=2025, week=3, game_date=kickoff,
        home_id=1, away_id=2, home_team='One', away_team='Two', actual_total=61.,
        market_total=50., market_home_spread=-6., neutral_site=False, status='STATUS_FINAL',
        market_source='verified_test_provider', source_verified_pregame=True,
        adjusted_history_games=1)])


def adjusted_cache(games):
    cache = games.copy()
    cache['ratings_cutoff'] = [ordinary.weekly_cutoff(t).isoformat() for t in games.game_date]
    for name, value in [('adjusted_score_total',54.), ('adjusted_drive_total',56.),
                        ('adjusted_clock_total',58.), ('adjusted_pass_yards',14.),
                        ('adjusted_rush_yards',8.), ('adjusted_pass_share',.5)]:
        cache[name] = value
    # Cached deltas and context are intentionally wrong. Only fixed totals are trusted.
    cache['adjusted_score_minus_market'] = 999.
    cache['abs_spread'] = 999.
    cache['home_score'] = 31.
    cache['away_score'] = 30.
    return cache


def feature_frame(games, history, schedules, **kwargs):
    return ordinary.query_features(games, history, schedules, **kwargs)[ordinary.FEATURES]


def test_exact_approved_predictor_allowlist_excludes_target_and_postgame_fields():
    assert len(ordinary.FEATURES) == len(set(ordinary.FEATURES)) == 58
    assert len(ordinary.FORM_FEATURES) == 32
    assert len(ordinary.CONTEXT_FEATURES) == len(ordinary.COUNT_FEATURES) == 10
    assert len(ordinary.ADJUSTED_FEATURES) == 6
    assert not {'actual_total','home_score','away_score','winner','attendance','points_for',
                'ppd','drives','epa','fpi','ratings_cutoff'} & set(ordinary.FEATURES)
    history, schedules = sources()
    games = query()
    before = ordinary.query_features(games,history,schedules)
    games['actual_total'] = -999.
    for column in ('home_score','away_score','winner','attendance','points_for','epa','fpi'):
        games[column] = 999999.
    after = ordinary.query_features(games,history,schedules)
    pd.testing.assert_frame_equal(before[ordinary.FEATURES],after[ordinary.FEATURES])
    assert after.actual_total.iloc[0] == -999.
    assert not {'home_score','away_score','winner','attendance','epa','fpi'} & set(after)


def test_future_outcomes_and_at_cutoff_observations_cannot_change_predictors():
    history, schedules = sources(
        (104,'2025-09-07T22:00:00Z',1,2,(100.,100.)),  # available exactly Monday cutoff
        (900,'2025-09-13T18:00:00Z',1,2,(31.,30.)),
    )
    games = query()
    cutoff = ordinary.weekly_cutoff(games.game_date.iloc[0])
    assert cutoff == pd.Timestamp('2025-09-08T04:00:00Z')
    expected = feature_frame(games,*sources())
    original = feature_frame(games,history,schedules)
    history.loc[history.available_at.ge(cutoff),list(ordinary.METRICS)] = 99999.
    changed = feature_frame(games,history,schedules)
    pd.testing.assert_frame_equal(expected,original)
    pd.testing.assert_frame_equal(original,changed)


def test_historical_and_live_queries_with_same_cutoff_and_batch_are_identical():
    history, schedules = sources()
    games = pd.concat([query(),query(901,'2025-09-14T18:00:00Z')],ignore_index=True)
    cache = adjusted_cache(games)
    historical = feature_frame(games,history,schedules,adjusted_cache=cache)
    live = feature_frame(games,history,schedules,as_of='2025-09-08T04:00:00Z',adjusted_cache=cache)
    pd.testing.assert_frame_equal(historical,live)
    for i in games.index:
        single = feature_frame(games.loc[[i]],history,schedules,adjusted_cache=cache)
        pd.testing.assert_frame_equal(historical.loc[[i]].reset_index(drop=True),single)


def test_hand_computed_prior_weights_shrinkage_and_reciprocal_opponent_rows():
    history, schedules = sources()
    result = ordinary.query_features(query(),history,schedules).iloc[0]
    old_weight = .92*.65
    league_mean = 25.
    expected = (50+old_weight*30+4*league_mean)/(1+old_weight+4)
    opponent = (20+old_weight*10+4*league_mean)/(1+old_weight+4)
    assert result.home_prior_own_points_for == pytest.approx(expected)
    assert result.home_prior_opp_points_for == pytest.approx(opponent)
    assert result.home_prior_game_count == 2
    assert result.home_season_prior_game_count == 1
    assert result.home_effective_game_count == pytest.approx(1+old_weight)
    assert result.home_complete_drive_game_count == 2
    assert result.away_prior_game_count == result.ordinary_prior_games_min == 1
    assert result.home_rest_days_capped60 == 7


def test_missing_stat_uses_only_finite_metric_weights_and_league_observations():
    history, schedules = sources()
    history.loc[(history.game_id==102)&(history.team_id==1),'ppd'] = np.nan
    result = ordinary.query_features(query(),history,schedules).iloc[0]
    league = history.ppd.mean()
    old_weight = .92*.65
    assert result.home_prior_own_ppd == pytest.approx((3*old_weight+4*league)/(old_weight+4))
    assert result.home_complete_drive_game_count == 1
    assert result.home_prior_game_count == 2


def test_no_history_uses_declared_defaults_zero_counts_and_missing_rest():
    history, schedules = sources()
    result = ordinary.query_features(query(),history,schedules,as_of='2020-01-01T00:00:00Z').iloc[0]
    for metric, default in zip(ordinary.METRICS,ordinary.DEFAULTS):
        assert result['home_prior_own_'+metric] == default
        assert result['away_prior_opp_'+metric] == default
    assert result.home_prior_game_count == result.home_effective_game_count == 0
    assert np.isnan(result.home_rest_days_capped60)


def test_repaired_market_recomputes_context_and_adjusted_deltas():
    history, schedules = sources()
    games = query()
    cache = adjusted_cache(games)
    games['market_total'] = 60.
    games['market_home_spread'] = -10.
    games['abs_spread'] = games['home_implied_points'] = 999.
    result = ordinary.query_features(games,history,schedules,adjusted_cache=cache).iloc[0]
    assert result.abs_spread == 10
    assert result.spread_total_interaction == 600
    assert result.home_implied_points == 35
    assert result.away_implied_points == 25
    assert result.adjusted_score_minus_market == -6
    assert result.adjusted_drive_minus_market == -4
    assert result.adjusted_clock_minus_market == -2
    assert result.adjusted_pass_yards == 14
    assert result.ordinary_adjusted_available
    games['market_home_spread'] = np.nan
    missing = ordinary.query_features(games,history,schedules).iloc[0]
    assert missing.spread_missing == 1
    assert np.isnan(missing.abs_spread)
    assert np.isnan(missing.home_implied_points)


def test_earlier_as_of_cannot_reuse_adjusted_features_from_later_cutoff():
    history, schedules = sources()
    games = query()
    result = ordinary.query_features(games,history,schedules,
        as_of='2025-09-06T00:00:00Z',adjusted_cache=adjusted_cache(games))
    assert not result.ordinary_adjusted_available.any()
    assert result[list(ordinary.ADJUSTED_FEATURES)].isna().all().all()
    assert result.ordinary_prior_games_min.iloc[0] == 0


@pytest.mark.parametrize('source',['history','schedule','query','adjusted'])
def test_duplicate_identity_rejected(source):
    history, schedules = sources()
    games = query()
    cache = adjusted_cache(games)
    if source == 'history':
        history = pd.concat([history,history.iloc[[0]]],ignore_index=True)
    elif source == 'schedule':
        schedules = pd.concat([schedules,schedules.iloc[[0]]],ignore_index=True)
    elif source == 'query':
        games = pd.concat([games,games],ignore_index=True)
    else:
        cache = pd.concat([cache,cache],ignore_index=True)
    with pytest.raises(ValueError,match='[Dd]uplicate'):
        ordinary.query_features(games,history,schedules,adjusted_cache=cache)


@pytest.mark.parametrize('defect,match',[
    ('nonfinal','nonfinal'),('opponent','identities mismatch'),('missing_pair','reciprocal'),
    ('availability','availability'),('kickoff','kickoff'),('neutral','neutral-site'),
])
def test_prior_source_corruption_fails_closed(defect,match):
    history, schedules = sources()
    if defect == 'nonfinal':
        schedules.loc[0,'status'] = 'STATUS_IN_PROGRESS'
    elif defect == 'opponent':
        history.loc[0,'opponent_id'] = 999
    elif defect == 'missing_pair':
        history = history.iloc[1:].copy()
    elif defect == 'availability':
        history.loc[0,'available_at'] -= pd.Timedelta(hours=1)
    elif defect == 'kickoff':
        schedules.loc[0,'game_date'] += pd.Timedelta(hours=1)
    elif defect == 'neutral':
        schedules.loc[0,'neutral_site'] = True
    with pytest.raises(ValueError,match=match):
        ordinary.query_features(query(),history,schedules)


def test_integer_and_numeric_string_id_inputs_are_equivalent_without_mutation():
    history, schedules = sources()
    games = query()
    cache = adjusted_cache(games)
    expected = feature_frame(games,history,schedules,adjusted_cache=cache)
    for frame,columns in [(history,('game_id','team_id','opponent_id','season','week')),
                          (schedules,('game_id','home_id','away_id','season','week')),
                          (games,('game_id','home_id','away_id','season','week')),
                          (cache,('game_id','home_id','away_id','season','week'))]:
        for column in columns:
            frame[column] = frame[column].astype(str)
    originals = [f.copy(deep=True) for f in (history,schedules,games,cache)]
    actual = feature_frame(games,history,schedules,adjusted_cache=cache)
    pd.testing.assert_frame_equal(expected,actual)
    for frame,original in zip((history,schedules,games,cache),originals):
        pd.testing.assert_frame_equal(frame,original)


def test_verified_cache_rejects_market_and_role_mismatch():
    cache = adjusted_cache(query())
    market = cache.copy()
    ordinary.validate_cache(cache,market)
    market['market_total'] = 55.
    with pytest.raises(ValueError,match='numeric mismatch: market_total'):
        ordinary.validate_cache(cache,market)
    market = cache.copy()
    cache['source_verified_pregame'] = False
    with pytest.raises(ValueError,match='repaired provider role'):
        ordinary.validate_cache(cache,market)


def test_full_builder_rejects_changed_pinned_source_before_loading(monkeypatch):
    monkeypatch.setattr(ordinary,'_sha',lambda path: '0'*64)
    with pytest.raises(ValueError,match='fingerprint mismatch'):
        ordinary.build_features(Path('/unused-offline-test'))
