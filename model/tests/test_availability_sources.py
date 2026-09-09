"""Synthetic source contracts only: no HTTP, ignored archives or fitted models."""
from copy import deepcopy
import json

import pytest

from ncaaf_model import availability_sources as s


GID, HOME, AWAY = "401900001", "183", "25"
KICKOFF = "2026-09-05T16:00:00Z"


def fitt(content):
    return {"page": {"content": content}}


def html(value, prefix=""):
    return ("<html><script>" + prefix + "window['__espnfitt__']=" +
            json.dumps(value) + ";</script></html>").encode()


def athlete(aid="1001", name="C.J. O’Neill Jr.", position="QB"):
    return {"id": aid, "uid": "s:20~l:23~a:" + aid, "name": name,
            "position": position, "jersey": "7"}


def roster_source():
    return fitt({"roster": {"team": {"id": HOME, "displayName": "Home School"},
        "metadata": {"season": "2026"}, "groups": [{"athletes": [
            athlete(), athlete("1002", "Other Quarterback"),
            athlete("1003", "Receiver One", "WR"),
            athlete("1004", "Unused Quarterback"),
        ]}]}})


def roster():
    return s.parse_roster(roster_source(), team_id=HOME)


def passing_source():
    rows = []
    for aid, name, catt in [("1001", "C.J. O’Neill Jr.", "4/10"),
                            ("1002", "Other Quarterback", "3/6"),
                            ("1003", "Receiver One", "1/4")]:
        rows.append({"athlt": {"id": aid, "uid": "s:20~l:23~a:" + aid,
                               "dspNm": name, "jersey": "7"}, "stats": [catt, "99"]})
    return fitt({"gamepackage": {
        "gmStrp": {"gid": GID, "uid": f"s:20~l:23~e:{GID}", "dt": KICKOFF,
            "tbd": False, "statusState": "post", "status": {
                "state": "post", "desc": "Final", "det": "Final", "statusPrimary": "Final"}},
        "compUID": f"s:20~l:23~e:{GID}~c:{GID}",
        "gmInfo": {"dtTm": KICKOFF, "dtTmVld": True, "gameState": "post"},
        "prsdTms": {"home": {"id": HOME}, "away": {"id": AWAY}},
        "bxscr": [{"tm": {"id": HOME, "hm": True}, "stats": [{"type": "passing",
            "keys": ["completions/passingAttempts", "passingYards"],
            "lbls": ["C/ATT", "YDS"], "ttls": ["8/20", "297"], "athlts": rows}]}],
    }})


def passing(source=None, **kwargs):
    context = dict(game_id=GID, team_id=HOME, home_id=HOME, away_id=AWAY, kickoff=KICKOFF)
    context.update(kwargs)
    return s.parse_passing(passing_source() if source is None else source, **context)


def category(source):
    return source["page"]["content"]["gamepackage"]["bxscr"][0]["stats"][0]


def team(rows, **kwargs):
    return s.parse_acc_team(rows, team_id=HOME, **kwargs)


def out(name="CJ O'Neill Jr"):
    return {"name": name, "status": "Out"}


def report():
    return {"ReportType": "Initial", "games": [
        {"teamName": "Away School", "rows": [{"name": "None", "status": "Available"}]},
        {"teamName": "Home School", "rows": [out()]}],
        "conferenceTimeZone": "ET", "publishDate": "2026-09-02", "postedTime": "20:00:00",
        "footer": {"date": "2026-09-05", "time": "12:00:00"}}


def contexts():
    return {HOME: {"source_names": ["Home School"], "roster": roster(), "passing": passing()},
            AWAY: {"source_names": {"displayName": "Away School"}, "roster": None, "passing": None}}


