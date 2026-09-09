"""Synthetic mathematics and chronology; no historical corpus read or fit."""
import json

import numpy as np
import pytest
from scipy.special import ndtr

from ncaaf_model import score_shape as shape


def normal(mean=55., sigma=15.):
    y=np.arange(251)
    p=np.maximum(ndtr((y+.5-mean)/sigma)-ndtr((y-.5-mean)/sigma),1e-15)
    p[-1]+=max(0.,1-ndtr((250.5-mean)/sigma))
    return p/p.sum()


def ratios():
    return np.clip(1.+.45*np.cos(np.arange(251)*1.79),.6,1.6)


def test_count_prior_and_fixed_clip_are_exact():
    expected=np.tile(np.ones(251)/251,(5,1))
    observed=np.array([0,0,1,7,250])
    model=shape.ScoreShape.fit(observed,expected,[2020]*5)
    counts=np.bincount(observed,minlength=251)
    expectation=expected.sum(axis=0)
    prior=800.*expectation/5
    wanted=np.clip((counts+prior)/(expectation+prior),.6,1.6)
    np.testing.assert_allclose(model.ratios,wanted,rtol=1e-15)
    np.testing.assert_array_equal(model.observed_counts,counts)
    assert model.training_max_season==2020 and model.training_games==5
    assert model.metadata()['prior_games']==800


def test_identity_ratio_preserves_full_distribution():
    p=normal()
    got,diagnostic=shape.moment_correct(p,np.ones(251))
    np.testing.assert_allclose(got,p,rtol=2e-14,atol=2e-16)
    assert diagnostic['iterations']==0
    assert diagnostic['theta']==[0.,0.]


def test_raw_ratio_clip_counts_and_metadata_are_json_safe():
    expected=np.tile(np.ones(251)/251,(801,1))
    model=shape.ScoreShape.fit(np.full(801,50),expected,[2020]*801)
    metadata=model.metadata()
    assert model.clipped_low_scores==250 and model.clipped_high_scores==1
    assert model.raw_ratios[0]<.6 and model.raw_ratios[50]>1.6
    assert model.ratios[0]==.6 and model.ratios[50]==1.6
    for key in ['ratios','raw_ratios','observed_counts','expected_counts']:
        assert len(metadata[key])==251
    json.dumps(metadata,allow_nan=False)
    with pytest.raises(ValueError):model.raw_ratios[0]=1.


@pytest.mark.parametrize('mean,sigma',[(15,6),(15,30),(55,6),(55,15),(100,6),(100,30),(0,6),(240,30)])
def test_positive_mass_and_exact_discrete_moments_in_tails(mean,sigma):
    p=normal(mean,sigma)
    got,diagnostic=shape.moment_correct(p,ratios())
    assert got.shape==(251,) and np.all(got>0)
    assert got.sum()==pytest.approx(1.,abs=2e-15)
    first=p@shape.SCORES;variance=p@(shape.SCORES-first)**2
    assert got@shape.SCORES==pytest.approx(first,abs=1e-8)
    assert got@(shape.SCORES-first)**2==pytest.approx(variance,rel=2e-11,abs=1e-10)
    assert diagnostic['max_standardized_moment_error']<=1e-11
    assert not np.allclose(got,p,rtol=1e-6,atol=1e-8)


def test_analytic_gradient_and_hessian_match_finite_differences():
    p=normal(48,13);mean=p@shape.SCORES;sd=np.sqrt(p@(shape.SCORES-mean)**2)
    z=(shape.SCORES-mean)/sd;basis=np.column_stack([z,z*z])
    log_weights=np.log(p)+np.log(ratios());theta=np.array([.03,-.02])
    objective,gradient,hessian,_=shape._dual(theta,log_weights,basis)
    step=1e-5;numeric_gradient=[];numeric_hessian=[]
    for direction in np.eye(2):
        plus=shape._dual(theta+step*direction,log_weights,basis)
        minus=shape._dual(theta-step*direction,log_weights,basis)
        numeric_gradient.append((plus[0]-minus[0])/(2*step))
        numeric_hessian.append((plus[1]-minus[1])/(2*step))
    np.testing.assert_allclose(gradient,numeric_gradient,rtol=1e-7,atol=1e-9)
    np.testing.assert_allclose(hessian,np.array(numeric_hessian).T,rtol=1e-7,atol=1e-8)
    assert np.linalg.eigvalsh(hessian).min()>0


