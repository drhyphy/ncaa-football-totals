"""Synthetic chronology/pricing/serialization contracts; no sources or fits."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import math

import numpy as np
import pytest
from scipy.special import expit

from ncaaf_model import availability_decisions as d
from ncaaf_model import availability_models as m


GAME = "9007199254740993"
NOW = datetime(2026, 9, 9, 13, tzinfo=timezone.utc)
KICKOFF = datetime(2026, 9, 12, 19, 30, tzinfo=timezone.utc)
CUTOFF = "2026-09-07T04:00:00Z"


def stamp(delta=0):
    return (NOW + timedelta(seconds=delta)).isoformat().replace("+00:00", "Z")


def model(challenger=False, intercept=0.):
    names = m.ALL_FEATURES if challenger else m.BASE_FEATURES
    return m.ProbabilityModel(m.Standardizer(names, np.zeros(len(names)), np.ones(len(names))),
        np.array([intercept] + [0.] * len(names)), 30, 24, 8)


def pair(intercept=.7):
    return {"cutoff": CUTOFF, "reference": d.model_to_dict(model()),
            "challenger": d.model_to_dict(model(True, intercept))}


def availability():
    return {"reference": stamp(-100), "challenger": stamp(-99)}


def quote(book="draftkings", *, line=50.5, over=2.1, under=1.8, request=0, observed=1):
    return {"game_id": GAME, "provider_event_id": "70001", "sportsbook": book,
        "market": "Totals", "period": "full_game", "line": line,
        "over_decimal_odds": over, "under_decimal_odds": under,
        "requested_at": stamp(request), "observed_at": stamp(observed),
        "market_updated_at": stamp(-5), "observation_kind": "provider_full_state",
        "source": "odds_api_io", "receipt_path": "data/runtime/availability_prices/receipt.json",
        "body_sha256": "b" * 64, "quote_id": book + "_original_quote"}


def observation():
    ref = quote()
    return {"observation_id": "original-designated-observation", "game_id": GAME,
        "phase": "initial", "group_kickoff": KICKOFF.isoformat(), "kickoff": KICKOFF.isoformat(),
        "report_received_at": stamp(-20), "context_received_at": stamp(-10),
        "features": {"market_total": 50.5,
            "log_hours_to_kickoff": math.log1p((KICKOFF - (NOW + timedelta(seconds=1))).total_seconds() / 3600),
            "market_total_change": -1., "phase_gameday": 0., "missing_previous_report": 0.,
            "missing_previous_quote": 0., "qb_out_burden": .7, "qb_out_burden_change": .2},
        "reference_quote": ref, "offers": [deepcopy(ref), quote("fanduel")],
        "role_available_at": [stamp(-60), stamp(-59)]}


def prediction(obs=None, *, target_p=None, artifacts=None, available=None, recorded=2):
    obs = observation() if obs is None else obs
    if artifacts is None:
        intercept = .7
        if target_p is not None:
            ref = obs["reference_quote"]
            q = (1 / ref["under_decimal_odds"]) / (1 / ref["under_decimal_odds"] + 1 / ref["over_decimal_odds"])
            intercept = float(np.log(target_p) - np.log1p(-target_p) - np.log(q) + np.log1p(-q))
        artifacts = pair(intercept)
    return d.make_prediction(obs, artifacts, available_at=availability() if available is None else available,
                             recorded_at=stamp(recorded))


def paper(obs=None, *, pred=None, target_p=None, locked=3):
    obs = observation() if obs is None else obs
    pred = prediction(obs, target_p=target_p) if pred is None else pred
    return d.paper_selection(obs, pred, locked_at=stamp(locked))


@pytest.mark.parametrize("challenger", [False, True])
def test_model_json_roundtrip_is_exact_and_arrays_independent_readonly(challenger):
    source = model(challenger, .2345678901234567)
    source.standardizer.mean[:] = np.linspace(-.3, .7, len(source.standardizer.names))
    source.standardizer.scale[:] = np.linspace(.1, 2., len(source.standardizer.names))
    serialized = d.model_to_dict(source)
    encoded = json.dumps(serialized, allow_nan=False)
    result = d.model_from_dict(json.loads(encoded))
    np.testing.assert_array_equal(result.coefficients, source.coefficients)
    np.testing.assert_array_equal(result.standardizer.mean, source.standardizer.mean)
    np.testing.assert_array_equal(result.standardizer.scale, source.standardizer.scale)
    assert result.standardizer.names == source.standardizer.names
    assert not result.coefficients.flags.writeable and not result.standardizer.mean.flags.writeable
    serialized["coefficients"][0] = 999.
    assert result.coefficients[0] == source.coefficients[0]


@pytest.mark.parametrize("mutation", ["version", "extra", "missing", "names_order", "names_unknown",
    "short_mean", "long_scale", "short_coefficients", "matrix", "nan", "inf", "zero_scale",
    "negative_scale", "boolean", "mixed_boolean", "string_coeff", "count_bool", "count_float",
    "count_zero", "more_games", "too_many_rows", "negative_iterations"])
def test_strict_artifact_schema(mutation):
    value = d.model_to_dict(model())
    if mutation == "version": value["version"] = "other-version"
    if mutation == "extra": value["extra"] = 1
    if mutation == "missing": del value["scale"]
    if mutation == "names_order": value["names"].reverse()
    if mutation == "names_unknown": value["names"][0] = "future_score"
    if mutation == "short_mean": value["mean"].pop()
    if mutation == "long_scale": value["scale"].append(1.)
    if mutation == "short_coefficients": value["coefficients"].pop()
    if mutation == "matrix": value["mean"] = [value["mean"]]
    if mutation == "nan": value["mean"][0] = float("nan")
    if mutation == "inf": value["coefficients"][0] = float("inf")
    if mutation == "zero_scale": value["scale"][0] = 0.
    if mutation == "negative_scale": value["scale"][0] = -1.
    if mutation == "boolean": value["coefficients"] = [True] * len(value["coefficients"])
    if mutation == "mixed_boolean": value["coefficients"][0] = True
    if mutation == "string_coeff": value["coefficients"][0] = "0.2"
    if mutation == "count_bool": value["training_rows"] = True
    if mutation == "count_float": value["training_games"] = 24.
    if mutation == "count_zero": value["training_games"] = 0
    if mutation == "more_games": value["training_games"] = 31
    if mutation == "too_many_rows": value["training_rows"] = 49
    if mutation == "negative_iterations": value["optimizer_iterations"] = -1
    with pytest.raises(ValueError):
        d.model_from_dict(value)


def test_serializer_does_not_invent_a_training_eligibility_gate():
    tiny = model()
    tiny = m.ProbabilityModel(tiny.standardizer, tiny.coefficients, 1, 1, 0)
    assert d.model_from_dict(d.model_to_dict(tiny)).training_games == 1
    with pytest.raises(ValueError):
        d.model_to_dict({})


def test_prediction_under_orientation_matches_independent_offset_math():
    obs, artifacts, available = observation(), pair(), availability()
    original = deepcopy((obs, artifacts, available))
    result = prediction(obs, artifacts=artifacts, available=available)
    expected_q = (1 / 1.8) / (1 / 1.8 + 1 / 2.1)
    assert expected_q > .5
    assert result["q_under"] == expected_q
    assert result["reference_logit"] == pytest.approx(math.log(expected_q / (1 - expected_q)))
    assert result["challenger_logit"] == pytest.approx(result["reference_logit"] + .7)
    assert result["reference_p_under"] == pytest.approx(expected_q)
    assert result["challenger_p_under"] == expit(result["challenger_logit"])
    assert result["game_id"] == GAME and result["observation_id"] == obs["observation_id"]
    assert result["recorded_at"] == stamp(2) and result["reference_observed_at"] == stamp(1)
    assert len(result["prediction_id"]) == 64
    assert (obs, artifacts, available) == original


def test_actual_transform_and_coefficients_are_used_without_refitting():
    obs, artifacts = observation(), pair()
    for name, artifact in [("reference", artifacts["reference"]), ("challenger", artifacts["challenger"])]:
        n = len(artifact["names"])
        artifact["mean"] = list(np.arange(n) / 7)
        artifact["scale"] = list(np.arange(n) + 1.)
        artifact["coefficients"] = list(np.arange(n + 1) / 9)
    result = prediction(obs, artifacts=artifacts)
    for name in ("reference", "challenger"):
        artifact = artifacts[name]
        transformed = [max(-5., min(5., (obs["features"][feature] - mean) / scale))
                       for feature, mean, scale in zip(artifact["names"], artifact["mean"], artifact["scale"])]
        expected = math.log(result["q_under"]) - math.log1p(-result["q_under"]) + artifact["coefficients"][0]
        expected += sum(x * coef for x, coef in zip(transformed, artifact["coefficients"][1:]))
        assert result[name + "_logit"] == pytest.approx(expected, abs=2e-14)


@pytest.mark.parametrize("mutation", ["game_float", "game_bool", "game_leading_zero", "wrong_quote_game",
    "phase_raw", "phase_feature", "market_feature", "time_feature", "extra_feature", "missing_feature",
    "array_feature", "nan_feature", "unknown_previous_quote", "unknown_previous_report", "bad_burden",
    "bad_previous_burden", "reference_book", "integer_line", "zero_line", "str_price", "bool_price",
    "one_price", "nan_price", "market_period", "missing_quote_id"])
def test_prediction_rejects_wrong_context_prices_and_features(mutation):
    obs = observation()
    if mutation == "game_float": obs["game_id"] = 123.
    if mutation == "game_bool": obs["game_id"] = True
    if mutation == "game_leading_zero": obs["game_id"] = "0123"
    if mutation == "wrong_quote_game": obs["reference_quote"]["game_id"] = "123"
    if mutation == "phase_raw": obs["phase"] = "Initial"
    if mutation == "phase_feature": obs["features"]["phase_gameday"] = 1.
    if mutation == "market_feature": obs["features"]["market_total"] = 51.5
    if mutation == "time_feature": obs["features"]["log_hours_to_kickoff"] += 1e-10
    if mutation == "extra_feature": obs["features"]["future_score"] = 1
    if mutation == "missing_feature": del obs["features"]["qb_out_burden"]
    if mutation == "array_feature": obs["features"]["qb_out_burden"] = [.7]
    if mutation == "nan_feature": obs["features"]["qb_out_burden"] = float("nan")
    if mutation == "unknown_previous_quote": obs["features"]["missing_previous_quote"] = 1.
    if mutation == "unknown_previous_report": obs["features"]["missing_previous_report"] = 1.
    if mutation == "bad_burden": obs["features"]["qb_out_burden"] = 2.1
    if mutation == "bad_previous_burden": obs["features"]["qb_out_burden_change"] = 1.9
    if mutation == "reference_book": obs["reference_quote"]["sportsbook"] = "unknown"
    if mutation == "integer_line": obs["reference_quote"]["line"] = 50.
    if mutation == "zero_line": obs["reference_quote"]["line"] = 0.
    if mutation == "str_price": obs["reference_quote"]["under_decimal_odds"] = "1.8"
    if mutation == "bool_price": obs["reference_quote"]["under_decimal_odds"] = True
    if mutation == "one_price": obs["reference_quote"]["over_decimal_odds"] = 1.
    if mutation == "nan_price": obs["reference_quote"]["over_decimal_odds"] = float("nan")
    if mutation == "market_period": obs["reference_quote"]["period"] = "first_half"
    if mutation == "missing_quote_id": del obs["reference_quote"]["quote_id"]
    with pytest.raises(ValueError):
        prediction(obs)


def test_gameday_anchor_reschedule_and_no_irrelevant_roles_are_allowed():
    obs = observation()
    obs.update(phase="gameday", group_kickoff="2026-09-01T00:00Z", role_available_at=[])
    obs["features"]["phase_gameday"] = True
    result = prediction(obs)
    assert result["phase"] == "gameday"
    assert result["group_kickoff"] == "2026-09-01T00:00:00Z"


@pytest.mark.parametrize("mutation", ["report_after_context", "context_after_request", "receipt_before_request",
    "role_equal_request", "role_after_request", "reference_equal_request", "challenger_equal_request",
    "availability_missing", "availability_before_cutoff", "naive_report", "naive_recorded",
    "wrong_cutoff_day", "wrong_cutoff_hour", "older_cutoff", "swapped_models", "unpaired_training_counts"])
def test_all_information_must_be_available_in_order(mutation):
    obs, available, artifacts = observation(), availability(), pair()
    recorded = stamp(2)
    if mutation == "report_after_context": obs["report_received_at"] = stamp(-9)
    if mutation == "context_after_request": obs["context_received_at"] = stamp(.1)
    if mutation == "receipt_before_request": obs["reference_quote"]["observed_at"] = stamp(-1)
    if mutation == "role_equal_request": obs["role_available_at"] = [stamp(0)]
    if mutation == "role_after_request": obs["role_available_at"] = [stamp(1)]
    if mutation == "reference_equal_request": available["reference"] = stamp(0)
    if mutation == "challenger_equal_request": available["challenger"] = stamp(0)
    if mutation == "availability_missing": del available["challenger"]
    if mutation == "availability_before_cutoff": available["reference"] = "2026-09-07T03:59:59Z"
    if mutation == "naive_report": obs["report_received_at"] = "2026-09-09T12:59:40"
    if mutation == "naive_recorded": recorded = "2026-09-09T13:00:02"
    if mutation == "wrong_cutoff_day": artifacts["cutoff"] = "2026-09-08T04:00Z"
    if mutation == "wrong_cutoff_hour": artifacts["cutoff"] = "2026-09-07T05:00Z"
    if mutation == "older_cutoff": artifacts["cutoff"] = "2026-08-31T04:00Z"
    if mutation == "swapped_models": artifacts["reference"], artifacts["challenger"] = artifacts["challenger"], artifacts["reference"]
    if mutation == "unpaired_training_counts": artifacts["challenger"]["training_rows"] += 1
    with pytest.raises(ValueError):
        d.make_prediction(obs, artifacts, available_at=available, recorded_at=recorded)


def test_prediction_exact_120_seconds_valid_later_or_before_receipt_invalid():
    assert prediction(recorded=121)["recorded_at"] == stamp(121)
    with pytest.raises(ValueError, match="age"):
        prediction(recorded=121.000001)
    with pytest.raises(ValueError):
        prediction(recorded=.999999)


def test_prediction_cannot_be_at_kickoff_and_cutoff_is_dst_aware():
    obs = observation()
    obs["kickoff"] = stamp(2)
    obs["features"]["log_hours_to_kickoff"] = math.log1p(1/3600)
    with pytest.raises(ValueError):
        prediction(obs)
    # The same autumn Monday midnight is accepted via its explicit local offset.
    artifacts = pair()
    artifacts["cutoff"] = "2026-09-07T00:00:00-04:00"
    assert prediction(artifacts=artifacts)["cutoff"] == CUTOFF


def test_one_book_suffices_and_challenger_only_controls_paper():
    obs = observation()
    obs["offers"] = [obs["offers"][0]]
    pred = prediction(obs, target_p=.7)
    selected = paper(obs, pred=pred)
    assert selected["side"] == "under" and selected["sportsbook"] == "draftkings"
    assert selected["decimal_odds"] == 1.8
    assert selected["estimated_ev"] == pytest.approx(1.8 * .7 - 1)
    assert selected["units_risked"] == 1 and selected["status"] == "pending"
    assert selected["quote_id"] == obs["offers"][0]["quote_id"]
    assert selected["prediction_id"] == pred["prediction_id"]


def test_wrong_line_peer_is_not_allowed_to_borrow_probability():
    obs = observation()
    obs["offers"][1].update(line=49.5, under_decimal_odds=9.)
    selected = paper(obs, target_p=.7)
    assert selected["sportsbook"] == "draftkings" and selected["line"] == 50.5


@pytest.mark.parametrize("different_line", [False, True])
def test_duplicate_main_book_excluded_but_other_book_survives(different_line):
    obs = observation()
    duplicate = deepcopy(obs["offers"][0])
    if different_line: duplicate["line"] = 51.5
    obs["offers"].append(duplicate)
    assert paper(obs, target_p=.7)["sportsbook"] == "fanduel"


def test_both_books_ambiguous_or_no_offers_produces_none():
    obs = observation()
    obs["offers"] *= 2
    assert paper(obs) is None
    obs["offers"] = []
    assert paper(obs) is None


def test_maximum_ev_and_fixed_side_then_book_ties():
    obs = observation()
    for q in [obs["reference_quote"], *obs["offers"]]:
        q.update(over_decimal_odds=2.2, under_decimal_odds=2.2)
    selected = paper(obs, target_p=.5)
    assert (selected["side"], selected["sportsbook"]) == ("under", "draftkings")
    # Under-at-FD wins a side tie over Over-at-DK despite the book preference.
    obs["offers"][0]["under_decimal_odds"] = 2.
    obs["offers"][1]["over_decimal_odds"] = 2.
    obs["reference_quote"] = deepcopy(obs["offers"][0])
    selected = paper(obs, target_p=.5)
    assert (selected["side"], selected["sportsbook"]) == ("under", "fanduel")
    obs["offers"][0]["over_decimal_odds"] = 2.20001
    obs["reference_quote"] = deepcopy(obs["offers"][0])
    selected = paper(obs, target_p=.5)
    assert (selected["side"], selected["sportsbook"]) == ("over", "draftkings")


def test_exact_zero_ev_is_not_selected_but_strict_positive_is():
    obs = observation()
    for q in [obs["reference_quote"], *obs["offers"]]:
        q.update(over_decimal_odds=2., under_decimal_odds=2.)
    assert paper(obs, target_p=.5) is None
    obs["offers"][1]["under_decimal_odds"] = 2.000000000000001
    assert paper(obs, target_p=.5)["sportsbook"] == "fanduel"


@pytest.mark.parametrize("mutation", ["bad_price", "unknown_book", "integer_line", "wrong_game",
    "wrong_period", "missing_id", "future_request", "after_inference", "before_context", "before_role"])
def test_invalid_peer_skipped_without_both_book_gate(mutation):
    obs = observation()
    offer = obs["offers"][1]
    if mutation == "bad_price": offer["under_decimal_odds"] = float("nan")
    if mutation == "unknown_book": offer["sportsbook"] = "unknown"
    if mutation == "integer_line": offer["line"] = 50.
    if mutation == "wrong_game": offer["game_id"] = "123"
    if mutation == "wrong_period": offer["period"] = "first_half"
    if mutation == "missing_id": del offer["quote_id"]
    if mutation == "future_request": offer["requested_at"] = stamp(10)
    if mutation == "after_inference": offer.update(requested_at=stamp(2.1), observed_at=stamp(2.5))
    if mutation == "before_context": offer.update(requested_at=stamp(-15), observed_at=stamp(1))
    if mutation == "before_role":
        obs["role_available_at"] = [stamp(-2)]
        offer.update(requested_at=stamp(-3), observed_at=stamp(1))
    assert paper(obs, target_p=.7)["sportsbook"] == "draftkings"


def test_peer_request_can_precede_reference_if_all_dependencies_already_available():
    obs = observation()
    obs["offers"][1].update(requested_at=stamp(-1), observed_at=stamp(.5), under_decimal_odds=1.99)
    assert paper(obs, target_p=.7)["sportsbook"] == "fanduel"


def test_reference_and_selected_offer_exact_120_age_boundary():
    obs = observation()
    pred = prediction(obs)
    assert paper(obs, pred=pred, locked=121) is not None
    with pytest.raises(ValueError, match="reference quote age"):
        paper(obs, pred=pred, locked=121.000001)
    obs["offers"][1].update(requested_at=stamp(-1), observed_at=stamp(0))
    obs["offers"][0]["under_decimal_odds"] = 1.000001
    obs["offers"][0]["over_decimal_odds"] = 1.000001
    obs["reference_quote"] = deepcopy(obs["offers"][0])
    pred = prediction(obs)
    assert paper(obs, pred=pred, locked=120) is not None
    assert paper(obs, pred=pred, locked=120.000001) is None


@pytest.mark.parametrize("mutation", ["game", "phase", "anchor", "kickoff", "feature", "quote_id", "quote_price",
    "role_time", "prediction_p", "prediction_line", "prediction_id", "prediction_version"])
def test_lock_checks_original_observation_and_prediction_content(mutation):
    obs = observation()
    pred = prediction(obs)
    if mutation == "game": obs["game_id"] = "123"
    if mutation == "phase": obs["phase"] = "gameday"; obs["features"]["phase_gameday"] = 1.
    if mutation == "anchor": obs["group_kickoff"] = "2026-09-13T19:30Z"
    if mutation == "kickoff": obs["kickoff"] = "2026-09-12T20:30Z"
    if mutation == "feature": obs["features"]["qb_out_burden"] = .8
    if mutation == "quote_id": obs["reference_quote"]["quote_id"] = "changed"
    if mutation == "quote_price": obs["reference_quote"]["under_decimal_odds"] = 1.9
    if mutation == "role_time": obs["role_available_at"][0] = stamp(-58)
    if mutation == "prediction_p": pred["challenger_p_under"] = .999
    if mutation == "prediction_line": pred["reference_line"] = 51.5
    if mutation == "prediction_id": pred["prediction_id"] = "changed"
    if mutation == "prediction_version": pred["schema_version"] = "wrong"
    with pytest.raises(ValueError):
        paper(obs, pred=pred)


def test_paper_lock_before_inference_or_at_kickoff_refused_and_inputs_unchanged():
    obs = observation()
    pred = prediction(obs)
    original = deepcopy((obs, pred))
    paper(obs, pred=pred)
    assert (obs, pred) == original
    with pytest.raises(ValueError):
        paper(obs, pred=pred, locked=1.99)
    with pytest.raises(ValueError):
        d.paper_selection(obs, pred, locked_at=KICKOFF.isoformat())


@pytest.mark.parametrize("mutation", ["peer_price", "peer_receipt", "peer_id", "remove", "append", "reorder", "repair_invalid"])
def test_original_offer_fingerprint_rejects_post_inference_changes(mutation):
    obs = observation()
    if mutation == "repair_invalid": obs["offers"][1]["under_decimal_odds"] = float("nan")
    pred = prediction(obs)
    if mutation == "peer_price": obs["offers"][1]["under_decimal_odds"] = 1.9
    if mutation == "peer_receipt": obs["offers"][1]["observed_at"] = stamp(1.1)
    if mutation == "peer_id": obs["offers"][1]["quote_id"] = "changed"
    if mutation == "remove": obs["offers"].pop()
    if mutation == "append": obs["offers"].append(deepcopy(obs["offers"][1]))
    if mutation == "reorder": obs["offers"].reverse()
    if mutation == "repair_invalid": obs["offers"][1]["under_decimal_odds"] = 1.9
    with pytest.raises(ValueError, match="offers changed"):
        paper(obs, pred=pred)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), None, "not numeric", {}, []])
def test_nonfinite_and_json_malformed_peers_fingerprinted_but_skipped(bad):
    obs = observation()
    obs["offers"][1]["under_decimal_odds"] = bad
    pred = prediction(obs)
    assert len(pred["offers_sha256"]) == 64
    json.dumps(pred, allow_nan=False)
    selected = paper(obs, pred=pred)
    assert selected["sportsbook"] == "draftkings"


def test_fingerprint_does_not_execute_or_repr_arbitrary_python_objects():
    class Untrusted:
        def __repr__(self):
            raise AssertionError("Must not use repr")

        def __str__(self):
            raise AssertionError("Must not use str")

    obs = observation()
    obs["offers"][1]["under_decimal_odds"] = Untrusted()
    with pytest.raises(ValueError, match="JSON-shaped"):
        prediction(obs)


def test_same_quote_id_cannot_silently_mean_different_original_prices():
    obs = observation()
    obs["offers"][0]["under_decimal_odds"] = 99.
    assert paper(obs, target_p=.7)["sportsbook"] == "fanduel"


def test_current_artifact_week_changes_exactly_at_eastern_monday_midnight():
    for recorded, cutoff in [("2026-09-14T03:59:59.999999Z", "2026-09-07T04:00:00Z"),
                             ("2026-09-14T04:00:01Z", "2026-09-14T04:00:00Z")]:
        obs = observation()
        now = datetime.fromisoformat(recorded.replace("Z", "+00:00"))
        kickoff = now + timedelta(days=2)
        quote_time = now - timedelta(microseconds=1)
        obs.update(kickoff=kickoff.isoformat(), group_kickoff=kickoff.isoformat(),
            report_received_at=now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
            context_received_at=(quote_time-timedelta(microseconds=1)).isoformat(), role_available_at=[])
        obs["reference_quote"].update(requested_at=quote_time.isoformat(), observed_at=quote_time.isoformat())
        obs["features"]["log_hours_to_kickoff"] = math.log1p((kickoff-quote_time).total_seconds()/3600)
        artifacts = pair()
        artifacts["cutoff"] = cutoff
        available = {name: cutoff for name in ("reference", "challenger")}
        result = d.make_prediction(obs, artifacts, available_at=available, recorded_at=recorded)
        assert result["cutoff"] == cutoff
        if cutoff == "2026-09-14T04:00:00Z":
            artifacts["cutoff"] = "2026-09-07T04:00:00Z"
            with pytest.raises(ValueError, match="Current Eastern week's"):
                d.make_prediction(obs, artifacts, available_at=available, recorded_at=recorded)
