from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from ncaaf_model import calibration_research as study


def observations(year=2021, n=160):
    rng = np.random.default_rng(4001)
    line = rng.choice([40., 45.5, 50., 55.5, 60.5], n)
    projected = line+rng.normal(0, 2, n)
    return pd.DataFrame({"game_id":np.arange(n), "season":year, "week":np.arange(n)%12+1,
        "game_date":pd.date_range(f"{year}-09-01",periods=n,freq="12h",tz="UTC"),
        "market_total":line, "projected_total":projected, "abs_spread":rng.uniform(0,30,n),
        "actual_total":np.clip(np.round(projected+rng.normal(0,15,n)),0,250),
        "residual_sigma":np.full(n,17.), "market_source":"verified_fixture"})


def test_calibration_cannot_predict_its_training_season():
    train = observations()
    fitted = study.Recalibration.fit(train)
    with pytest.raises(ValueError,match="follow every"):
        fitted.parameters(train,"recalibrated")


def test_parameters_never_inspect_test_outcomes_and_mean_is_shared():
    fitted = study.Recalibration.fit(observations())
    test = observations(2022)
    changed = test.assign(actual_total=250-test.actual_total)
    for method in study.METHODS:
        mu, sigma = fitted.parameters(test,method)
        new_mu, new_sigma = fitted.parameters(changed,method)
        np.testing.assert_array_equal(mu,new_mu)
        np.testing.assert_array_equal(sigma,new_sigma)
    np.testing.assert_array_equal(fitted.parameters(test,"recalibrated")[0],
                                  fitted.parameters(test,"conditional_variance")[0])


def test_raw_uses_saved_fold_sigma_and_preserves_its_center():
    fitted = study.Recalibration.fit(observations())
    test = observations(2022)
    test["residual_sigma"] = np.linspace(10.,24.,len(test))
    mu, sigma = fitted.parameters(test,"raw")
    np.testing.assert_array_equal(mu,test.projected_total)
    np.testing.assert_array_equal(sigma,test.residual_sigma)


def test_market_deviation_coefficient_is_exactly_zero():
    train = observations()
    train["projected_total"] = train.market_total
    fitted = study.Recalibration.fit(train)
    assert fitted.beta[1] == 0
    assert 100 <= fitted.variance <= 576
    assert fitted.optimizer_success


def test_failed_variance_fit_falls_back_to_flagged_constant(monkeypatch):
    monkeypatch.setattr(study,"minimize",lambda *args,**kwargs:SimpleNamespace(success=False,fun=np.nan,x=np.full(3,np.nan),message="failure fixture"))
    fitted = study.Recalibration.fit(observations())
    assert fitted.optimizer_success is False
    np.testing.assert_array_equal(fitted.gamma,np.zeros(3))
    test = observations(2022)
    np.testing.assert_array_equal(fitted.parameters(test,"recalibrated")[1],
                                  fitted.parameters(test,"conditional_variance")[1])


def test_optimizer_numeric_exception_is_visible_and_uses_fixed_fallback(monkeypatch):
    def fail(*args,**kwargs):
        raise FloatingPointError("numeric fixture")
    monkeypatch.setattr(study,"minimize",fail)
    fitted = study.Recalibration.fit(observations())
    assert not fitted.optimizer_success
    assert "FloatingPointError" in fitted.optimizer_message
    np.testing.assert_array_equal(fitted.gamma,np.zeros(3))


def test_score_probabilities_handle_actual_integer_pushes_and_half_lines():
    test = observations(2022,n=2)
    test["market_total"] = [50.,50.5]
    test["actual_total"] = [50.,51.]
    scored = study.score(test,np.array([50.,50.5]),np.array([15.,15.]))
    assert scored.iloc[0].predicted_push > 0
    assert scored.iloc[1].predicted_push == 0
    assert np.isnan(scored.iloc[0].conditional_log_loss)
    assert scored.iloc[0].market_nll == pytest.approx(-np.log(scored.iloc[0].predicted_push))
    assert scored.iloc[1].market_nll == pytest.approx(scored.iloc[1].conditional_log_loss)
    assert (scored.crps >= 0).all()
    assert not any("profit" in col or "bet" in col for col in scored)


def test_all_probability_mass_is_retained_for_edge_score_support():
    pmf = study.normal_pmf(np.array([0.,249.]),np.array([24.,24.]))
    over, under, push = study.market_probabilities(pmf,np.array([0.,250.]))
    np.testing.assert_allclose(over+under+push,np.ones(2),atol=1e-12)
    assert under[0] == 0 and over[1] == 0
    assert push[0] > 0 and push[1] > 0


def test_weeks_are_monday_to_sunday_by_eastern_kickoff_date():
    f = pd.DataFrame({"game_date":["2025-09-08T02:00:00Z","2025-09-08T05:00:00Z"]})
    assert study.week_blocks(f).tolist() == ["2025-09-01","2025-09-08"]


def test_frozen_plan_cannot_be_overwritten(tmp_path):
    target = tmp_path/"reports/calibration_research_plan.json"
    target.parent.mkdir()
    target.write_text("original plan")
    with pytest.raises(ValueError,match="already exists"):
        study.make_plan(tmp_path)
    assert target.read_text() == "original plan"
