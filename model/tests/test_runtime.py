from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from ncaaf_model.distribution import probabilities, infer_center, expected_value
from ncaaf_model.runtime import quotes_from_events, score_games, record_positions, grade_positions, performance, safe_failure

NOW = datetime(2026, 9, 8, 10, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize("family", ["normal", "student_t7"])
def test_discrete_probabilities_and_integer_push(family):
    over, under, push = probabilities(52, 52, 16, family)
    assert over + under + push == pytest.approx(1)
    assert push > .01
    assert probabilities(52, 52.5, 16, family)[2] == 0
    assert probabilities(0, 0, 16, family)[1] == 0
    assert probabilities(52, 51.5, 16, family)[0] > probabilities(52, 52.5, 16, family)[0]
    center = infer_center(52, .55, 16, family)
    po, pu, _ = probabilities(center, 52, 16, family)
    assert po / (po + pu) == pytest.approx(.55)


def test_push_ev_refunds_stake():
    ev, win, push = expected_value(52, 52, -110, "over", 16)
    assert ev == pytest.approx(win * (100 / 110) - (1 - win - push))


def event(quote_time=None):
    return [{"id": "e", "home_team": "Home", "away_team": "Away", "commence_time": "2026-09-09T20:00:00Z",
             "bookmakers": [{"key": name, "last_update": quote_time,
                 "markets": [{"key": "totals", "outcomes": [
                     {"name": "Over", "point": 49.5 if name == "shop" else 52.5, "price": -110},
                     {"name": "Under", "point": 49.5 if name == "shop" else 52.5, "price": -110}]}]}
                for name in ("shop", "a", "b", "c")]}]


def games(payload):
    frame = quotes_from_events(payload, SimpleNamespace(allowed_books=("shop", "a", "b", "c")), NOW)
    return frame.assign(schedule_match=True, schedule_status="STATUS_SCHEDULED", canonical_kickoff=lambda x:x.commence_time, espn_game_id=1, home_prior_games=15, away_prior_games=15)


def score(payload):
    return score_games(games(payload), {}, {"sigma": 16, "family": "normal"}, NOW)


def test_missing_stale_and_future_timestamps_fail_closed():
    for timestamp in (None, "2026-09-08T08:00:00Z", "2026-09-08T12:00:00Z"):
        rows = score(event(timestamp))
        assert not rows[0]["eligible"]
        assert "quote_timestamp_missing_or_stale" in rows[0]["flags"]


def test_market_timestamp_takes_precedence_and_reference_excludes_execution():
    payload = event("2026-09-08T08:00:00Z")
    for book in payload[0]["bookmakers"]:
        book["markets"][0]["last_update"] = "2026-09-08T10:25:00Z"
    row = score(payload)[0]
    assert row["sportsbook"] == "shop"
    assert row["eligible"]
    assert row["reference_books"] == ["a", "b", "c"]
    assert row["consensus_total"] == pytest.approx(52.5, abs=.03)


def test_two_distinct_books_are_sufficient_and_one_is_not():
    payload = event("2026-09-08T10:25:00Z")
    payload[0]["bookmakers"] = payload[0]["bookmakers"][:2]
    assert score(payload)[0]["eligible"]
    payload[0]["bookmakers"] = payload[0]["bookmakers"][:1]
    row = score(payload)[0]
    assert not row["eligible"]
    assert "no_other_current_sportsbook" in row["flags"]


def test_duplicates_cannot_increase_reference_count():
    payload = event("2026-09-08T10:25:00Z")
    payload[0]["bookmakers"] = payload[0]["bookmakers"][:1] * 3
    row = score(payload)[0]
    assert not row["eligible"]
    assert len(row["reference_books"]) == 0


def test_first_entry_is_locked_and_grading_has_pushes():
    row = score(event("2026-09-08T10:25:00Z"))[0]
    first = record_positions([], [row], NOW)
    assert len(first) == 1
    changed = {**row, "line": 55.5}
    later = record_positions(first, [changed], NOW + timedelta(days=1))
    assert len(later) == 1 and later[0]["line"] == row["line"]
    push = {**later[0], "line": 50}
    schedule = pd.DataFrame([{"game_id": 1, "status": "STATUS_FINAL", "home_score": 30, "away_score": 20}])
    graded = grade_positions([push], schedule)
    assert graded[0]["result"] == "push" and graded[0]["profit_units"] == 0
    assert graded[0]["clv"] is None
    perf = performance(graded, row["candidate"])
    assert perf["bets"] == 1 and perf["pushes"] == 1 and perf["roi_95_low"] is None


def test_exception_messages_cannot_leak_key():
    assert "secret" not in safe_failure(RuntimeError("https://example.com/?apiKey=secret"))


def test_same_candidate_across_versions_keeps_separate_forward_results():
    from ncaaf_model.runtime import VERSION, forecast_performance
    row = score(event("2026-09-08T10:25:00Z"))[0]
    current = {**row, "actual_total": 60, "result": "win", "profit_units": .9}
    previous = {**current, "model_version": "previous", "result": "loss", "profit_units": -1., "actual_total": 30}
    pending = {**row, "model_version": "previous", "result": "pending"}
    entries = [previous, current, pending]
    active = performance(entries, row["candidate"])
    assert active["model_version"] == VERSION and active["bets"] == 1
    assert active["wins"] == 1 and active["pending"] == 0
    old = performance(entries, row["candidate"], "previous")
    assert old["losses"] == 1 and old["pending"] == 1
    groups = {m["model_version"]: m for m in forecast_performance(entries)}
    assert groups[VERSION]["forecast_entries"] == 1
    assert groups["previous"]["forecast_entries"] == 2
    assert groups[VERSION]["games"] == groups["previous"]["games"] == 1


def test_forward_forecasts_track_abstentions_without_counting_bets():
    from ncaaf_model.runtime import record_forecasts, forecast_performance
    forecast = score(event(None))[0]
    assert not forecast["eligible"]
    entries = record_forecasts([], [forecast], NOW)
    assert len(entries) == 1
    assert len(record_forecasts(entries, [forecast], NOW + timedelta(hours=1))) == 1
    assert not record_positions([], [forecast], NOW)
    schedule = pd.DataFrame([{"game_id": 1, "status": "STATUS_FINAL", "home_score": 30, "away_score": 35}])
    metrics = forecast_performance(grade_positions(entries, schedule))[0]
    assert metrics["games"] == 1 and metrics["brier"] >= 0 and metrics["log_loss"] >= 0
    assert metrics["pending"] == 0


def test_same_book_from_two_aggregators_is_not_an_independent_opinion():
    from ncaaf_model.runtime import merge_provider_events
    old = event(None)
    incoming = event("2026-09-08T10:25:00Z")
    incoming[0]["id"] = "another-provider-id"
    merged = merge_provider_events(old, incoming)
    assert len(merged) == 1 and len(merged[0]["bookmakers"]) == 4
    assert merged[0]["id"] == "e"
    assert all(book["last_update"] == "2026-09-08T10:25:00Z" for book in merged[0]["bookmakers"])


def test_decimal_payout_is_graded_without_display_rounding():
    row = score(event("2026-09-08T10:25:00Z"))[0]
    row.update(decimal_odds=1.92, american_odds=-109, line=40, side="over")
    position = record_positions([], [row], NOW)
    schedule = pd.DataFrame([{"game_id": 1, "status": "STATUS_FINAL", "home_score": 30, "away_score": 20}])
    assert grade_positions(position, schedule)[0]["profit_units"] == pytest.approx(.92)


def test_unchanged_market_is_current_only_after_authoritative_full_state_receipt():
    payload=event("2026-09-07T10:00:00Z")
    for b in payload[0]["bookmakers"]:
        b['source']='odds_api_io'
        b['markets'][0].update(observed_at="2026-09-08T10:29:00Z",observation_kind='provider_full_state')
    row=score(payload)[0]
    assert row['eligible']
    assert row['freshness_basis']=='provider_full_state_receipt'
    assert row['market_updated_at']=='2026-09-07T10:00:00Z'
    assert row['quote_time']=='2026-09-08T10:29:00Z'
    for b in payload[0]["bookmakers"]:
        b['source']='actionnetwork_public'
    assert not score(payload)[0]['eligible']


def test_receipt_time_does_not_refresh_an_old_archive_or_validate_future_market_updates():
    payload=event("2026-09-07T10:00:00Z")
    for b in payload[0]["bookmakers"]:
        b['source']='odds_api_io'
        b['markets'][0].update(observed_at="2026-09-08T10:20:00Z",observation_kind='provider_full_state')
    assert not score(payload)[0]['eligible']
    for b in payload[0]["bookmakers"]:
        b['markets'][0].update(observed_at="2026-09-08T10:29:00Z",last_update="2026-09-08T12:00:00Z")
    assert not score(payload)[0]['eligible']


def test_statistical_candidate_can_qualify_below_six_point_adjustment():
    payload=event("2026-09-08T10:25:00Z")
    for b in payload[0]['bookmakers']:
        for o in b['markets'][0]['outcomes']:
            o['point']=50.5
    output=score_games(games(payload),{'opponent_adjusted_ridge':[54.]},{'sigma':16,'family':'normal'},NOW)
    row=next(r for r in output if r['candidate']=='opponent_adjusted_ridge')
    assert row['eligible']
    assert row['paper_stake_fraction'] > 0
    assert row['confidence']=='experimental'
    assert not row['execution_confirmed']