def test_static_extraction_skips_strings_comments_and_other_json():
    payload = fitt({"large_identity": 9007199254740993, "name": "O'Neill"})
    prefix = ("window['__CONFIG__']={\"unused\":true};"
              "const decoy=\"window['__espnfitt__']={bad}\";"
              "/* window['__espnfitt__']={bad}; */\n"
              "// window['__espnfitt__']={bad};\n/* plain final comment */\n")
    assert s.extract_fitt(html(payload, prefix)) == payload


@pytest.mark.parametrize("script", [
    "window['__espnfitt__']={};window['__espnfitt__']={};",
    "window['__espnfitt__']={\"a\":1,\"a\":2};",
    "window['__espnfitt__']={\"a\":NaN};",
    "window['__espnfitt__']={\"a\":Infinity};",
    "window['__espnfitt__']={};window[\"__espnfitt__\"]={};",
    "window['__espnfitt__']={} || {} ;",
    "window['__espnfitt__']=JSON.parse('{}');",
    "window['__espnfitt__']=[];",
    "function x(){window['__espnfitt__']={};}",
    "notwindow['__espnfitt__']={};",
    "other.window['__espnfitt__']={};",
    "const x=window['__espnfitt__']={};",
])
def test_nonstatic_duplicate_or_nonfinite_bindings_refused(script):
    with pytest.raises(s.SourceError):
        s.extract_fitt(("<script>" + script + "</script>").encode())


@pytest.mark.parametrize("body", [b"\xff", b"<script>window['__espnfitt__']={}", b"<html></html>", None])
def test_unreadable_or_incomplete_html_refused(body):
    with pytest.raises(s.SourceError):
        s.extract_fitt(body)


def test_html_size_cap(monkeypatch):
    monkeypatch.setattr(s, "MAX_HTML_BYTES", 4)
    with pytest.raises(s.SourceError, match="html_body_limit"):
        s.extract_fitt(html({}))


def test_external_script_text_is_not_an_inline_binding():
    with pytest.raises(s.SourceError):
        s.extract_fitt(b"<script src='/external.js'>window['__espnfitt__']={};</script>")


@pytest.mark.parametrize("kind", ["application/json", "application/ld+json", "text/plain", "importmap"])
def test_inert_script_cannot_supply_a_fitt_binding_or_duplicate_the_real_one(kind):
    inert = f'<script type="{kind}">window[\'__espnfitt__\']={{"fake":true}};</script>'
    with pytest.raises(s.SourceError, match="missing_or_duplicate_fitt"):
        s.extract_fitt(inert)
    expected = fitt({"real": True})
    assert s.extract_fitt(inert.encode() + html(expected)) == expected


@pytest.mark.parametrize("kind", ["text/javascript", "application/javascript; charset=utf-8", "module"])
def test_explicit_javascript_type_keeps_genuine_static_binding(kind):
    expected = fitt({"real": True})
    value = html(expected).decode().replace("<script>", f'<script type="{kind}">')
    assert s.extract_fitt(value) == expected


@pytest.mark.parametrize("prefix", [
    'const r=/;window[\'__espnfitt__\']={"fake":true};/;',
    'if (ready) /;window[\'__espnfitt__\']={"fake":true};/.test(value);',
    r'const r=/[\/;]window[\'__espnfitt__\']={"fake":true};/gi;',
])
def test_regex_literal_text_is_not_an_assignment_and_does_not_hide_real_binding(prefix):
    with pytest.raises(s.SourceError, match="missing_or_duplicate_fitt"):
        s.extract_fitt("<script>" + prefix + "</script>")
    expected = fitt({"real": True})
    assert s.extract_fitt(html(expected, prefix)) == expected


def test_ordinary_division_before_static_binding_remains_supported():
    expected = fitt({"real": True})
    assert s.extract_fitt(html(expected, "const ratio=6/2;const another=ratio/3;")) == expected


