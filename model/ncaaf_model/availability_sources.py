"""Pure source parsers; no HTTP, receipt clocks, outcomes or fitted models.

The public ACC renderer supplies name/status fields, exact Initial/Game Day
phases, and a nonempty all-Available declaration. It does not normalize names.
Our research matching rule is NFKD -> ASCII -> lowercase -> alphanumeric,
retaining every name token/suffix; collisions are ambiguous, never fuzzy matches.

Caller responsibilities: original-byte/receipt verification, source availability
and prior-game selection, the canonical game context, and reuse of the SAME
passing profile for current and previous report burdens. Successful parsing is
source-posted classification, not certification of a backend completed bit.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import re
import unicodedata
from zoneinfo import ZoneInfo


VERSION = "availability-sources-v1"
PHASES = frozenset({"Initial", "Game Day"})
STATUSES = frozenset({"Out", "Out - (1st Half)", "Doubtful", "Questionable",
                      "Probable", "Game Time Decision", "Available"})
POSITIONS = frozenset("QB RB FB WR TE OL OT OG C DL DE DT NT LB ILB OLB DB CB S FS SS PK K P LS".split())
MAX_HTML_BYTES = 8 * 1024 * 1024


class SourceError(ValueError):
    """A stable error code, never an original page or player value."""


def canonical_name(value: str) -> str:
    if not isinstance(value, str):
        raise SourceError("name_not_string")
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", value)
                  .encode("ascii", "ignore").decode().lower())


def _id(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise SourceError("invalid_identity")
    value = str(value)
    if not value.isascii() or not value.isdigit() or int(value) <= 0 or str(int(value)) != value:
        raise SourceError("invalid_identity")
    return value


def _time(value):
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError
        return result.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError) as exc:
        raise SourceError("invalid_context_time") from exc


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SourceError("duplicate_json_key")
        result[key] = value
    return result


def _bad_constant(_):
    raise SourceError("nonfinite_json")


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.scripts, self.current = [], None

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            # An assignment-looking data block is not an executable JS binding.
            # Preserve the ordinary absent-type ESPN scripts and JS MIME types.
            kind = next((value or "" for key, value in attrs if key == "type"), "")
            kind = kind.split(";", 1)[0].strip().lower()
            javascript = {"", "module", "application/ecmascript", "application/javascript",
                          "application/x-ecmascript", "application/x-javascript",
                          "text/ecmascript", "text/javascript", "text/jscript", "text/livescript",
                          "text/x-ecmascript", "text/x-javascript",
                          *(f"text/javascript1.{i}" for i in range(6))}
            self.current = None if kind not in javascript or any(key == "src" for key, _ in attrs) else []

    def handle_data(self, data):
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.current is not None:
            self.scripts.append("".join(self.current))
            self.current = None


def _skip_regexp(script, start):
    """Skip a JS regexp literal, including escaped characters and character classes."""
    i, in_class = start + 1, False
    while i < len(script):
        char = script[i]
        if char in "\r\n":
            raise SourceError("unterminated_javascript_regexp")
        if char == "\\":
            if i + 1 >= len(script) or script[i + 1] in "\r\n":
                raise SourceError("unterminated_javascript_regexp")
            i += 2
            continue
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif char == "/" and not in_class:
            i += 1
            while i < len(script) and script[i].isascii() and script[i].isalpha():
                i += 1
            return i
        i += 1
    raise SourceError("unterminated_javascript_regexp")


def extract_fitt(body: bytes | str) -> dict:
    """Read one static, top-level window['__espnfitt__'] JSON assignment.

    Strings/comments/regexp literals are skipped, not executed. Expressions, duplicate bindings,
    duplicate JSON keys and nonfinite constants are refused. This deliberately
    does not evaluate arbitrary JavaScript or search unrelated nested objects.
    """
    if not isinstance(body, (bytes, str)):
        raise SourceError("html_body_type")
    raw = body if isinstance(body, bytes) else body.encode("utf-8")
    if len(raw) > MAX_HTML_BYTES:
        raise SourceError("html_body_limit")
    try:
        parser = _Scripts()
        parser.feed(raw.decode("utf-8-sig"))
        parser.close()
    except (UnicodeError, ValueError) as exc:
        raise SourceError("invalid_html_encoding") from exc
    binding = re.compile(r"window\s*\[\s*(['\"])__espnfitt__\1\s*\]\s*=\s*")
    identifier_pattern = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
    number_pattern = re.compile(r"[0-9][A-Za-z0-9_.]*")
    decoder = json.JSONDecoder(object_pairs_hook=_unique_object, parse_constant=_bad_constant)
    found = []
    for script in parser.scripts:
        i, stack, last_token, regexp_allowed = 0, [], None, True
        while i < len(script):
            char = script[i]
            if script.startswith("//", i):
                j = script.find("\n", i + 2)
                i = len(script) if j < 0 else j + 1
                continue
            if script.startswith("/*", i):
                j = script.find("*/", i + 2)
                i = len(script) if j < 0 else j + 2
                continue
            if char in "\"'`":
                quote = char
                last_token = "string"
                regexp_allowed = False
                i += 1
                while i < len(script):
                    if script[i] == "\\":
                        i += 2
                    elif script[i] == quote:
                        i += 1
                        break
                    else:
                        i += 1
                continue
            if char == "/":
                if regexp_allowed:
                    i = _skip_regexp(script, i)
                    last_token, regexp_allowed = "regexp", False
                else:
                    # After an expression this is division. A closing ordinary
                    # parenthesis/brace can also end a statement; without a full
                    # JS grammar that case is ambiguous and must not expose text
                    # inside a possible regexp as a new top-level assignment.
                    if last_token in (")", "}"):
                        raise SourceError("ambiguous_javascript_slash")
                    last_token, regexp_allowed = "/", True
                    i += 1
                continue
            statement_start = last_token in (None, ";")
            match = binding.match(script, i) if not stack and statement_start else None
            if match:
                try:
                    value, consumed = decoder.raw_decode(script[match.end():])
                except (ValueError, RecursionError) as exc:
                    if isinstance(exc, SourceError):
                        raise
                    raise SourceError("invalid_fitt_json") from exc
                end = match.end() + consumed
                if not isinstance(value, dict) or (script[end:].lstrip() and not script[end:].lstrip().startswith(";")):
                    raise SourceError("fitt_not_static_object")
                found.append(value)
                last_token = "object"
                regexp_allowed = False
                i = end
                continue
            identifier = identifier_pattern.match(script, i)
            if identifier:
                last_token = identifier.group()
                regexp_allowed = last_token in {"return", "throw", "case", "delete", "void", "typeof",
                                                 "new", "in", "instanceof", "yield", "await", "else", "do",
                                                 "break", "continue"}
                i += len(last_token)
                continue
            if char.isascii() and char.isdigit():
                number = number_pattern.match(script, i).group()
                last_token, regexp_allowed = "number", False
                i += len(number)
                continue
            if script[i:i + 2] in ("++", "--"):
                last_token = script[i:i + 2]
                i += 2
                continue
            if char in "([{":
                control = char == "(" and last_token in {"if", "while", "for", "with", "switch", "catch"}
                stack.append((char, control))
                regexp_allowed = True
            elif char in ")]}":
                opening = stack.pop() if stack else None
                regexp_allowed = char == ")" and opening == ("(", True)
            elif char in ";,:=!?~+-*%&|^<>":
                regexp_allowed = True
            elif char == ".":
                regexp_allowed = False
            if not char.isspace():
                last_token = char
            i += 1
    if len(found) != 1:
        raise SourceError("missing_or_duplicate_fitt")
    return found[0]


def _content(source):
    value = source if isinstance(source, dict) else extract_fitt(source)
    try:
        content = value["page"]["content"]
        if not isinstance(content, dict):
            raise TypeError
        return content
    except (KeyError, TypeError) as exc:
        raise SourceError("missing_page_content") from exc


def _result(kind, **fields):
    return {"schema_version": VERSION, "kind": kind, "valid": False, "unknown": True,
            "reasons": [], **fields}


def _failure(result, exc):
    result["reasons"].append(str(exc) if isinstance(exc, SourceError) else "malformed_source")
    return result


def _success(result):
    result.update(valid=True, unknown=False)
    return result


def parse_roster(source, *, team_id, season=2026) -> dict:
    """Return source IDs/names/positions and explicit team-scoped name collisions."""
    out = _result("roster", team_id=str(team_id), season=season, athletes=[], by_id={},
                  name_to_ids={}, ambiguous_names=[], unknown_position_ids=[])
    try:
        tid = _id(team_id)
        if type(season) is not int or season != 2026:
            raise SourceError("unsupported_roster_season")
        roster = _content(source)["roster"]
        if _id(roster["team"]["id"]) != tid or str(roster["metadata"]["season"]) != str(season):
            raise SourceError("roster_context_mismatch")
        out["team_name"] = roster["team"].get("displayName")
        groups = roster["groups"]
        if not isinstance(groups, list):
            raise SourceError("invalid_roster_groups")
        names = defaultdict(list)
        for group in groups:
            rows = group["athletes"]
            if not isinstance(rows, list):
                raise SourceError("invalid_roster_rows")
            for row in rows:
                aid, raw_name = _id(row["id"]), row["name"]
                name = canonical_name(raw_name)
                if not name:
                    raise SourceError("empty_roster_name")
                if aid in out["by_id"]:
                    raise SourceError("duplicate_roster_id")
                uid = row.get("uid")
                if uid is not None and uid != "s:20~l:23~a:" + aid:
                    raise SourceError("roster_uid_mismatch")
                position = row.get("position")
                item = {"athlete_id": aid, "raw_name": raw_name, "canonical_name": name,
                        "uid": uid, "jersey": row.get("jersey"), "position": position,
                        "position_known": isinstance(position, str) and position in POSITIONS}
                out["athletes"].append(item)
                out["by_id"][aid] = item
                names[name].append(aid)
                if not item["position_known"]:
                    out["unknown_position_ids"].append(aid)
        if not out["athletes"]:
            raise SourceError("empty_roster")
        out["name_to_ids"] = dict(names)
        out["ambiguous_names"] = sorted(name for name, ids in names.items() if len(ids) != 1)
        return _success(out)
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        return _failure(out, exc)


def _completed_game(source, game_id, home_id, away_id, kickoff):
    g = _content(source)["gamepackage"]
    gid, hid, aid = _id(game_id), _id(home_id), _id(away_id)
    if hid == aid:
        raise SourceError("duplicate_game_teams")
    strip, info = g["gmStrp"], g["gmInfo"]
    if (_id(strip["gid"]) != gid or strip["uid"] != f"s:20~l:23~e:{gid}"
            or g["compUID"] != f"s:20~l:23~e:{gid}~c:{gid}"
            or _id(g["prsdTms"]["home"]["id"]) != hid
            or _id(g["prsdTms"]["away"]["id"]) != aid
            or _time(strip["dt"]) != _time(kickoff) or _time(info["dtTm"]) != _time(kickoff)):
        raise SourceError("passing_game_context_mismatch")
    status = strip["status"]
    final = re.compile(r"Final(?:/(?:[1-9][0-9]*)?OT)?\Z")
    if (strip.get("statusState") != "post" or status.get("state") != "post"
            or info.get("gameState") != "post" or strip.get("tbd") is not False
            or info.get("dtTmVld") is not True
            or not all(isinstance(status.get(k), str) and final.fullmatch(status[k])
                       for k in ("desc", "det", "statusPrimary"))):
        raise SourceError("prior_game_not_explicit_final")
    return g


def verify_completed_game(source, *, game_id, home_id, away_id, kickoff) -> dict:
    """Verify the designated prior event without returning any scores/outcomes.

    An optional boxscore link is preserved literally, and resolved only when it
    is the exact same-game public ESPN route. Missing links remain missing; no
    endpoint is invented from a game ID. This does not verify receipt timing.
    """
    out = _result("completed_game", game_id=str(game_id), home_id=str(home_id),
                  away_id=str(away_id), kickoff=kickoff, completed=False,
                  boxscore_href=None, boxscore_url=None)
    try:
        g = _completed_game(source, game_id, home_id, away_id, kickoff)
        header_links = g.get("hdrLnks", {})
        link = header_links.get("boxscore")
        if link is not None:
            href = link["href"]
            path = f"/college-football/boxscore/_/gameId/{_id(game_id)}"
            if href not in (path, "https://www.espn.com" + path):
                raise SourceError("invalid_observed_boxscore_link")
            out["boxscore_href"] = href
            out["boxscore_url"] = "https://www.espn.com" + href if href == path else href
        out["completed"] = True
        return _success(out)
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        return _failure(out, exc)


def _catt(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9]+/[0-9]+", value) is None:
        raise SourceError("invalid_C_ATT")
    completed, attempted = map(int, value.split("/"))
    if completed > attempted:
        raise SourceError("completions_exceed_attempts")
    return completed, attempted


def parse_passing(source, *, game_id, team_id, home_id, away_id, kickoff, roster=None) -> dict:
    """Parse the complete labeled table; denominator includes ALL passing athletes.

    Optional roster matching is diagnostic. Unrelated passers need not be QB or
    match that roster for their attempts to remain in the published denominator.
    A valid profile with zero team attempts records that fact without a 0/0 share.
    """
    out = _result("passing", game_id=str(game_id), team_id=str(team_id), kickoff=kickoff,
                  athletes=[], attempts_by_id={}, weights_by_id=None, total_attempts=None,
                  total_completions=None, roster_unmatched_ids=[])
    try:
        tid = _id(team_id)
        if tid not in {_id(home_id), _id(away_id)}:
            raise SourceError("passing_team_outside_game")
        g = _completed_game(source, game_id, home_id, away_id, kickoff)
        teams = [t for t in g["bxscr"] if _id(t["tm"]["id"]) == tid]
        if len(teams) != 1 or teams[0]["tm"]["hm"] is not (tid == _id(home_id)):
            raise SourceError("ambiguous_passing_team")
        cats = [c for c in teams[0]["stats"] if c["type"] == "passing"]
        if len(cats) != 1:
            raise SourceError("missing_or_duplicate_passing_category")
        c = cats[0]
        keys, labels, totals, rows = (c[k] for k in ("keys", "lbls", "ttls", "athlts"))
        if (not all(isinstance(x, list) for x in (keys, labels, totals, rows))
                or len(keys) != len(labels) or len(keys) != len(totals)
                or keys.count("completions/passingAttempts") != 1 or labels.count("C/ATT") != 1):
            raise SourceError("invalid_passing_headers")
        j = keys.index("completions/passingAttempts")
        if labels[j] != "C/ATT":
            raise SourceError("passing_key_label_mismatch")
        tc, ta = _catt(totals[j])
        for row in rows:
            athlete, stats = row["athlt"], row["stats"]
            aid = _id(athlete["id"])
            if aid in out["attempts_by_id"]:
                raise SourceError("duplicate_passing_id")
            if not isinstance(stats, list) or len(stats) != len(keys):
                raise SourceError("passing_stats_alignment")
            uid = athlete.get("uid")
            if uid is not None and uid != "s:20~l:23~a:" + aid:
                raise SourceError("passing_uid_mismatch")
            cc, aa = _catt(stats[j])
            known = roster.get("by_id", {}).get(aid) if (isinstance(roster, dict)
                    and roster.get("valid") and roster.get("team_id") == tid) else None
            if known is None:
                out["roster_unmatched_ids"].append(aid)
            out["athletes"].append({"athlete_id": aid, "raw_name": athlete.get("dspNm"),
                "uid": uid, "jersey": athlete.get("jersey"), "completions": cc, "attempts": aa,
                "roster_position": known.get("position") if known else None})
            out["attempts_by_id"][aid] = aa
        if sum(a["attempts"] for a in out["athletes"]) != ta or sum(a["completions"] for a in out["athletes"]) != tc:
            raise SourceError("passing_team_totals_mismatch")
        out.update(total_attempts=ta, total_completions=tc,
                   weights_by_id={aid: attempts / ta for aid, attempts in out["attempts_by_id"].items()} if ta else None)
        return _success(out)
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        return _failure(out, exc)


def parse_acc_team(rows, *, team_id, roster=None, passing=None) -> dict:
    """Compute one team's defined full-game Out QB burden, or explicit unknown."""
    out = _result("acc_team", team_id=str(team_id), full_out_burden=None, explicit_none=False,
                  rows=rows, full_out_rows=[], first_half_out_rows=[], unmatched_out_names=[], ambiguous_out_names=[])
    try:
        tid = _id(team_id)
        if not isinstance(rows, list) or not rows:
            raise SourceError("empty_or_invalid_team_rows")
        if any(not isinstance(row, dict) or not isinstance(row.get("status"), str)
               or row["status"] not in STATUSES for row in rows):
            raise SourceError("malformed_row_or_unknown_status")
        out["explicit_none"] = all(row["status"] == "Available" for row in rows)
        if not out["explicit_none"] and any(not isinstance(row.get("name"), str) or not canonical_name(row["name"])
                                            for row in rows):
            raise SourceError("invalid_player_name")
        out["first_half_out_rows"] = [dict(row) for row in rows if row["status"] == "Out - (1st Half)"]
        full = [row for row in rows if row["status"] == "Out"]
        if not full:
            out["full_out_burden"] = 0.0
            return _success(out)
        if not isinstance(roster, dict) or not roster.get("valid") or roster.get("team_id") != tid:
            raise SourceError("out_roster_unavailable_or_wrong_team")
        seen, burden = set(), 0.0
        for row in full:
            name = canonical_name(row["name"])
            if sum(canonical_name(other["name"]) == name for other in rows) != 1:
                raise SourceError("duplicate_or_conflicting_full_out_row")
            ids = roster.get("name_to_ids", {}).get(name, [])
            if len(ids) != 1:
                out["unmatched_out_names" if not ids else "ambiguous_out_names"].append(row["name"])
                continue
            aid = ids[0]
            if aid in seen:
                raise SourceError("duplicate_full_out_identity")
            seen.add(aid)
            athlete = roster["by_id"][aid]
            detail = {"raw_name": row["name"], "canonical_name": name, "athlete_id": aid,
                      "position": athlete["position"], "attempts": None, "contribution": None}
            out["full_out_rows"].append(detail)
            if not athlete.get("position_known"):
                raise SourceError("out_position_unknown")
            if athlete["position"] != "QB":
                detail["contribution"] = 0.0
                continue
            if not isinstance(passing, dict) or not passing.get("valid") or passing.get("team_id") != tid:
                raise SourceError("out_QB_passing_unavailable_or_wrong_team")
            counts = passing["attempts_by_id"]
            total = passing["total_attempts"]
            if (not isinstance(counts, dict) or type(total) is not int or total < 0
                    or any(_id(key) != key or type(value) is not int or value < 0
                           for key, value in counts.items()) or sum(counts.values()) != total):
                raise SourceError("invalid_passing_profile")
            attempts = counts.get(aid, 0)
            detail["attempts"] = attempts
            if attempts == 0:
                detail["contribution"] = 0.0
            else:
                detail["contribution"] = attempts / total
            burden += detail["contribution"]
        if out["unmatched_out_names"] or out["ambiguous_out_names"]:
            raise SourceError("full_out_identity_unknown")
        out["full_out_burden"] = burden
        return _success(out)
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        return _failure(out, exc)


