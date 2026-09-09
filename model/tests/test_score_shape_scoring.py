"""Synthetic-only proper-score, push, identity and cluster-ratio checks."""
import numpy as np
import pandas as pd
import pytest

from ncaaf_model import score_shape_scoring as scoring


def games(actual=(50, 51, 49), lines=(50., 50.5, 50.)):
    size = len(actual)
    return pd.DataFrame({
        "game_id": np.arange(size, dtype=np.int64) + 9007199254741049,
        "season": [2024] * size,
        "game_date": [pd.Timestamp("2024-09-07T18:00:00Z") + pd.Timedelta(days=7*i) for i in range(size)],
        "market_source": ["synthetic_only"] * size,
        "actual_total": actual, "market_total": lines, "week": np.arange(size) + 1,
        "ratings_cutoff": [pd.Timestamp("2024-09-02T04:00:00Z")] * size,
        "ignored_future_outcome": ["never used"] * size,
    })


def pmf(size):
    return np.full((size, 251), 1 / 251.)


def under_mass(value, line=30.5):
    lower = scoring.SUPPORT < line
    return np.where(lower, value/lower.sum(), (1-value)/(~lower).sum())


def test_integer_push_and_halfpoint_probabilities_scores_and_copy_contract():
    original = games()
    before = original.copy(deep=True)
    probability = pmf(3)
    before_p = probability.copy()
    result = scoring.score_pmf(original, probability)
    pd.testing.assert_frame_equal(original, before)
    np.testing.assert_array_equal(probability, before_p)
    assert result.game_id.tolist() == before.game_id.tolist()
    assert result.ratings_cutoff.equals(before.ratings_cutoff)
    assert result.week.equals(before.week)
    assert "ignored_future_outcome" not in result
    assert tuple(scoring.METRICS) == ("three_outcome_nll", "conditional_log_loss", "conditional_brier",
                                     "exact_score_nll", "discrete_crps", "absolute_mean_error")
    np.testing.assert_allclose(result.under_probability, [50/251, 51/251, 50/251])
    np.testing.assert_allclose(result.over_probability, [200/251, 200/251, 200/251])
    np.testing.assert_allclose(result.push_probability, [1/251, 0, 1/251])
    np.testing.assert_allclose(result[list(scoring.PROBABILITY_COLUMNS[:3])].sum(axis=1), 1.)
    assert result.loc[0, "three_outcome_nll"] == pytest.approx(np.log(251.))
    assert np.isnan(result.loc[0, "conditional_log_loss"])
    assert np.isnan(result.loc[0, "conditional_brier"])
    assert result.loc[1, "three_outcome_nll"] == pytest.approx(result.loc[1, "conditional_log_loss"])
    assert result.loc[2, "conditional_log_loss"] == pytest.approx(-np.log(50/250.))
    assert result.loc[2, "conditional_brier"] == pytest.approx((200/250.)**2)
    result.loc[0, "market_source"] = "mutated_copy"
    assert original.loc[0, "market_source"] == "synthetic_only"


def test_discrete_crps_matches_independent_pairwise_absolute_distance_identity():
    rows = games(actual=(0, 50, 250), lines=(50., 50.5, 250.))
    random = np.random.default_rng(1729)
    probability = random.uniform(.01, 1., (3, 251))
    probability /= probability.sum(axis=1, keepdims=True)
    result = scoring.score_pmf(rows, probability)
    pair_distance = abs(scoring.SUPPORT[:, None] - scoring.SUPPORT[None, :])
    for i, actual in enumerate(rows.actual_total):
        weights = probability[i]
        expected = weights @ abs(scoring.SUPPORT - actual) - .5 * weights @ pair_distance @ weights
        assert result.loc[i, "discrete_crps"] == pytest.approx(expected, abs=1e-12)
        mean = weights @ scoring.SUPPORT
        assert result.loc[i, "mean"] == pytest.approx(mean)
        assert result.loc[i, "variance"] == pytest.approx(weights @ ((scoring.SUPPORT-mean)**2))
        assert result.loc[i, "absolute_mean_error"] == pytest.approx(abs(mean-actual))
        assert result.loc[i, "exact_score_nll"] == pytest.approx(-np.log(weights[actual]))