def test_ambiguous_slash_cannot_expose_regex_decoy_as_static_binding():
    # A complete JS grammar would distinguish a statement block from an object
    # expression here. The limited extractor must not guess a decoy is data.
    body = '<script>if (ready) {}; /;window[\'__espnfitt__\']={"fake":true};/;</script>'
    with pytest.raises(s.SourceError):
        s.extract_fitt(body)
    ambiguous = '<script>if (ready) {} /;window[\'__espnfitt__\']={"fake":true};/;</script>'
    with pytest.raises(s.SourceError, match="ambiguous_javascript_slash"):
        s.extract_fitt(ambiguous)


def test_canonical_rule_preserves_all_tokens_suffixes_and_has_no_prefix_match():
    assert s.canonical_name(" C.J. O’Néill Jr. ") == "cjoneilljr"
    assert s.canonical_name("CJ O'Neill Jr") == "cjoneilljr"
    assert s.canonical_name("CJ O'Neill") != "cjoneilljr"
    assert s.canonical_name("Christopher O'Neill Jr") != "cjoneilljr"


def test_roster_accepts_exact_nullable_position_and_large_string_identity():
    source = roster_source()
    rows = source["page"]["content"]["roster"]["groups"][0]["athletes"]
    rows.append(athlete("9007199254740993", "Large Id", None))
    result = s.parse_roster(html(source), team_id=HOME)
    assert result["valid"] and len(result["athletes"]) == 5
    assert result["unknown_position_ids"] == ["9007199254740993"]
    assert result["by_id"]["1001"]["raw_name"] == "C.J. O’Neill Jr."


@pytest.mark.parametrize("kwargs", [{"team_id": AWAY}, {"team_id": True},
    {"team_id": "0183"}, {"team_id": HOME, "season": 2025},
    {"team_id": HOME, "season": 2026.0}, {"team_id": HOME, "season": "2026"}])
def test_roster_contexts_are_exact(kwargs):
    assert not s.parse_roster(roster_source(), **kwargs)["valid"]


@pytest.mark.parametrize("mutation", ["duplicate", "wrong_uid", "empty", "float_id", "wrong_season"])
def test_roster_malformed_identity_refused(mutation):
    source = roster_source()
    r = source["page"]["content"]["roster"]
    rows = r["groups"][0]["athletes"]
    if mutation == "duplicate": rows.append(deepcopy(rows[0]))
    if mutation == "wrong_uid": rows[0]["uid"] = "s:20~l:23~a:9999"
    if mutation == "empty": rows.clear()
    if mutation == "float_id": rows[0]["id"] = 1001.0
    if mutation == "wrong_season": r["metadata"]["season"] = "2025"
    assert not s.parse_roster(source, team_id=HOME)["valid"]


def test_name_collision_recorded_without_destroying_unrelated_roster():
    source = roster_source()
    source["page"]["content"]["roster"]["groups"][0]["athletes"].append(athlete("1005", "CJ O'Neill Jr"))
    r = s.parse_roster(source, team_id=HOME)
    assert r["valid"] and r["ambiguous_names"] == ["cjoneilljr"]
    result = team([out()], roster=r, passing=passing())
    assert result["unknown"] and result["ambiguous_out_names"]
    assert team([out("Receiver One")], roster=r)["full_out_burden"] == 0


def test_full_table_denominator_keeps_non_qb_and_unmatched_passers():
    result = passing(html(passing_source()), roster=roster())
    assert result["valid"] and result["total_attempts"] == 20
    assert result["weights_by_id"] == {"1001": .5, "1002": .3, "1003": .2}
    assert result["roster_unmatched_ids"] == []
    result_without_roster = passing()
    assert result_without_roster["valid"]
    assert result_without_roster["weights_by_id"] == result["weights_by_id"]
    assert len(result_without_roster["roster_unmatched_ids"]) == 3


@pytest.mark.parametrize("key,value", [("game_id", "401900002"), ("team_id", "999"),
    ("home_id", AWAY), ("away_id", HOME), ("kickoff", "2026-09-05T17:00Z"),
    ("kickoff", "2026-09-05T16:00")])