def parse_acc_report(report, *, teams, expected_phase=None, game_id=None, kickoff=None) -> dict:
    """Parse one raw report value against exactly two caller-supplied team contexts.

    ``teams`` maps IDs to ``{source_names: list|dict, roster: result|None,
    passing: result|None}``. Results map those same IDs to team diagnostics.
    Optional game_id/kickoff preserve and verify the caller's matchup context;
    actual HTTP receipt and issue-time availability checks belong to the caller.
    """
    phase = report.get("ReportType") if isinstance(report, dict) else None
    out = _result("acc_report", phase=phase, game_id=game_id, teams={}, full_out_burden=None,
                  source_posted=False, context_kickoff_verified=False)
    try:
        if phase not in PHASES:
            raise SourceError("pending_report" if phase == "Report Pending" else "unknown_report_phase")
        if expected_phase is not None and phase != expected_phase:
            raise SourceError("unexpected_report_phase")
        if not isinstance(teams, dict) or len(teams) != 2:
            raise SourceError("invalid_team_contexts")
        contexts = {_id(tid): value for tid, value in teams.items()}
        if len(contexts) != 2:
            raise SourceError("duplicate_team_context")
        blocks = report["games"]
        if not isinstance(blocks, list) or len(blocks) != 2:
            raise SourceError("invalid_report_team_blocks")
        if game_id is not None:
            out["game_id"] = _id(game_id)
        if kickoff is not None:
            eastern = _time(kickoff).astimezone(ZoneInfo("America/New_York"))
            if (report.get("conferenceTimeZone") != "ET" or report["footer"].get("date") != eastern.strftime("%Y-%m-%d")
                    or report["footer"].get("time") != eastern.strftime("%H:%M:%S")):
                raise SourceError("report_kickoff_context_mismatch")
            out["context_kickoff_verified"] = True
        out["source_posted"] = True
        out["source_issue_labels"] = {key: report.get(key) for key in ("publishDate", "postedTime", "conferenceTimeZone")}
        for block in blocks:
            source_name = block["teamName"]
            name = canonical_name(source_name)
            matches = []
            for tid, context in contexts.items():
                aliases = context["source_names"]
                aliases = aliases.values() if isinstance(aliases, dict) else aliases
                if isinstance(aliases, str) or not aliases:
                    raise SourceError("invalid_context_source_names")
                if name and name in {canonical_name(value) for value in aliases}:
                    matches.append(tid)
            if len(matches) != 1 or matches[0] in out["teams"]:
                raise SourceError("missing_or_ambiguous_report_team")
            tid = matches[0]
            context = contexts[tid]
            result = parse_acc_team(block["rows"], team_id=tid, roster=context.get("roster"), passing=context.get("passing"))
            result["source_team_name"] = source_name
            out["teams"][tid] = result
        if any(not row["valid"] for row in out["teams"].values()):
            raise SourceError("team_burden_unknown")
        out["full_out_burden"] = sum(row["full_out_burden"] for row in out["teams"].values())
        return _success(out)
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        return _failure(out, exc)
