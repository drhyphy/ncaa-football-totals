"""Staged, fixed integer-score shape comparison on repaired historical forecasts."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version as package_version
import json
from pathlib import Path
import subprocess
from uuid import uuid4

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from . import calibration_research as calibration
from .conditional_distribution import normal_pmf
from .score_shape import ScoreShape
from .score_shape_scoring import score_pmf, summary_metrics, paired_summary

VERSION = 'repaired-moment-preserving-score-shape-v1'
CONFIGS = ('market_normal', 'ridge_normal', 'market_score_shape')
YEARS = (2022, 2023, 2024)
PRIMARY = 'three_outcome_nll'
PLAN = Path('reports/score_shape_research_plan.json')
SPEC = Path('reports/SCORE_SHAPE_RESEARCH_PLAN.md')
SELECTION = Path('reports/score_shape_selection.json')
RESULTS = Path('reports/score_shape_results.json')
SPACE = Path('data/normalized/score_shape_v1')
ATTEMPTS = Path('data/normalized/score_shape_attempts_v1')
LIBRARIES = ('numpy','pandas','scipy','scikit-learn','pyarrow','threadpoolctl')
CORE_PINS = {
    'reports/opponent_adjusted_predictions.csv': '1b032e26993c7c15f98f74c26ed69d6112520da8dbae442e41b20ea602b0b524',
    calibration.FEATURE_CACHE: 'ec33e9add2cb5b84742a17e65c313ca225503f6edadba125fbf7eeb5a45bb73c',
}


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def write_new(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as f:
        json.dump(record, f, indent=2, allow_nan=False)
        f.write('\n')


def committed(root, paths):
    for relative in paths:
        path = root/relative
        blob = subprocess.check_output(['git','show','HEAD:model/'+str(relative)], cwd=root.parent)
        if path.is_symlink() or path.read_bytes() != blob:
            raise ValueError('Required tracked bytes differ from HEAD: '+str(relative))
    return subprocess.check_output(['git','rev-parse','HEAD'], cwd=root.parent).decode().strip()


def freeze(root):
    if any((root/p).exists() for p in (PLAN, SELECTION, RESULTS, SPACE)):
        raise FileExistsError('An experiment plan or generated output already exists')
    modules = ('score_shape','score_shape_scoring','score_shape_research')
    tracked = [SPEC, Path('requirements-lock.txt'), Path('pyproject.toml'),
               Path('ncaaf_model/calibration_research.py'), Path('ncaaf_model/conditional_distribution.py'),
               Path('ncaaf_model/opponent_model.py'), Path('reports/opponent_adjusted_predictions.csv'),
               Path('reports/opponent_adjusted_development.json'), Path('reports/calibration_research_plan.json'),
               Path('reports/calibration_research_audit.json'), Path('reports/CALIBRATION_RESEARCH_AUDIT.md')]
    for name in modules:
        tracked.extend((Path('ncaaf_model')/(name+'.py'), Path('tests')/('test_'+name+'.py')))
    inputs = {str(path):sha(root/path) for path in tracked}
    inputs[calibration.FEATURE_CACHE] = sha(root/calibration.FEATURE_CACHE)
    for path, expected in CORE_PINS.items():
        if inputs[path] != expected:
            raise ValueError('Previously repaired source pin changed: '+path)
    record = {'version':VERSION, 'frozen_at':now(), 'tracked_files':list(map(str,tracked)),
              'source_files_sha256':inputs, 'libraries':{k:package_version(k) for k in LIBRARIES},
              'candidate_order':list(CONFIGS), 'selection_years':list(YEARS), 'later_check_year':2025,
              'warmup_year':2021, 'primary_metric':PRIMARY, 'new_shape_fits_or_scores_computed':False,
              'all_historical_years_reused':True, 'no_2026_outcomes':True, 'live_policy_changes':False}
    write_new(root/PLAN,record)
    return {'status':'plan_frozen', 'plan_sha256':sha(root/PLAN), 'new_shape_fits_or_scores_computed':False}


def read_plan(root):
    plan = json.loads((root/PLAN).read_text())
    if (plan['version'] != VERSION or plan['candidate_order'] != list(CONFIGS)
            or plan['selection_years'] != list(YEARS) or plan['primary_metric'] != PRIMARY):
        raise ValueError('Fixed study configuration changed')
    for path, expected in plan['source_files_sha256'].items():
        if sha(root/path) != expected:
            raise ValueError('Frozen input changed: '+path)
    if {k:package_version(k) for k in LIBRARIES} != plan['libraries']:
        raise ValueError('Frozen numerical package versions changed')
    return plan, committed(root,[PLAN,*map(Path,plan['tracked_files'])])


def validate_sources(frame, cache):
    """Enforce source/game equalities beyond the reused baseline loader."""
    if cache.game_id.duplicated().any() or len(cache) != 5008:
        raise ValueError('Verified feature universe changed')
    expected = cache.loc[cache.season.between(2021,2025) & cache.adjusted_history_games.ge(5)].set_index('game_id').sort_index()
    shared = ['season','week','home_id','away_id','home_team','away_team','home_score','away_score',
              'actual_total','market_total','market_home_spread','neutral_site','status','market_source',
              'source_verified_pregame','adjusted_history_games','ratings_training_rows']
    for name in calibration.BASES:
        actual = frame.loc[frame.candidate.eq(name)].set_index('game_id').sort_index()
        if not actual.index.equals(expected.index):
            raise ValueError('OOF/cache game universe mismatch')
        pd.testing.assert_frame_equal(actual[shared],expected[shared],check_dtype=False,check_exact=True)
        for field in ('game_date','ratings_cutoff'):
            if not np.array_equal(pd.to_datetime(actual[field],utc=True).to_numpy(),pd.to_datetime(expected[field],utc=True).to_numpy()):
                raise ValueError('OOF/cache date or cutoff mismatch: '+field)
    market = frame.loc[frame.candidate.eq('market_only')]
    if not np.array_equal(market.projected_total.to_numpy(),market.market_total.to_numpy()):
        raise ValueError('Market reference center differs from its actual line')


def load_baselines(root):
    frame = calibration.load_oof(root)
    validate_sources(frame,pd.read_parquet(root/calibration.FEATURE_CACHE))
    return frame


def probabilities(rows):
    return normal_pmf(rows.projected_total.to_numpy(float),np.clip(rows.residual_sigma.to_numpy(float),6.,30.))


def annual_forecasts(frame, years):
    pieces, fits = [], []
    market = frame.loc[frame.candidate.eq('market_only')]
    for year in years:
        if year not in (*YEARS,2025):
            raise ValueError('Unplanned test year')
        train = market.loc[market.season.lt(year)].sort_values(['season','game_id']).reset_index(drop=True)
        test = market.loc[market.season.eq(year)].sort_values('game_id').reset_index(drop=True)
        ridge = frame.loc[frame.candidate.eq('opponent_adjusted_ridge') & frame.season.eq(year)].sort_values('game_id').reset_index(drop=True)
        if (not len(train) or not len(test) or not np.array_equal(test.game_id,ridge.game_id)
                or train.game_id.isin(test.game_id).any() or set(train.season) != set(range(2021,year))):
            raise ValueError('Strictly earlier disjoint shape history required')
        fitted = ScoreShape.fit(train.actual_total.to_numpy(),probabilities(train),train.season.to_numpy())
        base = probabilities(test)
        shaped, diagnostics = fitted.predict_pmf(base,test.season.to_numpy())
        for name,pmf in zip(CONFIGS,(base,probabilities(ridge),shaped)):
            scored = score_pmf(test,pmf)
            scored['configuration'] = name
            pieces.append(scored)
        fits.append({'test_year':year, 'test_games':len(test), 'fit':fitted.metadata(),
                     'prediction_game_ids':test.game_id.astype(int).tolist(), 'moment_diagnostics':diagnostics})
        print(json.dumps({'stage':'shape_fit','test_year':year,'training_games':len(train),'test_games':len(test)}),flush=True)
    return pd.concat(pieces,ignore_index=True),fits


def summarize(rows):
    groups = {name:rows.loc[rows.configuration.eq(name)].reset_index(drop=True) for name in CONFIGS}
    if any(not len(group) for group in groups.values()):
        raise ValueError('Every comparison must preserve all configurations')
    comparisons = []
    for reference in CONFIGS[:2]:
        comparisons.append({'candidate':CONFIGS[-1],'reference':reference,
                            **paired_summary(groups[CONFIGS[-1]],groups[reference])})
    return {'games':len(groups[CONFIGS[0]]), 'configurations':{k:summary_metrics(v) for k,v in groups.items()},
            'comparisons':comparisons}


def period_report(rows):
    return {'pooled':summarize(rows),
            'by_season':{str(k):summarize(g.reset_index(drop=True)) for k,g in rows.groupby('season')},
            'by_source':{str(k):summarize(g.reset_index(drop=True)) for k,g in rows.groupby('market_source')}}


def choose(rows):
    if set(rows.season) != set(YEARS):
        raise ValueError('Choice requires exactly the pre-2025 evaluation years')
    keys = None
    for name in CONFIGS:
        group = rows.loc[rows.configuration.eq(name)]
        if group.game_id.duplicated().any():
            raise ValueError('Duplicate selection game')
        current = sorted(zip(group.game_id,group.season))
        if keys is None:
            keys = current
        elif current != keys:
            raise ValueError('Selection configurations must share all games')
    losses = {name:float(rows.loc[rows.configuration.eq(name),PRIMARY].mean()) for name in CONFIGS}
    if set(rows.configuration) != set(CONFIGS) or not all(np.isfinite(v) for v in losses.values()):
        raise ValueError('Incomplete or nonfinite selection losses')
    return min(CONFIGS,key=lambda name:(losses[name],CONFIGS.index(name)))


def save_forecasts(root,name,frame):
    path = root/SPACE/(name+'.parquet')
    if path.exists():
        raise FileExistsError('Research forecasts are immutable')
    path.parent.mkdir(parents=True,exist_ok=True)
    frame.to_parquet(path,index=False)
    return {'path':str(path.relative_to(root)),'sha256':sha(path)}


def select(root):
    _,head = read_plan(root)
    if any((root/path).exists() for path in (SELECTION,RESULTS,SPACE)):
        raise FileExistsError('Selection or a generated stage already exists')
    frame = load_baselines(root)
    with threadpool_limits(limits=1):
        replay = calibration.verify_oof(root,frame)
        forecasts,fits = annual_forecasts(frame.loc[frame.season.le(2024)],YEARS)
    choice = choose(forecasts)
    report = period_report(forecasts)
    record = {'version':VERSION,'selected_at':now(),'git_commit':head,'plan_sha256':sha(root/PLAN),
              'choice':choice,'report':report,'fits':fits,'original_reference_reconstruction':replay,
              'prediction_file':save_forecasts(root,'selection',forecasts),
              'new_2025_shape_forecasts_computed':False,'no_2026_outcomes':True,'live_policy_changes':False}
    write_new(root/SELECTION,record)
    return {'status':'selection_complete','choice':choice,'pooled':report['pooled']['configurations'],
            'selection_sha256':sha(root/SELECTION)}


def verify_selection(root):
    head = committed(root,[SELECTION])
    record = json.loads((root/SELECTION).read_text())
    if record['version'] != VERSION or record['plan_sha256'] != sha(root/PLAN) or record['new_2025_shape_forecasts_computed'] is not False:
        raise ValueError('Selection context changed')
    file = record['prediction_file']
    if sha(root/file['path']) != file['sha256'] or choose(pd.read_parquet(root/file['path'])) != record['choice']:
        raise ValueError('Frozen earlier selection does not reproduce')
    return record,head


def check(root):
    read_plan(root)
    selected,head = verify_selection(root)
    if (root/RESULTS).exists() or (root/SPACE/'2025.parquet').exists():
        raise FileExistsError('The later check already exists')
    frame = load_baselines(root)
    with threadpool_limits(limits=1):
        forecasts,fits = annual_forecasts(frame,[2025])
    report = period_report(forecasts)
    record = {'version':VERSION,'checked_at':now(),'git_commit':head,'plan_sha256':sha(root/PLAN),
              'selection_sha256':sha(root/SELECTION),'selected_on_2022_2024':selected['choice'],
              'selection_2022_2024':selected['report'],'reused_2025':report,'fits_2025':fits,
              'prediction_file_2025':save_forecasts(root,'2025',forecasts),
              'no_2026_outcomes':True,'live_policy_changes':False,'credible_executable_edge_established':False,
              'probability_artifact_promoted':False,'roi_evaluated':False,
              'limitations':['All historical years are reused development data.',
                 'Week intervals condition on fitted forecasts; they omit training-estimation uncertainty and the full search history.',
                 'There are no actual integer market lines in the 2024 or 2025 evaluation cohorts.',
                 'Historical quote receipt times and paired offered prices are unavailable.',
                 'Shape isolation does not identify a causal scoring mechanism or establish executable profitability.']}
    write_new(root/RESULTS,record)
    return {'status':'later_check_complete','selected_on_2022_2024':selected['choice'],
            'pooled_2025':report['pooled']['configurations'],'comparisons_2025':report['pooled']['comparisons']}


def run_stage(root,stage):
    """Retain an immutable start and terminal receipt, including failed stages."""
    if stage not in ('freeze','select','check'):
        raise ValueError('Unknown research stage')
    identifier = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'-'+uuid4().hex[:12]
    folder = root/ATTEMPTS/identifier
    started = {'stage':stage,'started_at':now(),
               'git_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root.parent).decode().strip(),
               'plan_sha256':sha(root/PLAN) if (root/PLAN).exists() else None}
    write_new(folder/'started.json',started)
    try:
        result = globals()[stage](root)
    except BaseException as error:
        write_new(folder/'finished.json',{'stage':stage,'finished_at':now(),'status':'failed',
                   'started_sha256':sha(folder/'started.json'),'error_type':type(error).__name__,
                   'error':str(error),'output_files_sha256':{str(p):sha(root/p) for p in (PLAN,SELECTION,RESULTS) if (root/p).exists()}})
        raise
    write_new(folder/'finished.json',{'stage':stage,'finished_at':now(),'status':'succeeded',
               'started_sha256':sha(folder/'started.json'),
               'output_files_sha256':{str(p):sha(root/p) for p in (PLAN,SELECTION,RESULTS) if (root/p).exists()}})
    return {**result,'attempt_receipt':str((folder/'finished.json').relative_to(root))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=('freeze','select','check'))
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    args=parser.parse_args()
    print(json.dumps(run_stage(args.root.resolve(),args.stage),indent=2,allow_nan=False),flush=True)


if __name__ == '__main__':
    main()
