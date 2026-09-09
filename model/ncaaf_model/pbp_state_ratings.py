"""Pure, cutoff-safe conditional PBP ratings; no IO or outcome-model fitting.

Conversion is fitted as a continuous state-adjusted rating. Its output is a
raw rate contribution, not a calibrated probability (and is not clipped).
"""
from __future__ import annotations

from numbers import Integral
import re

import numpy as np
import pandas as pd
from pandas.api.types import is_bool_dtype, is_integer_dtype
from scipy import sparse
from sklearn.linear_model import Ridge

from .opponent_model import weekly_cutoff

VERSION = 'pbp-state-ratings-v1'
FEATURES = ['pbp_clock_rating', 'pbp_conversion_rating']
RESPONSES = {'clock_seconds': 'clock', 'conversion': 'conversion'}
STATE_COLUMNS = (
    'down_2', 'down_3', 'down_4', 'log_distance', 'distance_capped20',
    'yards_to_endzone', 'yards_to_endzone_squared', 'score_margin',
    'absolute_score_margin', 'half_seconds_remaining', 'half_seconds_squared',
    'period_2', 'period_3', 'period_4', 'pass_play', 'pass_unknown',
    'clock_rule_2023', 'two_minute_rule_2024',
)
ROW_COLUMNS = (
    'game_id', 'season', 'week', 'team_id', 'opponent_id', 'available_at',
    'is_home', 'neutral_site', 'down', 'distance', 'yards_to_endzone',
    'score_margin', 'period', 'half_seconds_remaining', 'pass_play',
    'clock_seconds', 'conversion',
)
GAME_COLUMNS = ('game_id', 'season', 'week', 'game_date', 'home_id', 'away_id')
WEIGHT_COLUMNS = ('game_id', 'team_id', 'season', 'week', 'eligible_rows', 'weight_sum', 'row_weight')


def _require(frame, columns, name):
    if not isinstance(frame, pd.DataFrame):
        raise TypeError(f'{name} must be a DataFrame')
    if frame.columns.duplicated().any():
        raise ValueError(f'{name} has duplicate columns')
    missing = set(columns) - set(frame)
    if missing:
        raise ValueError(f'{name} missing columns: {sorted(missing)}')


def _numeric(frame, column, *, low=None, high=None, integer=False, missing=False):
    raw = frame[column]
    value = pd.to_numeric(raw, errors='coerce').astype(float)
    invalid = ~np.isfinite(value)
    if missing:
        invalid &= ~raw.isna()
    if low is not None:
        invalid |= value.lt(low)
    if high is not None:
        invalid |= value.gt(high)
    if integer:
        invalid |= value.notna() & value.ne(np.floor(value))
    if invalid.any():
        raise ValueError(f'Invalid {column}')
    frame[column] = value.astype('int64') if integer and not missing else value


def _canonical_ids(frame, column):
    """Preserve signed-int64 identities exactly; never accept floating IDs."""
    raw = frame[column]
    maximum = np.iinfo(np.int64).max
    if raw.isna().any() or is_bool_dtype(raw.dtype):
        raise ValueError(f'Invalid {column}')
    if is_integer_dtype(raw.dtype):
        if raw.lt(1).any() or raw.gt(maximum).any():
            raise ValueError(f'Invalid {column}')
        frame[column] = raw.astype('int64')
        return
    parsed = []
    for value in raw:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError(f'Invalid {column}')
        if isinstance(value, Integral):
            integer = int(value)
        elif isinstance(value, str) and re.fullmatch(r'[0-9]+', value):
            integer = int(value)
        else:
            raise ValueError(f'Invalid {column}: floating IDs are not accepted')
        if not 1 <= integer <= maximum:
            raise ValueError(f'Invalid {column}')
        parsed.append(integer)
    frame[column] = pd.Series(parsed, index=raw.index, dtype='int64')


def _times(values, name):
    try:
        result = pd.to_datetime(values, utc=True, format='mixed', errors='raise')
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f'Invalid {name}') from exc
    if bool(pd.isna(result).any()) if isinstance(result, pd.Series) else bool(pd.isna(result)):
        raise ValueError(f'Invalid {name}')
    return result


