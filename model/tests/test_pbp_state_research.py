"""Fixed residual fit, chronological scoring and staged selection contracts."""
import json

import numpy as np
import pandas as pd
import pytest

from ncaaf_model import opponent_model
from ncaaf_model import pbp_state_research as study


def frame(n=160, season=2020):
    rng = np.random.default_rng(94+season)
    data = pd.DataFrame(rng.normal(size=(n,len(study.FULL_COLUMNS))), columns=study.FULL_COLUMNS)
    data['game_id'] = np.arange(n)+season*10000
    data['season'] = season
    data['market_total'] = 48+rng.normal(size=n)*6
    data['actual_total'] = np.rint(data.market_total+rng.normal(size=n)*15)
    data['adjusted_history_games'] = 8
    data['game_date'] = pd.Timestamp(f'{season}-09-01',tz='UTC')+pd.to_timedelta(np.arange(n),unit='D')
    return data


def test_baseline_matches_original_fixed_ridge():
    train, test = frame(), frame(20,2021)
    artifact = opponent_model.fit_artifact(train)
    x = np.column_stack([np.ones(len(test)),
                         (test[opponent_model.FEATURES].to_numpy()-artifact['center'])/artifact['scale']])
    expected = test.market_total.to_numpy()+np.clip(x@np.array(artifact['coefficients']),-10,10)
    got, metadata = study.fit_residual(train,test,opponent_model.FEATURES)
    np.testing.assert_array_equal(got,expected)
    assert metadata['coefficients'] == artifact['coefficients']


def test_test_outcomes_and_unlisted_poison_do_not_affect_forecasts():
    train,test = frame(),frame(20,2021)
    expected,_ = study.fit_residual(train,test,study.FULL_COLUMNS)
    original=train.copy(deep=True)
    test['actual_total']=np.nan
    test['future_result']='poison'
    train['unlisted_leaked_score']=1e100
    got,_ = study.fit_residual(train,test,study.FULL_COLUMNS)
    np.testing.assert_array_equal(got,expected)
    pd.testing.assert_frame_equal(train[original.columns],original)


def test_added_predictor_has_training_only_scale_and_prediction_cap():
    train,test = frame(),frame(20,2021)
    train['pbp_conversion_percentage_points']=np.linspace(-4,4,len(train))
    train['actual_total']=train.market_total+50*train.pbp_conversion_percentage_points
    test['pbp_conversion_percentage_points']=1e8
    got,metadata=study.fit_residual(train,test,study.FULL_COLUMNS)
    np.testing.assert_allclose(got-test.market_total,10)
    assert metadata['scale'][-1] == pytest.approx(train.pbp_conversion_percentage_points.std(ddof=0))
    assert metadata['clipped_forecasts']==len(test)


@pytest.mark.parametrize('fault',['same_year','overlap','two_test_years','nonfinite','unplanned_columns'])
def test_fixed_fit_rejects_invalid_contract(fault):
    train,test=frame(),frame(20,2021)
    columns=study.FULL_COLUMNS
    if fault=='same_year': train['season']=2021
    if fault=='overlap': test.loc[0,'game_id']=train.game_id.iloc[0]
    if fault=='two_test_years': test.loc[0,'season']=2022
    if fault=='nonfinite': test.loc[0,'pbp_clock_rating']=np.inf
    if fault=='unplanned_columns': columns=(*study.FULL_COLUMNS,'actual_total')
    with pytest.raises(ValueError): study.fit_residual(train,test,columns)


def test_choose_uses_only_prescribed_years_and_fixed_tie_order():
    data=pd.concat([frame(2,y) for y in study.SELECTION_YEARS],ignore_index=True)
    for name in study.CONFIGS:data[name]=data.actual_total
    assert study.choose(data)=='market_only'
    data['pbp_state_ridge']=data.actual_total+1
    data['market_only']=data.actual_total+2
    assert study.choose(data)=='opponent_adjusted_ridge'
    extra=frame(2,2025)
    for name in study.CONFIGS:extra[name]=extra.actual_total
    with pytest.raises(ValueError):study.choose(pd.concat([data,extra],ignore_index=True))


