"""Regression protection for provider-clock drift and official game status."""
from dataclasses import replace
from datetime import datetime, timezone
import json

import numpy as np
import pandas as pd
import pytest

from ncaaf_model import runtime
from ncaaf_model.config import load_settings
from ncaaf_model.totals_scoring import attach_totals_schedule

NOW = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
UPCOMING = "2026-09-09T19:00:00Z"


def event(event_id="valid", home="Alabama", away="Georgia", kickoff=UPCOMING):
    return {"id":event_id,"home_team":home,"away_team":away,"commence_time":kickoff,
            "bookmakers":[{"key":name,"last_update":"2026-09-09T11:59:00Z",
                            "markets":[{"key":"totals","outcomes":[
                                {"name":"Over","point":50.5,"price":-110},
                                {"name":"Under","point":50.5,"price":-110}]}]}
                           for name in ["draftkings","fanduel"]]}


def schedule_row(game_id=1, home="Alabama", away="Georgia", kickoff=UPCOMING, status="STATUS_SCHEDULED"):
    return {"game_id":game_id,"game_date":kickoff,"home_team":home,"away_team":away,
            "home_id":game_id*2,"away_id":game_id*2+1,"week":2,"season":2026,
            "neutral_site":False,"status":status,"home_score":np.nan,"away_score":np.nan}


def matched_games(events, schedule):
    settings=replace(load_settings(),allowed_books=("draftkings","fanduel"))
    odds=runtime.quotes_from_events(events,settings,NOW)
    return attach_totals_schedule(odds,schedule).assign(home_prior_games=10,away_prior_games=10)


def scored(events, schedule):
    games=matched_games(events,schedule)
    rows=runtime.score_games(games,{runtime.PRIMARY:np.full(len(games),58.)},
                             {"sigma":16.,"family":"normal"},NOW,
                             {"input_failures":{},"schedule_fresh":True})
    return games,rows


def test_previous_day_final_game_cannot_reenter_through_future_provider_clock():
    schedule=pd.DataFrame([schedule_row(kickoff="2026-09-08T19:00:00Z",status="STATUS_FINAL")])
    games,rows=scored([event()],schedule)
    assert not games.schedule_match.iloc[0]
    assert all(not row["eligible"] for row in rows)
    assert not runtime.record_positions([],rows,NOW)
    assert not runtime.record_forecasts([],rows,NOW)


@pytest.mark.parametrize("status",["STATUS_FINAL","STATUS_IN_PROGRESS","STATUS_HALFTIME",None])
def test_matching_teams_and_future_clocks_do_not_override_non_scheduled_status(status):
    games,rows=scored([event()],pd.DataFrame([schedule_row(status=status)]))
    assert games.schedule_match.iloc[0]
    assert not runtime.schedule_is_upcoming(games.iloc[0],NOW)
    assert all("official_schedule_not_upcoming_or_time_mismatch" in row["flags"] for row in rows)
    assert not runtime.record_positions([],rows,NOW)
    assert not runtime.record_forecasts([],rows,NOW)


def test_canonical_kickoff_already_started_is_rejected_despite_future_provider_time():
    # The two clocks are only two hours apart and pass identity matching.
    games,rows=scored([event(kickoff="2026-09-09T13:00:00Z")],
                     pd.DataFrame([schedule_row(kickoff="2026-09-09T11:00:00Z")]))
    assert games.schedule_match.iloc[0]
    assert not runtime.schedule_is_upcoming(games.iloc[0],NOW)
    assert all(not row["eligible"] for row in rows)
    assert not runtime.record_forecasts([],rows,NOW)


def test_provider_drift_over_four_hours_cannot_match_official_game():
    games,rows=scored([event(kickoff="2026-09-09T23:01:00Z")],pd.DataFrame([schedule_row()]))
    assert not games.schedule_match.iloc[0]
    assert all(not row["eligible"] for row in rows)


def test_valid_two_book_scheduled_game_passes_and_uses_canonical_kickoff():
    games,rows=scored([event(kickoff="2026-09-09T20:00:00Z")],pd.DataFrame([schedule_row()]))
    assert runtime.schedule_is_upcoming(games.iloc[0],NOW)
    primary=next(row for row in rows if row["candidate"]==runtime.PRIMARY)
    assert primary["eligible"]
    assert pd.Timestamp(primary["kickoff"])==pd.Timestamp(UPCOMING)
    assert primary["confidence"]=="experimental"
    assert not primary["execution_confirmed"]
    assert len(runtime.record_positions([],rows,NOW))==1


def daily_fixture(tmp_path,monkeypatch,distribution_fingerprint="fixture"):
    settings=replace(load_settings(),root=tmp_path/"model",allowed_books=("draftkings","fanduel"))
    settings.models_dir.mkdir(parents=True)
    (settings.models_dir/"score_distribution_v2.json").write_text(json.dumps({
        "sigma":16.,"family":"normal","data_fingerprint":distribution_fingerprint,
        "market_provenance":"cfbd_and_verified_pregame_provider_only"}))
    events=[event(),event("final","SMU","TCU"),event("live","Michigan","Iowa"),
            event("started","Oregon","USC",kickoff="2026-09-09T13:00:00Z"),
            event("unmatched","Nobody","Another Team")]
    schedule=pd.DataFrame([schedule_row(),schedule_row(2,"SMU","TCU",status="STATUS_FINAL"),
                           schedule_row(3,"Michigan","Iowa",status="STATUS_IN_PROGRESS"),
                           schedule_row(4,"Oregon","USC",kickoff="2026-09-09T11:00:00Z")])
    monkeypatch.setattr(runtime,"refresh_inputs",lambda *_:(schedule,{"input_failures":{},"schedule_fresh":True}))
    monkeypatch.setattr(runtime,"fetch_odds",lambda *_:(events,{}))
    def projections(_settings,matched,_now,diagnostics):
        diagnostics["active_data_fingerprint"]="fixture"
        return matched.assign(home_prior_games=10,away_prior_games=10),{runtime.PRIMARY:np.full(len(matched),58.)}
    monkeypatch.setattr(runtime,"candidate_projections",projections)
    return settings


def test_daily_scanner_receives_only_official_future_games(tmp_path,monkeypatch):
    from ncaaf_model import market_opportunities
    settings=daily_fixture(tmp_path,monkeypatch)
    seen=[]
    def scan(events,*_):
        seen.extend(events)
        return {"arbitrages":[],"hedges":[],"dominance":[],"actual_bets_placed":0}
    monkeypatch.setattr(market_opportunities,"scan_market_opportunities",scan)
    board=runtime.daily(settings,NOW)
    assert board["status"]=="ok"
    assert [row["id"] for row in seen]==["valid"]
    assert pd.Timestamp(seen[0]["commence_time"])==pd.Timestamp(UPCOMING)
    assert {row["game_id"] for row in board["today_picks"]}=={"1"}
    ledger=json.loads((settings.ledger_dir/"forecast_entries.json").read_text())
    assert {row["game_id"] for row in ledger}=={"1"}


def test_mismatched_distribution_artifact_prevents_all_publication(tmp_path,monkeypatch):
    settings=daily_fixture(tmp_path,monkeypatch,distribution_fingerprint="different_fit")
    board=runtime.daily(settings,NOW)
    assert board["status"]=="unavailable"
    assert board["today_picks"]==[]
    assert board["forecasts"]==[]
    assert json.loads((settings.ledger_dir/"positions.json").read_text())==[]
