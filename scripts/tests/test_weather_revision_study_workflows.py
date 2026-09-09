"""Offline orchestration tests for study freshness, boundaries and publication."""
import ast
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
import os
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]


def workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text())


def python_step(step):
    return step["run"].split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]


class StudyWorkflowTests(unittest.TestCase):
    def test_capture_hooks_are_immediate_and_study_namespaces_are_separate(self):
        for name in ("weather-revisions.yml", "weather-revision-season.yml"):
            with self.subTest(name=name):
                doc = workflow(name)
                self.assertEqual(doc["concurrency"]["group"], "daily-totals-pages")
                steps = doc["jobs"]["collect"]["steps"]
                positions = {s["id"]: i for i, s in enumerate(steps) if "id" in s}
                self.assertEqual(positions["study"], positions["collector"] + 1)
                if "inventory" in positions:
                    self.assertLess(positions["study"], positions["inventory"])
                study = steps[positions["study"]]
                self.assertEqual(study["env"]["PYTHONPATH"], "model")
                self.assertIn('--run-id "$GITHUB_RUN_ID" --run-attempt "$GITHUB_RUN_ATTEMPT"', study["run"])
                self.assertNotIn("--labels-only", study["run"])
                self.assertIn("always()", study["if"])
                self.assertIn("steps.study_checks.outcome == 'success'", study["if"])
                archive = steps[positions["archive"]]["run"]
                self.assertIn("model/data/runtime/weather_revision_study", archive)
                self.assertIn("site/data/weather-revision-study.json", archive)
                self.assertNotIn("model/ledger", archive)
                self.assertNotIn("site/data/board.json", archive)
                tests = steps[positions["study_checks"]]
                self.assertEqual(tests["env"]["PYTHONPATH"], "model")
                for expected in ("test_weather_revision_models.py", "test_weather_revision_dataset.py", "test_weather_revision_study.py", "test_weather_revision_evaluation.py", "scripts/tests/test_weather_revision_study*.py"):
                    self.assertIn(expected, tests["run"])

    def test_daily_maintenance_is_labels_only_and_ends_after_report_day(self):
        doc = workflow("weather-revision-study.yml")
        triggers = doc.get("on", doc.get(True))
        self.assertEqual(triggers["schedule"], [{"cron": "15 12 * * *"}])
        self.assertNotIn("push", triggers)
        self.assertEqual(doc["concurrency"]["group"], "daily-totals-pages")
        steps = {s["id"]: s for s in doc["jobs"]["maintain"]["steps"] if "id" in s}
        tree = ast.parse(python_step(steps["window"]))
        function = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef)], type_ignores=[])
        scope = {"datetime": datetime, "timezone": timezone}
        exec(compile(function, "workflow-window", "exec"), scope)
        for text, expected in [("2026-09-08T23:59:59+00:00", False), ("2026-09-09T00:00:00+00:00", True),
                               ("2027-02-08T12:15:00+00:00", True), ("2027-02-08T23:59:59+00:00", True),
                               ("2027-02-09T00:00:00+00:00", False)]:
            self.assertEqual(scope["admitted"](datetime.fromisoformat(text)), expected)
        self.assertIn("--labels-only", steps["study"]["run"])
        self.assertNotIn("secrets", json.dumps(doc))
        self.assertNotIn("weather_revision_collector", json.dumps(doc))
        self.assertNotIn("model/ledger", steps["archive"]["run"])
        self.assertIn("steps.attention.outcome == 'success'", steps["archive"]["if"])
        self.assertIn("always()", doc["jobs"]["deploy"]["if"])

    def test_failure_fallback_preserves_prior_counts_without_inventing_successful_zero(self):
        doc = workflow("weather-revision-study.yml")
        step = next(s for s in doc["jobs"]["maintain"]["steps"] if s.get("id") == "attention")
        code = compile(python_step(step), "workflow-attention", "exec")
        with tempfile.TemporaryDirectory() as directory:
            prior = Path.cwd()
            os.chdir(directory)
            self.addCleanup(os.chdir, prior)
            path = Path("site/data/weather-revision-study.json")
            exec(code, {})
            data = json.loads(path.read_text())
            self.assertEqual(data["status"], "attention")
            self.assertEqual(data["observations"], {})
            self.assertEqual(data["paper"], {})
            path.write_text(json.dumps({"observations": {"total": 62}, "paper": {"locked": 3, "settled": 2, "profit_units": -.2}, "error": "DO NOT COPY"}))
            exec(code, {})
            data = json.loads(path.read_text())
            self.assertEqual(data["observations"]["total"], 62)
            self.assertEqual(data["paper"]["profit_units"], -.2)
            self.assertFalse(data["evidence"]["edge_established"])
            self.assertNotIn("DO NOT COPY", json.dumps(data))


if __name__ == "__main__":
    unittest.main()