def test_training_and_prediction_inputs_are_not_mutated_or_retained_as_views():
    pmfs=np.stack([normal(45),normal(60),normal(70)]);actual=np.array([44.,57.,73.]);seasons=np.array([2020,2021,2020])
    before=[x.copy() for x in (pmfs,actual,seasons)]
    model=shape.ScoreShape.fit(actual,pmfs,seasons)
    future=np.stack([normal(50),normal(70)]);future_before=future.copy()
    wanted,details=model.predict_pmf(future,[2022,2023])
    for old,current in zip(before,(pmfs,actual,seasons)):np.testing.assert_array_equal(old,current)
    np.testing.assert_array_equal(future,future_before)
    pmfs[:]=1/251;actual[:]=250;seasons[:]=2030
    got,_=model.predict_pmf(future,[2022,2023])
    np.testing.assert_array_equal(got,wanted)
    with pytest.raises(ValueError):model.ratios[0]=1.
    assert model.training_seasons==(2020,2021)


def test_prediction_api_cannot_consume_future_labels_and_checks_seasons():
    model=shape.ScoreShape.fit([50,55],np.stack([normal(),normal()]),[2020,2021])
    future=np.stack([normal()])
    with pytest.raises(TypeError):model.predict_pmf(future,[2022],actual_total=[500])
    for seasons in ([2021],[2020],[],[2022,2023],[np.nan],[True]):
        with pytest.raises(ValueError):model.predict_pmf(future,seasons)


@pytest.mark.parametrize('bad',[[-1],[251],[5.2],[np.nan],[True],[],[50,55]])
def test_invalid_exact_training_scores_are_rejected(bad):
    with pytest.raises(ValueError):shape.ScoreShape.fit(bad,np.stack([normal()]),[2020])


@pytest.mark.parametrize('fault',['zero','negative','nonnormal','nan','infinity','short','one_dimensional','empty'])
def test_invalid_pmfs_are_not_silently_repaired(fault):
    p=np.stack([normal()])
    if fault=='zero':p[0,0]=0
    if fault=='negative':p[0,0]=-1e-8
    if fault=='nonnormal':p*=.8
    if fault=='nan':p[0,0]=np.nan
    if fault=='infinity':p[0,0]=np.inf
    if fault=='short':p=p[:,:250]
    if fault=='one_dimensional':p=p[0]
    if fault=='empty':p=p[:0]
    with pytest.raises(ValueError):shape.ScoreShape.fit([50],p,[2020])


@pytest.mark.parametrize('fault',['short','low','high','nan'])
def test_invalid_ratio_is_rejected(fault):
    r=np.ones(251)
    if fault=='short':r=r[:250]
    if fault=='low':r[0]=.599
    if fault=='high':r[0]=1.601
    if fault=='nan':r[0]=np.nan
    with pytest.raises(ValueError):shape.moment_correct(normal(),r)


@pytest.mark.parametrize('bad',[1.,[1.,2.],np.ones((1,251))/251])
def test_single_pmf_shape_errors_raise_valueerror(bad):
    with pytest.raises(ValueError):shape.moment_correct(bad,np.ones(251))


def test_solver_failure_raises_without_returning_baseline(monkeypatch):
    monkeypatch.setattr(shape,'MAX_ITERATIONS',0)
    with pytest.raises(shape.ShapeConvergenceError,match='iteration limit'):
        shape.moment_correct(normal(),ratios())


def test_single_prior_game_is_valid_without_an_evidence_or_sample_gate():
    fitted=shape.ScoreShape.fit([48],np.stack([normal()]),[2020])
    out,details=fitted.predict_pmf(np.stack([normal()]),[2021])
    assert out.shape==(1,251) and details[0]['status']=='converged'
    assert fitted.metadata()['status']=='unvalidated_research_candidate'
