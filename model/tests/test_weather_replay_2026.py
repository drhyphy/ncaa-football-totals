from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pandas as pd
import pytest

from ncaaf_model import weather_replay_2026 as wr


NOW = datetime(2026, 9, 5, 13, tzinfo=timezone.utc)
KICK = datetime(2026, 9, 5, 20, tzinfo=timezone.utc)


def quote(book="draftkings", observed=NOW, **changes):
    return {"book": book, "line": 50., "over_price": -110., "under_price": -110.,
        "market_updated_at": observed - timedelta(minutes=1), "archived_event_id": "abc", "source_prestate": "odds_feed_event_before_kickoff",
        "game_id": 401, "season": 2026, "week": 1, "observed_at": observed, "kickoff": KICK,
        "home_team": "Home", "away_team": "Away", "home_id": 1, "away_id": 2,
        "source": "the_odds_api", "source_path": "/private/input.json", "source_sha256": "frozen",
        "receipt_evidence": "local_metadata_hash", "receipt_hash_matches": True, **changes}


def capture():
    return wr.select_captures(pd.DataFrame([quote(), quote("fanduel")]))[0][0]


def test_first_capture_is_locked_even_when_a_later_price_is_better():
    rows = [quote(under_price=-120.), quote("fanduel", under_price=-115.),
        quote(observed=NOW + timedelta(hours=1), line=52., under_price=100., source_sha256="later"),
        quote("fanduel", observed=NOW + timedelta(hours=1), line=52., under_price=100., source_sha256="later")]
    selected, _ = wr.select_captures(pd.DataFrame(rows))
    assert len(selected) == 1 and selected[0]["decision_time"] == NOW.isoformat()
    assert selected[0]["selected_quote"] is None and not selected[0]["price_eligible"]


def test_two_books_cannot_be_paired_from_different_receipts_or_archives():
    for fd in (quote("fanduel", observed=NOW + timedelta(seconds=1)), quote("fanduel", source_sha256="other")):
        assert wr.select_captures(pd.DataFrame([quote(), fd]))[0] == []


@pytest.mark.parametrize("update", [None, NOW + timedelta(seconds=1), NOW - timedelta(minutes=61)])
def test_primary_requires_both_market_updates_within_hour(update):
    assert not wr.select_captures(pd.DataFrame([quote(), quote("fanduel", market_updated_at=update)]))[0]


def test_later_eligible_capture_only_after_first_pair_was_ineligible_for_freshness():
    rows = [quote(market_updated_at=NOW - timedelta(hours=2)), quote("fanduel"),
        quote(observed=NOW + timedelta(minutes=1), source_sha256="later"),
        quote("fanduel", observed=NOW + timedelta(minutes=1), source_sha256="later")]
    assert wr.select_captures(pd.DataFrame(rows))[0][0]["decision_time"] == (NOW + timedelta(minutes=1)).isoformat()


def test_price_policy_highest_line_above_median_before_best_price():
    rows = [quote(line=51., under_price=-110.), quote("fanduel", line=50., under_price=120.)]
    selected = wr.select_captures(pd.DataFrame(rows))[0][0]
    assert selected["reference_total"] == 50.5
    assert selected["selected_quote"]["book"] == "draftkings"
    assert "/private" not in str(selected)


def test_espn_single_book_has_own_cohort_without_invented_market_update():
    row = quote(source="espn_scoreboard", source_prestate="explicit_espn_pre; DraftKings100; current", market_updated_at=None)
    selected = wr.select_captures(pd.DataFrame([row]))[0][0]
    assert selected["cohort"] == wr.SECONDARY
    assert selected["quotes"][0]["market_updated_at"] is None


def test_before0630_or_other_calendar_day_capture_is_excluded():
    for observed in (NOW.replace(hour=10, minute=29), NOW - timedelta(days=1)):
        assert not wr.select_captures(pd.DataFrame([quote(observed=observed), quote("fanduel", observed=observed)]))[0]


def context(**changes):
    return {"game_id": "401", "home_id": "1", "away_id": "2", "kickoff": KICK.isoformat(),
        "observed_at": (NOW - timedelta(minutes=1)).isoformat(), "state": "pre", "neutral_site": False,
        "venue_id": "5", "venue_name": "Test", "indoor": False, "source_archive": "espn/abc.json",
        "source_url": "https://site.api.espn.com/scoreboard", **changes}


CATALOG = {"venues": {"5": {"latitude": 40., "longitude": -75.}}, "coordinates_source": "https://coords"}


def test_later_venue_metadata_is_explicit_assumption_and_unknown_roof_excludes():
    item, _ = wr.match_context(capture(), [context(observed_at=(NOW + timedelta(hours=1)).isoformat())], CATALOG)
    assert "not_contemporaneous_proof" in item["roof_history_status"]
    assert wr.match_context(capture(), [context(indoor=None)], CATALOG)[0] is None


