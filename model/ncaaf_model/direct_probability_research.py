"""Fixed direct-probability study on repaired, reused historical observations.

Selection and the 2025 development check are separate immutable phases. This
module never writes an active artifact, betting threshold or forecast ledger.
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
from scipy.special import expit
from threadpoolctl import threadpool_limits

from . import direct_probability_models as models
from . import opponent_model
from .calibration_research import load_oof, verify_oof
from .conditional_distribution import normal_pmf, market_probabilities
from .ordinary_features_research import validate_cache
from .revision_archive import immutable_json, immutable_bytes

VERSION = 'direct-probability-research-v1-20260909'
ROOT_PLAN = Path('reports/direct_probability_research_plan.json')
PROTOCOL = Path('reports/DIRECT_PROBABILITY_RESEARCH_PLAN.md')
CACHE = Path('data/normalized/opponent_features_verified_cf764f803b38ecfd.parquet')
CONFIGS = ('raw50', 'rawridge', 'context_logit', 'opponent_logit', 'context_hgb', 'opponent_hgb')
NEW = CONFIGS[2:]
COMPARISONS = (('opponent_logit', 'context_logit'), ('opponent_hgb', 'context_hgb'),
               ('opponent_logit', 'rawridge'), ('opponent_hgb', 'rawridge'))
SELECTION_YEARS = (2021, 2022, 2023, 2024)
COUNTS = {2020: 323, 2021: 428, 2022: 403, 2023: 777, 2024: 798, 2025: 852}
OUTPUT = Path('data/normalized/direct_probability_v1')
SELECTION = Path('reports/direct_probability_selection.json')
RESULT = Path('reports/direct_probability_results.json')
IMPLEMENTATION = ('ncaaf_model/direct_probability_models.py', 'ncaaf_model/direct_probability_research.py',
                  'ncaaf_model/calibration_research.py', 'ncaaf_model/conditional_distribution.py',
                  'ncaaf_model/opponent_model.py', 'ncaaf_model/ordinary_features_research.py',
                  'ncaaf_model/revision_archive.py')
VALIDATION = ('tests/test_direct_probability_models.py', 'tests/test_direct_probability_research.py',
              'requirements-lock.txt')
SEED, DRAWS = 20260909, 10000


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def eligible(frame):
    return frame.loc[frame.adjusted_history_games.ge(5) & frame.market_total.mod(1).eq(.5)].copy()


def coverage(frame):
    def counts(g):
        history = g.adjusted_history_games.ge(5)
        half = g.market_total.mod(1).eq(.5)
        return {'base_games': len(g), 'history_eligible': int(history.sum()),
                'excluded_history': int((~history).sum()),
                'excluded_nonhalfpoint_after_history': int((history & ~half).sum()),
                'eligible_halfpoint_games': int((history & half).sum())}
    return {'total': counts(frame), 'by_year': {str(k): counts(g) for k, g in frame.groupby('season', sort=True)},
            'by_source': {str(k): counts(g) for k, g in frame.groupby('market_source', sort=True)},
            'by_year_source': {str(year): {str(source): counts(g) for source, g in group.groupby('market_source', sort=True)}
                               for year, group in frame.groupby('season', sort=True)}}


def load_inputs(root):
    """Validate source roles, exact weekly information sets and saved OOF means."""
    full = pd.read_parquet(root / CACHE)
    validate_cache(full, opponent_model.load_market_games(root))
    if len(full) != 5008 or set(full.season) != set(range(2020, 2026)):
        raise ValueError('Repaired feature universe changed')
    if not np.isfinite(full[opponent_model.FEATURES].to_numpy(float)).all():
        raise ValueError('Nonfinite frozen feature')
    if not full.actual_total.between(0, 250).all() or not full.actual_total.mod(1).eq(0).all():
        raise ValueError('Invalid final total')
    for family in ('score', 'drive', 'clock'):
        if not np.allclose(full['adjusted_' + family + '_minus_market'],
                           full['adjusted_' + family + '_total'] - full.market_total, rtol=0, atol=1e-10):
            raise ValueError('Adjusted total difference does not use the repaired line')
    frame = eligible(full).sort_values(['game_date', 'game_id']).reset_index(drop=True)
    if frame.groupby('season').size().to_dict() != COUNTS:
        raise ValueError('Frozen half-point/history cohort changed')
    saved = load_oof(root)
    audit = verify_oof(root, saved)
    ridge = saved.loc[saved.candidate.eq('opponent_adjusted_ridge')].set_index('game_id')
    test = frame.loc[frame.season.ge(2021)].set_index('game_id')
    if not test.index.isin(ridge.index).all():
        raise ValueError('Saved ridge lacks a common evaluation game')
    paired = ridge.loc[test.index]
    for column in ('season', 'week', 'home_id', 'away_id', 'actual_total', 'market_total',
                   'abs_spread', 'market_source', 'status', 'source_verified_pregame', 'neutral_site'):
        if not test[column].eq(paired[column]).all():
            raise ValueError('Common ridge benchmark differs: ' + column)
    for column in ('game_date', 'ratings_cutoff'):
        if not pd.to_datetime(test[column], utc=True).equals(pd.to_datetime(paired[column], utc=True)):
            raise ValueError('Common ridge benchmark timing differs: ' + column)
    if not np.allclose(test.market_home_spread, paired.market_home_spread, rtol=0, atol=1e-10, equal_nan=True):
        raise ValueError('Common ridge benchmark spread differs')
    frame = frame.merge(ridge[['projected_total', 'residual_sigma']], left_on='game_id', right_index=True,
                        how='left', validate='one_to_one')
    return frame, {'saved_ridge_chronology': audit, 'coverage': coverage(full)}


def freeze(root):
    """Pin implementation after synthetic tests, before either real-fit phase."""
    path = root / ROOT_PLAN
    plan = json.loads(path.read_text())
    if plan.get('implementation_sha256'):
        verify_plan(root)
        return plan
    plan['implementation_sha256'] = {name: sha(root / name) for name in IMPLEMENTATION}
    plan['validation_sha256'] = {name: sha(root / name) for name in VALIDATION}
    plan['implementation_frozen_at'] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    plan['implementation_protocol_sha256'] = sha(root / PROTOCOL)
    plan['freeze_status'] = 'frozen_before_new_model_fits'
    plan['python_package_versions'] = {name: version(name) for name in ('numpy', 'pandas', 'scipy', 'scikit-learn')}
    # The plan itself is committed before execution; no training occurs here.
    path.write_text(json.dumps(plan, sort_keys=True, indent=2, allow_nan=False) + '\n')
    return plan


def verify_plan(root):
    plan = json.loads((root / ROOT_PLAN).read_text())
    if plan.get('implementation_protocol_sha256') != sha(root / PROTOCOL):
        raise ValueError('Direct-probability protocol is not frozen or changed')
    expected = plan.get('implementation_sha256', {})
    if set(expected) != set(IMPLEMENTATION):
        raise ValueError('Incomplete implementation fingerprint')
    for relative, value in expected.items():
        if sha(root / relative) != value:
            raise ValueError('Frozen implementation changed: ' + relative)
    if plan.get('validation_sha256') != {name: sha(root / name) for name in VALIDATION}:
        raise ValueError('Frozen validation or dependency specification changed')
    if any(version(name) != expected_version for name, expected_version in plan['python_package_versions'].items()):
        raise ValueError('Use the same pinned scientific package versions for both study phases')
    # Exact source keys are supplied by the preregistration audit. No source may
    # silently follow a revised market or replace a failed historical archive.
    source_hashes = plan.get('source_files_sha256', {})
    if source_hashes.get(str(CACHE)) != 'ec33e9add2cb5b84742a17e65c313ca225503f6edadba125fbf7eeb5a45bb73c':
        raise ValueError('Pinned repaired cache missing from preregistration')
    for relative, value in source_hashes.items():
        if sha(root / relative) != value:
            raise ValueError('Frozen input changed: ' + relative)
    repository = root.parent
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repository, text=True).strip()
    for relative in (*IMPLEMENTATION, *VALIDATION, str(PROTOCOL), str(ROOT_PLAN)):
        body = subprocess.check_output(['git', 'show', commit + ':model/' + relative], cwd=repository,
                                        stderr=subprocess.DEVNULL)
        if body != (root / relative).read_bytes():
            raise ValueError('Commit frozen study before real fitting: ' + relative)
    return plan, {'git_commit': commit, 'plan_sha256': sha(root / ROOT_PLAN),
                  'protocol_sha256': sha(root / PROTOCOL), 'implementation_sha256': expected}


def fit_fold(train, test):
    if (not len(train) or not len(test) or train.season.max() >= test.season.min()
            or test.season.nunique() != 1 or train.game_id.isin(test.game_id).any()):
        raise ValueError('Strictly earlier seasons and disjoint games required')
    if not train.market_total.mod(1).eq(.5).all() or not test.market_total.mod(1).eq(.5).all():
        raise ValueError('Only exact half-point reference contracts are admitted')
    y = train.actual_total.lt(train.market_total).to_numpy(int)
    output = test[['game_id', 'season', 'week', 'game_date', 'actual_total', 'market_total', 'market_source']].copy()
    output['label_under'] = test.actual_total.lt(test.market_total).astype(int)
    output['raw50_logit'] = 0.
    pmf = normal_pmf(test.projected_total.to_numpy(float), np.clip(test.residual_sigma.to_numpy(float), 6., 30.))
    _, under, push = market_probabilities(pmf, test.market_total.to_numpy(float))
    if not np.isfinite(under).all() or not ((under > 0) & (under < 1) & (push == 0)).all():
        raise ValueError('Invalid same-contract saved ridge probability')
    output['rawridge_logit'] = np.log(under) - np.log1p(-under)
    metadata = {}
    for name in NEW:
        columns = models.columns_for_config(name)
        with threadpool_limits(limits=1):
            fitted = models.fit(train[list(columns)], y, name)
            logits = fitted.predict_logits(test[list(columns)])
        if not np.isfinite(logits).all():
            raise ValueError('Nonfinite direct probability forecast')
        output[name + '_logit'] = logits
        metadata[name] = fitted.metadata
    return output, {'test_season': int(test.season.iloc[0]), 'training_games': len(train),
                    'training_seasons': sorted(map(int, train.season.unique())), 'test_games': len(test),
                    'models': metadata}


def metric_arrays(frame, name):
    z, y = frame[name + '_logit'].to_numpy(float), frame.label_under.to_numpy(float)
    if not np.isfinite(z).all() or not np.isin(y, (0, 1)).all():
        raise ValueError('Invalid immutable probability forecast')
    return np.logaddexp(0, np.where(y == 1, -z, z)), (expit(z)-y)**2


def metrics(frame, name):
    if not len(frame):
        return {'games': 0, 'log_loss': None, 'brier': None, 'reliability': []}
    loss, brier = metric_arrays(frame, name)
    p = expit(frame[name + '_logit'].to_numpy(float))
    bins = []
    edges = np.linspace(0, 1, 11)
    for i in range(10):
        mask = (p >= edges[i]) & ((p < edges[i+1]) if i < 9 else p <= edges[i+1])
        bins.append({'lower': float(edges[i]), 'upper': float(edges[i+1]), 'games': int(mask.sum()),
                     'mean_predicted_under': float(p[mask].mean()) if mask.any() else None,
                     'observed_under_rate': float(frame.label_under.to_numpy()[mask].mean()) if mask.any() else None})
    return {'games': len(frame), 'log_loss': float(loss.mean()), 'brier': float(brier.mean()),
            'mean_predicted_under': float(p.mean()), 'observed_under_rate': float(frame.label_under.mean()),
            'reliability': bins}


def paired(frame, first, second, *, draws=DRAWS):
    dates = pd.to_datetime(frame.game_date, utc=True).dt.tz_convert('America/New_York').dt.tz_localize(None).dt.normalize()
    week = (dates-pd.to_timedelta(dates.dt.weekday, unit='D')).dt.strftime('%Y-%m-%d')
    d = metric_arrays(frame, first)[0] - metric_arrays(frame, second)[0]
    sums = pd.DataFrame({'week': week.to_numpy(), 'difference': d, 'games': 1.}).groupby('week', sort=True).sum().to_numpy()
    result = {'candidate': first, 'reference': second, 'games': len(frame), 'week_blocks': len(sums),
              'log_loss_difference': float(d.mean()), 'interval_95': None, 'interval_98_75': None}
    if len(sums) >= 2:
        rng = np.random.Generator(np.random.PCG64(SEED))
        sampled = sums[rng.integers(0, len(sums), (draws, len(sums)))].sum(axis=1)
        values = sampled[:, 0]/sampled[:, 1]
        result['interval_95'] = np.quantile(values, [.025, .975], method='linear').tolist()
        result['interval_98_75'] = np.quantile(values, [.00625, .99375], method='linear').tolist()
    return result


def summary(frame):
    return {'games': len(frame), 'configurations': {name: metrics(frame, name) for name in CONFIGS},
            'comparisons': [paired(frame, a, b) for a, b in COMPARISONS],
            'by_year': {str(year): {n: metrics(g, n) for n in CONFIGS} for year, g in frame.groupby('season', sort=True)},
            'by_source': {str(source): {n: metrics(g, n) for n in CONFIGS} for source, g in frame.groupby('market_source', sort=True)}}


def choose(frame):
    if set(frame.season) != set(SELECTION_YEARS):
        raise ValueError('Selection requires exactly 2021–2024, without 2025')
    return min(CONFIGS, key=lambda name: (metrics(frame, name)['log_loss'], CONFIGS.index(name)))


def save_predictions(root, name, frame):
    # Portable text records are local research artifacts, not an active forecast ledger.
    path = root / OUTPUT / (name + '.csv')
    immutable_bytes(path, frame.to_csv(index=False, float_format='%.17g').encode())
    return {'path': path.relative_to(root).as_posix(), 'sha256': sha(path), 'rows': len(frame)}


def select(root):
    plan, provenance = verify_plan(root)
    if (root / SELECTION).exists():
        raise ValueError('Selection already frozen; do not rerun or overwrite it')
    frame, audit = load_inputs(root)
    earlier = frame.loc[frame.season.le(2024)].copy()
    forecasts, fits = [], []
    for year in SELECTION_YEARS:
        prediction, metadata = fit_fold(earlier.loc[earlier.season.lt(year)], earlier.loc[earlier.season.eq(year)])
        forecasts.append(prediction)
        fits.append(metadata)
    predicted = pd.concat(forecasts, ignore_index=True)
    choice = choose(predicted)
    result = {'schema_version': VERSION, 'phase': 'selection', 'provenance': provenance,
              'selected_configuration': choice, 'selection_years': list(SELECTION_YEARS),
              'selection_rule': 'minimum game-weighted binary log loss; frozen configuration-order ties',
              'predictions': save_predictions(root, 'selection', predicted), 'summary': summary(predicted),
              'fit_metadata': fits, 'input_audit': audit,
              'created_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
              '2025_metrics_computed': False, 'real_betting_prices_available': False,
              'historical_data_reused': True, 'active_model_changed': False}
    immutable_json(root / SELECTION, result)
    return result


def check(root):
    _, provenance = verify_plan(root)
    if (root / RESULT).exists():
        raise ValueError('Development check already recorded; do not overwrite it')
    selection = json.loads((root / SELECTION).read_text())
    if (selection['phase'] != 'selection' or selection['2025_metrics_computed'] is not False
            or selection['provenance']['plan_sha256'] != provenance['plan_sha256']):
        raise ValueError('Matching frozen earlier-year choice required')
    selected_path = root / selection['predictions']['path']
    if sha(selected_path) != selection['predictions']['sha256']:
        raise ValueError('Frozen selection predictions changed')
    old = pd.read_csv(selected_path, float_precision='round_trip')
    if choose(old) != selection['selected_configuration']:
        raise ValueError('Frozen earlier-year choice is inconsistent')
    # Commit the choice before the separate 2025 phase can compute its scores.
    recorded = subprocess.check_output(['git', 'show', 'HEAD:model/' + str(SELECTION)], cwd=root.parent,
                                       stderr=subprocess.DEVNULL)
    if recorded != (root / SELECTION).read_bytes():
        raise ValueError('Commit the unchanged selection before the 2025 development check')
    frame, audit = load_inputs(root)
    predicted, metadata = fit_fold(frame.loc[frame.season.lt(2025)], frame.loc[frame.season.eq(2025)])
    result = {'schema_version': VERSION, 'phase': '2025_reused_development_check', 'provenance': provenance,
              'selection_record_sha256': sha(root / SELECTION), 'selected_configuration': selection['selected_configuration'],
              'selection_summary': selection['summary'], 'development_2025': summary(predicted),
              'predictions': save_predictions(root, '2025', predicted), 'fit_metadata': metadata,
              'input_audit': audit, 'created_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
              'limits': ['All historical years are reused development observations, including 2025.',
                         'Provider roles are repaired; original morning prices, paired odds and quote receipt times remain unavailable.',
                         'Raw50 is a price-free statistical reference, not verified historical no-vig probabilities.',
                         'New classifiers share half-point training games; saved ridge originally used its larger prior-year score cohort.',
                         'A single-line classifier is not a coherent distribution for alternate totals or integer pushes.',
                         'Weekly intervals do not account for all previous searches or all recurring-team dependence.'],
              'high_confidence_edge_established': False, 'active_model_changed': False, 'historical_ev_or_roi_computed': False}
    immutable_json(root / RESULT, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('freeze', 'select', 'check'))
    parser.add_argument('--root', type=Path, default=Path('.'))
    args = parser.parse_args()
    result = globals()[args.phase](args.root.resolve())
    print(json.dumps({'phase': args.phase, 'selected_configuration': result.get('selected_configuration'),
                      'artifact': str(RESULT if args.phase == 'check' else SELECTION if args.phase == 'select' else ROOT_PLAN)}, indent=2))


if __name__ == '__main__':
    main()
