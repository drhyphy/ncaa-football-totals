"""Frozen, staged comparison of two raw-play conditional ratings.

Research only: no active artifact, probability, EV, wagering or ledger writes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from . import opponent_model
from .ordinary_features_research import validate_cache
from .ordinary_model_research import scores, weeks

VERSION = 'pbp-state-residual-comparison-v1'
PLAN = Path('reports/pbp_state_research_plan.json')
SPEC = Path('reports/PBP_STATE_RESEARCH_PLAN.md')
BASE = Path('data/normalized/opponent_features_verified_cf764f803b38ecfd.parquet')
SPACE = Path('data/normalized/pbp_state_v1')
FEATURE_AUDIT = Path('reports/pbp_state_feature_audit.json')
SELECTION = Path('reports/pbp_state_selection.json')
RESULTS = Path('reports/pbp_state_results.json')
CONFIGS = ('market_only', 'opponent_adjusted_ridge', 'pbp_state_ridge')
NEW_COLUMNS = ('pbp_clock_rating', 'pbp_conversion_percentage_points')
FULL_COLUMNS = (*opponent_model.FEATURES, *NEW_COLUMNS)
SELECTION_YEARS = (2021, 2022, 2023, 2024)
MODULES = ('pbp_state_rows', 'pbp_state_ratings', 'pbp_state_dataset', 'pbp_state_research')
LIBRARIES = ('numpy', 'pandas', 'scipy', 'scikit-learn', 'pyarrow', 'threadpoolctl')


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as f:
        json.dump(value, f, indent=2, allow_nan=False)
        f.write('\n')


def committed(root, relatives):
    """Require selected tracked bytes to match this checkout's HEAD."""
    repo = root.parent
    for relative in relatives:
        path = root / relative
        blob = subprocess.check_output(['git', 'show', 'HEAD:model/'+str(relative)], cwd=repo)
        if path.is_symlink() or path.read_bytes() != blob:
            raise ValueError('Required tracked bytes differ from HEAD: '+str(relative))
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo).decode().strip()


def freeze(root):
    if (root/PLAN).exists() or (root/SPACE).exists():
        raise FileExistsError('This experiment is already frozen or has generated data')
    prior = json.loads((root/'reports/direct_probability_research_plan.json').read_text())
    inputs = dict(prior['source_files_sha256'])
    tracked = [SPEC, Path('reports/PBP_RESEARCH_SOURCE_INVENTORY.json'),
               Path('reports/PBP_RAW_ACQUISITION_AUDIT.json'),
               Path('requirements-lock.txt'), Path('pyproject.toml'),
               Path('ncaaf_model/ordinary_model_research.py'),
               Path('ncaaf_model/ordinary_features_research.py')]
    for module in MODULES:
        tracked.extend([Path('ncaaf_model')/(module+'.py'), Path('tests')/('test_'+module+'.py')])
    for item in tracked:
        inputs[str(item)] = sha(root/item)
    for year in range(2019, 2026):
        item = Path(f'data/raw/sportsdataverse/cfb_schedule_{year}.parquet')
        inputs[str(item)] = sha(root/item)
    for item, expected in inputs.items():
        if sha(root/item) != expected:
            raise ValueError('Previously audited input changed: '+item)
    plan = {'version': VERSION, 'frozen_at': now(), 'source_files_sha256': inputs,
            'tracked_files': list(map(str, tracked)), 'libraries': {k: version(k) for k in LIBRARIES},
            'candidate_order': list(CONFIGS), 'new_predictors': list(NEW_COLUMNS),
            'selection_years': list(SELECTION_YEARS), 'later_check_year': 2025,
            'primary_metric': 'mse', 'plays_read': False, 'new_model_fits_run': False,
            'no_2026_outcomes': True, 'live_policy_changes': False}
    write_new(root/PLAN, plan)
    return {'status': 'frozen', 'plan_sha256': sha(root/PLAN), 'plays_read': False}


def read_plan(root):
    plan = json.loads((root/PLAN).read_text())
    if (plan['version'] != VERSION or plan['candidate_order'] != list(CONFIGS)
            or plan['new_predictors'] != list(NEW_COLUMNS)):
        raise ValueError('Frozen scientific specification changed')
    for item, expected in plan['source_files_sha256'].items():
        if sha(root/item) != expected:
            raise ValueError('Frozen source changed: '+item)
    if {k: version(k) for k in LIBRARIES} != plan['libraries']:
        raise ValueError('Frozen numerical package versions changed')
    head = committed(root, [PLAN, *map(Path, plan['tracked_files'])])
    return plan, head