def test_passing_exact_game_context(key, value):
    assert passing(**{key: value})["unknown"]


@pytest.mark.parametrize("field,value", [("statusState", "pre"), ("tbd", True),
    ("status.desc", "Scheduled"), ("status.det", "Final - postponed"),
    ("status.statusPrimary", "Suspended"), ("status.state", "in")])
def test_passing_requires_explicit_final(field, value):
    source = passing_source()
    strip = source["page"]["content"]["gamepackage"]["gmStrp"]
    if "." in field: strip["status"][field.split(".")[1]] = value
    else: strip[field] = value
    assert passing(source)["unknown"]


def test_overtime_final_and_wrong_roster_is_only_unmatched_diagnostic():
    source = passing_source()
    status = source["page"]["content"]["gamepackage"]["gmStrp"]["status"]
    status.update(desc="Final/2OT", det="Final/2OT", statusPrimary="Final/2OT")
    r = roster()
    r["team_id"] = AWAY
    result = passing(source, roster=r)
    assert result["valid"] and len(result["roster_unmatched_ids"]) == 3


@pytest.mark.parametrize("href", [f"/college-football/boxscore/_/gameId/{GID}",
    f"https://www.espn.com/college-football/boxscore/_/gameId/{GID}", None])
def test_public_completion_helper_preserves_only_observed_link_and_no_scores(href):
    source = passing_source()
    g = source["page"]["content"]["gamepackage"]
    g["gmStrp"]["score"] = "DO_NOT_RETURN"
    if href is not None: g["hdrLnks"] = {"boxscore": {"href": href}}
    result = s.verify_completed_game(source, game_id=GID, home_id=HOME, away_id=AWAY, kickoff=KICKOFF)
    assert result["valid"] and result["completed"]
    assert result["boxscore_href"] == href
    assert result["boxscore_url"] == (f"https://www.espn.com/college-football/boxscore/_/gameId/{GID}" if href else None)
    assert "DO_NOT_RETURN" not in json.dumps(result)


@pytest.mark.parametrize("href", [f"https://evil.example/college-football/boxscore/_/gameId/{GID}",
    "/college-football/boxscore/_/gameId/401999999", f"//www.espn.com/college-football/boxscore/_/gameId/{GID}",
    f"/college-football/boxscore/_/gameId/{GID}?other=1", "", 123])
def test_completion_helper_rejects_misassigned_or_nonliteral_link(href):
    source = passing_source()
    source["page"]["content"]["gamepackage"]["hdrLnks"] = {"boxscore": {"href": href}}
    result = s.verify_completed_game(source, game_id=GID, home_id=HOME, away_id=AWAY, kickoff=KICKOFF)
    assert result["unknown"] and result["boxscore_url"] is None


def test_public_completion_helper_does_not_accept_pending_or_wrong_identity():
    source = passing_source()
    source["page"]["content"]["gamepackage"]["gmStrp"]["status"]["state"] = "in"
    assert s.verify_completed_game(source, game_id=GID, home_id=HOME, away_id=AWAY, kickoff=KICKOFF)["unknown"]
    assert s.verify_completed_game(passing_source(), game_id="401999999", home_id=HOME, away_id=AWAY, kickoff=KICKOFF)["unknown"]


@pytest.mark.parametrize("mutation", ["duplicate_team", "duplicate_category", "missing_table",
    "duplicate_key", "duplicate_label", "misaligned_label", "short_totals", "short_stats",
    "duplicate_athlete", "wrong_uid", "sum_attempts", "sum_completions"])
