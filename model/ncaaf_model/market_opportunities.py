"""Two-book totals opportunities with exact settlement, not a truth-by-consensus.

Dominance is a cheaper/better payoff, not positive EV. Arbitrage is proved over
all nonnegative integer final scores, conditional on both legs being accepted
and settled under the same full-game/OT/void rules. Probability-based EV requires
external probability bounds; this module never manufactures them from a peer.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import itertools
import math
from typing import Any, Iterable

import numpy as np


EPS = 1e-10
FULL_GAME = "full_game_total_including_overtime"


def _time(value: Any) -> datetime | None:
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


@dataclass(frozen=True)
class TotalQuote:
    event_id: str
    book: str
    side: str
    line: float
    decimal_odds: float
    market_updated_at: str | None = None
    observed_at: str | None = None
    observation_kind: str | None = None
    source: str = "unknown"
    provider_event_id: str | None = None
    settlement_scope: str = FULL_GAME

    def __post_init__(self):
        if self.side not in {"over", "under"}:
            raise ValueError("Side must be over or under")
        if not math.isfinite(self.line) or self.line < 0 or abs(2 * self.line - round(2 * self.line)) > EPS:
            raise ValueError("Only nonnegative integer/half-point full-game lines are supported")
        if not math.isfinite(self.decimal_odds) or self.decimal_odds <= 1:
            raise ValueError("Finite decimal odds greater than one are required")


def settlement_profit(quote: TotalQuote, scores: Any) -> np.ndarray:
    """Net profit per unit stake, including stake refunds on integer pushes."""
    scores = np.asarray(scores, dtype=float)
    if not np.isfinite(scores).all() or (scores < 0).any() or (scores != np.floor(scores)).any():
        raise ValueError("Final scores must be nonnegative integers")
    wins = scores > quote.line if quote.side == "over" else scores < quote.line
    return np.where(scores == quote.line, 0., np.where(wins, quote.decimal_odds - 1., -1.))


def settlement_states(quotes: Iterable[TotalQuote]) -> np.ndarray:
    """One exact representative from every payoff cell, including the high tail.

    Payoffs only change immediately around a quoted total. Enumerating these
    breakpoints represents unbounded integer scores without a maximum-score
    assumption, and includes even extremely unlikely scores conservatively.
    """
    states = {0}
    for quote in quotes:
        point = math.floor(quote.line)
        states.update(max(0, point + offset) for offset in (-1, 0, 1, 2))
    return np.array(sorted(states), dtype=int)


def _compatible(a: TotalQuote, b: TotalQuote):
    if a.event_id != b.event_id or a.settlement_scope != b.settlement_scope:
        raise ValueError("Quotes must describe the same event and settlement scope")


def payoff_dominance(better: TotalQuote, worse: TotalQuote) -> dict:
    """Statewise weak dominance with a strict improvement in at least one cell."""
    _compatible(better, worse)
    states = settlement_states([better, worse])
    improvement = settlement_profit(better, states) - settlement_profit(worse, states)
    dominates = bool((improvement >= -EPS).all() and (improvement > EPS).any())
    return {"dominates": dominates, "minimum_payoff_improvement": float(improvement.min()),
            "maximum_payoff_improvement": float(improvement.max()),
            "positive_ev_proven": False,
            "interpretation": "Payoff improvement relative to another quote, not evidence of positive expected profit."}


def optimize_two_leg_hedge(over: TotalQuote, under: TotalQuote) -> dict:
    """Maximize worst-case net ROI for one unit of TOTAL two-leg stake.

    With x allocated to Over, every settlement cell gives an affine function
    x*r_over+(1-x)*r_under. Its lower envelope is concave. The optimum is at an
    endpoint or a pairwise intersection, which are enumerated exactly here.
    """
    _compatible(over, under)
    if over.side != "over" or under.side != "under" or over.book == under.book:
        raise ValueError("One Over and one Under from different books are required")
    states = settlement_states([over, under])
    r_over, r_under = settlement_profit(over, states), settlement_profit(under, states)
    slopes, intercepts = r_over - r_under, r_under
    balanced = under.decimal_odds / (over.decimal_odds + under.decimal_odds)
    candidates = [0., 1., balanced]
    for i, j in itertools.combinations(range(len(states)), 2):
        denominator = slopes[i] - slopes[j]
        if abs(denominator) > EPS:
            x = (intercepts[j] - intercepts[i]) / denominator
            if -EPS <= x <= 1 + EPS:
                candidates.append(float(np.clip(x, 0., 1.)))
    allocation = max(candidates, key=lambda x: (round(float(np.min(x * slopes + intercepts)), 12), -abs(x - balanced)))
    payoffs = allocation * r_over + (1 - allocation) * r_under
    floor, ceiling = float(payoffs.min()), float(payoffs.max())
    if floor > EPS:
        classification = "strict_score_arbitrage"
    elif floor >= -EPS and ceiling > EPS:
        classification = "nonnegative_hedge_with_upside"
    elif ceiling <= EPS:
        classification = "no_positive_settlement_outcome"
    else:
        classification = "risky_middle_or_hedge"
    # Equalized outside returns allow a transparent sufficient middle-probability
    # condition. Push endpoints with smaller bonuses are retained in that bound.
    tail_floor = min(float(payoffs[0]), float(payoffs[-1]))
    bonus = payoffs > tail_floor + EPS
    minimum_bonus_payoff = float(payoffs[bonus].min()) if bonus.any() else None
    required_mass = None
    if floor >= tail_floor - EPS and minimum_bonus_payoff is not None:
        required_mass = max(0., -tail_floor / (minimum_bonus_payoff - tail_floor))
    return {"kind": classification, "event_id": over.event_id,
            "over": asdict(over), "under": asdict(under),
            "over_stake_fraction": float(allocation), "under_stake_fraction": float(1 - allocation),
            "worst_case_roi": floor, "best_case_roi": ceiling,
            "outside_roi": tail_floor,
            "strict_positive_roi_all_scores": floor > EPS,
            "nonnegative_all_scores": floor >= -EPS,
            "middle_probability_sufficient_for_positive_ev": required_mass,
            "middle_probability_supplied": False,
            "payoff_states": [{"score": int(score), "roi": float(value)} for score, value in zip(states, payoffs)],
            "assumptions": ["Both legs accepted at these exact prices and stake fractions",
                            "Identical game/OT settlement and compatible cancellation/void treatment",
                            "No fees, taxes, stake limits or stake rounding losses included"],
            "execution_confirmed": False}


def probability_ev_bounds(decimal_odds: float, win_bounds: tuple[float, float],
                          push_bounds: tuple[float, float] = (0., 0.)) -> tuple[float, float]:
    """Sharp EV range over externally supplied win/push probability bounds.

    Constraints are nonnegative win, push, loss probabilities summing to one.
    EV = decimal_odds * P(win) + P(push) - 1. No sportsbook price is a probability
    bound unless a caller explicitly supplies and labels that extra assumption.
    """
    if not math.isfinite(decimal_odds) or decimal_odds <= 1:
        raise ValueError("Invalid decimal price")
    wl, wh = map(float, win_bounds)
    pl, ph = map(float, push_bounds)
    if not all(math.isfinite(x) for x in (wl, wh, pl, ph)) or not (0 <= wl <= wh <= 1 and 0 <= pl <= ph <= 1) or wl + pl > 1:
        raise ValueError("Probability bounds are invalid or infeasible")
    minimum = decimal_odds * wl + pl - 1
    best_win = min(wh, 1 - pl)
    maximum = decimal_odds * best_win + min(ph, 1 - best_win) - 1
    return float(minimum), float(maximum)


def bounded_probability_signal(quote: TotalQuote, win_bounds: tuple[float, float],
                               push_bounds: tuple[float, float] = (0., 0.), *,
                               probability_provenance: str, minimum_ev: float = .01) -> dict:
    if not probability_provenance.strip():
        raise ValueError("External probability provenance must be explicit")
    if quote.line % 1 and tuple(push_bounds) != (0., 0.):
        raise ValueError("Half-point lines cannot push on integer final scores")
    if not math.isfinite(minimum_ev) or minimum_ev < 0:
        raise ValueError("Minimum EV must be finite and nonnegative")
    low, high = probability_ev_bounds(quote.decimal_odds, win_bounds, push_bounds)
    return {"kind": "probability_bound_signal", "quote": asdict(quote), "ev_lower": low, "ev_upper": high,
            "signal": low >= minimum_ev, "win_probability_bounds": list(win_bounds),
            "push_probability_bounds": list(push_bounds), "probability_provenance": probability_provenance,
            "interpretation": "Positive EV conditional on the supplied probability bounds, not a score arbitrage.",
            "execution_confirmed": False}


def quote_observation(quote: TotalQuote, now: datetime, max_observation_seconds: float = 120.) -> dict:
    observed, updated = _time(quote.observed_at), _time(quote.market_updated_at)
    observed_age = (now - observed).total_seconds() if observed else None
    update_age = (now - updated).total_seconds() if updated else None
    authoritative = quote.source == "odds_api_io" and quote.observation_kind == "provider_full_state"
    recent = bool(authoritative and observed_age is not None and -5 <= observed_age <= max_observation_seconds)
    # A provider update clock far in the future is malformed even if just seen.
    if update_age is not None and update_age < -300:
        recent = False
    return {"recent_full_state_observation": recent, "observation_age_seconds": observed_age,
            "market_update_age_seconds": update_age, "book_acceptance_verified": False,
            "cache_policy": f"{max_observation_seconds:g}-second receipt-age ceiling; provider recommends 30–60-second pre-match caching; reconfirm immediately before any possible execution"}


def quotes_from_normalized_event(event: dict, allowed_books: tuple[str, ...] = ("draftkings", "fanduel")) -> list[TotalQuote]:
    quotes = {}
    for book in event.get("bookmakers", []):
        if book.get("key") not in allowed_books:
            continue
        for market in book.get("markets", []):
            if market.get("key") != "totals":
                continue
            for outcome in market.get("outcomes", []):
                try:
                    american = float(outcome["price"])
                    decimal = float(outcome.get("decimal_price") or (1 + american / 100 if american > 0 else 1 + 100 / abs(american)))
                    quote = TotalQuote(event_id=str(event["id"]), book=str(book["key"]),
                        side=str(outcome["name"]).lower(), line=float(outcome["point"]), decimal_odds=decimal,
                        market_updated_at=market.get("last_update") or book.get("last_update"),
                        observed_at=market.get("observed_at"), observation_kind=market.get("observation_kind"),
                        source=book.get("source", event.get("source", "unknown")),
                        provider_event_id=book.get("source_event_id", event.get("source_event_id")))
                except (ValueError, TypeError, KeyError, ZeroDivisionError):
                    continue
                key = quote.book, quote.side, quote.line
                previous = quotes.get(key)
                # Never line-shop across two aggregators' copies of one book.
                if previous is None or str(quote.observed_at or "") > str(previous.observed_at or ""):
                    quotes[key] = quote
    return list(quotes.values())


def scan_market_opportunities(events: list[dict], now: datetime,
                              allowed_books: tuple[str, ...] = ("draftkings", "fanduel"),
                              max_observation_seconds: float = 120.) -> dict:
    hedges, dominance, observations = [], [], []
    games_with_two_books = 0
    for event in events:
        kickoff = _time(event.get("commence_time"))
        if kickoff is None or kickoff <= now:
            continue
        quotes = quotes_from_normalized_event(event, allowed_books)
        if len({q.book for q in quotes}) < 2:
            continue
        games_with_two_books += 1
        name = f"{event.get('away_team')} at {event.get('home_team')}"
        for quote in quotes:
            observations.append({"quote": asdict(quote), **quote_observation(quote, now, max_observation_seconds)})
        for better, worse in itertools.permutations(quotes, 2):
            if better.book == worse.book or better.side != worse.side:
                continue
            result = payoff_dominance(better, worse)
            if result["dominates"]:
                dominance.append({"matchup": name, "better": asdict(better), "worse": asdict(worse), **result})
        for over in [q for q in quotes if q.side == "over"]:
            for under in [q for q in quotes if q.side == "under" and q.book != over.book]:
                hedge = optimize_two_leg_hedge(over, under)
                hedge.update(matchup=name, kickoff=event["commence_time"])
                hedge["both_recently_observed"] = all(quote_observation(q, now, max_observation_seconds)["recent_full_state_observation"] for q in (over, under))
                hedge["screen_status"] = "recent_provider_quotes_reconfirm_required" if hedge["both_recently_observed"] else "archived_or_unverified_observation"
                hedges.append(hedge)
    hedges.sort(key=lambda x: (x["worst_case_roi"], x["best_case_roi"]), reverse=True)
    arbitrages = [h for h in hedges if h["nonnegative_all_scores"] and h["best_case_roi"] > EPS]
    return {"version": "two-book-opportunities-v1", "as_of": now.isoformat(),
            "games_with_two_books": games_with_two_books, "hedge_pairs_evaluated": len(hedges),
            "arbitrages": arbitrages, "dominance": dominance, "hedges": hedges,
            "quote_observations": observations,
            "distribution_free_interpretation": "Only the exact hedge floor bounds ROI without a scoring probability model. Dominance alone does not establish positive EV.",
            "actual_bets_placed": 0}


def refetch_io_events(session, key: str, provider_event_ids: list[str], now: datetime,
                     allowed_books: tuple[str, ...] = ("draftkings", "fanduel"), timeout: float = 20.) -> list[dict]:
    """Read-only confirming full-state fetch for at most ten candidate events.

    This performs no account-selection mutation and never transmits a wager.
    Consumers must replace previous event/book state, not union missing markets.
    """
    from .odds_api_io import BOOK_KEYS, _get, parse_odds_api_io
    ids = list(dict.fromkeys(str(value) for value in provider_event_ids))
    if not ids or len(ids) > 10:
        raise ValueError("Confirm one to ten explicit provider event IDs")
    books = [name for name, book in BOOK_KEYS.items() if book in allowed_books]
    payload = _get(session, "/odds/multi", key, timeout, eventIds=",".join(ids), bookmakers=",".join(books))
    return parse_odds_api_io(payload, now, allowed_books, observed_at=datetime.now(timezone.utc))


def confirm_hedge(original: dict, refreshed_events: list[dict], now: datetime) -> dict:
    """Reprice both original legs from a replacement snapshot; absence fails closed."""
    old_over, old_under = TotalQuote(**original["over"]), TotalQuote(**original["under"])
    found = []
    for old in (old_over, old_under):
        matches = [q for event in refreshed_events for q in quotes_from_normalized_event(event)
                   if q.provider_event_id == old.provider_event_id and old.provider_event_id is not None
                   and q.book == old.book and q.side == old.side and q.line == old.line]
        if len(matches) != 1 or not quote_observation(matches[0], now)["recent_full_state_observation"]:
            return {"confirmed": False, "reason": "A leg is absent, ambiguous, or not recently observed in replacement state"}
        found.append(matches[0])
    result = optimize_two_leg_hedge(*found)
    result["confirmed"] = True  # Confirms provider presence, never stake acceptance.
    result["confirmation_scope"] = "Provider full-state quote presence only; no wager submitted"
    result["prices_unchanged_or_improved"] = all(new.decimal_odds >= old.decimal_odds - EPS for new, old in zip(found, (old_over, old_under)))
    return result
