from datetime import datetime
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("schedule_state", Path(__file__).parents[1] / "schedule_state.py")
state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state)


class PublicationTests(unittest.TestCase):
    def board(self, generated, date="2026-09-12"):
        return {"schema_version": 1, "status": "ok", "date": date, "generated_at": generated, "today_picks": [{"game_id": "1"}]}

    def test_success_guard_observes_eastern_run_time(self):
        now = datetime.fromisoformat("2026-09-12T10:45:00+00:00")
        self.assertTrue(state.already_succeeded(self.board("2026-09-12T10:31:00Z"), now))
        self.assertFalse(state.already_succeeded(self.board("2026-09-12T10:29:00Z"), now))
        self.assertFalse(state.already_succeeded(self.board("2026-09-11T10:31:00Z", "2026-09-11"), now))

    def test_winter_schedule_uses_est(self):
        now = datetime.fromisoformat("2026-11-07T11:45:00+00:00")
        self.assertTrue(state.already_succeeded(self.board("2026-11-07T11:31:00Z", "2026-11-07"), now))
        self.assertFalse(state.already_succeeded(self.board("2026-11-07T10:31:00Z", "2026-11-07"), now))

    def test_failures_never_skip_retry(self):
        now = datetime.fromisoformat("2026-09-12T10:45:00+00:00")
        board = self.board("2026-09-12T10:31:00Z")
        board["status"] = "unavailable"
        self.assertFalse(state.already_succeeded(board, now))

    def test_failed_runtime_clears_picks_preserves_evidence(self):
        now = datetime.fromisoformat("2026-09-12T10:45:00+00:00")
        board = self.board("2026-09-12T10:31:00Z")
        board["performance"] = {"bets": 5}
        result, failed = state.publication_board(board, "failure", now)
        self.assertTrue(failed)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["today_picks"], [])
        self.assertEqual(result["performance"], {"bets": 5})

    def test_missing_output_becomes_publishable_unavailable_board(self):
        now = datetime.fromisoformat("2026-09-12T10:45:00+00:00")
        result, failed = state.publication_board({}, "success", now)
        self.assertTrue(failed)
        self.assertEqual(result["date"], "2026-09-12")
        self.assertEqual(result["schema_version"], 1)

    def test_naive_and_future_timestamps_do_not_count_as_success(self):
        now = datetime.fromisoformat("2026-09-12T10:45:00+00:00")
        for generated in ["2026-09-12T10:31:00", "2026-09-12T11:00:00Z", None, "broken"]:
            self.assertFalse(state.already_succeeded(self.board(generated), now))


if __name__ == "__main__":
    unittest.main()
