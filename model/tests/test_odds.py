import math

from ncaaf_model.scoring import normalize_current_odds


def test_current_odds_consensus_uses_paired_devig_and_best_price() -> None:
    payload = [
        {
            "id": "event-1",
            "commence_time": "2099-08-29T16:00:00Z",
            "home_team": "Home Bears",
            "away_team": "Away Cats",
            "bookmakers": [
                {
                    "key": "fanduel",
                    "last_update": "2099-08-20T00:00:00Z",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Home Bears", "price": -120},
                                {"name": "Away Cats", "price": +110},
                            ],
                        },
                        {
                            "key": "spreads",
                            "outcomes": [
                                {"name": "Home Bears", "price": -110, "point": -2.5},
                                {"name": "Away Cats", "price": -110, "point": 2.5},
                            ],
                        },
                        {
                            "key": "totals",
                            "outcomes": [
                                {"name": "Over", "price": -110, "point": 51.5},
                                {"name": "Under", "price": -110, "point": 51.5},
                            ],
                        },
                    ],
                },
                {
                    "key": "draftkings",
                    "last_update": "2099-08-20T00:01:00Z",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Home Bears", "price": -115},
                                {"name": "Away Cats", "price": +105},
                            ],
                        }
                    ],
                },
            ],
        }
    ]
    frame = normalize_current_odds(payload, ("fanduel", "draftkings"))
    assert len(frame) == 1
    assert frame.iloc[0]["book_count"] == 2
    assert frame.iloc[0]["best_home_american"] == -115
    assert frame.iloc[0]["best_home_book"] == "draftkings"
    assert math.isclose(frame.iloc[0]["consensus_home_spread"], -2.5)
    assert math.isclose(frame.iloc[0]["consensus_total"], 51.5)