def _validate_train(train, season, query_ids):
    """Only admitted rows are inspected; future values cannot change old fits."""
    train = train.copy()
    for column in ('game_id', 'team_id', 'opponent_id'):
        _canonical_ids(train, column)
    _numeric(train, 'season', low=1, high=9999, integer=True)
    _numeric(train, 'week', low=0, integer=True)
    for column in ('is_home', 'neutral_site'):
        _numeric(train, column, low=0, high=1, integer=True)
    _numeric(train, 'pass_play', low=0, high=1, integer=True, missing=True)
    _numeric(train, 'down', low=1, high=4, integer=True)
    _numeric(train, 'period', low=1, high=4, integer=True)
    for column in ('distance', 'yards_to_endzone'):
        _numeric(train, column, low=1, high=100)
    _numeric(train, 'score_margin')
    _numeric(train, 'half_seconds_remaining', low=0, high=1800)
    _numeric(train, 'clock_seconds', low=0, high=60, missing=True)
    _numeric(train, 'conversion', low=0, high=1, integer=True, missing=True)
    if train.team_id.eq(train.opponent_id).any():
        raise ValueError('Training team and opponent must differ')
    if train.season.gt(season).any() or train.season.gt(train.available_at.dt.year).any():
        raise ValueError('Future season information in admitted history')
    if train.game_id.isin(query_ids).any():
        raise ValueError('Query game appears in admitted history')
    for column in ('play_id', 'id'):
        if column in train:
            if train[column].isna().any() or train[column].astype(str).str.strip().eq('').any():
                raise ValueError(f'Invalid {column}')
            if train.assign(_play_id=train[column].astype(str)).duplicated(['game_id', '_play_id']).any():
                raise ValueError(f'Duplicate {column} within game')
    fixed = ['season', 'week', 'opponent_id', 'is_home', 'neutral_site']
    if train.groupby(['game_id', 'team_id'])[fixed].nunique().gt(1).any().any():
        raise ValueError('Inconsistent team-game metadata')
    return train


def _state_basis(train):
    distance = train.distance.to_numpy(float)
    yard = train.yards_to_endzone.to_numpy(float) / 100
    margin = np.clip(train.score_margin.to_numpy(float), -28, 28) / 28
    clock = train.half_seconds_remaining.to_numpy(float) / 1800
    return np.column_stack([
        *(train.down.eq(d).to_numpy(float) for d in (2, 3, 4)),
        np.log1p(distance)/np.log(101), np.minimum(distance, 20)/20,
        yard, yard**2, margin, np.abs(margin), clock, clock**2,
        *(train.period.eq(p).to_numpy(float) for p in (2, 3, 4)),
        train.pass_play.fillna(0).to_numpy(float), train.pass_play.isna().to_numpy(float),
        train.season.ge(2023).to_numpy(float),
        train.season.ge(2024).to_numpy(float),
    ])


def _response_weights(train, season, week):
    years = season - train.season.to_numpy(float)
    game_weights = .92**np.maximum(0, years*18 + week-train.week.to_numpy(float)) * .65**years
    counts = train.groupby(['game_id', 'team_id']).game_id.transform('size').to_numpy(float)
    return game_weights/counts


def _weight_evidence(eligible, weights):
    """Exact team-game values in columns, without a dictionary for every row.

    Sum the same eligible row weights in the same within-group order as before.
    No fitting inputs or arithmetic are changed by this reporting optimization.
    """
    result = {name: [] for name in WEIGHT_COLUMNS}
    for (game_id, team_id), indices in eligible.groupby(['game_id', 'team_id'], sort=True).indices.items():
        first = int(indices[0])
        values = (int(game_id), int(team_id), int(eligible.season.iloc[first]),
                  int(eligible.week.iloc[first]), len(indices), float(weights[indices].sum()),
                  float(weights[first]))
        for column, value in zip(WEIGHT_COLUMNS, values):
            result[column].append(value)
    return result


def _fit_response(train, response, season, week):
    eligible = train.loc[train[response].notna()].copy()
    teams = sorted(set(eligible.team_id) | set(eligible.opponent_id))
    team_map = {int(team): i for i, team in enumerate(teams)}
    k, n = len(teams), len(eligible)
    names = ([f'offense:{t}' for t in teams] + [f'defense:{t}' for t in teams]
             + ['nonneutral_home'] + list(STATE_COLUMNS))
    coefficients = np.zeros(len(names))
    weights = np.empty(0)
    intercept = 0.
    iterations = None
    if n:
        row_index = np.arange(n)
        indicators = sparse.csr_matrix((np.ones(2*n),
            (np.concatenate([row_index, row_index]), np.concatenate([
                eligible.team_id.map(team_map).to_numpy(int),
                k+eligible.opponent_id.map(team_map).to_numpy(int)]))), shape=(n, 2*k))
        home = (eligible.is_home * (1-eligible.neutral_site)).to_numpy(float)[:, None]
        design = sparse.hstack([indicators, sparse.csr_matrix(home),
                                sparse.csr_matrix(_state_basis(eligible))], format='csr')
        weights = _response_weights(eligible, season, week)
        model = Ridge(alpha=4., fit_intercept=True, solver='lsqr', tol=1e-7)
        model.fit(design, eligible[response].to_numpy(float), sample_weight=weights)
        coefficients = np.asarray(model.coef_, dtype=float)
        intercept = float(model.intercept_)
        iterations = np.asarray(model.n_iter_, dtype=int).tolist() if model.n_iter_ is not None else None
        if not np.isfinite(coefficients).all() or not np.isfinite(intercept):
            raise ValueError('Nonfinite conditional rating fit')
    summaries = _weight_evidence(eligible, weights)
    metadata = dict(status='fitted' if n else 'no_history', response=response,
        training_rows=n, training_games=int(eligible.game_id.nunique()),
        training_team_games=len(summaries['game_id']), training_seasons=sorted(eligible.season.unique().astype(int).tolist()),
        weight_sum=float(weights.sum()), team_game_weights=summaries,
        team_game_weights_format='aligned_columns_v1',
        team_map={str(t): i for t, i in team_map.items()}, coefficient_names=names,
        coefficients=coefficients.tolist(), intercept=intercept,
        fit_iterations=iterations,
        parameters=dict(alpha=4., fit_intercept=True, solver='lsqr', tol=1e-7))
    return eligible, team_map, coefficients, metadata


