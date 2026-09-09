from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from ncaaf_model.market_opportunities import (
    TotalQuote, bounded_probability_signal, confirm_hedge, optimize_two_leg_hedge,
    payoff_dominance, probability_ev_bounds, quote_observation, scan_market_opportunities,
    settlement_profit, settlement_states,
)


NOW = datetime(2026, 9, 9, 0, 0, tzinfo=timezone.utc)


def quote(side, line, decimal, book=None, **kwargs):
    return TotalQuote("event", book or ("draftkings" if side == "over" else "fanduel"), side, line, decimal, **kwargs)


def test_equal_half_point_positive_prices_are_probability_free_arbitrage():
    result = optimize_two_leg_hedge(quote("over", 52.5, 2.1), quote("under", 52.5, 2.1))
    assert result["worst_case_roi"] == pytest.approx(.05)
    assert result["over_stake_fraction"] == pytest.approx(.5)
    assert result["kind"] == "strict_score_arbitrage"
    assert not result["execution_confirmed"]


def test_integer_same_line_can_refund_both_legs_instead_of_guaranteed_profit():
    result = optimize_two_leg_hedge(quote("over", 52, 2.1), quote("under", 52, 2.1))
    assert result["worst_case_roi"] == pytest.approx(0.)
    assert result["best_case_roi"] == pytest.approx(.05)
    assert result["kind"] == "nonnegative_hedge_with_upside"
    assert next(row for row in result["payoff_states"] if row["score"] == 52)["roi"] == 0


def test_middle_reports_loss_outside_and_needed_middle_probability():
    result = optimize_two_leg_hedge(quote("over", 51.5, 1 + 100 / 110), quote("under", 53.5, 1 + 100 / 110))
    assert result["worst_case_roi"] == pytest.approx(-1 / 22)
    assert result["best_case_roi"] == pytest.approx(10 / 11)
    assert result["middle_probability_sufficient_for_positive_ev"] == pytest.approx(1 / 21)
    assert not result["nonnegative_all_scores"]


def test_reverse_middle_cannot_be_misclassified_by_inverse_odds_sum():
    # 1/3 + 1/3 < 1, but scores 52 and 53 lose BOTH legs.
    result = optimize_two_leg_hedge(quote("over", 53.5, 3), quote("under", 51.5, 3))
    assert result["worst_case_roi"] == -1
    assert result["kind"] == "risky_middle_or_hedge"
    assert not result["nonnegative_all_scores"]


def test_asymmetric_hedge_returns_match_known_inverse_price_allocation():
    result = optimize_two_leg_hedge(quote("over", 49.5, 2.2), quote("under", 52.5, 2.0))
    assert result["over_stake_fraction"] == pytest.approx(2 / 4.2)
    assert result["worst_case_roi"] == pytest.approx(1 / (1 / 2.2 + 1 / 2) - 1)


@pytest.mark.parametrize("over_line,under_line", [(51.5, 53.5), (52, 53), (53, 52), (52, 52), (52.5, 52.5)])
def test_minimax_solution_covers_all_integer_scores_and_beats_dense_stake_grid(over_line, under_line):
    over, under = quote("over", over_line, 1.83), quote("under", under_line, 2.15)
    result = optimize_two_leg_hedge(over, under)
    full_scores = np.arange(0, 401)
    a, b = settlement_profit(over, full_scores), settlement_profit(under, full_scores)
    x = result["over_stake_fraction"]
    assert np.min(x * a + (1 - x) * b) == pytest.approx(result["worst_case_roi"])
    grid = np.linspace(0, 1, 5001)
    brute_best = np.max(np.min(grid[:, None] * a + (1 - grid[:, None]) * b, axis=1))
    assert result["worst_case_roi"] >= brute_best - 1e-10
    assert len(settlement_states([over, under])) < len(full_scores)


