"""Regression tests for failures that can create misleading public picks."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import numpy as np
import pandas as pd
import pytest

from ncaaf_model import runtime
from ncaaf_model.config import load_settings


NOW = datetime(2026, 9, 8, 10, 30, tzinfo=timezone.utc)
BOOKS = ("shop", "a", "b", "c")


def _events(now=NOW):
    return [{"id": "e", "home_team": "Home", "away_team": "Away",
             "commence_time": runtime.stamp(now + timedelta(days=1)),
             "bookmakers": [{"key": name, "last_update": runtime.stamp(now - timedelta(minutes=5)),
                              "markets": [{"key": "totals", "outcomes": [
                                  {"name": side, "point": 49.5 if name == "shop" else 52.5, "price": -110}
                                  for side in ("Over", "Under")]}]} for name in BOOKS]}]


def _settings(root=None):
    settings = replace(load_settings(), allowed_books=BOOKS)
    return replace(settings, root=root) if root is not None else settings


def _games(now=NOW):
    odds = runtime.quotes_from_events(_events(now), _settings(), now)
    return odds.assign(schedule_match=True, schedule_status="STATUS_SCHEDULED", canonical_kickoff=lambda x:x.commence_time, espn_game_id=1, home_prior_games=15, away_prior_games=15)


def _score(games, now=NOW, diagnostics=None):
    return runtime.score_games(games, {"challenger": np.array([60.5])}, {"sigma": 16., "family": "normal"}, now, diagnostics)


def test_nan_away_history_cannot_pass_challenger_gate():
    games = _games()
    games["away_prior_games"] = np.nan
    row = next(r for r in _score(games) if r["candidate"] == "challenger")
    assert not row["eligible"]
    assert "team_history_sparse" in row["flags"]


@pytest.mark.parametrize("price", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_price_is_rejected_before_consensus(price):
    events = _events()
    events[0]["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = price
    odds = runtime.quotes_from_events(events, _settings(), NOW)
    assert "shop" not in {quote["book"] for quote in odds.iloc[0]["quotes"]}
    games = odds.assign(schedule_match=True, schedule_status="STATUS_SCHEDULED", canonical_kickoff=lambda x:x.commence_time, espn_game_id=1, home_prior_games=15, away_prior_games=15)
    assert all(row["sportsbook"] != "shop" for row in _score(games))
    assert all(np.isfinite(row["expected_value"]) for row in _score(games))


def test_january_postseason_uses_prior_calendar_season():
    january = datetime(2027, 1, 10, 10, 30, tzinfo=timezone.utc)
    rows = _score(_games(january), now=january)
    primary = next(row for row in rows if row["candidate"] == runtime.PRICE_REFERENCE)
    assert primary["eligible"]
    assert "model_requires_new_season_validation" not in primary["flags"]


@pytest.mark.parametrize("failed_input", ["current_team_history", "current_drive_history"])
def test_missing_current_history_blocks_challenger_but_keeps_market_reference(failed_input):
    rows = _score(_games(), diagnostics={"schedule_fresh": True, "input_failures": {failed_input: "ValueError"}})
    challenger = next(row for row in rows if row["candidate"] == "challenger")
    primary = next(row for row in rows if row["candidate"] == runtime.PRICE_REFERENCE)
    assert not challenger["eligible"]
    assert "current_form_refresh_incomplete" in challenger["flags"]
    assert primary["eligible"]


def test_failed_immutable_snapshot_cannot_publish_picks_or_lock_positions(tmp_path, monkeypatch):
    settings = _settings(tmp_path / "model")
    settings.models_dir.mkdir(parents=True)
    (settings.models_dir / "score_distribution_v2.json").write_text(json.dumps({"sigma": 16., "family": "normal"}))
    schedule = pd.DataFrame([{"game_id": 1, "season": 2026, "week": 2,
                              "home_id": 1, "away_id": 2, "home_team": "Home", "away_team": "Away",
                              "game_date": runtime.stamp(NOW + timedelta(days=1)), "neutral_site": False,
                              "status": "STATUS_SCHEDULED", "home_score": np.nan, "away_score": np.nan}])
    monkeypatch.setattr(runtime, "refresh_inputs", lambda settings, now: (schedule, {"schedule_fresh": True, "input_failures": {}}))
    monkeypatch.setattr(runtime, "fetch_odds", lambda settings, now: (_events(now), {}))
    monkeypatch.setattr(runtime, "candidate_projections", lambda settings, games, now, diagnostics: (games, {}))
    actual_write = runtime.atomic_write_bytes
    def fail_archive(path, payload):
        if str(path).endswith(".json.gz"):
            raise OSError("Archive disk unavailable")
        return actual_write(path, payload)
    monkeypatch.setattr(runtime, "atomic_write_bytes", fail_archive)
    board = runtime.daily(settings=settings, now=NOW)
    assert board["status"] == "unavailable"
    assert not board["today_picks"] and not board["upcoming_picks"] and not board["forecasts"]
    assert board["results"] == []
    assert json.loads((settings.ledger_dir / "positions.json").read_text()) == []
    saved = json.loads((settings.root.parent / "site/data/board.json").read_text())
    assert saved["status"] == "unavailable" and saved["results"] == []
    assert saved["diagnostics"]["run_failure"] == "OSError"
