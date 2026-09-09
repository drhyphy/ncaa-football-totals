"""Publish a reproducible, repaired-data research bundle and readable report.

This command renders existing research results; it does not fit models, alter
forecasts, or promote quarantined experiments. --check detects stale outputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

PRIMARY = "opponent_adjusted_development.json"
REPAIR = "espn_verified_provider_repair.json"
AUDIT = "espn_market_timing_audit.json"
SENSITIVITY = "source_sensitivity.json"
PROVENANCE = "cfbd_and_verified_pregame_provider_only"
LEGACY = (
    "opponent_adjusted_original_data.json",
    "opponent_adjusted_mixed_data_quarantined.json",
    "conditional_distribution_development.json",
    "totals_backtest_summary.json",
    "public_superensemble_v2_summary.json",
    "drive_clock_development_summary.json",
    "sensitivity_exclude_55_5.json",
)
LABELS = {
    "market_only": "Market baseline",
    "opponent_adjusted_ridge": "Opponent-adjusted ridge",
    "opponent_adjusted_structural": "Opponent-adjusted structural",
}
ORDINARY_CONFIGURATIONS = ('market_only', 'opponent_adjusted_ridge', 'ordinary_ridge', 'ordinary_hgb')
ORDINARY_COMPARISONS = {(new, base) for new in ORDINARY_CONFIGURATIONS[2:] for base in ORDINARY_CONFIGURATIONS[:2]}
DIRECT_CONFIGURATIONS = ('raw50', 'rawridge', 'context_logit', 'opponent_logit', 'context_hgb', 'opponent_hgb')
DIRECT_COMPARISONS = (('opponent_logit', 'context_logit'), ('opponent_hgb', 'context_hgb'),
                      ('opponent_logit', 'rawridge'), ('opponent_hgb', 'rawridge'))
# Completed immutable study, including its independent audit receipt. Publication
# does not silently accept revised results or a substitute selection/audit.
DIRECT_REPORT_SHA256 = {
    'direct_probability_research_plan.json': 'ef36cdea636dd87661141560af6f3934bf883730aab6d354370c218f26b5778b',
    'direct_probability_selection.json': 'f444d8c04790a66beb73a069858304834c34763be16aea140ee0990bd40d77be',
    'direct_probability_results.json': 'ba150975d6565a87539fd5dce80ca88212eda1ed91a24f8fd9f54ce181cc37cf',
    'direct_probability_research_audit.json': 'bcf606dd375297b33436e2dfc8a10ce6bab18a584e029f9dd64393e961c5c36d',
}
DIRECT_YEAR_COUNTS = {'2021': 428, '2022': 403, '2023': 777, '2024': 798, '2025': 852}


def percent(value):
    if value is None or not math.isfinite(float(value)):
        return "—"
    value = 0.0 if abs(float(value)) < 0.00005 else float(value)
    return f"{value:+.2%}"


def interval(values):
    return "—" if values is None else f"{percent(values[0])} to {percent(values[1])}"


def metric_summary(metrics):
    keys = ("games", "mae", "rmse", "mae_delta", "mae_delta_week_bootstrap_95",
            "brier", "bets", "wins", "losses", "assumed_minus110_roi", "roi_week_bootstrap_95")
    result = {key: metrics.get(key) for key in keys}
    result["pushes"] = int(metrics.get("bets", 0)) - int(metrics.get("wins", 0)) - int(metrics.get("losses", 0))
    result["roi_display"] = percent(metrics.get("assumed_minus110_roi"))
    result["roi_interval_display"] = interval(metrics.get("roi_week_bootstrap_95"))
    return result


def noaa_summary(summary):
    """Retain all cohorts/uncertainty while leaving per-week rows in the report."""
    groups = ('weather_rule', 'all_under_same_weather_coverage', 'nonselected_games')
    for group in groups:
        m = summary[group]
        if any(type(m.get(key)) is not int or m[key] < 0 for key in ('bets', 'wins', 'losses', 'pushes')) or m['bets'] != m['wins']+m['losses']+m['pushes']:
            raise ValueError('NOAA stake/outcome counts are inconsistent')
        expected = m['wins']*100/110-m['losses']
        if not math.isclose(m['profit_units'], expected, abs_tol=1e-8):
            raise ValueError('NOAA return differs from assumed minus110 settlement')
        if (m['bets'] == 0 and m['roi'] is not None) or (m['bets'] > 0 and (m['roi'] is None or not math.isclose(m['roi'], expected/m['bets'], abs_tol=1e-10))):
            raise ValueError('NOAA zero-bet ROI must be unavailable; settled ROI must match stakes')
    rule, benchmark, nonselected = (summary[key] for key in groups)
    if benchmark['bets'] != summary['games'] or rule['bets']+nonselected['bets'] != summary['games']:
        raise ValueError('NOAA coverage and selection denominators disagree')
    if any(rule[key]+nonselected[key] != benchmark[key] for key in ('wins', 'losses', 'pushes')):
        raise ValueError('NOAA selected and nonselected outcomes do not conserve coverage')
    keys = ('games', 'calendar_week_blocks', *groups, 'price_assumption',
            'bootstrap_draws', 'bootstrap_seed', 'bootstrap_draws_with_no_rule_bets', 'bootstrap_valid_rule_draws',
            'weather_rule_roi_95_week_bootstrap', 'weather_rule_roi_99_week_bootstrap',
            'rule_minus_all_under_roi', 'rule_minus_all_under_roi_95_paired_week_bootstrap',
            'rule_minus_all_under_roi_99_paired_week_bootstrap', 'active_week_cluster_t',
            'leave_one_week_out_roi_range')
    return {key: summary[key] for key in keys}


def ordinary_summary(summary, compact=False):
    scores = summary['configurations']
    if set(scores) != set(ORDINARY_CONFIGURATIONS):
        raise ValueError('Ordinary study must retain all four configurations')
    n = summary['games']
    if type(n) is not int or n <= 0:
        raise ValueError('Ordinary study requires a nonempty common game cohort')
    for name, row in scores.items():
        if row.get('games') != n or any(not isinstance(row.get(key), (int, float)) or not math.isfinite(row[key]) for key in ('mse', 'rmse', 'mae', 'mean_error')):
            raise ValueError('Ordinary configurations must use the same finite-score cohort')
        if row['mse'] < 0 or row['mae'] < 0 or row['rmse'] < 0 or not math.isclose(row['rmse']**2, row['mse'], abs_tol=1e-8):
            raise ValueError('Ordinary MSE/RMSE metrics disagree')
    comparisons = summary['comparisons']
    if len(comparisons) != 4 or {(row['candidate'], row['reference']) for row in comparisons} != ORDINARY_COMPARISONS:
        raise ValueError('Ordinary study must retain all four paired comparisons')
    for row in comparisons:
        if row['games'] != n or any(not math.isclose(row[key+'_difference'], scores[row['candidate']][key]-scores[row['reference']][key], abs_tol=1e-8) for key in ('mse', 'mae')):
            raise ValueError('Ordinary paired comparisons differ from reported scores')
    result = {'games': n, 'configurations': {name: scores[name] for name in ORDINARY_CONFIGURATIONS}}
    if not compact:
        result['comparisons'] = comparisons
    return result


def direct_scores(scores, expected_games):
    if set(scores) != set(DIRECT_CONFIGURATIONS):
        raise ValueError('Direct probability publication requires all six configurations')
    result = {}
    for name in DIRECT_CONFIGURATIONS:
        row = scores[name]
        if type(row.get('games')) is not int or row['games'] != expected_games:
            raise ValueError('Direct probability configurations must retain exact common counts')
        for metric in ('log_loss', 'brier'):
            value = row.get(metric)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (metric == 'brier' and value > 1):
                raise ValueError('Direct probability scores must be finite and valid')
        result[name] = {key: row[key] for key in ('games', 'log_loss', 'brier')}
    return result


def direct_summary(summary, years):
    expected = sum(DIRECT_YEAR_COUNTS[year] for year in years)
    if summary.get('games') != expected or set(summary.get('by_year', {})) != set(years):
        raise ValueError('Direct probability period/year coverage changed')
    scores = direct_scores(summary['configurations'], expected)
    annual = {year: direct_scores(summary['by_year'][year], DIRECT_YEAR_COUNTS[year]) for year in years}
    sources = {}
    for name, group in summary['by_source'].items():
        count = group.get('raw50', {}).get('games')
        if type(count) is not int or count <= 0:
            raise ValueError('Direct probability source coverage must be nonempty')
        sources[name] = direct_scores(group, count)
    if sum(group['raw50']['games'] for group in sources.values()) != expected:
        raise ValueError('Direct probability source counts do not conserve coverage')
    for groups in (annual, sources):
        for candidate in DIRECT_CONFIGURATIONS:
            for metric in ('log_loss', 'brier'):
                pooled = sum(group[candidate][metric]*group[candidate]['games'] for group in groups.values())/expected
                if not math.isclose(pooled, scores[candidate][metric], rel_tol=0, abs_tol=1e-12):
                    raise ValueError('Direct probability pooled scores do not match year/source scores')
    comparisons = summary.get('comparisons', [])
    if [(row.get('candidate'), row.get('reference')) for row in comparisons] != list(DIRECT_COMPARISONS):
        raise ValueError('Direct probability must retain all four fixed comparisons')
    for row in comparisons:
        if row.get('games') != expected or type(row.get('week_blocks')) is not int or row['week_blocks'] < 2:
            raise ValueError('Direct probability paired counts changed')
        delta = scores[row['candidate']]['log_loss']-scores[row['reference']]['log_loss']
        if not math.isclose(row['log_loss_difference'], delta, rel_tol=0, abs_tol=1e-12):
            raise ValueError('Direct probability comparison disagrees with scores')
        for key in ('interval_95', 'interval_98_75'):
            ci = row.get(key)
            if not isinstance(ci, list) or len(ci) != 2 or not all(type(v) in (int, float) and math.isfinite(v) for v in ci) or ci[0] > ci[1]:
                raise ValueError('Direct probability interval is invalid')
        if not row['interval_98_75'][0] <= row['interval_95'][0] <= row['interval_95'][1] <= row['interval_98_75'][1]:
            raise ValueError('Direct probability sensitivity interval must contain its 95% interval')
    return {'games': expected, 'configurations': scores, 'by_year': annual,
            'by_source': sources, 'comparisons': comparisons}


def direct_publication(read, read_bytes, inputs):
    records = {name: read(name) for name in DIRECT_REPORT_SHA256}
    for name, expected in DIRECT_REPORT_SHA256.items():
        if inputs['model/reports/'+name] != expected:
            raise ValueError('Direct probability immutable report hash changed: '+name)
    plan, selection, result, audit = (records[name] for name in DIRECT_REPORT_SHA256)
    protocol_name = 'DIRECT_PROBABILITY_RESEARCH_PLAN.md'
    read_bytes(protocol_name)
    protocol_hash = inputs['model/reports/'+protocol_name]
    if (plan.get('candidate_order') != list(DIRECT_CONFIGURATIONS)
            or plan.get('comparison_pairs') != [list(pair) for pair in DIRECT_COMPARISONS]
            or plan.get('chronology', {}).get('selection_years') != [2021, 2022, 2023, 2024]
            or plan.get('source_files_sha256', {}).get('reports/'+protocol_name) != protocol_hash
            or plan.get('implementation_protocol_sha256') != protocol_hash):
        raise ValueError('Direct probability frozen plan/protocol contract changed')
    if (selection.get('phase') != 'selection' or selection.get('2025_metrics_computed') is not False
            or selection.get('historical_data_reused') is not True or selection.get('active_model_changed') is not False
            or result.get('phase') != '2025_reused_development_check' or result.get('active_model_changed') is not False
            or result.get('historical_ev_or_roi_computed') is not False or result.get('high_confidence_edge_established') is not False):
        raise ValueError('Direct probability development study cannot become live betting evidence')
    for record in (selection, result):
        p = record.get('provenance', {})
        if (p.get('plan_sha256') != DIRECT_REPORT_SHA256['direct_probability_research_plan.json']
                or p.get('protocol_sha256') != protocol_hash or p.get('implementation_sha256') != plan.get('implementation_sha256')):
            raise ValueError('Direct probability stage provenance differs from frozen inputs')
    if result.get('selection_record_sha256') != DIRECT_REPORT_SHA256['direct_probability_selection.json'] or result.get('selection_summary') != selection.get('summary'):
        raise ValueError('Direct probability later check does not retain its frozen selection')
    for name in tuple(DIRECT_REPORT_SHA256)[:3]:
        if audit.get('input_report_sha256', {}).get('reports/'+name) != DIRECT_REPORT_SHA256[name]:
            raise ValueError('Direct probability independent audit does not bind these reports')
    if audit.get('status') != 'passed_no_material_discrepancy' or audit.get('new_candidate_promoted') is not False or audit.get('model_refit_count') != 20 or audit.get('ridge_refit_count') != 5:
        raise ValueError('Direct probability independent audit status/counts changed')
    for year, count in {'2020': 323, **DIRECT_YEAR_COUNTS}.items():
        if (plan.get('cohort', {}).get('halfpoint_games_by_season', {}).get(year) != count
                or audit.get('coverage', {}).get('by_year', {}).get(year, {}).get('eligible_halfpoint_games') != count):
            raise ValueError('Direct probability frozen/audited cohort counts differ')
    earlier = direct_summary(selection['summary'], tuple(DIRECT_YEAR_COUNTS)[:4])
    later = direct_summary(result['development_2025'], ('2025',))
    selected = min(DIRECT_CONFIGURATIONS, key=lambda name: earlier['configurations'][name]['log_loss'])
    if selected != 'rawridge' or any(record.get('selected_configuration') != selected for record in (selection, result, audit)):
        raise ValueError('Direct probability selection must remain the pre-2025 raw ridge')
    links = []
    for name, label in (('direct_probability_results.md', 'All six configurations and interpretation'),
                        ('direct_probability_results.json', 'Full scores and paired intervals'),
                        (protocol_name, 'Frozen direct-probability plan'),
                        ('direct_probability_research_plan.json', 'Pinned machine plan'),
                        ('direct_probability_selection.json', 'Selection committed before the 2025 check'),
                        ('DIRECT_PROBABILITY_RESEARCH_AUDIT.md', 'Independent direct-probability audit'),
                        ('direct_probability_research_audit.json', 'Independent audit receipt')):
        read_bytes(name)
        links.append({'name': label, 'url': 'https://github.com/drhyphy/ncaa-football-totals/blob/main/model/reports/'+name,
                      'sha256': inputs['model/reports/'+name]})
    return {'version': result['schema_version'], 'status': 'reused_development_probability_score_study',
        'evaluated_at': result['created_at'], 'candidate_order': list(DIRECT_CONFIGURATIONS),
        'configuration_count': 6, 'selected_configuration': selected,
        'selection_2021_2024': earlier, 'reused_2025': later,
        'source_report_sha256': dict(DIRECT_REPORT_SHA256), 'protocol_sha256': protocol_hash,
        'implementation_freeze_commit': selection['provenance']['git_commit'],
        'selection_commit': result['provenance']['git_commit'],
        'historical_data_reused': True, 'roi_evaluated': False, 'live_policy_changes': False,
        'credible_executable_edge_established': False,
        'all_new_2025_point_scores_worse_than_raw50': all(later['configurations'][name][metric] > later['configurations']['raw50'][metric]
            for name in DIRECT_CONFIGURATIONS[2:] for metric in ('log_loss', 'brier')),
        'all_local_2025_comparison_intervals_include_zero': all(row['interval_98_75'][0] <= 0 <= row['interval_98_75'][1] for row in later['comparisons']),
        'limitations': result['limits'], 'links': links}


def render_opponent_report(primary):
    ridge = primary["pooled"]["opponent_adjusted_ridge"]
    last_year = max(primary["by_season"], key=int)
    last = primary["by_season"][last_year]["opponent_adjusted_ridge"]
    lines = [
        "# Opponent-adjusted development experiment", "",
        f"The repaired-data ridge candidate returned {percent(ridge['assumed_minus110_roi'])} across {ridge['bets']:,} hypothetical bets, with a descriptive 95% week-bootstrap interval of {interval(ridge['roi_week_bootstrap_95'])}. In {last_year}, it returned **{percent(last['assumed_minus110_roi'])}** across {last['bets']:,} bets. These results do not establish a profitable betting edge.", "",
        "The comparison uses documented CFBD fields and verified non-live ESPN bookmaker providers. Exact closing times, 06:30 ET availability, and offered total-side prices are unverified. Every return assumes −110. All 2019–2025 periods have been reused in research; none is an untouched test. Earlier results using the compromised scalar odds archive remain quarantined.", "",
        "## Pooled results", "",
        "| Candidate | Games | MAE | MAE change vs market | Bets | W–L–P | ROI at assumed −110 | Descriptive 95% ROI interval |",
        "|---|---:|---:|---:|---:|---|---:|---|",
    ]
    for candidate, metrics in primary["pooled"].items():
        m = metric_summary(metrics)
        lines.append(f"| {LABELS.get(candidate, candidate)} | {m['games']:,} | {m['mae']:.3f} | {m['mae_delta']:+.3f} | {m['bets']} | {m['wins']}–{m['losses']}–{m['pushes']} | {m['roi_display']} | {m['roi_interval_display']} |")
    lines.extend([
        "", "## Results by season", "",
        "Each season fits only earlier seasons. Both candidates and all evaluated years appear below, including losing periods. A positive single-season result is not an independent validation after repeated research use.", "",
        "| Season | Candidate | Games | Bets | W–L–P | ROI at assumed −110 | Descriptive 95% ROI interval | MAE change vs market |",
        "|---|---|---:|---:|---|---:|---|---:|",
    ])
    for year, candidates in sorted(primary["by_season"].items(), key=lambda item: int(item[0])):
        for candidate, metrics in candidates.items():
            if candidate == "market_only":
                continue
            m = metric_summary(metrics)
            lines.append(f"| {year} | {LABELS.get(candidate, candidate)} | {m['games']:,} | {m['bets']} | {m['wins']}–{m['losses']}–{m['pushes']} | {m['roi_display']} | {m['roi_interval_display']} | {m['mae_delta']:+.3f} |")
    lines.extend([
        "", "## Specification and limits", "",
        "Ratings use a shared league intercept, offensive team and opposing-defense effects, partial pooling, recency decay, and offseason decay. Separate regressions estimate scoring, points per drive, possessions and duration, pass/rush yards, and passing share. The two existing specifications remain fixed: a strongly regularized residual model and a simple structural shrinkage model. No EPA, annual source-file features, or publisher power ratings enter these candidates.", "",
        "Team observations must precede the weekly Monday cutoff; kickoff plus six hours is the recorded availability proxy. Each test season estimates residual uncertainty from earlier seasons. The fixed research rule requires modeled EV ≥3%, stressed EV ≥1% at assumed −110, and at least five prior games. Historical data cannot reproduce current quote-presence, execution, or bookmaker-account checks.", "",
        "Week-bootstrap intervals are descriptive, do not correct for all previous model searches, and do not capture every data-quality or execution risk. Improved coverage and repaired market roles do not create an untouched holdout. Current forecasts remain experimental until prospective records support a stronger conclusion.", "",
        f"Specification: `{primary['specification']}`. Data fingerprint: `{primary['data_fingerprint']}`.", "",
        "Generated from `opponent_adjusted_development.json` by `python scripts/publish_research.py --root .`. Re-run after changing research reports; the generator does not fit models or modify their artifacts.",
    ])
    return "\n".join(lines) + "\n"


def build_outputs(root):
    root = Path(root).resolve()
    reports = root / "model/reports"
    inputs = {}

    def read_bytes(name):
        path = reports / name
        content = path.read_bytes()
        inputs[f"model/reports/{name}"] = hashlib.sha256(content).hexdigest()
        return content

    def read(name):
        return json.loads(read_bytes(name))

    primary, repair, audit, sensitivity = [read(name) for name in (PRIMARY, REPAIR, AUDIT, SENSITIVITY)]
    if primary.get("market_provenance") != PROVENANCE:
        raise ValueError("Primary research does not use the required repaired market provenance")
    if primary.get("data_fingerprint") != sensitivity.get("primary_data_fingerprint"):
        raise ValueError("Source sensitivity is stale: primary data fingerprints differ")
    if "2025" not in primary.get("by_season", {}):
        raise ValueError("Primary publication must retain the 2025 development period")
    artifact_path = root / "model/data/models/opponent_adjusted_v1.json"
    if artifact_path.exists():
        artifact = json.loads(artifact_path.read_text())
        if artifact.get("data_fingerprint") != primary.get("data_fingerprint"):
            raise ValueError("Primary report and fitted artifact fingerprints differ")

    pooled = {candidate: metric_summary(m) for candidate, m in primary["pooled"].items()}
    by_season = {year: {candidate: metric_summary(m) for candidate, m in candidates.items()}
                 for year, candidates in primary["by_season"].items()}
    current_reports = {
        PRIMARY: {
            "role": "primary_repaired_development",
            "specification": primary["specification"],
            "data_fingerprint": primary["data_fingerprint"],
            "market_provenance": primary["market_provenance"],
            "pooled": pooled,
            "by_season": by_season,
            "market_source_counts": primary["market_source_counts"],
            "historical_price_assumption": -110,
        },
        REPAIR: {key: repair[key] for key in ("label", "policy", "input_games", "verified_provider_games", "excluded_games", "coverage", "provider_counts", "limitations")},
        AUDIT: {key: audit[key] for key in ("status", "sample_design", "conclusion", "summary")},
        SENSITIVITY: {
            "role": "supporting_reused_development_source_check",
            "primary_data_fingerprint": sensitivity["primary_data_fingerprint"],
            "line_discrepancies": sensitivity["line_discrepancies"],
            "pooled": {source: {candidate: metric_summary(m) for candidate, m in candidates.items()}
                       for source, candidates in sensitivity["pooled"].items()},
            "limits": "Different provider/line source, unchanged models and gates. Same-game cohorts reported separately. Offered total-side prices and quote timestamps are missing; all ROI assumes -110. Pooled intervals remain descriptive and do not establish profitability.",
        },
    }
    quarantine = []
    for filename in LEGACY:
        path = reports / filename
        if path.exists():
            quarantine.append({
                "report": f"model/reports/{filename}",
                "status": "quarantined_not_primary_evidence",
                "reason": "Uses original or mixed scalar market data with unverified or contaminated pregame timing; retained for research audit, excluded from current evidence.",
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            })
    weather = {"status": "shadow_research_no_validated_weather_edge"}
    capture_path = reports / "weather_forecast_capture.json"
    if capture_path.exists():
        capture = read(capture_path.name)
        weather.update({"forecast_capture_as_of": capture.get("as_of"), "captured_games": len(capture.get("games", [])),
                        "historical_archive_probe": capture.get("historical_archive_probe")})
    plan_path = reports / "weather_request_plan.json"
    if plan_path.exists():
        plan = read(plan_path.name)
        weather["fixed_historical_request_plan"] = {key: plan.get(key) for key in (
            "version", "created_at", "included_games", "input_games", "lead_hours", "publication_buffer_hours",
            "decision_policy", "market_role", "plan_sha256", "outcome_data_used_for_plan")}
    weather_results_path = reports / "weather_published_hypothesis_results.json"
    if weather_results_path.exists():
        result = read(weather_results_path.name)
        weather["historical_hypothesis_results"] = {key: result.get(key) for key in (
            "version", "plan_sha256", "partial", "repaired_market_games", "venue_eligible_games",
            "weather_covered_games", "weather_missing_reasons", "thresholds", "pooled", "by_season", "limitations")}
        weather["historical_hypothesis_results"]["missing_games"] = len(result.get("missing_game_ids", []))
        weather["interpretation"] = "Fixed weather hypothesis on reused development data, with covered-game all-under control. This is not a calibrated win probability, current EV estimate, or prospective profitability record."
    weather_sensitivity_path = reports / "weather_source_sensitivity.json"
    if weather_sensitivity_path.exists():
        sensitivity_weather = read(weather_sensitivity_path.name)
        expected_plan = weather.get("historical_hypothesis_results", {}).get("plan_sha256")
        if expected_plan != sensitivity_weather.get("plan_sha256"):
            raise ValueError("Weather source sensitivity is stale or lacks its matching hypothesis report")
        weather["market_source_sensitivity"] = {key: sensitivity_weather.get(key) for key in (
            "version", "status", "plan_sha256", "weather_flags_changed", "only_repriced_field", "price_assumption",
            "primary_games", "common_games", "unshared_primary_games", "unshared_primary_weather_rule_bets",
            "source_counts", "line_discrepancies", "pooled", "by_season", "paired_quote_impact", "limitations")}
    weather_robustness_path = reports / "weather_robustness.json"
    if weather_robustness_path.exists():
        robustness = read(weather_robustness_path.name)
        hypothesis = weather.get("historical_hypothesis_results", {})
        if robustness.get("plan_sha256") != hypothesis.get("plan_sha256") or not hypothesis:
            raise ValueError("Weather robustness is stale or lacks its matching hypothesis report")
        for source_name in ("weather_request_plan.json", "weather_published_hypothesis_results.json"):
            key = f"model/reports/{source_name}"
            if robustness.get("input_sha256", {}).get(key) != inputs.get(key) or key not in inputs:
                raise ValueError("Weather robustness input source hash is stale: " + source_name)
        expected = hypothesis["pooled"]["weather_rule"]
        if any(robustness.get("published_result", {}).get(key) != expected.get(key) for key in ("bets", "wins", "losses", "pushes", "roi")):
            raise ValueError("Weather robustness published result differs from its source")
        pooled_robustness = robustness["pooled"]
        weather["statistical_robustness"] = {
            "version": robustness["version"], "status": robustness["status"],
            "plan_sha256": robustness["plan_sha256"],
            "published_result": robustness["published_result"],
            "calendar_weeks": pooled_robustness["calendar_weeks"],
            "active_weeks": pooled_robustness["active_weeks"],
            "all_covered_week_cluster_t": pooled_robustness["all_covered_week_cluster_t"],
            "active_week_cluster_t": pooled_robustness["active_week_cluster_t"],
            "leave_one_week_out_roi_range": pooled_robustness["leave_one_week_out_roi_range"],
            "venue_concentration": {key: robustness["venue_concentration"][key] for key in ("groups", "largest_group_bet_share", "top_five_group_bet_share", "leave_one_group_out_roi_range")},
            "team_involvement": {key: robustness["team_involvement"][key] for key in ("unique_teams", "largest_team_game_share", "leave_one_team_out_roi_range")},
            "multiplicity_interpretation": robustness["methods"]["multiplicity"],
            "limitations": robustness["limitations"],
            "report_url": "https://github.com/drhyphy/ncaa-football-totals/blob/main/model/reports/weather_robustness.md",
        }
    source_audit_path = reports / "WEATHER_PRIMARY_SOURCE_AUDIT.md"
    if source_audit_path.exists():
        read_bytes(source_audit_path.name)
        weather["primary_source_audit"] = {
            "report": "model/reports/WEATHER_PRIMARY_SOURCE_AUDIT.md",
            "url": "https://github.com/drhyphy/ncaa-football-totals/blob/main/model/reports/WEATHER_PRIMARY_SOURCE_AUDIT.md",
            "sha256": inputs["model/reports/WEATHER_PRIMARY_SOURCE_AUDIT.md"],
        }
    noaa_path = reports / "noaa_weather_results.json"
    if noaa_path.exists():
        noaa, noaa_plan = read(noaa_path.name), read("noaa_weather_request_plan.json")
        read_bytes("NOAA_WEATHER_EVALUATION_PROTOCOL.md")
        read_bytes("noaa_weather_results.md")
        plan_body = {key: value for key, value in noaa_plan.items() if key != 'plan_sha256'}
        plan_hash = hashlib.sha256(json.dumps(plan_body, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        if noaa.get('plan_sha256') != plan_hash or noaa_plan.get('plan_sha256') != plan_hash or noaa.get('thresholds') != noaa_plan.get('thresholds'):
            raise ValueError('NOAA results differ from their frozen request plan')
        protocol_hash = inputs['model/reports/NOAA_WEATHER_EVALUATION_PROTOCOL.md']
        if noaa.get('protocol_sha256') != protocol_hash or noaa.get('source_files_sha256', {}).get('reports/NOAA_WEATHER_EVALUATION_PROTOCOL.md') != protocol_hash:
            raise ValueError('NOAA evaluation protocol source hash is stale')
        if any(noaa.get('source_files_sha256', {}).get(path) != expected for path, expected in noaa_plan['source_files_sha256'].items()):
            raise ValueError('NOAA result source hashes differ from the frozen source plan')
        if noaa.get('status') != 'separate_reused_development_forecast_source_replication' or noaa.get('price_assumption') != -110 or noaa.get('no_live_policy_changes') is not True or noaa.get('credible_executable_edge_established') is not False:
            raise ValueError('NOAA source replication cannot become executable or live betting evidence')
        if noaa.get('timing_evidence_class') != 'original_s3_metadata_availability_proxy_not_certified_publication':
            raise ValueError('NOAA publication must retain the timestamp availability-proxy qualification')
        if set(noaa.get('by_season', {})) != {'2021', '2022', '2023'}:
            raise ValueError('NOAA publication must retain all three seasons')
        noaa_pooled = noaa_summary(noaa['pooled'])
        noaa_years = {year: noaa_summary(row) for year, row in sorted(noaa['by_season'].items())}
        noaa_sources = {source: noaa_summary(row) for source, row in sorted(noaa['by_market_source'].items())}
        for partition in (noaa_years, noaa_sources):
            if sum(row['games'] for row in partition.values()) != noaa_pooled['games'] or any(sum(row['weather_rule'][key] for row in partition.values()) != noaa_pooled['weather_rule'][key] for key in ('bets', 'wins', 'losses', 'pushes')):
                raise ValueError('NOAA annual/source partitions do not conserve pooled coverage')
        coverage = noaa['coverage']
        if coverage['planned_games'] != len(noaa_plan['games']) or coverage['weather_available_with_final_valid_market_games'] != noaa_pooled['games'] or coverage['selected_games'] != noaa_pooled['weather_rule']['bets']:
            raise ValueError('NOAA coverage funnel differs from its results')
        weather['original_noaa_2021_2023'] = {
            'version': noaa['version'], 'status': noaa['status'], 'evaluated_at': noaa['evaluated_at'],
            'plan_sha256': plan_hash, 'classification_sha256': noaa['classification_sha256'],
            'classification_file_sha256': noaa['classification_file_sha256'], 'protocol_sha256': protocol_hash,
            'thresholds': noaa['thresholds'], 'price_assumption': -110,
            'timing_evidence_class': noaa['timing_evidence_class'], 'timing_limitation': noaa['timing_limitation'],
            'multipart_field_ranges': noaa['multipart_field_ranges'], 'coverage': coverage,
            'pooled': noaa_pooled, 'by_season': noaa_years, 'by_market_source': noaa_sources,
            'source_game_counts': {source: row['games'] for source, row in noaa_sources.items()},
            'source_summary_note': 'Source splits are descriptive. Zero- or one-selection subgroups cannot estimate stable profitability or strategy uncertainty.',
            'no_live_policy_changes': True, 'credible_executable_edge_established': False,
            'combined_with_other_weather_studies': False,
            'interpretation': 'Separate original NOAA 2021–2023 forecast-source replication on reused historical data. The older-period test did not confirm a profitable edge. Prices assume −110 and source times are availability proxies. No combined historical ROI or live policy change.',
            'limitations': noaa['limitations'],
            'links': [{'name': label, 'url': 'https://github.com/drhyphy/ncaa-football-totals/blob/main/model/reports/'+filename}
                      for label, filename in (('Full NOAA results', 'noaa_weather_results.md'),
                                              ('NOAA data and all exclusions', 'noaa_weather_results.json'),
                                              ('Fixed NOAA evaluation protocol', 'NOAA_WEATHER_EVALUATION_PROTOCOL.md'))],
        }
        for filename, label in (('NOAA_WEATHER_REQUEST_PLAN.md', 'Frozen NOAA request plan'),
                                ('NOAA_WEATHER_SOURCE_AUDIT.md', 'Independent NOAA source audit'),
                                ('NOAA_WEATHER_DOWNLOAD_AUDIT.md', 'NOAA download integrity audit'),
                                ('NOAA_WEATHER_RESULTS_AUDIT.md', 'Independent NOAA classification and result audit')):
            if (reports/filename).exists():
                read_bytes(filename)
                weather['original_noaa_2021_2023']['links'].append({'name': label, 'url': 'https://github.com/drhyphy/ncaa-football-totals/blob/main/model/reports/'+filename})
    weather_replay_path = reports / "weather_2026_replay_results.json"
    if weather_replay_path.exists():
        weather_replay, weather_replay_plan = read(weather_replay_path.name), read("weather_2026_replay_plan.json")
        if weather_replay.get("plan_sha256") != weather_replay_plan.get("plan_sha256") or weather_replay.get("rule") != weather_replay_plan.get("thresholds"):
            raise ValueError("2026 weather replay differs from its fixed plan")
        for cohort in weather_replay["cohorts"].values():
            if cohort["rule"]["bets"] == 0 and cohort["rule"]["roi"] is not None:
                raise ValueError("Zero-bet weather replay cannot publish a realized ROI")
        weather["archived_2026_replay"] = {key: weather_replay[key] for key in (
            "version", "plan_sha256", "evaluated_at", "cohort_plan_counts", "forecast_unavailable_rows", "cohorts", "rule", "limitations")}
        weather["archived_2026_replay"].update({
            "prospective_model_performance": False, "exact_0630_replay": False,
            "interpretation": "Reconstructed after games. The two-book primary and overlapping one-book sensitivity remain separate. Zero qualifying bets means ROI is unavailable and provides no strategy performance evidence.",
            "links": [{"name": "2026 weather replay data", "url": "https://github.com/drhyphy/ncaa-football-totals/blob/main/model/reports/weather_2026_replay_results.json"},
                      {"name": "2026 weather replay plan", "url": "https://github.com/drhyphy/ncaa-football-totals/blob/main/model/reports/WEATHER_2026_REPLAY_PLAN.md"}],
        })
        for filename in ("weather_2026_replay_results.md", "WEATHER_2026_REPLAY_PLAN.md"):
            if (reports / filename).exists():
                read_bytes(filename)
        if (reports / "weather_2026_replay_results.md").exists():
            weather["archived_2026_replay"]["links"][0] = {"name": "2026 weather replay report", "url": "https://github.com/drhyphy/ncaa-football-totals/blob/main/model/reports/weather_2026_replay_results.md"}
    archived_replay = None
    replay_path = reports / "archived_2026_replay_results.json"
    if replay_path.exists():
        replay, replay_plan = read(replay_path.name), read("archived_2026_replay_plan.json")
        if (reports / "archived_2026_replay_results.md").exists():
            read_bytes("archived_2026_replay_results.md")
        if replay.get("plan_sha256") != replay_plan.get("plan_sha256") or not replay.get("plan_sha256"):
            raise ValueError("2026 archived-price replay has a stale or missing plan")
        if replay.get("status") != "retrospective_2026_replay_with_observed_prices_not_prospective_forecasts":
            raise ValueError("2026 archived-price replay must retain its retrospective status")
        planned = {(row["source_sha256"], row["observed_at"]) for row in replay_plan["snapshots"]}
        cohorts = {}
        for name, cohort in replay["cohorts"].items():
            snapshots = cohort["snapshots"]
            if cohort["snapshot_count"] != len(snapshots) or any((row["source_sha256"], row["observed_at"]) not in planned for row in snapshots):
                raise ValueError("2026 archived-price replay capture times or hashes differ from the plan")
            cohorts[name] = {"books": cohort["books"], "snapshot_count": cohort["snapshot_count"],
                             "forecast_games": cohort["forecast_games"],
                             "positions": {candidate: {key: metrics.get(key) for key in ("model_version", "candidate", "bets", "pending", "wins", "losses", "pushes", "profit_units", "roi", "roi_95_low", "roi_95_high", "week_clusters")}
                                           for candidate, metrics in cohort["positions"].items()},
                             "snapshots": [{key: row[key] for key in ("observed_at", "games", "source_sha256")} for row in snapshots]}
        archived_replay = {"version": replay["version"], "status": replay["status"],
                           "plan_sha256": replay["plan_sha256"], "evaluated_at": replay["evaluated_at"],
                           "prospective_model_performance": False, "exact_0630_replay": False,
                           "actual_archived_prices_used": True,
                           "quote_receipt_evidence": "Local metadata and hashes; no independent timestamp attestation",
                           "interpretation": "Predictions reconstructed after games using locally supported earlier actual price captures. Actual snapshot times differ from 06:30; this is not prospective model performance. The settled sample is too small to establish an edge.",
                           "cohorts": cohorts, "limitations": replay["limitations"],
                           "links": [{"name": "2026 replay report", "url": "https://github.com/drhyphy/ncaa-football-totals/blob/main/model/reports/archived_2026_replay_results.md"},
                                     {"name": "Frozen replay plan", "url": "https://github.com/drhyphy/ncaa-football-totals/blob/main/model/reports/archived_2026_replay_plan.json"}]}
        provenance_path = reports / "ARCHIVED_2026_QUOTES.md"
        if provenance_path.exists():
            read_bytes(provenance_path.name)
            archived_replay["links"].append({"name": "Archived quote provenance", "url": "https://github.com/drhyphy/ncaa-football-totals/blob/main/model/reports/ARCHIVED_2026_QUOTES.md"})
    calibration = None
    if (reports / "calibration_research_results.json").exists():
        result, plan = read("calibration_research_results.json"), read("calibration_research_plan.json")
        read_bytes("CALIBRATION_RESEARCH_PLAN.md")
        read_bytes("calibration_research_results.md")
        if not result.get("plan_sha256") or result["plan_sha256"] != plan.get("plan_sha256") or result.get("parameters") != plan.get("parameters"):
            raise ValueError("Calibration research has a stale or mismatched fixed plan")
        for filename in (PRIMARY, "CALIBRATION_RESEARCH_PLAN.md"):
            if plan.get("input_sha256", {}).get(f"reports/{filename}") != inputs[f"model/reports/{filename}"]:
                raise ValueError("Calibration research source hash is stale: " + filename)
        configurations = ["market_only:raw", "opponent_adjusted_ridge:raw", "market_only:recalibrated",
                          "opponent_adjusted_ridge:recalibrated", "market_only:conditional_variance",
                          "opponent_adjusted_ridge:conditional_variance"]
        if set(plan.get("bases", [])) != {"market_only", "opponent_adjusted_ridge"} or set(plan.get("methods", [])) != {"raw", "recalibrated", "conditional_variance"}:
            raise ValueError("Calibration research must preserve all six fixed configurations")
        periods = result.get("periods", {})
        for period in ("selection_2022_2024", "reused_2025"):
            metrics = periods.get(period, {})
            if set(metrics) != set(configurations):
                raise ValueError("Calibration research must publish all six configurations in both periods")
            if len({row.get("games") for row in metrics.values()}) != 1 or any(not row.get("games") or not math.isfinite(float(row.get("market_nll", math.nan))) for row in metrics.values()):
                raise ValueError("Calibration research configurations must use the same nonempty scored cohort")
        selected = min(configurations, key=lambda name: periods["selection_2022_2024"][name]["market_nll"])
        if result.get("selected_configuration") != selected:
            raise ValueError("Calibration research selection must use the pre-2025 period")
        if result.get("status") != "reused_historical_development_only" or result.get("credible_new_betting_edge") is not False:
            raise ValueError("Calibration publication cannot promote this development experiment as a betting edge")
        calibration = {
            "version": result["version"], "status": result["status"], "plan_sha256": result["plan_sha256"],
            "evaluated_at": result["evaluated_at"], "configuration_count": len(configurations),
            "configurations": configurations, "selected_configuration": selected,
            "selection_period": "2022–2024", "selection_rule": result["selection_rule"],
            "primary_metric": "market_nll", "lower_scores_are_better": True,
            "periods": {name: periods[name] for name in ("selection_2022_2024", "reused_2025")},
            "all_ridge_variants_worse_than_raw_market_2025": all(
                periods["reused_2025"][name]["market_nll"] > periods["reused_2025"]["market_only:raw"]["market_nll"]
                for name in configurations if name.startswith("opponent_adjusted_ridge:")),
            "roi_evaluated": False, "credible_new_betting_edge": False, "live_policy_changed": False,
            "interpretation": "Six fixed probability-score configurations on reused historical data. No ROI test, no new betting-edge claim, and no added live candidate; the four-policy forward protocol is unchanged.",
            "limitations": result["limitations"],
            "links": [{"name": label, "url": "https://github.com/drhyphy/ncaa-football-totals/blob/main/model/reports/" + filename}
                      for label, filename in (("All six calibration configurations", "calibration_research_results.md"),
                                              ("Full probability scores and comparisons", "calibration_research_results.json"),
                                              ("Fixed calibration study plan", "CALIBRATION_RESEARCH_PLAN.md"))],
        }
        if (reports / "CALIBRATION_RESEARCH_AUDIT.md").exists():
            read_bytes("CALIBRATION_RESEARCH_AUDIT.md")
            calibration["links"].append({"name": "Independent calibration audit", "url": "https://github.com/drhyphy/ncaa-football-totals/blob/main/model/reports/CALIBRATION_RESEARCH_AUDIT.md"})
    ordinary = None
    if (reports/'ordinary_model_research_results.json').exists():
        result, plan = read('ordinary_model_research_results.json'), read('ordinary_model_research_plan.json')
        read_bytes('ORDINARY_MODEL_RESEARCH_PLAN.md')
        read_bytes('ordinary_model_research_results.md')
        body = {key: value for key, value in plan.items() if key != 'plan_sha256'}
        plan_hash = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        if plan.get('plan_sha256') != plan_hash or result.get('plan_sha256') != plan_hash:
            raise ValueError('Ordinary result does not match its frozen plan')
        if result.get('source_files_sha256') != plan.get('source_files_sha256') or plan.get('source_files_sha256', {}).get('reports/ORDINARY_MODEL_RESEARCH_PLAN.md') != inputs['model/reports/ORDINARY_MODEL_RESEARCH_PLAN.md']:
            raise ValueError('Ordinary source/protocol hashes are stale')
        if result.get('feature_columns') != plan.get('feature_columns') or len(plan.get('feature_columns', [])) != 58 or len(set(plan['feature_columns'])) != 58:
            raise ValueError('Ordinary study changed its fixed 58-predictor contract')
        if result.get('candidate_order') != list(ORDINARY_CONFIGURATIONS) or plan.get('candidate_order') != list(ORDINARY_CONFIGURATIONS) or result.get('primary_metric') != 'mse' or plan.get('selection_years') != [2021, 2022, 2023, 2024]:
            raise ValueError('Ordinary study configuration or selection contract changed')
        if result.get('status') != 'reused_development_point_prediction_study' or result.get('no_2026_outcomes') is not True or result.get('live_policy_changes') is not False or result.get('credible_executable_edge_established') is not False:
            raise ValueError('Ordinary point-prediction study cannot become live betting evidence')
        selection, last = ordinary_summary(result['selection_2021_2024']), ordinary_summary(result['reused_2025'])
        selected = min(ORDINARY_CONFIGURATIONS, key=lambda name: selection['configurations'][name]['mse'])
        if result.get('selected_on_2021_2024') != selected:
            raise ValueError('Ordinary study selection must use pre-2025 MSE')
        if set(result.get('by_season', {})) != {'2021', '2022', '2023', '2024', '2025'}:
            raise ValueError('Ordinary publication must retain all five years')
        years = {year: ordinary_summary(row, compact=True) for year, row in sorted(result['by_season'].items())}
        sources = {source: ordinary_summary(row, compact=True) for source, row in sorted(result['by_market_source'].items())}
        period_sources = {period: {source: ordinary_summary(row, compact=True) for source, row in sorted(rows.items())}
                          for period, rows in result['by_period_and_market_source'].items()}
        if set(period_sources) != {'selection_2021_2024', 'reused_2025'}:
            raise ValueError('Ordinary source breakdown must retain both periods')
        if sum(row['games'] for row in years.values()) != selection['games']+last['games'] or sum(row['games'] for row in sources.values()) != selection['games']+last['games']:
            raise ValueError('Ordinary source/year coverage does not match scored periods')
        for label, period in (('selection_2021_2024', selection), ('reused_2025', last)):
            selected_years = [row for year, row in years.items() if (year == '2025') == (label == 'reused_2025')]
            if sum(row['games'] for row in selected_years) != period['games'] or sum(row['games'] for row in period_sources[label].values()) != period['games']:
                raise ValueError('Ordinary period coverage changed')
        manifest = plan['feature_manifest']
        ordinary = {'version': result['version'], 'status': result['status'], 'evaluated_at': result['evaluated_at'],
            'plan_sha256': plan_hash, 'prediction_file_sha256': result['prediction_file_sha256'],
            'configuration_count': 4, 'candidate_order': list(ORDINARY_CONFIGURATIONS), 'predictor_count': 58,
            'primary_metric': 'mse', 'selected_on_2021_2024': selected,
            'selection_2021_2024': selection, 'reused_2025': last, 'by_season': years,
            'new_models_higher_mse_than_both_references_in_both_periods': all(
                comparison['mse_difference'] > 0 for period in (selection, last) for comparison in period['comparisons']),
            'by_market_source': sources, 'by_market_source_scope': result['by_market_source_scope'],
            'by_period_and_market_source': period_sources,
            'feature_provenance': {key: manifest.get(key) for key in ('version', 'feature_contract_sha256', 'feature_fingerprint', 'cache_data_fingerprint', 'cache_sha256', 'history_sha256', 'counts')},
            'feature_contract': manifest['feature_contract'], 'no_2026_outcomes': True,
            'live_policy_changes': False, 'probabilities_evaluated': False, 'roi_evaluated': False,
            'credible_executable_edge_established': False, 'limitations': result['limitations'],
            'links': [{'name': label, 'url': 'https://github.com/drhyphy/ncaa-football-totals/blob/main/model/reports/'+filename}
                      for label, filename in (('All four ordinary-stat configurations', 'ordinary_model_research_results.md'),
                                              ('Annual/source scores and paired intervals', 'ordinary_model_research_results.json'),
                                              ('Fixed ordinary-stat research plan', 'ORDINARY_MODEL_RESEARCH_PLAN.md'))]}
        for filename, label in (('ORDINARY_MODEL_RESEARCH_AUDIT.md', 'Independent ordinary-stat study audit'),
                                ('ORDINARY_MODEL_PREFIT_AUDIT.md', 'Independent audit before fitting'),
                                ('ORDINARY_MODEL_RESULTS_AUDIT.md', 'Independent ordinary-stat results audit'),
                                ('RICH_FEATURE_REUSE_AUDIT.md', 'Feature reuse and timing audit')):
            if (reports/filename).exists():
                read_bytes(filename)
                ordinary['links'].append({'name': label, 'url': 'https://github.com/drhyphy/ncaa-football-totals/blob/main/model/reports/'+filename})
    direct = None
    if (reports/'direct_probability_results.json').exists():
        direct = direct_publication(read, read_bytes, inputs)
    protocol = None
    protocol_path = reports / "PROSPECTIVE_EVALUATION_PROTOCOL.md"
    if protocol_path.exists():
        read_bytes(protocol_path.name)
        protocol = {"report": "model/reports/PROSPECTIVE_EVALUATION_PROTOCOL.md",
                    "url": "https://github.com/drhyphy/ncaa-football-totals/blob/main/model/reports/PROSPECTIVE_EVALUATION_PROTOCOL.md",
                    "sha256": inputs["model/reports/PROSPECTIVE_EVALUATION_PROTOCOL.md"]}
    ridge = pooled["opponent_adjusted_ridge"]
    last_ridge = by_season["2025"]["opponent_adjusted_ridge"]
    conclusion = (f"No high-confidence profitable edge established. Pooled ridge ROI is {ridge['roi_display']} "
                  f"with a descriptive interval of {ridge['roi_interval_display']}; "
                  f"2025 ridge ROI is {last_ridge['roi_display']}. Full annual results are retained below.")
    bundle = {
        "schema_version": 2,
        "status": "repaired_retrospective_development_only",
        "primary_report": PRIMARY,
        "data_fingerprint": primary["data_fingerprint"],
        "conclusion": conclusion,
        "untouched_test": False,
        "historical_prices_observed": False,
        "quote_or_closing_times_verified": False,
        "price_assumption": -110,
        "price_assumption_scope": "Primary 2019-2025 development evidence; the separate 2026 replay uses archived offered prices",
        "reports": current_reports,
        "quarantined_reports": quarantine,
        "weather_shadow": weather,
        "archived_2026_scoring_replay": archived_replay,
        "calibration_research": calibration,
        "ordinary_model_research": ordinary,
        "direct_probability_research": direct,
        "prospective_evaluation_protocol": protocol,
        "limitations": [
            "All 2019–2025 periods have been reused in development; no untouched historical test is claimed.",
            "Verified bookmaker pregame role does not certify exact closing time, 06:30 availability, execution, or payout.",
            "Bootstrap intervals are descriptive and do not correct for the complete research selection process.",
            "Prospective immutable forecasts and offered quotes must establish live performance separately.",
        ],
        "source_report_sha256": inputs,
        "generator": "scripts/publish_research.py",
    }
    return {
        root / "site/data/research.json": json.dumps(bundle, indent=2, allow_nan=False) + "\n",
        reports / "opponent_adjusted_development.md": render_opponent_report(primary),
    }


def main(root, check=False):
    outputs = build_outputs(root)
    stale = []
    for path, content in outputs.items():
        if check:
            if not path.exists() or path.read_text() != content:
                stale.append(str(path))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            print(f"Published {path}")
    if stale:
        raise SystemExit("Stale research publication; run scripts/publish_research.py: " + ", ".join(stale))
    if check:
        print("Research publication matches its current input reports")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check", action="store_true", help="Fail if the public bundle or rendered report is stale")
    args = parser.parse_args()
    main(args.root, args.check)