def test_extreme_positive_tail_keeps_stable_conditional_log_loss_without_clipping():
    rows = games(actual=(50, 0), lines=(50., 1.5))
    probability = np.full((2, 251), 1e-300)
    probability[0, 50] = 1.
    probability[1, 250] = 1.
    result = scoring.score_pmf(rows, probability)
    assert result.loc[0, "conditional_over"] == pytest.approx(200/250.)
    assert np.isnan(result.loc[0, "conditional_log_loss"])
    assert result.loc[1, "conditional_over"] == 1.  # rounded ratio cannot recover its tiny Under tail
    assert result.loc[1, "conditional_log_loss"] == pytest.approx(-np.log(2e-300))
    assert np.isfinite(result.loc[1, "three_outcome_nll"])
    assert result.loc[1, "exact_score_nll"] > 600


@pytest.mark.parametrize("fault", ["zero", "negative", "nan", "infinite", "sum", "bins", "rows", "strings", "complex", "boolean"])
def test_invalid_pmfs_are_rejected_without_repair(fault):
    probability = pmf(3)
    if fault == "zero": probability[0, 0] = 0
    elif fault == "negative": probability[0, 0] = -.1
    elif fault == "nan": probability[0, 0] = np.nan
    elif fault == "infinite": probability[0, 0] = np.inf
    elif fault == "sum": probability[0] *= 1 + 2e-12
    elif fault == "bins": probability = probability[:, :-1]
    elif fault == "rows": probability = probability[:2]
    elif fault == "strings": probability = probability.astype(str)
    elif fault == "complex": probability = probability.astype(complex)
    elif fault == "boolean": probability = probability.astype(bool)
    with pytest.raises(ValueError): scoring.score_pmf(games(), probability)


@pytest.mark.parametrize("field,value", [
    ("actual_total", -.5), ("actual_total", 251), ("actual_total", 49.5), ("actual_total", np.nan),
    ("actual_total", "50"), ("market_total", 0), ("market_total", -1), ("market_total", 50.25),
    ("market_total", 50.5 + 1e-12), ("market_total", np.inf), ("market_total", True),
    ("game_date", "2024-09-07"), ("game_date", pd.NaT), ("season", 2024.5),
    ("market_source", None), ("ratings_cutoff", None),
])
def test_invalid_reference_outcome_and_temporal_identity_inputs_fail(field, value):
    rows = games().astype({field: object})
    rows.loc[0, field] = value
    with pytest.raises(ValueError): scoring.score_pmf(rows, pmf(3))


def test_support_boundary_halfpoint_above_support_and_exact_identity_validation():
    rows = games(actual=(250, 0), lines=(250.5, .5))
    result = scoring.score_pmf(rows, pmf(2))
    assert result.loc[0, "over_probability"] == 0
    assert result.loc[0, "conditional_over"] == 0
    assert result.loc[0, "push_probability"] == 0
    assert result.loc[0, "three_outcome_nll"] == pytest.approx(0, abs=1e-12)
    for fault in ("duplicate_id", "float_id", "duplicate_column", "missing_column"):
        invalid = rows.copy()
        if fault == "duplicate_id": invalid.loc[1, "game_id"] = invalid.loc[0, "game_id"]
        elif fault == "float_id": invalid["game_id"] = invalid.game_id.astype(float)
        elif fault == "duplicate_column": invalid = pd.concat([invalid, invalid[["actual_total"]]], axis=1)
        else: invalid = invalid.drop(columns="game_date")
        with pytest.raises(ValueError): scoring.score_pmf(invalid, pmf(2))


def test_dataframe_pmfs_have_strict_row_and_score_axis_alignment():
    rows = games()
    probability = pd.DataFrame(pmf(3), index=rows.index, columns=range(251))
    expected = scoring.score_pmf(rows, probability.to_numpy())
    pd.testing.assert_frame_equal(scoring.score_pmf(rows, probability), expected)
    with pytest.raises(ValueError, match="index/support"):
        scoring.score_pmf(rows, probability.iloc[::-1])
    with pytest.raises(ValueError, match="index/support"):
        scoring.score_pmf(rows, probability.iloc[:, ::-1])
    with pytest.raises(ValueError, match="index/support"):
        scoring.score_pmf(rows, probability.set_axis(np.arange(251, dtype=float), axis=1))


