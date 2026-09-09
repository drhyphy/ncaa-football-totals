"""Observe ACC report metadata and subsequent prices; never fit or publish bets."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

import requests

from .availability_archive import capture_current
from .revision_archive import ArchiveClient, digest_json, immutable_json, timestamp
from .sources import load_dotenv
from .teams import normalize_team
from .weather_revision_collector import SCOREBOARD, SUMMARY, collect_quotes, parse_cohort

VERSION = "acc-availability-collector-v1"
START = datetime(2026, 9, 9, 8, tzinfo=timezone.utc)
END = datetime(2026, 9, 16, 7, tzinfo=timezone.utc)
ZONE = ZoneInfo("America/New_York")
PROTOCOL = "reports/ACC_AVAILABILITY_CAPTURE_PROTOCOL.md"
MAX_GAMES = 20


def as_time(value):
    result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Aware receipt required")
    return result.astimezone(timezone.utc)


def good(envelope):
    receipt = envelope["receipt"]
    return (receipt.get("status_code") == 200 and receipt.get("body_complete", True) is True
            and not any(receipt.get(k) for k in ("transport_error", "capture_error", "parse_error", "http_error")))


def official_games(payload, start):
    """Use exact identity and variants supplied by the same official inventory."""
    games, exclusions, enumerated = parse_cohort(payload, start)
    candidates = {}
    for event in payload["events"]:
        if isinstance(event, dict):
            candidates.setdefault(str(event.get("id")), []).append(event)
    kept = []
    for game in games:
        events = candidates.get(game["game_id"], [])
        if len(events) != 1:
            exclusions.append({"game_id": game["game_id"], "reason": "duplicate_official_inventory_id"})
            continue
        competitors = events[0]["competitions"][0]["competitors"]
        row = dict(game)
        for competitor in competitors:
            side, team = competitor["homeAway"], competitor["team"]
            row[side + "_source_names"] = {k: v for k in ("location", "displayName", "shortDisplayName", "abbreviation")
                                           if isinstance(v := team.get(k), str) and v.strip()}
        kept.append(row)
    return kept, exclusions, enumerated


def match_reports(payload, games):
    """Unknown completion/rows remain unknown; Pending still gets price coverage."""
    if not isinstance(payload, dict):
        raise ValueError("Current report root must be an object")
    records = []
    for key, report in sorted(payload.items()):
        row = {"source_key": str(key), "state": "metadata_invalid", "game_id": None,
               "completion_verified": False, "failure": None}
        try:
            if not isinstance(report, dict):
                raise ValueError("invalid_report_object")
            teams, footer = report["games"], report["footer"]
            if not isinstance(teams, list) or len(teams) != 2 or not isinstance(footer, dict):
                raise ValueError("invalid_team_blocks_or_footer")
            names = [team["teamName"] for team in teams]
            if any(not isinstance(name, str) or not name.strip() for name in names):
                raise ValueError("invalid_team_names")
            pair = [normalize_team(name) for name in names]
            if len(set(pair)) != 2 or any(not isinstance(team.get("rows"), list) for team in teams):
                raise ValueError("duplicate_teams_or_invalid_rows")
            phase = report.get("ReportType")
            if not isinstance(phase, str) or not phase.strip():
                raise ValueError("missing_report_type")
            row.update(state="pending" if phase == "Report Pending" else "nonpending_completion_unverified",
                       report_type=phase, source_teams=names, source_row_items=[len(team["rows"]) for team in teams],
                       source_date=footer.get("date"), source_time=footer.get("time"),
                       source_timezone=report.get("conferenceTimeZone"),
                       source_publish_date=report.get("publishDate"), source_posted_time=report.get("postedTime"))
            matches = []
            for game in games:
                home = {normalize_team(v) for v in game["home_source_names"].values()}
                away = {normalize_team(v) for v in game["away_source_names"].values()}
                if (pair[0] in home and pair[1] in away) or (pair[1] in home and pair[0] in away):
                    matches.append(game)
            if len(matches) != 1:
                raise ValueError("missing_or_ambiguous_official_pair")
            game = matches[0]
            kickoff = as_time(game["kickoff"]).astimezone(ZONE)
            if (row["source_timezone"] != "ET" or row["source_date"] != kickoff.strftime("%Y-%m-%d")
                    or row["source_time"] != kickoff.strftime("%H:%M:%S")):
                raise ValueError("source_clock_and_official_kickoff_disagree")
            row.update(game_id=game["game_id"], context_id=digest_json(game),
                       matching_names={side: game[side + "_source_names"] for side in ("home", "away")},
                       clock_interpretation="Literal ET interpreted as America/New_York for matchup reconciliation only")
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            row["failure"] = str(exc) if isinstance(exc, ValueError) else "invalid_report_metadata"
        records.append(row)
    # Compatible source records share one price target. Their opaque keys and
    # unverified phase meanings remain separate; no semantic revision is inferred.
    targets = [game for game in games if any(row["game_id"] == game["game_id"] for row in records)]
    targets.sort(key=lambda g: (as_time(g["kickoff"]), int(g["game_id"])))
    excluded = {game["game_id"] for game in targets[MAX_GAMES:]}
    for row in records:
        if row["game_id"] in excluded:
            row["failure"], row["game_id"] = "frozen_twenty_game_cap", None
    return records, targets[:MAX_GAMES]


def context_matches(payload, game):
    try:
        header = payload["header"]
        competitions = header["competitions"]
        if str(header["id"]) != game["game_id"] or len(competitions) != 1:
            return False
        competition = competitions[0]
        if (str(competition["id"]) != game["game_id"] or as_time(competition["date"]) != as_time(game["kickoff"])
                or competition["status"]["type"]["state"] != "pre" or competition.get("dateValid") is False):
            return False
        people = competition["competitors"]
        return (len(people) == 2 and {p["homeAway"] for p in people} == {"home", "away"}
                and all(str(p["team"]["id"]) == game[p["homeAway"] + "_id"] for p in people))
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def pair_is_after(quote, report_receipt, context_receipt, kickoff):
    try:
        source, context = as_time(report_receipt["received_at"]), as_time(context_receipt["received_at"])
        source_request, context_request = as_time(report_receipt["requested_at"]), as_time(context_receipt["requested_at"])
        requested, received, starts = map(as_time, (quote["requested_at"], quote["observed_at"], kickoff))
        return source_request <= source <= context_request <= context <= requested <= received < starts
    except (KeyError, TypeError, ValueError):
        return False


class PriceArchive(ArchiveClient):
    def __init__(self, root, archive, timeout=30., session=None):
        if session is None:
            session = requests.Session()
            session.trust_env = False
        super().__init__(root, archive, timeout, session)

    def fetch(self, url, params=None, secret_params=None, purpose="research"):
        if purpose == "fresh_totals_after_weather":
            purpose = "fresh_totals_after_acc_availability"
        return super().fetch(url, params, secret_params, purpose)


def collect(root, *, now=None, client=None, source_capture=None):
    root = Path(root).resolve()
    start = as_time(now or timestamp())
    archive = root / "data/runtime/availability"
    invocation = os.getenv("GITHUB_RUN_ID") or "local-" + start.strftime("%Y%m%dT%H%M%S%fZ")
    attempt = os.getenv("GITHUB_RUN_ATTEMPT", "1")
    if not invocation or not attempt or not all(c.isalnum() or c in "-_" for c in invocation + attempt):
        raise ValueError("Invalid invocation identity")
    path = archive / "runs" / f"{invocation}-{attempt}.json"
    if path.exists():
        raise ValueError("Invocation already archived")
    manifest = {"schema_version": VERSION, "protocol": PROTOCOL, "run_id": invocation, "run_attempt": attempt,
                "trigger": os.getenv("GITHUB_EVENT_NAME", "manual_local"), "capture_started_at": start.isoformat(),
                "capture_completed_at": None, "status": "failed", "reports": [], "rows": [], "failures": [],
                "receipts": [], "counts": {}, "models_fitted": 0, "verified_completed_reports": 0}
    owns_client = client is None
    client = client or PriceArchive(root, archive)
    source_envelopes = []
    try:
        if not START <= start < END:
            manifest["status"] = "outside_pilot"
            return manifest
        sources = [PROTOCOL, "ncaaf_model/availability_collector.py", "ncaaf_model/availability_archive.py",
                   "ncaaf_model/revision_archive.py", "ncaaf_model/weather_revision_collector.py", "ncaaf_model/teams.py"]
        manifest["source_hashes"] = {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in sources}
        manifest["git_commit"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
        manifest["workflow_commit"] = os.getenv("GITHUB_SHA")
        workflow = root.parent / ".github/workflows/availability.yml"
        manifest["workflow_sha256"] = hashlib.sha256(workflow.read_bytes()).hexdigest() if workflow.exists() else None
        inventory = client.fetch(SCOREBOARD, {"groups": 80, "limit": 1000,
            "dates": f"{start:%Y%m%d}-{start + timedelta(days=7):%Y%m%d}"}, purpose="official_availability_inventory")
        if not good(inventory):
            raise ValueError("official_inventory_failed")
        games, exclusions, enumerated = official_games(inventory["payload"], start)
        cohort_path = archive / "cohorts" / f"{invocation}-{attempt}.json"
        immutable_json(cohort_path, {"inventory_receipt": inventory["receipt"]["receipt_path"], "games": games,
                                   "exclusions": exclusions, "enumerated_games": enumerated, "frozen_at": timestamp()})
        manifest.update(cohort_path=cohort_path.relative_to(root).as_posix(),
                        cohort_sha256=hashlib.sha256(cohort_path.read_bytes()).hexdigest(), official_games=len(games),
                        inventory_exclusions=exclusions)
        source = (source_capture or capture_current)(root, archive=archive)
        source_envelopes = source["envelopes"]
        manifest["source_status"] = source["status"]
        current = source.get("current")
        if (source["status"] != "captured" or not current or not good(current)
                or current["receipt"].get("body_complete") is not True or not isinstance(current.get("payload"), dict)):
            raise ValueError("current_acc_source_unavailable")
        manifest["current_receipt"] = current["receipt"]["receipt_path"]
        records, targets = match_reports(current["payload"], games)
        manifest["reports"] = records
        rows = [{**game, "quotes": [], "pairs": [], "missingness": []} for game in targets]
        manifest["rows"] = rows
        def recheck(row):
            response = client.fetch(SUMMARY, {"event": row["game_id"]}, purpose="official_context_after_availability")
            row["context_receipt"] = response["receipt"]
            row["context_verified"] = good(response) and context_matches(response["payload"], row)
            if not row["context_verified"]:
                row["missingness"].append("official_context_changed_or_recheck_failed")
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(recheck, rows))
        eligible = [row for row in rows if row["context_verified"]]
        quotes, failures = [], []
        if eligible:
            load_dotenv(root.parents[1] / ".env")
            quotes, failures = collect_quotes(client, eligible, os.getenv("ODDS_API_IO_KEY", ""), start)
        manifest["quote_diagnostics"] = failures
        for row in rows:
            row["quotes"] = [quote for quote in quotes if quote["game_id"] == row["game_id"]]
            for quote in row["quotes"]:
                if pair_is_after(quote, current["receipt"], row["context_receipt"], row["kickoff"]):
                    row["pairs"].append({"quote_id": quote["quote_id"], "report_receipt": manifest["current_receipt"],
                                         "quote_receipt": quote["receipt_path"], "sportsbook": quote["sportsbook"],
                                         "report_to_quote_seconds": (as_time(quote["observed_at"]) - as_time(current["receipt"]["received_at"])).total_seconds()})
            if not row["pairs"]:
                row["missingness"].append("no_verified_subsequent_price_pair")
        covered = {row["game_id"] for row in rows if row["pairs"]}
        # The shared parser diagnoses both books even when just one is selected.
        # A missing/ambiguous peer does not negate an actually observed valid pair.
        failures = [failure for failure in failures if not (
            failure.get("reason") == "missing_or_duplicate_main_totals" and failure.get("game_id") in covered)]
        manifest["failures"].extend(failures)
        manifest["status"] = "partial" if failures or exclusions or any(r["failure"] for r in records) or any(r["missingness"] for r in rows) else "captured"
    except (OSError, AttributeError, KeyError, TypeError, ValueError, subprocess.CalledProcessError) as exc:
        manifest["failures"].append({"reason": "capture_failed", "exception_class": type(exc).__name__})
    finally:
        manifest["capture_completed_at"] = timestamp()
        receipts = list(client.receipts) + [envelope["receipt"] for envelope in source_envelopes]
        manifest["receipts"] = [r["receipt_path"] for r in receipts]
        rows, reports = manifest["rows"], manifest["reports"]
        manifest["counts"] = {"source_reports": len(reports), "pending_reports": sum(r["state"] == "pending" for r in reports),
            "nonpending_unverified_reports": sum(r["state"] == "nonpending_completion_unverified" for r in reports),
            "matched_games": len(rows), "context_verified_games": sum(r.get("context_verified", False) for r in rows),
            "paired_games": sum(bool(r["pairs"]) for r in rows),
            "paired_two_book_games": sum(len({p["sportsbook"] for p in r["pairs"]}) == 2 for r in rows),
            "total_requests": len(receipts)}
        immutable_json(path, manifest)
        if owns_client:
            client.session.close()
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    result = collect(args.root)
    print(json.dumps({key: result[key] for key in ("status", "run_id", "counts")}, allow_nan=False))
    if result["status"] in {"failed", "partial"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
