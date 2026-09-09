"""Pure serialization, current-quote predictions and challenger paper choices.

The caller verifies immutable original source/receipt/hash provenance, designates
the unique DK-then-FD reference before inference, enforces training eligibility,
and permits at most one paper position per game. These utilities neither fetch
data nor prove that an observed price was executable. Invalid core inputs raise
ValueError; individually invalid/stale offers cannot become paper positions.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from numbers import Integral, Real
from zoneinfo import ZoneInfo

import numpy as np
from scipy.special import expit

from .availability_models import (ALL_FEATURES, BASE_FEATURES, BINARY_FEATURES,
    VERSION as MODEL_VERSION, ProbabilityModel, Standardizer)


VERSION = "availability-decisions-v1-unvalidated"
MODEL_KEYS = frozenset({"version", "names", "mean", "scale", "coefficients",
                       "training_rows", "training_games", "optimizer_iterations"})
BOOKS = ("draftkings", "fanduel")
PHASES = ("initial", "gameday")
MAX_AGE_SECONDS = 120.


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _number(value, name, *, binary=False):
    _require(isinstance(value, Real) and (binary or not isinstance(value, (bool, np.bool_))),
             name + " must be numeric")
    result = float(value)
    _require(math.isfinite(result), name + " must be finite")
    if binary:
        _require(result in (0., 1.), name + " must be binary")
    return result


def _count(value, name, minimum):
    _require(isinstance(value, Integral) and not isinstance(value, (bool, np.bool_))
             and value >= minimum, name + " must be an integer count")
    return int(value)


def _game_id(value):
    _require(isinstance(value, (str, Integral)) and not isinstance(value, (bool, np.bool_)),
             "Canonical positive game ID required")
    result = str(value)
    _require(result.isascii() and result.isdigit() and not result.startswith("0"),
             "Canonical positive game ID required")
    return result


def _time(value):
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("Timezone-aware ISO timestamp required") from exc
    _require(result.tzinfo is not None and result.utcoffset() is not None,
             "Timezone-aware ISO timestamp required")
    return result.astimezone(timezone.utc)


def _stamp(value):
    return _time(value).isoformat().replace("+00:00", "Z")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _offers_digest(offers):
    """Bind original offers without treating malformed peer values as valid JSON.

    The typed encoding preserves invalid scalars as explicit tags so NaN/Infinity
    never enter JSON as numeric values. Malformed peers can still be skipped by
    selection; repairing, changing or replacing them after inference is detected.
    """
    _require(isinstance(offers, list), "Original observed offer list required")

    def encode(value, active):
        if value is None:
            return ["null"]
        if isinstance(value, (bool, np.bool_)):
            return ["boolean", bool(value)]
        if isinstance(value, str):
            return ["string", value]
        if isinstance(value, Integral):
            return ["integer", str(value)]
        if isinstance(value, Real):
            number = float(value)
            return ["float", number.hex()] if math.isfinite(number) else ["invalid_numeric", str(number)]
        if isinstance(value, (dict, list)):
            _require(id(value) not in active, "Original JSON offers cannot contain cycles")
            active = active | {id(value)}
            if isinstance(value, dict):
                _require(all(isinstance(key, str) for key in value), "Original JSON offer keys must be text")
                entries = [[encode(key, active), encode(item, active)] for key, item in value.items()]
                entries.sort(key=lambda item: json.dumps(item[0], sort_keys=True, allow_nan=False))
                return ["mapping", entries]
            return ["sequence", [encode(item, active) for item in value]]
        raise ValueError("Original offers must contain JSON-shaped values")

    return _digest({"encoding": "original-offers-typed-v1", "value": encode(offers, set())})


def _array(value, length, name):
    _require(isinstance(value, (list, tuple, np.ndarray)), name + " must be a vector")
    raw = np.asarray(value)
    _require(raw.ndim == 1 and len(raw) == length and raw.dtype.kind in "iuf",
             name + " has invalid length or type")
    _require(not any(isinstance(item, (bool, np.bool_)) for item in value), name + " cannot contain Boolean values")
    array = np.asarray(raw, dtype=float).copy()
    _require(bool(np.isfinite(array).all()), name + " must be finite")
    array.setflags(write=False)
    return array


def model_from_dict(value: dict) -> ProbabilityModel:
    """Load the exact numerical model schema; chronology stays with its artifact."""
    _require(isinstance(value, Mapping) and set(value) == MODEL_KEYS, "Exact model schema required")
    _require(value["version"] == MODEL_VERSION, "Model version mismatch")
    names = value["names"]
    _require(isinstance(names, (list, tuple)) and all(isinstance(n, str) for n in names),
             "Ordered model names required")
    names = tuple(names)
    _require(names in (BASE_FEATURES, ALL_FEATURES), "Unknown or reordered model names")
    mean = _array(value["mean"], len(names), "Mean")
    scale = _array(value["scale"], len(names), "Scale")
    _require(bool((scale > 0).all()), "Positive standardizer scales required")
    coefficients = _array(value["coefficients"], len(names) + 1, "Coefficients")
    rows = _count(value["training_rows"], "Training rows", 1)
    games = _count(value["training_games"], "Training games", 1)
    iterations = _count(value["optimizer_iterations"], "Optimizer iterations", 0)
    _require(games <= rows <= 2 * games, "Rows must represent one or two unique phases per game")
    return ProbabilityModel(Standardizer(names, mean, scale), coefficients, rows, games, iterations)


def model_to_dict(model: ProbabilityModel) -> dict:
    """Serialize finite coefficients/transforms without rounding or refitting."""
    _require(isinstance(model, ProbabilityModel), "ProbabilityModel required")
    _require(isinstance(model.standardizer, Standardizer), "Standardizer required")
    value = {"version": MODEL_VERSION, "names": list(model.standardizer.names),
             "mean": model.standardizer.mean.tolist(), "scale": model.standardizer.scale.tolist(),
             "coefficients": model.coefficients.tolist(), "training_rows": model.training_rows,
             "training_games": model.training_games, "optimizer_iterations": model.optimizer_iterations}
    checked = model_from_dict(value)
    value.update(training_rows=checked.training_rows, training_games=checked.training_games,
                 optimizer_iterations=checked.optimizer_iterations)
    return value


def _quote(value, game_id):
    _require(isinstance(value, Mapping), "Paired quote required")
    _require(_game_id(value["game_id"]) == game_id, "Quote game mismatch")
    _require(value["sportsbook"] in BOOKS and value["market"] == "Totals"
             and value["period"] == "full_game", "Allowed book and main full-game total required")
    line = _number(value["line"], "Line")
    _require(line > 0 and line % 1 == .5, "Exact positive half-point reference line required")
    over = _number(value["over_decimal_odds"], "Over price")
    under = _number(value["under_decimal_odds"], "Under price")
    _require(min(over, under) > 1., "Both decimal prices must exceed one")
    quote_id = value["quote_id"]
    _require(isinstance(quote_id, str) and bool(quote_id.strip()), "Original quote identity required")
    requested, observed = _time(value["requested_at"]), _time(value["observed_at"])
    _require(requested <= observed, "Quote receipt predates request")
    return {"game_id": game_id, "quote_id": quote_id, "sportsbook": value["sportsbook"],
            "line": line, "over_decimal_odds": over, "under_decimal_odds": under,
            "requested_at": _stamp(requested), "observed_at": _stamp(observed),
            "receipt_path": value.get("receipt_path"), "body_sha256": value.get("body_sha256")}


def _available(value):
    _require(isinstance(value, Mapping) and set(value) == {"reference", "challenger"},
             "Both model availability timestamps required")
    return {name: _stamp(value[name]) for name in ("reference", "challenger")}


def _observation(observation, *, available_at, recorded_at):
    _require(isinstance(observation, Mapping), "Observation required")
    gid = _game_id(observation["game_id"])
    phase = observation["phase"]
    _require(phase in PHASES, "Canonical study phase required")
    kickoff, anchor, recorded = map(_time, (observation["kickoff"], observation["group_kickoff"], recorded_at))
    report, context = map(_time, (observation["report_received_at"], observation["context_received_at"]))
    quote = _quote(observation["reference_quote"], gid)
    requested, observed = map(_time, (quote["requested_at"], quote["observed_at"]))
    _require(report <= context <= requested <= observed <= recorded < kickoff,
             "Report, context, quote, inference and kickoff order invalid")
    _require((recorded - observed).total_seconds() <= MAX_AGE_SECONDS,
             "Prediction exceeded original quote age limit")
    available = _available(available_at)
    role_times = observation["role_available_at"]
    _require(isinstance(role_times, (list, tuple)), "Explicit role availability list required")
    role_times = [_stamp(time) for time in role_times]
    _require(all(_time(time) < requested for time in [*available.values(), *role_times]),
             "Models and roles must be available strictly before quote request")
    raw = observation["features"]
    _require(isinstance(raw, Mapping) and set(raw) == set(ALL_FEATURES), "Exact scalar feature set required")
    features = {name: _number(raw[name], name, binary=name in BINARY_FEATURES) for name in ALL_FEATURES}
    _require(features["phase_gameday"] == float(phase == "gameday"), "Phase feature mismatch")
    _require(features["market_total"] == quote["line"], "Feature market total differs from reference line")
    expected = math.log1p((kickoff - observed).total_seconds() / 3600.)
    _require(abs(features["log_hours_to_kickoff"] - expected) <= 1e-12,
             "Time-to-kickoff feature must use actual reference receipt")
    inverse_under, inverse_over = 1. / quote["under_decimal_odds"], 1. / quote["over_decimal_odds"]
    q = inverse_under / (inverse_under + inverse_over)
    _require(0 < q < 1, "Under-price reference is not numerically interior")
    normalized = {"game_id": gid, "phase": phase, "group_kickoff": _stamp(anchor),
        "kickoff": _stamp(kickoff), "report_received_at": _stamp(report),
        "context_received_at": _stamp(context), "features": features,
        "reference_quote": quote, "role_available_at": role_times}
    if "observation_id" in observation:
        oid = observation["observation_id"]
        _require(isinstance(oid, str) and bool(oid.strip()), "Observation ID must be nonempty text")
        normalized["observation_id"] = oid
    return normalized, available, q


def make_prediction(observation, artifact_pair, *, available_at, recorded_at) -> dict:
    """Predict both prescribed models at the already designated original pair."""
    try:
        normalized, available, q = _observation(observation, available_at=available_at, recorded_at=recorded_at)
        _require(isinstance(artifact_pair, Mapping) and {"cutoff", "reference", "challenger"} <= set(artifact_pair),
                 "Reference/challenger artifact pair required")
        cutoff = _time(artifact_pair["cutoff"])
        eastern = cutoff.astimezone(ZoneInfo("America/New_York"))
        _require(eastern.weekday() == 0 and (eastern.hour, eastern.minute, eastern.second, eastern.microsecond) == (0, 0, 0, 0),
                 "Monday 00:00 Eastern cutoff required")
        current = _time(recorded_at).astimezone(ZoneInfo("America/New_York"))
        current_monday = current.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=current.weekday())
        _require(cutoff == current_monday, "Current Eastern week's artifact pair required; no older-fit fallback")
        _require(all(cutoff <= _time(time) for time in available.values()), "Artifact available before its cutoff")
        models = {name: model_from_dict(artifact_pair[name]) for name in ("reference", "challenger")}
        _require(models["reference"].standardizer.names == BASE_FEATURES
                 and models["challenger"].standardizer.names == ALL_FEATURES, "Artifact model roles reversed")
        _require((models["reference"].training_rows, models["reference"].training_games)
                 == (models["challenger"].training_rows, models["challenger"].training_games),
                 "Paired models have different training counts")
        features = {name: [value] for name, value in normalized["features"].items()}
        logits = {name: float(model.predict_logits(features, [q])[0]) for name, model in models.items()}
        _require(all(math.isfinite(value) for value in logits.values()), "Prediction logit is nonfinite")
        reference = normalized["reference_quote"]
        result = {"schema_version": VERSION, "model_version": MODEL_VERSION,
            "game_id": normalized["game_id"], "phase": normalized["phase"],
            "group_kickoff": normalized["group_kickoff"], "kickoff": normalized["kickoff"],
            "recorded_at": _stamp(recorded_at), "cutoff": _stamp(cutoff),
            "available_at": available, "role_available_at": normalized["role_available_at"],
            "report_received_at": normalized["report_received_at"],
            "context_received_at": normalized["context_received_at"],
            "reference_line": reference["line"], "reference_quote_id": reference["quote_id"],
            "reference_sportsbook": reference["sportsbook"], "reference_quote": deepcopy(reference),
            "reference_observed_at": reference["observed_at"], "reference_requested_at": reference["requested_at"],
            "q_under": q, "reference_logit": logits["reference"], "challenger_logit": logits["challenger"],
            "reference_p_under": float(expit(logits["reference"])),
            "challenger_p_under": float(expit(logits["challenger"])),
            "observation_sha256": _digest(normalized),
            "offers_sha256": _offers_digest(observation["offers"]),
            "models_sha256": {name: _digest(model_to_dict(model)) for name, model in models.items()}}
        if "observation_id" in normalized:
            result["observation_id"] = normalized["observation_id"]
        result["prediction_id"] = _digest(result)
        return result
    except (KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise ValueError("Malformed prediction inputs") from exc


def paper_selection(observation, prediction, *, locked_at) -> dict | None:
    """One best strictly positive challenger EV at its exact modeled line.

    Duplicate main pairs exclude their book before line/price selection. A stale,
    malformed or wrong-line peer cannot invalidate a different unambiguous book.
    The caller enforces original receipt integrity and one entry per game.
    """
    try:
        _require(isinstance(prediction, Mapping) and prediction["schema_version"] == VERSION
                 and prediction["model_version"] == MODEL_VERSION, "Prediction version mismatch")
        original = {key: value for key, value in prediction.items() if key != "prediction_id"}
        _require(prediction["prediction_id"] == _digest(original), "Prediction content identity mismatch")
        normalized, available, q = _observation(observation, available_at=prediction["available_at"],
                                               recorded_at=prediction["recorded_at"])
        reference = normalized["reference_quote"]
        _require(_digest(normalized) == prediction["observation_sha256"]
                 and prediction["game_id"] == normalized["game_id"]
                 and prediction["phase"] == normalized["phase"]
                 and prediction["reference_line"] == reference["line"]
                 and prediction["reference_quote_id"] == reference["quote_id"]
                 and prediction["q_under"] == q, "Prediction does not describe original observation")
        _require(_offers_digest(observation["offers"]) == prediction["offers_sha256"],
                 "Original observed offers changed after inference")
        p = _number(prediction["challenger_p_under"], "Challenger probability")
        eta = _number(prediction["challenger_logit"], "Challenger logit")
        _require(0 <= p <= 1 and p == float(expit(eta)), "Challenger probability/logit mismatch")
        locked, recorded, kickoff = map(_time, (locked_at, prediction["recorded_at"], normalized["kickoff"]))
        _require(recorded <= locked < kickoff, "Paper lock must follow inference and precede kickoff")
        _require((locked - _time(reference["observed_at"])).total_seconds() <= MAX_AGE_SECONDS,
                 "Paper lock exceeded original reference quote age limit")
        offers = observation["offers"]
        _require(isinstance(offers, (list, tuple)), "Original observed offers required")
        by_book = {name: [] for name in BOOKS}
        for offer in offers:
            if not isinstance(offer, Mapping) or offer.get("sportsbook") not in BOOKS:
                continue
            try:
                if (_game_id(offer.get("game_id")) == normalized["game_id"]
                        and offer.get("market") == "Totals" and offer.get("period") == "full_game"):
                    by_book[offer["sportsbook"]].append(offer)
            except ValueError:
                continue
        candidates = []
        for book, items in by_book.items():
            if len(items) != 1:
                continue
            try:
                quote = _quote(items[0], normalized["game_id"])
                if quote["quote_id"] == reference["quote_id"] and quote != reference:
                    continue
                requested, observed = map(_time, (quote["requested_at"], quote["observed_at"]))
                if (quote["line"] != reference["line"]
                        or not _time(normalized["context_received_at"]) <= requested <= observed <= recorded
                        or (locked - observed).total_seconds() > MAX_AGE_SECONDS
                        or any(_time(time) >= requested for time in [*available.values(), *normalized["role_available_at"]])):
                    continue
                for side, probability in (("under", p), ("over", 1. - p)):
                    odds = quote[side + "_decimal_odds"]
                    ev = odds * probability - 1.
                    if ev > 0:
                        candidates.append({"quote": quote, "side": side, "probability": probability,
                                           "decimal_odds": odds, "estimated_ev": ev})
            except (KeyError, TypeError, AttributeError, ValueError, OverflowError):
                continue
        if not candidates:
            return None
        chosen = min(candidates, key=lambda row: (-row["estimated_ev"],
            0 if row["side"] == "under" else 1, BOOKS.index(row["quote"]["sportsbook"])))
        quote = chosen["quote"]
        result = {"schema_version": VERSION, "model_version": MODEL_VERSION,
            "prediction_id": prediction["prediction_id"], "game_id": normalized["game_id"],
            "phase": normalized["phase"], "group_kickoff": normalized["group_kickoff"],
            "kickoff": normalized["kickoff"], "locked_at": _stamp(locked), "side": chosen["side"],
            "sportsbook": quote["sportsbook"], "line": quote["line"], "decimal_odds": chosen["decimal_odds"],
            "estimated_probability": chosen["probability"], "estimated_ev": chosen["estimated_ev"],
            "units_risked": 1., "status": "pending", "quote_id": quote["quote_id"],
            "quote_requested_at": quote["requested_at"], "quote_observed_at": quote["observed_at"],
            "receipt_path": quote["receipt_path"], "body_sha256": quote["body_sha256"],
            "quote": deepcopy(quote), "reference_quote_id": reference["quote_id"],
            "reference_line": reference["line"], "q_under": q}
        if "observation_id" in normalized:
            result["observation_id"] = normalized["observation_id"]
        result["paper_id"] = _digest(result)
        return result
    except (KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise ValueError("Malformed paper decision inputs") from exc
