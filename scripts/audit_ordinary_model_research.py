#!/usr/bin/env python3
"""Replay all ten frozen ordinary fits; import no project research module."""
import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from threadpoolctl import threadpool_limits

PLAN_SHA = 'c74130cef9ec2ed994f3cb75f58fc354c9639323c06590eea43d6208bc82a150'
CONFIGS = ('market_only', 'opponent_adjusted_ridge', 'ordinary_ridge', 'ordinary_hgb')
PAIRS = [(a, b) for a in CONFIGS[2:] for b in CONFIGS[:2]]


def close(a, b, tolerance=1e-9):
    if a is None or b is None:
        assert a is b
    else:
        assert np.allclose(a, b, rtol=0, atol=tolerance), (a, b)


def prepare(train, test):
    """Independent median/missing-indicator preprocessing from raw matrices."""
    columns = []
    for j in range(train.shape[1]):
        present = train[:, j][np.isfinite(train[:, j])]
        columns.append(float(np.median(present)) if len(present) else 0.)
    medians = np.array(columns)
    missing = np.isnan(train).any(axis=0)
    x = np.column_stack([np.where(np.isnan(train), medians, train), np.isnan(train[:, missing]).astype(float)])
    z = np.column_stack([np.where(np.isnan(test), medians, test), np.isnan(test[:, missing]).astype(float)])
    assert np.isfinite(x).all() and np.isfinite(z).all()
    return x, z


def replay(train, test, predictors, candidate):
    x, z = prepare(train[predictors].to_numpy(float), test[predictors].to_numpy(float))
    target = (train.actual_total-train.market_total).to_numpy(float)
    assert train.season.max() < test.season.min() and not train.game_id.isin(test.game_id).any()
    with threadpool_limits(limits=1):
        if candidate == 'ordinary_ridge':
            # Solve the centered penalized normal equations independently of
            # sklearn's pipeline, scaler, Ridge estimator and SVD solver.
            center, scale = x.mean(axis=0), x.std(axis=0)
            scale[scale == 0] = 1
            x, z = (x-center)/scale, (z-center)/scale
            x_center, y_center = x.mean(axis=0), target.mean()
            xc = x-x_center
            coef = np.linalg.solve(xc.T@xc+140*np.eye(x.shape[1]), xc.T@(target-y_center))
            residual = (z-x_center)@coef+y_center
        else:
            # Use the fixed public estimator with independently built matrices,
            # not the study factory or preprocessing pipeline.
            tree = HistGradientBoostingRegressor(loss='squared_error', learning_rate=.025,
                max_iter=220, max_leaf_nodes=10, min_samples_leaf=45, l2_regularization=20.,
                random_state=2026, early_stopping=False)
            tree.fit(x, target)
            assert tree.n_iter_ == 220
            residual = tree.predict(z)
    assert np.isfinite(residual).all()
    return test.market_total.to_numpy(float)+np.clip(residual, -10, 10), residual


def basic(y, p):
    error = np.asarray(y)-np.asarray(p)
    return {'games': len(y), 'mse': float(np.dot(error, error)/len(y)),
            'rmse': float(np.sqrt(np.dot(error, error)/len(y))),
            'mae': float(np.abs(error).mean()), 'mean_error': float(error.mean())}


def week_key(value):
    time = pd.Timestamp(value).to_pydatetime().astimezone(ZoneInfo('America/New_York'))
    return (time.date()-timedelta(days=time.weekday())).isoformat()


