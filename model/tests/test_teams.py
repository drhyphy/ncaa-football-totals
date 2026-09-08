import pandas as pd
import pytest

from ncaaf_model.teams import best_prefix_match, normalize_team, pair_key
from ncaaf_model.totals_scoring import attach_totals_schedule


def test_aliases_and_prefixes() -> None:
    assert normalize_team("Hawai'i Rainbow Warriors") == "hawaii"
    assert normalize_team("UConn") == "connecticut"
    assert best_prefix_match("USC Trojans", ["USC", "UCLA"]) == "USC"
    assert best_prefix_match("NC State Wolfpack", ["NC State", "North Carolina"]) == "NC State"
    assert pair_key("UConn Huskies", "UMass Minutemen") == "connecticut@massachusetts"


@pytest.mark.parametrize("feed_name,schedule_name,home,kickoff,game_id,away_id", [
    ("Tennessee-Martin Skyhawks", "UT Martin Skyhawks", "West Virginia Mountaineers", "2026-09-12T17:00Z", 401856791, 2630),
    ("Central Connecticut State Blue Devils", "Central Connecticut Blue Devils", "Toledo Rockets", "2026-09-12T19:30Z", 401866419, 2115),
    ("Mercyhurst University Lakers", "Mercyhurst Lakers", "New Mexico Lobos", "2026-09-12T20:00Z", 401865252, 2385),
    ("Delaware Fightin Blue Hens", "Delaware Blue Hens", "Vanderbilt Commodores", "2026-09-12T20:15Z", 401856684, 48),
    ("Southern University Jaguars", "Southern Jaguars", "Houston Cougars", "2026-09-12T23:00Z", 401856787, 2582),
    ("Grambling State Tigers", "Grambling Tigers", "TCU Horned Frogs", "2026-09-13T00:00Z", 401856789, 2755),
])
def test_exact_public_feed_aliases_match_verified_schedule(feed_name, schedule_name, home, kickoff, game_id, away_id):
    assert normalize_team(feed_name) == normalize_team(schedule_name)
    odds = pd.DataFrame([{"event_id": "feed-event", "commence_time": kickoff,
                          "away_team": feed_name, "home_team": home}])
    schedule = pd.DataFrame([{"game_id": game_id, "game_date": kickoff, "away_team": schedule_name,
                              "home_team": home, "away_id": away_id, "home_id": 999,
                              "week": 2, "season": 2026, "neutral_site": False}])
    result = attach_totals_schedule(odds, schedule).iloc[0]
    assert result["schedule_match"]
    assert result["espn_game_id"] == game_id
    assert result["away_id"] == away_id


def test_new_exact_aliases_do_not_merge_similarly_named_schools():
    assert normalize_team("Tennessee-Martin Skyhawks") != normalize_team("Tennessee State Tigers")
    assert normalize_team("Central Connecticut State Blue Devils") != normalize_team("Connecticut Huskies")
    assert normalize_team("Delaware Fightin Blue Hens") != normalize_team("Delaware State Hornets")
    assert normalize_team("Southern University Jaguars") != normalize_team("Texas Southern Tigers")
