from datetime import datetime, timedelta, timezone
from copy import deepcopy
from dataclasses import replace
import json
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


def full_state_event(observed=NOW, kickoff=None):
    payload = event(observed.isoformat())
    if kickoff is not None:
        payload[0]["commence_time"] = kickoff.isoformat()
    for book in payload[0]["bookmakers"]:
        book["source"] = "odds_api_io"
        book["markets"][0].update(observed_at=observed.isoformat(), observation_kind="provider_full_state")
    return payload


def test_scoring_rechecks_freshness_instead_of_trusting_cached_true_flag():
    frame = games(full_state_event())
    assert all(q["fresh"] for q in frame.iloc[0]["quotes"])
    later = NOW + timedelta(seconds=120, microseconds=1)
    rows = score_games(frame, {}, {"sigma":16,"family":"normal"}, later)
    assert not any(row["eligible"] for row in rows)
    assert all("quote_timestamp_missing_or_stale" in row["flags"] for row in rows)
    assert all(q["fresh"] for q in frame.iloc[0]["quotes"])  # caller snapshot unchanged


def test_position_lock_checks_exact_receipt_boundary_and_started_game():
    row = score(full_state_event())[0]
    assert row["eligible"]
    assert record_positions([], [row], NOW+timedelta(seconds=120))
    assert not record_positions([], [row], NOW+timedelta(seconds=120,microseconds=1))
    started = {**row, "kickoff": NOW.isoformat()}
    assert not record_positions([], [started], NOW)


def test_lock_rechecks_peer_quotes_used_in_original_forecast():
    from ncaaf_model.runtime import forecasts_at_lock, quote_at_time
    payload=full_state_event()
    for book in payload[0]["bookmakers"][1:]:
        book["markets"][0]["observed_at"]=(NOW-timedelta(seconds=90)).isoformat()
    frame=games(payload)
    forecasts=score_games(frame, {}, {"sigma":16,"family":"normal"},NOW)
    original=deepcopy(forecasts)
    assert forecasts[0]["eligible"] and forecasts[0]["sportsbook"]=="shop"
    now=NOW+timedelta(seconds=31)
    assert quote_at_time(forecasts[0], now)["fresh"]
    checked=forecasts_at_lock(forecasts,frame,now)
    assert not checked[0]["eligible"]
    assert "reference_quote_timestamp_missing_or_stale" in checked[0]["flags"]
    assert not record_positions([],checked,now)
    assert forecasts==original


def daily_fixture(tmp_path, monkeypatch, *, projection_delay=0, snapshot_delay=0, kickoff=None, explicit=False):
    from ncaaf_model import runtime, weather_publishing
    from ncaaf_model.config import load_settings
    clock=SimpleNamespace(value=NOW)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            if explicit:
                raise AssertionError("Explicit synthetic now must not read the wall clock")
            return clock.value if tz is None else clock.value.astimezone(tz)
    monkeypatch.setattr(runtime,"datetime",Clock)
    settings=replace(load_settings(),root=tmp_path/"model",allowed_books=("shop","a","b","c"))
    settings.models_dir.mkdir(parents=True)
    (settings.models_dir/"score_distribution_v2.json").write_text(json.dumps({"sigma":16.,"family":"normal",
        "market_provenance":"cfbd_and_verified_pregame_provider_only","data_fingerprint":"test-fingerprint"}))
    kick=kickoff or NOW+timedelta(days=1)
    payload=full_state_event(kickoff=kick)
    schedule=pd.DataFrame([{"game_id":1,"season":2026,"week":2,"home_id":1,"away_id":2,
        "home_team":"Home","away_team":"Away","game_date":kick.isoformat(),"neutral_site":False,
        "status":"STATUS_SCHEDULED","home_score":float("nan"),"away_score":float("nan")}])
    monkeypatch.setattr(runtime,"refresh_inputs",lambda *_:(schedule,{"schedule_fresh":True,"input_failures":{}}))
    monkeypatch.setattr(runtime,"fetch_odds",lambda *_:(deepcopy(payload),{}))
    monkeypatch.setattr(weather_publishing,"collect_inputs",lambda *_:{"status":"unavailable","features":[],"message":"Synthetic"})
    monkeypatch.setattr(weather_publishing,"publish_weather",lambda *args:{"status":"unavailable"})
    def project(_settings,matched,now,diagnostics):
        clock.value += timedelta(seconds=projection_delay)
        diagnostics["active_data_fingerprint"]="test-fingerprint"
        return matched.assign(home_prior_games=15,away_prior_games=15),{runtime.PRIMARY:[56.]}
    monkeypatch.setattr(runtime,"candidate_projections",project)
    actual_write=runtime.atomic_write_bytes
    def write(path,payload):
        result=actual_write(path,payload)
        if path.name.startswith("snapshot-") and path.name.endswith(".json.gz"):
            clock.value += timedelta(seconds=snapshot_delay)
        return result
    monkeypatch.setattr(runtime,"atomic_write_bytes",write)
    return runtime,settings,clock,weather_publishing


@pytest.mark.parametrize("stage",["projections","snapshot"])
def test_live_daily_cannot_lock_prices_that_expire_during_expensive_work(tmp_path,monkeypatch,stage):
    runtime,settings,clock,_=daily_fixture(tmp_path,monkeypatch,
        projection_delay=121 if stage=="projections" else 0,snapshot_delay=121 if stage=="snapshot" else 0)
    board=runtime.daily(settings=settings)
    assert board["status"]=="ok"
    assert not board["today_picks"] and not board["upcoming_picks"] and not board["results"]
    assert not any(row["eligible"] for row in board["forecasts"])
    assert board["diagnostics"]["fresh_sportsbooks"]==[]
    assert json.loads((settings.ledger_dir/"positions.json").read_text())==[]
    assert all(entry["recorded_at"]==runtime.stamp(clock.value)
               for entry in json.loads((settings.ledger_dir/"forecast_entries.json").read_text()))


