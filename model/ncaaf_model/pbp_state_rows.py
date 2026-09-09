"""Pure, unvalidated raw-play state observations; no I/O, ratings or EPA.

Scores are reconstructed from the preceding consecutive archived post-play
record. First rows and first rows after numbered gaps have unavailable state.
Clock depletion describes
adjacent recorded game clocks, not measured between-snap tempo. Unknown helper
flags are counted; only explicit vetoes exclude a play. Missing response and
ambiguous pass/rush attribution remain missing, independently of one another.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import re

import numpy as np
import pandas as pd
from pandas.api.types import is_bool_dtype, is_integer_dtype

VERSION = "raw-play-state-rows-v1"
RAW_COLUMNS = (
    "season", "game_id", "id", "sequenceNumber", "game_play_number",
    "homeTeamId", "awayTeamId", "drive.id", "type.text", "orig_play_type", "text",
    "period.number", "clock.displayValue", "start.down", "start.distance",
    "start.yardsToEndzone", "start.team.id", "end.team.id", "statYardage",
    "homeScore", "awayScore", "scoringPlay", "isPenalty", "isTurnover",
    "penalty_flag", "penalty_no_play", "penalty_offset", "kneel_down",
    "kickoff_play", "punt_play",
)
SCHEDULE_COLUMNS = ("game_id", "season", "week", "game_date", "neutral_site",
                    "home_id", "away_id", "status")
INTEGER_COLUMNS = (
    "season", "game_id", "id", "sequenceNumber", "game_play_number", "homeTeamId",
    "awayTeamId", "period.number", "start.down", "start.distance",
    "start.yardsToEndzone", "start.team.id", "end.team.id", "statYardage",
    "homeScore", "awayScore",
)
FLAG_COLUMNS = ("scoringPlay", "isPenalty", "isTurnover", "penalty_flag",
                "penalty_no_play", "penalty_offset", "kneel_down", "kickoff_play", "punt_play")
STRING_COLUMNS = tuple(x for x in RAW_COLUMNS if x not in INTEGER_COLUMNS + FLAG_COLUMNS)
RUN_TYPES = frozenset(("Rush", "Rushing Touchdown"))
PASS_TYPES = frozenset(("Pass", "Pass Reception", "Pass Completion", "Pass Incompletion",
                       "Passing Touchdown", "Pass Reception Touchdown", "Sack", "Sack Touchdown",
                       "Interception", "Interception Return", "Interception Return Touchdown",
                       "Pass Interception", "Pass Interception Return", "Pass Interception Return Touchdown"))
FUMBLE_TYPES = frozenset(("Fumble Recovery (Own)", "Fumble Recovery (Own) Touchdown",
                         "Fumble Recovery (Opponent)", "Fumble Recovery (Opponent) Touchdown",
                         "Fumble Return Touchdown"))
LEGAL_TYPES = RUN_TYPES | PASS_TYPES | FUMBLE_TYPES
LOST_TYPES = frozenset(x for x in PASS_TYPES if "Interception" in x) | frozenset((
    "Sack Touchdown", "Fumble Recovery (Opponent)", "Fumble Recovery (Opponent) Touchdown",
    "Fumble Return Touchdown"))
OFFENSIVE_TD_TYPES = frozenset(("Rushing Touchdown", "Passing Touchdown",
                               "Pass Reception Touchdown", "Fumble Recovery (Own) Touchdown"))
OUTPUT_COLUMNS = (
    "game_id", "season", "week", "team_id", "opponent_id", "available_at",
    "is_home", "neutral_site", "down", "distance", "yards_to_endzone", "score_margin",
    "period", "half_seconds_remaining", "pass_play", "clock_seconds", "conversion",
    "play_id", "sequence_number", "game_play_number", "drive_id", "pre_home_score", "pre_away_score",
)
_PENALTY = re.compile(r"\bpenalt(?:y|ies)\b|\bno[ -]?play\b|\bnullified\b", re.I)
_KNEEL = re.compile(r"\bkneel(?:s|ed|ing)?\b|\btak(?:e|es|ing)\s+a\s+knee\b", re.I)
_SPIKE = re.compile(r"\bspik(?:e|es|ed|ing)\s+(?:the\s+)?ball\b|\bball\s+(?:(?:is|was)\s+)?spiked\b", re.I)
_SPECIAL_TYPE = re.compile(r"kick|punt|field goal|extra point|two[ -]point|\bPAT\b", re.I)
_PASS_TEXT = re.compile(r"\bpass(?:es|ed|ing)?\b|\bsack(?:s|ed)?\b", re.I)
_RUSH_TEXT = re.compile(r"\brush(?:es|ed|ing)?\b|\brun(?:s|ning)?\b", re.I)


def _integer(value):
    # Deliberately never round-trip any identity through float.
    return int(value) if isinstance(value, (int, np.integer)) and not isinstance(value, (bool, np.bool_)) else None


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _flag(value):
    return bool(value) if isinstance(value, (bool, np.bool_)) else None


def _clock(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,2}:\d{2}", value.strip()):
        return None
    minutes, seconds = map(int, value.strip().split(":"))
    total = 60 * minutes + seconds
    return total if seconds < 60 and 0 <= total <= 900 else None


def _schema(frame, columns, integers, flags=(), strings=()):
    if not isinstance(frame, pd.DataFrame) or frame.columns.has_duplicates or set(columns) - set(frame.columns):
        raise ValueError("Required unique raw/schedule columns are missing")
    for name in integers:
        if not is_integer_dtype(frame[name].dtype) or is_bool_dtype(frame[name].dtype):
            raise ValueError("Integer/nullable-integer dtype required without float ID conversion: " + name)
    for name in flags:
        if not is_bool_dtype(frame[name].dtype):
            raise ValueError("Boolean/nullable-boolean dtype required: " + name)
    for name in strings:
        if not frame[name].dropna().map(lambda x: isinstance(x, str)).all():
            raise ValueError("String or null values required: " + name)


def _post_scores(row):
    home, away = _integer(row["homeScore"]), _integer(row["awayScore"])
    return (home, away) if home is not None and away is not None and home >= 0 and away >= 0 else None


def _legal(row, home, away):
    typ, original, text = (_text(row[x]) for x in ("type.text", "orig_play_type", "text"))
    if any(_flag(row[x]) is True for x in ("isPenalty", "penalty_flag", "penalty_no_play", "penalty_offset")) or _PENALTY.search(" ".join((typ, original, text))):
        return "penalty_or_nullified"
    if _flag(row["kneel_down"]) is True or _KNEEL.search(" ".join((typ, original, text))):
        return "kneel"
    if "spike" in (typ + " " + original).lower() or _SPIKE.search(text):
        return "spike"
    if _flag(row["kickoff_play"]) is True or _flag(row["punt_play"]) is True or _SPECIAL_TYPE.search(original):
        return "special_team_provenance"
    if typ not in LEGAL_TYPES:
        return "unsupported_play_type"
    period, down, distance, position, team = (_integer(row[x]) for x in (
        "period.number", "start.down", "start.distance", "start.yardsToEndzone", "start.team.id"))
    if period not in (1, 2, 3, 4):
        return "nonregulation_or_unknown_period"
    if down not in (1, 2, 3, 4) or distance is None or not 1 <= distance <= 100 or position is None or not 1 <= position <= 100:
        return "invalid_scrimmage_state"
    if team not in (home, away):
        return "invalid_possession_team"
    if _clock(row["clock.displayValue"]) is None:
        return "invalid_clock"
    return None


def _pass_play(row):
    typ, original, text = (_text(row[x]) for x in ("type.text", "orig_play_type", "text"))
    if typ in PASS_TYPES:
        return 1.
    if typ in RUN_TYPES:
        return 0.
    if original in PASS_TYPES:
        return 1.
    if original in RUN_TYPES:
        return 0.
    passing, rushing = bool(_PASS_TEXT.search(text)), bool(_RUSH_TEXT.search(text))
    return float(passing) if passing != rushing else np.nan


def _conversion(row, pre, home, away):
    team, end = _integer(row["start.team.id"]), _integer(row["end.team.id"])
    typ = _text(row["type.text"])
    if typ in LOST_TYPES or _flag(row["isTurnover"]) is True:
        return 0., None
    post = _post_scores(row)
    own_index = 0 if team == home else 1
    own_change = post[own_index] - pre[own_index] if post else None
    other_change = post[1-own_index] - pre[1-own_index] if post else None
    if _flag(row["scoringPlay"]) is True and other_change is not None and other_change > 0 and own_change == 0:
        return 0., None
    if typ in OFFENSIVE_TD_TYPES and _flag(row["scoringPlay"]) is True and own_change in (6, 7, 8) and other_change == 0:
        return 1., None
    if "Touchdown" in typ or _flag(row["scoringPlay"]) is True:
        return np.nan, "ambiguous_scoring_attribution"
    if end in (home, away) and end != team:
        return 0., None
    yards = _integer(row["statYardage"])
    if end not in (home, away):
        return np.nan, "missing_or_invalid_end_team"
    if yards is None or not -100 <= yards <= 100:
        return np.nan, "missing_or_invalid_stat_yardage"
    required = min(_integer(row["start.distance"]), _integer(row["start.yardsToEndzone"]))
    return float(yards >= required), None


def prepare_rows(raw: pd.DataFrame, schedules: pd.DataFrame):
    """Return state/response rows and auditable coverage, without modifying inputs.

    Extra input fields are ignored; only RAW_COLUMNS can influence output.
    Schema errors or ambiguous duplicate schedule identities raise. Invalid raw
    game identities/order reject that game's rows with a counted reason. Gaps in
    game_play_number are retained for efficiency but break clock adjacency.
    """
    _schema(raw, RAW_COLUMNS, INTEGER_COLUMNS, FLAG_COLUMNS, STRING_COLUMNS)
    _schema(schedules, SCHEDULE_COLUMNS, ("game_id", "season", "week", "home_id", "away_id"), ("neutral_site",), ("status",))
    schedule = schedules.loc[:, list(SCHEDULE_COLUMNS)].copy()
    if schedule.game_id.dropna().duplicated().any():
        raise ValueError("Ambiguous duplicate canonical schedule game identity")
    schedule_map = {int(x["game_id"]): x for x in schedule.to_dict("records") if _integer(x["game_id"]) is not None and x["game_id"] > 0}
    exclusions, missing, unknown = Counter(), Counter(), Counter()
    output, game_reports = [], []
    work = raw.loc[:, list(RAW_COLUMNS)].copy()
    valid_game = work.game_id.notna() & work.game_id.gt(0).fillna(False)
    exclusions["missing_or_invalid_game_id"] += int((~valid_game).sum())
    for name in FLAG_COLUMNS:
        unknown[name] = int(work[name].isna().sum())
    matched_games = 0
    for game_id, group in work.loc[valid_game].groupby("game_id", sort=True):
        game_id = int(game_id)
        game = schedule_map.get(game_id)
        reason = None
        if game is None:
            reason = "unmatched_schedule_game"
        elif _text(game["status"]) != "STATUS_FINAL":
            reason = "nonfinal_schedule_game"
        elif any(_integer(game[x]) is None or int(game[x]) <= 0 for x in ("season", "home_id", "away_id")) or _integer(game["week"]) is None or int(game["week"]) < 0 or game["home_id"] == game["away_id"] or _flag(game["neutral_site"]) is None:
            reason = "invalid_schedule_context"
        else:
            try:
                value = game["game_date"]
                if not isinstance(value, (str, pd.Timestamp, datetime)):
                    raise ValueError("Unknown kickoff type")
                kickoff = pd.Timestamp(value)
                if pd.isna(kickoff) or kickoff.tzinfo is None:
                    raise ValueError("Explicit kickoff timezone required")
                available = kickoff.tz_convert("UTC") + pd.Timedelta(hours=6)
            except (ValueError, TypeError, OverflowError):
                reason = "invalid_schedule_kickoff"
        if reason is None:
            home, away = int(game["home_id"]), int(game["away_id"])
            if any(not group[col].notna().all() or not group[col].ge(0 if col in ("sequenceNumber", "game_play_number") else 1).all() for col in ("id", "sequenceNumber", "game_play_number")):
                reason = "missing_or_invalid_play_identity"
            elif any(group[col].duplicated().any() for col in ("id", "sequenceNumber", "game_play_number")):
                reason = "ambiguous_duplicate_play_identity"
            elif not group.season.eq(game["season"]).fillna(False).all() or not group.homeTeamId.eq(home).fillna(False).all() or not group.awayTeamId.eq(away).fillna(False).all():
                reason = "raw_schedule_identity_mismatch"
        if reason is None:
            group = group.sort_values("sequenceNumber", kind="stable")
            ordered = group.game_play_number.tolist()
            if any(int(b) <= int(a) for a, b in zip(ordered, ordered[1:])):
                reason = "inconsistent_archived_order"
        if reason is not None:
            exclusions[reason] += len(group)
            game_reports.append({"game_id": game_id, "raw_rows": len(group), "rejected_reason": reason, "output_rows": 0})
            continue
        matched_games += 1
        records = group.to_dict("records")
        legal = [_legal(row, home, away) for row in records]
        local_exclusions, n_clock, n_conversion, n_pass_unknown = Counter(), 0, 0, 0
        start_count = len(output)
        for i, row in enumerate(records):
            if legal[i] is not None:
                local_exclusions[legal[i]] += 1
                continue
            if i and int(row["game_play_number"]) != int(records[i-1]["game_play_number"]) + 1:
                local_exclusions["unavailable_pre_score_after_archived_play_number_gap"] += 1
                continue
            pre = _post_scores(records[i-1]) if i else None
            if pre is None:
                local_exclusions["unavailable_previous_post_score"] += 1
                continue
            conversion, why = _conversion(row, pre, home, away)
            if why:
                missing["conversion_" + why] += 1
            else:
                n_conversion += 1
            clock, clock_why = np.nan, "no_next_archived_row"
            if i + 1 < len(records):
                nxt = records[i+1]
                clock_why = "next_row_not_legal" if legal[i+1] is not None else None
                if clock_why is None and int(nxt["game_play_number"]) != int(row["game_play_number"]) + 1:
                    clock_why = "archived_play_number_gap"
                if clock_why is None and (not _text(row["drive.id"]) or _text(row["drive.id"]) != _text(nxt["drive.id"])):
                    clock_why = "missing_or_changed_drive"
                if clock_why is None and (row["period.number"] != nxt["period.number"] or row["start.team.id"] != nxt["start.team.id"] or _integer(row["end.team.id"]) != _integer(row["start.team.id"])):
                    clock_why = "period_or_possession_boundary"
                if clock_why is None and (_text(row["type.text"]) in LOST_TYPES or _flag(row["isTurnover"]) is True or _flag(row["scoringPlay"]) is True or "Touchdown" in _text(row["type.text"])):
                    clock_why = "turnover_or_scoring_boundary"
                if clock_why is None:
                    delta = _clock(row["clock.displayValue"]) - _clock(nxt["clock.displayValue"])
                    if 0 <= delta <= 60:
                        clock, n_clock = float(delta), n_clock + 1
                    else:
                        clock_why = "clock_decrement_outside_0_60"
            if clock_why:
                missing["clock_" + clock_why] += 1
            team, period = int(row["start.team.id"]), int(row["period.number"])
            home_offense = team == home
            passing = _pass_play(row)
            n_pass_unknown += int(np.isnan(passing))
            output.append((game_id, int(game["season"]), int(game["week"]), team, away if home_offense else home,
                available, home_offense, bool(game["neutral_site"]), int(row["start.down"]), int(row["start.distance"]),
                int(row["start.yardsToEndzone"]), pre[0]-pre[1] if home_offense else pre[1]-pre[0], period,
                _clock(row["clock.displayValue"]) + (900 if period in (1, 3) else 0), passing, clock, conversion,
                int(row["id"]), int(row["sequenceNumber"]), int(row["game_play_number"]), _text(row["drive.id"]) or None, pre[0], pre[1]))
        exclusions.update(local_exclusions)
        game_reports.append({"game_id": game_id, "season": int(game["season"]), "raw_rows": len(group),
            "legal_scrimmage_rows": sum(x is None for x in legal), "output_rows": len(output)-start_count,
            "clock_available_rows": n_clock, "conversion_available_rows": n_conversion,
            "pass_unknown_rows": n_pass_unknown, "exclusions": dict(local_exclusions), "rejected_reason": None})
    rows = pd.DataFrame.from_records(output, columns=OUTPUT_COLUMNS)
    if len(rows):
        rows = rows.sort_values(["available_at", "game_id", "sequence_number"]).reset_index(drop=True)
    coverage = {"version": VERSION, "raw_rows": len(raw), "raw_games_with_valid_id": int(work.loc[valid_game, "game_id"].nunique()),
        "matched_final_games": matched_games, "output_rows": len(rows),
        "clock_available_rows": int(rows.clock_seconds.notna().sum()), "conversion_available_rows": int(rows.conversion.notna().sum()),
        "pass_unknown_rows": int(rows.pass_play.isna().sum()), "exclusions": dict(exclusions), "missing_responses": dict(missing),
        "unknown_raw_flags": dict(unknown), "by_game": game_reports,
        "limits": ["First archived rows and rows immediately after numbered gaps have no inferred pre-score; later state uses the previous consecutive archived post-score.",
                   "Unknown helper flags are counted; absence of an explicit veto does not certify absence of an event.",
                   "Fumble origins may remain ambiguous after explicit special-team vetoes; pass/rush attribution can be missing.",
                   "Adjacent game-clock depletion is not measured between-snap tempo; gaps and administrative rows are never bridged.",
                   "Completed kickoff+6h is an availability proxy, not an original publication receipt."]}
    return rows, coverage
