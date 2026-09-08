import pandas as pd

from ncaaf_model.sources import merge_schedule_frames, parse_espn_scoreboard


def _row(game_id: int, status: str, score: float | None = None) -> dict:
    return {
        "game_id": game_id,
        "game_date": "2099-09-01T00:00Z",
        "home_team": "Home",
        "away_team": "Away",
        "status": status,
        "home_score": score,
    }


def test_schedule_refresh_is_a_union_and_new_row_wins() -> None:
    existing = pd.DataFrame([_row(1, "STATUS_SCHEDULED"), _row(2, "STATUS_SCHEDULED")])
    incoming = pd.DataFrame([_row(1, "STATUS_FINAL", 31.0)])
    merged = merge_schedule_frames(existing, incoming)
    assert set(merged["game_id"]) == {1, 2}
    refreshed = merged.loc[merged["game_id"].eq(1)].iloc[0]
    assert refreshed["status"] == "STATUS_FINAL"
    assert refreshed["home_score"] == 31.0


def test_parse_espn_scoreboard_builds_schedule_contract() -> None:
    payload = {
        "events": [
            {
                "id": "401000001",
                "date": "2099-09-01T16:00Z",
                "season": {"year": 2099, "type": 2},
                "week": {"number": 1},
                "competitions": [
                    {
                        "neutralSite": True,
                        "conferenceCompetition": False,
                        "attendance": 1234,
                        "venue": {"fullName": "Test Stadium"},
                        "status": {"type": {"name": "STATUS_SCHEDULED"}},
                        "competitors": [
                            {
                                "homeAway": "home",
                                "winner": False,
                                "score": "",
                                "team": {"id": "1", "displayName": "Home Bears", "abbreviation": "HOM"},
                            },
                            {
                                "homeAway": "away",
                                "winner": False,
                                "score": "",
                                "team": {"id": "2", "displayName": "Away Cats", "abbreviation": "AWY"},
                            },
                        ],
                    }
                ],
            }
        ]
    }
    frame = parse_espn_scoreboard(payload)
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["game_id"] == 401000001
    assert row["home_id"] == 1
    assert row["away_id"] == 2
    assert bool(row["neutral_site"])
    assert row["status"] == "STATUS_SCHEDULED"