def test_dominance_does_not_claim_positive_ev_or_ignore_price_tradeoff():
    better = quote("over", 51.5, 1.95)
    worse = quote("over", 52.5, 1.90, book="fanduel")
    result = payoff_dominance(better, worse)
    assert result["dominates"] and not result["positive_ev_proven"]
    assert not payoff_dominance(quote("over", 51.5, 1.89), worse)["dominates"]
    assert not payoff_dominance(better, better)["dominates"]


def test_probability_bounds_are_external_and_pushes_change_ev():
    low, high = probability_ev_bounds(1.9, (.55, .58), (.02, .03))
    assert low == pytest.approx(.065)
    assert high == pytest.approx(.132)
    assert probability_ev_bounds(1.9, (0., 1.)) == pytest.approx((-1., .9))
    with pytest.raises(ValueError):
        probability_ev_bounds(1.9, (.9, .95), (.2, .3))
    with pytest.raises(ValueError):
        bounded_probability_signal(quote("over", 52.5, 1.9), (.55, .6), probability_provenance="")
    with pytest.raises(ValueError):
        bounded_probability_signal(quote("over", 52.5, 1.9), (.55, .6), (.01, .02), probability_provenance="Impossible push")
    result = bounded_probability_signal(quote("over", 52.5, 1.9), (.55, .6), probability_provenance="Independent calibrated forecast interval")
    assert result["signal"] and result["ev_lower"] == pytest.approx(.045)


def test_recent_full_state_can_contain_unchanged_old_market_update():
    q = quote("over", 52.5, 1.9, source="odds_api_io", observed_at=NOW.isoformat(),
              observation_kind="provider_full_state", market_updated_at=(NOW - timedelta(days=3)).isoformat())
    result = quote_observation(q, NOW)
    assert result["recent_full_state_observation"]
    assert result["market_update_age_seconds"] == 3 * 86400
    assert not result["book_acceptance_verified"]
    assert not quote_observation(q, NOW + timedelta(minutes=3))["recent_full_state_observation"]
    public = quote("over", 52.5, 1.9, source="actionnetwork_public", observed_at=NOW.isoformat(), observation_kind="provider_full_state")
    assert not quote_observation(public, NOW)["recent_full_state_observation"]


def _event(include_under=True, decimal=2.1):
    books = []
    for book, side in [("draftkings", "Over"), ("fanduel", "Under")]:
        outcomes = [] if book == "fanduel" and not include_under else [{"name": side, "point": 52.5, "price": 110, "decimal_price": decimal}]
        books.append({"key": book, "source": "odds_api_io", "source_event_id": "123", "markets": [
            {"key": "totals", "last_update": "2026-09-06T00:00Z", "observed_at": NOW.isoformat(),
             "observation_kind": "provider_full_state", "outcomes": outcomes}]})
    return {"id": "oddsio-123", "source_event_id": "123", "home_team": "Home", "away_team": "Away",
            "commence_time": (NOW + timedelta(days=1)).isoformat(), "bookmakers": books}


def test_scanner_and_reconfirmation_require_both_legs_in_replacement_state():
    report = scan_market_opportunities([_event()], NOW)
    original = report["arbitrages"][0]
    assert original["both_recently_observed"]
    confirmed = confirm_hedge(original, [_event()], NOW)
    assert confirmed["confirmed"] and confirmed["prices_unchanged_or_improved"]
    missing = confirm_hedge(original, [_event(include_under=False)], NOW)
    assert not missing["confirmed"]
    changed = confirm_hedge(original, [_event(decimal=1.9)], NOW)
    assert changed["confirmed"] and not changed["nonnegative_all_scores"]
    assert not changed["prices_unchanged_or_improved"]


def test_scope_mismatch_is_rejected():
    with pytest.raises(ValueError):
        optimize_two_leg_hedge(quote("over", 52.5, 2.1), quote("under", 52.5, 2.1, settlement_scope="regulation_only"))