def test_summary_retains_push_denominator_and_fixed_reliability_bins():
    rows = games(actual=(50, 20, 40, 20, 40), lines=(50., 30.5, 30.5, 30.5, 30.5))
    probability = np.vstack([pmf(1)[0], *[under_mass(v) for v in [.8, .6, .5, .1]]])
    scored = scoring.score_pmf(rows, probability)
    summary = scoring.summary_metrics(scored)
    assert summary["games"] == 5
    assert summary["posted_integer_lines"] == 1
    assert summary["observed_pushes"] == 1
    assert summary["predicted_pushes"] == pytest.approx(1/251.)
    assert summary["metric_games"]["three_outcome_nll"] == 5
    assert summary["metric_games"]["conditional_log_loss"] == 4
    assert summary["metrics"]["conditional_brier"] == pytest.approx(np.nanmean(scored.conditional_brier))
    bins = summary["reliability"]
    assert [(b["lower"], b["upper"]) for b in bins] == list(zip(scoring.RELIABILITY_EDGES[:-1], scoring.RELIABILITY_EDGES[1:]))
    assert sum(b["games"] for b in bins) == 4
    assert all(b["predicted_over"] is None and b["observed_over"] is None for b in bins if b["games"] == 0)
    assert bins[-1]["games"] == 1 and bins[-1]["observed_over"] == 1


def test_reliability_boundary_inclusion_and_extreme_zero_one_probabilities():
    rows = games(actual=(0, 0, 0, 0, 0, 0, 0), lines=(1.,) * 7)
    scored = scoring.score_pmf(rows, pmf(7))
    # A synthetic scored-row fixture isolates deterministic bin boundary rules.
    # The three masses stay coherent, and all rows are observed nonpush games.
    scored["conditional_over"] = [0., .4, .45, .5, .55, .6, 1.]
    scored["push_probability"] = .01
    scored["over_probability"] = scored.conditional_over * .99
    scored["under_probability"] = (1 - scored.conditional_over) * .99
    counts = [row["games"] for row in scoring.summary_metrics(scored)["reliability"]]
    assert counts == [1, 1, 1, 1, 1, 2]


def test_all_pushes_and_empty_cohorts_never_become_zero_loss_or_confidence():
    scored = scoring.score_pmf(games(actual=(50, 50), lines=(50., 50.)), pmf(2))
    summary = scoring.summary_metrics(scored)
    assert summary["observed_pushes"] == 2
    assert summary["metrics"]["conditional_log_loss"] is None
    assert summary["metric_games"]["conditional_log_loss"] == 0
    pair = scoring.paired_summary(scored, scored)
    assert pair["metrics"]["conditional_log_loss"] == {"games": 0, "week_blocks": 0, "difference": None, "interval_95": None}
    assert pair["metrics"]["three_outcome_nll"]["games"] == 2
    empty = scoring.score_pmf(games(actual=(), lines=()), np.empty((0, 251)))
    result = scoring.summary_metrics(empty)
    assert result["games"] == 0 and all(value is None for value in result["metrics"].values())
    assert scoring.paired_summary(empty, empty)["metrics"]["three_outcome_nll"]["interval_99"] is None


def test_exact_pair_alignment_accepts_reordered_reference_and_rejects_mismatched_identity():
    scored = scoring.score_pmf(games(), pmf(3))
    expected = scoring.paired_summary(scored, scored)
    assert scoring.paired_summary(scored, scored.iloc[::-1]) == expected
    for fault in ("missing_id", "different_id", "duplicate_id", "actual_total", "market_total", "season", "game_date", "market_source", "week", "ratings_cutoff", "missing_cutoff"):
        reference = scored.copy(deep=True)
        if fault == "missing_id": reference = reference.iloc[1:]
        elif fault == "different_id": reference.loc[0, "game_id"] += 100
        elif fault == "duplicate_id": reference.loc[1, "game_id"] = reference.loc[0, "game_id"]
        elif fault == "missing_cutoff": reference = reference.drop(columns="ratings_cutoff")
        elif fault == "actual_total": reference.loc[1, fault] = 52  # remains a nonpush Over
        elif fault == "market_total": reference.loc[1, fault] = 51.5
        elif fault in ("season", "week"): reference.loc[1, fault] += 1
        elif fault in ("game_date", "ratings_cutoff"): reference.loc[1, fault] += pd.Timedelta(hours=1)
        else: reference.loc[1, fault] = "other_source"
        with pytest.raises(ValueError): scoring.paired_summary(scored, reference)


def test_conditional_missingness_cannot_silently_remove_games_from_pairs():
    scored = scoring.score_pmf(games(), pmf(3))
    for index, value in [(1, np.nan), (0, 0.)]:
        changed = scored.copy()
        changed.loc[index, "conditional_log_loss"] = value
        with pytest.raises(ValueError, match="Conditional metrics"):
            scoring.paired_summary(changed, scored)
    changed = scored.copy()
    changed.loc[0, "three_outcome_nll"] = np.nan
    with pytest.raises(ValueError, match="Unconditional metrics"):
        scoring.summary_metrics(changed)