def _coverage_by_team(train):
    """Two grouped scans per response, independent of the query-game count."""
    result = {}
    for role, column in (('offense', 'team_id'), ('defense', 'opponent_id')):
        grouped = train.groupby(column, sort=False).game_id.agg(['size', 'nunique'])
        for team, count in grouped.iterrows():
            result.setdefault(int(team), dict(offense_rows=0, offense_games=0, defense_rows=0, defense_games=0))
            result[int(team)][role+'_rows'] = int(count['size'])
            result[int(team)][role+'_games'] = int(count['nunique'])
    return result


def query_pbp_features(games, rows, as_of=None):
    """Return games plus two ratings, coverage columns, and JSON-safe fit evidence.

    Historical observations enter strictly before the Monday ET cutoff (or an
    earlier as_of), within 3*366 days. Equal-cutoff batches use minimum query
    season/week, matching opponent_model. Optional play_id/id duplicates are
    rejected within a game. Other columns, including final scores, are unused.
    """
    _require(games, GAME_COLUMNS, 'games')
    output = games.copy().reset_index(drop=True)
    query = output[list(GAME_COLUMNS)].copy()
    for column in ('game_id', 'home_id', 'away_id'):
        _canonical_ids(query, column)
    _numeric(query, 'season', low=1, high=9999, integer=True)
    _numeric(query, 'week', low=0, integer=True)
    query['game_date'] = _times(query.game_date, 'game_date')
    if query.game_id.duplicated().any():
        raise ValueError('Duplicate query game_id')
    if query.home_id.eq(query.away_id).any():
        raise ValueError('Query teams must differ')
    if query.season.gt(query.game_date.dt.year).any():
        raise ValueError('Future query season')
    if as_of is not None:
        as_of = _times(as_of, 'as_of')
    query['_cutoff'] = [weekly_cutoff(t, as_of) for t in query.game_date]
    if isinstance(rows, pd.DataFrame) and rows.empty:
        rows = rows.reindex(columns=list(dict.fromkeys([*ROW_COLUMNS, *rows.columns])))
    _require(rows, ROW_COLUMNS, 'rows')
    history = rows.loc[:, list(ROW_COLUMNS)+[c for c in ('play_id', 'id') if c in rows]].copy()
    history['available_at'] = _times(history.available_at, 'available_at')
    metadata = dict(version=VERSION, features=list(FEATURES), state_columns=list(STATE_COLUMNS),
        cutoff_rule='Monday 00:00 America/New_York, capped by as_of; available_at strictly earlier',
        history_days=3*366, rating_units={'pbp_clock_rating': 'seconds',
            'pbp_conversion_rating': 'raw continuous rate contribution, not probability'},
        cutoff_fits=[], game_coverage=[])
    for feature in FEATURES:
        output[feature] = 0.
    output['pbp_ratings_cutoff'] = [c.isoformat() for c in query['_cutoff']]
    for label in RESPONSES.values():
        output[f'pbp_{label}_history_games'] = 0
    for cutoff, group in query.groupby('_cutoff', sort=True):
        season, week = int(group.season.min()), float(group.week.min())
        selected = history.loc[history.available_at.lt(cutoff)
            & history.available_at.ge(cutoff-pd.Timedelta(days=3*366))]
        train = _validate_train(selected, season, set(group.game_id))
        fit = dict(cutoff=cutoff.isoformat(), query_season=season, query_week=week,
                   admitted_rows=len(train), responses={})
        coverage = {int(i): dict(game_id=int(game.game_id), cutoff=cutoff.isoformat(), responses={})
                    for i, game in group.iterrows()}
        for response, label in RESPONSES.items():
            eligible, team_map, coef, evidence = _fit_response(train, response, season, week)
            fit['responses'][response] = evidence
            k = len(team_map)
            team_coverage = _coverage_by_team(eligible)
            zero_coverage = dict(offense_rows=0, offense_games=0, defense_rows=0, defense_games=0)
            for i, game in group.iterrows():
                h, a = team_map.get(int(game.home_id)), team_map.get(int(game.away_id))
                contribution = lambda t, offset: 0. if t is None else float(coef[offset+t])
                output.loc[i, f'pbp_{label}_rating'] = .5*(contribution(h, 0)
                    + contribution(a, k) + contribution(a, 0) + contribution(h, k))
                home = team_coverage.get(int(game.home_id), zero_coverage).copy()
                away = team_coverage.get(int(game.away_id), zero_coverage).copy()
                output.loc[i, f'pbp_{label}_history_games'] = min(home['offense_games'], away['offense_games'])
                coverage[int(i)]['responses'][response] = dict(home=home, away=away)
        metadata['cutoff_fits'].append(fit)
        metadata['game_coverage'].extend(coverage.values())
    return output, metadata
