"""Bounded chronological comparison of ordinary-stat ridge and tree forecasts.

Research only. No active model, probability, EV, wager or ledger is produced.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version as package_version
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

VERSION = 'repaired-ordinary-stat-comparison-v1'
CONFIGURATIONS = ('market_only', 'opponent_adjusted_ridge', 'ordinary_ridge', 'ordinary_hgb')
NEW_MODELS = CONFIGURATIONS[2:]
YEARS = (2021, 2022, 2023, 2024, 2025)
SELECTION_YEARS = (2021, 2022, 2023, 2024)
COMPARISONS = tuple((new, base) for new in NEW_MODELS for base in CONFIGURATIONS[:2])
PROTOCOL = Path('reports/ORDINARY_MODEL_RESEARCH_PLAN.md')
PLAN = Path('reports/ordinary_model_research_plan.json')
CACHE = Path('data/normalized/ordinary_research_features_v1.parquet')
PREDICTIONS = Path('data/normalized/ordinary_research_predictions_v1.parquet')
RESULTS = Path('reports/ordinary_model_research_results.json')
CAP = 10.
MIN_TRAIN = 300
DRAWS = 10000
SEED = 20260909


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write('\n')


def factory(name):
    # Fit preprocessing only inside an earlier-season training fold. Preserve
    # entirely missing training columns as zeros, with missing indicators.
    imputer = SimpleImputer(strategy='median', add_indicator=True, keep_empty_features=True)
    if name == 'ordinary_ridge':
        return make_pipeline(imputer, StandardScaler(), Ridge(alpha=140., solver='svd'))
    if name == 'ordinary_hgb':
        return make_pipeline(imputer, HistGradientBoostingRegressor(
            loss='squared_error', learning_rate=.025, max_iter=220,
            max_leaf_nodes=10, min_samples_leaf=45, l2_regularization=20.,
            random_state=2026, early_stopping=False))
    raise ValueError('Unplanned model')


def eligible(frame):
    return frame.loc[frame.adjusted_history_games.ge(5)].copy()


def fit_fold(train, test, columns, name):
    if len(train) < MIN_TRAIN or not len(test):
        raise ValueError('Insufficient fixed-fold training or evaluation rows')
    if train.season.max() >= test.season.min() or train.game_id.isin(test.game_id).any():
        raise ValueError('Training must use strictly earlier seasons and disjoint games')
    forbidden = {'actual_total', 'home_score', 'away_score', 'game_id', 'season', 'status'}
    if not columns or len(set(columns)) != len(columns) or forbidden.intersection(columns):
        raise ValueError('Invalid explicit predictor contract')
    x = train[columns].to_numpy(float)
    future = test[columns].to_numpy(float)
    if np.isinf(x).any() or np.isinf(future).any():
        raise ValueError('Infinite model input')
    y = (train.actual_total - train.market_total).to_numpy(float)
    if not np.isfinite(y).all() or not np.isfinite(test.market_total.to_numpy(float)).all():
        raise ValueError('Invalid target or reference total')
    model = factory(name)
    with threadpool_limits(limits=1):
        model.fit(x, y)
        raw = model.predict(future)
    if not np.isfinite(raw).all():
        raise ValueError('Nonfinite fitted forecast')
    predicted = test.market_total.to_numpy(float) + np.clip(raw, -CAP, CAP)
    return predicted, {'candidate': name, 'test_season': int(test.season.min()),
        'training_games': len(train), 'training_seasons': sorted(map(int, train.season.unique())),
        'test_games': len(test), 'training_missing_values': int(np.isnan(x).sum()),
        'test_missing_values': int(np.isnan(future).sum()),
        'training_entirely_missing_columns': [column for column, empty in zip(columns, np.isnan(x).all(axis=0)) if empty],
        'clipped_predictions': int((np.abs(raw) > CAP).sum()),
        'fit_iterations': int(model[-1].n_iter_) if name == 'ordinary_hgb' else None}


def scores(actual, predicted):
    error = np.asarray(actual, float) - np.asarray(predicted, float)
    if error.ndim != 1 or not np.isfinite(error).all() or not len(error):
        raise ValueError('Nonempty finite score errors required')
    return {'games': len(error), 'mse': float(np.mean(error**2)),
            'rmse': float(np.sqrt(np.mean(error**2))), 'mae': float(np.mean(np.abs(error))),
            'mean_error': float(np.mean(error))}


def weeks(frame):
    local = pd.to_datetime(frame.game_date, utc=True).dt.tz_convert('America/New_York').dt.tz_localize(None).dt.normalize()
    return (local - pd.to_timedelta(local.dt.weekday, unit='D')).dt.strftime('%Y-%m-%d')


def contrast(frame, first, second, draws=DRAWS):
    y = frame.actual_total.to_numpy(float)
    a, b = frame[first].to_numpy(float), frame[second].to_numpy(float)
    delta = (y-a)**2 - (y-b)**2
    mae_delta = np.abs(y-a)-np.abs(y-b)
    grouped = pd.DataFrame({'week': weeks(frame), 'delta': delta, 'mae_delta': mae_delta, 'count': 1.}).groupby('week', sort=True).sum()
    result = {'candidate': first, 'reference': second, 'games': len(frame), 'calendar_week_blocks': len(grouped),
              'mse_difference': float(delta.mean()), 'mae_difference': float(mae_delta.mean()),
              'mse_difference_interval_95': None, 'mse_difference_interval_98_75': None,
              'mae_difference_interval_95': None, 'bootstrap_draws': draws, 'bootstrap_seed': SEED}
    if len(grouped) < 2:
        return result
    values = grouped[['delta', 'mae_delta', 'count']].to_numpy(float)
    rng = np.random.default_rng(SEED)
    sums = values[rng.integers(0, len(values), (draws, len(values)))].sum(axis=1)
    mse_means, mae_means = sums[:, 0]/sums[:, 2], sums[:, 1]/sums[:, 2]
    result.update(mse_difference_interval_95=np.quantile(mse_means, [.025, .975]).tolist(),
                  mse_difference_interval_98_75=np.quantile(mse_means, [.00625, .99375]).tolist(),
                  mae_difference_interval_95=np.quantile(mae_means, [.025, .975]).tolist())
    return result


def summarize(frame):
    return {'games': len(frame), 'configurations': {name: scores(frame.actual_total, frame[name]) for name in CONFIGURATIONS},
            'comparisons': [contrast(frame, a, b) for a, b in COMPARISONS]}


def select_candidate(frame):
    selection = frame.loc[frame.season.isin(SELECTION_YEARS)]
    if not len(selection):
        raise ValueError('No prescribed selection rows')
    values = {name: scores(selection.actual_total, selection[name])['mse'] for name in CONFIGURATIONS}
    return min(CONFIGURATIONS, key=lambda name: (values[name], CONFIGURATIONS.index(name)))


def freeze(root):
    from .ordinary_features_research import build_features, FEATURES
    from .calibration_research import load_oof, verify_oof
    if (root/PLAN).exists() or (root/CACHE).exists():
        raise FileExistsError('Frozen experiment already exists')
    frame, manifest = build_features(root)
    if len(frame) != 5008 or set(frame.season) != set(range(2020, 2026)) or frame.game_id.duplicated().any():
        raise ValueError('Repaired feature universe changed')
    if not frame.source_verified_pregame.eq(True).all():
        raise ValueError('Unverified market role')
    baseline = load_oof(root)
    checks = verify_oof(root, baseline)
    for year in YEARS:
        test = eligible(frame.loc[frame.season.eq(year)])
        saved = baseline.loc[baseline.season.eq(year) & baseline.candidate.eq('market_only')]
        if set(test.game_id) != set(saved.game_id):
            raise ValueError('New feature builder changed the shared evaluation cohort')
    inputs = dict(manifest['source_files_sha256'])
    for path in [PROTOCOL, Path('ncaaf_model/ordinary_features_research.py'), Path('ncaaf_model/ordinary_model_research.py'),
                 Path('ncaaf_model/calibration_research.py'), Path('ncaaf_model/opponent_model.py'),
                 Path('reports/opponent_adjusted_predictions.csv')]:
        inputs[str(path)] = sha(root/path)
    (root/CACHE).parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(root/CACHE, index=False)
    inputs[str(CACHE)] = sha(root/CACHE)
    plan = {'version': VERSION, 'frozen_at': datetime.now(timezone.utc).isoformat(),
            'source_files_sha256': inputs, 'feature_columns': list(FEATURES), 'feature_manifest': manifest,
            'baseline_reconstruction_checks': checks, 'games': len(frame),
            'test_years': list(YEARS), 'selection_years': list(SELECTION_YEARS),
            'candidate_order': list(CONFIGURATIONS), 'comparison_pairs': [list(x) for x in COMPARISONS],
            'minimum_training_games': MIN_TRAIN, 'minimum_history_games': 5, 'residual_cap': CAP,
            'primary_metric': 'mse', 'bootstrap_draws': DRAWS, 'bootstrap_seed': SEED,
            'libraries': {name: package_version(name) for name in ('numpy', 'pandas', 'scipy', 'scikit-learn', 'threadpoolctl')},
            'new_model_fits_run': False, 'no_2026_outcomes': True, 'live_policy_changes': False}
    plan['plan_sha256'] = digest(plan)
    write_new(root/PLAN, plan)
    return {'plan_sha256': plan['plan_sha256'], 'games': len(frame), 'predictors': len(FEATURES), 'new_model_fits_run': False}


def read_plan(root):
    from .ordinary_features_research import FEATURES
    plan = json.loads((root/PLAN).read_text())
    expected = plan.pop('plan_sha256')
    if digest(plan) != expected or plan['version'] != VERSION or plan['feature_columns'] != list(FEATURES):
        raise ValueError('Frozen plan or feature contract changed')
    for name, expected_sha in plan['source_files_sha256'].items():
        if sha(root/name) != expected_sha:
            raise ValueError('Frozen input changed: '+name)
    for name, expected_version in plan['libraries'].items():
        if package_version(name) != expected_version:
            raise ValueError('Frozen numerical library version changed: '+name)
    plan['plan_sha256'] = expected
    return plan


def evaluate(root):
    if (root/RESULTS).exists() or (root/PREDICTIONS).exists():
        raise FileExistsError('Research results are immutable')
    plan = read_plan(root)
    frame = pd.read_parquet(root/CACHE)
    saved = pd.read_csv(root/'reports/opponent_adjusted_predictions.csv')
    records, fits = [], []
    for year in YEARS:
        train = eligible(frame.loc[frame.season.lt(year)])
        test = eligible(frame.loc[frame.season.eq(year)]).sort_values('game_id').reset_index(drop=True)
        output = test[['game_id', 'season', 'game_date', 'actual_total', 'market_total', 'market_source']].copy()
        for name in CONFIGURATIONS[:2]:
            rows = saved.loc[saved.season.eq(year) & saved.candidate.eq(name)].sort_values('game_id')
            if not np.array_equal(rows.game_id, output.game_id) or not np.array_equal(rows.actual_total, output.actual_total) or not np.array_equal(rows.market_total, output.market_total):
                raise ValueError('Frozen baseline identities or outcomes differ')
            output[name] = rows.projected_total.to_numpy(float)
        for name in NEW_MODELS:
            output[name], details = fit_fold(train, test, plan['feature_columns'], name)
            fits.append(details)
        records.append(output)
        print(json.dumps({'stage': 'fit', 'test_season': year, 'training_games': len(train), 'test_games': len(test)}), flush=True)
    predicted = pd.concat(records, ignore_index=True)
    choice = select_candidate(predicted)
    selection = summarize(predicted.loc[predicted.season.isin(SELECTION_YEARS)].reset_index(drop=True))
    last_year = summarize(predicted.loc[predicted.season.eq(2025)].reset_index(drop=True))
    predicted.to_parquet(root/PREDICTIONS, index=False)
    result = {'version': VERSION, 'status': 'reused_development_point_prediction_study',
              'evaluated_at': datetime.now(timezone.utc).isoformat(), 'plan_sha256': plan['plan_sha256'],
              'source_files_sha256': plan['source_files_sha256'], 'prediction_file_sha256': sha(root/PREDICTIONS),
              'candidate_order': list(CONFIGURATIONS), 'primary_metric': 'mse',
              'selected_on_2021_2024': choice, 'selection_2021_2024': selection, 'reused_2025': last_year,
              'by_season': {str(year): summarize(group.reset_index(drop=True)) for year, group in predicted.groupby('season')},
              'by_market_source': {str(source): summarize(group.reset_index(drop=True)) for source, group in predicted.groupby('market_source')},
              'by_market_source_scope': 'All 2021–2025 development folds; era and source effects may be entangled.',
              'by_period_and_market_source': {
                  label: {str(source): summarize(group.reset_index(drop=True))
                          for source, group in period.groupby('market_source')}
                  for label, period in [('selection_2021_2024', predicted.loc[predicted.season.isin(SELECTION_YEARS)]),
                                        ('reused_2025', predicted.loc[predicted.season.eq(2025)])]},
              'fits': fits, 'feature_columns': plan['feature_columns'], 'no_2026_outcomes': True,
              'live_policy_changes': False, 'credible_executable_edge_established': False,
              'limitations': [
                  'All 2020–2025 outcomes have already been reused elsewhere in this project; this is neither prospective performance nor a pristine holdout.',
                  'Historical market sources establish pregame provider role, not exact morning quote timing, paired prices or accepted wagers.',
                  'Game statistics use a kickoff-plus-six-hours availability proxy and retrospective source revisions; cutoff checks do not certify historical publication.',
                  'Week intervals are descriptive. Four-comparison sensitivity does not account for the entire earlier search, shared-team cross-week dependence or regime changes.',
                  'No probabilities, EV filter, hypothetical ROI, live candidate or bankroll allocation is produced. A lower point-prediction loss alone cannot establish profitable betting.']}
    write_new(root/RESULTS, result)
    lines = ['# Ordinary-stat ridge and tree comparison', '',
             'Fixed models, prior-only game features and annual expanding training on repaired historical reference totals. All outcomes are reused development data.', '',
             f'Selection on 2021–2024 MSE chose **{choice}**. Every configuration is retained in the 2025 check.', '',
             '| Period | Configuration | Games | MSE | RMSE | MAE |', '|---|---|---:|---:|---:|---:|']
    for period, summary in [('2021–2024 selection', selection), ('2025 reused check', last_year)]:
        for name, values in summary['configurations'].items():
            lines.append(f"| {period} | {name} | {values['games']} | {values['mse']:.4f} | {values['rmse']:.4f} | {values['mae']:.4f} |")
    def interval(value):
        return 'unavailable' if value is None else ' to '.join(f'{x:+.4f}' for x in value)
    lines.extend(['', 'Negative paired differences favor the new model. MSE differences are in squared points; the intervals measure forecast-loss differences, not betting returns.', '',
                  '| Period | New model | Reference | MSE difference | Descriptive 95% interval | Four-comparison 98.75% interval |',
                  '|---|---|---|---:|---|---|'])
    for period, summary in [('2021–2024 selection', selection), ('2025 reused check', last_year)]:
        for row in summary['comparisons']:
            lines.append(f"| {period} | {row['candidate']} | {row['reference']} | {row['mse_difference']:+.4f} | {interval(row['mse_difference_interval_95'])} | {interval(row['mse_difference_interval_98_75'])} |")
    lines.extend(['', 'The JSON includes every annual/source breakdown, all four paired comparisons, descriptive 95% and four-comparison 98.75% MSE intervals, and fitting/coverage diagnostics.', '',
                  *['- '+value for value in result['limitations']]])
    (root/'reports/ordinary_model_research_results.md').write_text('\n'.join(lines)+'\n')
    return {'plan_sha256': plan['plan_sha256'], 'selected_on_2021_2024': choice,
            'selection_2021_2024': selection, 'reused_2025': last_year}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    stages = parser.add_mutually_exclusive_group(required=True)
    stages.add_argument('--freeze', action='store_true')
    stages.add_argument('--evaluate', action='store_true')
    args = parser.parse_args()
    print(json.dumps(freeze(args.root) if args.freeze else evaluate(args.root), indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