def base_frame(root):
    frame = pd.read_parquet(root/BASE)
    validate_cache(frame, opponent_model.load_market_games(root))
    if len(frame) != 5008 or set(frame.season) != set(range(2020, 2026)) or frame.game_id.duplicated().any():
        raise ValueError('Repaired game universe changed')
    return frame


def build(root):
    from .pbp_state_dataset import load_rows
    from .pbp_state_ratings import query_pbp_features
    plan, head = read_plan(root)
    if (root/SPACE).exists() or (root/FEATURE_AUDIT).exists():
        raise FileExistsError('Feature construction outputs are immutable')
    frame = base_frame(root)
    rows, coverage = load_rows(root)
    # The rating utility cannot see the target matchup's outcomes or market line.
    query = frame[['game_id', 'season', 'week', 'game_date', 'home_id', 'away_id']].copy()
    with threadpool_limits(limits=1):
        rated, metadata = query_pbp_features(query, rows)
    if not np.array_equal(query.game_id.to_numpy(), rated.game_id.to_numpy()):
        raise ValueError('Rating output identity/order mismatch')
    frame['pbp_clock_rating'] = rated.pbp_clock_rating.to_numpy(float)
    frame['pbp_conversion_percentage_points'] = rated.pbp_conversion_rating.to_numpy(float)*100.
    if not np.isfinite(frame[list(FULL_COLUMNS)].to_numpy(float)).all():
        raise ValueError('Nonfinite final feature')
    directory = root/SPACE
    directory.mkdir(parents=True)
    rows.to_parquet(directory/'rows.parquet', index=False)
    frame.to_parquet(directory/'features.parquet', index=False)
    rated.to_parquet(directory/'ratings_coverage.parquet', index=False)
    write_new(directory/'ratings_metadata.json', metadata)
    files = {str(p.relative_to(root)): sha(p) for p in directory.iterdir()}
    audit = {'version': VERSION, 'built_at': now(), 'git_commit': head,
             'plan_sha256': sha(root/PLAN), 'files_sha256': files,
             'games': len(frame), 'retained_play_rows': len(rows),
             'feature_columns': list(FULL_COLUMNS), 'dataset_coverage': coverage,
             'new_rating_fits_run': True, 'matchup_prediction_scores_computed': False,
             'no_2026_outcomes': True, 'target_game_pbp_coverage_gate': False}
    write_new(root/FEATURE_AUDIT, audit)
    return {'status': 'features_built', 'games': len(frame), 'retained_play_rows': len(rows),
            'feature_audit_sha256': sha(root/FEATURE_AUDIT), 'matchup_scores_computed': False}


def load_features(root):
    read_plan(root)
    committed(root, [FEATURE_AUDIT])
    audit = json.loads((root/FEATURE_AUDIT).read_text())
    if audit['plan_sha256'] != sha(root/PLAN):
        raise ValueError('Feature audit belongs to another plan')
    for path, expected in audit['files_sha256'].items():
        if sha(root/path) != expected:
            raise ValueError('Frozen normalized feature file changed: '+path)
    f = pd.read_parquet(root/SPACE/'features.parquet')
    base = base_frame(root)
    # Reconcile original cache fields exactly; only two added predictors vary.
    pd.testing.assert_frame_equal(f[base.columns].reset_index(drop=True), base.reset_index(drop=True))
    return f.loc[f.adjusted_history_games.ge(5)].copy()


