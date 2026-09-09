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

    def read(name):
        path = reports / name
        content = path.read_bytes()
        inputs[f"model/reports/{name}"] = hashlib.sha256(content).hexdigest()
        return json.loads(content)

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
        "reports": current_reports,
        "quarantined_reports": quarantine,
        "weather_shadow": weather,
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
