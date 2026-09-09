"""Independent synthetic ACC receipt/identity tests; no network or live data."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest

from ncaaf_model import availability_archive as source
from ncaaf_model import availability_collector as collector
from ncaaf_model import revision_archive


NOW = datetime(2026, 9, 9, 18, tzinfo=timezone.utc)
KICKOFF = datetime(2026, 9, 12, 19, 30, tzinfo=timezone.utc)


def event(gid=401, kickoff=KICKOFF, *, generic=False):
    teams = (("Syracuse", "Syracuse Orange", "Syracuse", "SYR"),
             ("California", "California Golden Bears", "Cal", "CAL"))
    if generic:
        teams = tuple((f"{side} {gid}", f"{side} {gid} University", f"{side}{gid}", f"{side[0]}{gid}")
                      for side in ("Home", "Away"))
    return {"id": str(gid), "competitions": [{"id": str(gid), "date": kickoff.isoformat(),
        "dateValid": True, "status": {"type": {"state": "pre"}}, "neutralSite": True,
        "competitors": [{"homeAway": side, "team": dict(zip(
            ("location", "displayName", "shortDisplayName", "abbreviation"), names), id=str(1000 + 2*gid + i))}
            for i, (side, names) in enumerate(zip(("home", "away"), teams))]}]}


def report(game=None, *, phase="Report Pending", variant="location", reverse=False, rows=None):
    game = game or event()
    competition = game["competitions"][0]
    when = datetime.fromisoformat(competition["date"]).astimezone(collector.ZONE)
    blocks = [{"teamName": p["team"][variant], "teamDisplayName": p["team"]["displayName"],
               "rows": deepcopy(rows or [])} for p in competition["competitors"]]
    if reverse:
        blocks.reverse()
    return {"ReportType": phase, "conferenceTimeZone": "ET", "publishDate": "2026-09-09",
        "postedTime": "20:00:00", "games": blocks,
        "footer": {"date": when.strftime("%Y-%m-%d"), "time": when.strftime("%H:%M:%S")}}


def official(games=None):
    return collector.official_games({"events": games or [event()]}, NOW)[0]


@pytest.mark.parametrize("variant", ["location", "displayName", "shortDisplayName", "abbreviation"])
@pytest.mark.parametrize("reverse", [False, True])
def test_unordered_exact_official_variants_match_without_using_opaque_id(variant, reverse):
    payload = {"1182": report(variant=variant, reverse=reverse)}
    original = deepcopy(payload)
    records, targets = collector.match_reports(payload, official())
    assert [g["game_id"] for g in targets] == ["401"]
    assert records[0]["source_key"] == "1182" and records[0]["game_id"] == "401"
    assert records[0]["completion_verified"] is False
    assert records[0]["state"] == "pending" and records[0]["source_row_items"] == [0, 0]
    assert records[0]["matching_names"]["away"][variant]
    assert payload == original


def test_source_alias_uses_existing_exact_normalization_not_a_new_prefix_rule():
    game = event()
    game["competitions"][0]["competitors"][1]["team"]["location"] = "NC State"
    payload = report(game)
    payload["games"][1]["teamName"] = "North Carolina State"
    records, targets = collector.match_reports({"x": payload}, official([game]))
    assert len(targets) == 1 and records[0]["failure"] is None
    payload["games"][1]["teamName"] = "North Carolina"
    records, targets = collector.match_reports({"x": payload}, official([game]))
    assert not targets and records[0]["failure"] == "missing_or_ambiguous_official_pair"


@pytest.mark.parametrize("phase,rows", [("Report Pending", []), ("Unknown New Phase", []),
    ("Initial", [{"unknown": {"status": "Out", "position": "QB"}}, "opaque row"]),
    ("Report Pending", [{"future_schema": True}])])
def test_rows_and_nonpending_labels_never_certify_player_completion(phase, rows):
    records, targets = collector.match_reports({"abc": report(phase=phase, rows=rows)}, official())
    assert len(targets) == 1
    assert records[0]["completion_verified"] is False
    assert records[0]["state"] == ("pending" if phase == "Report Pending" else "nonpending_completion_unverified")
    assert records[0]["source_row_items"] == [len(rows)]*2
    assert not {"injuries", "available_players", "qb_status", "probability"} & records[0].keys()


def test_two_source_records_for_same_game_are_retained_with_one_quote_target():
    records, targets = collector.match_reports({"phase-a": report(), "phase-b": report(phase="Unverified Update")}, official())
    assert len(records) == 2 and len(targets) == 1
    assert all(r["game_id"] == "401" and r["failure"] is None for r in records)
    assert {r["state"] for r in records} == {"pending", "nonpending_completion_unverified"}


@pytest.mark.parametrize("change", ["date", "time", "timezone", "missing_timezone", "duplicate_team", "not_rows"])
def test_old_or_ambiguous_metadata_stays_raw_but_cannot_match(change):
    value = report()
    if change == "date": value["footer"]["date"] = "2026-09-05"
    elif change == "time": value["footer"]["time"] = "19:30:00"  # UTC is not literal ET.
    elif change == "timezone": value["conferenceTimeZone"] = "PT"
    elif change == "missing_timezone": value.pop("conferenceTimeZone")
    elif change == "duplicate_team": value["games"][1]["teamName"] = value["games"][0]["teamName"]
    else: value["games"][1]["rows"] = None
    original = deepcopy(value)
    records, targets = collector.match_reports({"x": value}, official())
    assert not targets and len(records) == 1 and records[0]["failure"]
    assert value == original


def test_pending_publish_label_is_not_a_release_time_or_kickoff():
    value = report()
    value["publishDate"] = "2099-01-01"
    value["postedTime"] = "03:00:00"
    records, targets = collector.match_reports({"x": value}, official())
    assert len(targets) == 1
    assert records[0]["source_publish_date"] == "2099-01-01"
    assert "published_at" not in records[0]


def test_duplicate_official_id_rejected_even_when_other_copy_is_old():
    good, old = event(), event(kickoff=NOW-timedelta(days=1))
    games, failures, _ = collector.official_games({"events": [good, old]}, NOW)
    assert not games
    assert any(x["reason"] == "duplicate_official_inventory_id" for x in failures)


def test_two_official_games_with_same_team_pair_are_ambiguous_and_no_first_match_wins():
    second = event(402, KICKOFF+timedelta(days=1))
    records, targets = collector.match_reports({"x": report()}, official([event(), second]))
    assert not targets
    assert records[0]["failure"] == "missing_or_ambiguous_official_pair"


def test_official_pregame_window_and_exact_integer_id_survive():
    huge = 2**53+19
    games, failures, _ = collector.official_games({"events": [event(huge), event(501, NOW),
        event(502, NOW+timedelta(days=7, seconds=1))]}, NOW)
    assert [g["game_id"] for g in games] == [str(huge)]
    assert not failures


def test_twenty_game_cap_uses_kickoff_id_before_prices_or_report_state():
    games = [event(i, generic=True) for i in range(101, 122)]
    payload = {str(10000-i): report(g, phase="Report Pending" if i % 2 else "Unknown")
               for i, g in enumerate(reversed(games))}
    records, targets = collector.match_reports(payload, official(list(reversed(games))))
    assert [g["game_id"] for g in targets] == [str(i) for i in range(101, 121)]
    assert sum(r["failure"] == "frozen_twenty_game_cap" for r in records) == 1


def summary(game=None):
    game = game or event()
    return {"header": {"id": game["id"], "competitions": deepcopy(game["competitions"])}}


@pytest.mark.parametrize("change", ["kickoff", "post", "id", "team", "homeaway", "invalid_date"])
def test_current_context_changes_are_not_repaired_with_old_identity(change):
    payload = summary()
    c = payload["header"]["competitions"][0]
    if change == "kickoff": c["date"] = (KICKOFF+timedelta(minutes=1)).isoformat()
    elif change == "post": c["status"]["type"]["state"] = "post"
    elif change == "id": payload["header"]["id"] = "402"
    elif change == "team": c["competitors"][0]["team"]["id"] = "99999"
    elif change == "homeaway": c["competitors"][1]["homeAway"] = "home"
    else: c["dateValid"] = False
    assert not collector.context_matches(payload, official()[0])


def test_neutral_or_unknown_roof_does_not_add_weather_eligibility_to_availability():
    payload = summary()
    assert collector.context_matches(payload, official()[0])
    payload["header"]["competitions"][0]["competitors"].reverse()
    assert collector.context_matches(payload, official()[0])


def timing():
    stamps = [(NOW+timedelta(seconds=s)).isoformat() for s in range(6)]
    return ({"requested_at": stamps[4], "observed_at": stamps[5]},
            {"requested_at": stamps[0], "received_at": stamps[1]},
            {"requested_at": stamps[2], "received_at": stamps[3]})


@pytest.mark.parametrize("which,key,new_second", [("source", "requested_at", 2),
    ("context", "requested_at", 0), ("context", "received_at", 1),
    ("quote", "requested_at", 2), ("quote", "observed_at", 3)])
def test_every_inverted_receipt_edge_rejects_pair(which, key, new_second):
    quote, report_receipt, context = timing()
    {"quote": quote, "source": report_receipt, "context": context}[which][key] = (NOW+timedelta(seconds=new_second)).isoformat()
    assert not collector.pair_is_after(quote, report_receipt, context, KICKOFF)


def test_request_overlapping_report_is_not_post_report_even_if_response_arrives_later():
    quote, receipt, context = timing()
    assert collector.pair_is_after(quote, receipt, context, KICKOFF)
    quote["requested_at"] = receipt["requested_at"]
    assert not collector.pair_is_after(quote, receipt, context, KICKOFF)


@pytest.mark.parametrize("missing", ["requested_at", "received_at"])
def test_naive_or_missing_receipts_and_kickoff_equality_reject(missing):
    quote, receipt, context = timing()
    context.pop(missing)
    assert not collector.pair_is_after(quote, receipt, context, KICKOFF)
    quote, receipt, context = timing()
    receipt["received_at"] = "2026-09-09T18:00:01"
    assert not collector.pair_is_after(quote, receipt, context, KICKOFF)
    quote, receipt, context = timing()
    assert not collector.pair_is_after(quote, receipt, context, quote["observed_at"])


class Clock:
    def __init__(self):
        self.time = NOW
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            result = self.time.isoformat()
            self.time += timedelta(milliseconds=10)
        return result


class Response:
    def __init__(self, payload, *, status=200, remaining=100):
        self.content = json.dumps(payload, allow_nan=False).encode()
        self.status_code = status
        self.headers = {"Content-Type": "application/json", "X-RateLimit-Remaining": str(remaining),
                        "Cache-Control": "public, max-age=60", "Age": "45"}

    def iter_content(self, chunk_size):
        yield self.content

    def close(self):
        pass


class SyntheticHTTP:
    """Real archive writers and parsers around wholly synthetic HTTP bodies."""
    def __init__(self, root, games=None):
        self.root = root
        self.games = games or [event()]
        self.current = {f"opaque-{g['id']}": report(g) for g in self.games}
        self.calls = []
        self.headers, self.cookies, self.proxies = {}, {}, {}
        self.selected = ["DraftKings", "FanDuel"]
        self.remaining = 100
        self.access = {"public": True}
        self.context_change = None
        self.duplicate_quote = False
        self.book_change = None

    def post(self, url, **kwargs):
        assert list((self.root/"data/runtime/availability/cohorts").glob("*.json")), "Inventory must be durable first"
        self.calls.append(("POST", url, json.loads(kwargs["data"])))
        if url == source.PUBLIC_ACCESS_URL:
            return Response(self.access)
        assert url == source.CURRENT_URL
        return Response(self.current)

    def provider(self, game):
        c = game["competitions"][0]
        names = {p["homeAway"]: p["team"]["displayName"] for p in c["competitors"]}
        odds = [{"hdp": 50.5, "under": 1.91, "over": 1.95}]
        if self.duplicate_quote:
            odds.append({"hdp": "50.50", "under": 2.2, "over": 1.7})
        result = {"id": int(game["id"])+10000, "date": c["date"], "home": names["home"],
            "away": names["away"], "status": "pending", "league": {"name": "NCAA Football"},
            "bookmakers": {book: [{"name": "Totals", "updatedAt": (NOW-timedelta(days=1)).isoformat(),
                                   "odds": deepcopy(odds)}] for book in self.selected}}
        if self.book_change:
            self.book_change(result)
        return result

    def get(self, url, params, **kwargs):
        self.calls.append(("GET", url, deepcopy(params)))
        if url == collector.SCOREBOARD:
            payload = {"events": deepcopy(self.games)}
        elif url == collector.SUMMARY:
            assert any(c[1] == source.CURRENT_URL for c in self.calls)
            payload = summary(next(g for g in self.games if g["id"] == params["event"]))
            if self.context_change:
                self.context_change(payload)
        elif url.endswith("/bookmakers/selected"):
            payload = {"bookmakers": self.selected}
        elif url.endswith("/events"):
            payload = [self.provider(g) for g in self.games]
        elif url.endswith("/odds/multi"):
            ids = params["eventIds"].split(",")
            payload = [self.provider(g) for g in self.games if str(int(g["id"])+10000) in ids]
        else:
            raise AssertionError("Unexpected network route: " + url)
        return Response(payload, remaining=self.remaining)

    def close(self):
        pass


@pytest.fixture
def environment(tmp_path, monkeypatch):
    root = tmp_path/"project/model"
    for relative in (collector.PROTOCOL, "ncaaf_model/availability_collector.py", "ncaaf_model/availability_archive.py",
                     "ncaaf_model/revision_archive.py", "ncaaf_model/weather_revision_collector.py", "ncaaf_model/teams.py"):
        p = root/relative
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("Synthetic source provenance, not live code or inputs.\n")
    clock = Clock()
    monkeypatch.setattr(collector, "timestamp", clock)
    monkeypatch.setattr(source, "utc_now", clock)
    monkeypatch.setattr(revision_archive, "timestamp", clock)
    monkeypatch.setattr(collector, "load_dotenv", lambda _: None)
    monkeypatch.setattr(collector.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout="a"*40+"\n"))
    monkeypatch.setenv("ODDS_API_IO_KEY", "synthetic-secret")
    for name in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_EVENT_NAME", "GITHUB_SHA"):
        monkeypatch.delenv(name, raising=False)
    return root


def run(root, http=None, *, mutate_source=None, now=NOW):
    http = http or SyntheticHTTP(root)
    client = collector.PriceArchive(root, root/"data/runtime/availability", session=http)
    def capture(model_root, archive):
        result = source.capture_current(model_root, archive, requester=lambda: http)
        if mutate_source:
            mutate_source(result)
        return result
    result = collector.collect(root, now=now, client=client, source_capture=capture)
    return result, http, client


def test_end_to_end_pending_capture_retains_originals_and_post_report_quotes_without_models(environment):
    result, http, _ = run(environment)
    assert result["status"] == "captured"
    assert result["counts"]["pending_reports"] == result["counts"]["matched_games"] == result["counts"]["paired_two_book_games"] == 1
    assert result["models_fitted"] == result["verified_completed_reports"] == 0
    assert result["counts"]["total_requests"] == 7
    assert http.calls[0][1] == collector.SCOREBOARD
    assert [x[1] for x in http.calls[1:3]] == [source.PUBLIC_ACCESS_URL, source.CURRENT_URL]
    assert result["rows"][0]["quotes"][0]["under_decimal_odds"] == 1.91
    assert result["rows"][0]["quotes"][0]["market_updated_at"].startswith("2026-09-08")  # Change time is not observation freshness.
    assert all(p["report_to_quote_seconds"] > 0 for p in result["rows"][0]["pairs"])
    for path in result["receipts"]:
        receipt = json.loads((environment/path).read_text())
        body = gzip.decompress((environment/receipt["body_path"]).read_bytes())
        assert hashlib.sha256(body).hexdigest() == receipt["body_sha256"]
        assert "synthetic-secret" not in json.dumps(receipt)
    assert not (environment/"ledger").exists()
    assert not (environment/"data/models").exists()


def test_one_book_is_sufficient_and_live_board_cannot_change_cohort(environment):
    board = environment.parent/"site/data/board.json"
    board.parent.mkdir(parents=True)
    board.write_text('{"forecasts":[{"game_id":"not-this-source"}],"picks":["ignore"]}')
    original = board.read_bytes()
    http = SyntheticHTTP(environment)
    http.selected = ["FanDuel"]
    result, _, _ = run(environment, http)
    assert result["status"] == "captured" and result["counts"]["paired_games"] == 1
    assert result["counts"]["paired_two_book_games"] == 0
    assert any(x["book"] == "draftkings" and x["reason"] == "missing_or_duplicate_main_totals"
               for x in result["quote_diagnostics"])
    assert not result["failures"]
    assert [g["game_id"] for g in result["rows"]] == ["401"]
    assert board.read_bytes() == original


def test_nonpending_unknown_rows_do_not_activate_status_or_role_interpretation(environment):
    http = SyntheticHTTP(environment)
    http.current = {"x": report(phase="Game Day", rows=[{"Position": "QB", "Status": "Out"}])}
    result, _, _ = run(environment, http)
    assert result["status"] == "captured"
    assert result["counts"]["nonpending_unverified_reports"] == 1
    assert result["verified_completed_reports"] == result["models_fitted"] == 0
    assert "probability" not in json.dumps(result).lower()


@pytest.mark.parametrize("kind", ["source_status", "capture_error", "parse_error", "body_incomplete"])
def test_http200_source_failure_cannot_be_relabelled_usable_or_quoted(environment, kind):
    def mutate(value):
        if kind == "source_status": value["status"] = "current_unavailable"
        elif kind == "body_incomplete": value["current"]["receipt"]["body_complete"] = False
        else: value["current"]["receipt"][kind] = "synthetic_rejected_source"
    result, http, _ = run(environment, mutate_source=mutate)
    assert result["status"] == "failed"
    assert result["counts"]["total_requests"] == 3
    assert result["counts"]["paired_games"] == 0
    assert not any("odds-api.io" in c[1] or c[1] == collector.SUMMARY for c in http.calls)
    assert len(result["receipts"]) == 3


def test_public_access_false_stops_current_and_prices_but_retains_receipts(environment):
    http = SyntheticHTTP(environment)
    http.access = {"public": False}
    result, _, _ = run(environment, http)
    assert result["status"] == "failed" and result["counts"]["total_requests"] == 2
    assert not any(c[1] == source.CURRENT_URL or "odds-api.io" in c[1] for c in http.calls)


def test_post_report_context_change_prevents_prices_and_keeps_missing_game(environment):
    http = SyntheticHTTP(environment)
    http.context_change = lambda p: p["header"]["competitions"][0].update(date=(KICKOFF+timedelta(hours=1)).isoformat())
    result, _, _ = run(environment, http)
    assert result["status"] == "partial"
    assert result["counts"]["matched_games"] == 1 and result["counts"]["context_verified_games"] == 0
    assert not any("odds-api.io" in c[1] for c in http.calls)
    assert result["rows"][0]["kickoff"] == KICKOFF.isoformat().replace("+00:00", "Z")


def test_duplicate_numeric_line_aliases_do_not_choose_favorable_price(environment):
    http = SyntheticHTTP(environment)
    http.duplicate_quote = True
    result, _, _ = run(environment, http)
    assert result["status"] == "partial" and result["counts"]["paired_games"] == 0
    assert not result["rows"][0]["quotes"]


def test_ambiguous_optional_peer_stays_diagnostic_without_negating_valid_book(environment):
    http = SyntheticHTTP(environment)
    def duplicate_peer(payload):
        payload["bookmakers"]["DraftKings"] *= 2
    http.book_change = duplicate_peer
    result, _, _ = run(environment, http)
    assert result["status"] == "captured"
    assert {p["sportsbook"] for p in result["rows"][0]["pairs"]} == {"fanduel"}
    assert any(x["book"] == "draftkings" and x["reason"] == "missing_or_duplicate_main_totals"
               for x in result["quote_diagnostics"])
    assert not result["failures"]


def test_invalid_peer_price_keeps_valid_book_but_remains_a_data_failure(environment):
    http = SyntheticHTTP(environment)
    def invalid_peer(payload):
        payload["bookmakers"]["DraftKings"][0]["odds"][0]["under"] = 1
    http.book_change = invalid_peer
    result, _, _ = run(environment, http)
    assert result["status"] == "partial"
    assert {p["sportsbook"] for p in result["rows"][0]["pairs"]} == {"fanduel"}
    assert any(x["reason"] == "invalid_or_duplicate_total_pair" for x in result["failures"])


def test_quota_reserve_blocks_price_requests_without_losing_report(environment):
    http = SyntheticHTTP(environment)
    http.remaining = 20
    result, _, _ = run(environment, http)
    assert result["status"] == "partial" and result["counts"]["pending_reports"] == 1
    calls = [c for c in http.calls if "odds-api.io" in c[1]]
    assert len(calls) == 1 and calls[0][1].endswith("/bookmakers/selected")


def test_twenty_games_never_exceeds_four_odds_calls_or_27_total_requests(environment):
    games = [event(i, generic=True) for i in range(101, 122)]
    result, http, _ = run(environment, SyntheticHTTP(environment, list(reversed(games))))
    assert result["counts"]["matched_games"] == result["counts"]["paired_games"] == 20
    assert [g["game_id"] for g in result["rows"]] == [str(i) for i in range(101, 121)]
    assert sum("odds-api.io" in c[1] for c in http.calls) == 4
    assert result["counts"]["total_requests"] == 27


@pytest.mark.parametrize("when", [collector.START-timedelta(microseconds=1), collector.END])
def test_outside_fixed_window_has_no_source_or_price_io(environment, when):
    result, http, _ = run(environment, now=when)
    assert result["status"] == "outside_pilot"
    assert not http.calls and result["counts"]["total_requests"] == 0


def test_immutable_invocation_retry_does_not_repeat_http_or_replace_entry(environment):
    result, http, _ = run(environment)
    paths = list((environment/"data/runtime/availability/runs").glob("*.json"))
    original = paths[0].read_bytes()
    calls = len(http.calls)
    with pytest.raises(ValueError, match="already archived"):
        run(environment, http)
    assert len(http.calls) == calls and paths[0].read_bytes() == original
