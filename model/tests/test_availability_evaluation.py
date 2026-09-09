"""Hand-calculated, synthetic availability reports; no real labels or fits."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from itertools import product
import json
import math

import numpy as np
import pytest

from ncaaf_model import availability_evaluation as evaluation


def logit(probability):
    return math.log(probability) - math.log1p(-probability)


def anchor(week):
    return (datetime(2026, 9, 12, 17, tzinfo=timezone.utc) + timedelta(weeks=week)).isoformat()


def forecast(gid=1, week=0, *, phase="initial", total=50, line=55.5, reference=.5, challenger=.7, q=.5):
    return {"game_id": str(gid), "phase": phase, "group_kickoff": anchor(week),
            "reference_line": line, "q_under": q, "reference_logit": logit(reference),
            "challenger_logit": logit(challenger), "final_total": total}


def position(gid=1, week=0, *, total=50, line=55.5, price=2.1, side="under", status="settled", **extra):
    return {"game_id": str(gid), "group_kickoff": anchor(week), "side": side,
            "line": line, "decimal_odds": price, "status": status, "final_total": total, **extra}


def test_empty_report_is_unavailable_not_zero_profit_or_confidence():
    report = evaluation.evaluate([], [])
    assert report["forecasts"]["scored_games"] == 0
    assert all(value is None for scores in report["forecasts"]["mean_scores"].values() for value in scores.values())
    assert report["paper"]["full_cohort_roi"] is None
    assert report["paper"]["full_cohort_status"] == "no_positions"
    assert report["paper"]["unresolved_as_full_loss"]["estimate"] is None
    assert report["forecasts"]["paired_challenger_minus_reference"]["intervals"]["0.975"] is None
    assert report["automatic_promotion"] is report["high_confidence_edge_claim"] is False
    json.dumps(report, allow_nan=False)


def test_game_equal_scores_preserve_different_line_labels_for_the_same_final_total():
    # Game 1 has two observations with opposite Under labels at different lines.
    rows = [forecast(1, phase="initial", total=50, line=49.5, challenger=.9),
            forecast(1, phase="gameday", total=50, line=51.5, challenger=.8),
            forecast(2, week=1, total=40, challenger=.6)]
    scored = evaluation.evaluate(rows, [position(2, week=1, total=40)]) ["forecasts"]
    expected_ll = ((-math.log(.1) - math.log(.8)) / 2 - math.log(.6)) / 2
    expected_brier = ((.9 ** 2 + .2 ** 2) / 2 + .4 ** 2) / 2
    assert scored["mean_scores"]["challenger"]["log_loss"] == pytest.approx(expected_ll)
    assert scored["mean_scores"]["challenger"]["brier"] == pytest.approx(expected_brier)
    assert scored["mean_scores"]["price_reference"]["log_loss"] == pytest.approx(math.log(2))
    assert scored["mean_scores"]["reference"]["brier"] == .25
    assert (scored["locked_forecasts"], scored["scored_games"]) == (3, 2)
    point = scored["paired_challenger_minus_reference"]
    assert point["estimate"] == pytest.approx(expected_ll - math.log(2))
    assert [week["denominator"] for week in point["weekly"]] == [1., 1.]
    assert scored["by_phase"]["initial"]["scored_games"] == 2
    assert scored["by_phase"]["gameday"]["scored_games"] == 1
    assert scored["by_phase"]["initial"]["paired_challenger_minus_reference"]["intervals"] == {}


def test_two_week_intervals_match_all_possible_block_draws_not_week_mean_point_estimate():
    rows = [forecast(1, week=0, challenger=.8)] + [forecast(i, week=1, challenger=.3) for i in (2, 3, 4)]
    result = evaluation.evaluate(rows, [])
    comparison = result["forecasts"]["paired_challenger_minus_reference"]
    first = -math.log(.8) - math.log(2)
    second = -math.log(.3) - math.log(2)
    # Enumerate the complete 2^2 resampling support, without a bootstrap helper.
    numerator, denominator = [first, 3 * second], [1, 3]
    possibilities = [sum(numerator[i] for i in draw) / sum(denominator[i] for i in draw)
                     for draw in product(range(2), repeat=2)]
    assert comparison["estimate"] == pytest.approx((first + 3 * second) / 4)
    assert comparison["estimate"] != pytest.approx((first + second) / 2)
    # Both extremes have probability 1/4, beyond either 97.5% quantile tail.
    assert comparison["intervals"]["0.975"] == pytest.approx({"lower": min(possibilities), "upper": max(possibilities)})
    assert result["bootstrap"] == {"generator": "PCG64", "seed": 20260909, "draws": 10000,
                                  "unit": "fixed group-kickoff Eastern Monday week", "quantile_method": "linear"}
    assert result["local_endpoint_interval_coverage"] == .975
    assert len(result["local_endpoints"]) == 2
    assert any("broader project searches" in text for text in result["limitations"])


def test_missing_labels_remain_visible_even_when_same_game_has_another_known_total():
    rows = [forecast(1, phase="initial", total=50), forecast(1, phase="gameday", total=None),
            forecast(2, week=1, total=None)]
    result = evaluation.evaluate(rows, []) ["forecasts"]
    assert result["locked_forecasts"] == 3 and result["scored_forecasts"] == 1
    assert result["unresolved_forecasts"] == 2 and result["partially_labeled_games"] == 1
    assert result["forecast_games"] == 2 and result["scored_games"] == result["unresolved_games"] == 1
    assert result["mean_scores"]["challenger"]["log_loss"] == pytest.approx(-math.log(.7))
    assert result["by_phase"]["gameday"]["mean_scores"]["challenger"]["log_loss"] is None
    assert result["paired_challenger_minus_reference"]["intervals"]["0.975"] is None


def test_pending_risk_and_void_risk_stay_in_the_full_cohort():
    bets = [position(1, price=2.1, sportsbook="draftkings", phase="initial"),
            position(2, week=1, status="void", total=None, sportsbook="fanduel", phase="gameday"),
            position(3, week=1, status="pending", total=None)]
    result = evaluation.evaluate([], bets)["paper"]
    assert (result["locked_positions"], result["total_units_risked"]) == (3, 3)
    assert (result["settled_positions"], result["void_positions"], result["pending_positions"]) == (1, 1, 1)
    assert result["full_cohort_status"] == "incomplete" and result["full_cohort_roi"] is None
    assert result["full_cohort_uncertainty"] is None
    assert result["known_profit_units"] == pytest.approx(1.1)
    resolved = result["settled_only_descriptive"]
    assert resolved["included_statuses"] == ["settled", "void"]
    assert resolved["positions"] == resolved["units_risked"] == 2
    assert resolved["roi"] == pytest.approx(.55) and resolved["is_partial_cohort"] is True
    pessimistic = result["unresolved_as_full_loss"]
    assert pessimistic["estimate"] == pytest.approx(.1 / 3)
    for coverage in ("0.975", "0.95", "0.99"):
        assert pessimistic["intervals"][coverage] == pytest.approx({"lower": -.5, "upper": 1.1})
    assert result["by_book"] == {"draftkings": 1, "fanduel": 1, "not_recorded": 1}
    assert result["by_phase"] == {"initial": 1, "gameday": 1, "not_recorded": 1}
    omitted = {row["omitted_week"]: row for row in result["leave_one_week_out"]}
    assert omitted["2026-09-07"]["full_cohort_roi"] is None
    assert omitted["2026-09-07"]["unresolved_as_loss_roi"] == -.5
    assert omitted["2026-09-14"]["full_cohort_roi"] == pytest.approx(1.1)


def test_settlement_uses_each_locked_price_correct_side_and_void_zero():
    bets = [position(1, total=50, price=2.2, side="under"),
            position(2, week=1, total=60, price=1.8, side="over"),
            position(3, week=1, total=60, price=3., side="under"),
            position(4, week=2, status="void", total=None, price=2.)]
    result = evaluation.evaluate([], bets)["paper"]
    assert (result["wins"], result["losses"], result["void_positions"]) == (2, 1, 1)
    assert result["known_profit_units"] == pytest.approx(1.)
    assert result["full_cohort_roi"] == pytest.approx(.25)
    assert result["full_cohort_status"] == "complete"
    assert result["full_cohort_uncertainty"] == result["unresolved_as_full_loss"]
    assert result["by_side"] == {"under": 3, "over": 1}
    # Three active weeks: the full 3^3 support bounds are selected at these tails.
    supports = [sum([1.2, -.2, 0.][i] for i in draw) / sum([1, 2, 1][i] for i in draw)
                for draw in product(range(3), repeat=3)]
    for interval in result["full_cohort_uncertainty"]["intervals"].values():
        assert interval == pytest.approx({"lower": min(supports), "upper": max(supports)})


def test_all_pending_and_all_void_are_distinct_from_no_positions():
    pending = evaluation.evaluate([], [position(status="pending", total=None)])["paper"]
    assert pending["full_cohort_roi"] is None and pending["full_cohort_status"] == "incomplete"
    assert pending["settled_only_descriptive"]["roi"] is None
    assert pending["unresolved_as_full_loss"]["estimate"] == -1.
    void = evaluation.evaluate([], [position(status="void", total=None)])["paper"]
    assert void["full_cohort_roi"] == 0. and void["total_units_risked"] == 1
    assert void["full_cohort_status"] == "complete"
    assert all(bound is None for bound in void["full_cohort_uncertainty"]["intervals"].values())
    assert void["leave_one_week_out"][0]["full_cohort_roi"] is None


def test_a_known_forecast_total_does_not_silently_settle_a_pending_position():
    result = evaluation.evaluate([forecast(total=50)], [position(status="pending", total=None)])
    assert result["forecasts"]["scored_games"] == 1
    assert result["paper"]["pending_positions"] == 1
    assert result["paper"]["unresolved_as_full_loss"]["estimate"] == -1.


def test_reliability_uses_same_within_game_weights_and_fixed_bins():
    rows = [forecast(1, phase="initial", q=.1, challenger=.7),
            forecast(1, phase="gameday", q=.9, challenger=.7),
            forecast(2, q=.9, challenger=.7)]
    result = evaluation.evaluate(rows, [])["forecasts"]
    bins = result["reliability"]["price_reference"]
    assert bins[1]["forecast_count"] == 1 and bins[1]["game_weight"] == .5
    assert bins[9]["forecast_count"] == 2 and bins[9]["game_weight"] == 1.5
    assert bins[1]["mean_probability"] == .1 and bins[9]["mean_probability"] == pytest.approx(.9)
    assert bins[9]["observed_under_rate"] == 1.
    assert sum(row["game_weight"] for row in bins) == 2.
    assert sum(row["forecast_count"] for row in bins) == 3


def test_extreme_logit_logloss_stays_finite_without_probability_clipping():
    rows = [forecast(1, total=50), forecast(2, week=1, total=60)]
    rows[0]["challenger_logit"] = -1000.
    rows[1]["challenger_logit"] = 1000.
    result = evaluation.evaluate(rows, [])["forecasts"]
    assert result["mean_scores"]["challenger"]["log_loss"] == 1000.
    assert result["mean_scores"]["challenger"]["brier"] == 1.
    assert result["reliability"]["challenger"][0]["mean_probability"] == 0.
    assert result["reliability"]["challenger"][9]["mean_probability"] == 1.


def test_fixed_anchor_uses_eastern_week_and_not_other_kickoff_fields():
    first, second = forecast(1), forecast(2)
    # Monday UTC is still Sunday in New York; the next instant starts a new week.
    first["group_kickoff"] = "2026-09-14T03:30:00Z"
    second["group_kickoff"] = "2026-09-14T04:30:00Z"
    first["kickoff"] = second["kickoff"] = "2026-10-01T20:00:00Z"
    result = evaluation.evaluate([first, second], [])["forecasts"]["paired_challenger_minus_reference"]
    assert [row["week"] for row in result["weekly"]] == ["2026-09-07", "2026-09-14"]


def test_input_order_and_global_random_seed_do_not_change_report_or_mutate_inputs():
    rows = [forecast(i + 1, i % 3, challenger=.3 + i / 20) for i in range(7)]
    bets = [position(i + 1, i % 3) for i in range(7)]
    before = deepcopy((rows, bets))
    np.random.seed(11)
    first = evaluation.evaluate(rows, bets)
    np.random.seed(928)
    second = evaluation.evaluate(list(reversed(rows)), list(reversed(bets)))
    assert first == second and (rows, bets) == before
    json.dumps(first, allow_nan=False)


def test_large_integer_ids_are_preserved_and_numeric_string_alias_cannot_duplicate_game():
    ids = [9007199254740992, 9007199254740993]
    rows = [forecast(gid, i) for i, gid in enumerate(ids)]
    rows[0]["game_id"] = ids[0]
    assert evaluation.evaluate(rows, [])["forecasts"]["forecast_games"] == 2
    duplicate = deepcopy(rows[0])
    duplicate["game_id"] = str(ids[0])
    with pytest.raises(ValueError, match="Duplicate"):
        evaluation.evaluate(rows + [duplicate], [])


@pytest.mark.parametrize("mutate", [
    lambda row: row.update(game_id=True), lambda row: row.update(game_id=1.),
    lambda row: row.update(game_id="01"), lambda row: row.update(game_id="٠١"),
    lambda row: row.update(phase="update"), lambda row: row.update(group_kickoff="2026-09-12T17:00:00"),
    lambda row: row.update(reference_line=55.), lambda row: row.update(reference_line=-.5),
    lambda row: row.update(q_under=0), lambda row: row.update(q_under=1),
    lambda row: row.update(q_under=True), lambda row: row.update(q_under=float("nan")),
    lambda row: row.update(reference_logit=float("inf")), lambda row: row.update(challenger_logit=".1"),
    lambda row: row.update(final_total=-1), lambda row: row.update(final_total=50.5),
    lambda row: row.update(final_total=True),
])
def test_invalid_forecast_contracts_are_not_coerced_or_scored(mutate):
    row = forecast()
    mutate(row)
    with pytest.raises(ValueError):
        evaluation.evaluate([row], [])


@pytest.mark.parametrize("mutation", ["duplicate_phase", "anchor", "total", "paper_anchor", "paper_total", "duplicate_paper"])
def test_same_game_identity_and_outcome_conflicts_fail(mutation):
    rows, bets = [forecast()], []
    if mutation == "duplicate_phase":
        rows.append(deepcopy(rows[0]))
    elif mutation in ("anchor", "total"):
        other = forecast(phase="gameday")
        other["group_kickoff" if mutation == "anchor" else "final_total"] = anchor(1) if mutation == "anchor" else 51
        rows.append(other)
    elif mutation == "paper_anchor":
        bets.append(position(week=1))
    elif mutation == "paper_total":
        bets.append(position(total=51))
    else:
        bets = [position(), position()]
    with pytest.raises(ValueError):
        evaluation.evaluate(rows, bets)


@pytest.mark.parametrize("update", [
    {"side": "Under"}, {"status": "unknown"}, {"status": "settled", "final_total": None},
    {"status": "pending", "final_total": 50}, {"decimal_odds": 1}, {"decimal_odds": True},
    {"decimal_odds": float("inf")}, {"line": 55}, {"units_risked": 2}, {"phase": "other"},
    {"sportsbook": "draftkings", "book": "fanduel"}, {"sportsbook": ""},
])
def test_invalid_paper_records_do_not_delete_risk_or_invent_settlement(update):
    row = position()
    row.update(update)
    with pytest.raises(ValueError):
        evaluation.evaluate([], [row])