@pytest.mark.parametrize("stage",["projections","snapshot"])
def test_live_daily_cannot_record_forecasts_or_positions_after_kickoff(tmp_path,monkeypatch,stage):
    runtime,settings,clock,_=daily_fixture(tmp_path,monkeypatch,kickoff=NOW+timedelta(seconds=30),
        projection_delay=31 if stage=="projections" else 0,snapshot_delay=31 if stage=="snapshot" else 0)
    board=runtime.daily(settings=settings)
    assert board["status"]=="ok" and not board["results"]
    assert not any(row["eligible"] for row in board["forecasts"])
    assert json.loads((settings.ledger_dir/"forecast_entries.json").read_text())==[]


def test_daily_explicit_now_retains_deterministic_injection(tmp_path,monkeypatch):
    runtime,settings,clock,_=daily_fixture(tmp_path,monkeypatch,projection_delay=1000,snapshot_delay=1000,explicit=True)
    board=runtime.daily(settings=settings,now=NOW)
    assert board["status"]=="ok" and board["results"]
    assert all(row["recorded_at"]==runtime.stamp(NOW) for row in board["results"])
    assert clock.value>NOW+timedelta(minutes=30)


def test_positions_are_durable_before_later_publishing_work(tmp_path,monkeypatch):
    runtime,settings,clock,weather=daily_fixture(tmp_path,monkeypatch)
    captured=[]
    def publish(*args):
        locked=json.loads((settings.ledger_dir/"positions.json").read_text())
        assert locked and all(row["recorded_at"]==runtime.stamp(clock.value) for row in locked)
        captured.extend(locked)
        clock.value+=timedelta(minutes=10)
        return {"status":"unavailable"}
    monkeypatch.setattr(weather,"publish_weather",publish)
    board=runtime.daily(settings=settings)
    assert board["results"]==captured


def final_schedule(home=30,away=20):
    return pd.DataFrame([{"game_id":1,"status":"STATUS_FINAL","home_score":home,"away_score":away}])


def test_first_final_and_corrections_append_grade_history_without_mutating_prior_items():
    row=record_positions([],score(full_state_event()),NOW)[0]
    receipt=NOW+timedelta(days=2)
    first=grade_positions([row],final_schedule(),now=receipt)[0]
    assert first["grade_history"]==[{"outcome_received_at":receipt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                    "actual_total":50.,"result":"win","profit_units":pytest.approx(100/110)}]
    original=deepcopy(first)
    same=grade_positions([first],final_schedule(),now=receipt+timedelta(hours=1))[0]
    assert same==first
    corrected=grade_positions([first],final_schedule(20,20),now=receipt+timedelta(hours=2))[0]
    assert len(corrected["grade_history"])==2
    assert corrected["grade_history"][0]==first["grade_history"][0]
    assert corrected["grade_history"][1]["actual_total"]==40 and corrected["grade_history"][1]["result"]=="loss"
    assert corrected["outcome_received_at"]==(receipt+timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert first==original and "grade_history" not in row


def test_unchanged_legacy_settlement_does_not_get_an_invented_receipt():
    row=score(full_state_event())[0]
    old=grade_positions([row],final_schedule())[0]
    assert "outcome_received_at" not in old and "grade_history" not in old
    observed=grade_positions([old],final_schedule(),now=NOW+timedelta(days=2))[0]
    assert observed==old
    correction=grade_positions([old],final_schedule(20,20),now=NOW+timedelta(days=3))[0]
    assert len(correction["grade_history"])==1
    assert correction["grade_history"][0]["actual_total"]==40


@pytest.mark.parametrize("score_value",[float("nan"),float("inf"),float("-inf"),None,-1,1.5,True])
def test_invalid_final_scores_cannot_create_or_overwrite_grade(score_value):
    row=record_positions([],score(full_state_event()),NOW)[0]
    assert grade_positions([row],final_schedule(score_value,20),now=NOW+timedelta(days=2))==[row]
    settled=grade_positions([row],final_schedule(),now=NOW+timedelta(days=2))[0]
    assert grade_positions([settled],final_schedule(30,score_value),now=NOW+timedelta(days=3))==[settled]


def test_final_total_change_is_a_new_observation_even_if_win_and_payout_unchanged():
    row=record_positions([],score(full_state_event()),NOW)[0]
    first=grade_positions([row],final_schedule(),now=NOW+timedelta(days=2))[0]
    next_grade=grade_positions([first],final_schedule(31,20),now=NOW+timedelta(days=3))[0]
    assert first["result"]==next_grade["result"]=="win" and len(next_grade["grade_history"])==2


def test_untimed_legacy_correction_does_not_reuse_old_outcome_receipt():
    row=record_positions([],score(full_state_event()),NOW)[0]
    first=grade_positions([row],final_schedule(),now=NOW+timedelta(days=2))[0]
    corrected=grade_positions([first],final_schedule(20,20))[0]
    assert corrected["result"]=="loss" and "outcome_received_at" not in corrected
    assert corrected["grade_history"]==first["grade_history"]
