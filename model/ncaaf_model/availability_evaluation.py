"""Pure, unvalidated prospective availability-study summaries.

``evaluate(forecasts, positions)`` accepts already verified immutable decisions
and the caller's latest valid labels at its frozen report cutoff. It performs
no fetching, fitting, label discovery, file writes or betting actions. The
minimal records do not contain receipt/artifact hashes or availability times;
the controller must verify those before calling this numerical evaluator.

Missing labels are never inferred from another row. Known totals and fixed
game-week anchors must nevertheless agree wherever the same game appears.
There is one challenger paper policy, not a second reference betting policy.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
import math
from numbers import Real
from zoneinfo import ZoneInfo

import numpy as np

VERSION = "availability-evaluation-v1-unvalidated"
BOOTSTRAP_DRAWS = 10000
BOOTSTRAP_SEED = 20260909
ZONE = ZoneInfo("America/New_York")
NAMES = ("reference", "challenger", "price_reference")
PHASES = ("initial", "gameday")
RELIABILITY_EDGES = tuple(i / 10 for i in range(11))


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _records(value, name):
    _require(isinstance(value, Sequence) and not isinstance(value, (str, bytes)), name + " must be a sequence")
    _require(all(isinstance(row, Mapping) for row in value), name + " must contain mappings")
    return value


def _number(value, name):
    _require(isinstance(value, Real) and not isinstance(value, (bool, np.bool_))
             and math.isfinite(value), name + " must be finite numeric data")
    return float(value)


def _game(value):
    _require(isinstance(value, (str, int, np.integer)) and not isinstance(value, (bool, np.bool_)),
             "Canonical positive game ID required")
    value = str(value)
    _require(value.isascii() and value.isdigit() and not value.startswith("0"),
             "Canonical positive game ID required")
    return value


def _utc(value):
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise ValueError("Invalid fixed group kickoff") from error
    _require(result.tzinfo is not None and result.utcoffset() is not None, "Timezone-aware fixed group kickoff required")
    return result.astimezone(timezone.utc)


def _week(anchor):
    day = anchor.astimezone(ZONE).date()
    return (day - timedelta(days=day.weekday())).isoformat()


def _line(value):
    value = _number(value, "Line")
    _require(value > 0 and value % 1 == .5, "Exact positive half-point line required")
    return value


def _total(value):
    if value is None:
        return None
    result = _number(value, "Final total")
    _require(result >= 0 and result.is_integer(), "Final total must be a nonnegative integer")
    return int(result)


def _register(row, games):
    gid, anchor, total = _game(row["game_id"]), _utc(row["group_kickoff"]), _total(row.get("final_total"))
    existing = games.setdefault(gid, {"anchor": anchor, "known_total": None})
    _require(existing["anchor"] == anchor, "Same game's fixed group kickoff changed")
    if total is not None:
        _require(existing["known_total"] in (None, total), "Same game has conflicting known final totals")
        existing["known_total"] = total
    return gid, _week(anchor), total


def _probability(logit):
    return 1 / (1 + math.exp(-logit)) if logit >= 0 else math.exp(logit) / (1 + math.exp(logit))


def _forecast(row, games):
    gid, week, total = _register(row, games)
    phase = row["phase"]
    _require(phase in PHASES, "Unknown forecast phase")
    line, q = _line(row["reference_line"]), _number(row["q_under"], "Under-price reference")
    _require(0 < q < 1, "Under-price reference must be strictly between zero and one")
    logits = {"reference": _number(row["reference_logit"], "Reference logit"),
              "challenger": _number(row["challenger_logit"], "Challenger logit"),
              "price_reference": math.log(q) - math.log1p(-q)}
    probabilities = {name: _probability(logit) for name, logit in logits.items()}
    probabilities["price_reference"] = q
    outcome = int(total < line) if total is not None else None
    scores = None if outcome is None else {
        name: {"log_loss": float(np.logaddexp(0., -logit if outcome else logit)),
               "brier": (probabilities[name] - outcome) ** 2}
        for name, logit in logits.items()}
    return {"game_id": gid, "week": week, "phase": phase, "scores": scores,
            "outcome_under": outcome, "probabilities": probabilities}


def _paper(row, games):
    gid, week, total = _register(row, games)
    side, status = row["side"], row["status"]
    _require(side in ("under", "over"), "Unknown paper side")
    _require(status in ("settled", "void", "pending"), "Unknown paper status")
    line, odds = _line(row["line"]), _number(row["decimal_odds"], "Decimal odds")
    _require(odds > 1, "Decimal odds must exceed one")
    if "units_risked" in row:
        _require(_number(row["units_risked"], "Units risked") == 1., "Every paper position risks exactly one unit")
    phase = row.get("phase")
    _require(phase is None or phase in PHASES, "Unknown optional paper phase")
    book = row.get("sportsbook", row.get("book"))
    _require(book is None or isinstance(book, str) and bool(book.strip()), "Optional paper book must be a nonempty name")
    if "sportsbook" in row and "book" in row:
        _require(row["sportsbook"] == row["book"], "Conflicting paper book fields")
    if status == "settled":
        _require(total is not None, "Settled paper position requires a final total")
        win = total < line if side == "under" else total > line
        profit, outcome = (odds - 1., "win") if win else (-1., "loss")
    elif status == "void":
        profit, outcome = 0., "void"
    else:
        _require(total is None, "Pending paper position cannot carry a supplied final total")
        profit, outcome = None, "pending"
    return {"game_id": gid, "week": week, "profit": profit, "outcome": outcome,
            "side": side, "phase": phase, "book": book}


def _bootstrap(rows, coverages):
    """Each row is (fixed Eastern week, numerator, denominator)."""
    weekly = {}
    for week, numerator, denominator in rows:
        _require(math.isfinite(numerator) and math.isfinite(denominator) and denominator > 0,
                 "Invalid bootstrap contribution")
        value = weekly.setdefault(week, [0., 0.])
        value[0] += numerator
        value[1] += denominator
    ordered = sorted(weekly)
    numerator = np.array([weekly[w][0] for w in ordered])
    denominator = np.array([weekly[w][1] for w in ordered])
    _require(np.isfinite(numerator).all() and np.isfinite(denominator).all(), "Weekly aggregation overflowed")
    estimate = float(math.fsum(numerator) / math.fsum(denominator)) if ordered else None
    intervals = {str(coverage): None for coverage in coverages}
    if len(ordered) >= 2 and coverages:
        generator = np.random.Generator(np.random.PCG64(BOOTSTRAP_SEED))
        chosen = generator.integers(0, len(ordered), size=(BOOTSTRAP_DRAWS, len(ordered)))
        draws = numerator[chosen].sum(axis=1) / denominator[chosen].sum(axis=1)
        _require(np.isfinite(draws).all(), "Bootstrap aggregation overflowed")
        for coverage in coverages:
            quantiles = {.975: (.0125, .9875), .95: (.025, .975), .99: (.005, .995)}[coverage]
            lower, upper = np.quantile(draws, quantiles, method="linear")
            intervals[str(coverage)] = {"lower": float(lower), "upper": float(upper)}
    return {"estimate": estimate, "records": len(rows), "contributing_weeks": len(ordered),
            "intervals": intervals,
            "interval_status": "not_requested" if not coverages else "approximate" if len(ordered) >= 2 else "fewer_than_two_weeks",
            "weekly": [{"week": week, "numerator": weekly[week][0], "denominator": weekly[week][1]} for week in ordered]}


def _reliability(scored, name, counts):
    bins = [{"lower": RELIABILITY_EDGES[i], "upper": RELIABILITY_EDGES[i + 1],
             "forecast_count": 0, "game_weight": 0., "mean_probability": None,
             "observed_under_rate": None} for i in range(10)]
    sums = np.zeros((10, 2))
    for row in scored:
        p, weight = row["probabilities"][name], 1 / counts[row["game_id"]]
        index = min(9, int(np.searchsorted(RELIABILITY_EDGES, p, side="right") - 1))
        bins[index]["forecast_count"] += 1
        bins[index]["game_weight"] += weight
        sums[index] += weight * np.array([p, row["outcome_under"]])
    for index, value in enumerate(bins):
        if value["game_weight"]:
            value["mean_probability"], value["observed_under_rate"] = (sums[index] / value["game_weight"]).tolist()
    return bins


def _forecast_summary(rows, *, inference=True):
    scored = [row for row in rows if row["scores"] is not None]
    observed_counts = Counter(row["game_id"] for row in rows)
    counts = Counter(row["game_id"] for row in scored)
    by_game = {}
    for row in scored:
        by_game.setdefault(row["game_id"], []).append(row)
    means = {name: {metric: (math.fsum(
        math.fsum(row["scores"][name][metric] for row in game) / len(game)
        for game in by_game.values()) / len(by_game) if by_game else None)
        for metric in ("log_loss", "brier")} for name in NAMES}
    differences = [(game[0]["week"], math.fsum(
        row["scores"]["challenger"]["log_loss"] - row["scores"]["reference"]["log_loss"]
        for row in game) / len(game), 1.) for game in by_game.values()]
    return {"locked_forecasts": len(rows), "scored_forecasts": len(scored),
            "unresolved_forecasts": len(rows) - len(scored), "forecast_games": len(observed_counts),
            "scored_games": len(counts), "unresolved_games": len(observed_counts) - len(counts),
            "partially_labeled_games": sum(0 < counts[g] < n for g, n in observed_counts.items()),
            "mean_scores": means,
            "paired_challenger_minus_reference": _bootstrap(differences, (.975,) if inference else ()),
            "reliability": {name: _reliability(scored, name, counts) for name in NAMES}}


def _paper_summary(rows):
    resolved = [row for row in rows if row["profit"] is not None]
    pending = len(rows) - len(resolved)
    known_profit = math.fsum(row["profit"] for row in resolved)
    complete = _bootstrap([(row["week"], row["profit"], 1.) for row in rows], (.975, .95, .99)) if not pending else None
    pessimistic = _bootstrap([(row["week"], row["profit"] if row["profit"] is not None else -1., 1.)
                              for row in rows], (.975, .95, .99))
    omissions = []
    for week in sorted({row["week"] for row in rows}):
        remaining = [row for row in rows if row["week"] != week]
        ready = all(row["profit"] is not None for row in remaining)
        omissions.append({"omitted_week": week, "locked_positions": len(remaining),
            "pending_positions": sum(row["profit"] is None for row in remaining),
            "full_cohort_roi": math.fsum(row["profit"] for row in remaining) / len(remaining) if remaining and ready else None,
            "unresolved_as_loss_roi": math.fsum(row["profit"] if row["profit"] is not None else -1. for row in remaining) / len(remaining) if remaining else None})
    return {"locked_positions": len(rows), "total_units_risked": len(rows),
            "settled_positions": sum(row["outcome"] in ("win", "loss") for row in rows),
            "void_positions": sum(row["outcome"] == "void" for row in rows),
            "pending_positions": pending, "resolved_positions": len(resolved),
            "wins": sum(row["outcome"] == "win" for row in rows),
            "losses": sum(row["outcome"] == "loss" for row in rows),
            "known_profit_units": known_profit,
            "full_cohort_status": "no_positions" if not rows else "incomplete" if pending else "complete",
            "full_cohort_roi": complete["estimate"] if complete else None,
            "full_cohort_uncertainty": complete,
            "settled_only_descriptive": {"included_statuses": ["settled", "void"],
                "positions": len(resolved), "units_risked": len(resolved), "profit_units": known_profit,
                "roi": known_profit / len(resolved) if resolved else None, "is_partial_cohort": bool(pending)},
            "unresolved_as_full_loss": pessimistic, "leave_one_week_out": omissions,
            "by_side": dict(Counter(row["side"] for row in rows)),
            "by_book": dict(Counter(row["book"] if row["book"] is not None else "not_recorded" for row in rows)),
            "by_phase": dict(Counter(row["phase"] if row["phase"] is not None else "not_recorded" for row in rows))}


def evaluate(forecasts, positions):
    """Evaluate direct records without changing them or filling missing labels.

    Forecast fields: game_id, phase, group_kickoff, reference_line, q_under,
    reference_logit, challenger_logit, final_total (nullable).
    Position fields: unique game_id, group_kickoff, side, line, decimal_odds,
    status (settled/void/pending), and final_total for settled positions.
    Optional position phase and sportsbook/book provide descriptive counts.
    A supplied void status is an external administrative resolution, not one
    inferred from a missing score. Its zero return retains one unit of risk.
    """
    games, keys, paper_games = {}, set(), set()
    forecast_rows, paper_rows = [], []
    for source in _records(forecasts, "forecasts"):
        row = _forecast(source, games)
        key = (row["game_id"], row["phase"])
        _require(key not in keys, "Duplicate game/phase forecast")
        keys.add(key)
        forecast_rows.append(row)
    for source in _records(positions, "positions"):
        row = _paper(source, games)
        _require(row["game_id"] not in paper_games, "At most one paper position per game")
        paper_games.add(row["game_id"])
        paper_rows.append(row)
    # Stable summation and draw assignment must not depend on file enumeration.
    forecast_rows.sort(key=lambda row: (row["week"], int(row["game_id"]), row["phase"]))
    paper_rows.sort(key=lambda row: (row["week"], int(row["game_id"])))
    summary = _forecast_summary(forecast_rows)
    summary["by_phase"] = {phase: _forecast_summary([row for row in forecast_rows if row["phase"] == phase], inference=False)
                           for phase in PHASES}
    return {"schema_version": VERSION, "forecasts": summary, "paper": _paper_summary(paper_rows),
            "bootstrap": {"generator": "PCG64", "seed": BOOTSTRAP_SEED, "draws": BOOTSTRAP_DRAWS,
                          "unit": "fixed group-kickoff Eastern Monday week", "quantile_method": "linear"},
            "local_endpoints": ["challenger locked-cohort flat-unit ROI", "game-equal challenger-minus-reference log loss"],
            "local_endpoint_interval_coverage": .975, "automatic_promotion": False, "high_confidence_edge_claim": False,
            "limitations": ["97.5% intervals are a local two-endpoint Bonferroni sensitivity under an approximate weekly-block model; they do not cover broader project searches or diagnostics.",
                            "Repeated teams and expanding training induce dependence that weekly resampling may not fully capture.",
                            "The caller must verify immutable source/quote/artifact records and actual label availability at the fixed report cutoff; these minimal inputs cannot certify either.",
                            "Observed paper offers do not establish acceptance or profitable real-money execution.",
                            "Missing labels remain missing; pending paper risk remains in the full locked cohort."]}
