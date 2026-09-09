from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pandas as pd

from ncaaf_model.weather_publishing import select_paper_weather, publish_weather, VERSION

NOW = datetime(2026, 9, 12, 10, 35, tzinfo=timezone.utc)


def fixture():
    quotes = [{"book": book, "line": line, "under_price": price, "fresh": True,
               "freshness_basis": "provider_full_state_receipt", "quote_time": NOW.isoformat()}
              for book, line, price in [("draftkings", 50.5, -110), ("fanduel", 51.5, -105)]]
    games = pd.DataFrame([{"game_id": 123, "home_team": "Home", "away_team": "Away",
        "schedule_match": True, "schedule_status": "STATUS_SCHEDULED",
        "canonical_kickoff": "2026-09-12T19:30:00Z", "commence_time": "2026-09-12T19:30:00Z", "quotes": quotes}])
    features = [{"game_id": "123", "weather_rule_match": True, "feature_status": "available", "flags": []}]
    return games, features


def test_weather_can_qualify_at_observed_prices_without_invented_probabilities():
    games, features = fixture()
    row = select_paper_weather(games, features, NOW)[0]
    assert row["eligible"] and row["sportsbook"] == "fanduel" and row["line"] == 51.5
    assert row["win_probability"] is None and row["expected_value"] is None
    assert row["paper_units"] == 1 and not row["execution_confirmed"]


def test_weather_rejects_bad_price_lower_line_single_book_and_stale_receipts():
    games, features = fixture()
    games.iloc[0]["quotes"][1]["under_price"] = -115
    assert not select_paper_weather(games, features, NOW)[0]["eligible"]
    games, features = fixture()
    games.iloc[0]["quotes"].pop()
    assert "two_current_books_required" in select_paper_weather(games, features, NOW)[0]["flags"]
    games, features = fixture()
    assert not select_paper_weather(games, features, NOW + timedelta(minutes=3))[0]["eligible"]


def test_weather_rejects_unknown_features_started_games_and_future_days():
    games, features = fixture()
    features[0]["weather_rule_match"] = None
    assert not select_paper_weather(games, features, NOW)[0]["eligible"]
    features[0]["weather_rule_match"] = True
    games["schedule_status"] = "STATUS_FINAL"
    assert not select_paper_weather(games, features, NOW)[0]["eligible"]
    games, features = fixture()
    games["canonical_kickoff"] = games["commence_time"] = "2026-09-13T19:30:00Z"
    assert not select_paper_weather(games, features, NOW)[0]["eligible"]


def test_weather_locks_first_price_and_grades_in_its_own_version(tmp_path):
    settings = SimpleNamespace(root=tmp_path / "model", ledger_dir=tmp_path / "model/ledger", reports_dir=tmp_path / "model/reports")
    settings.ledger_dir.mkdir(parents=True)
    games, features = fixture()
    state = {"status": "ok", "features": features, "message": "Inspected"}
    pending = pd.DataFrame([{"game_id": 123, "status": "STATUS_SCHEDULED", "home_score": None, "away_score": None}])
    first = publish_weather(settings, pending, games, state, NOW)
    assert first["performance"]["pending"] == 1
    games.iloc[0]["quotes"][1]["line"] = 52.5
    publish_weather(settings, pending, games, state, NOW + timedelta(seconds=20))
    positions = json.loads((settings.ledger_dir / "weather_positions.json").read_text())
    assert len(positions) == 1 and positions[0]["line"] == 51.5
    final = pd.DataFrame([{"game_id": 123, "status": "STATUS_FINAL", "home_score": 24, "away_score": 20}])
    graded = publish_weather(settings, final, pd.DataFrame(), {**state, "features": []}, NOW + timedelta(days=1))
    assert graded["performance"]["model_version"] == VERSION
    assert graded["performance"]["wins"] == 1
    assert graded["performance"]["profit_units"] == 100/105
    assert not (settings.ledger_dir / "positions.json").exists()