def fit_residual(train, test, columns):
    if (not len(train) or not len(test) or train.season.max() >= test.season.min()
            or test.season.nunique() != 1 or train.game_id.isin(test.game_id).any()):
        raise ValueError('Strict earlier-season training and disjoint future cohort required')
    if tuple(columns) not in (tuple(opponent_model.FEATURES), FULL_COLUMNS):
        raise ValueError('Unplanned predictor configuration')
    x, future = train[list(columns)].to_numpy(float), test[list(columns)].to_numpy(float)
    if not np.isfinite(x).all() or not np.isfinite(future).all():
        raise ValueError('Fixed features must be finite')
    y = (train.actual_total-train.market_total).to_numpy(float)
    if not np.isfinite(y).all():
        raise ValueError('Nonfinite training outcome')
    med = np.median(x, axis=0)
    center, scale = x.mean(axis=0), np.maximum(x.std(axis=0), 1.)
    design = np.column_stack([np.ones(len(x)), (x-center)/scale])
    coefficients = np.linalg.solve(design.T@design + 200.*np.eye(design.shape[1]), design.T@y)
    raw = np.column_stack([np.ones(len(future)), (future-center)/scale])@coefficients
    forecasts = test.market_total.to_numpy(float) + np.clip(raw, -10., 10.)
    if not np.isfinite(forecasts).all():
        raise ValueError('Nonfinite forecast')
    return forecasts, {'test_season': int(test.season.min()), 'training_games': len(train),
                       'training_seasons': sorted(map(int, train.season.unique())), 'test_games': len(test),
                       'features': list(columns), 'median': med.tolist(), 'center': center.tolist(),
                       'scale': scale.tolist(), 'coefficients': coefficients.tolist(),
                       'clipped_forecasts': int((abs(raw)>10.).sum())}


def paired(frame, first, second, draws=10000):
    y = frame.actual_total.to_numpy(float)
    a, b = frame[first].to_numpy(float), frame[second].to_numpy(float)
    delta, absolute = (y-a)**2-(y-b)**2, abs(y-a)-abs(y-b)
    group = pd.DataFrame({'week': weeks(frame), 'mse': delta, 'mae': absolute,
                          'count': np.ones(len(frame))}).groupby('week', sort=True).sum()
    result = {'candidate': first, 'reference': second, 'games': len(frame), 'week_blocks': len(group),
              'mse_difference': float(delta.mean()), 'mae_difference': float(absolute.mean()),
              'mse_interval_95': None, 'mse_interval_99': None, 'mae_interval_95': None,
              'draws': draws, 'seed': 20260909}
    if len(group) >= 2:
        values = group[['mse', 'mae', 'count']].to_numpy(float)
        rng = np.random.Generator(np.random.PCG64(20260909))
        sums = values[rng.integers(0, len(values), (draws, len(values)))].sum(axis=1)
        mse, mae = sums[:,0]/sums[:,2], sums[:,1]/sums[:,2]
        result.update(mse_interval_95=np.quantile(mse, [.025,.975]).tolist(),
                      mse_interval_99=np.quantile(mse, [.005,.995]).tolist(),
                      mae_interval_95=np.quantile(mae, [.025,.975]).tolist())
    return result


def summary(frame):
    return {'games': len(frame), 'metrics': {k: scores(frame.actual_total, frame[k]) for k in CONFIGS},
            'comparisons': [paired(frame, 'pbp_state_ridge', ref) for ref in CONFIGS[:2]]}


def choose(frame):
    if set(frame.season) != set(SELECTION_YEARS):
        raise ValueError('Choice requires exactly 2021–2024, without 2025')
    return min(CONFIGS, key=lambda name: (scores(frame.actual_total, frame[name])['mse'], CONFIGS.index(name)))


def predictions(root, frame, years):
    saved = pd.read_csv(root/'reports/opponent_adjusted_predictions.csv')
    pieces, metadata = [], []
    for year in years:
        train = frame.loc[frame.season.lt(year)]
        test = frame.loc[frame.season.eq(year)].sort_values('game_id').reset_index(drop=True)
        out = test[['game_id','season','game_date','actual_total','market_total','market_source']].copy()
        old, old_fit = fit_residual(train, test, opponent_model.FEATURES)
        expected = saved.loc[saved.candidate.eq('opponent_adjusted_ridge') & saved.season.eq(year)].sort_values('game_id')
        if (not np.array_equal(expected.game_id.to_numpy(), out.game_id.to_numpy())
                or not np.array_equal(expected.actual_total.to_numpy(), out.actual_total.to_numpy())
                or not np.array_equal(expected.market_total.to_numpy(), out.market_total.to_numpy())
                or not np.allclose(expected.projected_total.to_numpy(), old, atol=1e-10, rtol=0)):
            raise ValueError('Original ridge reconstruction or shared test cohort changed')
        new, new_fit = fit_residual(train, test, FULL_COLUMNS)
        out['market_only'], out['opponent_adjusted_ridge'], out['pbp_state_ridge'] = out.market_total, old, new
        pieces.append(out)
        metadata.append({'season': year, 'baseline_fit': old_fit, 'added_feature_fit': new_fit,
                         'baseline_max_abs_replay_difference': float(abs(expected.projected_total.to_numpy()-old).max())})
        print(json.dumps({'stage': 'matchup_fit', 'test_year': year, 'training_games': len(train), 'test_games': len(test)}), flush=True)
    return pd.concat(pieces, ignore_index=True), metadata


