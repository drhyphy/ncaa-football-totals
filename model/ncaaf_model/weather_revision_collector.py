"""Bounded prospective weather/price archive. Does not evaluate or publish bets."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess

from .revision_archive import ArchiveClient, digest_json, immutable_json, load_envelope, timestamp
from .sources import load_dotenv
from .teams import normalize_team
from .weather_revision_weather import (select_run, single_run_request, previous_day2_request,
    parse_single_run, parse_previous_day2, maturity_time)

VERSION = "weather-revision-collector-v1"
START = datetime(2026, 9, 9, 3, tzinfo=timezone.utc)
END = datetime(2026, 9, 16, 3, tzinfo=timezone.utc)
SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"
SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/summary"
VENUES = "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/venues/"
ODDS = "https://api.odds-api.io/v3"
BOOKS = {"DraftKings": "draftkings", "FanDuel": "fanduel"}
CATALOG_SHA = "eec79812c7faef8d70b21d9ad4018b3d2a71074b27b72508358d71ae56519ce7"
MAX_GAMES = 150
WORKERS = 4
QUOTA_RESERVE = 20


def _time(value):
    result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Aware timestamp required")
    return result.astimezone(timezone.utc)


def _stamp(value):
    return _time(value).isoformat().replace("+00:00", "Z")


def _ok(envelope):
    r = envelope["receipt"]
    return r["status_code"] == 200 and not r["transport_error"]


def _identity(competition):
    people = competition.get("competitors", [])
    if len(people) != 2 or {p.get("homeAway") for p in people} != {"home", "away"}:
        raise ValueError("ambiguous_competitors")
    result = {}
    for p in people:
        side, team = p["homeAway"], p["team"]
        identity = str(team["id"])
        if not identity.isdigit() or int(identity) <= 0 or not team.get("displayName"):
            raise ValueError("invalid_team_identity")
        result[side + "_id"] = identity
        result[side + "_team"] = team["displayName"]
    if result["home_id"] == result["away_id"]:
        raise ValueError("duplicate_team_identity")
    return result


def parse_cohort(payload, start):
    """Select from official inventory before observing weather/book availability."""
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise ValueError("invalid_official_inventory")
    rows, exclusions, candidates = [], [], {}
    for event in payload["events"]:
        if not isinstance(event, dict):
            exclusions.append({"game_id": None, "reason": "invalid_official_event"})
            continue
        game_id = str(event.get("id", ""))
        try:
            if not game_id.isdigit() or int(game_id) <= 0:
                raise ValueError("invalid_game_id")
            comps = event.get("competitions", [])
            if len(comps) != 1 or str(comps[0].get("id")) != game_id:
                raise ValueError("ambiguous_competition")
            c = comps[0]
            kickoff = _time(c["date"])
            if not start < kickoff <= start + timedelta(days=7):
                continue
            if c.get("status", {}).get("type", {}).get("state") != "pre" or c.get("dateValid") is False:
                raise ValueError("not_confirmed_pregame")
            row = {"game_id": game_id, "kickoff": _stamp(kickoff), **_identity(c),
                   "inventory_venue_id": str(c.get("venue", {}).get("id", "")),
                   "inventory_neutral_site": c.get("neutralSite")}
            candidates.setdefault(game_id, []).append(row)
        except (AttributeError, KeyError, TypeError, ValueError):
            exclusions.append({"game_id": game_id, "reason": "invalid_or_unconfirmed_official_event"})
    for game_id, matches in candidates.items():
        if len(matches) != 1:
            exclusions.append({"game_id": game_id, "reason": "duplicate_official_event"})
        else:
            rows.append(matches[0])
    rows.sort(key=lambda g: (_time(g["kickoff"]), int(g["game_id"])))
    for row in rows[MAX_GAMES:]:
        exclusions.append({"game_id": row["game_id"], "reason": "frozen_150_game_cap"})
    return rows[:MAX_GAMES], exclusions, len(rows)


def summary_identity(payload, game):
    if not isinstance(payload, dict):
        raise ValueError("invalid_summary_payload")
    header = payload.get("header", {})
    comps = header.get("competitions", [])
    if str(header.get("id")) != game["game_id"] or len(comps) != 1 or str(comps[0].get("id")) != game["game_id"]:
        raise ValueError("official_summary_identity_mismatch")
    c = comps[0]
    identity = _identity(c)
    if any(identity[k] != game[k] for k in ("home_id", "away_id")) or any(
        normalize_team(identity[k]) != normalize_team(game[k]) for k in ("home_team", "away_team")):
        raise ValueError("official_summary_team_mismatch")
    if _time(c["date"]) != _time(game["kickoff"]):
        raise ValueError("official_summary_kickoff_changed")
    if c.get("status", {}).get("type", {}).get("state") != "pre" or c.get("dateValid") is False:
        raise ValueError("official_summary_not_pregame")
    venue_id = str(payload.get("gameInfo", {}).get("venue", {}).get("id", ""))
    if not venue_id.isdigit() or int(venue_id) <= 0:
        raise ValueError("official_venue_missing")
    return {"game_id": game["game_id"], "kickoff": game["kickoff"], **identity,
            "venue_id": venue_id, "neutral_site": c.get("neutralSite"), "state": "pre"}


def collect_context(client, game, catalog):
    row = {**game, "context": None, "context_id": None, "missingness": [],
           "single_run": None, "previous_day2": None, "quotes": [], "pairs": []}
    summary = client.fetch(SUMMARY, {"event": game["game_id"]}, purpose="official_game_context")
    row["summary_receipt"] = summary["receipt"]["receipt_path"]
    if not _ok(summary):
        row["missingness"].append("official_summary_request_failed")
        return row
    try:
        context = summary_identity(summary["payload"], game)
        roof = client.fetch(VENUES + context["venue_id"], {"lang": "en", "region": "us"}, purpose="official_roof_context")
        row["roof_receipt"] = roof["receipt"]["receipt_path"]
        if not _ok(roof) or not isinstance(roof["payload"], dict) or str(roof["payload"].get("id")) != context["venue_id"]:
            raise ValueError("official_roof_unavailable")
        context["indoor"] = roof["payload"].get("indoor")
        row["context"], row["context_id"] = context, digest_json(context)
        if context["neutral_site"] is not False:
            row["missingness"].append("neutral_or_unknown_site")
        if context["indoor"] is not False:
            row["missingness"].append("indoor_or_unknown_roof")
        if context["venue_id"] not in catalog["venues"]:
            row["missingness"].append("venue_not_in_frozen_catalog")
    except (AttributeError, KeyError, TypeError, ValueError):
        row["missingness"].append("official_context_invalid")
    return row


def _cached_comparators(root, archive):
    cached = {}
    for path in sorted((archive / "runs").glob("*.json")):
        try:
            manifest = json.loads(path.read_text())
            for row in manifest.get("rows", []):
                comparator = row.get("previous_day2")
                if not comparator:
                    continue
                spec = comparator["request_spec"]
                envelope = load_envelope(root, comparator["receipt_path"])
                measurement = parse_previous_day2(envelope["payload"], spec, envelope["receipt"], row["kickoff"])
                if row["context_id"] != spec["context_id"] or digest_json(row["context"]) != row["context_id"]:
                    continue
                key = spec["request_key"]
                candidate = {"request_spec": spec, "measurement": measurement,
                             "receipt_path": comparator["receipt_path"], "cached": True}
                if key not in cached or _time(envelope["receipt"]["received_at"]) < _time(cached[key]["measurement"]["received_at"]):
                    cached[key] = candidate
        except (KeyError, OSError, TypeError, ValueError):
            continue
    return cached


def _fetch_weather(client, spec):
    return client.fetch(spec["source_url"], spec["parameters"], purpose="weather_" + spec["product"])


def _remaining(envelope):
    try:
        return int(envelope["receipt"]["response_headers"]["x_ratelimit_remaining"])
    except (KeyError, TypeError, ValueError):
        return None


def _match_key(event, provider=False):
    return (normalize_team(str(event["home"] if provider else event["home_team"])),
            normalize_team(str(event["away"] if provider else event["away_team"])),
            _stamp(event["date"] if provider else event["kickoff"]))


def parse_quote_pairs(payload, targets, receipt):
    """Keep every unambiguous main Totals pair; no price/EV selection."""
    output, failures = [], []
    events = payload if isinstance(payload, list) else [payload]
    counts = {}
    for e in events:
        if isinstance(e, dict):
            identity = str(e.get("id", ""))
            counts[identity] = counts.get(identity, 0) + 1
    for event in events:
        if not isinstance(event, dict):
            continue
        provider_id = str(event.get("id", ""))
        if provider_id not in targets:
            continue
        game = targets[provider_id]
        try:
            received = _time(receipt["received_at"])
            if (counts[provider_id] != 1 or _match_key(event, True) != _match_key(game)
                or event.get("status") not in {"pending", "upcoming", "scheduled", "prematch"}
                or _time(receipt["requested_at"]) > received
                or received >= _time(game["kickoff"])):
                raise ValueError("provider_event_mismatch_or_started")
            for title, book in BOOKS.items():
                markets = (event.get("bookmakers") or {}).get(title, [])
                totals = [m for m in markets if isinstance(m, dict) and m.get("name") == "Totals"]
                if len(totals) != 1:
                    failures.append({"game_id": game["game_id"], "book": book, "reason": "missing_or_duplicate_main_totals"})
                    continue
                market = totals[0]
                updated = _time(market["updatedAt"]) if market.get("updatedAt") else None
                if updated is not None and updated > received + timedelta(seconds=5):
                    raise ValueError("future_market_timestamp")
                if market.get("period") not in (None, "full_game", "Full Game", "FT"):
                    raise ValueError("unverified_market_period")
                odds = market.get("odds", [])
                seen = {}
                for raw in odds:
                    if isinstance(raw, dict):
                        try:
                            numeric_line = float(raw.get("hdp"))
                            seen[numeric_line] = seen.get(numeric_line, 0) + 1
                        except (TypeError, ValueError):
                            pass
                for raw in odds:
                    try:
                        if not isinstance(raw, dict) or any(isinstance(raw.get(k), bool) for k in ("hdp", "over", "under")):
                            raise ValueError("invalid_numeric_pair")
                        line, over, under = [float(raw[k]) for k in ("hdp", "over", "under")]
                        if not all(math.isfinite(x) for x in (line, over, under)) or line <= 0 or line * 2 != round(line * 2) or min(over, under) <= 1:
                            raise ValueError("invalid_numeric_pair")
                        if seen[line] != 1:
                            raise ValueError("duplicate_line")
                        quote = {"game_id": game["game_id"], "provider_event_id": provider_id,
                                 "sportsbook": book, "market": "Totals", "period": "full_game",
                                 "line": line, "over_decimal_odds": over, "under_decimal_odds": under,
                                 "market_updated_at": _stamp(updated) if updated else None,
                                 "requested_at": receipt["requested_at"], "observed_at": receipt["received_at"],
                                 "observation_kind": "provider_full_state", "source": "odds_api_io",
                                 "receipt_path": receipt["receipt_path"], "body_sha256": receipt["body_sha256"]}
                        quote["quote_id"] = digest_json(quote)
                        output.append(quote)
                    except (KeyError, TypeError, ValueError):
                        failures.append({"game_id": game["game_id"], "book": book, "reason": "invalid_or_duplicate_total_pair"})
        except (AttributeError, KeyError, TypeError, ValueError):
            failures.append({"game_id": game["game_id"], "reason": "provider_event_or_market_invalid"})
    return output, failures


def collect_quotes(client, rows, key, start):
    if not key:
        return [], [{"reason": "odds_key_missing"}]
    auth = {"apiKey": key}
    selected = client.fetch(ODDS + "/bookmakers/selected", secret_params=auth, purpose="selected_books")
    if not _ok(selected):
        return [], [{"reason": "selected_books_request_failed"}]
    selected_remaining = _remaining(selected)
    if selected_remaining is not None and selected_remaining <= QUOTA_RESERVE:
        return [], [{"reason": "selected_books_quota_reserve", "remaining": selected_remaining}]
    payload = selected["payload"]
    selected_names = payload.get("bookmakers", []) if isinstance(payload, dict) else payload
    if not isinstance(selected_names, list):
        return [], [{"reason": "selected_books_schema_invalid"}]
    allowed = [name for name in BOOKS if name in selected_names]
    if not allowed:
        return [], [{"reason": "required_books_not_selected"}]
    events = client.fetch(ODDS + "/events", {"sport": "american-football", "league": "usa-college", "status": "pending",
        "limit": 500, "from": _stamp(start), "to": _stamp(start + timedelta(days=7))}, auth, purpose="provider_inventory")
    if not _ok(events) or not isinstance(events["payload"], list):
        return [], [{"reason": "provider_inventory_failed"}]
    official, candidates = {}, {}
    for game in rows:
        official.setdefault(_match_key(game), []).append(game)
    for event in events["payload"]:
        try:
            if (event.get("status") != "pending" or not any(s in str(event.get("league", {})).lower() for s in ("college", "ncaa"))):
                continue
            candidates.setdefault(_match_key(event, True), []).append(event)
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    targets, failures = {}, []
    for match, games in official.items():
        choices = candidates.get(match, [])
        if len(games) != 1 or len(choices) != 1 or not str(choices[0].get("id", "")).isdigit():
            failures.append({"game_id": games[0]["game_id"], "reason": "missing_or_ambiguous_provider_match"})
            continue
        identity = str(choices[0]["id"])
        if identity in targets:
            return [], [{"reason": "duplicate_provider_identity"}]
        targets[identity] = games[0]
    remaining = _remaining(events)
    required = math.ceil(len(targets) / 10)
    if remaining is None or remaining < required + QUOTA_RESERVE:
        return [], failures + [{"reason": "quota_reserve_or_metadata_unavailable", "remaining": remaining, "required": required}]
    identities = sorted(targets, key=lambda k: (_time(targets[k]["kickoff"]), int(targets[k]["game_id"])))
    quotes = []
    for offset in range(0, len(identities), 10):
        batch_targets = {k: targets[k] for k in identities[offset:offset + 10]}
        envelope = client.fetch(ODDS + "/odds/multi", {"eventIds": ",".join(identities[offset:offset + 10]),
            "bookmakers": ",".join(allowed)}, auth, purpose="fresh_totals_after_weather")
        if _ok(envelope):
            normalized, errors = parse_quote_pairs(envelope["payload"], batch_targets, envelope["receipt"])
            quotes.extend(normalized)
            failures.extend(errors)
        else:
            failures.append({"reason": "odds_batch_failed", "receipt_path": envelope["receipt"]["receipt_path"]})
        remaining = _remaining(envelope)
        if envelope["receipt"]["status_code"] in {401, 403, 429} or remaining is None or remaining <= QUOTA_RESERVE:
            if offset + 10 < len(identities):
                failures.append({"reason": "remaining_batches_stopped_for_quota"})
            break
    return quotes, failures


def collect(root, now=None, client=None):
    root = root.resolve()
    start = _time(now or timestamp())
    archive = root / "data/runtime/weather_revisions"
    run_id = os.environ.get("GITHUB_RUN_ID") or "local-" + start.strftime("%Y%m%dT%H%M%S%fZ")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    if not all(c.isalnum() or c in "-_" for c in run_id + attempt):
        raise ValueError("Invalid invocation identity")
    manifest = {"schema_version": "weather-revision-capture-v1", "collector_version": VERSION,
        "run_id": run_id, "run_attempt": attempt, "trigger": os.environ.get("GITHUB_EVENT_NAME", "manual_local"),
        "capture_started_at": _stamp(start), "capture_completed_at": None,
        "requested_run": _stamp(select_run(start)), "status": "failed", "rows": [], "failures": [],
        "counts": {k: 0 for k in ("cohort_games", "weather_available_games", "two_book_games", "paired_games",
            "paired_two_book_games", "mature_comparator_games", "failed_requests", "total_requests")}}
    client = client or ArchiveClient(root)
    path = archive / "runs" / f"{run_id}-{attempt}.json"
    if path.exists():
        raise ValueError("Invocation already archived; use a new run attempt")
    try:
        if not START <= start < END:
            manifest["status"] = "outside_pilot"
            return manifest
        pinned = ["ncaaf_model/weather_revision_collector.py", "ncaaf_model/weather_revision_weather.py",
                  "ncaaf_model/revision_archive.py", "ncaaf_model/teams.py", "reports/WEATHER_REVISION_CAPTURE_PROTOCOL.md", "data/models/weather_venues_v1.json"]
        manifest["provenance"] = {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in pinned}
        manifest["git_commit"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip() or None
        manifest["workflow_commit"] = os.environ.get("GITHUB_SHA")
        if manifest["provenance"][pinned[-1]] != CATALOG_SHA:
            raise ValueError("Frozen catalog hash mismatch")
        catalog = json.loads((root / pinned[-1]).read_text())
        inventory = client.fetch(SCOREBOARD, {"groups": 80, "limit": 1000,
            "dates": f"{start:%Y%m%d}-{start + timedelta(days=7):%Y%m%d}"}, purpose="official_frozen_cohort")
        manifest["inventory_receipt"] = inventory["receipt"]["receipt_path"]
        if not _ok(inventory):
            raise ValueError("Official inventory request failed")
        games, exclusions, enumerated = parse_cohort(inventory["payload"], start)
        manifest["cohort_exclusions"], manifest["enumerated_games"] = exclusions, enumerated
        manifest["counts"]["cohort_games"] = len(games)
        cohort_path = archive / "cohorts" / f"{run_id}-{attempt}.json"
        immutable_json(cohort_path, {"capture_started_at": _stamp(start), "inventory_receipt": manifest["inventory_receipt"],
            "games": games, "exclusions": exclusions, "enumerated_games": enumerated, "frozen_at": timestamp()})
        manifest["cohort_path"] = cohort_path.relative_to(root).as_posix()
        if not games:
            manifest["status"] = "partial" if exclusions else "no_games"
            return manifest
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            rows = list(pool.map(lambda g: collect_context(client, g, catalog), games))
        manifest["rows"] = rows
        specs = {}
        for row in rows:
            if not row["missingness"] and row["context"]:
                vid = row["context"]["venue_id"]
                spec = single_run_request({"venue_id": vid, **catalog["venues"][vid]}, start)
                row["single_run_request_key"] = spec["request_key"]
                specs[spec["request_key"]] = spec
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            bodies = dict(zip(specs, pool.map(lambda spec: _fetch_weather(client, spec), specs.values())))
        cached = _cached_comparators(root, archive)
        for row in rows:
            key = row.get("single_run_request_key")
            if key is None:
                continue
            envelope, spec = bodies[key], specs[key]
            try:
                measurement = parse_single_run(envelope["payload"], spec, envelope["receipt"], row["kickoff"])
                row["single_run"] = {"request_spec": spec, "measurement": measurement,
                    "receipt_path": envelope["receipt"]["receipt_path"]}
            except (KeyError, TypeError, ValueError):
                row["missingness"].append("single_run_validation_failed")
            comparator = previous_day2_request(spec["venue"], row["kickoff"], start, row["context_id"])
            row["comparator_mature_at"] = _stamp(maturity_time(row["kickoff"]))
            if comparator is None:
                row["comparator_status"] = "not_mature"
            elif comparator["request_key"] in cached:
                row["previous_day2"] = cached[comparator["request_key"]]
                row["comparator_status"] = "cached_mature"
            else:
                envelope = _fetch_weather(client, comparator)
                try:
                    measurement = parse_previous_day2(envelope["payload"], comparator, envelope["receipt"], row["kickoff"])
                    row["previous_day2"] = {"request_spec": comparator, "measurement": measurement,
                        "receipt_path": envelope["receipt"]["receipt_path"], "cached": False}
                    row["comparator_status"] = "received_mature"
                except (KeyError, TypeError, ValueError):
                    row["comparator_status"] = "failed"
                    row["missingness"].append("previous_day2_validation_failed")
        manifest["weather_stage_completed_at"] = timestamp()
        def recheck(row):
            if not row["single_run"]:
                return
            envelope = client.fetch(SUMMARY, {"event": row["game_id"]}, purpose="official_context_after_weather")
            row["context_recheck_receipt"] = envelope["receipt"]["receipt_path"]
            row["context_recheck_received_at"] = envelope["receipt"]["received_at"]
            row["context_recheck_ok"] = False
            try:
                identity = summary_identity(envelope["payload"], row) if _ok(envelope) else None
                expected = {k: v for k, v in row["context"].items() if k != "indoor"}
                row["context_recheck_ok"] = identity == expected
            except (AttributeError, KeyError, TypeError, ValueError):
                pass
            if not row["context_recheck_ok"]:
                row["missingness"].append("context_changed_or_recheck_failed")
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            list(pool.map(recheck, rows))
        load_dotenv(root.parents[1] / ".env")
        quotes, failures = collect_quotes(client, rows, os.environ.get("ODDS_API_IO_KEY", ""), start)
        manifest["failures"].extend(failures)
        for row in rows:
            row["quotes"] = [q for q in quotes if q["game_id"] == row["game_id"]]
            if not row["quotes"]:
                row["missingness"].append("no_valid_totals_quote")
            if row["single_run"] and row.get("context_recheck_ok"):
                weather = row["single_run"]["measurement"]
                for quote in row["quotes"]:
                    gap = (_time(quote["observed_at"]) - _time(weather["received_at"])).total_seconds()
                    if gap >= 0 and _time(quote["requested_at"]) >= max(_time(manifest["weather_stage_completed_at"]), _time(row["context_recheck_received_at"])):
                        row["pairs"].append({"context_id": row["context_id"], "quote_id": quote["quote_id"],
                            "weather_receipt": row["single_run"]["receipt_path"], "quote_receipt": quote["receipt_path"],
                            "weather_to_quote_seconds": gap, "sportsbook": quote["sportsbook"],
                            "context_recheck_receipt": row["context_recheck_receipt"]})
        counts = manifest["counts"]
        counts["weather_available_games"] = sum(r["single_run"] is not None for r in rows)
        counts["mature_comparator_games"] = sum(r["previous_day2"] is not None for r in rows)
        counts["two_book_games"] = sum(len({q["sportsbook"] for q in r["quotes"]}) == 2 for r in rows)
        counts["paired_games"] = sum(bool(r["pairs"]) for r in rows)
        counts["paired_two_book_games"] = sum(len({p["sportsbook"] for p in r["pairs"]}) == 2 for r in rows)
        manifest["status"] = "partial" if failures or any(r["missingness"] for r in rows) or exclusions else "ok"
    except (OSError, AttributeError, KeyError, TypeError, ValueError) as exc:
        manifest["failures"].append({"reason": "collector_failed", "exception_class": type(exc).__name__})
        manifest["status"] = "failed"
    finally:
        rows = manifest["rows"]
        counts = manifest["counts"]
        counts["weather_available_games"] = sum(r.get("single_run") is not None for r in rows)
        counts["mature_comparator_games"] = sum(r.get("previous_day2") is not None for r in rows)
        counts["two_book_games"] = sum(len({q["sportsbook"] for q in r.get("quotes", [])}) == 2 for r in rows)
        counts["paired_games"] = sum(bool(r.get("pairs")) for r in rows)
        counts["paired_two_book_games"] = sum(len({p["sportsbook"] for p in r.get("pairs", [])}) == 2 for r in rows)
        manifest["capture_completed_at"] = timestamp()
        manifest["receipts"] = [r["receipt_path"] for r in client.receipts]
        manifest["counts"]["total_requests"] = len(client.receipts)
        manifest["counts"]["failed_requests"] = sum(r["status_code"] != 200 or bool(r["transport_error"]) for r in client.receipts)
        manifest["stored_response_bytes"] = sum(r.get("body_size_bytes", 0) for r in client.receipts)
        immutable_json(path, manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    result = collect(args.root)
    print(json.dumps({k: result[k] for k in ("run_id", "status", "counts")}, sort_keys=True))
    return 0 if result["status"] in {"ok", "no_games", "outside_pilot"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