def paired(frame, candidate, reference, saved):
    y = frame.actual_total.to_numpy(float)
    a, b = frame[candidate].to_numpy(float), frame[reference].to_numpy(float)
    d, m = (y-a)**2-(y-b)**2, abs(y-a)-abs(y-b)
    groups = defaultdict(list)
    for index, date in enumerate(frame.game_date):
        groups[week_key(date)].append(index)
    values = np.array([[d[indexes].sum(), m[indexes].sum(), len(indexes)]
                       for _, indexes in sorted(groups.items())])
    assert saved['games'] == len(frame) and saved['calendar_week_blocks'] == len(groups)
    close(d.mean(), saved['mse_difference'])
    close(m.mean(), saved['mae_difference'])
    assert saved['bootstrap_seed'] == 20260909 and saved['bootstrap_draws'] == 10000
    result = {'candidate': candidate, 'reference': reference,
              'mse_difference': float(d.mean()), 'mae_difference': float(m.mean()),
              'weeks': len(groups)}
    if len(groups) < 2:
        assert all(saved[k] is None for k in ['mse_difference_interval_95', 'mse_difference_interval_98_75', 'mae_difference_interval_95'])
        return result
    rng = np.random.default_rng(20260909)
    draws = rng.integers(0, len(groups), size=(10000, len(groups)))
    multiplicities = np.array([np.bincount(row, minlength=len(groups)) for row in draws])
    totals = multiplicities@values
    for field, column, q in [('mse_difference_interval_95', 0, [.025, .975]),
                             ('mse_difference_interval_98_75', 0, [.00625, .99375]),
                             ('mae_difference_interval_95', 1, [.025, .975])]:
        interval = np.quantile(totals[:, column]/totals[:, 2], q).tolist()
        close(interval, saved[field])
        result[field] = interval
    return result


def summary(frame, saved):
    assert len(frame) == saved['games']
    calculated = {name: basic(frame.actual_total, frame[name]) for name in CONFIGS}
    for name, metrics in calculated.items():
        for key, value in metrics.items():
            close(value, saved['configurations'][name][key])
    pairs = {(r['candidate'], r['reference']): r for r in saved['comparisons']}
    assert set(pairs) == set(PAIRS) and len(saved['comparisons']) == 4
    return {'games': len(frame), 'configurations': calculated,
            'comparisons': [paired(frame, a, b, pairs[(a, b)]) for a, b in PAIRS]}


