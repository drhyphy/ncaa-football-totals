"""Descriptive uncertainty/concentration audit of the frozen weather selections.

No threshold search, repricing, model fit, probability, or runtime change occurs.
Reproduce: python scripts/weather_robustness.py --root .
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import t

METHOD_SOURCE = 'https://cameron.econ.ucdavis.edu/research/Cameron_Miller_JHR_2015_February.pdf'
FAMILIES = (1, 4, 10, 25, 100)


def cluster_ratio(counts, profits):
    """CR1 ratio-score sandwich with t(G-1), not an exact finite-sample CI.

    theta=sum(P_g)/sum(N_g); U_g=P_g-theta*N_g;
    SE²=G/(G-1)*sum(U_g²)/(sum(N_g))².
    Zero-count/zero-profit clusters are allowed and explicitly reported.
    """
    n, p = np.asarray(counts, float), np.asarray(profits, float)
    if n.ndim != 1 or n.shape != p.shape or len(n) < 2:
        raise ValueError('At least two aligned one-dimensional clusters required')
    if not np.isfinite(n).all() or not np.isfinite(p).all() or (n < 0).any() or n.sum() <= 0:
        raise ValueError('Finite nonnegative counts, finite profits, and positive total count required')
    if ((n == 0) & (p != 0)).any():
        raise ValueError('A zero-count cluster cannot have profit')
    g, total = len(n), float(n.sum())
    theta = float(p.sum() / total)
    score = p - theta * n
    se = float(np.sqrt(g / (g - 1) * np.dot(score, score)) / total)
    if se == 0:
        raise ValueError('Degenerate zero score variance; no reliable interval')
    interval = lambda alpha: [float(theta - t.ppf(1 - alpha / 2, g - 1) * se),
                             float(theta + t.ppf(1 - alpha / 2, g - 1) * se)]
    p_value = float(2 * t.sf(abs(theta / se), g - 1))
    return {'roi': theta, 'clusters': g, 'active_clusters': int((n > 0).sum()),
            'df': g - 1, 'standard_error': se, 't_statistic': theta / se,
            'two_sided_p_zero_roi': p_value,
            'interval_95': interval(.05), 'interval_99': interval(.01),
            'multiplicity_sensitivity': [{'hypothetical_family_size': m,
                'two_sided_per_interval_confidence': 1 - .05 / m,
                'bonferroni_95_family_interval': interval(.05 / m),
                'bonferroni_adjusted_p': min(1., m * p_value)} for m in FAMILIES]}


def weekly_table(frame):
    """Eastern Monday calendar-week denominator includes covered zero-bet weeks."""
    dates = pd.to_datetime(frame.kickoff, utc=True).dt.tz_convert('America/New_York')
    local = dates.dt.tz_localize(None).dt.normalize()
    weeks = (local - pd.to_timedelta(dates.dt.weekday, unit='D')).dt.strftime('%Y-%m-%d')
    flag = frame.shadow_under_flag.to_numpy(bool)
    profit = np.where(frame.actual_total < frame.market_total, 100 / 110,
                      np.where(frame.actual_total > frame.market_total, -1., 0.))
    data = pd.DataFrame({'week': weeks.to_numpy(), 'covered_games': 1, 'bets': flag.astype(int),
                         'wins': ((profit > 0) & flag).astype(int),
                         'losses': ((profit < 0) & flag).astype(int),
                         'pushes': ((profit == 0) & flag).astype(int), 'profit_units': profit * flag})
    return data.groupby('week', sort=True).sum().reset_index()


def week_audit(frame):
    table = weekly_table(frame)
    active = table.loc[table.bets.gt(0)]
    all_ci = cluster_ratio(table.bets, table.profit_units)
    active_ci = cluster_ratio(active.bets, active.profit_units)
    total_n, total_profit = int(table.bets.sum()), float(table.profit_units.sum())
    rows = []
    for row in table.to_dict('records'):
        rest = table.loc[table.week.ne(row['week'])]
        rest_ci = cluster_ratio(rest.bets, rest.profit_units)
        rows.append({**row, 'selected_roi': row['profit_units'] / row['bets'] if row['bets'] else None,
                     'share_of_bets': row['bets'] / total_n,
                     'leave_one_week_out_bets': total_n - row['bets'],
                     'leave_one_week_out_roi': (total_profit - row['profit_units']) / (total_n - row['bets']),
                     'leave_one_week_out_cluster_t_95': rest_ci['interval_95']})
    return {'calendar_weeks': len(table), 'active_weeks': len(active), 'zero_bet_weeks': int(table.bets.eq(0).sum()),
            'bet_weight_effective_week_count': float(table.bets.sum() ** 2 / (table.bets ** 2).sum()),
            'all_covered_week_cluster_t': all_ci, 'active_week_cluster_t': active_ci,
            'leave_one_week_out_roi_range': [min(row['leave_one_week_out_roi'] for row in rows), max(row['leave_one_week_out_roi'] for row in rows)],
            'leave_one_week_out_95_lower_positive_count': sum(row['leave_one_week_out_cluster_t_95'][0] > 0 for row in rows),
            'largest_profit_week': {**max(rows, key=lambda row: row['profit_units']),
                                    'share_of_net_profit': max(row['profit_units'] for row in rows) / total_profit if total_profit else None},
            'weeks': rows}


def concentration(selected, key, name):
    """Non-overlapping venue or role-specific team groups; deletion is descriptive."""
    n, total_profit = len(selected), float(selected.profit_units.sum())
    rows = []
    for group_id, group in selected.groupby(key, sort=True):
        bets, profit = len(group), float(group.profit_units.sum())
        rows.append({'id': str(group_id), 'name': str(group[name].iloc[0]), 'bets': bets,
                     'wins': int(group.profit_units.gt(0).sum()), 'losses': int(group.profit_units.lt(0).sum()),
                     'pushes': int(group.profit_units.eq(0).sum()), 'profit_units': profit,
                     'share_of_bets': bets / n,
                     'leave_one_group_out_roi': (total_profit - profit) / (n - bets) if n > bets else None})
    rows.sort(key=lambda row: (-row['bets'], row['id']))
    hhi = sum(row['share_of_bets'] ** 2 for row in rows)
    return {'groups': len(rows), 'largest_group_bet_share': rows[0]['share_of_bets'],
            'top_five_group_bet_share': sum(row['share_of_bets'] for row in rows[:5]),
            'count_hhi': hhi, 'inverse_count_hhi': 1 / hhi,
            'leave_one_group_out_roi_range': [min(row['leave_one_group_out_roi'] for row in rows), max(row['leave_one_group_out_roi'] for row in rows)],
            'groups_table': rows}


def team_involvement(selected):
    """Treat a team identically in home/away roles; never count a game twice on deletion."""
    ids = sorted(set(selected.home_id) | set(selected.away_id))
    total, total_profit = len(selected), float(selected.profit_units.sum())
    rows = []
    for team_id in ids:
        home, away = selected.home_id.eq(team_id), selected.away_id.eq(team_id)
        group = selected.loc[home | away]
        label = selected.loc[home, 'home_team'].iloc[0] if home.any() else selected.loc[away, 'away_team'].iloc[0]
        count, profit = len(group), float(group.profit_units.sum())
        rows.append({'team_id': str(team_id), 'team': label, 'games': count,
                     'home_games': int(home.sum()), 'away_games': int(away.sum()),
                     'appearance_share': count / (2 * total), 'share_of_bets_involving_team': count / total,
                     'wins': int(group.profit_units.gt(0).sum()), 'losses': int(group.profit_units.lt(0).sum()),
                     'associated_profit_units': profit,
                     'leave_one_team_out_roi': (total_profit - profit) / (total - count)})
    rows.sort(key=lambda row: (-row['games'], row['team_id']))
    hhi = sum(row['appearance_share'] ** 2 for row in rows)
    return {'unique_teams': len(rows), 'team_appearances': 2 * total,
            'largest_team_game_share': rows[0]['share_of_bets_involving_team'],
            'top_five_appearance_share': sum(row['appearance_share'] for row in rows[:5]),
            'appearance_hhi': hhi, 'inverse_appearance_hhi': 1 / hhi,
            'leave_one_team_out_roi_range': [min(row['leave_one_team_out_roi'] for row in rows), max(row['leave_one_team_out_roi'] for row in rows)],
            'interpretation': 'Each game has two team appearances. Associated profit sums double-count the portfolio; every individual team-deletion calculation removes each game only once.',
            'teams': rows}


def pct(value):
    return '—' if value is None else f'{value:+.2%}'


def interval(values):
    return ' to '.join(pct(value) for value in values)


def main(root):
    root = Path(root).resolve()
    paths = {name: root / relative for name, relative in {
        'game_results': 'model/data/raw/weather_research/game_results.parquet',
        'repaired_market': 'model/data/raw/alternative/espn_verified_pregame_games.parquet',
        'plan': 'model/reports/weather_request_plan.json',
        'published_results': 'model/reports/weather_published_hypothesis_results.json'}.items()}
    report_base = json.loads(paths['published_results'].read_text())
    plan = json.loads(paths['plan'].read_text())
    if report_base['partial'] or report_base['plan_sha256'] != plan['plan_sha256']:
        raise ValueError('Complete, matching frozen plan/result required')
    frame = pd.read_parquet(paths['game_results'])
    frame = frame.loc[frame.status.eq('shadow_only')].copy()
    if frame.game_id.duplicated().any() or frame.shadow_under_flag.isna().any():
        raise ValueError('Duplicate games or missing frozen selection flags')
    threshold = report_base['thresholds']
    if threshold != plan['thresholds'] or threshold != {'wind_mph_above': 7.78, 'temperature_f_below': 64.81, 'relative_humidity_percent_above': 56.8}:
        raise ValueError('Frozen published weather thresholds changed')
    expected_flag = frame.wind_mph.gt(threshold['wind_mph_above']) & frame.temperature_f.lt(threshold['temperature_f_below']) & frame.relative_humidity_percent.gt(threshold['relative_humidity_percent_above'])
    if not expected_flag.eq(frame.shadow_under_flag).all():
        raise ValueError('Saved selections differ from the unchanged published rule')
    selected = frame.loc[frame.shadow_under_flag].copy()
    selected['profit_units'] = np.where(selected.actual_total < selected.market_total, 100 / 110,
                                       np.where(selected.actual_total > selected.market_total, -1., 0.))
    published = report_base['pooled']['weather_rule']
    if (len(frame) != report_base['weather_covered_games'] or len(selected) != published['bets'] or
            int(selected.profit_units.gt(0).sum()) != published['wins'] or
            int(selected.profit_units.lt(0).sum()) != published['losses'] or
            not math.isclose(float(selected.profit_units.mean()), published['roi'], abs_tol=1e-12)):
        raise ValueError('Input games do not reproduce the published study')
    identity = pd.read_parquet(paths['repaired_market'])[['game_id', 'home_id', 'away_id', 'home_team', 'away_team', 'actual_total', 'market_total']]
    selected = selected.merge(identity, on='game_id', validate='one_to_one', how='left', suffixes=('', '_source'))
    if not selected.actual_total.eq(selected.actual_total_source).all() or not selected.market_total.eq(selected.market_total_source).all() or selected[['home_id', 'away_id']].isna().any().any():
        raise ValueError('Team source identity, score, or line mismatch')
    venue_ids = {int(row['game_id']): str(row['venue_id']) for row in plan['games']}
    selected['venue_id'] = selected.game_id.map(venue_ids)
    if selected.venue_id.isna().any():
        raise ValueError('Selected venue identity absent from frozen plan')
    pooled = week_audit(frame)
    report = {'version': 'weather-robustness-v1', 'status': 'descriptive_reused_development_robustness',
              'plan_sha256': plan['plan_sha256'], 'thresholds_unchanged': True,
              'probabilities_fitted': False, 'price_assumption': -110,
              'published_result': published,
              'published_percentile_bootstrap_95': report_base['pooled']['weather_rule_roi_95_week_bootstrap'],
              'pooled': pooled, 'by_season': {str(year): week_audit(group) for year, group in frame.groupby('season')},
              'venue_concentration': concentration(selected, 'venue_id', 'venue_name'),
              'home_team_concentration': concentration(selected, 'home_id', 'home_team'),
              'away_team_concentration': concentration(selected, 'away_id', 'away_team'),
              'team_involvement': team_involvement(selected),
              'methods': {'estimand': 'Mean hypothetical profit per one-unit selected bet, not mean of weekly ROIs',
                          'cluster_score': 'U_g=P_g-theta*N_g; theta=sum(P_g)/sum(N_g)',
                          'cluster_se': 'sqrt(G/(G-1)*sum(U_g^2))/sum(N_g)',
                          'reference_distribution': 'Student t with G-1 degrees of freedom; approximate finite-cluster inference',
                          'multiplicity': 'Two-sided alpha=0.05/m Bonferroni sensitivity, m=1,4,10,25,100; m=4 represents the requested current-strategy comparison. Wider sizes are illustrative, not a known count of all prior searches.',
                          'two_way_team_cluster': 'Not computed: ordinary home-role/away-role two-way clustering misses dependence when the same team switches roles. Concentration and deletion are descriptive, not a replacement covariance estimator.',
                          'source': METHOD_SOURCE},
              'limitations': ['Calendar-week clusters are assumed independent; persistent teams, weather regimes, seasons, or bookmaker-source errors can violate this.',
                              'Only two seasons and 21 active betting weeks; unequal cluster sizes and the t reference do not give exact finite-sample coverage.',
                              'Bonferroni sensitivities do not retroactively establish familywise control for the unknown cumulative adaptive research search.',
                              'All 2024-25 outcomes are reused development data; every interval and deletion analysis is descriptive.',
                              'Archived non-live line timing and actual historical prices are unavailable; returns assume -110 and the historical roof/forecast-provenance limitations remain.',
                              'A positive leave-one-group-out point estimate does not establish calibration, future profitability, or validity of a confidence interval.'],
              'further_evidence': ['Preserve the frozen rule, price filter, and version before observing new outcomes; retain unavailable games and abstentions.',
                                   'Evaluate prospectively recorded game-day forecasts and accepted/observed line prices, with a separate deduplicated ledger.',
                                   'Accumulate more independent active weeks and seasons across teams/venues; report both nominal and multiplicity-aware uncertainty on a prespecified review schedule.',
                                   'Do not infer an individual matchup probability or stake optimization from the 55/85 historical group record.'],
              'input_sha256': {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths.values()}}
    directory = root / 'model/reports'
    (directory / 'weather_robustness.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    primary, active = pooled['all_covered_week_cluster_t'], pooled['active_week_cluster_t']
    lines = ['# Frozen weather rule: statistical robustness', '',
             f"The point return is resilient to single-week deletion, but the positive lower confidence bound is fragile. The unchanged 85 selections produce 55 wins, 30 losses and +20.00 units ({pct(published['roi'])} ROI) at assumed −110. The weekly cluster-t 99% interval and four-strategy Bonferroni 95% sensitivity both include zero. This is development evidence for a forward paper experiment, not a proven edge.", '',
             '## Dependence and uncertainty', '',
             '| Method | Clusters / df | Standard error | 95% interval | 99% interval |', '|---|---|---:|---|---|',
             f"| Calendar-week score sandwich | {primary['clusters']} / {primary['df']} | {pct(primary['standard_error'])} | {interval(primary['interval_95'])} | {interval(primary['interval_99'])} |",
             f"| Active betting weeks only | {active['clusters']} / {active['df']} | {pct(active['standard_error'])} | {interval(active['interval_95'])} | {interval(active['interval_99'])} |", '',
             f"The originally published percentile week-bootstrap 95% interval is {interval(report['published_percentile_bootstrap_95'])}. Of {pooled['calendar_weeks']} covered weeks, {pooled['zero_bet_weeks']} contain no bets. Bet-count concentration gives an inverse-HHI equivalent of {pooled['bet_weight_effective_week_count']:.2f} equally sized weeks; this is a concentration diagnostic, not substituted degrees of freedom.", '',
             'For weekly selected counts N and profits P, estimate theta = sum(P)/sum(N), score U = P − theta × N, and SE = sqrt[G/(G−1) × sum(U²)] / sum(N). Intervals use t(G−1). This preserves per-bet weighting. The t reference and small-sample factor are approximations, requiring independent weeks; they do not handle persistent cross-week team effects. Zero-bet weeks contain no score information, hence the active-week sensitivity.', '',
             f"Method background: [Cameron and Miller, A Practitioner's Guide to Cluster-Robust Inference]({METHOD_SOURCE}).", '',
             '## Multiplicity sensitivity', '',
             'All intervals below use the calendar-week estimate and two-sided alpha=0.05/m. The four-strategy case is the requested current comparison; larger families are illustrative. The complete prior adaptive search count is unknown, so these are not retroactive familywise-valid discovery claims.', '',
             '| Hypothetical comparisons | Per-interval confidence | Bonferroni 95% family sensitivity interval | Adjusted two-sided p |', '|---:|---:|---|---:|']
    for row in primary['multiplicity_sensitivity']:
        lines.append(f"| {row['hypothetical_family_size']} | {row['two_sided_per_interval_confidence']:.3%} | {interval(row['bonferroni_95_family_interval'])} | {row['bonferroni_adjusted_p']:.4f} |")
    lines += ['', '## Week concentration and deletion', '',
              f"Leave-one-week-out ROI ranges from {interval(pooled['leave_one_week_out_roi_range'])}. The recalculated 95% cluster-t lower bound remains above zero in {pooled['leave_one_week_out_95_lower_positive_count']} of {pooled['calendar_weeks']} deletions (including zero-bet weeks). These are influence diagnostics, not alternative rules selected for deployment.", '',
              f"The most profitable week, {pooled['largest_profit_week']['week']}, has {pooled['largest_profit_week']['wins']} wins and {pooled['largest_profit_week']['losses']} losses, contributing {pooled['largest_profit_week']['profit_units']:+.2f} units ({pooled['largest_profit_week']['share_of_net_profit']:.2%} of net profit). Without it, ROI is {pct(pooled['largest_profit_week']['leave_one_week_out_roi'])}, with 95% cluster-t interval {interval(pooled['largest_profit_week']['leave_one_week_out_cluster_t_95'])}.", '',
              '| Eastern Monday week | Covered | Bets | W–L–P | Profit units | ROI after removing week | 95% cluster-t after removal |', '|---|---:|---:|---|---:|---:|---|']
    for row in pooled['weeks']:
        lines.append(f"| {row['week']} | {row['covered_games']} | {row['bets']} | {row['wins']}–{row['losses']}–{row['pushes']} | {row['profit_units']:+.2f} | {pct(row['leave_one_week_out_roi'])} | {interval(row['leave_one_week_out_cluster_t_95'])} |")
    venues, teams = report['venue_concentration'], report['team_involvement']
    lines += ['', '## Team and venue concentration', '',
              f"The selections span {venues['groups']} venues and {teams['unique_teams']} teams. The largest venue contributes {venues['largest_group_bet_share']:.2%} of bets; the five busiest venues contribute {venues['top_five_group_bet_share']:.2%}. Removing any one venue leaves ROI from {interval(venues['leave_one_group_out_roi_range'])}. Removing every game involving one team, across both home and away roles, leaves ROI from {interval(teams['leave_one_team_out_roi_range'])}.", '',
              '| Venue (top 10 by count) | Bets | W–L | Associated profit | ROI without venue |', '|---|---:|---|---:|---:|']
    for row in venues['groups_table'][:10]:
        lines.append(f"| {row['name']} | {row['bets']} | {row['wins']}–{row['losses']} | {row['profit_units']:+.2f} | {pct(row['leave_one_group_out_roi'])} |")
    lines += ['', '| Team (top 10 by involvement) | Games | Home / away | W–L | Associated profit | ROI without team |', '|---|---:|---|---|---:|---:|']
    for row in teams['teams'][:10]:
        lines.append(f"| {row['team']} | {row['games']} | {row['home_games']} / {row['away_games']} | {row['wins']}–{row['losses']} | {row['associated_profit_units']:+.2f} | {pct(row['leave_one_team_out_roi'])} |")
    lines += ['', teams['interpretation'], '', report['methods']['two_way_team_cluster'], '',
              '## Further evidence needed', '', *['- ' + value for value in report['further_evidence']], '',
              '## Limits', '', *['- ' + value for value in report['limitations']], '',
              'Reproduce with `python scripts/weather_robustness.py --root .`. Full weekly, seasonal, home/away team, venue, and multiplicity results with input hashes are in `weather_robustness.json`. The frozen rule, probabilities, runtime, and source artifacts are unchanged.']
    (directory / 'weather_robustness.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps({'roi': published['roi'], 'weekly_cluster_t': primary, 'active_week_cluster_t': active,
                      'leave_one_week_out_roi_range': pooled['leave_one_week_out_roi_range'],
                      'venues': venues['groups'], 'teams': teams['unique_teams']}, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    main(parser.parse_args().root)
