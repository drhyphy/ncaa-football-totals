"""Reprice unchanged historical weather flags against a second public line source.

No weather request, threshold tuning, model fitting, or live probability estimate
occurs here. Only market_total changes, and shared cohorts are compared directly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd


def common_cohorts(primary, secondary):
    """Fail on identity/outcome conflicts; preserve every frozen weather column."""
    primary = primary.copy()
    secondary = secondary.copy()
    primary['game_id'] = pd.to_numeric(primary.game_id).astype(int)
    secondary['game_id'] = pd.to_numeric(secondary.game_id).astype(int)
    if primary.game_id.duplicated().any() or secondary.game_id.duplicated().any():
        raise ValueError('Duplicate game IDs in a source cohort')
    if 'validated' in secondary and not secondary.validated.eq(True).all():
        raise ValueError('Secondary source contains unvalidated market rows')
    quotes = secondary[['game_id', 'season', 'actual_total', 'market_total', 'market_source']].rename(
        columns={c: 'secondary_' + c for c in ['season', 'actual_total', 'market_total', 'market_source']})
    joined = primary.merge(quotes, on='game_id', how='inner', validate='one_to_one')
    if not joined.season.eq(joined.secondary_season).all() or not joined.actual_total.eq(joined.secondary_actual_total).all():
        raise ValueError('Secondary game season or outcome conflicts with the frozen weather result')
    if not joined.secondary_market_total.notna().all() or not np.isfinite(joined.secondary_market_total.to_numpy(float)).all():
        raise ValueError('Secondary line is missing or nonfinite')
    shared = joined[primary.columns].copy()
    repriced = shared.copy()
    repriced['market_total'] = joined.secondary_market_total.to_numpy(float)
    pd.testing.assert_frame_equal(shared.drop(columns='market_total'), repriced.drop(columns='market_total'))
    return shared, repriced, joined


def pct(value):
    if value is None:
        return '—'
    value = 0.0 if abs(float(value)) < .00005 else float(value)
    return f'{value:+.2%}'


def ci(values):
    return '—' if values is None else ' to '.join(pct(x) for x in values)


def paired_quote_impact(shared, repriced, settle_under, draws=5000, seed=84621):
    """Same calendar-week sampling convention as weather_research.summarize."""
    selected = shared.shadow_under_flag.to_numpy(bool)
    primary_profit = settle_under(shared.actual_total, shared.market_total)
    secondary_profit = settle_under(repriced.actual_total, repriced.market_total)
    dates = pd.to_datetime(shared.kickoff, utc=True).dt.tz_convert('America/New_York')
    local = dates.dt.tz_localize(None).dt.normalize()
    week = (local - pd.to_timedelta(dates.dt.weekday, unit='D')).dt.strftime('%Y-%m-%d')
    table = pd.DataFrame({'week': week, 'selected_n': selected.astype(float),
                          'difference': (secondary_profit - primary_profit) * selected})
    blocks = table.groupby('week', sort=True)[['selected_n', 'difference']].sum().to_numpy()
    rng = np.random.default_rng(seed)
    sums = blocks[rng.integers(0, len(blocks), size=(draws, len(blocks)))].sum(axis=1)
    with np.errstate(invalid='ignore', divide='ignore'):
        deltas = sums[:, 1] / sums[:, 0]
    valid = deltas[np.isfinite(deltas)]
    interval = [float(x) for x in np.quantile(valid, [.025, .975])] if len(blocks) >= 8 and len(valid) else None
    labels = lambda x: np.where(x > 0, 'win', np.where(x < 0, 'loss', 'push'))
    transitions = pd.crosstab(pd.Series(labels(primary_profit[selected]), name='primary'),
                             pd.Series(labels(secondary_profit[selected]), name='secondary'))
    return {'selected_games': int(selected.sum()),
            'changed_settlements': int(np.sum(primary_profit[selected] != secondary_profit[selected])),
            'secondary_minus_primary_rule_roi': float(np.mean(secondary_profit[selected]-primary_profit[selected])) if selected.any() else None,
            'secondary_minus_primary_roi_95_paired_week_bootstrap': interval,
            'settlement_transitions': {str(a): {str(b): int(n) for b, n in row.items() if n} for a, row in transitions.to_dict('index').items()},
            'calendar_week_blocks': len(blocks), 'bootstrap_draws': draws,
            'bootstrap_seed': seed, 'bootstrap_draws_with_no_rule_bets': int(np.sum(~np.isfinite(deltas)))}


def main(root, secondary_file=None):
    root = Path(root).resolve()
    model = root / 'model'
    sys.path.insert(0, str(model))
    from ncaaf_model.weather_research import summarize, settle_under

    primary_file = model / 'data/raw/weather_research/game_results.parquet'
    secondary_file = Path(secondary_file).resolve() if secondary_file else model / 'data/raw/alternative/cfbd_supplement_2024_2025.parquet'
    hypothesis_file = model / 'reports/weather_published_hypothesis_results.json'
    hypothesis = json.loads(hypothesis_file.read_text())
    if hypothesis.get('partial'):
        raise ValueError('Wait for a complete frozen weather evaluation before source sensitivity')
    primary = pd.read_parquet(primary_file)
    primary = primary.loc[primary.status.eq('shadow_only')].copy().reset_index(drop=True)
    if primary.shadow_under_flag.isna().any():
        raise ValueError('Frozen weather flags contain missing values')
    secondary = pd.read_parquet(secondary_file)
    shared, repriced, joined = common_cohorts(primary, secondary)
    full = summarize(primary)
    if full['games'] != hypothesis['pooled']['games'] or any(
        full['weather_rule'][key] != hypothesis['pooled']['weather_rule'][key]
        for key in ['bets', 'wins', 'losses', 'pushes']):
        raise ValueError('Frozen weather game rows do not match the published hypothesis results')

    cohorts = {'primary_all_weather_covered': primary,
               'primary_shared': shared, 'secondary_shared': repriced}
    pooled = {name: summarize(frame) for name, frame in cohorts.items()}
    by_season = {str(year): {name: summarize(frame.loc[frame.season.eq(year)]) for name, frame in cohorts.items()}
                 for year in (2024, 2025)}
    impacts = {'pooled': paired_quote_impact(shared, repriced, settle_under),
               **{str(year): paired_quote_impact(shared.loc[shared.season.eq(year)].reset_index(drop=True),
                                                 repriced.loc[repriced.season.eq(year)].reset_index(drop=True), settle_under)
                  for year in (2024, 2025)}}
    unshared = primary.loc[~primary.game_id.isin(shared.game_id)]
    difference = joined.secondary_market_total - joined.market_total
    selected = joined.shadow_under_flag.astype(bool)
    report = {
        'version': 'weather-source-sensitivity-v1',
        'status': 'reused_development_fixed_hypothesis_source_sensitivity',
        'plan_sha256': hypothesis['plan_sha256'],
        'thresholds': hypothesis['thresholds'],
        'weather_flags_changed': False,
        'only_repriced_field': 'market_total',
        'price_assumption': -110,
        'primary_games': len(primary), 'common_games': len(shared),
        'unshared_primary_games': len(unshared),
        'unshared_primary_weather_rule_bets': int(unshared.shadow_under_flag.sum()),
        'unshared_primary_by_season': {str(year): {'games': int(unshared.season.eq(year).sum()),
            'weather_rule_bets': int(unshared.loc[unshared.season.eq(year), 'shadow_under_flag'].sum())} for year in (2024, 2025)},
        'source_counts': {'primary_all': primary.market_source.value_counts().to_dict(),
            'primary_shared': shared.market_source.value_counts().to_dict(),
            'secondary_shared': joined.secondary_market_source.value_counts().to_dict()},
        'line_discrepancies': {'exact_matches': int(difference.eq(0).sum()),
            'mean_absolute_difference': float(difference.abs().mean()),
            'maximum_absolute_difference': float(difference.abs().max()),
            'selected_exact_matches': int(difference[selected].eq(0).sum()),
            'selected_mean_absolute_difference': float(difference[selected].abs().mean())},
        'pooled': pooled, 'by_season': by_season, 'paired_quote_impact': impacts,
        'input_sha256': {str(path.relative_to(root)) if path.is_relative_to(root) else str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in [primary_file, secondary_file, hypothesis_file]},
        'limitations': [
            'Exact weather flags, forecast windows, thresholds and eligible weather cohort are inherited unchanged from the fixed hypothesis.',
            'Secondary CFBD derivative retains its total but discards bookmaker identity, quote timestamp and total-side prices. All returns assume -110.',
            'Shared cohorts compare identical games; the all-primary weather cohort is reported separately so missing secondary coverage is visible.',
            'Fixed-lead weather archive is a pregame availability proxy, not proof of original public dissemination time. Historical roof stability is assumed.',
            '2024–25 outcomes are reused development data. Week-bootstrap intervals do not correct for overall research selection or establish high-confidence profitability.',
            'No probabilities, live expected values, new thresholds or wagering recommendations were fitted by this check.',
        ],
    }
    report_file = model / 'reports/weather_source_sensitivity.json'
    report_file.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
    joined.to_parquet(model / 'data/raw/weather_research/source_sensitivity_games.parquet', index=False)
    lines = ['# Weather hypothesis: fixed line-source sensitivity', '',
             'The weather selections and forecast inputs are unchanged. Only the archived total is replaced with an independently published CFBD-derived total. Both sources assume −110; offered prices and quote timestamps are unavailable. These are reused development results, not evidence of an established live edge.', '',
             f"The primary weather cohort contains {len(primary):,} games. The shared comparison contains {len(shared):,}; {len(unshared):,} primary games, including {int(unshared.shadow_under_flag.sum())} weather selections, lack a secondary line.", '',
             '| Period | Source/cohort | Covered games | Weather bets | W–L–P | Assumed ROI | Descriptive 95% week ROI interval | All-under ROI, same cohort |',
             '|---|---|---:|---:|---|---:|---|---:|']
    for period, sources in [('2024–25', pooled), *by_season.items()]:
        for source, metric in sources.items():
            rule = metric['weather_rule']; baseline = metric['all_under_same_weather_coverage']
            lines.append(f"| {period} | {source} | {metric['games']} | {rule['bets']} | {rule['wins']}–{rule['losses']}–{rule['pushes']} | {pct(rule['roi'])} | {ci(metric['weather_rule_roi_95_week_bootstrap'])} | {pct(baseline['roi'])} |")
    lines += ['', '## Paired source changes', '',
              '| Period | Changed selected settlements | Secondary minus primary ROI | Descriptive 95% paired week interval |',
              '|---|---:|---:|---|']
    for period, result in impacts.items():
        lines.append(f"| {period} | {result['changed_settlements']} | {pct(result['secondary_minus_primary_rule_roi'])} | {ci(result['secondary_minus_primary_roi_95_paired_week_bootstrap'])} |")
    lines += ['', '## Limits', '', *['- '+text for text in report['limitations']], '',
              'Reproduce with `python scripts/weather_source_sensitivity.py --root .`; optional `--secondary-file` accepts the same normalized market-only schema. No download or model training occurs.',
              f"Frozen weather plan: `{report['plan_sha256']}`."]
    (model / 'reports/weather_source_sensitivity.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'common_games': len(shared), 'pooled': pooled,
                      'paired_quote_impact': impacts}, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--secondary-file', type=Path)
    args = parser.parse_args()
    main(args.root, args.secondary_file)
