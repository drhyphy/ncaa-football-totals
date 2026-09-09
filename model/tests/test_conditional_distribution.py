import numpy as np
import pandas as pd
import pytest

from ncaaf_model.conditional_distribution import (
    CANDIDATES, ConditionalDistribution, market_probabilities, normal_pmf, select_candidate,
)


def synthetic_history(n=500):
    rng = np.random.default_rng(91283)
    total = rng.uniform(35, 75, n)
    score = np.round(np.clip(total + rng.normal(0, 5 + total / 5, n), 0, 180))
    return pd.DataFrame({"market_total":total, "actual_total":score,
                         "spread":rng.uniform(0, 35, n), "season":2023})


def test_every_family_produces_normalized_nonnegative_integer_mass():
    train = synthetic_history()
    fitted = ConditionalDistribution.fit(train)
    test = train.iloc[:12].copy().assign(season=2024)
    for candidate in CANDIDATES:
        pmf = fitted.predict_pmf(test, candidate)
        assert pmf.shape == (12, 251)
        assert np.all(pmf >= 0)
        np.testing.assert_allclose(pmf.sum(axis=1), 1, atol=1e-12)
        over, under, push = market_probabilities(pmf, test.market_total.to_numpy())
        np.testing.assert_allclose(over + under + push, 1, atol=1e-12)


def test_integer_push_mass_is_separate_from_sides_and_half_lines_do_not_push():
    pmf = normal_pmf(np.array([51.,51.]),np.array([15.,15.]))
    over, under, push = market_probabilities(pmf,np.array([51.,51.5]))
    assert push[0] > 0
    assert push[1] == 0
    assert over[1] == over[0]
    assert under[1] == pytest.approx(under[0] + push[0])


def test_future_outcomes_cannot_change_forecast():
    fitted = ConditionalDistribution.fit(synthetic_history())
    test = synthetic_history(10).assign(season=2024)
    for candidate in CANDIDATES:
        expected = fitted.predict_pmf(test, candidate)
        changed = fitted.predict_pmf(test.assign(actual_total=200), candidate)
        np.testing.assert_array_equal(expected, changed)


def test_same_or_earlier_training_seasons_are_rejected():
    train = synthetic_history()
    fitted = ConditionalDistribution.fit(train)
    for season in [2022,2023]:
        with pytest.raises(ValueError,match="later than every fitted season"):
            fitted.predict_pmf(train.iloc[:2].assign(season=season),"conditional_normal")


def test_candidate_selection_is_unaffected_by_2025_outcomes():
    rows = [{"candidate":candidate,"season":year,"log_loss":.7 + index*.01}
            for year in [2022,2023,2024,2025] for index,candidate in enumerate(CANDIDATES)]
    frame = pd.DataFrame(rows)
    assert select_candidate(frame) == "constant_normal"
    frame.loc[frame.season.eq(2025),"log_loss"] = [100.,0.,0.,0.]
    assert select_candidate(frame) == "constant_normal"


def test_insufficient_history_and_unknown_family_fail_explicitly():
    with pytest.raises(ValueError,match="100 prior games"):
        ConditionalDistribution.fit(synthetic_history(20))
    fitted = ConditionalDistribution.fit(synthetic_history())
    with pytest.raises(ValueError,match="Unknown candidate"):
        fitted.predict_pmf(synthetic_history(2).assign(season=2024),"searched_after_results")
