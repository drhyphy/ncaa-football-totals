"""Deterministic reporting for the frozen four-policy prospective cohort.

No source fetching, model fitting, ledger mutation or file writes. The caller
supplies the combined scoring/weather position ledgers, verifies original entry
archives, and persists the final report once. This module validates ledger
fields; it cannot independently certify original receipt or decision bytes.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
import math
from numbers import Integral, Real
from zoneinfo import ZoneInfo

from scipy.stats import t

VERSION = "four-policy-prospective-evaluation-v1"
POLICIES = (
    ("opponent_adjusted_ridge", "totals-v4-20260908"),
    ("opponent_adjusted_structural", "totals-v4-20260908"),
    ("market_price_reference", "totals-v4-20260908"),
    ("published_weather_under", "weather-under-v1-20260908"),
)
START = datetime(2026, 9, 9, 4, tzinfo=timezone.utc)
END = datetime(2027, 2, 1, 5, tzinfo=timezone.utc)
FORMAL_AT = datetime(2027, 2, 8, 12, tzinfo=timezone.utc)
COVERAGE = .9875
ZONE = ZoneInfo("America/New_York")
SETTLED = frozenset(("win", "loss", "push"))
RESULTS = SETTLED | {"pending", "void"}


def _require(condition, code):
    if not condition:
        raise ValueError(code)


def _time(value):
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, AttributeError, ValueError) as error:
        raise ValueError("aware_timestamp_required") from error
    _require(result.tzinfo is not None and result.utcoffset() is not None, "aware_timestamp_required")
    return result.astimezone(timezone.utc)


def _iso(value):
    return value.isoformat().replace("+00:00", "Z")


def _id(value):
    _require(isinstance(value, (str, Integral)) and not isinstance(value, bool), "canonical_game_id_required")
    text = str(value)
    _require(text.isascii() and text.isdigit() and not text.startswith("0"), "canonical_game_id_required")
    return text


def _number(value):
    _require(isinstance(value, Real) and not isinstance(value, bool), "finite_numeric_value_required")
    result = float(value)
    _require(math.isfinite(result), "finite_numeric_value_required")
    return result


def _week(kickoff):
    day = kickoff.astimezone(ZONE).date()
    return (day-timedelta(days=day.weekday())).isoformat()


def cluster_ratio_interval(weekly, *, coverage=COVERAGE):
    """Exact frozen cluster-ratio score formula; zero-risk active weeks stay.

    ``weekly`` supplies [{week, units_risked, profit_units}]. There is no
    bootstrap, sample-size gate, independence proof or automatic promotion.
    """
    _require(isinstance(weekly, (list, tuple)) and 0 < coverage < 1, "invalid_cluster_input")
    keys, values = set(), []
    for row in weekly:
        key = row["week"]
        _require(isinstance(key, str) and key not in keys, "unique_week_keys_required")
        n, p = _number(row["units_risked"]), _number(row["profit_units"])
        _require(n >= 0 and (n > 0 or p == 0), "invalid_zero_risk_week")
        values.append((key, n, p));keys.add(key)
    values.sort()
    total_n, total_p, g = sum(x[1] for x in values), sum(x[2] for x in values), len(values)
    theta = total_p/total_n if total_n else None
    result = {"roi": theta, "profit_units": total_p, "units_risked": total_n,
              "active_weeks": g, "degrees_of_freedom": g-1 if g else None,
              "coverage": coverage, "standard_error": None, "interval": None,
              "zero_risk_active_weeks": [x[0] for x in values if x[1] == 0],
              "interval_status": "no_risk" if total_n == 0 else "fewer_than_two_active_weeks"}
    if total_n > 0 and g >= 2:
        scores = [p-theta*n for _, n, p in values]
        se = math.sqrt(g/(g-1)*sum(u*u for u in scores))/total_n
        width = float(t.ppf((1+coverage)/2, g-1))*se
        result.update(standard_error=se, interval=[theta-width, theta+width], interval_status="approximate_week_cluster_t")
    return result


def _grade(value, position):
    _require(isinstance(value, Mapping), "grade_object_required")
    status = value.get("result", "pending")
    _require(isinstance(status, str) and status in RESULTS, "unknown_position_result")
    if status not in SETTLED:
        return {"result": status, "profit_units": None, "actual_total": None}
    actual = _number(value["actual_total"])
    _require(actual >= 0 and actual.is_integer(), "nonnegative_integer_final_total_required")
    expected = ("push" if actual == position["line"] else
                "win" if ((actual > position["line"]) == (position["side"] == "over")) else "loss")
    _require(status == expected, "grade_disagrees_with_original_line_side_and_final_total")
    profit = position["decimal_odds"]-1 if status == "win" else -1. if status == "loss" else 0.
    if value.get("profit_units") is not None:
        _require(math.isclose(_number(value["profit_units"]), profit, rel_tol=0., abs_tol=1e-10),
                 "grade_profit_differs_from_original_decimal_price")
    return {"result": status, "profit_units": profit, "actual_total": actual}


def _as_of_grade(row, position, cutoff):
    history = row.get("grade_history")
    _require(history is None or isinstance(history, list), "grade_history_must_be_a_list")
    records = history or []
    if not records and row.get("outcome_received_at") is not None:
        records = [row]
    if records:
        by_time = {}
        for record in records:
            received = _time(record["outcome_received_at"])
            # Values in later corrections are outside this evaluation. Even
            # validating their result/score would let future labels affect it.
            if received > cutoff:
                continue
            grade = _grade(record, position)
            _require(received not in by_time or by_time[received] == grade, "conflicting_same_receipt_grades")
            by_time[received] = grade
        eligible = [(clock, grade) for clock, grade in by_time.items() if clock <= cutoff]
        if eligible:
            received, grade = max(eligible, key=lambda x: x[0])
            return {**grade, "outcome_received_at": _iso(received), "outcome_timing_audited": True,
                    "grade_availability": "received_by_evaluation_cutoff"}
        return {"result": "pending", "profit_units": None, "actual_total": None,
                "outcome_received_at": None, "outcome_timing_audited": True,
                "grade_availability": "no_official_grade_received_by_cutoff"}
    grade = _grade(row, position)
    legacy = grade["result"] != "pending"
    return {**grade, "outcome_received_at": None, "outcome_timing_audited": not legacy,
            "grade_availability": "legacy_grade_receipt_unavailable" if legacy else "not_yet_graded"}


def _summary(rows):
    active = sorted({r["week"] for r in rows})
    weekly, sensitivity = [], []
    for week in active:
        sample = [r for r in rows if r["week"] == week]
        settled = [r for r in sample if r["result"] in SETTLED]
        profit = sum(r["profit_units"] for r in settled)
        weekly.append({"week": week, "units_risked": len(settled), "profit_units": profit})
        sensitivity.append({"week": week, "units_risked": len(sample),
                            "profit_units": profit-(len(sample)-len(settled))})
    settled, stressed = cluster_ratio_interval(weekly), cluster_ratio_interval(sensitivity)
    statuses = Counter(r["result"] for r in rows)
    return {"locked_positions": len(rows), "total_original_units_risked": len(rows),
            "wins": statuses["win"], "losses": statuses["loss"], "pushes": statuses["push"],
            "pending": statuses["pending"], "void": statuses["void"],
            "settled_positions": sum(statuses[s] for s in SETTLED),
            "settled": settled,
            "all_unsettled_including_void_as_loss": {**stressed,
                "charged_pending_positions": statuses["pending"], "charged_void_positions": statuses["void"],
                "definition": "Every non-win/loss/push original position is charged one full-unit loss; no refund rule assumed."},
            "weekly": [{**w, "locked_positions": s["units_risked"], "unsettled_as_loss_profit_units": s["profit_units"]}
                       for w, s in zip(weekly, sensitivity)]}


def _closing_proxy(raw, row, cutoff):
    value = raw.get("clv")
    if value is None:
        return None, "not_observed"
    try:
        _require(isinstance(value, Mapping), "invalid_proxy_object")
        observed = _time(value["observed_at"])
        kickoff, entry = _time(row["kickoff"]), _time(row["recorded_at"])
        _require(observed <= cutoff, "not_observed_by_evaluation_cutoff")
        _require(entry < observed < kickoff, "outside_entry_to_kickoff_window")
        lead = (kickoff-observed).total_seconds()/60
        _require(lead <= 30, "outside_thirty_minute_near_close_window")
        _require(value.get("sportsbook", row["sportsbook"]) == row["sportsbook"], "different_sportsbook")
        _require(value.get("status") == "last_observed_pregame_proxy_not_exact_close", "unrecognized_proxy_status")
        line, points = _number(value["line"]), _number(value["points"])
        expected = line-row["line"] if row["side"] == "over" else row["line"]-line
        _require(line >= 0 and line*2 == int(line*2) and math.isclose(points, expected, abs_tol=1e-10, rel_tol=0),
                 "proxy_points_disagree_with_original_side_and_lines")
        assumed_ev = value.get("closing_fair_ev_at_entry")
        if assumed_ev is not None:
            assumed_ev = _number(assumed_ev)
        return {"observed_at": _iso(observed), "lead_minutes": lead, "line": line, "points": points,
                "sportsbook": row["sportsbook"], "closing_fair_ev_at_entry": assumed_ev,
                "snapshot": value.get("snapshot"), "observation_id": value.get("observation_id"),
                "status": "last_observed_pregame_proxy_not_exact_close",
                "price_model_assumption": value.get("price_model_assumption")}, None
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
        # Invalid auxiliary evidence must not delete an original position/risk.
        return None, "invalid_or_out_of_window_closing_proxy"


def evaluate(positions, *, as_of):
    """Report all four prescribed policy/version pairs, including empty cohorts.

    Combine scoring and weather ledgers before calling. On/after the formal
    date, outcomes and entries are capped at the fixed formal instant. The most
    recent supplied grade-history receipt at/before that cutoff wins. Untimed
    legacy settlements remain descriptive with an explicit provenance warning.
    Void is reported separately from settled risk; the conservative sensitivity
    charges void and pending positions as losses. No void status is invented.
    """
    now = _time(as_of)
    formal = now >= FORMAL_AT
    cutoff = min(now, FORMAL_AT)
    _require(isinstance(positions, (list, tuple)), "combined_position_ledger_required")
    included, excluded, identities, position_ids = [], [], set(), set()
    for raw in positions:
        _require(isinstance(raw, Mapping), "position_object_required")
        candidate, version = raw.get("candidate"), raw.get("model_version")
        if (candidate, version) not in POLICIES:
            excluded.append({"candidate": candidate, "model_version": version, "reason": "outside_fixed_policy_version"})
            continue
        gid = _id(raw["game_id"])
        key = (candidate, version, gid)
        _require(key not in identities, "duplicate_policy_game_position")
        identities.add(key)
        pid = raw.get("position_id")
        if pid is not None:
            _require(isinstance(pid, str) and bool(pid) and pid not in position_ids, "duplicate_or_invalid_position_id")
            position_ids.add(pid)
        kickoff, recorded = _time(raw["kickoff"]), _time(raw["recorded_at"])
        reason = ("outside_fixed_kickoff_cohort" if not START <= kickoff < END else
                  "entry_not_observed_by_cutoff" if recorded > cutoff else
                  "entry_not_before_kickoff" if recorded >= kickoff else
                  "original_entry_not_eligible" if raw.get("eligible") is False else
                  "reconstruction_not_prospective" if raw.get("reconstructed") is True else None)
        if reason:
            excluded.append({"position_id": pid, "game_id": gid, "candidate": candidate,
                             "model_version": version, "reason": reason})
            continue
        line, odds = _number(raw["line"]), _number(raw["decimal_odds"])
        _require(line > 0 and line*2 == int(line*2) and odds > 1., "valid_original_total_and_decimal_price_required")
        _require(raw["side"] in ("over", "under"), "original_side_required")
        row = {"position_id": pid, "game_id": gid, "candidate": candidate, "model_version": version,
               "kickoff": _iso(kickoff), "recorded_at": _iso(recorded), "week": _week(kickoff),
               "side": raw["side"], "line": line, "decimal_odds": odds,
               "sportsbook": raw.get("sportsbook") or "unavailable",
               "source": raw.get("source") or raw.get("freshness_basis") or "unavailable"}
        _require(isinstance(row["sportsbook"], str) and isinstance(row["source"], str), "book_and_source_must_be_text")
        flags = []
        if pid is None: flags.append("position_identity_missing")
        if raw.get("eligible") is not True: flags.append("original_eligibility_field_unavailable")
        if not raw.get("quote_time"): flags.append("original_quote_time_unavailable")
        elif _time(raw["quote_time"]) > recorded: flags.append("quote_time_after_recorded_entry")
        if not raw.get("receipt_path"): flags.append("original_receipt_path_not_in_ledger")
        if row["sportsbook"] == "unavailable": flags.append("sportsbook_unavailable")
        if row["source"] == "unavailable": flags.append("source_basis_unavailable")
        row.update(_as_of_grade(raw, row, cutoff))
        if not row["outcome_timing_audited"]: flags.append("outcome_receipt_unavailable")
        row["provenance_flags"] = flags
        row["closing_proxy"], row["closing_proxy_missing_reason"] = _closing_proxy(raw, row, cutoff)
        included.append(row)
    included.sort(key=lambda r: (r["candidate"], r["kickoff"], int(r["game_id"])))
    reports = []
    for candidate, version in POLICIES:
        rows = [r for r in included if (r["candidate"], r["model_version"]) == (candidate, version)]
        summary = _summary(rows)
        deletion = []
        for week in sorted({r["week"] for r in rows}):
            rest = _summary([r for r in rows if r["week"] != week])
            deletion.append({"removed_week": week, "settled": rest["settled"],
                             "all_unsettled_including_void_as_loss": rest["all_unsettled_including_void_as_loss"]})
        flags = Counter(flag for row in rows for flag in row["provenance_flags"])
        min_met = summary["settled_positions"] >= 50 and summary["settled"]["active_weeks"] >= 12
        lower = summary["settled"]["interval"]
        sensitivity_lower = summary["all_unsettled_including_void_as_loss"]["interval"]
        closing = [r["closing_proxy"] for r in rows if r["closing_proxy"] is not None]
        closing_evs = [c["closing_fair_ev_at_entry"] for c in closing if c["closing_fair_ev_at_entry"] is not None]
        reports.append({"candidate": candidate, "model_version": version, **summary,
            "sample_minimum_met_for_review": min_met,
            "formal_positive_lower_bound": bool(formal and min_met and lower and lower[0] > 0),
            "unsettled_loss_sensitivity_positive_lower_bound": bool(formal and min_met and sensitivity_lower and sensitivity_lower[0] > 0),
            "incomplete_settlement_week": bool(summary["settled"]["zero_risk_active_weeks"]),
            "unaudited_outcome_positions": sum(not r["outcome_timing_audited"] for r in rows),
            "provenance_flag_counts": dict(sorted(flags.items())),
            "source_counts": dict(sorted(Counter(r["source"] for r in rows).items())),
            "sportsbook_counts": dict(sorted(Counter(r["sportsbook"] for r in rows).items())),
            "closing_comparisons": {"available_positions": len(closing), "missing_positions": len(rows)-len(closing),
                "mean_points": sum(c["points"] for c in closing)/len(closing) if closing else None,
                "positive_points": sum(c["points"] > 0 for c in closing),
                "mean_assumed_closing_fair_ev_at_entry": sum(closing_evs)/len(closing_evs) if closing_evs else None,
                "assumed_closing_ev_positions": len(closing_evs),
                "missing_reason_counts": dict(sorted(Counter(r["closing_proxy_missing_reason"] for r in rows if r["closing_proxy"] is None).items())),
                "definition": "Attached same-book observed proxy within30minutes before kickoff, strictly after entry and no later than evaluation cutoff; not exact closing price, known fair probability or accepted execution.",
                "source_scope": "Timestamp/line arithmetic checked here; caller must verify original same-book snapshot and observation identity."},
            "by_sportsbook": {name: _summary([r for r in rows if r["sportsbook"] == name])
                              for name in sorted({r["sportsbook"] for r in rows})},
            "by_calendar_month": {month: _summary([r for r in rows if _time(r["kickoff"]).astimezone(ZONE).strftime("%Y-%m") == month])
                                  for month in sorted({_time(r["kickoff"]).astimezone(ZONE).strftime("%Y-%m") for r in rows})},
            "leave_one_week_out": deletion,
            "profit_disappears_without_one_week": any(d["settled"]["roi"] is not None and d["settled"]["roi"] <= 0 for d in deletion),
            "automatic_promotion": False,
            "review_status": "interim_descriptive_only" if not formal else
                             "inconclusive_sample_minimum" if not min_met else "requires_independent_provenance_and_assumption_review"})
    return {"schema_version": VERSION, "as_of": _iso(now), "evaluation_cutoff": _iso(cutoff),
            "formal_at": _iso(FORMAL_AT), "stage": "formal_cutoff_report" if formal else "interim_descriptive",
            "cohort": {"kickoff_start_inclusive": _iso(START), "kickoff_end_exclusive": _iso(END),
                       "timezone": "America/New_York", "policies": [{"candidate": c, "model_version": v} for c, v in POLICIES]},
            "method": {"interval": "active-week cluster-ratio-score Student t", "two_sided_coverage": COVERAGE,
                       "family_size": 4, "minimum_settled_positions": 50, "minimum_active_weeks": 12,
                       "formula": "theta=sum(P)/sum(N); u=P-theta*N; SE=sqrt(G/(G-1)*sum(u^2))/sum(N); df=G-1",
                       "interpretation": "Approximate local four-policy family; not a guarantee or a correction for earlier research searches."},
            "counts": {"input_positions": len(positions), "in_cohort_positions": len(included), "excluded_positions": len(excluded)},
            "candidates": reports, "excluded_positions": excluded,
            "exclusion_reason_counts": dict(sorted(Counter(r["reason"] for r in excluded).items())),
            "evaluated_positions": included, "automatic_promotion": False, "edge_established": False,
            "limitations": ["Interim positive intervals are descriptive and cannot establish confirmatory success.",
                "Original receipt/decision bytes, paired offers and frozen-policy implementation require independent archive audit; ledger field checks do not certify them.",
                "Missing outcome receipt times leave legacy settlements descriptive and unaudited, including at the formal cutoff.",
                "Week blocks may be dependent and the t approximation unreliable with few or unrepresentative weeks.",
                "Observed-price paper returns do not prove sportsbook acceptance, realized cash profit or a void/refund rule.",
                "Missing-data counts cover supplied locked positions; unobserved slate/report/quote coverage must come from collection diagnostics."]}