def period_report(frame):
    return {'pooled': summary(frame),
            'by_season': {str(y): summary(g.reset_index(drop=True)) for y,g in frame.groupby('season')},
            'by_source': {str(s): summary(g.reset_index(drop=True)) for s,g in frame.groupby('market_source')}}


def save_forecasts(root, name, predicted):
    path = root/SPACE/(name+'.parquet')
    if path.exists():
        raise FileExistsError('Forecasts are immutable')
    predicted.to_parquet(path, index=False)
    return {'path': str(path.relative_to(root)), 'sha256': sha(path)}


def select(root):
    if (root/SELECTION).exists() or (root/RESULTS).exists() or (root/SPACE/'selection.parquet').exists():
        raise FileExistsError('Selection already exists')
    frame = load_features(root)
    predicted, fits = predictions(root, frame.loc[frame.season.le(2024)], SELECTION_YEARS)
    choice = choose(predicted)
    record = {'version': VERSION, 'selected_at': now(), 'git_commit': committed(root,[PLAN,FEATURE_AUDIT]),
              'plan_sha256': sha(root/PLAN), 'feature_audit_sha256': sha(root/FEATURE_AUDIT),
              'prediction_file': save_forecasts(root, 'selection', predicted), 'choice': choice,
              'report': period_report(predicted), 'fits': fits, 'new_2025_matchup_forecasts_computed': False,
              'no_2026_outcomes': True, 'live_policy_changes': False}
    write_new(root/SELECTION, record)
    return {'status': 'selection_complete', 'choice': choice, 'metrics': record['report']['pooled']['metrics'],
            'selection_sha256': sha(root/SELECTION)}


def verify_selection(root):
    head = committed(root,[SELECTION])
    record = json.loads((root/SELECTION).read_text())
    if record['plan_sha256'] != sha(root/PLAN) or record['feature_audit_sha256'] != sha(root/FEATURE_AUDIT):
        raise ValueError('Selection source context changed')
    source = record['prediction_file']
    if sha(root/source['path']) != source['sha256']:
        raise ValueError('Selection forecasts changed')
    if choose(pd.read_parquet(root/source['path'])) != record['choice']:
        raise ValueError('Earlier-year choice does not reproduce')
    return record, head


def check(root):
    if (root/RESULTS).exists() or (root/SPACE/'2025.parquet').exists():
        raise FileExistsError('Later check already exists')
    read_plan(root)
    selected, head = verify_selection(root)  # Before new 2025 forecast or metric.
    frame = load_features(root)
    predicted, fits = predictions(root, frame, [2025])
    result = {'version': VERSION, 'checked_at': now(), 'git_commit': head,
              'plan_sha256': sha(root/PLAN), 'selection_sha256': sha(root/SELECTION),
              'selected_on_2021_2024': selected['choice'], 'selection_2021_2024': selected['report'],
              'reused_2025': period_report(predicted), 'fits_2025': fits,
              'prediction_file_2025': save_forecasts(root, '2025', predicted),
              'no_2026_outcomes': True, 'live_policy_changes': False,
              'credible_executable_edge_established': False,
              'limitations': ['Every historical year has already influenced this project; 2025 is reused development.',
                             'Week intervals do not correct the full search history or all shared-team dependence.',
                             'Archived source corrections and the kickoff-plus-six-hours proxy do not certify historical delivery.',
                             'Recorded game-clock consumption is not verified snap timing.',
                             'This experiment produces point forecasts, not EV, priced ROI or a probability artifact.']}
    write_new(root/RESULTS, result)
    return {'status': 'later_check_complete', 'selected_on_2021_2024': selected['choice'],
            'metrics_2025': result['reused_2025']['pooled']['metrics'],
            'comparisons_2025': result['reused_2025']['pooled']['comparisons']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('freeze','build','select','check'))
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    print(json.dumps(globals()[args.stage](args.root.resolve()), indent=2, allow_nan=False), flush=True)


if __name__ == '__main__':
    main()