def test_table_completeness_and_identity_checks(mutation):
    source = passing_source()
    g = source["page"]["content"]["gamepackage"]
    c = category(source)
    if mutation == "duplicate_team": g["bxscr"].append(deepcopy(g["bxscr"][0]))
    if mutation == "duplicate_category": g["bxscr"][0]["stats"].append(deepcopy(c))
    if mutation == "missing_table": del g["bxscr"]
    if mutation == "duplicate_key": c["keys"][1] = c["keys"][0]
    if mutation == "duplicate_label": c["lbls"][1] = c["lbls"][0]
    if mutation == "misaligned_label": c["lbls"].reverse()
    if mutation == "short_totals": c["ttls"].pop()
    if mutation == "short_stats": c["athlts"][0]["stats"].pop()
    if mutation == "duplicate_athlete": c["athlts"].append(deepcopy(c["athlts"][0]))
    if mutation == "wrong_uid": c["athlts"][0]["athlt"]["uid"] = "s:20~l:23~a:9"
    if mutation == "sum_attempts": c["ttls"][0] = "8/21"
    if mutation == "sum_completions": c["ttls"][0] = "9/20"
    assert passing(source)["unknown"]


@pytest.mark.parametrize("catt", ["11/10", "4/-10", "4/10.0", "4 / 10", "4-10", True, None, 10])
def test_catt_is_explicit_integer_pair(catt):
    source = passing_source()
    category(source)["athlts"][0]["stats"][0] = catt
    assert passing(source)["unknown"]


@pytest.mark.parametrize("status", sorted(s.STATUSES - {"Out"}))
def test_no_literal_full_out_is_mathematical_zero_without_profiles(status):
    result = team([{"name": "Unmatched Source Player", "status": status}])
    assert result["valid"] and result["full_out_burden"] == 0
    assert result["explicit_none"] is (status == "Available")
    assert bool(result["first_half_out_rows"]) is (status == "Out - (1st Half)")


def test_nonempty_all_available_placeholder_requires_no_identity():
    result = team([{"status": "Available"}])
    assert result["valid"] and result["explicit_none"] and result["full_out_burden"] == 0


@pytest.mark.parametrize("rows", [[], None, {}, [None], [{"name": "x", "status": "out"}],
    [{"name": "x", "status": "Unknown"}], [{"status": "Questionable"}],
    [{"name": "", "status": "Out"}], [{"name": "x", "status": None}]])
def test_empty_malformed_or_unknown_status_never_coerces_zero(rows):
    result = team(rows)
    assert result["unknown"] and result["full_out_burden"] is None


def test_actual_full_out_dependencies_and_exact_qb_share():
    assert team([out()])["unknown"]
    assert team([out()], roster=roster())["unknown"]
    assert team([out("Receiver One")], roster=roster())["full_out_burden"] == 0
    result = team([out()], roster=roster(), passing=passing())
    assert result["valid"] and result["full_out_burden"] == .5
    assert result["full_out_rows"][0]["raw_name"] == "CJ O'Neill Jr"
    assert team([out("Unused Quarterback")], roster=roster(), passing=passing())["full_out_burden"] == 0


def test_zero_attempt_complete_profile_is_known_zero_not_zero_division():
    source = passing_source()
    c = category(source)
    c["athlts"], c["ttls"][0] = [], "0/0"
    profile = passing(source)
    assert profile["valid"] and profile["weights_by_id"] is None
    assert team([out()], roster=roster(), passing=profile)["full_out_burden"] == 0


@pytest.mark.parametrize("name", ["CJ O'Neill", "Christopher O'Neill Jr", "Missing Person"])
def test_no_prefix_initial_expansion_or_suffix_dropping(name):
    result = team([out(name)], roster=roster(), passing=passing())
    assert result["unknown"] and result["unmatched_out_names"] == [name]


@pytest.mark.parametrize("status", ["Out", "Available", "Out - (1st Half)", "Questionable"])
def test_duplicated_or_conflicting_full_out_identity_unavailable(status):
    result = team([out(), {"name": "C.J. O’Neill Jr.", "status": status}], roster=roster(), passing=passing())
    assert result["unknown"] and "duplicate_or_conflicting_full_out_row" in result["reasons"]


