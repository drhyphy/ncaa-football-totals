from datetime import datetime, timezone

import numpy as np
import pytest
import requests

from ncaaf_model.odds_api_io import fetch_odds_api_io, parse_odds_api_io

NOW = datetime(2026, 9, 8, 23, 30, tzinfo=timezone.utc)


def event():
    return {"id": 70898552, "home": "Michigan Wolverines", "away": "Oklahoma Sooners",
            "date": "2026-09-12T16:00:00Z", "status": "pending",
            "league": {"name": "USA - College", "slug": "usa-college"},
            "bookmakers": {"DraftKings": [
                {"name": "Totals", "updatedAt": "2026-09-08T23:15:26.112Z",
                 "odds": [{"hdp": 43.5, "over": "1.92", "under": "1.89"}]},
                {"name": "Spread", "updatedAt": "2026-09-08T17:30:51.978Z",
                 "odds": [{"hdp": 5.5, "home": "1.95", "away": "1.86"}]},
                {"name": "Totals HT", "updatedAt": "2026-09-08T23:00:00Z",
                 "odds": [{"hdp": 21.5, "over": "1.9", "under": "1.9"}]},
            ]}}


def test_full_game_pairs_preserve_exact_prices_and_market_specific_timestamps():
    result = parse_odds_api_io([event()], NOW)[0]
    book = result["bookmakers"][0]
    assert book["last_update"] is None
    assert len(book["markets"]) == 2
    totals, spreads = book["markets"]
    assert totals["last_update"] == "2026-09-08T23:15:26.112000Z"
    assert spreads["last_update"] == "2026-09-08T17:30:51.978000Z"
    under = totals["outcomes"][1]
    assert under["decimal_price"] == 1.89
    assert np.isclose(1 + 100 / abs(under["price"]), 1.89)
    assert [x["point"] for x in spreads["outcomes"]] == [5.5, -5.5]


def test_missing_timestamp_and_old_timestamp_are_never_replaced_by_fetch_time():
    raw = event()
    del raw["bookmakers"]["DraftKings"][0]["updatedAt"]
    totals = parse_odds_api_io([raw], NOW)[0]["bookmakers"][0]["markets"][0]
    assert totals["last_update"] is None
    raw["bookmakers"]["DraftKings"][0]["updatedAt"] = "2026-09-01T00:00:00Z"
    assert parse_odds_api_io([raw], NOW)[0]["bookmakers"][0]["markets"][0]["last_update"] == "2026-09-01T00:00:00Z"


def test_unpaired_invalid_partial_or_live_totals_do_not_enter_feed():
    raw = event()
    del raw["bookmakers"]["DraftKings"][0]["odds"][0]["under"]
    assert parse_odds_api_io([raw], NOW) == []
    raw = event()
    raw["status"] = "live"
    assert parse_odds_api_io([raw], NOW) == []
    raw = event()
    raw["bookmakers"]["DraftKings"][0]["odds"][0]["hdp"] = 43.25
    assert parse_odds_api_io([raw], NOW) == []


class Response:
    status_code = 200
    def __init__(self, payload):
        self.payload = payload
    def json(self):
        return self.payload


class Session:
    def __init__(self):
        self.calls = []
    def get(self, url, params, timeout):
        self.calls.append((url, params))
        if url.endswith('/bookmakers/selected'):
            return Response({"bookmakers": ["DraftKings", "FanDuel"]})
        if url.endswith('/events'):
            return Response([event()])
        return Response([event()])


def test_fetch_only_reads_existing_book_selection_and_batches_known_events():
    session = Session()
    result, diagnostics = fetch_odds_api_io(session, 'test-secret', NOW)
    assert len(result) == 1
    assert diagnostics['selected_bookmakers'] == ['DraftKings', 'FanDuel']
    assert diagnostics['timestamped_total_markets'] == 1
    assert len(session.calls) == 3
    assert session.calls[-1][1]['bookmakers'] == 'DraftKings,FanDuel'
    assert session.calls[-1][1]['eventIds'] == '70898552'


def test_network_errors_never_expose_key_bearing_urls():
    class FailingSession:
        def get(self, *args, **kwargs):
            raise requests.RequestException('https://api.odds-api.io/?apiKey=test-secret')
    with pytest.raises(RuntimeError, match='network request failed') as error:
        fetch_odds_api_io(FailingSession(), 'test-secret', NOW)
    assert 'test-secret' not in str(error.value)
