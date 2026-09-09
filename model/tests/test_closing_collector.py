from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from ncaaf_model.closing_collector import append_observations, attach_clv, capture_observations, collect


NOW = datetime(2026, 9, 12, 16, 0, tzinfo=timezone.utc)
KICK = NOW + timedelta(minutes=20)


def position(side="over"):
    return {"position_id": "locked", "game_id": "401", "event_id": "different-provider-id",
        "home_team": "Home", "away_team": "Away", "kickoff": KICK.isoformat(),
        "sportsbook": "draftkings", "side": side, "line": 51.5, "decimal_odds": 1.95,
        "recorded_at": (NOW - timedelta(hours=4)).isoformat(), "candidate": "paper", "clv": None}


def event(observed=NOW, line=52.5):
    return {"id": "oddsio-1", "source_event_id": "1", "home_team": "Home", "away_team": "Away",
        "commence_time": KICK.isoformat(), "bookmakers": [{"key": "draftkings", "source": "odds_api_io",
            "markets": [{"key": "totals", "last_update": (NOW - timedelta(days=2)).isoformat(),
                "observed_at": observed.isoformat(), "observation_kind": "provider_full_state",
                "outcomes": [{"name": "Over", "point": line, "decimal_price": 1.91},
                             {"name": "Under", "point": line, "decimal_price": 1.91}]}]}]}


def observation(observed=NOW, line=52.5):
    return capture_observations([event(observed, line)], observed, "data/runtime/closing/test.gz")[0]


def test_capture_distinguishes_receipt_from_unchanged_quote_and_rejects_archived_parse():
    rows = capture_observations([event()], NOW, "snapshot.gz")
    assert len(rows) == 1 and rows[0]["observed_at"] == NOW.isoformat()
    assert rows[0]["market_updated_at"] == (NOW - timedelta(days=2)).isoformat()
    old = event(NOW - timedelta(minutes=3))
    assert capture_observations([old], NOW, "snapshot.gz") == []
    missing = event()
    missing["bookmakers"][0]["markets"][0]["observed_at"] = None
    assert capture_observations([missing], NOW, "snapshot.gz") == []


def test_paired_prices_must_be_unique_and_present():
    incomplete = event()
    incomplete["bookmakers"][0]["markets"][0]["outcomes"].pop()
    assert capture_observations([incomplete], NOW, "snapshot.gz") == []
    duplicate = event()
    duplicate["bookmakers"][0]["markets"][0]["outcomes"].append(deepcopy(duplicate["bookmakers"][0]["markets"][0]["outcomes"][0]))
    assert capture_observations([duplicate], NOW, "snapshot.gz") == []


def test_clv_preserves_locked_entry_and_signs_over_under_correctly():
    original = position()
    before = deepcopy(original)
    rows, summary = attach_clv([original, position("under")], [observation()], NOW)
    assert original == before
    assert {k: v for k, v in rows[0].items() if k != "clv"} == {k: v for k, v in original.items() if k != "clv"}
    assert rows[0]["clv"]["points"] == 1
    assert rows[1]["clv"]["points"] == -1
    assert rows[0]["clv"]["lead_minutes"] == 20
    assert rows[0]["clv"]["status"] == "last_observed_pregame_proxy_not_exact_close"
    assert rows[0]["clv"]["closing_fair_ev_at_entry"] > rows[1]["clv"]["closing_fair_ev_at_entry"]
    assert summary["positions_with_clv"] == 2


@pytest.mark.parametrize("mutation", ["other_book", "other_team", "other_kickoff", "too_early", "after_kickoff", "at_kickoff", "before_entry", "future_receipt", "not_authoritative"])
def test_invalid_comparison_never_fills_clv(mutation):
    obs = observation()
    if mutation == "other_book": obs["sportsbook"] = "fanduel"
    elif mutation == "other_team": obs["away_team"] = "Other"
    elif mutation == "other_kickoff": obs["kickoff"] = (KICK + timedelta(days=1)).isoformat()
    elif mutation == "too_early": obs["observed_at"] = (KICK - timedelta(minutes=31)).isoformat()
    elif mutation == "after_kickoff": obs["observed_at"] = (KICK + timedelta(seconds=1)).isoformat()
    elif mutation == "at_kickoff": obs["observed_at"] = KICK.isoformat()
    elif mutation == "before_entry": obs["observed_at"] = (NOW - timedelta(hours=5)).isoformat()
    elif mutation == "future_receipt": obs["observed_at"] = (NOW + timedelta(minutes=1)).isoformat()
    elif mutation == "not_authoritative": obs["observation_kind"] = None
    assert attach_clv([position()], [obs], NOW)[0][0]["clv"] is None


def test_latest_observation_wins_and_ledger_deduplication_is_append_only():
    first, later = observation(), observation(NOW + timedelta(minutes=10), 53.5)
    originals = [deepcopy(first)]
    ledger = append_observations(originals, [first, later, later])
    assert len(ledger) == 2 and originals == [first]
    rows, _ = attach_clv([position()], [later, first], NOW + timedelta(minutes=11))
    assert rows[0]["clv"]["line"] == 53.5
    assert rows[0]["clv"]["lead_minutes"] == 10
    # Re-running from an older subset cannot rewrite an already better close.
    assert attach_clv(rows, [first], NOW + timedelta(minutes=11))[0] == rows


def test_no_target_collector_creates_immutable_audit_without_network(tmp_path):
    root = tmp_path / "model"
    root.mkdir()
    class NoNetwork:
        def get(self, *args, **kwargs):
            raise AssertionError("No future target should request odds")
    summary = collect(root, NOW, NoNetwork())
    assert summary["observation_count"] == 0
    assert (root / summary["snapshot"]).exists()
    with pytest.raises(FileExistsError):
        collect(root, NOW, NoNetwork())


def test_integer_entry_pushes_are_accounted_for_in_closing_price_ev():
    p = position()
    p["line"] = 52
    row = attach_clv([p], [observation(line=52)], NOW)[0][0]
    # At a symmetric integer close, pushes reduce the magnitude of expected vig loss.
    assert -.025 < row["clv"]["closing_fair_ev_at_entry"] < 0