def test_unknown_position_is_not_assumed_non_qb():
    source = roster_source()
    source["page"]["content"]["roster"]["groups"][0]["athletes"][0]["position"] = None
    r = s.parse_roster(source, team_id=HOME)
    assert r["valid"]
    assert team([out()], roster=r, passing=passing())["reasons"] == ["out_position_unknown"]


@pytest.mark.parametrize("mutation", ["negative", "float", "boolean", "sum", "missing", "wrong_team"])
def test_tampered_count_profiles_do_not_create_false_burdens(mutation):
    profile = passing()
    if mutation == "negative": profile["attempts_by_id"]["1001"] = -1
    if mutation == "float": profile["attempts_by_id"]["1001"] = 10.0
    if mutation == "boolean": profile["total_attempts"] = True
    if mutation == "sum": profile["total_attempts"] = 21
    if mutation == "missing": del profile["attempts_by_id"]
    if mutation == "wrong_team": profile["team_id"] = AWAY
    result = team([out()], roster=roster(), passing=profile)
    assert result["unknown"] and result["full_out_burden"] is None


def test_prior_and_current_share_same_weights_without_mutation():
    r, p = roster(), passing()
    before = deepcopy((r, p))
    previous = team([out("Other Quarterback")], roster=r, passing=p)
    current = team([out()], roster=r, passing=p)
    assert previous["full_out_burden"] == .3 and current["full_out_burden"] == .5
    assert (r, p) == before
    p["weights_by_id"] = {"1001": 999}
    assert team([out()], roster=r, passing=p)["full_out_burden"] == .5


def test_report_matches_unordered_teams_context_and_raw_phase():
    source, ctx = report(), contexts()
    original = deepcopy((source, ctx))
    result = s.parse_acc_report(source, teams=ctx, expected_phase="Initial", game_id=GID, kickoff=KICKOFF)
    assert result["valid"] and result["full_out_burden"] == .5
    assert result["source_posted"] and result["context_kickoff_verified"]
    assert result["teams"][AWAY]["explicit_none"]
    assert (source, ctx) == original
    source["ReportType"] = "Game Day"
    assert s.parse_acc_report(source, teams=ctx)["valid"]


@pytest.mark.parametrize("phase", ["Report Pending", "Update 1", "game day", None, "Initial "])
def test_pending_and_unknown_phases_unusable_even_all_available(phase):
    source = report()
    source["ReportType"] = phase
    for block in source["games"]: block["rows"] = [{"status": "Available"}]
    result = s.parse_acc_report(source, teams=contexts())
    assert result["unknown"] and result["full_out_burden"] is None and not result["source_posted"]


@pytest.mark.parametrize("mutation", ["wrong_time", "wrong_date", "wrong_zone", "duplicate_team",
    "missing_team", "unknown_team", "ambiguous_alias", "unknown_row", "wrong_phase"])
def test_report_context_and_coverage_fail_closed(mutation):
    source, ctx = report(), contexts()
    kwargs = dict(teams=ctx, kickoff=KICKOFF)
    if mutation == "wrong_time": source["footer"]["time"] = "13:00:00"
    if mutation == "wrong_date": source["footer"]["date"] = "2026-09-06"
    if mutation == "wrong_zone": source["conferenceTimeZone"] = "UTC"
    if mutation == "duplicate_team": source["games"][1]["teamName"] = "Away School"
    if mutation == "missing_team": source["games"].pop()
    if mutation == "unknown_team": source["games"][0]["teamName"] = "Unknown School"
    if mutation == "ambiguous_alias": ctx[HOME]["source_names"].append("Away School")
    if mutation == "unknown_row": source["games"][0]["rows"] = []
    if mutation == "wrong_phase": kwargs["expected_phase"] = "Game Day"
    result = s.parse_acc_report(source, **kwargs)
    assert result["unknown"] and result["full_out_burden"] is None