def audit(root):
    root = Path(root)
    plan = json.loads((root/'reports/ordinary_model_research_plan.json').read_text())
    claim = plan.pop('plan_sha256')
    canonical = json.dumps(plan, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    assert hashlib.sha256(canonical).hexdigest() == claim == PLAN_SHA
    for path, expected in plan['source_files_sha256'].items():
        assert hashlib.sha256((root/path).read_bytes()).hexdigest() == expected
    for name, expected in plan['libraries'].items():
        assert version(name) == expected
    report = json.loads((root/'reports/ordinary_model_research_results.json').read_text())
    assert report['plan_sha256'] == PLAN_SHA
    path = root/'data/normalized/ordinary_research_predictions_v1.parquet'
    assert hashlib.sha256(path.read_bytes()).hexdigest() == report['prediction_file_sha256']
    saved = pd.read_parquet(path)
    features = pd.read_parquet(root/'data/normalized/ordinary_research_features_v1.parquet')
    baseline = pd.read_csv(root/'reports/opponent_adjusted_predictions.csv')
    assert len(features) == 5008 and len(plan['feature_columns']) == 58
    assert set(features.season) == set(range(2020, 2026)) and set(saved.season) == set(range(2021, 2026))
    assert not features.game_id.duplicated().any() and not saved.game_id.duplicated().any()
    assert (pd.to_datetime(features.ordinary_cutoff, utc=True) < pd.to_datetime(features.game_date, utc=True)).all()
    fits = {(r['candidate'], r['test_season']): r for r in report['fits']}
    assert len(fits) == len(report['fits']) == 10
    checks, maximum = [], defaultdict(float)
    for year in range(2021, 2026):
        train = features.loc[features.season.lt(year)&features.adjusted_history_games.ge(5)]
        test = features.loc[features.season.eq(year)&features.adjusted_history_games.ge(5)].sort_values('game_id')
        recorded = saved.loc[saved.season.eq(year)].sort_values('game_id')
        assert test.game_id.tolist() == recorded.game_id.tolist()
        close(test.actual_total, recorded.actual_total)
        close(test.market_total, recorded.market_total)
        for candidate in CONFIGS[:2]:
            old = baseline.loc[baseline.candidate.eq(candidate)&baseline.season.eq(year)].sort_values('game_id')
            assert old.game_id.tolist() == test.game_id.tolist()
            close(old.projected_total, recorded[candidate])
        close(recorded.market_only, recorded.market_total)
        for candidate in CONFIGS[2:]:
            predicted, raw = replay(train, test, plan['feature_columns'], candidate)
            error = float(np.max(abs(predicted-recorded[candidate].to_numpy())))
            assert error < 1e-8, (candidate, year, error)
            maximum[candidate] = max(maximum[candidate], error)
            detail = fits[(candidate, year)]
            assert detail['training_seasons'] == sorted(train.season.unique().tolist())
            assert detail['training_games'] == len(train) >= 300 and detail['test_games'] == len(test)
            assert detail['clipped_predictions'] == int((abs(raw) > 10).sum())
            x, z = train[plan['feature_columns']].to_numpy(float), test[plan['feature_columns']].to_numpy(float)
            assert detail['training_missing_values'] == int(np.isnan(x).sum())
            assert detail['test_missing_values'] == int(np.isnan(z).sum())
            assert detail['training_entirely_missing_columns'] == [c for c, missing in zip(plan['feature_columns'], np.isnan(x).all(axis=0)) if missing]
            checks.append({'candidate': candidate, 'test_year': year, 'training_max_year': int(train.season.max()),
                          'training_games': len(train), 'test_games': len(test), 'maximum_forecast_error': error})
        print(json.dumps({'stage': 'independent_ordinary_replay', 'year': year, 'fits_verified': len(checks)}), flush=True)
    selection = saved.loc[saved.season.le(2024)]
    selected = min(CONFIGS, key=lambda name: basic(selection.actual_total, selection[name])['mse'])
    assert selected == report['selected_on_2021_2024']
    summaries = {'selection_2021_2024': summary(selection, report['selection_2021_2024']),
                 'reused_2025': summary(saved.loc[saved.season.eq(2025)], report['reused_2025'])}
    group_count, contrast_count = 2, 8
    for key, column in [('by_season', 'season'), ('by_market_source', 'market_source')]:
        assert set(report[key]) == {str(v) for v in saved[column].unique()}
        for value, group in saved.groupby(column):
            summary(group, report[key][str(value)])
            group_count += 1
            contrast_count += 4
    periods = {'selection_2021_2024': saved.season.le(2024), 'reused_2025': saved.season.eq(2025)}
    assert set(report['by_period_and_market_source']) == set(periods)
    for name, mask in periods.items():
        source_results = report['by_period_and_market_source'][name]
        period = saved.loc[mask]
        assert set(source_results) == set(period.market_source.unique())
        for source, group in period.groupby('market_source'):
            summary(group, source_results[source])
            group_count += 1
            contrast_count += 4
    output = {'status': 'passed', 'audited_at': datetime.now(timezone.utc).isoformat(),
        'plan_sha256': PLAN_SHA, 'prediction_file_sha256': report['prediction_file_sha256'],
        'implementation': 'No project module imports; manual train-only median/indicator preprocessing, centered ridge normal equations, separately constructed fixed HGB fits, independent week-multiplicity bootstrap.',
        'replayed_fits': checks, 'maximum_forecast_errors': dict(maximum),
        'prediction_games': len(saved), 'configuration_prediction_rows': len(saved)*4,
        'summary_groups_checked': group_count, 'paired_comparisons_checked': contrast_count,
        'selected_on_2021_2024': selected, **summaries,
        'no_2026_outcomes': True, 'active_policy_changes': False, 'executable_edge_confirmed': False}
    (root/'reports/ordinary_model_research_audit.json').write_text(json.dumps(output, indent=2, allow_nan=False)+'\n')
    print(json.dumps(output, indent=2, allow_nan=False))
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1]/'model')
    audit(parser.parse_args().root)
