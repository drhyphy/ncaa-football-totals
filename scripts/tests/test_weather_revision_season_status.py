from datetime import datetime
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("season_status", Path(__file__).parents[1] / "weather_revision_status.py")
STATUS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STATUS)


class SeasonStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def at(self, value):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def manifest(self, name="season", **changes):
        value = {"schema_version": STATUS.CAPTURE_SCHEMA, "status": "ok", "run_id": "900", "run_attempt": "1",
                 "collection_profile": "season", "collection_protocol_id": "weather-revision-season-collection-v1",
                 "collection_protocol_file": "reports/WEATHER_REVISION_SEASON_COLLECTION_PROTOCOL.md",
                 "collection_window": {"start_inclusive": "2026-09-16T03:00:00Z", "end_exclusive": "2027-02-01T03:00:00Z"},
                 "scheduled_utc_slots": STATUS.SCHEDULE_UTC, "capture_started_at": "2026-09-16T07:17:00Z",
                 "capture_completed_at": "2026-09-16T07:20:00Z", "requested_run": "2026-09-16T00:00:00Z",
                 "counts": {"cohort_games": 10, "weather_available_games": 8, "two_book_games": 5, "paired_games": 7,
                            "paired_two_book_games": 4, "failed_requests": 0, "total_requests": 30},
                 "rows": [{"source_url": "https://example.invalid?apiKey=DO-NOT-PUBLISH", "temperature": 66}],
                 "failures": ["DO-NOT-PUBLISH"]}
        value.update(changes)
        path = self.root / STATUS.ARCHIVE_PATH / "runs" / (name + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return value

    def build(self, now="2026-09-16T07:22:00Z", outcome="success", **kwargs):
        return STATUS.build_status(self.root, self.at(now), outcome, profile="season", **kwargs)

    def test_season_window_is_separate_and_exact(self):
        for now, expected in [("2026-09-16T02:59:59Z", False), ("2026-09-16T03:00:00Z", True),
                              ("2027-02-01T02:59:59Z", True), ("2027-02-01T03:00:00Z", False)]:
            self.assertEqual(STATUS.decision(self.root, self.at(now), "season")["collect"], expected)
        self.assertFalse(STATUS.decision(self.root, self.at("2026-09-17T00:00:00Z"))["collect"])

    def test_initial_publication_is_scheduled_and_cannot_collect_early(self):
        now = self.at("2026-09-09T04:00:00Z")
        status, failed = STATUS.build_status(self.root, now, profile="season")
        self.assertFalse(failed)
        self.assertEqual(status["status"], "scheduled")
        self.assertEqual(status["schema_version"], "weather-revision-season-status-v1")
        self.assertEqual(status["season_end"], "2027-02-01T03:00:00Z")
        self.assertNotIn("pilot_start", status)
        self.assertEqual(status["expected_scheduled_slots"], 552)
        STATUS.write_public(self.root, status, "season")
        self.assertEqual(STATUS.decision(self.root, now, "season"), {"collect": False, "publish": False})

    def test_end_is_published_once_without_repeated_commits(self):
        now = self.at("2027-02-01T03:00:00Z")
        self.assertEqual(STATUS.decision(self.root, now, "season"), {"collect": False, "publish": True})
        value, _ = STATUS.build_status(self.root, now, profile="season")
        STATUS.write_public(self.root, value, "season")
        self.assertEqual(STATUS.decision(self.root, now, "season"), {"collect": False, "publish": False})

    def test_pilot_ignores_season_even_before_validation(self):
        self.manifest(status="outside_season", counts={"deliberately": "invalid"})
        value, failed = STATUS.build_status(self.root, self.at("2026-09-09T07:22:00Z"))
        self.assertFalse(failed)
        self.assertEqual(value["invalid_manifests"], 0)
        self.assertEqual(value["archived_runs"], 0)
        self.assertEqual(value["schema_version"], STATUS.SCHEMA)

    def test_season_ignores_legacy_pilot_counts_and_attempts(self):
        pilot = self.manifest("old", collection_profile="pilot", capture_started_at="2026-09-09T07:17:00Z",
                              capture_completed_at="2026-09-09T07:20:00Z", counts={"invalid": 1})
        pilot.pop("collection_profile")
        (self.root / STATUS.ARCHIVE_PATH / "runs/old.json").write_text(json.dumps(pilot))
        self.manifest()
        status, failed = self.build()
        self.assertFalse(failed)
        self.assertEqual(status["archived_runs"], 1)
        self.assertEqual(status["invalid_manifests"], 0)
        self.assertEqual(status["game_observation_totals"]["cohort_games"], 10)

    def test_schema_whitelists_metadata_and_fixed_protocol_link(self):
        self.manifest()
        status, failed = self.build()
        self.assertFalse(failed)
        self.assertNotIn("DO-NOT-PUBLISH", json.dumps(status))
        self.assertNotIn("temperature", json.dumps(status))
        self.assertTrue(status["collection_only"])
        self.assertFalse(status["performance_evaluated"])
        self.assertFalse(status["active_policy_changed"])
        self.assertEqual(status["links"][1]["url"], STATUS.SEASON_PLAN_URL)

    def test_wrong_window_or_protocol_is_invalid(self):
        for override in [{"collection_window": {"start_inclusive": "2026-09-09T03:00:00Z", "end_exclusive": "2027-02-01T03:00:00Z"}},
                         {"collection_protocol_id": "pilot"}, {"scheduled_utc_slots": ["06:30"]}]:
            self.manifest(**override)
            value, failed = self.build()
            self.assertTrue(failed)
            self.assertEqual(value["invalid_manifests"], 1)

    def test_missing_or_wrong_attempt_does_not_reuse_recent_success(self):
        self.manifest()
        for runid, attempt, started in [("901", "1", "2026-09-16T07:16:00Z"),
                                        ("900", "2", "2026-09-16T07:16:00Z"),
                                        ("900", "1", "2026-09-16T07:21:00Z")]:
            value, failed = self.build(expected_run_id=runid, expected_run_attempt=attempt, attempt_started_at=self.at(started))
            self.assertTrue(failed)
            self.assertFalse(value["last_workflow_attempt"]["matching_manifest"])
            self.assertEqual(value["status"], "attention")

    def test_partial_and_prerequisite_failure_publish_attention(self):
        data = self.manifest()
        self.manifest(status="partial", counts={**data["counts"], "failed_requests": 3})
        value, failed = self.build(outcome="failure", attempt_started_at=self.at("2026-09-16T07:16:00Z"))
        self.assertTrue(failed)
        self.assertEqual(value["latest"]["counts"]["failed_requests"], 3)
        value, failed = self.build(outcome="skipped", attempt_started_at=self.at("2026-09-16T07:21:00Z"))
        self.assertTrue(failed)
        self.assertEqual(value["status"], "attention")

    def test_outside_season_skip_has_no_game_observations(self):
        counts = {key: 0 for key in STATUS.COUNT_FIELDS}
        self.manifest(status="outside_season", counts=counts, requested_run=None,
                      capture_started_at="2027-02-01T03:01:00Z", capture_completed_at="2027-02-01T03:01:00Z")
        value, _ = self.build(now="2027-02-01T03:02:00Z", outcome="skipped")
        self.assertEqual(value["status"], "ended")
        self.assertEqual(value["latest"]["status"], "outside_season")

    def test_wrapper_writes_only_season_status(self):
        wrapper = Path(__file__).parents[1] / "weather_revision_season_status.py"
        subprocess.run([sys.executable, str(wrapper), "publish", "--root", str(self.root)], check=True, capture_output=True)
        self.assertTrue((self.root / STATUS.SEASON_PUBLIC_PATH).exists())
        self.assertFalse((self.root / STATUS.PUBLIC_PATH).exists())


class WorkflowContractTests(unittest.TestCase):
    def test_season_workflow_collects_explicit_profile_and_preserves_failure_publication(self):
        text = (Path(__file__).resolve().parents[2] / ".github/workflows/weather-revision-season.yml").read_text()
        self.assertIn("cron: '17 1,7,13,19 * * *'", text)
        self.assertIn("group: daily-totals-pages", text)
        self.assertIn("--profile season", text)
        self.assertIn("ODDS_API_IO_KEY: ${{ secrets.ODDS_API_IO_KEY }}", text)
        self.assertNotIn("ODDS_API_KEY:", text)
        self.assertNotIn("  push:", text)
        self.assertIn("if: always() && steps.decision.outputs.publish == 'true'", text)
        self.assertIn("needs.collect.outputs.archive_uploaded == 'true'", text)
        self.assertIn("weather_revision_model_inventory.py --root .", text)
        self.assertIn("steps.inventory.outcome", text)


if __name__ == "__main__":
    unittest.main()
