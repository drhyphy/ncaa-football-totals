"""Publish a small, whitelisted collection status; never fetch data or change picks."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

SCHEMA = "weather-revision-status-v1"
PILOT_START = datetime(2026, 9, 9, 3, tzinfo=timezone.utc)
PILOT_END = datetime(2026, 9, 16, 3, tzinfo=timezone.utc)
PUBLIC_PATH = Path("site/data/weather-revisions.json")
ARCHIVE_PATH = Path("model/data/runtime/weather_revisions")
SCHEDULE_UTC = ["01:17", "07:17", "13:17", "19:17"]
COUNT_FIELDS = ("cohort_games", "weather_available_games", "two_book_games", "paired_games", "failed_requests")
CAPTURE_SCHEMA = "weather-revision-capture-v1"
CAPTURE_STATUSES = {"ok", "partial", "failed", "no_games", "outside_pilot"}
ARCHIVE_URL = "https://github.com/drhyphy/ncaa-football-totals/tree/main/model/data/runtime/weather_revisions"
PLAN_URL = "https://github.com/drhyphy/ncaa-football-totals/blob/main/model/reports/WEATHER_REVISION_CAPTURE_PROTOCOL.md"


def timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo is not None else None
    except ValueError:
        return None


def iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("UTC-aware time required")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def phase(now: datetime) -> str:
    iso(now)
    return "scheduled" if now < PILOT_START else "active" if now < PILOT_END else "ended"


def read_public(root: Path) -> dict:
    try:
        data = json.loads((root / PUBLIC_PATH).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def decision(root: Path, now: datetime) -> dict:
    current_phase = phase(now)
    previous = read_public(root)
    # One final status transition after expiry, then no network or recurring commits.
    return {"collect": current_phase == "active", "publish": current_phase == "active" or
            previous.get("schema_version") != SCHEMA or previous.get("phase") != current_phase}


def emit(name: str, value: bool) -> None:
    emit_text(name, "true" if value else "false")


def emit_text(name: str, value: str) -> None:
    line = f"{name}={value}"
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as handle:
            handle.write(line + "\n")
    print(line)


def write_public(root: Path, value: dict) -> None:
    path = root / PUBLIC_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def manifest_summary(data: dict, body: bytes, now: datetime) -> dict:
    """Validate aggregate metadata without copying payloads, URLs or exception text."""
    if not isinstance(data, dict) or data.get("schema_version") != CAPTURE_SCHEMA or data.get("status") not in CAPTURE_STATUSES:
        raise ValueError("Invalid capture manifest schema or status")
    started = timestamp(data.get("capture_started_at"))
    completed = timestamp(data.get("capture_completed_at"))
    if started is None or completed is None or completed < started or (completed - now).total_seconds() > 300:
        raise ValueError("Invalid capture receipt times")
    if data["status"] != "outside_pilot" and not PILOT_START <= started < PILOT_END:
        raise ValueError("Capture start is outside the pilot")
    counts = data.get("counts")
    if not isinstance(counts, dict) or any(type(counts.get(key)) is not int or counts[key] < 0 for key in COUNT_FIELDS):
        raise ValueError("Invalid capture counts")
    counts = {key: counts[key] for key in COUNT_FIELDS}
    if counts["weather_available_games"] > counts["cohort_games"] or counts["two_book_games"] > counts["cohort_games"] or counts["paired_games"] > counts["weather_available_games"]:
        raise ValueError("Capture counts do not conserve the cohort")
    paired_two = data["counts"].get("paired_two_book_games")
    if paired_two is not None and (type(paired_two) is not int or not 0 <= paired_two <= min(counts["paired_games"], counts["two_book_games"])):
        raise ValueError("Invalid weather and two-book intersection count")
    counts["paired_two_book_games"] = paired_two
    total = data["counts"].get("total_requests")
    if total is not None and (type(total) is not int or total < counts["failed_requests"]):
        raise ValueError("Invalid total request count")
    counts["total_requests"] = total
    if data["status"] in {"no_games", "outside_pilot"} and any(counts[key] for key in COUNT_FIELDS[:4]):
        raise ValueError("Non-collection status contains game observations")
    requested = timestamp(data.get("requested_run"))
    if data.get("requested_run") is not None and requested is None:
        raise ValueError("Invalid requested initialization time")
    if requested is not None and requested > started:
        raise ValueError("Requested initialization follows capture start")
    return {"capture_started_at": iso(started), "capture_completed_at": iso(completed),
            "requested_run": iso(requested) if requested else None, "status": data["status"],
            "counts": counts, "manifest_sha256": hashlib.sha256(body).hexdigest()}


def build_status(root: Path, now: datetime, outcome: str = "skipped", attempt_started_at: datetime | None = None,
                 expected_run_id: str | None = None, expected_run_attempt: str | None = None) -> tuple[dict, bool]:
    current_phase = phase(now)
    manifests, matching_attempts, invalid = [], [], 0
    for path in sorted((root / ARCHIVE_PATH / "runs").glob("*.json")):
        try:
            body = path.read_bytes()
            data = json.loads(body)
            summary = manifest_summary(data, body, now)
            manifests.append(summary)
            if attempt_started_at is not None and timestamp(summary["capture_started_at"]) >= attempt_started_at and (
                expected_run_id is None or str(data.get("run_id")) == expected_run_id) and (
                expected_run_attempt is None or str(data.get("run_attempt")) == expected_run_attempt):
                matching_attempts.append(summary)
        except (OSError, ValueError, TypeError):
            invalid += 1
    manifests.sort(key=lambda row: (timestamp(row["capture_started_at"]), timestamp(row["capture_completed_at"]), row["manifest_sha256"]))
    latest = manifests[-1] if manifests else None
    totals = {key: sum(row["counts"][key] for row in manifests) for key in COUNT_FIELDS}
    totals["total_requests"] = sum(row["counts"]["total_requests"] for row in manifests) if manifests and all(row["counts"]["total_requests"] is not None for row in manifests) else None
    totals["paired_two_book_games"] = sum(row["counts"]["paired_two_book_games"] for row in manifests) if manifests and all(row["counts"]["paired_two_book_games"] is not None for row in manifests) else None
    expected_attempt_failed = attempt_started_at is not None and (outcome != "success" or not matching_attempts or
        any(row["status"] in {"partial", "failed"} for row in matching_attempts))
    failed = outcome in {"failure", "cancelled"} or expected_attempt_failed or (current_phase == "active" and (invalid > 0 or
             (latest is not None and latest["status"] in {"partial", "failed"}) or
             (outcome == "success" and latest is None)))
    if current_phase == "scheduled":
        health = "scheduled"
        message = "The seven-day collection pilot has not started. No forecast revision performance has been evaluated."
    elif current_phase == "ended":
        health = "ended"
        message = "The seven-day pilot window has ended. Automatic collection is stopped; archived inputs remain available for a separately specified future study."
    elif failed:
        health = "attention"
        message = "The latest collection needs attention. Available archives and failure counts are retained; missing receipts are not filled retrospectively."
    elif latest is None:
        health = "awaiting_first_capture"
        message = "The pilot is active and awaiting its first archived collection. No new selections or profitability evidence are produced."
    elif (now - timestamp(latest["capture_completed_at"])).total_seconds() > 8 * 3600:
        health = "stale"
        message = "No completed collection receipt in the past eight hours. Scheduled jobs can be delayed or missed."
    else:
        health = "current"
        message = "Collection receipts are current. These archives collect prospective research inputs; coverage does not establish a betting edge."
    value = {"schema_version": SCHEMA, "generated_at": iso(now), "phase": current_phase,
             "status": health, "message": message, "pilot_start": iso(PILOT_START), "pilot_end": iso(PILOT_END),
             "schedule_utc": SCHEDULE_UTC, "collection_only": True, "performance_evaluated": False,
             "active_policy_changed": False, "archived_runs": len(manifests), "invalid_manifests": invalid,
             "partial_or_failed_runs": sum(row["status"] in {"partial", "failed"} for row in manifests),
             "latest": latest, "game_observation_totals": totals,
             "last_workflow_attempt": {"started_at": iso(attempt_started_at), "outcome": outcome,
                                       "matching_manifest": bool(matching_attempts)} if attempt_started_at else None,
             "counting_note": "Totals count repeated game observations across collection runs, not distinct games or bets.",
             "pairing_note": "Paired games have weather and at least one same-book Over/Under quote pair. The two-book count independently counts games with both books; paired_two_book_games is their weather intersection when supplied.",
             "timing_note": "Actual response receipt times are retained. Requested initialization and source contracts do not independently certify original model vintage or pre-receipt availability.",
             "links": [{"name": "Public collection archives", "url": ARCHIVE_URL}, {"name": "Collection design and limitations", "url": PLAN_URL}]}
    return value, failed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["decision", "publish"])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--collector-outcome", choices=["success", "failure", "skipped", "cancelled", ""], default="skipped")
    parser.add_argument("--attempt-started-at", default="")
    parser.add_argument("--expected-run-id", default=os.getenv("GITHUB_RUN_ID", ""))
    parser.add_argument("--expected-run-attempt", default=os.getenv("GITHUB_RUN_ATTEMPT", ""))
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    if args.command == "decision":
        values = decision(args.root, now)
        for name, value in values.items():
            emit(name, value)
        emit_text("attempt_started_at", iso(now) if values["collect"] else "")
    else:
        attempt = timestamp(args.attempt_started_at) if args.attempt_started_at else None
        if args.attempt_started_at and attempt is None:
            parser.error("attempt-started-at must be a timezone-aware timestamp")
        status, failed = build_status(args.root, now, args.collector_outcome, attempt,
                                     args.expected_run_id or None, args.expected_run_attempt or None)
        write_public(args.root, status)
        emit("collection_failed", failed)


if __name__ == "__main__":
    main()
