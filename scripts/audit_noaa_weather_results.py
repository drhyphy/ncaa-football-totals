#!/usr/bin/env python3
"""Independently audit fixed NOAA returns after raw weather reconstruction.

No project model/research module is imported. No alternate threshold, source,
forecast, price assumption, or strategy is tested.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from scipy.stats import t

PLAN_SHA = '47747f5a0216b5b0a43e9df5777575bfafb165ad6616332869eda35c2b508258'
CLASS_SHA = '132874fe46c150121fad2b40d7aefed8b17fb5593f6318b3db3f062ea31d8951'
RAW = Path('data/raw/noaa_weather_research')


def close(one, other, atol=1e-10):
    if one is None or other is None:
        assert one is other
    else:
        assert np.allclose(one, other, rtol=0, atol=atol), (one, other)


def statistics(rows):
    n = len(rows)
    wins = sum(r['actual_total'] < r['market_total'] for r in rows)
    losses = sum(r['actual_total'] > r['market_total'] for r in rows)
    pushes = n-wins-losses
    units = (10*wins-11*losses)/11
    return {'bets': n, 'wins': wins, 'losses': losses, 'pushes': pushes,
            'profit_units': units, 'roi': units/n if n else None,
            'win_rate_excluding_pushes': wins/(wins+losses) if wins+losses else None}


def summary(rows, recorded):
    # Rebuild the denominators and exact integer win/loss accounting rather
    # than using the evaluator's profit vector or weekly aggregates.
    rule = [r for r in rows if r['shadow_under_flag']]
    nonselected = [r for r in rows if not r['shadow_under_flag']]
    calculated = {'games': len(rows), 'weather_rule': statistics(rule),
                  'all_under_same_weather_coverage': statistics(rows),
                  'nonselected_games': statistics(nonselected)}
    assert calculated['games'] == recorded['games']
    for key in ['weather_rule', 'all_under_same_weather_coverage', 'nonselected_games']:
        for metric, value in calculated[key].items():
            close(value, recorded[key][metric])
    blocks = defaultdict(list)
    for row in rows:
        date = datetime.fromisoformat(row['kickoff']).astimezone(ZoneInfo('America/New_York')).date()
        blocks[(date-timedelta(days=date.weekday())).isoformat()].append(row)
    weekly = []
    for week, games in sorted(blocks.items()):
        selected = statistics([r for r in games if r['shadow_under_flag']])
        all_under = statistics(games)
        weekly.append((week, all_under['bets'], all_under['profit_units'],
                       selected['bets'], selected['profit_units']))
    assert len(weekly) == recorded['calendar_week_blocks']
    array = np.array([w[1:] for w in weekly], dtype=float)
    assert recorded['bootstrap_draws'] == 10000 and recorded['bootstrap_seed'] == 20260909
    # The same predeclared draw stream, independently accumulated as week
    # multiplicities rather than a draw-by-week-by-metric tensor.
    rng = np.random.default_rng(20260909)
    picks = rng.integers(0, len(weekly), size=(10000, len(weekly)))
    multiplicities = np.array([np.bincount(draw, minlength=len(weekly)) for draw in picks])
    totals = multiplicities @ array
    valid = totals[:, 2] > 0
    assert int((~valid).sum()) == recorded['bootstrap_draws_with_no_rule_bets']
    assert int(valid.sum()) == recorded['bootstrap_valid_rule_draws']
    point_delta = calculated['weather_rule']['roi']-calculated['all_under_same_weather_coverage']['roi'] if rule else None
    close(point_delta, recorded['rule_minus_all_under_roi'])
    for name, quantiles in [('95', [.025, .975]), ('99', [.005, .995])]:
        interval = contrast = None
        if valid.any() and len(weekly) >= 2:
            rule_roi = totals[valid, 3]/totals[valid, 2]
            contrast_roi = rule_roi-totals[valid, 1]/totals[valid, 0]
            interval = np.quantile(rule_roi, quantiles).tolist()
            contrast = np.quantile(contrast_roi, quantiles).tolist()
        close(interval, recorded['weather_rule_roi_'+name+'_week_bootstrap'])
        close(contrast, recorded['rule_minus_all_under_roi_'+name+'_paired_week_bootstrap'])
        calculated['weather_rule_roi_'+name+'_week_bootstrap'] = interval
        calculated['rule_minus_all_under_roi_'+name+'_paired_week_bootstrap'] = contrast
    active = array[:, 2] > 0
    n, p = array[active, 2], array[active, 3]
    g, total = len(n), n.sum()
    theta = p.sum()/total if total else None
    cluster = recorded['active_week_cluster_t']
    assert cluster['active_weeks'] == g
    close(theta, cluster['roi'])
    if g >= 2:
        score = p-theta*n
        se = math.sqrt(g/(g-1)*math.fsum(score**2))/total
        close(se, cluster['standard_error'])
        assert cluster['df'] == g-1
        for name, alpha in [('95', .05), ('99', .01)]:
            width = t.ppf(1-alpha/2, g-1)*se
            close([theta-width, theta+width], cluster['interval_'+name])
    leaves = []
    assert len(recorded['weeks']) == len(weekly)
    for own, saved in zip(weekly, recorded['weeks']):
        week, count, all_profit, selected_n, selected_profit = own
        assert week == saved['week'] and count == saved['covered_games'] and selected_n == saved['selected_bets']
        close(all_profit, saved['all_under_profit_units'])
        close(selected_profit, saved['selected_profit_units'])
        remain = len(rule)-selected_n
        roi = (calculated['weather_rule']['profit_units']-selected_profit)/remain if remain else None
        assert remain == saved['leave_one_week_out_bets']
        close(roi, saved['leave_one_week_out_roi'])
        if roi is not None:
            leaves.append(roi)
    close([min(leaves), max(leaves)] if leaves else None, recorded['leave_one_week_out_roi_range'])
    calculated.update(calendar_week_blocks=len(weekly), active_rule_weeks=g,
                      rule_minus_all_under_roi=point_delta,
                      leave_one_week_out_roi_range=[min(leaves), max(leaves)] if leaves else None)
    return calculated


def audit(root):
    root = Path(root)
    weather_audit = json.loads((root/'reports/noaa_weather_classification_audit.json').read_text())
    assert weather_audit['status'] == 'passed' and weather_audit['classification_sha256'] == CLASS_SHA
    weather = json.loads((root/RAW/'independent_classification_audit_values.json').read_text())
    assert len(weather) == 1747 and len({r['game_id'] for r in weather}) == len(weather)
    plan = json.loads((root/'reports/noaa_weather_request_plan.json').read_text())
    result = json.loads((root/'reports/noaa_weather_results.json').read_text())
    assert plan['plan_sha256'] == result['plan_sha256'] == PLAN_SHA
    assert result['classification_sha256'] == CLASS_SHA and result['price_assumption'] == -110
    for path, expected in result['source_files_sha256'].items():
        assert hashlib.sha256((root/path).read_bytes()).hexdigest() == expected
    identity = {r['game_id']: r for r in plan['games']}
    columns = ['game_id', 'season', 'home_id', 'away_id', 'market_source', 'market_total',
               'home_score', 'away_score', 'actual_total', 'status']
    # Precedence is reconstructed at the record level: validated CFBD is used
    # only where the role-verified repaired ESPN source has no game record.
    cfbd = pd.read_parquet(root/'data/raw/alternative/cfbd_market_games.parquet', columns=columns,
                          filters=[('season', 'in', [2021, 2022, 2023]), ('validated', '==', True)])
    repaired = pd.read_parquet(root/'data/raw/alternative/espn_verified_pregame_games.parquet', columns=columns,
                              filters=[('season', 'in', [2021, 2022, 2023]), ('role_verified', '==', True)])
    assert not cfbd.game_id.duplicated().any() and not repaired.game_id.duplicated().any()
    market = {}
    for row in cfbd.to_dict('records'):
        market[str(row['game_id'])] = {**row, 'market_source': 'cfbd_'+row['market_source']}
    for row in repaired.to_dict('records'):
        market[str(row['game_id'])] = row
    schedule = {}
    for year in [2021, 2022, 2023]:
        data = pd.read_parquet(root/f'data/raw/sportsdataverse/cfb_schedule_{year}.parquet',
            columns=['game_id', 'season', 'home_id', 'away_id', 'home_score', 'away_score', 'status'])
        for row in data.to_dict('records'):
            gid = str(row['game_id'])
            assert gid not in schedule
            schedule[gid] = row
    rows = []
    for forecast in weather:
        assert forecast['status'] == 'available'
        gid = forecast['game_id']
        planned, offered, final = identity[gid], market[gid], schedule[gid]
        for key in ['season', 'home_id', 'away_id']:
            assert planned[key] == offered[key] == final[key]
        assert offered['market_source'] == planned['market_source']
        assert offered['status'] == final['status'] == 'STATUS_FINAL'
        for key in ['home_score', 'away_score']:
            assert offered[key] == final[key] >= 0 and final[key] == int(final[key])
        total = final['home_score']+final['away_score']
        assert total == offered['actual_total']
        line = float(offered['market_total'])
        assert math.isfinite(line) and 15 <= line <= 100
        rows.append({**forecast, 'season': planned['season'], 'kickoff': planned['kickoff'],
                     'market_source': offered['market_source'], 'market_total': line, 'actual_total': total})
    evaluated = pd.read_parquet(root/RAW/'evaluated_games.parquet').set_index('game_id')
    assert not evaluated.index.duplicated().any() and set(evaluated.index) == {r['game_id'] for r in rows}
    for row in rows:
        saved = evaluated.loc[row['game_id']]
        for key in ['season', 'market_source', 'market_total', 'actual_total', 'shadow_under_flag']:
            assert row[key] == saved[key]
    assert len(rows) == result['coverage']['weather_available_with_final_valid_market_games'] == 1747
    assert sum(r['shadow_under_flag'] for r in rows) == result['coverage']['selected_games'] == 130
    assert result['excluded'] == [] and result['coverage']['evaluation_exclusion_reasons'] == {}
    summaries = {'pooled': summary(rows, result['pooled']), 'by_season': {}, 'by_market_source': {}}
    for year in [2021, 2022, 2023]:
        summaries['by_season'][str(year)] = summary([r for r in rows if r['season'] == year], result['by_season'][str(year)])
    sources = sorted({r['market_source'] for r in rows})
    assert sources == sorted(result['by_market_source'])
    for source in sources:
        summaries['by_market_source'][source] = summary([r for r in rows if r['market_source'] == source], result['by_market_source'][source])
    report = {'status': 'passed', 'audited_at': datetime.now(timezone.utc).isoformat(),
        'plan_sha256': PLAN_SHA, 'classification_sha256': CLASS_SHA,
        'results_file_sha256': hashlib.sha256((root/'reports/noaa_weather_results.json').read_bytes()).hexdigest(),
        'implementation': 'No project module imports; independent raw-GRIB classifications, record-level market precedence and identity/final-score joins, integer W/L/P accounting, week-multiplicity bootstrap and ratio-score t.',
        'games': len(rows), 'source_groups': len(sources), 'bootstrap_draws': 10000,
        'bootstrap_seed': 20260909, 'summary_groups_checked': 1+3+len(sources),
        **summaries, 'strategy_changes': False, 'executable_edge_confirmed': False}
    (root/'reports/noaa_weather_results_audit.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
    print(json.dumps(report, indent=2, allow_nan=False))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1]/'model')
    audit(parser.parse_args().root)
