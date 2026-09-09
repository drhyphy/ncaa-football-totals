from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("weather_revision_status", Path(__file__).parents[1] / "weather_revision_status.py")
STATUS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STATUS)


class PilotStatusTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def at(self, value):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def test_fixed_window_is_inclusive_start_exclusive_end(self):
        for clock, expected in [("2026-09-09T02:59:59Z", False), ("2026-09-09T03:00:00Z", True),
                                ("2026-09-16T02:59:59Z", True), ("2026-09-16T03:00:00Z", False)]:
            self.assertEqual(STATUS.decision(self.root, self.at(clock))["collect"], expected)
        self.assertEqual(STATUS.phase(self.at("2026-09-08T23:00:00-04:00")), "active")

    def test_expired_pilot_publishes_only_one_final_transition(self):
        now = self.at("2026-09-16T03:00:00Z")
        self.assertEqual(STATUS.decision(self.root, now), {"collect": False, "publish": True})
        STATUS.write_public(self.root, {"schema_version": STATUS.SCHEMA, "phase": "ended"})
        self.assertEqual(STATUS.decision(self.root, now), {"collect": False, "publish": False})
        self.assertFalse(STATUS.decision(self.root, self.at("2027-01-01T00:00:00Z"))["publish"])

    def test_active_window_does_not_skip_runs_after_a_previous_success(self):
        STATUS.write_public(self.root, {"schema_version": STATUS.SCHEMA, "phase": "active"})
        self.assertEqual(STATUS.decision(self.root, self.at("2026-09-10T07:17:00Z")), {"collect": True, "publish": True})

    def test_naive_time_is_not_assumed_to_be_utc(self):
        self.assertIsNone(STATUS.timestamp("2026-09-09T03:00:00"))
        with self.assertRaisesRegex(ValueError, "UTC-aware"):
            STATUS.phase(datetime(2026, 9, 9, 3))

    def manifest(self, name="one", **changes):
        data = {"schema_version": STATUS.CAPTURE_SCHEMA, "run_id": name, "status": "ok",
                "capture_started_at": "2026-09-09T07:17:00Z", "capture_completed_at": "2026-09-09T07:20:00Z",
                "requested_run": "2026-09-09T00:00:00Z", "counts": {"cohort_games": 10, "weather_available_games": 7,
                "two_book_games": 8, "paired_games": 6, "failed_requests": 0, "total_requests": 14},
                "rows": [{"request_url": "https://example.invalid?apiKey=DO-NOT-PUBLISH", "weather": 55}],
                "error": "DO-NOT-PUBLISH", "response_headers": {"Authorization": "DO-NOT-PUBLISH"}}
        data.update(changes)
        directory = self.root / STATUS.ARCHIVE_PATH / "runs"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}.json"
        path.write_text(json.dumps(data))
        return data

    def test_metadata_whitelist_never_copies_payload_credentials_or_errors(self):
        self.manifest()
        result, failed = STATUS.build_status(self.root, self.at("2026-09-09T07:22:00Z"), "success")
        self.assertFalse(failed)
        self.assertEqual(result["status"], "current")
        self.assertEqual(result["latest"]["counts"]["paired_games"], 6)
        self.assertEqual(len(result["latest"]["manifest_sha256"]), 64)
        self.assertNotIn("DO-NOT-PUBLISH", json.dumps(result))
        self.assertNotIn("request_url", json.dumps(result))
        self.assertTrue(result["collection_only"])
        self.assertFalse(result["performance_evaluated"])
        self.assertFalse(result["active_policy_changed"])

    def test_partial_manifest_is_retained_and_workflow_reports_failure(self):
        source = self.manifest()
        source["counts"]["failed_requests"] = 2
        self.manifest(status="partial", counts=source["counts"])
        result, failed = STATUS.build_status(self.root, self.at("2026-09-09T07:22:00Z"), "failure")
        self.assertTrue(failed)
        self.assertEqual(result["status"], "attention")
        self.assertEqual(result["archived_runs"], 1)
        self.assertEqual(result["partial_or_failed_runs"], 1)
        self.assertEqual(result["latest"]["counts"]["failed_requests"], 2)

    def test_missing_failure_manifest_does_not_become_successful_empty_collection(self):
        result, failed = STATUS.build_status(self.root, self.at("2026-09-09T07:22:00Z"), "failure")
        self.assertTrue(failed)
        self.assertEqual(result["status"], "attention")
        self.assertIsNone(result["latest"])

    def test_success_without_any_manifest_is_an_error(self):
        result, failed = STATUS.build_status(self.root, self.at("2026-09-09T07:22:00Z"), "success")
        self.assertTrue(failed)
        self.assertEqual(result["status"], "attention")

    def test_empty_and_outside_pilot_states_are_distinct_from_missing_receipts(self):
        counts = {key: 0 for key in STATUS.COUNT_FIELDS}
        self.manifest(status="no_games", counts=counts, requested_run=None)
        result, failed = STATUS.build_status(self.root, self.at("2026-09-09T07:22:00Z"), "success")
        self.assertFalse(failed)
        self.assertEqual(result["latest"]["status"], "no_games")
        self.assertEqual(result["latest"]["counts"]["cohort_games"], 0)
        self.assertIsNone(result["game_observation_totals"]["total_requests"])
        self.manifest("expired", status="outside_pilot", counts=counts, requested_run=None,
                      capture_started_at="2026-09-16T03:01:00Z", capture_completed_at="2026-09-16T03:01:00Z")
        result, failed = STATUS.build_status(self.root, self.at("2026-09-16T03:02:00Z"), "skipped")
        self.assertEqual(result["status"], "ended")
        self.assertEqual(result["latest"]["status"], "outside_pilot")

    def test_counts_are_observations_and_invalid_cohort_arithmetic_is_withheld(self):
        first = self.manifest()
        self.manifest("two", capture_started_at="2026-09-09T13:17:00Z", capture_completed_at="2026-09-09T13:20:00Z")
        self.manifest("invalid", counts={**first["counts"], "paired_games": 11})
        result, failed = STATUS.build_status(self.root, self.at("2026-09-09T13:22:00Z"), "success")
        self.assertTrue(failed)
        self.assertEqual(result["archived_runs"], 2)
        self.assertEqual(result["invalid_manifests"], 1)
        self.assertEqual(result["game_observation_totals"]["cohort_games"], 20)
        self.assertIn("not distinct games or bets", result["counting_note"])

    def test_old_receipts_are_stale_even_when_public_status_is_fresh(self):
        self.manifest()
        result, failed = STATUS.build_status(self.root, self.at("2026-09-09T16:00:00Z"), "skipped")
        self.assertFalse(failed)
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["generated_at"], "2026-09-09T16:00:00Z")

    def test_future_or_reversed_receipts_are_invalid_not_latest_successes(self):
        for times in [{"capture_completed_at": "2026-09-09T06:00:00Z"},
                      {"capture_completed_at": "2026-09-09T10:00:00Z"},
                      {"requested_run": "2026-09-09T12:00:00Z"},
                      {"capture_started_at": "2026-09-08T07:17:00Z"}]:
            self.manifest(**times)
            result, failed = STATUS.build_status(self.root, self.at("2026-09-09T07:22:00Z"), "success")
            self.assertTrue(failed)
            self.assertEqual(result["invalid_manifests"], 1)
            self.assertIsNone(result["latest"])

    def test_weather_and_one_book_pair_does_not_require_two_books(self):
        original = self.manifest()
        self.manifest(counts={**original["counts"], "two_book_games": 0, "paired_games": 7, "paired_two_book_games": 0})
        result, failed = STATUS.build_status(self.root, self.at("2026-09-09T07:22:00Z"), "success")
        self.assertFalse(failed)
        self.assertEqual(result["latest"]["counts"]["paired_games"], 7)
        self.assertEqual(result["latest"]["counts"]["paired_two_book_games"], 0)

    def test_success_requires_the_current_workflow_attempt_manifest(self):
        self.manifest(run_id="700", run_attempt="1")
        now = self.at("2026-09-09T07:22:00Z")
        for started, run_id, attempt in [("2026-09-09T07:21:00Z", "700", "1"),
                                         ("2026-09-09T07:16:00Z", "701", "1"),
                                         ("2026-09-09T07:16:00Z", "700", "2")]:
            result, failed = STATUS.build_status(self.root, now, "success", self.at(started), run_id, attempt)
            self.assertTrue(failed)
            self.assertEqual(result["status"], "attention")
            self.assertFalse(result["last_workflow_attempt"]["matching_manifest"])
        result, failed = STATUS.build_status(self.root, now, "success", self.at("2026-09-09T07:16:00Z"), "700", "1")
        self.assertFalse(failed)
        self.assertTrue(result["last_workflow_attempt"]["matching_manifest"])

    def test_skipped_or_cancelled_expected_attempt_cannot_reuse_old_success(self):
        self.manifest()
        for outcome in ["skipped", "cancelled", ""]:
            result, failed = STATUS.build_status(self.root, self.at("2026-09-09T07:22:00Z"), outcome,
                                                self.at("2026-09-09T07:21:00Z"))
            self.assertTrue(failed)
            self.assertEqual(result["status"], "attention")
            self.assertEqual(result["archived_runs"], 1)


if __name__ == "__main__":
    unittest.main()
