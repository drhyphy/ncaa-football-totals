"""Inventory revision-model prerequisites without reading weather values or labels.

Only local immutable manifests, metadata, opaque response hashes and recorded Git
blobs are used. Existing independent capture audits provide additional semantic
validation; this script does not import their reconstruction routines.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess

UTC = timezone.utc
START = datetime(2026, 9, 9, 3, tzinfo=UTC)
END = datetime(2026, 9, 16, 3, tzinfo=UTC)
SEASON_END = datetime(2027, 2, 1, 3, tzinfo=UTC)
SEASON_PROTOCOL = "reports/WEATHER_REVISION_SEASON_COLLECTION_PROTOCOL.md"
SLOTS = ["01:17", "07:17", "13:17", "19:17"]
BASE = Path("data/runtime/weather_revisions")
DESIGN = Path("model/reports/WEATHER_REVISION_MODEL_DESIGN.md")
REQUIRED_SOURCES = {
    "reports/WEATHER_REVISION_CAPTURE_PROTOCOL.md",
    "ncaaf_model/revision_archive.py",
    "ncaaf_model/weather_revision_collector.py",
    "ncaaf_model/weather_revision_weather.py",
    "ncaaf_model/teams.py",
    "data/models/weather_venues_v1.json",
}
COHORT_KEYS = ("game_id", "home_id", "away_id", "home_team", "away_team",
               "kickoff", "inventory_venue_id", "inventory_neutral_site")


def sha(body):
    return hashlib.sha256(body).hexdigest()


def digest(value):
    return sha(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())


def stamp(value):
    if not isinstance(value, str):
        raise ValueError("missing_or_nonstring_timestamp")
    value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("naive timestamp")
    return value.astimezone(UTC)


def iso(value):
    return value.isoformat().replace("+00:00", "Z")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def local_path(root, relative, directory):
    require(isinstance(relative, str), "missing path")
    path = (root / relative).resolve()
    require(path.is_relative_to((root / directory).resolve()), "path outside permitted archive directory")
    return path


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def profile(manifest):
    """Legacy archives are pilot; a season archive must name its fixed window."""
    name = manifest.get("collection_profile", "pilot")
    require(name in ("pilot", "season"), "unknown_collection_profile")
    start, end = (START, END) if name == "pilot" else (END, SEASON_END)
    if name == "season":
        require(manifest.get("collection_protocol_id") == "weather-revision-season-collection-v1"
                and manifest.get("collection_protocol_file") == SEASON_PROTOCOL, "season_protocol_identity")
    if "collection_profile" in manifest:
        require(manifest.get("collection_window") == {"start_inclusive": iso(start), "end_exclusive": iso(end)}, "collection_window_identity")
        require(manifest.get("scheduled_utc_slots") == SLOTS, "scheduled_collection_slots")
    return name, start, end


class Integrity:
    """Rehash original archives; response bodies are never decoded as JSON."""

    def __init__(self, root):
        self.root = root.resolve()
        self.model = self.root / "model"
        self.receipts = {}
        self.blobs = {}
        self.audits = []
        for path in sorted((self.model / "reports").glob("weather_revision*capture_audit.json")):
            data = json.loads(path.read_text())
            if data.get("schema_version") == "weather-revision-independent-audit-v1":
                self.audits.append((path, data))

    def receipt(self, relative):
        if relative in self.receipts:
            return self.receipts[relative]
        path = local_path(self.model, relative, BASE / "receipts")
        data = json.loads(path.read_text())
        require(data.get("schema_version") == "raw-http-receipt-v1", "receipt schema")
        require(data.get("receipt_path") == relative, "receipt self path")
        require(path.name == digest({k: v for k, v in data.items() if k != "receipt_path"}) + ".json", "receipt digest")
        require(stamp(data["requested_at"]) <= stamp(data["received_at"]), "receipt time order")
        if data.get("body_path"):
            body = gzip.decompress(local_path(self.model, data["body_path"], BASE / "bodies").read_bytes())
            require(sha(body) == data["body_sha256"] and len(body) == data["body_size_bytes"], "opaque body hash or size")
        else:
            require(data.get("transport_error") or data.get("body_withheld"), "missing body without recorded failure")
        self.receipts[relative] = data
        return data

    def verify(self, path, manifest, cohort):
        errors, sources = [], {}
        related = [(p, a) for p, a in self.audits if a.get("run_id") == manifest.get("run_id")
                   and str(a.get("run_attempt")) == str(manifest.get("run_attempt"))]
        matches = [(p, a) for p, a in related
                   if a.get("manifest_sha256") == sha(path.read_bytes())
                   and a.get("audit_passed") is True
                   and a.get("recorded_commit_provenance_verified") is True]
        if related and not matches:
            errors.append("known_independent_audit_failed_or_original_manifest_changed")
        commit = manifest.get("git_commit", "")
        recorded = manifest.get("provenance", {})
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
            errors.append("recorded_git_commit_invalid")
        elif not (REQUIRED_SOURCES | ({SEASON_PROTOCOL} if manifest.get("collection_profile") == "season" else set())) <= recorded.keys():
            errors.append("required_protocol_writer_parser_catalog_reference_missing")
        else:
            for relative, expected in recorded.items():
                source = Path(relative)
                if source.is_absolute() or ".." in source.parts:
                    errors.append("unsafe_source_reference")
                    continue
                key = (commit, relative)
                if key not in self.blobs:
                    try:
                        self.blobs[key] = subprocess.check_output(
                            ["git", "show", commit + ":model/" + source.as_posix()],
                            cwd=self.root, stderr=subprocess.DEVNULL)
                    except subprocess.CalledProcessError:
                        self.blobs[key] = None
                blob = self.blobs[key]
                sources[relative] = {"recorded_sha256": expected,
                                     "recorded_git_blob_verified": blob is not None and sha(blob) == expected}
                if not sources[relative]["recorded_git_blob_verified"]:
                    errors.append("recorded_source_blob_missing_or_mismatched:" + relative)
                for _, audit in matches:
                    old = audit.get("provenance", {}).get(relative, {})
                    if old.get("recorded") != expected or old.get("recorded_commit_matches") is not True:
                        errors.append("audit_source_reference_mismatch:" + relative)
        try:
            require(manifest["schema_version"] == "weather-revision-capture-v1", "manifest schema")
            start, end = stamp(manifest["capture_started_at"]), stamp(manifest["capture_completed_at"])
            require(start <= end, "capture time order")
            require(cohort["capture_started_at"] == manifest["capture_started_at"], "cohort capture identity")
            require(cohort["inventory_receipt"] == manifest["inventory_receipt"], "cohort inventory identity")
            receipt = self.receipt(manifest["inventory_receipt"])
            require(receipt["purpose"] == "official_frozen_cohort" and receipt["status_code"] == 200, "official cohort receipt")
            require(start <= stamp(receipt["received_at"]) <= stamp(cohort["frozen_at"]) <= end, "cohort freeze ordering")
            require(len(cohort["games"]) == manifest["counts"]["cohort_games"], "cohort count")
            if manifest.get("rows"):
                require([{k: r[k] for k in COHORT_KEYS} for r in manifest["rows"]] == cohort["games"], "cohort versus manifest identity")
            for relative in manifest["receipts"]:
                self.receipt(relative)
            for row in manifest.get("rows", []):
                if row.get("single_run"):
                    require(stamp(cohort["frozen_at"]) <= stamp(row["single_run"]["measurement"]["requested_at"]), "weather predates frozen cohort")
                    catalog_blob = self.blobs.get((commit, "data/models/weather_venues_v1.json"))
                    require(catalog_blob is not None, "recorded frozen catalog unavailable")
                    catalog = json.loads(catalog_blob)["venues"]
                    vid = row["context"]["venue_id"]
                    require(row["single_run"]["request_spec"]["venue"] == {"venue_id": vid, **catalog[vid]}, "request differs from frozen venue coordinates")
        except (ValueError, KeyError, TypeError, OSError, EOFError) as error:
            errors.append("archive_integrity:" + str(error))
        return {"verified": not errors, "independently_audited": bool(matches),
                "errors": sorted(set(errors)), "source_references": sources,
                "independent_audits": [{"path": str(p.relative_to(self.root)), "sha256": sha(p.read_bytes())} for p, _ in matches]}


def metadata_row(manifest, row, receipt):
    """Return whitelisted context/request/receipt metadata; ignore weather values."""
    context = row.get("context")
    require(isinstance(context, dict) and row.get("context_id") == digest(context), "context_missing_or_digest_invalid")
    require(context.get("state") == "pre" and context.get("indoor") is False
            and context.get("neutral_site") is False, "unconfirmed_outdoor_pregame_context")
    require(all(context.get(k) == row.get(k) for k in ("game_id", "home_id", "away_id", "kickoff")), "row_context_identity")
    record = row.get("single_run")
    require(isinstance(record, dict), "single_run_unavailable")
    m, spec = record["measurement"], record["request_spec"]
    require(m["model"] == "gfs_global" and m["product"] == spec["product"] == "single_run", "forecast_product")
    require(m["requested_run"] == spec["requested_run"] == manifest["requested_run"], "initialization_identity")
    require(m["kickoff"] == row["kickoff"], "forecast_kickoff")
    require(m["request_key"] == spec["request_key"], "forecast_request_key")
    params = spec["parameters"]
    require(params.get("models") == "gfs_global" and params.get("run") == stamp(m["requested_run"]).strftime("%Y-%m-%dT%H:%M"), "explicit_initialization_parameter")
    require(m["source_url"] == spec["source_url"] == "https://single-runs-api.open-meteo.com/v1/forecast", "forecast_source")
    require(spec["venue"] == m["venue"] and str(spec["venue"]["venue_id"]) == context["venue_id"], "frozen_venue_identity")
    require(params == {"models": "gfs_global", "run": stamp(m["requested_run"]).strftime("%Y-%m-%dT%H:%M"),
                       "latitude": spec["venue"]["latitude"], "longitude": spec["venue"]["longitude"], "forecast_days": 8,
                       "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m", "temperature_unit": "fahrenheit",
                       "timezone": "GMT", "wind_speed_unit": "mph"}, "forecast_request_options_differ_from_collection_contract")
    valid = [stamp(row["kickoff"]).replace(minute=0, second=0, microsecond=0) + timedelta(hours=i) for i in range(4)]
    require([stamp(v) for v in m["valid_hours_utc"]] == valid, "game_valid_hours")
    weather = receipt(record["receipt_path"])
    require(record["receipt_path"] == m["receipt_path"] and weather["body_sha256"] == m["body_sha256"], "weather_receipt_identity")
    require(weather["status_code"] == 200 and weather.get("transport_error") is None, "weather_receipt_failure")
    require(weather["received_at"] == m["received_at"] and weather["requested_at"] == m["requested_at"], "weather_receipt_clock")
    require(weather["request"]["url"] == spec["source_url"] and weather["request"]["params"] == params, "weather_request_receipt")
    start, end = stamp(manifest["capture_started_at"]), stamp(manifest["capture_completed_at"])
    require(start <= stamp(m["requested_at"]) <= stamp(m["received_at"]) <= end, "weather_outside_capture")
    return {"context": context, "context_id": row["context_id"], "initialization": m["requested_run"],
            "request_options": {k: v for k, v in params.items() if k != "run"},
            "venue": spec["venue"], "valid_hours": m["valid_hours_utc"],
            "parser_version": m.get("parser_version"), "source_url": spec["source_url"],
            "weather_receipt": record["receipt_path"], "weather_received_at": m["received_at"],
            "body_sha256": m["body_sha256"]}


def unique_quote(manifest, row, book, weather, receipt):
    quotes = [q for q in row.get("quotes", []) if q.get("sportsbook") == book]
    require(len(quotes) == 1, "unique_" + book + "_main_pair_unavailable")
    quote = quotes[0]
    require(quote.get("market") == "Totals" and quote.get("period") == "full_game"
            and quote.get("source") == "odds_api_io" and quote.get("observation_kind") == "provider_full_state", "ambiguous_quote_contract")
    require(quote.get("game_id") == row["game_id"], "quote_game_identity")
    require(finite(quote.get("line")) and quote["line"] > 0
            and all(finite(quote.get(k)) and quote[k] > 1 for k in ("over_decimal_odds", "under_decimal_odds")), "invalid_line_or_sides")
    require(quote.get("quote_id") == digest({k: v for k, v in quote.items() if k != "quote_id"}), "quote_identity_digest")
    qreceipt = receipt(quote["receipt_path"])
    require(qreceipt["status_code"] == 200 and qreceipt.get("transport_error") is None, "quote_receipt_failure")
    require(qreceipt["body_sha256"] == quote["body_sha256"]
            and qreceipt["received_at"] == quote["observed_at"]
            and qreceipt["requested_at"] == quote["requested_at"], "quote_receipt_identity")
    require(row.get("context_recheck_ok") is True, "context_recheck_unavailable")
    recheck = receipt(row["context_recheck_receipt"])
    require(recheck["status_code"] == 200 and recheck.get("transport_error") is None
            and recheck["received_at"] == row["context_recheck_received_at"], "context_recheck_receipt")
    require(stamp(weather["weather_received_at"]) <= stamp(recheck["requested_at"])
            <= stamp(recheck["received_at"]) <= stamp(quote["requested_at"])
            <= stamp(quote["observed_at"]) <= stamp(manifest["capture_completed_at"]), "weather_recheck_quote_order")
    require(stamp(quote["observed_at"]) < stamp(row["kickoff"]), "quote_not_pregame")
    pairs = [p for p in row.get("pairs", []) if p.get("quote_id") == quote["quote_id"]]
    require(len(pairs) == 1, "unique_receipt_link_unavailable")
    pair = pairs[0]
    require(pair.get("weather_receipt") == weather["weather_receipt"]
            and pair.get("context_id") == weather["context_id"]
            and pair.get("quote_receipt") == quote["receipt_path"]
            and pair.get("context_recheck_receipt") == row["context_recheck_receipt"]
            and pair.get("sportsbook") == book, "receipt_link_identity")
    return quote


def pair_metadata(old, new, game_id, receipt):
    """Validate the shared t0/t1 input contract, without calculating revisions."""
    require(old is not None, "preceding_scheduled_capture_unavailable")
    gap = (stamp(new["manifest"]["capture_started_at"]) - stamp(old["manifest"]["capture_started_at"])).total_seconds() / 3600
    require(4 <= gap <= 8, "capture_gap_outside_4_8_hours")
    oldrow, row = old["rows"].get(game_id), new["rows"].get(game_id)
    require(oldrow is not None and row is not None, "game_missing_from_immediate_capture")
    a = metadata_row(old["manifest"], oldrow, receipt)
    b = metadata_row(new["manifest"], row, receipt)
    for key in ("context", "context_id", "request_options", "venue", "valid_hours", "source_url", "parser_version"):
        require(a[key] == b[key], "changed_" + key)
    require(stamp(b["initialization"]) - stamp(a["initialization"]) == timedelta(hours=6), "initializations_not_exactly_six_hours_apart")
    require(a["weather_receipt"] != b["weather_receipt"], "reused_weather_receipt")
    q0 = unique_quote(old["manifest"], oldrow, "draftkings", a, receipt)
    dk = unique_quote(new["manifest"], row, "draftkings", b, receipt)
    fd = unique_quote(new["manifest"], row, "fanduel", b, receipt)
    cutoff = max(stamp(dk["observed_at"]), stamp(fd["observed_at"]))
    require(stamp(q0["observed_at"]) < stamp(b["weather_received_at"]) <= cutoff, "cross_capture_receipt_order")
    lead = (stamp(row["kickoff"]) - cutoff).total_seconds() / 3600
    require(24 <= lead <= 48, "actual_quote_decision_outside_24_48_hours")
    half_point = (dk["line"] * 2).is_integer() if isinstance(dk["line"], float) else True
    half_point = half_point and dk["line"] % 1 == 0.5
    return {"context_id": b["context_id"], "older_initialization": a["initialization"],
            "current_initialization": b["initialization"], "capture_gap_hours": gap,
            "decision_received_at": iso(cutoff), "kickoff_lead_hours": lead,
            "half_point_contract": half_point,
            "quote_ids": {"t0_draftkings": q0["quote_id"], "t1_draftkings": dk["quote_id"], "t1_fanduel": fd["quote_id"]},
            "weather_receipts": [a["weather_receipt"], b["weather_receipt"]]}


def inventory(runs, receipt, *, complete_enumeration=True, validate_run_identities=None, include_diagnostics=True):
    """Select from cohorts first. Missing data never advances a designated game."""
    runs = sorted(runs, key=lambda x: (stamp(x["manifest"]["capture_started_at"]), x["manifest"]["run_id"], str(x["manifest"]["run_attempt"])))
    scheduled = []
    profile_counts = Counter()
    for run in runs:
        name, begin, end = profile(run["manifest"])
        profile_counts[name] += 1
        if run["manifest"].get("trigger") == "schedule" and begin <= stamp(run["manifest"]["capture_started_at"]) < end:
            scheduled.append(run)
    decisions, designated = [], set()
    for index, run in enumerate(scheduled):
        start = stamp(run["manifest"]["capture_started_at"])
        for game in run["cohort"].get("games", []):
            gid = str(game["game_id"])
            if gid in designated or not 24 <= (stamp(game["kickoff"]) - start).total_seconds() / 3600 <= 48:
                continue
            designated.add(gid)  # Must precede every weather/quote/integrity filter.
            prior = scheduled[index - 1] if index else None
            item = {"game_id": gid, "kickoff": game["kickoff"], "capture_started_at": iso(start),
                    "run_id": run["manifest"]["run_id"], "run_attempt": str(run["manifest"]["run_attempt"]),
                    "preceding_run_id": prior["manifest"]["run_id"] if prior else None,
                    "metadata_input_eligible": False, "source_receipt_verified_input_eligible": False,
                    "independently_audited_input_eligible": False,
                    "probability_input_eligible": False, "movement_input_eligible": False, "exclusions": []}
            if (validate_run_identities is not None and
                    (str(item['run_id']), str(item['run_attempt'])) not in validate_run_identities):
                item['exclusions'].append('validation_deferred_until_after_live_inference')
                decisions.append(item)
                continue
            try:
                details = pair_metadata(prior, run, gid, receipt)
                require(run["rows"][gid]["kickoff"] == game["kickoff"], "decision_cohort_kickoff_changed")
                item.update(details)
                item["metadata_input_eligible"] = True
                if not complete_enumeration:
                    item["exclusions"].append("unreadable_archive_prevents_safe_run_order")
                if not run["integrity"]["verified"] or not prior["integrity"]["verified"]:
                    item["exclusions"].append("source_receipt_integrity_unverified")
                item["source_receipt_verified_input_eligible"] = not item["exclusions"]
                item["independently_audited_input_eligible"] = (not item["exclusions"]
                    and run["integrity"].get("independently_audited", False)
                    and prior["integrity"].get("independently_audited", False))
                item["movement_input_eligible"] = item["source_receipt_verified_input_eligible"]
                item["probability_input_eligible"] = item["source_receipt_verified_input_eligible"] and details["half_point_contract"]
                if not details["half_point_contract"]:
                    item["exclusions"].append("probability_requires_half_point_contract")
            except (KeyError, TypeError, ValueError, OSError, EOFError) as error:
                item["exclusions"].append(str(error))
            decisions.append(item)
    diagnostics = Counter()
    for old, new in (zip(runs, runs[1:]) if include_diagnostics else []):
        for gid in old["rows"].keys() & new["rows"].keys():
            try:
                a = metadata_row(old["manifest"], old["rows"][gid], receipt)
                b = metadata_row(new["manifest"], new["rows"][gid], receipt)
                if any(a[k] != b[k] for k in ("context_id", "request_options", "venue", "valid_hours", "source_url", "parser_version")):
                    diagnostics["adjacent_changed_context_or_options"] += 1
                    continue
                delta = stamp(b["initialization"]) - stamp(a["initialization"])
                if delta == timedelta(0):
                    diagnostics["adjacent_same_initialization_game_pairs"] += 1
                    diagnostics["same_initialization_" + ("unchanged" if a["body_sha256"] == b["body_sha256"] else "changed") + "_body_game_pairs"] += 1
                elif delta == timedelta(hours=6):
                    diagnostics["adjacent_six_hour_initialization_game_pairs"] += 1
            except (KeyError, TypeError, ValueError, OSError, EOFError):
                diagnostics["adjacent_game_pair_metadata_unavailable"] += 1
    counts = {"archived_runs": len(runs), "scheduled_runs": len(scheduled),
              "manual_runs": sum(r["manifest"].get("trigger") in ("manual_local", "workflow_dispatch") for r in runs),
              "unknown_trigger_runs": sum(r["manifest"].get("trigger") not in ("schedule", "manual_local", "workflow_dispatch") for r in runs),
              "verified_runs": sum(r["integrity"]["verified"] for r in runs),
              "independently_audited_runs": sum(r["integrity"].get("independently_audited", False) for r in runs),
              "distinct_requested_initializations": len({r["manifest"].get("requested_run") for r in runs if r["manifest"].get("requested_run")}),
              "designated_games": len(decisions),
              **{key: sum(d[key] for d in decisions) for key in ("metadata_input_eligible", "source_receipt_verified_input_eligible", "independently_audited_input_eligible", "probability_input_eligible", "movement_input_eligible")},
              "movement_targets_inspected": 0, "outcomes_inspected": 0,
              "adjacent_six_hour_initialization_game_pairs": diagnostics["adjacent_six_hour_initialization_game_pairs"]}
    return {"counts": counts, "collection_profiles": dict(sorted(profile_counts.items())),
            "diagnostics": dict(sorted(diagnostics.items())), "decisions": decisions,
            "exclusion_counts": dict(sorted(Counter(e for d in decisions for e in d["exclusions"]).items()))}


def load_runs(root, *, verify=True):
    """Load archive order and integrity evidence without selecting observations.

    The shared Integrity instance retains verified receipt metadata for downstream
    callers. Known failed/empty runs remain ordering barriers; unreadable files
    are returned separately so callers can preserve fail-closed enumeration.
    """
    root = root.resolve()
    integrity = Integrity(root)
    runs, failures = [], []
    for path in sorted((root / "model" / BASE / "runs").glob("*.json")):
        try:
            m = json.loads(path.read_text())
            stamp(m["capture_started_at"])
            profile(m)
            if not m.get("cohort_path"):
                # A known failed/empty invocation remains in temporal order. It
                # can block the next input pair but cannot designate unknown games.
                runs.append({"manifest": m, "cohort": {"games": []}, "rows": {},
                             "integrity": {"verified": False, "errors": ["preweather_cohort_unavailable"]},
                             "path": str(path.relative_to(root)), "sha256": sha(path.read_bytes()), "cohort_sha256": None})
                continue
            cpath = local_path(root / "model", m["cohort_path"], BASE / "cohorts")
            cohort = json.loads(cpath.read_text())
            require(len({str(g["game_id"]) for g in cohort["games"]}) == len(cohort["games"]), "duplicate cohort game")
            require(len({str(r["game_id"]) for r in m["rows"]}) == len(m["rows"]), "duplicate manifest game")
            runs.append({"manifest": m, "cohort": cohort, "rows": {str(r["game_id"]): r for r in m["rows"]},
                         "integrity": integrity.verify(path, m, cohort) if verify else {'verified': False, 'deferred': True},
                         "path": str(path.relative_to(root)),
                         "sha256": sha(path.read_bytes()), "cohort_sha256": sha(cpath.read_bytes())})
        except (KeyError, TypeError, ValueError, OSError, EOFError) as error:
            failures.append({"path": str(path.relative_to(root)), "reason": str(error)})
    return runs, integrity, failures


def execute(root):
    root = root.resolve()
    runs, integrity, failures = load_runs(root)
    result = inventory(runs, integrity.receipt, complete_enumeration=not failures)
    result.update(schema_version="weather-revision-model-inventory-v1", no_network=True,
                  numeric_weather_fields_accessed=False, labels_extracted=False, models_fitted=False,
                  active_policy_changed=False, design_sha256=sha((root / DESIGN).read_bytes()),
                  script_sha256=sha(Path(__file__).read_bytes()), unreadable_archives=failures,
                  original_receipt_envelopes_and_opaque_bodies_verified=len(integrity.receipts),
                  archived_run_enumeration_complete=not failures,
                  intended_scheduler_invocation_enumeration_available=False,
                  movement_target_availability_evaluated=False)
    result["runs"] = [{"path": r["path"], "manifest_sha256": r["sha256"], "cohort_sha256": r["cohort_sha256"],
                       "collection_profile": r["manifest"].get("collection_profile", "pilot"),
                       **{k: r["manifest"].get(k) for k in ("run_id", "run_attempt", "trigger", "capture_started_at", "capture_completed_at", "requested_run", "status", "git_commit", "counts")},
                       "integrity": r["integrity"]} for r in sorted(runs, key=lambda r: r["manifest"]["capture_started_at"])]
    result["limitations"] = [
        "First archived scheduled capture is observable; missing entire scheduler invocations cannot be inferred from this archive. No nominal slot is reconstructed.",
        "Designations precede forecast/quote availability and cannot be replaced by later successful captures. An absent immediate predecessor is not replaced by an older successful row.",
        "This inventory verifies metadata links, opaque response hashes and recorded Git sources. It does not independently reconstruct numeric weather or quote fields from raw bodies. Matching independent semantic audits are separately counted extra evidence, not a prerequisite for future source/receipt-verified inputs. A known audited manifest that changes is rejected.",
        "Requested initialization and receipt timestamps do not independently certify model vintage, first publication, sportsbook acceptance or continuous offer availability.",
        "Same-initialization body changes are response-provenance diagnostics. Counts describe overlapping game pairs, not independent weather revisions or football weeks.",
        "Movement input coverage is not a completed movement-target dataset: no next-capture price change or final outcome was read or calculated.",
        "Pilot and separately authorized season collection profiles retain their original windows and protocol identities; legacy archives without a profile are pilot. Neither collection changes the four active policies.",
        "No training/test boundaries are frozen by this inventory. The design requires at least 60 distinct games and two completed weeks before experimental fitting, followed by separately registered evaluation. This operational minimum does not establish confidence, profitability or eligibility for promotion."]
    return result


def markdown(data):
    c = data["counts"]
    lines = ["# Label-free weather-revision model inventory", "",
             f"{c['archived_runs']} archived captures: {c['scheduled_runs']} scheduled and {c['manual_runs']} manual. "
             f"They contain {c['distinct_requested_initializations']} distinct requested GFS initialization(s). "
             f"There are **{c['designated_games']} designated primary games**, {c['probability_input_eligible']} source/receipt-verified probability-input pairs "
             f"and {c['movement_input_eligible']} source/receipt-verified movement-input pairs. No model is fitted and no edge is estimated.", "",
             "The observable selection rule is the first archived scheduled capture starting 24–48 hours before the pre-weather cohort kickoff. "
             "It is selected before weather, quotes or audit availability are checked. The immediately preceding archived scheduled capture must be 4–8 hours older, "
             "with a requested initialization exactly six hours earlier and unchanged context, coordinates, request options and valid hours. "
             "Missing data excludes that designated game; a later successful capture or an older successful predecessor cannot replace it.", "",
             "| Capture start (UTC) | Profile / trigger | Requested initialization (UTC) | Cohort | Weather available | Both books | Weather + both books | Integrity |",
             "|---|---|---|---:|---:|---:|---:|---|"]
    for r in data["runs"]:
        counts = r["counts"] or {}
        lines.append(f"| {r['capture_started_at']} | {r['collection_profile']} / {r['trigger']} | {r['requested_run']} | {counts.get('cohort_games', 0)} | {counts.get('weather_available_games', 0)} | {counts.get('two_book_games', 0)} | {counts.get('paired_two_book_games', 0)} | {'verified' if r['integrity']['verified'] else 'unverified'} |")
    d = data["diagnostics"]
    lines += ["", f"Across adjacent archived captures, {d.get('adjacent_same_initialization_game_pairs', 0)} games have comparable "
              f"same-initialization weather metadata: {d.get('same_initialization_changed_body_game_pairs', 0)} changed response-body hashes and "
              f"{d.get('same_initialization_unchanged_body_game_pairs', 0)} unchanged hashes. "
              f"There are {c['adjacent_six_hour_initialization_game_pairs']} comparable six-hour initialization pairs. "
              "These overlapping response observations are not independent training samples; body hashes do not measure the size or direction of a weather revision.", "",
              f"{c['verified_runs']} capture manifests pass source/receipt-integrity checks; {c['independently_audited_runs']} also match an existing independent audit's original SHA-256. "
              f"This run verified {data['original_receipt_envelopes_and_opaque_bodies_verified']} unique receipt envelopes and available opaque response bodies, "
              "and checked protocol, writer, collector, parser, team aliases and catalog against their recorded Git commits. "
              "It never decoded raw response bodies or read numeric weather measurements, game outcomes or future line changes. "
              "The machine report retains each original manifest/cohort hash, audit link/hash and recorded source reference.", "",
              "A valid two-capture input contract also requires unique full-game DraftKings pairs at both captures, FanDuel at the decision, "
              "weather followed by successful context recheck followed by new book receipts, and a decision receipt still 24–48 hours before kickoff. "
              "Half-point lines alone qualify for the probability design. Integer-line input pairs can qualify for the separate movement design, "
              "whose next-capture target availability remains unexamined in this label-free inventory.", "", "Limitations:", ""]
    lines.extend("- " + item for item in data["limitations"])
    if data["exclusion_counts"]:
        lines += ["", "Designated-game exclusions: " + json.dumps(data["exclusion_counts"], sort_keys=True) + "."]
    if data["unreadable_archives"]:
        lines += ["", "Unreadable archive entries prevent verified eligibility until resolved; see the JSON report."]
    lines += ["", "Reproduce without network access:", "", "```sh",
              "python scripts/weather_revision_model_inventory.py --root .", "```", "",
              "References: [unfitted design](WEATHER_REVISION_MODEL_DESIGN.md), "
              "[capture protocol](WEATHER_REVISION_CAPTURE_PROTOCOL.md), "
              "[first capture audit](WEATHER_REVISION_FIRST_CAPTURE_AUDIT.md), "
              "[hosted capture audit](WEATHER_REVISION_HOSTED_CAPTURE_AUDIT.md), "
              "[machine inventory](weather_revision_model_inventory.json).", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="repository root")
    args = parser.parse_args()
    result = execute(args.root)
    reports = args.root / "model/reports"
    (reports / "weather_revision_model_inventory.json").write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    (reports / "WEATHER_REVISION_MODEL_INVENTORY.md").write_text(markdown(result))
    print(json.dumps(result["counts"], sort_keys=True))


if __name__ == "__main__":
    main()
