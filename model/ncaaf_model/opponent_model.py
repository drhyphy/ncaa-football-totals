"""Opponent-adjusted game observations; fixed weekly information cutoffs.

This development candidate uses no EPA or publisher power ratings. Ratings fit
all available scored games, not just games in the historical odds archive.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.linear_model import Ridge

VERSION = 'opponent-adjusted-v3-pregame-provider'
TARGETS = ['points_for', 'ppd', 'drives', 'seconds_per_drive', 'other_points',
           'yards_per_pass', 'yards_per_rush', 'passes_rate']
DEFAULTS = [28., 2.25, 11.5, 150., 1.5, 7., 4., .5]
FEATURES = ['adjusted_score_minus_market', 'adjusted_drive_minus_market',
            'adjusted_clock_minus_market', 'adjusted_pass_yards', 'adjusted_rush_yards',
            'adjusted_pass_share', 'market_total', 'abs_spread', 'week',
            'clock_rule_2023', 'two_minute_rule_2024']


def load_history(root: Path, seasons) -> pd.DataFrame:
    from .drive_model import load_drive_team_games
    raw = root / 'data/raw/sportsdataverse'
    drive = load_drive_team_games(raw, seasons)
    pieces = []
    for season in seasons:
        ap, sp = raw/f'adv_team_gamelog_{season}.parquet', raw/f'cfb_schedule_{season}.parquet'
        if not ap.exists() or not sp.exists():
            continue
        a, s = pd.read_parquet(ap), pd.read_parquet(sp)
        ids = s.loc[s.status.eq('STATUS_FINAL'), 'game_id']
        cols = ['game_id', 'team_id', 'opponent_id', 'season', 'week', 'start_date',
                'is_home', 'neutral_site', 'points_for', 'yards_per_pass', 'yards_per_rush', 'passes_rate']
        a = a.loc[a.game_id.isin(ids), cols].drop_duplicates(['game_id', 'team_id']).copy()
        a['available_at'] = pd.to_datetime(a.start_date, utc=True) + pd.Timedelta(hours=6)
        pieces.append(a)
    history = pd.concat(pieces, ignore_index=True)
    if len(drive):
        d = drive[['game_id', 'team_id', 'td_rate', 'fg_rate', 'drives', 'seconds_per_drive', 'other_points']].copy()
        d['ppd'] = 7*d.td_rate + 3*d.fg_rate
        history = history.merge(d.drop(columns=['td_rate', 'fg_rate']), on=['game_id', 'team_id'], how='left')
    for col in TARGETS:
        history[col] = pd.to_numeric(history.get(col), errors='coerce')
    return history.sort_values('available_at').reset_index(drop=True)


def weekly_cutoff(kickoff, as_of=None):
    """Monday 00:00 ET before kickoff; never newer than the actual run."""
    date = pd.to_datetime(kickoff, utc=True).tz_convert('America/New_York')
    cutoff = date.normalize() - pd.Timedelta(days=date.weekday())
    cutoff = cutoff.tz_convert('UTC')
    return min(cutoff, pd.to_datetime(as_of, utc=True)) if as_of is not None else cutoff


def _matrix(off, defense, home, team_map):
    n, k = len(off), len(team_map)
    rows, cols, vals = [], [], []
    for j, (o, d, h) in enumerate(zip(off, defense, home)):
        if o in team_map:
            rows.append(j); cols.append(team_map[o]); vals.append(1.)
        if d in team_map:
            rows.append(j); cols.append(k+team_map[d]); vals.append(1.)
        rows.append(j); cols.append(2*k); vals.append(float(h))
    return sparse.csr_matrix((vals, (rows, cols)), shape=(n, 2*k+1))


def adjusted_features(games: pd.DataFrame, history: pd.DataFrame, as_of=None) -> pd.DataFrame:
    """Fit offense + opponent defense with partial pooling for each past cutoff."""
    games = games.copy().reset_index(drop=True)
    if games.empty:
        return games
    times = games['game_date'] if 'game_date' in games else games['commence_time']
    games['game_date'] = pd.to_datetime(times, utc=True, format='mixed')
    games['_cutoff'] = [weekly_cutoff(t, as_of) for t in games.game_date]
    records = {}
    for cutoff, group in games.groupby('_cutoff', sort=True):
        train = history.loc[(history.available_at < cutoff) &
                            (history.available_at >= cutoff-pd.Timedelta(days=3*366))].copy()
        team_map = {int(t): i for i, t in enumerate(sorted(set(train.team_id)|set(train.opponent_id)))}
        home = train.is_home.astype(float) * (~train.neutral_site.fillna(False).astype(bool)).astype(float)
        x = _matrix(train.team_id, train.opponent_id, home, team_map)
        latest_year = int(group.season.min())
        years = np.maximum(0, latest_year-train.season.to_numpy(float))
        # The offseason is penalized explicitly; in-season days supply a mild
        # recency penalty. Fixed before this experiment, not outcome-selected.
        season_week = pd.to_numeric(train.week, errors='coerce').fillna(1).to_numpy(float)
        game_week = float(pd.to_numeric(group.week, errors='coerce').fillna(1).min())
        effective_weeks = np.maximum(0, years*18 + game_week-season_week)
        weights = .92**effective_weeks * .65**years
        ghome = (~group.neutral_site.fillna(False).astype(bool)).astype(float)
        hmat = _matrix(group.home_id, group.away_id, ghome, team_map)
        amat = _matrix(group.away_id, group.home_id, np.zeros(len(group)), team_map)
        hp, ap = {}, {}
        for target, prior in zip(TARGETS, DEFAULTS):
            y = train[target].to_numpy(float)
            valid = np.isfinite(y)
            if valid.sum() < 50:
                hp[target] = ap[target] = np.full(len(group), prior)
            else:
                model = Ridge(alpha=4., fit_intercept=True, solver='lsqr', tol=1e-7)
                model.fit(x[valid], y[valid], sample_weight=weights[valid])
                hp[target], ap[target] = model.predict(hmat), model.predict(amat)
        possessions = np.clip(.5*(hp['drives']+ap['drives']), 7, 17)
        clock_possessions = np.clip(3600/np.maximum(hp['seconds_per_drive']+ap['seconds_per_drive'], 180), 7, 17)
        scoring = np.clip(hp['ppd'], .2, 5.5)+np.clip(ap['ppd'], .2, 5.5)
        other = np.clip(hp['other_points'], 0, 8)+np.clip(ap['other_points'], 0, 8)
        score_total = hp['points_for']+ap['points_for']
        drive_total = possessions*scoring+other
        clock_total = .5*(possessions+clock_possessions)*scoring+other
        counts = train.groupby('team_id').size().to_dict()
        for j, (i, game) in enumerate(group.iterrows()):
            market = float(game.market_total)
            spread = pd.to_numeric(game.get('market_home_spread'), errors='coerce')
            records[i] = {'adjusted_score_total': score_total[j], 'adjusted_drive_total': drive_total[j],
                'adjusted_clock_total': clock_total[j], 'adjusted_score_minus_market': score_total[j]-market,
                'adjusted_drive_minus_market': drive_total[j]-market, 'adjusted_clock_minus_market': clock_total[j]-market,
                'adjusted_pass_yards': hp['yards_per_pass'][j]+ap['yards_per_pass'][j],
                'adjusted_rush_yards': hp['yards_per_rush'][j]+ap['yards_per_rush'][j],
                'adjusted_pass_share': .5*(hp['passes_rate'][j]+ap['passes_rate'][j]),
                'abs_spread': abs(spread) if np.isfinite(spread) else 0.,
                'clock_rule_2023': float(game.season >= 2023), 'two_minute_rule_2024': float(game.season >= 2024),
                'adjusted_history_games': min(counts.get(game.home_id, 0), counts.get(game.away_id, 0)),
                'ratings_cutoff': cutoff.isoformat(), 'ratings_training_rows': len(train)}
    extra = pd.DataFrame.from_dict(records, orient='index').sort_index()
    for col in extra:
        games[col] = extra[col]
    return games.drop(columns='_cutoff')


def fit_artifact(train):
    good = train.loc[train.actual_total.notna() & train.market_total.notna() & train.adjusted_history_games.ge(5)]
    x = good[FEATURES].to_numpy(float)
    med = np.nanmedian(x, axis=0)
    x = np.where(np.isfinite(x), x, med)
    center, scale = x.mean(axis=0), np.maximum(x.std(axis=0), 1.)
    x = np.column_stack([np.ones(len(x)), (x-center)/scale])
    y = (good.actual_total-good.market_total).to_numpy(float)
    # Regularize intercept as well: this is a residual forecast around the market.
    penalty = 200.*np.eye(x.shape[1])
    coef = np.linalg.solve(x.T@x+penalty, x.T@y)
    return {'version': VERSION, 'features': FEATURES, 'median': med.tolist(), 'center': center.tolist(),
            'scale': scale.tolist(), 'coefficients': coef.tolist(), 'training_games': len(x),
            'training_seasons': sorted(good.season.unique().astype(int).tolist()),
            'adjustment_cap': 10., 'status': 'development_candidate_unvalidated',
            'market_provenance': 'cfbd_and_verified_pregame_provider_only' if 'source_verified_pregame' in good and good.source_verified_pregame.all() else 'unverified'}


def projections(features, artifact):
    x = features[artifact['features']].to_numpy(float)
    x = np.where(np.isfinite(x), x, artifact['median'])
    x = np.column_stack([np.ones(len(x)), (x-artifact['center'])/artifact['scale']])
    market = features.market_total.to_numpy(float)
    residual = np.clip(x@np.array(artifact['coefficients']), -artifact['adjustment_cap'], artifact['adjustment_cap'])
    return {'opponent_adjusted_ridge': market+residual,
            'opponent_adjusted_structural': market+.25*np.clip(features.adjusted_drive_total.to_numpy(float)-market, -20, 20)}


def load_market_games(root):
    """Never fall back to the compromised scalar archive, even when plausible."""
    raw = root/'data/raw/sportsdataverse'
    alternative = root/'data/raw/alternative'
    repaired_path = alternative/'espn_verified_pregame_games.parquet'
    cfbd_path = alternative/'cfbd_market_games.parquet'
    if not repaired_path.exists() or not cfbd_path.exists():
        raise FileNotFoundError('Both documented replacement market archives are required')
    repaired = pd.read_parquet(repaired_path)
    repaired = repaired.loc[repaired.role_verified.eq(True)].copy()
    rich = pd.read_parquet(cfbd_path)
    rich = rich.loc[rich.validated.eq(True) & ~rich.game_id.isin(repaired.game_id)].copy()
    schedules = pd.concat([pd.read_parquet(raw/f'cfb_schedule_{y}.parquet')[['game_id','neutral_site']]
                          for y in range(2020,2024)], ignore_index=True).drop_duplicates('game_id')
    rich = rich.merge(schedules,on='game_id',how='left')
    rich['game_date'] = rich.date
    rich['market_home_spread'] = rich.spread
    rich['market_source'] = 'cfbd_' + rich.market_source.astype(str)
    cols = ['game_id','season','week','game_date','home_id','away_id','home_team','away_team',
            'home_score','away_score','actual_total','market_total','market_home_spread','neutral_site','status','market_source']
    frame = pd.concat([repaired[cols], rich[cols]], ignore_index=True)
    frame['source_verified_pregame'] = True
    frame['game_date'] = pd.to_datetime(frame.game_date,utc=True,format='mixed')
    if frame.game_id.duplicated().any():
        raise ValueError('Duplicate games in replacement market data')
    return frame.loc[frame.market_total.between(15,100)].sort_values('game_date').reset_index(drop=True)


def metric(frame):
    from .drive_model import _week_interval
    f = frame.copy()
    f['mae_delta'] = abs(f.actual_total-f.projected_total)-abs(f.actual_total-f.market_total)
    edge = f.projected_total-f.market_total
    # A single fixed economic threshold, not a six-point filter. Prices in this
    # historical dataset are unavailable; the following always assumes -110.
    from .distribution import probabilities
    sigmas = f.residual_sigma.to_numpy(float) if 'residual_sigma' in f else np.full(len(f),16.)
    p = np.array([probabilities(c,l,sig,'normal') for c,l,sig in zip(f.projected_total,f.market_total,sigmas)])
    side = edge >= 0
    pw = np.where(side,p[:,0],p[:,1]); push=p[:,2]
    ev = pw*(100/110)-(1-pw-push)
    f['profit'] = np.where(f.actual_total.eq(f.market_total),0,np.where((f.actual_total.gt(f.market_total)) == side,100/110,-1))
    from .distribution import expected_value
    robust = np.array([min(expected_value(c-1 if is_over else c+1,l,-110,'over' if is_over else 'under',sig*factor,family)[0]
                          for factor in (.85,1.15) for family in ('normal','student_t7'))
                       for c,l,sig,is_over in zip(f.projected_total,f.market_total,sigmas,side)])
    bets = f.loc[(ev >= .03)&(robust >= .01)&f.adjusted_history_games.ge(5)]
    decided = f.actual_total.ne(f.market_total)
    return {'games':len(f),'mae':float(abs(f.actual_total-f.projected_total).mean()),
        'rmse':float(np.sqrt(np.mean((f.actual_total-f.projected_total)**2))),
        'mae_delta':float(f.mae_delta.mean()),'mae_delta_week_bootstrap_95':_week_interval(f,'mae_delta'),
        'brier':float(np.mean((p[decided,0]/(1-p[decided,2])-f.loc[decided,'actual_total'].gt(f.loc[decided,'market_total']))**2)),
        'bets':len(bets),'wins':int(bets.profit.gt(0).sum()),'losses':int(bets.profit.lt(0).sum()),
        'assumed_minus110_roi':float(bets.profit.mean()) if len(bets) else None,
        'roi_week_bootstrap_95':_week_interval(bets,'profit') if len(bets) else None}


def run_research(root):
    root=Path(root)
    history=load_history(root,range(2019,2026))
    history.to_parquet(root/'data/models/opponent_history.parquet',index=False)
    market_games = load_market_games(root)
    fingerprint = hashlib.sha256(Path(__file__).read_bytes()+pd.util.hash_pandas_object(market_games,index=False).values.tobytes()+pd.util.hash_pandas_object(history,index=False).values.tobytes()).hexdigest()[:16]
    cache=root/f'data/normalized/opponent_features_verified_{fingerprint}.parquet'
    if cache.exists():
        features=pd.read_parquet(cache)
    else:
        features=adjusted_features(market_games,history)
        cache.parent.mkdir(parents=True,exist_ok=True)
        features.to_parquet(cache,index=False)
    rows=[]; summary={}
    for season in (2021,2022,2023,2024,2025):
        train=features.loc[features.season < season]
        valid=features.loc[features.season.eq(season)&features.adjusted_history_games.ge(5)].copy()
        artifact=fit_artifact(train)
        candidates={'market_only':valid.market_total.to_numpy(float),**projections(valid,artifact)}
        summary[str(season)]={}
        for candidate,pred in candidates.items():
            f=valid.copy(); f['candidate']=candidate; f['projected_total']=pred; f['residual_sigma']=float(np.sqrt(np.mean((train.actual_total-train.market_total)**2)))
            summary[str(season)][candidate]=metric(f); rows.append(f)
        print(json.dumps({'season':season,'metrics':summary[str(season)]}),flush=True)
    output=pd.concat(rows,ignore_index=True)
    pooled={c:metric(f) for c,f in output.groupby('candidate')}
    artifact=fit_artifact(features)
    artifact['data_fingerprint'] = fingerprint
    artifact['market_source_counts'] = features.market_source.value_counts().to_dict()
    (root/'data/models/opponent_adjusted_v1.json').write_text(json.dumps(artifact,indent=2)+'\n')
    # A frozen, discrete normal location family with sigma estimated only from
    # the replacement pregame-provider market residuals. Both tails are still
    # varied in live stress testing; this fit is not a probability-calibration proof.
    residual = features.actual_total-features.market_total
    distribution = {'version':'score-distribution-v2-pregame-provider','family':'normal',
                    'sigma':float(np.sqrt(np.mean(residual**2))), 'training_games':len(features),
                    'training_seasons':sorted(features.season.unique().astype(int).tolist()),
                    'market_provenance':artifact['market_provenance'],'data_fingerprint':fingerprint,
                    'status':'development_distribution_not_profitability_evidence'}
    (root/'data/models/score_distribution_v2.json').write_text(json.dumps(distribution,indent=2)+'\n')
    report={'market_provenance':artifact['market_provenance'],'data_fingerprint':fingerprint,'market_source_counts':features.market_source.value_counts().to_dict(),'status':'reused_development_not_proof_of_profit','specification':VERSION,'historical_price_assumption':-110,'alternative_data_used':(root/'data/raw/alternative/cfbd_market_games.parquet').exists(),
        'selection':'two fixed candidates, no outcome-based hyperparameter search; same specifications refitted on replacement pregame-provider data; 2021 and2022 added to evaluation because historical coverage now supports prior-season fits','by_season':summary,'pooled':pooled,
        'limitations':['Verified pregame-provider role, no certified closing time, offered total prices or morning timestamps.',
                      '2019–2025 reused development data; not an untouched holdout.',
                      'Weekly Monday cutoff and kickoff+6h availability proxy; later upstream revisions not fully auditable.',
                      'Individual bootstrap intervals are descriptive, not corrected for all prior research selection.']}
    (root/'reports/opponent_adjusted_development.json').write_text(json.dumps(report,indent=2)+'\n')
    output.to_csv(root/'reports/opponent_adjusted_predictions.csv',index=False)
    lines=['# Opponent-adjusted development experiment','','All ratings train on prior completed game observations, including games without historical odds. No EPA or publisher ratings. Two fixed specifications; no tuning on the 2025 results. The whole 2019–2025 corpus has already been reused in this project.','',
        '| Candidate | Games | MAE | MAE change vs market | Bets at assumed -110 | ROI | 95% week bootstrap |','|---|---:|---:|---:|---:|---:|---|']
    for c,m in pooled.items():
        roi=f"{m['assumed_minus110_roi']:.2%}" if m['assumed_minus110_roi'] is not None else '—'
        lines.append(f"| {c} | {m['games']} | {m['mae']:.3f} | {m['mae_delta']:+.3f} | {m['bets']} | {roi} | {m['roi_week_bootstrap_95']} |")
    lines+=['','Ratings use a shared league intercept, offensive team effect and opposing defense effect, partial pooling, recency decay and offseason decay. Separate regressions estimate scoring, points per drive, possession count/duration, pass/rush yards and passing share. A strongly regularized residual model combines these signals with the line, spread and known clock-rule eras.','', 'The fixed probability filter requires modeled EV ≥3% and stressed EV ≥1% at assumed -110, plus at least five prior games. Each test season estimates sigma from prior seasons only. Historical data cannot reproduce the current two-book quote-presence and pricing checks. It does not impose a point threshold that a shrunk model cannot reach. These are research signals, not established profitable bets.']
    (root/'reports/opponent_adjusted_development.md').write_text('\n'.join(lines)+'\n')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--root',type=Path,default=Path('.')); args=parser.parse_args()
    print(json.dumps(run_research(args.root)['pooled'],indent=2))
