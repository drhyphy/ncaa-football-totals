from datetime import datetime, timezone

from ncaaf_model.actionnetwork_odds import parse_scoreboard


def payload(over_update=None, under_update=None):
    rows = [{"side": "over", "value": 51.5, "odds": -110},
            {"side": "under", "value": 51.5, "odds": -110}]
    if over_update:
        rows[0]["updated_at"] = over_update
    if under_update:
        rows[1]["updated_at"] = under_update
    return {"league": {"updated_at": "2026-09-08T20:00:00Z"}, "games": [{
        "id": 1, "start_time": "2026-09-10T20:00:00Z", "status": "scheduled",
        "home_team_id": 1, "away_team_id": 2,
        "teams": [{"id": 1, "full_name": "Home"}, {"id": 2, "full_name": "Away"}],
        "markets": {"15": {"event": {"total": rows}}},
    }]}


def parse(data):
    return parse_scoreboard(data, {15: ("fanduel", "FanDuel")},
                            now=datetime(2026, 9, 8, 21, tzinfo=timezone.utc))[0]["bookmakers"][0]


def test_actionnetwork_preserves_oldest_actual_quote_timestamp():
    book = parse(payload("2026-09-08T20:00:00Z", "2026-09-08T19:59:00Z"))
    market = book["markets"][0]
    assert market["last_update"] == "2026-09-08T19:59:00Z"
    assert market["outcomes"][0]["last_update"] == "2026-09-08T20:00:00Z"


def test_missing_quote_timestamps_never_inherit_league_fetch_or_creation_time():
    data = payload()
    for row in data["games"][0]["markets"]["15"]["event"]["total"]:
        row["created_at"] = "2026-09-08T20:00:00Z"
    book = parse(data)
    assert book["last_update"] is None
    assert book["markets"][0]["last_update"] is None
    assert all(row["last_update"] is None for row in book["markets"][0]["outcomes"])


def test_one_sided_or_timezone_unknown_updates_do_not_verify_pair():
    assert parse(payload("2026-09-08T20:00:00Z"))["markets"][0]["last_update"] is None
    assert parse(payload("2026-09-08T20:00:00Z", "2026-09-08T20:00:00"))["markets"][0]["last_update"] is None
