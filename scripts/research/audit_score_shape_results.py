#!/usr/bin/env python3
"""Independent numerical reproduction of the frozen score-shape study.

--self-test uses synthetic arrays only. --audit is for use only after both
original study stages have been released. This script imports no model,
calibration, shape or scoring implementation from ncaaf_model.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
from scipy.optimize import least_squares
from scipy.special import ndtr, logsumexp
from threadpoolctl import threadpool_limits

SUPPORT = np.arange(251, dtype=float)
CONFIGS = ('market_normal', 'ridge_normal', 'market_score_shape')
BASES = ('market_only', 'opponent_adjusted_ridge')
METRICS = ('three_outcome_nll', 'conditional_log_loss', 'conditional_brier',
           'exact_score_nll', 'discrete_crps', 'absolute_mean_error')
PRIMARY = METRICS[0]
COUNTS = {2021: 734, 2022: 734, 2023: 795, 2024: 798, 2025: 852}
SOURCE_COMMIT = '051c0c4'
ORIGINAL_SOURCE_COMMIT = 'e51db91'
PLAN_PIN = 'ffd16bc66a7334b4706cfd80062173edc6097cc5197863f54f4a0e434ab347f9'
PINS = {
    'reports/opponent_adjusted_predictions.csv': '1b032e26993c7c15f98f74c26ed69d6112520da8dbae442e41b20ea602b0b524',
    'data/normalized/opponent_features_verified_cf764f803b38ecfd.parquet': 'ec33e9add2cb5b84742a17e65c313ca225503f6edadba125fbf7eeb5a45bb73c',
}
PLAN = 'reports/score_shape_research_plan_v2.json'
SELECTION = 'reports/score_shape_selection.json'
RESULTS = 'reports/score_shape_results.json'
OUTPUT_JSON = 'reports/score_shape_numerical_audit.json'
OUTPUT_MD = 'reports/SCORE_SHAPE_NUMERICAL_AUDIT.md'


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def anchored(root, relative):
    p = Path(relative)
    require(not p.is_absolute() and '..' not in p.parts, 'Unsafe artifact path')
    result = root/p
    require(result.resolve().is_relative_to(root.resolve()) and not result.is_symlink(), 'Artifact escaped root')
    return result


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.DEVNULL)


class Comparisons:
    def __init__(self):
        self.count = 0
        self.max_absolute_difference = 0.
        self.largest_path = None

    def check(self, got, saved, path='value', tolerance=2e-8):
        if isinstance(got, dict):
            require(isinstance(saved, dict) and set(got) == set(saved), path+' keys differ')
            for key in got:
                self.check(got[key], saved[key], path+'.'+str(key), tolerance)
        elif isinstance(got, (list, tuple, np.ndarray)):
            require(isinstance(saved, (list, tuple, np.ndarray)) and len(got) == len(saved), path+' length differs')
            for i, (a, b) in enumerate(zip(got, saved)):
                self.check(a, b, f'{path}[{i}]', tolerance)
        elif isinstance(got, (float, np.floating)):
            require(isinstance(saved, (int, float, np.number)) and not isinstance(saved, bool), path+' numeric type differs')
            if np.isnan(got):
                require(np.isnan(saved), path+' missingness differs')
                return
            delta = abs(float(got)-float(saved))
            require(np.isfinite(delta) and delta <= tolerance, f'{path} differs by {delta}')
            self.count += 1
            if delta > self.max_absolute_difference:
                self.max_absolute_difference, self.largest_path = delta, path
        else:
            require(got == saved, path+' value differs')
            self.count += 1


def normal_mass(center, sigma):
    center = np.asarray(center, dtype=float).reshape(-1, 1)
    sigma = np.asarray(sigma, dtype=float).reshape(-1, 1)
    require(len(center) == len(sigma) and np.isfinite(center).all() and np.all((sigma >= 6) & (sigma <= 30)), 'Invalid baseline center/scale')
    edges = np.arange(-.5, 251., 1.)
    cdf = ndtr((edges[None, :]-center)/sigma)
    masses = np.maximum(np.diff(cdf, axis=1), 1e-15)
    masses[:, -1] += np.maximum(1.-cdf[:, -1], 0.)
    return masses/masses.sum(axis=1)[:, None]


def ratios_from_counts(actual, baseline):
    values = np.asarray(actual)
    require(len(values) == len(baseline) and len(values) > 0, 'Empty or misaligned shape training')
    require(np.all(np.isfinite(values)) and np.all((values >= 0) & (values <= 250)) and np.all(values == np.floor(values)), 'Invalid score labels')
    observed = np.array([np.count_nonzero(values == y) for y in range(251)], dtype=float)
    expected = np.sum(baseline, axis=0)
    prior = expected*(800./len(values))
    raw_ratio = (observed+prior)/(expected+prior)
    ratio = np.clip(raw_ratio, .6, 1.6)
    return ratio, raw_ratio, observed, expected


def corrected_mass(baseline, ratio):
    """Independent trust-region least-squares solve of the two moment equations."""
    baseline, ratio = np.asarray(baseline, float), np.asarray(ratio, float)
    require(baseline.shape == ratio.shape == (251,), 'Invalid support')
    require(np.all(np.isfinite(baseline)) and np.all(baseline > 0) and abs(baseline.sum()-1.) < 1e-12, 'Invalid baseline PMF')
    require(np.all(np.isfinite(ratio)) and np.all((ratio >= .6) & (ratio <= 1.6)), 'Invalid ratios')
    mean = baseline@SUPPORT
    variance = baseline@((SUPPORT-mean)**2)
    require(variance > 0, 'Degenerate baseline')
    z = (SUPPORT-mean)/np.sqrt(variance)
    basis = np.stack([z, z*z], axis=1)
    logw = np.log(baseline)+np.log(ratio)

    def evaluate(theta):
        logit = logw+basis@theta
        mass = np.exp(logit-logsumexp(logit))
        first = np.sum(mass*z)
        second = np.sum(mass*z*z)
        return np.array([first, second-1.]), mass

    def jacobian(theta):
        _, mass = evaluate(theta)
        expectations = mass@basis
        return (basis.T*mass)@basis-np.outer(expectations, expectations)

    result = least_squares(lambda theta: evaluate(theta)[0], np.zeros(2), jac=jacobian,
                           method='trf', ftol=1e-14, xtol=1e-14, gtol=1e-14,
                           max_nfev=500, x_scale='jac')
    residual, mass = evaluate(result.x)
    require(result.success and np.max(np.abs(residual)) <= 2e-12, 'Independent moment solver did not converge')
    require(np.all(mass > 0) and abs(mass.sum()-1.) < 1e-12, 'Independent PMF lost mass/support')
    return mass, {'theta': result.x, 'residual': float(np.max(np.abs(residual))),
                  'nfev': int(result.nfev), 'mean': float(mean), 'variance': float(variance),
                  'basis': basis, 'log_weights': logw}


def score(frame, probabilities):
    require(not frame.game_id.duplicated().any(), 'Duplicate scoring identity')
    y, line = frame.actual_total.to_numpy(int), frame.market_total.to_numpy(float)
    require(np.all((line > 0) & (line % .5 == 0)), 'Invalid exact market line')
    under = np.array([p[SUPPORT < threshold].sum() for p, threshold in zip(probabilities, line)])
    over = np.array([p[SUPPORT > threshold].sum() for p, threshold in zip(probabilities, line)])
    push = np.array([p[SUPPORT == threshold].sum() for p, threshold in zip(probabilities, line)])
    q = over/(under+over)
    nonpush = y != line
    conditional = np.full(len(y), np.nan)
    brier = np.full(len(y), np.nan)
    side = np.where(y > line, over, under)
    conditional[nonpush] = -np.log(side[nonpush]/(under[nonpush]+over[nonpush]))
    brier[nonpush] = (q[nonpush]-(y[nonpush] > line[nonpush]))**2
    mean = probabilities@SUPPORT
    variance = np.array([np.sum(p*(SUPPORT-m)**2) for p, m in zip(probabilities, mean)])
    columns = ['game_id', 'season', 'game_date', 'market_source', 'actual_total', 'market_total']
    columns += sorted(c for c in frame if c == 'week' or 'cutoff' in c.lower())
    output = frame[columns].copy()
    output['under_probability'], output['over_probability'], output['push_probability'] = under, over, push
    output['conditional_over'] = q
    output[PRIMARY] = -np.log(np.where(nonpush, side, push))
    output['conditional_log_loss'], output['conditional_brier'] = conditional, brier
    output['exact_score_nll'] = -np.log(probabilities[np.arange(len(y)), y])
    output['discrete_crps'] = np.sum((np.cumsum(probabilities, axis=1)[:, :-1]-(SUPPORT[None, :-1] >= y[:, None]))**2, axis=1)
    output['absolute_mean_error'] = abs(mean-y)
    output['mean'], output['variance'] = mean, variance
    return output


def metrics(rows):
    nonpush = rows.actual_total.ne(rows.market_total).to_numpy()
    losses = {m: (float(rows[m].dropna().mean()) if rows[m].notna().any() else None) for m in METRICS}
    counts = {m: int(rows[m].notna().sum()) for m in METRICS}
    bins = []
    edges = [0., .4, .45, .5, .55, .6, 1.]
    for i, (a, b) in enumerate(zip(edges, edges[1:])):
        selected = nonpush & rows.conditional_over.ge(a).to_numpy() & ((rows.conditional_over.le(b) if i == 5 else rows.conditional_over.lt(b)).to_numpy())
        subset = rows.loc[selected]
        bins.append({'lower': a, 'upper': b, 'upper_inclusive': i == 5, 'games': len(subset),
                     'predicted_over': float(subset.conditional_over.mean()) if len(subset) else None,
                     'observed_over': float(subset.actual_total.gt(subset.market_total).mean()) if len(subset) else None})
    return {'games': len(rows), 'metrics': losses, 'metric_games': counts,
            'posted_integer_lines': int(rows.market_total.mod(1).eq(0).sum()),
            'observed_pushes': int(np.count_nonzero(~nonpush)),
            'predicted_pushes': float(rows.push_probability.sum()), 'reliability': bins}


def paired(candidate, reference):
    reference = reference.set_index('game_id').loc[candidate.game_id].reset_index()
    candidate = candidate.reset_index(drop=True)
    for column in ['game_id', 'season', 'game_date', 'market_source', 'actual_total', 'market_total']+sorted(c for c in candidate if c == 'week' or 'cutoff' in c.lower()):
        require(candidate[column].tolist() == reference[column].tolist(), 'Paired field differs: '+column)
    local = pd.to_datetime(candidate.game_date, utc=True).dt.tz_convert('America/New_York').dt.tz_localize(None)
    weeks = local.dt.to_period('W-SUN').astype(str)
    results = {}
    for metric in METRICS:
        valid = candidate[metric].notna() & reference[metric].notna()
        delta = candidate.loc[valid, metric].to_numpy()-reference.loc[valid, metric].to_numpy()
        block = pd.DataFrame({'week': weeks[valid], 'difference': delta}).groupby('week', sort=True).difference.agg(['sum', 'size'])
        entry = {'games': int(valid.sum()), 'week_blocks': len(block), 'difference': float(delta.mean()) if len(delta) else None, 'interval_95': None}
        if metric == PRIMARY:
            entry['interval_99'] = None
        if len(block) >= 2:
            rng = np.random.Generator(np.random.PCG64(20260909))
            indices = rng.integers(0, len(block), size=(10000, len(block)))
            boot = block['sum'].to_numpy()[indices].sum(axis=1)/block['size'].to_numpy()[indices].sum(axis=1)
            entry['interval_95'] = np.quantile(boot, [.025, .975]).tolist()
            if metric == PRIMARY:
                entry['interval_99'] = np.quantile(boot, [.005, .995]).tolist()
        results[metric] = entry
    return {'games': len(candidate), 'draws': 10000, 'seed': 20260909,
            'difference_direction': 'candidate_minus_reference', 'metrics': results}


def summary(rows):
    by_config = {name: rows.loc[rows.configuration.eq(name)].reset_index(drop=True) for name in CONFIGS}
    return {'games': len(by_config[CONFIGS[0]]),
            'configurations': {name: metrics(part) for name, part in by_config.items()},
            'comparisons': [{'candidate': CONFIGS[-1], 'reference': reference,
                             **paired(by_config[CONFIGS[-1]], by_config[reference])} for reference in CONFIGS[:2]]}


def periods(rows):
    return {'pooled': summary(rows), 'by_season': {str(y): summary(part) for y, part in rows.groupby('season')},
            'by_source': {str(source): summary(part) for source, part in rows.groupby('market_source')}}


def input_and_stage_audit(root):
    model = root/'model'
    plan, selected, result = (json.loads((model/p).read_text()) for p in (PLAN, SELECTION, RESULTS))
    require(digest(model/PLAN) == PLAN_PIN and plan['execution_plan_revision'] == 2, 'Corrected plan pin changed')
    require(plan['candidate_order'] == list(CONFIGS) and plan['selection_years'] == [2022, 2023, 2024] and plan['primary_metric'] == PRIMARY, 'Study configuration changed')
    require(plan['new_shape_fits_or_scores_computed'] is False and selected['new_2025_shape_forecasts_computed'] is False, 'Invalid stage declaration')
    for relative, expected in plan['source_files_sha256'].items():
        require(digest(anchored(model, relative)) == expected, 'Frozen source changed: '+relative)
    for relative, expected in PINS.items():
        require(plan['source_files_sha256'][relative] == expected, 'Repair pin changed')
    require({name: version(name) for name in plan['libraries']} == plan['libraries'], 'Library versions differ from freeze')
    for stage in (selected, result):
        require(stage['plan_sha256'] == digest(model/PLAN), 'Stage plan hash changed')
    require(result['selection_sha256'] == digest(model/SELECTION), 'Selection hash changed')
    require(result['selected_on_2022_2024'] == selected['choice'], 'Selected configuration changed')
    require(pd.Timestamp(plan['frozen_at']) <= pd.Timestamp(selected['selected_at']) <= pd.Timestamp(result['checked_at']), 'Recorded stage times reversed')
    first, second = selected['git_commit'], result['git_commit']
    source_commit = git(root, 'rev-parse', SOURCE_COMMIT).decode().strip()
    original_source_commit = git(root, 'rev-parse', ORIGINAL_SOURCE_COMMIT).decode().strip()
    git(root, 'merge-base', '--is-ancestor', original_source_commit, source_commit)
    git(root, 'merge-base', '--is-ancestor', source_commit, first)
    git(root, 'merge-base', '--is-ancestor', first, second)
    for commit in [first, second]:
        require(git(root, 'show', commit+':model/'+PLAN) == (model/PLAN).read_bytes(), 'Plan absent/changed at execution commit')
        for relative in plan['tracked_files']:
            require(git(root, 'show', commit+':model/'+relative) == (model/relative).read_bytes(), 'Source bytes changed across stages')
    require(git(root, 'show', second+':model/'+SELECTION) == (model/SELECTION).read_bytes(), 'Choice was not committed before later stage')
    try:
        git(root, 'show', first+':model/'+SELECTION)
    except subprocess.CalledProcessError:
        pass
    else:
        raise ValueError('Earlier stage already contained a choice file')
    for record, field in [(selected, 'prediction_file'), (result, 'prediction_file_2025')]:
        spec = record[field]
        require(digest(anchored(model, spec['path'])) == spec['sha256'], 'Saved forecast hash changed')
    correction = json.loads((model/'reports/SCORE_SHAPE_SOURCE_CORRECTION.json').read_text())
    require(correction['execution_plan_revision'] == 2 and correction['new_shape_fits_or_scores_computed'] is False, 'Correction timing declaration changed')
    require(correction['original_plan_sha256'] == digest(model/'reports/score_shape_research_plan.json') == plan['prior_plan_sha256'], 'Original plan was not preserved')
    require(correction['no_generated_forecast_or_selection_or_result_at_correction'] is True, 'Correction changed scientific stage')
    for evidence in correction['original_attempt_records']:
        path = anchored(model, evidence['path'])
        require(digest(path) == evidence['sha256'] and json.loads(path.read_text()) == evidence['receipt'], 'Original failed-stage evidence changed')
    require(any(e['receipt'].get('stage') == 'select' and e['receipt'].get('status') == 'failed' for e in correction['original_attempt_records']), 'Original selection failure not preserved')
    attempts = []
    for p in sorted((model/'data/normalized/score_shape_attempts_v1').glob('*/finished.json')):
        terminal = json.loads(p.read_text()); start = p.with_name('started.json')
        require(terminal['started_sha256'] == digest(start), 'Attempt receipt chain changed')
        require(pd.Timestamp(json.loads(start.read_text())['started_at']) <= pd.Timestamp(terminal['finished_at']), 'Attempt times reversed')
        attempts.append({'stage': terminal['stage'], 'status': terminal['status'], 'receipt_sha256': digest(p)})
    require(all(any(x['stage'] == name and x['status'] == 'succeeded' for x in attempts) for name in ['freeze', 'select', 'check']), 'Missing successful stage receipts')
    return plan, selected, result, {'source_commit': source_commit, 'selection_execution_commit': first,
                                  'later_execution_commit': second, 'ancestry_verified': True,
                                  'original_source_commit': original_source_commit,
                                  'original_plan_sha256': correction['original_plan_sha256'],
                                  'source_bookkeeping_correction_before_fits_preserved': True,
                                  'choice_committed_before_later_stage': True, 'attempts': attempts}


def load_sources(root):
    model = root/'model'
    oof = pd.read_csv(model/'reports/opponent_adjusted_predictions.csv')
    oof = oof.loc[oof.candidate.isin(BASES)].copy()
    require(not oof.duplicated(['candidate', 'game_id']).any(), 'Duplicate saved OOF identity')
    cache = pd.read_parquet(model/next(p for p in PINS if p.endswith('.parquet')))
    require(len(cache) == 5008 and not cache.game_id.duplicated().any(), 'Invalid cache universe')
    eligible = cache.loc[cache.season.between(2021, 2025) & cache.adjusted_history_games.ge(5)].set_index('game_id').sort_index()
    fields = ['season', 'week', 'home_id', 'away_id', 'home_team', 'away_team', 'home_score', 'away_score', 'actual_total', 'market_total',
              'market_home_spread', 'neutral_site', 'status', 'market_source', 'source_verified_pregame', 'adjusted_history_games', 'ratings_training_rows']
    for base in BASES:
        saved = oof.loc[oof.candidate.eq(base)].set_index('game_id').sort_index()
        require(saved.index.equals(eligible.index), 'Source/cache game IDs differ')
        pd.testing.assert_frame_equal(saved[fields], eligible[fields], check_exact=True, check_dtype=False)
        require(saved.groupby('season').size().to_dict() == COUNTS, 'Saved cohort count changed')
        require(saved.source_verified_pregame.eq(True).all(), 'Unverified pregame provider')
        for column in ['game_date', 'ratings_cutoff']:
            for value in saved[column]:
                require(pd.Timestamp(value).tzinfo is not None, 'Missing explicit timezone')
            require(np.array_equal(pd.to_datetime(saved[column], utc=True), pd.to_datetime(eligible[column], utc=True)), 'Cache date/cutoff changed')
        require((pd.to_datetime(saved.ratings_cutoff, utc=True) < pd.to_datetime(saved.game_date, utc=True)).all(), 'Postgame feature cutoff')
    first = oof.loc[oof.candidate.eq(BASES[0])].set_index('game_id').sort_index()
    second = oof.loc[oof.candidate.eq(BASES[1])].set_index('game_id').sort_index()
    pd.testing.assert_frame_equal(first[fields+['residual_sigma']], second[fields+['residual_sigma']], check_dtype=False, check_exact=True)
    require(np.array_equal(first.projected_total, first.market_total), 'Market center differs from line')
    require(np.isfinite(oof[['actual_total', 'market_total', 'projected_total', 'residual_sigma']]).all().all(), 'Nonfinite source')
    require(oof.actual_total.mod(1).eq(0).all() and oof.actual_total.between(0, 250).all(), 'Invalid integer scores')
    # Independently reproduce the saved yearly market-residual scale. The ridge
    # center itself is intentionally taken from its source-pinned OOF forecast.
    for year in COUNTS:
        earlier = cache.loc[cache.season.lt(year)]
        sigma = float(np.sqrt(np.mean(np.square(earlier.actual_total-earlier.market_total))))
        require(np.allclose(oof.loc[oof.season.eq(year), 'residual_sigma'], sigma, rtol=0, atol=1e-10), 'Earlier-season scale mismatch')
    return oof


def audit(root):
    plan, selected, result, chain = input_and_stage_audit(root)
    model = root/'model'
    oof = load_sources(root)
    compare = Comparisons()
    fit_records = selected['fits']+result['fits_2025']
    require([f['test_year'] for f in fit_records] == [2022, 2023, 2024, 2025], 'Annual fit records changed')
    saved_frames = [pd.read_parquet(anchored(model, selected['prediction_file']['path'])),
                    pd.read_parquet(anchored(model, result['prediction_file_2025']['path']))]
    saved_all = pd.concat(saved_frames, ignore_index=True)
    require(not saved_all.duplicated(['configuration', 'game_id']).any(), 'Duplicate scored forecasts')
    market = oof.loc[oof.candidate.eq(BASES[0])]
    reconstructed, annual = [], []
    for year, saved_fit in zip(range(2022, 2026), fit_records):
        train = market.loc[market.season.lt(year)].sort_values(['season', 'game_id']).reset_index(drop=True)
        test = market.loc[market.season.eq(year)].sort_values('game_id').reset_index(drop=True)
        ridge = oof.loc[oof.candidate.eq(BASES[1]) & oof.season.eq(year)].sort_values('game_id').reset_index(drop=True)
        require(set(train.season) == set(range(2021, year)) and not set(train.game_id)&set(test.game_id), 'Non-prior shape history')
        baseline = normal_mass(test.projected_total, np.clip(test.residual_sigma, 6, 30))
        expected_train = normal_mass(train.projected_total, np.clip(train.residual_sigma, 6, 30))
        ratio, raw_ratio, observed, expected = ratios_from_counts(train.actual_total, expected_train)
        stored = saved_fit['fit']
        for key, value in {'training_games': len(train), 'training_seasons': list(range(2021, year)), 'training_max_season': year-1,
                           'prior_games': 800., 'ratio_bounds': [.6, 1.6], 'ratios': ratio, 'raw_ratios': raw_ratio,
                           'observed_counts': observed, 'expected_counts': expected,
                           'clipped_low_scores': int(np.count_nonzero(raw_ratio < .6)), 'clipped_high_scores': int(np.count_nonzero(raw_ratio > 1.6))}.items():
            compare.check(value, stored[key], f'{year}.fit.{key}')
        for key, value in {'version': 'score-shape-two-moment-v1', 'moment_tolerance': 1e-11, 'max_iterations': 100,
                           'max_backtracks': 50, 'armijo': 1e-4, 'normalization_tolerance': 1e-10,
                           'status': 'unvalidated_research_candidate', 'solver': 'damped_newton_analytic_gradient_covariance_hessian',
                           'objective_roundoff_multiplier': 8, 'moment_error_acceptance_factor': .5}.items():
            compare.check(value, stored[key], f'{year}.fit.{key}')
        compare.check(len(test), saved_fit['test_games'], f'{year}.test_games')
        compare.check(test.game_id.astype(int).tolist(), saved_fit['prediction_game_ids'], f'{year}.prediction_ids')
        require(len(saved_fit['moment_diagnostics']) == len(test), 'Missing per-game moment diagnostics')
        masses, evaluations, residuals = [], [], []
        for index, p0 in enumerate(baseline):
            mass, detail = corrected_mass(p0, ratio)
            recorded = saved_fit['moment_diagnostics'][index]
            require(recorded['status'] == 'converged' and isinstance(recorded['iterations'], int) and 0 <= recorded['iterations'] <= 100, 'Invalid original solver status')
            theta = np.asarray(recorded['theta'], float)
            logits = detail['log_weights']+detail['basis']@theta
            producer_mass = np.exp(logits-logsumexp(logits)); producer_mass /= producer_mass.sum()
            moments = producer_mass@detail['basis']
            residual = float(max(abs(moments[0]), abs(moments[1]-1)))
            require(residual <= 1.01e-11, 'Stored tilt does not satisfy frozen moments')
            corrected_mean = float(producer_mass@SUPPORT)
            corrected_variance = float(producer_mass@((SUPPORT-corrected_mean)**2))
            for key, value in {'baseline_mean': detail['mean'], 'baseline_variance': detail['variance'],
                               'corrected_mean': corrected_mean, 'corrected_variance': corrected_variance,
                               'mean_difference': corrected_mean-detail['mean'], 'variance_difference': corrected_variance-detail['variance'],
                               'max_standardized_moment_error': residual,
                               'dual_objective': float(logsumexp(logits)-theta[1])}.items():
                compare.check(value, recorded[key], f'{year}.diagnostic[{index}].{key}')
            compare.check(detail['theta'], theta, f'{year}.independent_theta[{index}]')
            compare.check(mass, producer_mass, f'{year}.independent_mass[{index}]', tolerance=3e-10)
            masses.append(mass); evaluations.append(detail['nfev']); residuals.append(detail['residual'])
        pmfs = (baseline, normal_mass(ridge.projected_total, np.clip(ridge.residual_sigma, 6, 30)), np.stack(masses))
        for name, pmf in zip(CONFIGS, pmfs):
            scored = score(test, pmf); scored['configuration'] = name
            saved = saved_all.loc[saved_all.configuration.eq(name) & saved_all.season.eq(year)].sort_values('game_id').reset_index(drop=True)
            require(list(scored.columns) == list(saved.columns), 'Scored schema changed')
            for column in scored:
                compare.check(scored[column].tolist(), saved[column].tolist(), f'{year}.{name}.{column}')
            reconstructed.append(scored)
        annual.append({'year': year, 'training_games': len(train), 'evaluation_games': len(test),
                       'independent_solver': 'scipy.optimize.least_squares(method=trf)',
                       'max_evaluations': max(evaluations), 'max_standardized_moment_residual': max(residuals),
                       'original_iteration_counts_checked_for_bounds_only': True})
        print(json.dumps({'audit_year': year, 'games': len(test), 'numerical_checks_passed': True}), flush=True)
    all_rows = pd.concat(reconstructed, ignore_index=True)
    earlier = all_rows.loc[all_rows.season.le(2024)]
    later = all_rows.loc[all_rows.season.eq(2025)]
    early_report, late_report = periods(earlier), periods(later)
    compare.check(early_report, selected['report'], 'selection_summary')
    compare.check(early_report, result['selection_2022_2024'], 'carried_selection_summary')
    compare.check(late_report, result['reused_2025'], 'later_summary')
    means = {name: early_report['pooled']['configurations'][name]['metrics'][PRIMARY] for name in CONFIGS}
    chosen = min(CONFIGS, key=lambda name: (means[name], CONFIGS.index(name)))
    require(chosen == selected['choice'] == result['selected_on_2022_2024'], 'Primary-loss choice differs')
    require(all(result[key] is False for key in ['live_policy_changes', 'credible_executable_edge_established', 'probability_artifact_promoted', 'roi_evaluated']), 'Research scope flags changed')
    return {'schema_version': 'score-shape-independent-numerical-audit-v1', 'status': 'passed',
            'audited_at': datetime.now(timezone.utc).isoformat(), 'audit_script_sha256': digest(Path(__file__)),
            'independent_implementation': True, 'imports_original_model_or_scoring_code': False,
            'source_files_sha256': PINS, 'plan_sha256': digest(model/PLAN),
            'selection_sha256': digest(model/SELECTION), 'results_sha256': digest(model/RESULTS),
            'git_stage_evidence': chain, 'annual_reconstructions': annual,
            'numeric_comparisons': compare.count, 'maximum_absolute_difference': compare.max_absolute_difference,
            'largest_difference_path': compare.largest_path, 'comparison_absolute_tolerance': 2e-8,
            'independent_pmf_comparison_tolerance': 3e-10, 'reproduced_choice': chosen,
            'compared_outputs': ['annual ratios, raw ratios, observed and expected counts, clipping and fit constants',
               'every saved Under/Push/Over and conditional probability, six losses, mean and variance',
               'recorded moment diagnostics and reconstructed original tilt vs independent solution',
               'pooled, annual and market-source summaries, push counts and reliability bins',
               'paired Eastern-calendar-week 10000-draw 95% and primary 99% intervals',
               'source pins, canonical identities, feature cutoffs, saved scales and committed stage ancestry'],
            'limitations': ['Numerical reproduction only; it does not establish betting edge or profitability.',
               'All historical data are reused development data; no 2026 inputs are used.',
               'References use pinned saved OOF centers; the original ridge training algorithm is not reimplemented here.',
               'Bootstrap intervals condition on fitted forecasts and do not correct all historical search or training uncertainty.',
               'Local receipts and Git content/ancestry show recorded order, not independent certified wall-clock publication.',
               'Original Newton iteration counts can differ from the independent solver; their bounds and returned solution are checked.']}


def self_test():
    baseline = normal_mass([55., 1., 130.], [16., 6., 30.])
    for p in baseline:
        same, _ = corrected_mass(p, np.ones(251))
        np.testing.assert_allclose(same, p, atol=2e-14, rtol=0)
        ratio = np.clip(1.+.4*np.cos(SUPPORT*.7), .6, 1.6)
        corrected, detail = corrected_mass(p, ratio)
        np.testing.assert_allclose(corrected@SUPPORT, p@SUPPORT, atol=1e-10, rtol=0)
        np.testing.assert_allclose(corrected@((SUPPORT-p@SUPPORT)**2), p@((SUPPORT-p@SUPPORT)**2), atol=1e-8, rtol=0)
        require(detail['residual'] < 2e-12, 'Synthetic moment residual')
    ratio, raw, observed, expected = ratios_from_counts([54, 55, 55], baseline)
    require(observed[55] == 2 and observed[54] == 1 and observed.sum() == 3, 'Synthetic count reconstruction')
    np.testing.assert_allclose(raw, (observed+800*expected/3)/(expected+800*expected/3), atol=1e-13, rtol=1e-13)
    require(np.all((ratio >= .6) & (ratio <= 1.6)), 'Synthetic clipping')
    frame = pd.DataFrame({'game_id': [1, 2, 3], 'season': [2022]*3,
                          'game_date': ['2022-09-03T16:00:00Z', '2022-09-10T16:00:00Z', '2022-09-17T16:00:00Z'],
                          'market_source': ['synthetic']*3, 'actual_total': [55, 55, 54], 'market_total': [55., 54.5, 55.5]})
    scored = score(frame, normal_mass([55.]*3, [16.]*3))
    require(np.isnan(scored.loc[0, 'conditional_log_loss']) and scored.loc[1, 'push_probability'] == 0, 'Synthetic push semantics')
    np.testing.assert_allclose(scored.loc[0, PRIMARY], -np.log(normal_mass([55.], [16.])[0, 55]))
    require(metrics(scored)['observed_pushes'] == 1 and metrics(scored)['metric_games']['conditional_log_loss'] == 2, 'Synthetic summary coverage')
    identical = paired(scored, scored)
    require(identical['metrics'][PRIMARY]['interval_95'] == [0., 0.] and identical['metrics']['conditional_log_loss']['week_blocks'] == 2, 'Synthetic paired intervals')
    try:
        corrected_mass(baseline[0], np.zeros(251))
    except ValueError:
        pass
    else:
        raise AssertionError('Invalid ratios accepted')
    print(json.dumps({'status': 'synthetic_self_test_passed', 'real_data_read': False, 'real_fits_or_scores': False}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--self-test', action='store_true')
    group.add_argument('--audit', action='store_true', help='Run only after parent releases completed original selection and check')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    root = args.root.resolve(); model = root/'model'
    outputs = [model/OUTPUT_JSON, model/OUTPUT_MD]
    require(not any(p.exists() for p in outputs), 'Audit outputs already exist; preserve them')
    try:
        with threadpool_limits(limits=1):
            record = audit(root)
    except Exception as error:
        record = {'schema_version': 'score-shape-independent-numerical-audit-v1', 'status': 'failed',
                  'audited_at': datetime.now(timezone.utc).isoformat(), 'audit_script_sha256': digest(Path(__file__)),
                  'error_type': type(error).__name__, 'error': str(error),
                  'scope': 'Independent numerical audit failure; original experiment artifacts unchanged.'}
        # Strip local root text if a third-party exception includes a filename.
        record['error'] = record['error'].replace(str(root), '<repo>')
    with outputs[0].open('x') as stream:
        json.dump(record, stream, indent=2, allow_nan=False); stream.write('\n')
    if record['status'] == 'passed':
        text = '# Score-shape independent numerical audit\n\n'+f"Audit status: **passed**. Reproduced all four annual shape fits and both saved references, {sum(x['evaluation_games'] for x in record['annual_reconstructions']):,} games per configuration. The independent two-moment solver uses SciPy trust-region least squares, not the original Newton implementation.\n\n"+f"Checked {record['numeric_comparisons']:,} values; largest absolute difference {record['maximum_absolute_difference']:.3g}. Reproduced choice: `{record['reproduced_choice']}`.\n\n"+'Checks include source hashes, paired identities/cutoffs, stage Git ancestry, probability/loss/moment outputs, ratio counts, reliability, and pooled/year/source 95% and 99% week-bootstrap intervals. Full details and artifact hashes: [audit JSON](score_shape_numerical_audit.json).\n\n'+ 'This is numerical reproduction of reused development data, not evidence of an executable betting edge. Ridge centers come from their pinned saved OOF forecasts; this audit does not reimplement ridge fitting. Git ancestry and local receipts do not certify original public availability.\n'
    else:
        text = '# Score-shape independent numerical audit\n\nAudit status: **failed**. Original experiment artifacts remain unchanged. See [the retained failure record](score_shape_numerical_audit.json). No edge claim is supported by this audit.\n'
    with outputs[1].open('x') as stream:
        stream.write(text)
    print(json.dumps({'status': record['status'], 'audit_json': OUTPUT_JSON, 'audit_markdown': OUTPUT_MD}), flush=True)
    if record['status'] != 'passed':
        sys.exit(1)


if __name__ == '__main__':
    main()
