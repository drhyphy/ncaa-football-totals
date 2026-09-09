"""Prior-only ordinary statistics for a separate, unfitted research candidate.

Contract: 52 ordinary predictors plus six verified opponent-adjusted predictors.
Ordinary forms use the shared weekly cutoff, completed games available strictly
before cutoff, a 3*366-day history, .92**newest-first-rank * .65**season-gap
weights, and four effective games of shrinkage toward the same-cutoff league
mean. No publisher ratings/EPA, model fitting, outcome correlations or ROI.

The three adjusted market deltas are recomputed from verified cached totals and
repaired market context. Adjusted values are unavailable for a different cutoff.
Historical source revisions and kickoff+6h availability remain explicit proxies.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .opponent_model import load_market_games, weekly_cutoff

VERSION = 'ordinary-prior-statistics-58-v1'
CACHE_PATH = Path('data/normalized/opponent_features_verified_cf764f803b38ecfd.parquet')
CACHE_SHA256 = 'ec33e9add2cb5b84742a17e65c313ca225503f6edadba125fbf7eeb5a45bb73c'
HISTORY_PATH = Path('data/models/opponent_history.parquet')
HISTORY_SHA256 = 'a747dbaa90b28cd555e9e45024091f34ce5f105cabefee6fc6a67a8f3bde4a57'
METRICS = ('points_for', 'ppd', 'drives', 'seconds_per_drive', 'other_points',
           'yards_per_pass', 'yards_per_rush', 'passes_rate')
DEFAULTS = (28., 2.25, 11.5, 150., 1.5, 7., 4., .5)
CONTEXT_FEATURES = ('market_total', 'abs_spread', 'spread_total_interaction',
                    'home_implied_points', 'away_implied_points', 'week', 'neutral_site',
                    'clock_rule_2023', 'two_minute_rule_2024', 'spread_missing')
FORM_FEATURES = tuple(f'{side}_prior_{perspective}_{metric}'
    for side in ('home','away') for perspective in ('own','opp') for metric in METRICS)
COUNT_FEATURES = tuple(f'{side}_{name}' for side in ('home','away') for name in (
    'rest_days_capped60', 'prior_game_count', 'season_prior_game_count',
    'effective_game_count', 'complete_drive_game_count'))
ADJUSTED_FEATURES = ('adjusted_score_minus_market', 'adjusted_drive_minus_market',
                     'adjusted_clock_minus_market', 'adjusted_pass_yards',
                     'adjusted_rush_yards', 'adjusted_pass_share')
FEATURES = list(CONTEXT_FEATURES + FORM_FEATURES + COUNT_FEATURES + ADJUSTED_FEATURES)
BASE_COLUMNS = ('game_id','season','week','game_date','home_id','away_id','home_team','away_team',
                'actual_total','market_total','market_home_spread','neutral_site','status',
                'market_source','source_verified_pregame','adjusted_history_games')
HISTORY_COLUMNS = ('game_id','team_id','opponent_id','season','week','start_date',
                   'is_home','neutral_site','available_at') + METRICS
ADJUSTED_COLUMNS = ('game_id','season','week','game_date','home_id','away_id','neutral_site',
                    'ratings_cutoff','adjusted_score_total','adjusted_drive_total','adjusted_clock_total',
                    'adjusted_pass_yards','adjusted_rush_yards','adjusted_pass_share')
WINDOW_DAYS = 3*366
SHRINK_GAMES = 4.
RECENCY_WEIGHT = .92
SEASON_WEIGHT = .65


def _sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _identities(frame, columns):
    for column in columns:
        values = pd.to_numeric(frame[column], errors='coerce')
        if not (np.isfinite(values) & values.eq(values.round()) & values.gt(0)).all():
            raise ValueError('Invalid identity: '+column)
        frame[column] = values.astype('int64')


def validate_cache(cache, market):
    """Read-only source parity, without selecting on prediction errors/results."""
    cache,market = cache.copy(),market.copy()
    for frame in (cache,market):
        _identities(frame,('game_id','season','week','home_id','away_id'))
    if cache.game_id.duplicated().any() or market.game_id.duplicated().any():
        raise ValueError('Duplicate target/provider game identity')
    if set(cache.game_id) != set(market.game_id):
        raise ValueError('Verified cache and repaired market game IDs differ')
    c, m = cache.set_index('game_id').sort_index(), market.set_index('game_id').sort_index()
    for name in ('season','week','home_id','away_id','neutral_site','status','market_source'):
        if not c[name].eq(m[name]).all():
            raise ValueError('Verified cache/source identity or status mismatch: '+name)
    for name in ('home_score','away_score','actual_total','market_total','market_home_spread'):
        if not np.allclose(c[name],m[name],rtol=0,atol=1e-10,equal_nan=True):
            raise ValueError('Verified cache/source numeric mismatch: '+name)
    if not pd.to_datetime(c.game_date,utc=True).equals(pd.to_datetime(m.game_date,utc=True)):
        raise ValueError('Verified cache/source kickoff mismatch')
    if not c.status.eq('STATUS_FINAL').all() or not c.source_verified_pregame.eq(True).all():
        raise ValueError('Completed game and repaired provider role required')
    expected = pd.Series([weekly_cutoff(t) for t in c.game_date],index=c.index)
    if not pd.to_datetime(c.ratings_cutoff,utc=True).eq(expected).all():
        raise ValueError('Cached adjusted features do not use the shared cutoff')


def prepare_history(history, schedules):
    """Validate reciprocal completed-game identities and discard other columns."""
    missing = set(HISTORY_COLUMNS)-set(history)
    required_schedule = {'game_id','season','week','game_date','home_id','away_id','neutral_site','status'}
    if missing or required_schedule-set(schedules):
        raise ValueError('History or schedule schema incomplete')
    h = history.loc[:,list(HISTORY_COLUMNS)].copy()
    s = schedules.loc[:,sorted(required_schedule)].copy()
    _identities(h, ('game_id','team_id','opponent_id','season','week'))
    _identities(s, ('game_id','season','week','home_id','away_id'))
    if h.duplicated(['game_id','team_id']).any() or s.game_id.duplicated().any():
        raise ValueError('Duplicate team-game or schedule identity')
    if not h.groupby('game_id').size().eq(2).all():
        raise ValueError('Two reciprocal team rows are required per prior game')
    merged = h.merge(s,on='game_id',how='left',suffixes=('','_schedule'),validate='many_to_one')
    if not merged.status.eq('STATUS_FINAL').all():
        raise ValueError('Missing or nonfinal prior-game status')
    if not merged.season.eq(merged.season_schedule).all() or not merged.week.eq(merged.week_schedule).all():
        raise ValueError('Prior game season/week mismatch')
    if not merged.is_home.isin([True,False]).all() or not merged.neutral_site.isin([True,False]).all():
        raise ValueError('Prior venue/home context missing')
    if not merged.neutral_site.eq(merged.neutral_site_schedule).all():
        raise ValueError('Prior neutral-site mismatch')
    expected_team = np.where(merged.is_home,merged.home_id,merged.away_id)
    expected_opponent = np.where(merged.is_home,merged.away_id,merged.home_id)
    if not merged.team_id.eq(expected_team).all() or not merged.opponent_id.eq(expected_opponent).all():
        raise ValueError('Prior home/away/opponent identities mismatch')
    h['start_date'] = pd.to_datetime(h.start_date,utc=True,errors='raise',format='mixed')
    official = pd.to_datetime(merged.game_date,utc=True,errors='raise',format='mixed')
    if not h.start_date.reset_index(drop=True).eq(official.reset_index(drop=True)).all():
        raise ValueError('Prior kickoff differs from official schedule')
    available = pd.to_datetime(h.available_at,utc=True,errors='raise')
    if not available.eq(h.start_date+pd.Timedelta(hours=6)).all():
        raise ValueError('Prior availability must equal kickoff+6h proxy')
    h['available_at'] = available
    for metric in METRICS:
        h[metric] = pd.to_numeric(h[metric],errors='coerce').replace([np.inf,-np.inf],np.nan)
    opponent = h[['game_id','team_id',*METRICS]].rename(columns={
        'team_id':'opponent_id',**{metric:'opp_'+metric for metric in METRICS}})
    h = h.merge(opponent,on=['game_id','opponent_id'],how='left',validate='one_to_one')
    return h.sort_values(['available_at','game_id','team_id']).reset_index(drop=True)


def repaired_context(games):
    """Recompute every market-derived context column; never trust cached copies."""
    total = pd.to_numeric(games.market_total,errors='coerce').astype(float)
    spread = pd.to_numeric(games.market_home_spread,errors='coerce').astype(float)
    if not np.isfinite(total).all() or not total.between(15,100).all():
        raise ValueError('Finite repaired market total in existing source bounds required')
    spread = spread.where(np.isfinite(spread),np.nan)
    return pd.DataFrame({'market_total':total,'abs_spread':spread.abs(),
        'spread_total_interaction':spread.abs()*total,
        'home_implied_points':(total-spread)/2,'away_implied_points':(total+spread)/2,
        'week':pd.to_numeric(games.week),'neutral_site':games.neutral_site.astype(float),
        'clock_rule_2023':games.season.ge(2023).astype(float),
        'two_minute_rule_2024':games.season.ge(2024).astype(float),
        'spread_missing':spread.isna().astype(float)},index=games.index)


def _states(prior, season):
    columns = list(METRICS)+['opp_'+m for m in METRICS]
    defaults = np.asarray(DEFAULTS+DEFAULTS,float)
    if prior.empty:
        return pd.DataFrame(columns=columns),defaults,{}, {}, {}, {}, {}
    prior = prior.sort_values(['team_id','available_at','game_id'],ascending=[True,False,False]).copy()
    ranks = prior.groupby('team_id').cumcount().to_numpy(float)
    weights = RECENCY_WEIGHT**ranks * SEASON_WEIGHT**np.maximum(0,season-prior.season.to_numpy(float))
    values = prior[columns].to_numpy(float)
    finite = np.isfinite(values)
    count = finite.sum(axis=0)
    league = np.divide(np.where(finite,values,0).sum(axis=0),count,
                       out=defaults.copy(),where=count>0)
    sums = pd.DataFrame(np.where(finite,values,0)*weights[:,None],columns=columns,index=prior.index)
    denominators = pd.DataFrame(finite*weights[:,None],columns=columns,index=prior.index)
    sums = sums.groupby(prior.team_id).sum()
    denominators = denominators.groupby(prior.team_id).sum()
    forms = (sums+SHRINK_GAMES*league)/(denominators+SHRINK_GAMES)
    counts = prior.groupby('team_id').size().to_dict()
    season_counts = prior.loc[prior.season.eq(season)].groupby('team_id').size().to_dict()
    effective = pd.Series(weights,index=prior.index).groupby(prior.team_id).sum().to_dict()
    complete = prior[['ppd','drives','seconds_per_drive','other_points']].notna().all(axis=1)
    drive_counts = prior.loc[complete].groupby('team_id').size().to_dict()
    last = prior.groupby('team_id').start_date.max().to_dict()
    return forms,league,counts,season_counts,effective,drive_counts,last


def query_features(games, history, schedules, *, as_of=None, adjusted_cache=None):
    """One historical/live query path; caller supplies already verified cache.

    Full build_features verifies the fixed cache's actual file hash and sources.
    A supplied cache can contribute adjusted predictors only at the exact query
    cutoff. This function does not fit replacement adjusted ratings for new dates.
    """
    needed = {'game_id','season','week','game_date','home_id','away_id','market_total','market_home_spread','neutral_site'}
    if needed-set(games):
        raise ValueError('Query game schema incomplete')
    g = games.loc[:,[c for c in BASE_COLUMNS if c in games]].copy().reset_index(drop=True)
    _identities(g,('game_id','season','week','home_id','away_id'))
    if g.game_id.duplicated().any() or g.home_id.eq(g.away_id).any():
        raise ValueError('Duplicate or invalid query game identity')
    if not g.neutral_site.isin([True,False]).all():
        raise ValueError('Known neutral-site context required')
    g['game_date'] = pd.to_datetime(g.game_date,utc=True,errors='raise',format='mixed')
    if g.game_date.isna().any():
        raise ValueError('Missing query kickoff')
    prepared = prepare_history(history,schedules)
    g['ordinary_cutoff'] = [weekly_cutoff(t,as_of).isoformat() for t in g.game_date]
    context = repaired_context(g)
    for column in CONTEXT_FEATURES:
        g[column] = context[column]
    for column in FORM_FEATURES+COUNT_FEATURES+ADJUSTED_FEATURES:
        g[column] = np.nan
    g['ordinary_history_rows'] = 0
    g['ordinary_adjusted_available'] = False
    for (cutoff_text,season), group in g.groupby(['ordinary_cutoff','season'],sort=True):
        cutoff = pd.Timestamp(cutoff_text)
        prior = prepared.loc[(prepared.available_at<cutoff) &
            (prepared.available_at>=cutoff-pd.Timedelta(days=WINDOW_DAYS)) & prepared.season.le(season)]
        forms,league,counts,season_counts,effective,drive_counts,last = _states(prior,int(season))
        g.loc[group.index,'ordinary_history_rows'] = len(prior)
        for index, game in group.iterrows():
            for side in ('home','away'):
                team = int(game[side+'_id'])
                values = forms.loc[team].to_numpy(float) if team in forms.index else league
                for i,perspective in enumerate(('own','opp')):
                    for j,metric in enumerate(METRICS):
                        g.at[index,f'{side}_prior_{perspective}_{metric}'] = values[i*len(METRICS)+j]
                rest = (game.game_date-last[team]).total_seconds()/86400 if team in last else np.nan
                g.at[index,side+'_rest_days_capped60'] = min(60.,rest) if np.isfinite(rest) else np.nan
                for name,mapping in [('prior_game_count',counts),('season_prior_game_count',season_counts),
                                     ('effective_game_count',effective),('complete_drive_game_count',drive_counts)]:
                    g.at[index,side+'_'+name] = mapping.get(team,0.)
    g['ordinary_prior_games_min'] = g[['home_prior_game_count','away_prior_game_count']].min(axis=1)
    if adjusted_cache is not None:
        if set(ADJUSTED_COLUMNS)-set(adjusted_cache):
            raise ValueError('Incomplete or duplicate adjusted-cache identity')
        a = adjusted_cache.loc[:,list(ADJUSTED_COLUMNS)].copy()
        _identities(a,('game_id','season','week','home_id','away_id'))
        if a.game_id.duplicated().any():
            raise ValueError('Incomplete or duplicate adjusted-cache identity')
        a = a.set_index('game_id')
        for index,game in g.iterrows():
            if game.game_id not in a.index:
                continue
            row = a.loc[game.game_id]
            if any(row[c] != game[c] for c in ('season','week','home_id','away_id','neutral_site')) or pd.Timestamp(row.game_date) != game.game_date:
                raise ValueError('Adjusted-cache/query identity mismatch')
            if pd.Timestamp(row.ratings_cutoff) != pd.Timestamp(game.ordinary_cutoff):
                continue
            values = [row.adjusted_score_total-game.market_total,
                      row.adjusted_drive_total-game.market_total,
                      row.adjusted_clock_total-game.market_total,
                      row.adjusted_pass_yards,row.adjusted_rush_yards,row.adjusted_pass_share]
            if not np.isfinite(values).all():
                raise ValueError('Verified adjusted features contain invalid values')
            g.loc[index,list(ADJUSTED_FEATURES)] = values
            g.at[index,'ordinary_adjusted_available'] = True
    return g


def feature_contract():
    return {'version':VERSION,'features':FEATURES,'ordinary_predictors':52,'verified_adjusted_predictors':6,
        'metrics':list(METRICS),'opponent_definition':'Reciprocal opponent row from each completed prior game; not current-game opponent statistics',
        'cutoff':'opponent_model.weekly_cutoff(game_date,as_of); available_at strictly before cutoff',
        'availability_proxy':'completed game kickoff+6h; later source corrections remain possible',
        'history_window_days':WINDOW_DAYS,'recency_weight':RECENCY_WEIGHT,'season_weight':SEASON_WEIGHT,
        'recency_definition':'Newest-first game rank within each team and eligible query history; no dependence on query batch',
        'shrink_effective_games':SHRINK_GAMES,'shrink_target':'Unweighted finite-value league mean from same eligible cutoff/window; fixed defaults only if no league observations',
        'empty_history_defaults':dict(zip(METRICS,DEFAULTS)),'rest':'Known prior kickoff to query kickoff, capped60days; no prior game remains missing',
        'missing_values':'Missing statistics excluded from each metric numerator/denominator; no global fit/imputation; explicit history and drive counts',
        'adjusted_features':'Verified cache only at exact identity/cutoff; score/drive/clock deltas recomputed using repaired market total',
        'postgame_fields':'actual_total retained only as target metadata; scores/winners/attendance/unshifted box scores never predictors',
        'excluded_families':['EPA','FPI','publisher ratings','undated summaries','rosters','injuries','weather','future schedules/results']}


def build_features(root: Path):
    """Return the complete5,008-game research frame and a source/feature manifest."""
    root = Path(root)
    if _sha(root/CACHE_PATH) != CACHE_SHA256 or _sha(root/HISTORY_PATH) != HISTORY_SHA256:
        raise ValueError('Verified cache/history file fingerprint mismatch')
    cache,history = pd.read_parquet(root/CACHE_PATH),pd.read_parquet(root/HISTORY_PATH)
    if len(cache) != 5008 or len(history) != 11926:
        raise ValueError('Verified research universe changed')
    market = load_market_games(root)
    validate_cache(cache,market)
    schedule_columns = ['game_id','season','week','game_date','home_id','away_id','neutral_site','status']
    schedule_paths = [Path(f'data/raw/sportsdataverse/cfb_schedule_{year}.parquet') for year in range(2019,2026)]
    schedules = pd.concat([pd.read_parquet(root/p,columns=schedule_columns) for p in schedule_paths],ignore_index=True)
    result = query_features(cache,history,schedules,adjusted_cache=cache)
    if not result.ordinary_adjusted_available.all() or not result.ordinary_prior_games_min.eq(result.adjusted_history_games).all():
        raise ValueError('Ordinary and verified adjusted history/cutoffs disagree')
    source_paths = [CACHE_PATH,HISTORY_PATH,*schedule_paths,
        Path('data/raw/alternative/espn_verified_pregame_games.parquet'),
        Path('data/raw/alternative/cfbd_market_games.parquet'),
        Path('ncaaf_model/opponent_model.py'),Path('ncaaf_model/ordinary_features_research.py')]
    contract = feature_contract()
    manifest = {'version':VERSION,'features':FEATURES,'feature_contract':contract,'feature_contract_sha256':_digest(contract),
        'source_files_sha256':{str(p):_sha(root/p) for p in source_paths},
        'cache_data_fingerprint':'cf764f803b38ecfd','cache_sha256':CACHE_SHA256,'history_sha256':HISTORY_SHA256,
        'counts':{'games':len(result),'history_team_rows':len(history),'history_games':int(history.game_id.nunique()),
            'by_season':{str(y):int(n) for y,n in result.season.value_counts().sort_index().items()},
            'market_sources':{str(k):int(v) for k,v in Counter(result.market_source).items()},
            'games_with_minimum5_prior_games':int(result.ordinary_prior_games_min.ge(5).sum()),
            'adjusted_cache_matching_cutoff_games':int(result.ordinary_adjusted_available.sum())},
        'feature_nonmissing_counts':{c:int(result[c].notna().sum()) for c in FEATURES},
        'history_missing_stat_counts':{c:int(history[c].isna().sum()) for c in METRICS},
        'builder_fits_models':False,'outcome_correlations_or_returns_computed':False,
        'limitations':['All2020–25 outcomes have been used in prior development; this is not an untouched holdout.',
            'Source provider role is repaired, but exact quote prices, closing times and morning availability remain unverified.',
            'Prior-game availability uses kickoff+6h and retrospective source revisions, not archived publication receipts.',
            'The58-feature historical comparison is not wired into active scoring; new-date adjusted features need their own verified cutoff source.']}
    manifest['feature_fingerprint'] = _digest({'sources':manifest['source_files_sha256'],'contract':contract})
    return result,manifest
