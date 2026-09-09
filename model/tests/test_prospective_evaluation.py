"""Independent synthetic frozen four-policy reporting checks."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import math

import pytest
from scipy.stats import t

from ncaaf_model import prospective_evaluation as study


FORMAL = "2027-02-08T12:00:00Z"
INTERIM = "2026-12-20T12:00:00Z"


def position(n=0, *, week=0, result="win", decimal=1.9, side="over", line=50.5, candidate=None, version=None):
    kickoff = datetime(2026, 9, 12, 19, tzinfo=timezone.utc)+timedelta(weeks=week)
    actual = line if result == "push" else 60 if (result == "win") == (side == "over") else 40
    payout = decimal-1 if result == "win" else -1. if result == "loss" else 0.
    row = {"position_id": "p"+str(n), "game_id": str(9007199254740993+n),
           "candidate": candidate or study.POLICIES[0][0], "model_version": version or study.POLICIES[0][1],
           "kickoff": kickoff.isoformat(), "recorded_at": (kickoff-timedelta(days=2)).isoformat(),
           "quote_time": (kickoff-timedelta(days=2, seconds=1)).isoformat(), "eligible": True,
           "line": line, "side": side, "decimal_odds": decimal, "sportsbook": "draftkings",
           "freshness_basis": "provider_full_state_receipt", "receipt_path": "data/receipt.json",
           "result": result, "profit_units": None if result == "pending" else payout}
    if result in study.SETTLED:
        row.update(actual_total=actual, outcome_received_at=(kickoff+timedelta(hours=5)).isoformat())
    return row


def primary(report):
    return report["candidates"][0]


def test_exact_unequal_risk_cluster_formula_and_t_critical_value():
    # Independent manual ratio and cluster scores; not a mean of weekly ROIs.
    weeks = [{"week": "a", "units_risked": 1, "profit_units": .9},
             {"week": "b", "units_risked": 3, "profit_units": -1.1},
             {"week": "c", "units_risked": 2, "profit_units": 1.8}]
    actual = study.cluster_ratio_interval(weeks)
    theta = 1.6/6
    scores = [.9-theta, -1.1-3*theta, 1.8-2*theta]
    se = math.sqrt(3/2*sum(x*x for x in scores))/6
    critical = t.ppf(.99375, 2)
    assert actual["roi"] == pytest.approx(theta)
    assert actual["standard_error"] == pytest.approx(se)
    assert actual["interval"] == pytest.approx([theta-critical*se, theta+critical*se])
    assert actual["degrees_of_freedom"] == 2 and actual["coverage"] == .9875
    assert study.cluster_ratio_interval(weeks[::-1]) == actual


def test_equal_size_week_standard_error_matches_sample_weekly_mean_se():
    means = [.5, -.3, .1, .8]
    result = study.cluster_ratio_interval([{"week": str(i), "units_risked": 4, "profit_units": 4*v} for i,v in enumerate(means)])
    mean = sum(means)/4
    expected = math.sqrt(sum((x-mean)**2 for x in means)/3)/math.sqrt(4)
    assert result["standard_error"] == pytest.approx(expected)


def test_empty_policies_all_present_zero_roi_unavailable_and_one_week_no_interval():
    result = study.evaluate([], as_of=FORMAL)
    assert len(result["candidates"]) == 4 and result["counts"]["in_cohort_positions"] == 0
    for row in result["candidates"]:
        assert row["settled"]["roi"] is None and row["settled"]["interval"] is None
        assert row["all_unsettled_including_void_as_loss"]["roi"] is None
    one = primary(study.evaluate([position()], as_of=FORMAL))
    assert one["settled"]["roi"] == pytest.approx(.9) and one["settled"]["interval"] is None


def test_push_void_pending_denominators_and_conservative_full_risk_sensitivity():
    rows = [position(0), position(1, result="loss"), position(2, result="push", line=50),
            position(3, week=1, result="pending"), position(4, week=1, result="void")]
    result = primary(study.evaluate(rows, as_of=FORMAL))
    assert result["locked_positions"] == 5 and result["total_original_units_risked"] == 5
    assert result["settled_positions"] == 3 and result["pushes"] == result["pending"] == result["void"] == 1
    assert result["settled"]["profit_units"] == pytest.approx(-.1)
    assert result["settled"]["roi"] == pytest.approx(-.1/3)
    stress = result["all_unsettled_including_void_as_loss"]
    assert stress["units_risked"] == 5 and stress["roi"] == pytest.approx(-2.1/5)
    assert stress["charged_pending_positions"] == stress["charged_void_positions"] == 1
    assert result["settled"]["active_weeks"] == 2 and result["settled"]["zero_risk_active_weeks"] == ["2026-09-14"]
    assert result["incomplete_settlement_week"]
    # A zero-settled active week remains in the prescribed score calculation.
    expected_se = math.sqrt(2*((-.1-(-.1/3)*3)**2+0))/3
    assert result["settled"]["standard_error"] == pytest.approx(expected_se)


def test_latest_actual_grade_before_fixed_cutoff_wins_over_later_correction():
    row = position()
    row.update(actual_total=40, result="loss", profit_units=-1., outcome_received_at="2027-02-09T00:00:00Z",
        grade_history=[{"outcome_received_at": "2026-09-12T23:00:00Z", "actual_total": 60, "result": "win", "profit_units": .9},
                       {"outcome_received_at": "2027-02-09T00:00:00Z", "actual_total": 40, "result": "loss", "profit_units": -1.}])
    report = study.evaluate([row], as_of="2027-03-01T00:00:00Z")
    assert report["evaluation_cutoff"] == FORMAL and primary(report)["wins"] == 1
    assert report["evaluated_positions"][0]["outcome_received_at"] == "2026-09-12T23:00:00Z"
    # No future score validation or selection may change the fixed report.
    row["grade_history"][1].update(actual_total="FUTURE_UNREAD", result="unrecognized", profit_units=None)
    assert study.evaluate([row], as_of="2027-03-01T00:00:00Z") == report


def test_only_future_grade_stays_pending_and_earlier_interim_grade_changes_prospectively():
    row = position()
    first = "2026-09-12T23:00:00Z"
    row["grade_history"] = [{"outcome_received_at": first, "actual_total": 60, "result": "win", "profit_units": .9},
                             {"outcome_received_at": "2026-09-13T00:00:00Z", "actual_total": 40, "result": "loss", "profit_units": -1.}]
    assert primary(study.evaluate([row], as_of="2026-09-12T22:59:59Z"))["pending"] == 1
    assert primary(study.evaluate([row], as_of=first))["wins"] == 1
    assert primary(study.evaluate([row], as_of="2026-09-13T00:00:00Z"))["losses"] == 1


def test_legacy_unstamped_grade_retained_descriptively_and_marked_unaudited():
    row = position();del row["outcome_received_at"]
    result = study.evaluate([row], as_of=FORMAL)
    assert primary(result)["wins"] == 1 and primary(result)["unaudited_outcome_positions"] == 1
    assert primary(result)["provenance_flag_counts"]["outcome_receipt_unavailable"] == 1
    assert result["automatic_promotion"] is False and result["edge_established"] is False


def test_sample_minimum_is_fifty_settled_and_twelve_active_weeks_not_signal_gate():
    rows = [position(i, week=i%12) for i in range(50)]
    interim = primary(study.evaluate(rows, as_of=INTERIM))
    formal = primary(study.evaluate(rows, as_of=FORMAL))
    assert interim["sample_minimum_met_for_review"] and interim["review_status"] == "interim_descriptive_only"
    assert not interim["formal_positive_lower_bound"] and not interim["automatic_promotion"]
    assert formal["sample_minimum_met_for_review"] and formal["formal_positive_lower_bound"]
    assert formal["review_status"] == "requires_independent_provenance_and_assumption_review"
    assert not primary(study.evaluate(rows[:-1], as_of=FORMAL))["sample_minimum_met_for_review"]
    fewer_weeks = [position(i, week=i%11) for i in range(50)]
    assert not primary(study.evaluate(fewer_weeks, as_of=FORMAL))["sample_minimum_met_for_review"]


def test_leave_one_week_out_influence_and_book_source_missingness():
    rows = [position(0, week=0, decimal=4.), position(1, week=1, result="loss"), position(2, week=2, result="loss")]
    rows[1].update(sportsbook="fanduel", source="observed_provider")
    del rows[2]["sportsbook"];del rows[2]["freshness_basis"];del rows[2]["receipt_path"]
    report = primary(study.evaluate(rows, as_of=FORMAL))
    first = report["leave_one_week_out"][0]
    assert first["settled"]["roi"] == -1 and report["profit_disappears_without_one_week"]
    assert report["sportsbook_counts"] == {"draftkings": 1, "fanduel": 1, "unavailable": 1}
    assert report["source_counts"]["unavailable"] == 1
    assert report["provenance_flag_counts"]["original_receipt_path_not_in_ledger"] == 1
    assert report["by_sportsbook"]["fanduel"]["settled"]["roi"] == -1


def test_cohort_boundaries_policy_versions_preentry_and_reconstructions():
    base = position()
    rows = []
    dates = ["2026-09-09T03:59:59Z", "2026-09-09T04:00:00Z", "2027-02-01T04:59:59Z", "2027-02-01T05:00:00Z"]
    for i, date in enumerate(dates):
        row = position(i, result="pending");kickoff=datetime.fromisoformat(date.replace("Z", "+00:00"))
        row.update(kickoff=date, recorded_at=(kickoff-timedelta(days=1)).isoformat(), quote_time=(kickoff-timedelta(days=1, seconds=1)).isoformat());rows.append(row)
    old = position(4);old["model_version"] = "totals-v3";rows.append(old)
    late = position(5);late["recorded_at"] = late["kickoff"];rows.append(late)
    reconstruction = position(6);reconstruction["reconstructed"] = True;rows.append(reconstruction)
    result = study.evaluate(rows, as_of=FORMAL)
    assert result["counts"]["in_cohort_positions"] == 2
    assert result["exclusion_reason_counts"] == {"outside_fixed_kickoff_cohort": 2, "outside_fixed_policy_version": 1,
                                                "entry_not_before_kickoff": 1, "reconstruction_not_prospective": 1}
    assert study.evaluate([base], as_of="2026-09-09T04:00:00Z")["exclusion_reason_counts"] == {"entry_not_observed_by_cutoff": 1}


def test_four_policy_versions_are_separate_and_never_pooled():
    rows = [position(i, result="win" if i%2==0 else "loss", candidate=c, version=v) for i,(c,v) in enumerate(study.POLICIES)]
    result = study.evaluate(rows, as_of=FORMAL)
    assert [r["settled"]["roi"] for r in result["candidates"]] == pytest.approx([.9,-1.,.9,-1.])
    assert all(r["locked_positions"] == 1 for r in result["candidates"])
    assert "pooled_roi" not in result


def test_formal_instant_and_locked_eastern_week_survive_dst():
    assert study.evaluate([], as_of="2027-02-08T11:59:59.999999Z")["stage"] == "interim_descriptive"
    assert study.evaluate([], as_of=FORMAL)["stage"] == "formal_cutoff_report"
    row = position(result="pending");row.update(kickoff="2026-11-02T04:30:00Z", recorded_at="2026-11-01T00:00:00Z", quote_time="2026-10-31T23:59:59Z")
    result = study.evaluate([row], as_of=FORMAL)
    assert result["evaluated_positions"][0]["week"] == "2026-10-26"  # Sunday23:30EST.


def test_deterministic_order_no_mutation_and_json_finite():
    rows = [position(i, week=i%3, result="win" if i%2 else "loss") for i in range(7)]
    original = deepcopy(rows)
    result = study.evaluate(rows, as_of=FORMAL)
    assert result == study.evaluate(rows[::-1], as_of=FORMAL)
    assert rows == original
    assert result["evaluated_positions"][0]["game_id"] == "9007199254740993"
    json.dumps(result, allow_nan=False)


def clv(row, *, minutes=10, line=52.5):
    kickoff = datetime.fromisoformat(row["kickoff"])
    return {"observed_at": (kickoff-timedelta(minutes=minutes)).isoformat(), "line": line,
            "points": line-row["line"] if row["side"] == "over" else row["line"]-line,
            "closing_fair_ev_at_entry": .04, "snapshot": "data/closing.json.gz", "observation_id": "c1",
            "status": "last_observed_pregame_proxy_not_exact_close",
            "price_model_assumption": "Fixed normal model; not a known fair probability"}


def test_same_book_closing_points_use_correct_side_and_preserve_missing_risk():
    rows = [position(0), position(1, side="under"), position(2, result="pending")]
    rows[0]["clv"] = clv(rows[0], line=52.5)
    rows[1]["clv"] = clv(rows[1], minutes=30, line=49.5)
    result = study.evaluate(rows, as_of=FORMAL)
    report = primary(result)["closing_comparisons"]
    assert report["available_positions"] == 2 and report["missing_positions"] == 1
    assert report["mean_points"] == 1.5 and report["positive_points"] == 2
    assert report["mean_assumed_closing_fair_ev_at_entry"] == .04
    assert result["evaluated_positions"][0]["closing_proxy"]["snapshot"] == "data/closing.json.gz"
    assert primary(result)["locked_positions"] == 3


@pytest.mark.parametrize("mutation", ["at_entry", "at_kickoff", "after_kickoff", "too_early", "other_book", "bad_points", "nan_ev", "unknown_status"])
def test_invalid_closing_comparison_does_not_delete_position_or_modify_grade(mutation):
    row = position();row["clv"] = clv(row)
    proxy = row["clv"]
    if mutation == "at_entry": proxy["observed_at"] = row["recorded_at"]
    elif mutation == "at_kickoff": proxy["observed_at"] = row["kickoff"]
    elif mutation == "after_kickoff": proxy["observed_at"] = "2026-09-12T19:01:00Z"
    elif mutation == "too_early": row["clv"] = clv(row, minutes=31)
    elif mutation == "other_book": proxy["sportsbook"] = "fanduel"
    elif mutation == "bad_points": proxy["points"] = 10.
    elif mutation == "nan_ev": proxy["closing_fair_ev_at_entry"] = float("nan")
    else: proxy["status"] = "exact_close"
    result = primary(study.evaluate([row], as_of=FORMAL))
    assert result["locked_positions"] == result["wins"] == 1
    assert result["closing_comparisons"]["available_positions"] == 0
    assert result["closing_comparisons"]["missing_positions"] == 1


def test_closing_observation_cannot_appear_before_actual_receipt():
    row = position(result="pending");row["clv"] = clv(row)
    before = primary(study.evaluate([row], as_of="2026-09-12T18:49:59Z"))
    at = primary(study.evaluate([row], as_of="2026-09-12T18:50:00Z"))
    assert before["closing_comparisons"]["available_positions"] == 0
    assert at["closing_comparisons"]["available_positions"] == 1


@pytest.mark.parametrize("mutation", ["duplicate", "duplicate_id", "bad_game", "line", "price", "side", "unknown_result",
    "fractional_total", "nonfinite_total", "wrong_grade", "wrong_profit", "conflicting_history", "naive_time"])
def test_corrupt_ledgers_fail_without_silently_erasing_risk(mutation):
    rows = [position(), position(1)]
    if mutation == "duplicate": rows[1] = deepcopy(rows[0])
    elif mutation == "duplicate_id": rows[1]["position_id"] = rows[0]["position_id"]
    elif mutation == "bad_game": rows[0]["game_id"] = "01"
    elif mutation == "line": rows[0]["line"] = 50.2
    elif mutation == "price": rows[0]["decimal_odds"] = 1.
    elif mutation == "side": rows[0]["side"] = "unknown"
    elif mutation == "unknown_result": rows[0]["result"] = "canceled"
    elif mutation == "fractional_total": rows[0]["actual_total"] = 60.5
    elif mutation == "nonfinite_total": rows[0]["actual_total"] = float("inf")
    elif mutation == "wrong_grade": rows[0]["result"] = "loss"
    elif mutation == "wrong_profit": rows[0]["profit_units"] = 10.
    elif mutation == "conflicting_history":
        rows[0]["grade_history"] = [{"outcome_received_at": "2026-09-12T23:00:00Z", "actual_total": 60, "result": "win", "profit_units": .9},
                                     {"outcome_received_at": "2026-09-12T23:00:00Z", "actual_total": 40, "result": "loss", "profit_units": -1.}]
    else: rows[0]["recorded_at"] = "2026-09-10T19:00:00"
    with pytest.raises(ValueError): study.evaluate(rows, as_of=FORMAL)
