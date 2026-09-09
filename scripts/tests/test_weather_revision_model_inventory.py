"""Synthetic timing/selection and immutable-source tests; no network or labels."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import gzip
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("revision_inventory", Path(__file__).parents[1] / "weather_revision_model_inventory.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class UnreadableMeasurement:
    def __float__(self):
        raise AssertionError("numeric weather read")


def capture(hour=7, trigger="schedule", line=54.5, day=9):
    start = datetime(2026, 9, day, hour, 17, tzinfo=timezone.utc)
    kickoff = f"2026-09-{day + 2:02}T08:17:00Z"
    init = start.replace(hour=((hour - 6) // 6) * 6, minute=0)
    gid, rid = "1", str(hour)
    when = lambda seconds: module.iso(start + timedelta(seconds=seconds))
    cohort = {"game_id": gid, "home_id": "2", "away_id": "3", "home_team": "Home", "away_team": "Away",
              "kickoff": kickoff, "inventory_venue_id": "4", "inventory_neutral_site": False}
    context = {**{k: v for k, v in cohort.items() if not k.startswith("inventory_")},
               "venue_id": "4", "neutral_site": False, "indoor": False, "state": "pre"}
    cid = module.digest(context)
    row = {**cohort, "context": context, "context_id": cid, "context_recheck_ok": True,
           "context_recheck_receipt": rid + "-recheck", "context_recheck_received_at": when(5), "quotes": [], "pairs": []}
    venue = {"venue_id": "4", "latitude": 38., "longitude": -78.}
    params = {"models": "gfs_global", "run": init.strftime("%Y-%m-%dT%H:%M"), "latitude": 38., "longitude": -78.,
              "forecast_days": 8, "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m",
              "temperature_unit": "fahrenheit", "timezone": "GMT", "wind_speed_unit": "mph"}
    url = "https://single-runs-api.open-meteo.com/v1/forecast"
    wpath = rid + "-weather"
    request = {"parameters": params, "product": "single_run", "requested_run": module.iso(init),
               "request_key": rid + "-key", "source_url": url, "venue": venue}
    measurement = {"model": "gfs_global", "product": "single_run", "requested_run": module.iso(init), "kickoff": kickoff,
                   "request_key": rid + "-key", "source_url": url, "venue": venue, "parser_version": "test-v1",
                   "valid_hours_utc": [f"2026-09-{day + 2:02}T{h:02}:00:00Z" for h in range(8, 12)],
                   "receipt_path": wpath, "body_sha256": rid + "-weather-body", "requested_at": when(2), "received_at": when(3),
                   "temperature_f": UnreadableMeasurement(), "wind_mph_four_hour_mean": UnreadableMeasurement()}
    row["single_run"] = {"measurement": measurement, "receipt_path": wpath, "request_spec": request}
    receipts = {wpath: {"status_code": 200, "transport_error": None, "body_sha256": rid + "-weather-body",
                       "requested_at": when(2), "received_at": when(3), "request": {"url": url, "params": params}},
                rid + "-recheck": {"status_code": 200, "transport_error": None, "requested_at": when(4), "received_at": when(5)}}
    for book in ("draftkings", "fanduel"):
        qp = rid + "-" + book
        q = {"sportsbook": book, "game_id": gid, "market": "Totals", "period": "full_game", "source": "odds_api_io",
             "observation_kind": "provider_full_state", "line": line, "over_decimal_odds": 1.91, "under_decimal_odds": 1.91,
             "receipt_path": qp, "body_sha256": qp + "-body", "requested_at": when(6), "observed_at": when(7)}
        q["quote_id"] = module.digest(q)
        row["quotes"].append(q)
        receipts[qp] = {"status_code": 200, "transport_error": None, "requested_at": when(6), "received_at": when(7), "body_sha256": q["body_sha256"]}
        row["pairs"].append({"quote_id": q["quote_id"], "quote_receipt": qp, "weather_receipt": wpath,
                             "context_id": cid, "context_recheck_receipt": rid + "-recheck", "sportsbook": book})
    manifest = {"run_id": rid, "run_attempt": "1", "trigger": trigger, "capture_started_at": when(0),
                "capture_completed_at": when(8), "requested_run": module.iso(init)}
    return {"manifest": manifest, "cohort": {"games": [cohort]}, "rows": {gid: row}, "integrity": {"verified": True}}, receipts


class SelectionTests(unittest.TestCase):
    def run_inventory(self, *runs):
        receipts = {k: v for _, rr in runs for k, v in rr.items()}
        return module.inventory([r for r, _ in runs], receipts.__getitem__)

    def test_valid_pair_is_first_eligible_scheduled_capture_and_values_are_unread(self):
        data = self.run_inventory(capture(7), capture(13), capture(19))
        self.assertEqual(data["counts"]["designated_games"], 1)
        self.assertEqual(data["decisions"][0]["run_id"], "13")
        self.assertEqual(data["counts"]["probability_input_eligible"], 1)
        self.assertEqual(data["counts"]["movement_targets_inspected"], 0)

    def test_manual_observations_cannot_designate(self):
        data = self.run_inventory(capture(7, "manual_local"), capture(13, "workflow_dispatch"))
        self.assertEqual(data["counts"]["scheduled_runs"], 0)
        self.assertEqual(data["counts"]["designated_games"], 0)

    def test_missing_first_weather_does_not_select_later_success(self):
        first = capture(13)
        first[0]["rows"]["1"]["single_run"] = None
        data = self.run_inventory(capture(7), first, capture(19))
        self.assertEqual(data["counts"]["designated_games"], 1)
        self.assertEqual(data["decisions"][0]["run_id"], "13")
        self.assertEqual(data["counts"]["probability_input_eligible"], 0)
        self.assertIn("single_run_unavailable", data["decisions"][0]["exclusions"])

    def test_immediate_predecessor_is_not_replaced(self):
        a, b, c = capture(7), capture(13), capture(19)
        # Only t2 has a decision-window cohort; t1 exists but has no game.
        b[0]["rows"] = {}
        b[0]["cohort"]["games"] = []
        data = self.run_inventory(a, b, c)
        self.assertEqual(data["decisions"][0]["preceding_run_id"], "13")
        self.assertIn("game_missing_from_immediate_capture", data["decisions"][0]["exclusions"])

    def test_same_initialization_is_not_a_revision(self):
        a, b = capture(7), capture(13)
        b[0]["manifest"]["requested_run"] = a[0]["manifest"]["requested_run"]
        r = b[0]["rows"]["1"]["single_run"]
        r["measurement"]["requested_run"] = r["request_spec"]["requested_run"] = a[0]["manifest"]["requested_run"]
        r["request_spec"]["parameters"]["run"] = "2026-09-09T00:00"
        data = self.run_inventory(a, b)
        self.assertIn("initializations_not_exactly_six_hours_apart", data["decisions"][0]["exclusions"])
        self.assertEqual(data["diagnostics"]["adjacent_same_initialization_game_pairs"], 1)

    def test_changed_request_options_are_unavailable(self):
        b = capture(13)
        b[0]["rows"]["1"]["single_run"]["request_spec"]["parameters"]["elevation"] = 200
        data = self.run_inventory(capture(7), b)
        self.assertIn("forecast_request_options_differ_from_collection_contract", data["decisions"][0]["exclusions"])

    def test_reschedule_context_is_not_combined(self):
        b = capture(13)
        row = b[0]["rows"]["1"]
        row["context"]["home_team"] = "Changed home"
        row["context_id"] = module.digest(row["context"])
        data = self.run_inventory(capture(7), b)
        self.assertIn("changed_context", data["decisions"][0]["exclusions"])

    def test_quote_before_context_recheck_is_unavailable(self):
        b = capture(13)
        b[1]["13-recheck"]["requested_at"] = "2026-09-09T13:17:01Z"
        data = self.run_inventory(capture(7), b)
        self.assertIn("weather_recheck_quote_order", data["decisions"][0]["exclusions"])

    def test_multiple_lines_are_not_cherry_picked(self):
        b = capture(13)
        b[0]["rows"]["1"]["quotes"].append(deepcopy(b[0]["rows"]["1"]["quotes"][0]))
        data = self.run_inventory(capture(7), b)
        self.assertIn("unique_draftkings_main_pair_unavailable", data["decisions"][0]["exclusions"])

    def test_integer_contract_only_counts_movement_inputs(self):
        data = self.run_inventory(capture(7), capture(13, line=54))
        self.assertEqual(data["counts"]["movement_input_eligible"], 1)
        self.assertEqual(data["counts"]["probability_input_eligible"], 0)

    def test_unknown_integrity_is_visible_but_not_verified_input(self):
        b = capture(13)
        b[0]["integrity"]["verified"] = False
        data = self.run_inventory(capture(7), b)
        self.assertEqual(data["counts"]["metadata_input_eligible"], 1)
        self.assertEqual(data["counts"]["source_receipt_verified_input_eligible"], 0)

    def test_valid_season_pair_counts_without_optional_manual_audit(self):
        runs = [capture(7, day=16), capture(13, day=16)]
        for r, _ in runs:
            r["manifest"].update(collection_profile="season", collection_protocol_id="weather-revision-season-collection-v1",
                collection_protocol_file=module.SEASON_PROTOCOL, scheduled_utc_slots=module.SLOTS,
                collection_window={"start_inclusive": "2026-09-16T03:00:00Z", "end_exclusive": "2027-02-01T03:00:00Z"})
        data = self.run_inventory(*runs)
        self.assertEqual(data["counts"]["probability_input_eligible"], 1)
        self.assertEqual(data["counts"]["source_receipt_verified_input_eligible"], 1)
        self.assertEqual(data["counts"]["independently_audited_input_eligible"], 0)
        self.assertEqual(data["collection_profiles"], {"season": 2})

    def test_capture_boundary_is_inclusive_but_actual_quote_must_stay_in_window(self):
        a, b = capture(7), capture(13)
        for run, _ in (a, b):
            row = run["rows"]["1"]
            kickoff = "2026-09-10T13:17:00Z"  # t1 start is exactly 24h early, its quote is later.
            row["kickoff"] = row["context"]["kickoff"] = run["cohort"]["games"][0]["kickoff"] = kickoff
            row["context_id"] = module.digest(row["context"])
            record = row["single_run"]["measurement"]
            record["kickoff"] = kickoff
            record["valid_hours_utc"] = [f"2026-09-10T{h:02}:00:00Z" for h in range(13, 17)]
            for p in row["pairs"]:
                p["context_id"] = row["context_id"]
        # The predecessor's cohort was out of this fixture's frozen cohort window.
        a[0]["cohort"]["games"] = []
        data = self.run_inventory(a, b)
        self.assertIn("actual_quote_decision_outside_24_48_hours", data["decisions"][0]["exclusions"])

    def test_missing_entire_parsable_archive_blocks_verified_eligibility(self):
        a, b = capture(7), capture(13)
        receipts = {**a[1], **b[1]}
        data = module.inventory([a[0], b[0]], receipts.__getitem__, complete_enumeration=False)
        self.assertEqual(data["counts"]["metadata_input_eligible"], 1)
        self.assertEqual(data["counts"]["source_receipt_verified_input_eligible"], 0)


class IntegrityTests(unittest.TestCase):
    def test_season_profile_requires_its_own_protocol_and_exact_window(self):
        m = {"collection_profile": "season", "collection_protocol_id": "weather-revision-season-collection-v1",
             "collection_protocol_file": module.SEASON_PROTOCOL,
             "collection_window": {"start_inclusive": "2026-09-16T03:00:00Z", "end_exclusive": "2027-02-01T03:00:00Z"},
             "scheduled_utc_slots": module.SLOTS}
        self.assertEqual(module.profile(m), ("season", module.END, module.SEASON_END))
        m["collection_window"]["end_exclusive"] = "2027-03-01T03:00:00Z"
        with self.assertRaisesRegex(ValueError, "collection_window_identity"):
            module.profile(m)

    def test_opaque_body_digest_is_checked_without_json_decoding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "model/reports").mkdir(parents=True)
            base = root / "model" / module.BASE
            (base / "receipts").mkdir(parents=True)
            (base / "bodies").mkdir()
            raw = b"not JSON; no weather or labels are decoded"
            body_path = module.BASE / "bodies/test.body.gz"
            (root / "model" / body_path).write_bytes(gzip.compress(raw))
            receipt = {"schema_version": "raw-http-receipt-v1", "requested_at": "2026-09-09T07:00:00Z",
                       "received_at": "2026-09-09T07:00:01Z", "body_path": str(body_path), "body_sha256": module.sha(raw), "body_size_bytes": len(raw)}
            relative = str(module.BASE / "receipts" / (module.digest(receipt) + ".json"))
            receipt["receipt_path"] = relative
            (root / "model" / relative).write_text(json.dumps(receipt))
            verified = module.Integrity(root)
            self.assertEqual(verified.receipt(relative)["body_sha256"], module.sha(raw))
            (root / "model" / body_path).write_bytes(gzip.compress(b"changed"))
            with self.assertRaisesRegex(ValueError, "opaque body hash"):
                module.Integrity(root).receipt(relative)

    def test_wrong_source_blob_is_rejected_without_head_substitution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "model/reports").mkdir(parents=True)
            path = root / "manifest.json"
            path.write_text("{}")
            data = {"run_id": "x", "run_attempt": "1", "git_commit": "a" * 40,
                    "provenance": {p: module.sha(b"old source") for p in module.REQUIRED_SOURCES}}
            with patch.object(module.subprocess, "check_output", return_value=b"different HEAD bytes"):
                value = module.Integrity(root).verify(path, data, {})
            self.assertFalse(value["verified"])
            self.assertTrue(any(e.startswith("recorded_source_blob_missing_or_mismatched") for e in value["errors"]))

    def test_valid_source_receipts_do_not_need_an_optional_audit_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "model/reports").mkdir(parents=True)
            path = root / "manifest.json"
            path.write_text("{}")
            m = {"schema_version": "weather-revision-capture-v1", "run_id": "new", "run_attempt": "1",
                 "git_commit": "a" * 40, "capture_started_at": "2026-09-16T07:17:00Z",
                 "capture_completed_at": "2026-09-16T07:17:03Z", "inventory_receipt": "inventory", "rows": [], "receipts": [],
                 "counts": {"cohort_games": 0}, "provenance": {p: module.sha(b"source") for p in module.REQUIRED_SOURCES}}
            cohort = {"capture_started_at": m["capture_started_at"], "inventory_receipt": "inventory",
                      "frozen_at": "2026-09-16T07:17:02Z", "games": []}
            inventory_receipt = {"purpose": "official_frozen_cohort", "status_code": 200, "received_at": "2026-09-16T07:17:01Z"}
            with patch.object(module.subprocess, "check_output", return_value=b"source"), patch.object(module.Integrity, "receipt", return_value=inventory_receipt):
                value = module.Integrity(root).verify(path, m, cohort)
            self.assertTrue(value["verified"], value["errors"])
            self.assertFalse(value["independently_audited"])

    def test_known_original_audit_hash_cannot_be_silently_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "model/reports").mkdir(parents=True)
            path = root / "manifest.json"
            path.write_text("{}")
            checker = module.Integrity(root)
            checker.audits = [(Path("audit"), {"run_id": "new", "run_attempt": "1", "manifest_sha256": "old-original"})]
            value = checker.verify(path, {"run_id": "new", "run_attempt": "1"}, {})
            self.assertIn("known_independent_audit_failed_or_original_manifest_changed", value["errors"])


if __name__ == "__main__":
    unittest.main()
