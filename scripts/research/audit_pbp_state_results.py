"""Independent audit of the frozen v2 PBP matchup experiment, never a study runner.

Use --self-test before results are released; it reads no research artifacts.
Use --run only after both selection and the reused-2025 check are complete.
The audit never imports model fitting/scoring functions or changes their files.
Ten residual ridges are reconstructed by augmented least squares (SVD), rather
than the study's normal-equation solver. Only a new compact audit MD/JSON is
written, with exclusive creation. No source downloads, strategy changes or EV.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
from importlib.metadata import version as package_version
import json
import math
from pathlib import Path
import subprocess
import tempfile
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

VERSION = 'pbp-state-independent-results-audit-v1'
STUDY_VERSION = 'pbp-state-residual-comparison-v2-local-order'
CONFIGS = ('market_only', 'opponent_adjusted_ridge', 'pbp_state_ridge')
OLD = ('adjusted_score_minus_market', 'adjusted_drive_minus_market',
       'adjusted_clock_minus_market', 'adjusted_pass_yards', 'adjusted_rush_yards',
       'adjusted_pass_share', 'market_total', 'abs_spread', 'week',
       'clock_rule_2023', 'two_minute_rule_2024')
FULL = (*OLD, 'pbp_clock_rating', 'pbp_conversion_percentage_points')
PLAN = 'reports/pbp_state_research_plan_v2.json'
FEATURE = 'reports/pbp_state_feature_audit_v2.json'
SELECTION = 'reports/pbp_state_selection_v2.json'
RESULTS = 'reports/pbp_state_results_v2.json'
BASE = 'data/normalized/opponent_features_verified_cf764f803b38ecfd.parquet'
SPACE = 'data/normalized/pbp_state_v2'
SEED, DRAWS = 20260909, 10000


def require(condition, message):
    if not bool(condition):
        raise ValueError(message)


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4*1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def git(repo, *args):
    return subprocess.check_output(['git', *args], cwd=repo, stderr=subprocess.PIPE)


def blob(repo, commit, path, expected):
    actual = hashlib.sha256(git(repo, 'show', f'{commit}:{path}')).hexdigest()
    require(actual == expected, f'Recorded Git blob mismatch: {commit}:{path}')
    return {'commit': commit, 'path': path, 'sha256': actual}


def absent(repo, commit, path):
    check = subprocess.run(['git', 'cat-file', '-e', f'{commit}:{path}'], cwd=repo,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    require(check.returncode != 0, f'Later-stage artifact already in earlier commit: {path}')


def finite(values, name):
    array = np.asarray(values, dtype=float)
    require(np.isfinite(array).all(), f'Nonfinite {name}')
    return array


def fit(train, test, columns):
    """Independent exact specification; never reads future actual_total."""
    require(tuple(columns) in (OLD, FULL), 'Unplanned predictors')
    require(len(train) and len(test), 'Empty annual fit')
    require(test.season.nunique() == 1 and train.season.max() < test.season.min(),
            'Training must be strictly earlier seasons')
    require(not train.game_id.duplicated().any() and not test.game_id.duplicated().any()
            and not train.game_id.isin(test.game_id).any(), 'Game identity overlap/duplicates')
    x, future = finite(train[list(columns)], 'training features'), finite(test[list(columns)], 'future features')
    response = finite(train.actual_total-train.market_total, 'training residual')
    mean = np.mean(x, axis=0)
    scale = np.maximum(np.sqrt(np.mean((x-mean)**2, axis=0)), 1.)
    design = np.concatenate((np.ones((len(train), 1)), (x-mean)/scale), axis=1)
    augmented_x = np.concatenate((design, np.sqrt(200.)*np.eye(design.shape[1])), axis=0)
    augmented_y = np.concatenate((response, np.zeros(design.shape[1])))
    coefficients = np.linalg.lstsq(augmented_x, augmented_y, rcond=None)[0]
    heldout_design = np.concatenate((np.ones((len(test), 1)), (future-mean)/scale), axis=1)
    residual = heldout_design @ coefficients
    predicted = finite(test.market_total, 'future market') + np.minimum(10., np.maximum(-10., residual))
    metadata = {'test_season': int(test.season.iloc[0]), 'training_games': len(train),
                'training_seasons': sorted(int(x) for x in train.season.unique()),
                'test_games': len(test), 'features': list(columns),
                'median': np.median(x, axis=0).tolist(), 'center': mean.tolist(),
                'scale': scale.tolist(), 'coefficients': coefficients.tolist(),
                'clipped_forecasts': int(np.sum(np.abs(residual) > 10.))}
    return predicted, metadata


def metrics(actual, predicted):
    errors = finite(actual, 'actual')-finite(predicted, 'prediction')
    require(errors.ndim == 1 and len(errors), 'Nonempty scalar errors required')
    squared = math.fsum(float(x*x) for x in errors)/len(errors)
    return {'games': len(errors), 'mse': squared, 'rmse': math.sqrt(squared),
            'mae': math.fsum(float(abs(x)) for x in errors)/len(errors),
            'mean_error': math.fsum(map(float, errors))/len(errors)}


def week_labels(values):
    labels = []
    for value in values:
        instant = pd.Timestamp(value)
        require(instant.tzinfo is not None, 'Explicit game timezone required')
        local_date = instant.to_pydatetime().astimezone(ZoneInfo('America/New_York')).date()
        labels.append((local_date-timedelta(days=local_date.weekday())).isoformat())
    return labels


def paired(frame, first, second, draws=DRAWS):
    y, a, b = (finite(frame[k], k) for k in ('actual_total', first, second))
    squared, absolute = (y-a)**2-(y-b)**2, np.abs(y-a)-np.abs(y-b)
    groups = defaultdict(list)
    for label, mse, mae in zip(week_labels(frame.game_date), squared, absolute):
        groups[label].append((float(mse), float(mae)))
    values = np.array([[math.fsum(x[0] for x in groups[k]), math.fsum(x[1] for x in groups[k]),
                        len(groups[k])] for k in sorted(groups)], dtype=float)
    result = {'candidate': first, 'reference': second, 'games': len(frame), 'week_blocks': len(groups),
              'mse_difference': math.fsum(map(float, squared))/len(frame),
              'mae_difference': math.fsum(map(float, absolute))/len(frame),
              'mse_interval_95': None, 'mse_interval_99': None, 'mae_interval_95': None,
              'draws': draws, 'seed': SEED}
    if len(groups) < 2:
        return result
    generator = np.random.Generator(np.random.PCG64(SEED))
    boot = np.empty((draws, 2))
    # Chunking preserves the exact PCG64 stream; weeks, not games, are sampled.
    for begin in range(0, draws, 256):
        end = min(begin+256, draws)
        sampled = generator.integers(0, len(values), size=(end-begin, len(values)))
        sums = values[sampled].sum(axis=1)
        boot[begin:end] = sums[:, :2]/sums[:, 2, None]
    result.update(mse_interval_95=np.percentile(boot[:, 0], [2.5,97.5]).tolist(),
                  mse_interval_99=np.percentile(boot[:, 0], [.5,99.5]).tolist(),
                  mae_interval_95=np.percentile(boot[:, 1], [2.5,97.5]).tolist())
    return result


def group_summary(frame):
    return {'games': len(frame), 'metrics': {k: metrics(frame.actual_total, frame[k]) for k in CONFIGS},
            'comparisons': [paired(frame, CONFIGS[2], ref) for ref in CONFIGS[:2]]}


def period_summary(frame):
    return {'pooled': group_summary(frame),
            'by_season': {str(k): group_summary(g) for k,g in frame.groupby('season')},
            'by_source': {str(k): group_summary(g) for k,g in frame.groupby('market_source')}}


def choose(frame):
    require(set(frame.season) == {2021,2022,2023,2024}, 'Selection includes wrong seasons')
    return min(CONFIGS, key=lambda name: (metrics(frame.actual_total, frame[name])['mse'], CONFIGS.index(name)))


class Comparison:
    def __init__(self):
        self.numeric_checks = 0
        self.max_absolute_difference = 0.
        self.mismatches = []

    def compare(self, expected, actual, path='root', atol=1e-8):
        if isinstance(expected, dict):
            if not isinstance(actual, dict) or set(expected) != set(actual):
                self.mismatches.append({'path': path, 'error': 'Object keys differ'})
                return
            for key in expected:
                self.compare(expected[key], actual[key], path+'.'+str(key), atol)
        elif isinstance(expected, (list,tuple)):
            if not isinstance(actual, (list,tuple)) or len(expected) != len(actual):
                self.mismatches.append({'path': path, 'error': 'List shape differs'})
                return
            for i,(a,b) in enumerate(zip(expected, actual)):
                self.compare(a,b,path+f'[{i}]',atol)
        elif isinstance(expected, (float,np.floating)):
            self.numeric_checks += 1
            if not isinstance(actual, (int,float)) or not math.isfinite(float(actual)):
                self.mismatches.append({'path': path, 'error': 'Nonfinite/not-numeric result'})
                return
            difference = abs(float(expected)-float(actual))
            self.max_absolute_difference = max(self.max_absolute_difference,difference)
            if difference > atol:
                self.mismatches.append({'path': path, 'expected': float(expected), 'actual': actual,
                                        'absolute_difference': difference})
        elif expected != actual:
            self.mismatches.append({'path': path, 'expected': expected, 'actual': actual})


def audit(root):
    repo = root.parent
    paths = [PLAN,FEATURE,SELECTION,RESULTS]
    require(all((root/p).is_file() for p in paths), 'Both study stages and frozen feature audit must exist')
    files = {p: sha(root/p) for p in paths}
    plan,feature,selection,result = [json.loads((root/p).read_text()) for p in paths]
    require(all(x['version'] == STUDY_VERSION for x in (plan,feature,selection,result)), 'Wrong experiment version')
    require(plan['candidate_order'] == list(CONFIGS) and feature['feature_columns'] == list(FULL), 'Changed model family')
    require(plan['selection_years'] == [2021,2022,2023,2024] and plan['later_check_year'] == 2025, 'Changed years')
    require(plan['primary_metric'] == 'mse' and plan['matchup_model_fits_or_scores_computed'] is False,
            'Pre-performance freeze is not recorded')
    require(feature['matchup_prediction_scores_computed'] is False and feature['target_game_pbp_coverage_gate'] is False,
            'Feature stage performed matchup scoring or applied a PBP target gate')
    require(selection['new_2025_matchup_forecasts_computed'] is False, 'Selection already includes later forecasts')
    require(all(x['no_2026_outcomes'] is True for x in (plan,feature,selection,result)), '2026 scope changed')
    require(all(x['live_policy_changes'] is False for x in (plan,selection,result)), 'Live policy changed')
    require(result['credible_executable_edge_established'] is False, 'Study asserts an executable edge')
    for x in (feature,selection,result):
        require(x['plan_sha256'] == files[PLAN], 'Plan identity mismatch')
    require(selection['feature_audit_sha256'] == files[FEATURE] and result['selection_sha256'] == files[SELECTION],
            'Stage hash chain mismatch')
    stamps = [plan['frozen_at'],feature['built_at'],selection['selected_at'],result['checked_at']]
    times = [datetime.fromisoformat(x) for x in stamps]
    require(all(x.tzinfo is not None for x in times) and times == sorted(times), 'Recorded stage time order')
    commits = [feature['git_commit'], selection['git_commit'], result['git_commit']]
    evidence = []
    for commit in commits:
        evidence.append(blob(repo,commit,'model/'+PLAN,files[PLAN]))
    for a,b in zip(commits,commits[1:]):
        require(subprocess.run(['git','merge-base','--is-ancestor',a,b],cwd=repo).returncode == 0,
                'Stage commits are not ancestor ordered')
    absent(repo,commits[0],'model/'+FEATURE)
    absent(repo,commits[1],'model/'+SELECTION)
    absent(repo,commits[2],'model/'+RESULTS)
    evidence.append(blob(repo,commits[1],'model/'+FEATURE,files[FEATURE]))
    evidence.append(blob(repo,commits[2],'model/'+SELECTION,files[SELECTION]))
    for path,expected in plan['source_files_sha256'].items():
        actual = sha(root/path); require(actual == expected,'Changed frozen source: '+path); files[path] = actual
    for path in plan['tracked_files']:
        evidence.append(blob(repo,commits[0],'model/'+path,plan['source_files_sha256'][path]))
    for library,expected in plan['libraries'].items():
        require(package_version(library) == expected,'Numerical library changed: '+library)
    for path,expected in feature['files_sha256'].items():
        actual=sha(root/path); require(actual == expected,'Changed feature artifact: '+path);files[path]=actual
    # Hash source bodies/receipts without decoding original PBP or source outcomes.
    for path,expected in feature['dataset_coverage']['source_files_sha256'].items():
        actual=sha(repo/path); require(actual == expected,'Changed dataset source chain: '+path)
        files['repository:'+path]=actual
    for record,key in [(selection,'prediction_file'),(result,'prediction_file_2025')]:
        binding=record[key];actual=sha(root/binding['path'])
        require(actual == binding['sha256'],'Changed prediction bytes');files[binding['path']]=actual
    feature_frame = pd.read_parquet(root/SPACE/'features.parquet')
    base = pd.read_parquet(root/BASE)
    pd.testing.assert_frame_equal(feature_frame[base.columns].reset_index(drop=True),base.reset_index(drop=True))
    require(len(base) == 5008 and set(base.season) == set(range(2020,2026)) and not base.game_id.duplicated().any(),
            'Changed repaired matchup universe')
    finite(feature_frame[list(FULL)],'full frozen predictor cache')
    eligible = feature_frame.loc[feature_frame.adjusted_history_games.ge(5)].copy()
    coverage = pd.read_parquet(root/SPACE/'ratings_coverage.parquet')
    require(coverage.game_id.tolist() == feature_frame.game_id.tolist(),'PBP coverage is not full shared game universe')
    np.testing.assert_array_equal(coverage.pbp_clock_rating,feature_frame.pbp_clock_rating)
    np.testing.assert_array_equal(coverage.pbp_conversion_rating.to_numpy()*100.,feature_frame.pbp_conversion_percentage_points)
    dates = pd.to_datetime(feature_frame.game_date,utc=True)
    cutoff = pd.to_datetime(coverage.pbp_ratings_cutoff,utc=True)
    require((cutoff <= dates).all(),'PBP cutoff is after target kickoff')
    # Match the declared existing weekly cutoff, including its timezone arithmetic.
    local=dates.dt.tz_convert('America/New_York')
    expected_cutoff=(local.dt.normalize()-pd.to_timedelta(local.dt.weekday,unit='D')).dt.tz_convert('UTC')
    np.testing.assert_array_equal(cutoff.to_numpy(),expected_cutoff.to_numpy())
    snapshots = {
        'selection': pd.read_parquet(root/selection['prediction_file']['path']),
        '2025': pd.read_parquet(root/result['prediction_file_2025']['path'])}
    saved = pd.read_csv(root/'reports/opponent_adjusted_predictions.csv')
    checks = Comparison();fits=[];forecast_errors={};replays={}
    with threadpool_limits(limits=1):
        for label,years in [('selection',range(2021,2025)),('2025',[2025])]:
            chunks=[];reported_fits=selection['fits'] if label=='selection' else result['fits_2025']
            require([x['season'] for x in reported_fits] == list(years),'Reported fold season identity')
            for year,recorded in zip(years,reported_fits):
                train=eligible.loc[eligible.season.lt(year)]
                test=eligible.loc[eligible.season.eq(year)].sort_values('game_id').reset_index(drop=True)
                result_rows=test[['game_id','season','game_date','actual_total','market_total','market_source']].copy()
                old,old_fit=fit(train,test,OLD);new,new_fit=fit(train,test,FULL)
                checks.compare(old_fit,recorded['baseline_fit'],f'{year}.baseline_fit',1e-9)
                checks.compare(new_fit,recorded['added_feature_fit'],f'{year}.added_fit',1e-9)
                reference=saved[(saved.candidate=='opponent_adjusted_ridge') & (saved.season==year)].sort_values('game_id')
                for key in ['game_id','actual_total','market_total']:
                    np.testing.assert_array_equal(reference[key].to_numpy(),result_rows[key].to_numpy())
                baseline_error=float(np.max(np.abs(old-reference.projected_total.to_numpy(float))))
                require(baseline_error <= 1e-9,'Existing saved ridge does not reproduce')
                checks.compare(baseline_error,recorded['baseline_max_abs_replay_difference'],f'{year}.saved_baseline_parity',1e-9)
                result_rows[CONFIGS[0]]=test.market_total.to_numpy(float)
                result_rows[CONFIGS[1]]=old;result_rows[CONFIGS[2]]=new
                chunks.append(result_rows)
                fits.append({'year':year,'training_games':len(train),'training_seasons':old_fit['training_seasons'],
                             'test_games':len(test),'disjoint_ids':True,'baseline_saved_max_abs_difference':baseline_error,
                             'baseline_fit':old_fit,'added_feature_fit':new_fit})
            replay=pd.concat(chunks,ignore_index=True);replays[label]=replay;published=snapshots[label]
            for key in ['game_id','season','actual_total','market_total','market_source']:
                np.testing.assert_array_equal(replay[key].to_numpy(),published[key].to_numpy())
            np.testing.assert_array_equal(pd.to_datetime(replay.game_date,utc=True).to_numpy(),
                                          pd.to_datetime(published.game_date,utc=True).to_numpy())
            forecast_errors[label]={}
            for candidate in CONFIGS:
                difference=float(np.max(np.abs(replay[candidate].to_numpy()-published[candidate].to_numpy())))
                forecast_errors[label][candidate]=difference
                require(difference <= 1e-9,'Reconstructed forecast mismatch: '+label+'/'+candidate)
    chosen=choose(replays['selection'])
    require(chosen == selection['choice'] == result['selected_on_2021_2024'],'Frozen selection mismatch')
    reports={k:period_summary(v) for k,v in replays.items()}
    checks.compare(reports['selection'],selection['report'],'selection.report')
    checks.compare(reports['selection'],result['selection_2021_2024'],'result.selection_copy')
    checks.compare(reports['2025'],result['reused_2025'],'result.reused_2025')
    return {'schema_version':VERSION,'audited_at':datetime.now(timezone.utc).isoformat(),
            'study_version':STUDY_VERSION,'passed':not checks.mismatches,
            'audit_script_sha256':sha(__file__),'file_sha256':files,'git_stage_evidence':evidence,
            'stage_commits':{'frozen':commits[0],'feature_audit_committed':commits[1],'selection_committed':commits[2]},
            'recorded_stage_timestamps':stamps,'base_games':len(base),'eligible_games':len(eligible),
            'eligibility_rule':'Only existing adjusted_history_games >=5; all three models share targets.',
            'fit_count':len(fits)*2,'annual_fits':fits,'forecast_max_abs_differences':forecast_errors,
            'numeric_comparisons':checks.numeric_checks,'max_absolute_numeric_difference':checks.max_absolute_difference,
            'mismatches':checks.mismatches,'selected_on_2021_2024':chosen,'independent_reports':reports,
            'limitations':['All historical periods, including 2025, are reused development; stage ordering is not an untouched holdout.',
                          'Ridge numerics and downstream scores were reconstructed; sparse historical rating fits are not refitted by this audit.',
                          'Historical archive corrections and kickoff+6h proxies do not prove original publication availability.',
                          'Whole-week intervals do not adjust for the full research search or every shared-team dependency.',
                          'Point-loss improvement is not calibrated betting probability, executable EV, priced ROI or prospective profit.'],
            'no_active_policy_changes':True,'no_2026_data':True,'no_edge_claim':True}


def render(report):
    lines=['# Independent audit of PBP state matchup results','',
           f"Audit status: **{'passed' if report['passed'] else 'FAILED'}**. Reconstructed {report['fit_count']} annual residual ridge fits without importing the study's fitting or scoring functions.",'',
           f"The fixed 2021–2024 MSE selection reproduces `{report['selected_on_2021_2024']}`. All {report['numeric_comparisons']:,} checked numeric values agree within the declared tolerances; {len(report['mismatches'])} mismatches. Maximum absolute numeric difference: {report['max_absolute_numeric_difference']:.3g}.",'',
           '| Period | Model | Games | MSE | MAE |','| --- | --- | ---: | ---: | ---: |']
    for label,period in report['independent_reports'].items():
        for name,m in period['pooled']['metrics'].items():
            lines.append(f"| {label} | {name} | {m['games']} | {m['mse']:.6f} | {m['mae']:.6f} |")
    lines += ['', 'The audit verifies immutable source/cache/prediction hashes, recorded Git blobs and stage ancestry, training-only mean/scale transforms, penalty 200 on every coefficient including intercept, residual clipping, strictly earlier seasons, disjoint target IDs, and saved baseline parity. It independently recomputes every pooled, annual and source MSE/MAE summary and the fixed 10,000 whole-week ratio bootstraps (PCG64 seed 20260909; 95%/99% MSE and 95% MAE intervals).','',
              'All history remains reused development. This audit does not refit the upstream sparse play-state ratings and does not certify historical publication time. Week intervals do not account for the full project search. The experiment evaluates point forecasts; no executable EV, priced profitability, new active policy or confirmed edge follows from this audit.','']
    return '\n'.join(lines)


def write_reports(prefix, report):
    paths=[prefix.with_suffix('.json'),prefix.with_suffix('.md')]
    require(not any(p.exists() for p in paths),'Audit output already exists')
    prefix.parent.mkdir(parents=True,exist_ok=True)
    with paths[0].open('x') as stream:json.dump(report,stream,indent=2,allow_nan=False);stream.write('\n')
    with paths[1].open('x') as stream:stream.write(render(report))
    return paths


def self_test():
    """Synthetic only: no repository data, historical labels or fitted artifacts."""
    rng=np.random.default_rng(1937)
    def sample(n,year):
        f=pd.DataFrame(rng.normal(size=(n,len(FULL))),columns=FULL)
        f['game_id']=year*10000+np.arange(n);f['season']=year
        f['market_total']=50.;f['actual_total']=54.
        return f
    train,test=sample(200,2020),sample(12,2021)
    train.loc[:,list(FULL)]=0.;train['market_total']=50.
    test.loc[:,list(FULL)]=0.;test['market_total']=50.
    pred,meta=fit(train,test,FULL)
    np.testing.assert_allclose(pred,52.,atol=1e-12)
    np.testing.assert_allclose(meta['coefficients'][0],2.,atol=1e-12)
    np.testing.assert_allclose(meta['coefficients'][1:],0.,atol=1e-12)
    require(meta['scale']==[1.]*len(FULL),'Scale floor test')
    t,u=sample(170,2020),sample(8,2021);t['actual_total']+=t.pbp_clock_rating*5
    before,m=fit(t,u,FULL);changed=u.copy();changed['actual_total']=float('nan');changed['unlisted_future_outcome']=1e100
    after,_=fit(t,changed,FULL);np.testing.assert_array_equal(before,after)
    extra=u.iloc[[0]].copy();extra['game_id']=999;extra['pbp_clock_rating']=1e8
    extended,em=fit(t,pd.concat([u,extra],ignore_index=True),FULL)
    np.testing.assert_allclose(extended[:len(u)],before,atol=1e-12)
    require(m==em|{'test_games':m['test_games'],'clipped_forecasts':m['clipped_forecasts']},'Training-only transform changed')
    require(abs(extended[-1]-50.)<=10.,'Residual clipping failed')
    for kind in ['future_training','overlap','duplicate','unplanned']:
        a,b=t.copy(),u.copy();cols=FULL
        if kind=='future_training':a['season']=2021
        if kind=='overlap':b.loc[0,'game_id']=a.game_id.iloc[0]
        if kind=='duplicate':a.loc[1,'game_id']=a.game_id.iloc[0]
        if kind=='unplanned':cols=(*FULL,'actual_total')
        try:fit(a,b,cols)
        except ValueError:pass
        else:raise AssertionError('Expected invalid contract rejection: '+kind)
    f=pd.DataFrame({'game_date':['2024-09-03T20:00:00Z','2024-09-04T20:00:00Z','2024-09-12T20:00:00Z'],
                    'actual_total':[0.,0.,0.],'a':[1.,3.,10.],'b':[0.,1.,4.]})
    check=paired(f,'a','b',draws=10000)
    blocks=np.array([[9.,3.,2.],[84.,6.,1.]])
    ix=np.random.Generator(np.random.PCG64(SEED)).integers(0,2,(10000,2));sums=blocks[ix].sum(axis=1)
    np.testing.assert_array_equal(check['mse_interval_95'],np.quantile(sums[:,0]/sums[:,2],[.025,.975]))
    np.testing.assert_array_equal(check['mse_interval_99'],np.quantile(sums[:,0]/sums[:,2],[.005,.995]))
    require(check['mse_difference']==31. and paired(f.iloc[:2],'a','b')['mse_interval_95'] is None,'Week ratio/coverage rule')
    require(week_labels(['2024-09-09T03:30:00Z','2024-09-09T04:00:00Z'])==['2024-09-02','2024-09-09'],'Eastern boundary')
    choice=pd.DataFrame({'season':[2021,2022,2023,2024],'actual_total':[50.]*4})
    for k in CONFIGS:choice[k]=50.
    require(choose(choice)==CONFIGS[0],'Fixed tie order')
    try:choose(pd.concat([choice,choice.iloc[[0]].assign(season=2025)]))
    except ValueError:pass
    else:raise AssertionError('2025 leaked into selection')
    compare=Comparison();compare.compare({'v':[1.,2]},{'v':[1.+1e-12,2]});require(not compare.mismatches,'Numerical comparison tolerance')
    print('Synthetic audit checks passed: independent penalized fit, chronology, transforms, clipping, scoring, week ratios, and selection.')


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    action=cli.add_mutually_exclusive_group(required=True)
    action.add_argument('--self-test',action='store_true')
    action.add_argument('--run',action='store_true',help='Read completed selection AND 2025 artifacts; never launch before root release')
    cli.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[2]/'model')
    cli.add_argument('--output-prefix',type=Path)
    args=cli.parse_args()
    if args.self_test:
        self_test();return
    root=args.root.resolve()
    report=audit(root)
    paths=write_reports(args.output_prefix or root/'reports/PBP_STATE_RESULTS_AUDIT_V2',report)
    print(json.dumps({'passed':report['passed'],'mismatches':len(report['mismatches']),
                      'outputs':{str(p):sha(p) for p in paths}},indent=2))
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':
    main()