def test_week_bootstrap_uses_ratio_of_loss_sums_to_game_counts():
    data=pd.DataFrame({'game_date':['2024-09-03T20:00:00Z','2024-09-04T20:00:00Z','2024-09-12T20:00:00Z'],
                       'actual_total':[0.,0.,0.], 'a':[1.,3.,10.], 'b':[0.,1.,4.]})
    result=study.paired(data,'a','b',draws=101)
    grouped=np.array([[9.,3.,2.],[84.,6.,1.]])
    rng=np.random.Generator(np.random.PCG64(20260909))
    sums=grouped[rng.integers(0,2,(101,2))].sum(axis=1)
    np.testing.assert_array_equal(result['mse_interval_95'],np.quantile(sums[:,0]/sums[:,2],[.025,.975]))
    np.testing.assert_array_equal(result['mse_interval_99'],np.quantile(sums[:,0]/sums[:,2],[.005,.995]))
    assert result['mse_difference']==31.
    assert study.paired(data.iloc[:2],'a','b')['mse_interval_95'] is None


def test_later_check_requires_committed_selection_before_loading_features(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(study,'read_plan',lambda root: calls.append('plan'))
    def blocked(root):
        calls.append('selection')
        raise ValueError('selection not committed')
    monkeypatch.setattr(study,'verify_selection',blocked)
    monkeypatch.setattr(study,'load_features',lambda root: pytest.fail('2025 features loaded too early'))
    with pytest.raises(ValueError,match='not committed'):study.check(tmp_path)
    assert calls==['plan','selection']


def test_mutated_selection_forecasts_block_later_check(tmp_path,monkeypatch):
    (tmp_path/study.PLAN).parent.mkdir(parents=True)
    (tmp_path/study.PLAN).write_text('{}')
    (tmp_path/study.FEATURE_AUDIT).write_text('{}')
    p=tmp_path/study.SPACE/'selection.parquet';p.parent.mkdir(parents=True)
    p.write_bytes(b'changed bytes')
    record={'plan_sha256':study.sha(tmp_path/study.PLAN),
            'feature_audit_sha256':study.sha(tmp_path/study.FEATURE_AUDIT),
            'prediction_file':{'path':str(p.relative_to(tmp_path)),'sha256':'0'*64}}
    (tmp_path/study.SELECTION).write_text(json.dumps(record))
    monkeypatch.setattr(study,'committed',lambda *a:'synthetic')
    with pytest.raises(ValueError,match='forecasts changed'):study.verify_selection(tmp_path)


def test_outputs_are_immutable(tmp_path):
    p=tmp_path/'result.json'
    study.write_new(p,{'value':1})
    with pytest.raises(FileExistsError):study.write_new(p,{'value':2})
    assert json.loads(p.read_text())=={'value':1}


@pytest.mark.parametrize('path',[
    'reports/pbp_state_selection.json', 'reports/pbp_state_results.json',
    'data/normalized/pbp_state_v1/selection.parquet', 'data/normalized/pbp_state_v1/2025.parquet'])
def test_source_amendment_cannot_claim_pre_performance_after_v1_forecasts(tmp_path,path):
    audit=tmp_path/'reports/pbp_state_feature_audit.json'
    audit.parent.mkdir(parents=True)
    audit.write_text(json.dumps({'matchup_prediction_scores_computed':False}))
    existing=tmp_path/path;existing.parent.mkdir(parents=True,exist_ok=True)
    existing.write_bytes(b'prior evaluation exists')
    with pytest.raises(ValueError,match='no earlier matchup evaluation'):study.freeze(tmp_path)
    assert not (tmp_path/study.PLAN).exists()
