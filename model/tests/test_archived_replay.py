from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from ncaaf_model import archived_replay as replay


def batch():
    observed = pd.Timestamp("2026-08-30T13:00:00Z")
    return pd.DataFrame([{"game_id": 1, "archived_event_id": "provider-id", "source_sha256": "a",
        "book": book, "home_team": "Home", "away_team": "Away", "home_id": 10, "away_id": 20,
        "season": 2026, "week": 2, "neutral_site": False, "line": 50.5, "over_price": -105.,
        "under_price": -115., "market_home_spread": -3., "observed_at": observed,
        "market_updated_at": observed-pd.Timedelta(minutes=10), "kickoff": pd.Timestamp("2026-09-05T19:30:00Z")}
        for book in ("draftkings", "fanduel")])


def test_replay_preserves_real_market_clock_prices_and_game_identity():
    frame = batch()
    event = replay.event_batch(frame)[0]
    assert event["id"] == "1"
    assert event["commence_time"] == frame.iloc[0].kickoff.isoformat()
    assert event["bookmakers"][0]["markets"][0]["last_update"] == "2026-08-30T12:50:00+00:00"
    assert event["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] == -105


def test_conflicting_same_book_snapshot_is_rejected():
    frame = batch()
    with pytest.raises(ValueError, match="duplicate"):
        replay.event_batch(pd.concat([frame, frame.iloc[[0]]], ignore_index=True))


def test_replay_locks_first_signal_and_never_moves_feature_cutoff_to_game_day(monkeypatch):
    first = batch()
    later = first.copy()
    later["observed_at"] += pd.Timedelta(days=1)
    later["market_updated_at"] += pd.Timedelta(days=1)
    later["source_sha256"] = "b"
    later["line"] = 51.5
    cutoffs = []
    def features(games, history, as_of):
        assert "actual_total" not in games and "home_score" not in games
        cutoffs.append(as_of)
        return games.assign(adjusted_history_games=10, ratings_cutoff=as_of.isoformat())
    monkeypatch.setattr(replay, "adjusted_features", features)
    monkeypatch.setattr(replay, "projections", lambda f, _: {"opponent_adjusted_ridge": f.market_total.to_numpy()+4.})
    positions, forecasts, snapshots = replay.replay_cohort(pd.concat([first, later]), pd.DataFrame(), {},
        {"sigma": 16., "family": "normal"}, SimpleNamespace(allowed_books=("draftkings", "fanduel")))
    selected = [r for r in positions if r["candidate"] == "opponent_adjusted_ridge"]
    assert len(selected) == 1 and selected[0]["line"] == 50.5
    assert selected[0]["source_snapshot_sha256"] == "a"
    assert selected[0]["reconstructed_after_games"] is True
    assert len(cutoffs) == 2 and cutoffs[0] == first.iloc[0].observed_at
    assert all(c < first.iloc[0].kickoff for c in cutoffs)
    assert len([r for r in forecasts if r["candidate"] == "opponent_adjusted_ridge"]) == 1


def test_old_market_update_remains_stale_in_replay(monkeypatch):
    frame = batch()
    frame["market_updated_at"] -= pd.Timedelta(days=1)
    monkeypatch.setattr(replay, "adjusted_features", lambda g, _, as_of: g.assign(adjusted_history_games=10, ratings_cutoff=as_of.isoformat()))
    monkeypatch.setattr(replay, "projections", lambda f, _: {"opponent_adjusted_ridge": f.market_total.to_numpy()+10.})
    positions, forecasts, _ = replay.replay_cohort(frame, pd.DataFrame(), {},
        {"sigma": 16., "family": "normal"}, SimpleNamespace(allowed_books=("draftkings", "fanduel")))
    assert not positions
    assert all("quote_timestamp_missing_or_stale" in r["flags"] for r in forecasts)


def test_existing_plan_cannot_be_replaced_even_before_reading_inputs(tmp_path):
    path = tmp_path / "reports/archived_2026_replay_plan.json"
    path.parent.mkdir()
    path.write_text("original")
    with pytest.raises(ValueError, match="already exists"):
        replay.make_plan(tmp_path)
    assert path.read_text() == "original"


def frozen_fixture(tmp_path):
    paths = {"quote_sha256": "data/raw/alternative/quotes.parquet",
             "model_sha256": "data/models/opponent_adjusted_v1.json",
             "distribution_sha256": "data/models/score_distribution_v2.json",
             "source_manifest_sha256": "data/raw/alternative/archived_2026_quote_manifest.json"}
    plan = {"version": replay.REPLAY_VERSION, "candidate_version": replay.VERSION,
            "book_cohorts": {name: list(books) for name, books in replay.BOOK_COHORTS.items()},
            "quote_path": paths["quote_sha256"]}
    for key, relative in paths.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("unchanged")
        plan[key] = replay.sha(path)
    plan["plan_sha256"] = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    replay.write(tmp_path / "reports/archived_2026_replay_plan.json", plan)
    return plan, paths


def test_changed_source_manifest_rejected_before_replay(tmp_path):
    plan, paths = frozen_fixture(tmp_path)
    assert replay.read_frozen_plan(tmp_path)[0]["plan_sha256"] == plan["plan_sha256"]
    (tmp_path / paths["source_manifest_sha256"]).write_text("changed receipt provenance")
    with pytest.raises(ValueError, match="provenance manifest"):
        replay.read_frozen_plan(tmp_path)


def test_changed_candidate_version_rejected_before_replay(tmp_path, monkeypatch):
    frozen_fixture(tmp_path)
    monkeypatch.setattr(replay, "VERSION", "changed-candidate")
    with pytest.raises(ValueError, match="candidate version"):
        replay.read_frozen_plan(tmp_path)