def test_eastern_week_boundary_and_timezone_equivalent_game_dates():
    rows = games(actual=(20, 20), lines=(30.5, 30.5))
    rows["game_date"] = ["2024-09-09T03:59:59Z", "2024-09-09T04:00:00Z"]
    scored = scoring.score_pmf(rows, pmf(2))
    pair = scoring.paired_summary(scored, scored)
    assert pair["metrics"]["three_outcome_nll"]["week_blocks"] == 2
    same = scored.copy()
    same["game_date"] = ["2024-09-08T23:59:59-04:00", "2024-09-09T00:00:00-04:00"]
    assert scoring.paired_summary(scored, same) == pair
    # Winter Monday starts at 05:00 UTC, not the summer 04:00 boundary.
    rows["game_date"] = ["2024-12-09T04:59:59Z", "2024-12-09T05:00:00Z"]
    winter = scoring.score_pmf(rows, pmf(2))
    assert scoring.paired_summary(winter, winter)["metrics"]["three_outcome_nll"]["week_blocks"] == 2
    rows["game_date"] = ["2024-09-07T12:00:00Z", "2024-09-08T12:00:00Z"]
    same_week = scoring.score_pmf(rows, pmf(2))
    assert all(entry["interval_95"] is None for entry in scoring.paired_summary(same_week, same_week)["metrics"].values())


def test_paired_bootstrap_resamples_loss_sums_over_counts_not_average_week_losses():
    counts = np.array([1, 2, 7, 11])
    effects = np.array([2., -1., .5, .2])
    labels = np.repeat(np.arange(4), counts)
    delta = effects[labels]
    rows = games(actual=(20,) * len(labels), lines=(30.5,) * len(labels))
    rows["game_date"] = [pd.Timestamp("2024-09-07T18:00:00Z") + pd.Timedelta(days=7*int(label)) for label in labels]
    reference = scoring.score_pmf(rows, np.tile(under_mass(.1), (len(rows), 1)))
    candidate = scoring.score_pmf(rows, np.vstack([under_mass(.1*np.exp(-value)) for value in delta]))
    result = scoring.paired_summary(candidate, reference)
    primary = result["metrics"]["three_outcome_nll"]
    assert result["games"] == counts.sum() and result["draws"] == 10000 and result["seed"] == 20260909
    assert primary["difference"] == pytest.approx(np.average(effects, weights=counts), abs=1e-12)
    assert primary["difference"] != pytest.approx(effects.mean())
    random = np.random.Generator(np.random.PCG64(20260909))
    sampled = random.integers(0, 4, (10000, 4))
    ratios = (counts*effects)[sampled].sum(axis=1) / counts[sampled].sum(axis=1)
    wrong = effects[sampled].mean(axis=1)
    assert primary["interval_95"] == pytest.approx(np.quantile(ratios, [.025, .975]), abs=1e-12)
    assert primary["interval_99"] == pytest.approx(np.quantile(ratios, [.005, .995]), abs=1e-12)
    assert not np.allclose(primary["interval_95"], np.quantile(wrong, [.025, .975]))
    assert all("interval_99" not in result["metrics"][name] for name in scoring.METRICS[1:])
    assert scoring.paired_summary(candidate, reference) == result
    reverse = scoring.paired_summary(reference, candidate)["metrics"]["three_outcome_nll"]
    assert reverse["difference"] == pytest.approx(-primary["difference"])
    assert reverse["interval_95"] == pytest.approx([-primary["interval_95"][1], -primary["interval_95"][0]])


def test_conditional_bootstrap_counts_only_nonpush_weeks_and_primary_keeps_pushes():
    rows = games(actual=(50, 20, 20), lines=(50., 30.5, 30.5))
    rows["game_date"] = ["2024-09-07T12:00:00Z", "2024-09-14T12:00:00Z", "2024-09-15T12:00:00Z"]
    scored = scoring.score_pmf(rows, pmf(3))
    result = scoring.paired_summary(scored, scored)
    assert result["metrics"]["three_outcome_nll"]["week_blocks"] == 2
    conditional = result["metrics"]["conditional_log_loss"]
    assert conditional["games"] == 2 and conditional["week_blocks"] == 1
    assert conditional["interval_95"] is None
