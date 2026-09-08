from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .backtest import run_backtest
from .config import load_settings
from .grading import grade_ledger
from .public_ensemble import run_public_backtest
from .scoring import run_snapshot
from .sources import DataClient
from .totals_backtest import run_totals_backtest
from .totals_grading import grade_totals_ledger
from .totals_scoring import run_totals_snapshot
from .timing import run_timing_report


def _print(payload: Any) -> None:
    print(json.dumps(payload, indent=2, default=str))


def bootstrap(refresh: bool = False, include_history: bool = True) -> dict[str, Any]:
    settings = load_settings()
    client = DataClient(settings)
    seasons = range(settings.historical_start_season, settings.season + 1) if include_history else [settings.season]
    downloaded: dict[int, list[str]] = {}
    for season in seasons:
        downloaded[int(season)] = [str(path) for path in client.archive_season(int(season), refresh).values()]
    ratings = client.ratings(settings.season, refresh)
    return {"seasons": downloaded, "ratings": str(ratings)}


def totals_bootstrap(refresh: bool = False) -> dict[str, Any]:
    settings = load_settings()
    client = DataClient(settings)
    downloaded: dict[int, list[str]] = {}
    for season in range(settings.historical_start_season, settings.season):
        base = client.archive_season(season, refresh)
        totals = client.archive_totals_season(season, refresh)
        public = client.archive_public_models_season(season, refresh, include_fpi=True)
        downloaded[season] = [str(path) for path in {**base, **totals, **public}.values()]
    client.archive_season(settings.season, refresh=False)
    client.archive_public_models_season(settings.season, refresh=refresh, include_fpi=False)
    return {"seasons": downloaded, "current_schedule": settings.season}


def main() -> None:
    parser = argparse.ArgumentParser(description="NCAA football market research workflow")
    sub = parser.add_subparsers(dest="command", required=True)
    bootstrap_parser = sub.add_parser("bootstrap", help="Download public schedule, closing-line, and FPI files")
    bootstrap_parser.add_argument("--refresh", action="store_true")
    bootstrap_parser.add_argument("--current-only", action="store_true")
    sub.add_parser("backtest", help="Run walk-forward probability backtest and fit live artifact")
    snapshot_parser = sub.add_parser("snapshot", help="Collect current odds and append prediction ledger")
    snapshot_parser.add_argument("--odds-json", type=Path, help="Use an existing The Odds API JSON response")
    snapshot_parser.add_argument("--no-refresh", action="store_true")
    sub.add_parser("grade", help="Append grades for completed prediction rows")
    sub.add_parser("timing-report", help="Compare calibration, CLV, and ROI by daily entry horizon")
    daily_parser = sub.add_parser("run-daily", help="Refresh current public data, score odds, then grade")
    daily_parser.add_argument("--refit", action="store_true", help="Re-download history and refit the model artifact")
    totals_bootstrap_parser = sub.add_parser("totals-bootstrap", help="Download score, drive, and closing-total history")
    totals_bootstrap_parser.add_argument("--refresh", action="store_true")
    sub.add_parser("totals-backtest", help="Run walk-forward totals backtest and fit the live ensemble")
    sub.add_parser(
        "totals-public-backtest",
        help="Run the point-in-time public-rating superensemble backtest",
    )
    totals_snapshot_parser = sub.add_parser("totals-snapshot", help="Score current totals and append every candidate")
    totals_snapshot_parser.add_argument("--odds-json", type=Path, help="Use an existing The Odds API JSON response")
    totals_snapshot_parser.add_argument("--no-refresh", action="store_true")
    sub.add_parser("totals-grade", help="Grade completed totals ledger rows")
    totals_daily_parser = sub.add_parser("totals-run-daily", help="Refresh, score, and grade the totals workflow")
    totals_daily_parser.add_argument("--refit", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    client = DataClient(settings)
    if args.command == "bootstrap":
        _print(bootstrap(args.refresh, not args.current_only))
    elif args.command == "totals-bootstrap":
        _print(totals_bootstrap(args.refresh))
    elif args.command == "totals-backtest":
        _print(run_totals_backtest(settings))
    elif args.command == "totals-public-backtest":
        _print(run_public_backtest(settings))
    elif args.command == "totals-snapshot":
        client.archive_season(settings.season, refresh=False)
        odds_path = args.odds_json or client.current_odds(refresh=not args.no_refresh)[0]
        _print(run_totals_snapshot(settings, odds_path))
    elif args.command == "totals-grade":
        client.archive_season(settings.season, refresh=True)
        _print(grade_totals_ledger(settings))
    elif args.command == "totals-run-daily":
        if args.refit or not (settings.models_dir / "totals_artifact_latest.json").exists():
            totals_bootstrap(refresh=args.refit)
            run_totals_backtest(settings)
        else:
            client.archive_season(settings.season, refresh=True)
        odds_path, quota = client.current_odds(refresh=True)
        result = run_totals_snapshot(settings, odds_path)
        result["grade"] = grade_totals_ledger(settings)
        result["quota_headers"] = quota
        _print(result)
    elif args.command == "backtest":
        _print(run_backtest(settings))
    elif args.command == "snapshot":
        client.archive_season(settings.season, refresh=False)
        client.ratings(settings.season, refresh=False)
        odds_path = args.odds_json or client.current_odds(refresh=not args.no_refresh)[0]
        schedule_refresh = client.ensure_schedule_for_odds(odds_path)
        result = run_snapshot(settings, odds_path)
        result["schedule_refresh"] = schedule_refresh
        _print(result)
    elif args.command == "grade":
        client.archive_season(settings.season, refresh=True)
        _print(grade_ledger(settings))
    elif args.command == "timing-report":
        _print(run_timing_report(settings))
    elif args.command == "run-daily":
        if args.refit or not (settings.models_dir / "market_residual_latest.json").exists():
            bootstrap(refresh=args.refit, include_history=True)
            run_backtest(settings)
        else:
            client.archive_season(settings.season, refresh=True)
            client.ratings(settings.season, refresh=True)
        odds_path, quota = client.current_odds(refresh=True)
        schedule_refresh = client.ensure_schedule_for_odds(odds_path)
        result = run_snapshot(settings, odds_path)
        result["grade"] = grade_ledger(settings)
        result["timing"] = run_timing_report(settings)
        result["schedule_refresh"] = schedule_refresh
        result["quota_headers"] = quota
        _print(result)


if __name__ == "__main__":
    main()