@pytest.mark.parametrize("change", [{"neutral_site": True}, {"venue_id": "6"}, {"home_id": "9"},
    {"kickoff": (KICK + timedelta(minutes=30)).isoformat()}, {"indoor": True}])
def test_exact_venue_identity_and_outdoor_policy_rejects_mismatch(change):
    assert wr.match_context(capture(), [context(**change)], CATALOG)[0] is None


def test_actual_payout_and_integer_push_settlement_and_small_week_uncertainty():
    frame = pd.DataFrame({"shadow_under_flag": [True, True, True, False], "actual_total": [49, 50, 51, 40],
        "line": [50, 50, 50, 50], "decimal_odds": [2.2, 1.91, 1.91, 1.91], "kickoff": [KICK.isoformat()] * 4})
    result = wr.summarize(frame)
    assert result["rule"]["profit_units"] == pytest.approx(.2)
    assert result["rule"]["pushes"] == 1
    assert result["rule"]["roi"] == pytest.approx(.2 / 3)
    assert result["rule_roi_95_week_bootstrap"] is None


def test_plan_reads_only_quote_columns_and_cannot_be_overwritten(tmp_path, monkeypatch):
    root = tmp_path / "model"
    quotes_path = root / wr.QUOTES
    quotes_path.parent.mkdir(parents=True)
    quotes_path.write_bytes(b"fixture quote archive")
    (root / wr.QUOTE_MANIFEST).write_text(json.dumps({"artifact_sha256": hashlib.sha256(quotes_path.read_bytes()).hexdigest(), "no_outcomes_loaded": True}))
    catalog = {"schema_version": "weather-venues-v1", "coordinates_source": "https://coords",
        "venues": {str(i): {"latitude": 40., "longitude": -75.} for i in range(100)}}
    (root / wr.CATALOG).parent.mkdir(parents=True)
    (root / wr.CATALOG).write_text(json.dumps(catalog))
    calls = []
    def only_quotes(path, columns):
        assert path == quotes_path and columns == wr.QUOTE_COLUMNS
        assert not set(columns) & {"actual_total", "home_score", "away_score", "result", "profit"}
        calls.append(path)
        return pd.DataFrame([quote(), quote("fanduel")])
    monkeypatch.setattr(wr.pd, "read_parquet", only_quotes)
    monkeypatch.setattr(wr, "read_venue_context", lambda source, ids: [context()])
    plan = wr.build_plan(root, tmp_path)
    assert plan["outcomes_used"] is False and plan["weather_values_used"] is False
    assert plan["included_unique_games"] == 1 and len(calls) == 1
    assert wr.read_plan(root)["plan_sha256"] == plan["plan_sha256"]
    original = (root / wr.PLAN_PATH).read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        wr.build_plan(root, tmp_path)
    assert (root / wr.PLAN_PATH).read_bytes() == original
    edited = json.loads(original)
    edited["games"][0]["decision_time"] = KICK.isoformat()
    (root / wr.PLAN_PATH).write_text(json.dumps(edited))
    with pytest.raises(ValueError, match="hash changed"):
        wr.read_plan(root)


def test_evaluation_joins_string_weather_ids_to_numeric_outcome_ids(tmp_path, monkeypatch):
    game = {**capture(), **wr.match_context(capture(), [context()], CATALOG)[0]}
    plan = {"games": [game], "requests": [{"request_id": "fixture", "game_ids": ["401"]}],
        "plan_sha256": "locked", "cohort_counts": {wr.PRIMARY: {"venue_eligible": 1}}}
    archive = tmp_path / wr.RAW / "batches/fixture.json.gz"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"archive fixture")
    score_path = tmp_path / "data/raw/sportsdataverse/cfb_schedule_2026.parquet"
    score_path.parent.mkdir(parents=True)
    score_path.write_bytes(b"outcome source fixture")
    monkeypatch.setattr(wr, "read_plan", lambda root: plan)
    monkeypatch.setattr(wr, "read_batch", lambda path, batch: {"payload": {}, "observed_at": "2026-09-09T01:00:00Z"})
    monkeypatch.setattr(wr, "normalize_batch", lambda *args: [{"game_id": "401"}])
    # This is the production helper's string-ID return contract.
    monkeypatch.setattr(wr, "weather_flag", lambda *args, **kwargs: {"game_id": "401", "status": "shadow_only", "shadow_under_flag": True})
    monkeypatch.setattr(wr.pd, "read_parquet", lambda path, columns: pd.DataFrame({"game_id": [401], "home_score": [21], "away_score": [17], "status": ["STATUS_FINAL"]}))
    report = wr.evaluate_plan(tmp_path)
    assert report["cohorts"][wr.PRIMARY]["rule"]["wins"] == 1
    assert report["cohorts"][wr.PRIMARY]["rule"]["profit_units"] == pytest.approx(100 / 110)
