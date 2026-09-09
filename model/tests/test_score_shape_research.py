import json

import numpy as np
import pandas as pd
import pytest

from ncaaf_model import score_shape_research as study


def choice_rows():
    return pd.DataFrame([{'game_id':year,'season':year,'configuration':name,
                          study.PRIMARY:1.,'exact_score_nll':10.-i}
                         for i,name in enumerate(study.CONFIGS) for year in study.YEARS])


def test_choice_uses_actual_line_primary_and_fixed_tie_order():
    rows=choice_rows()
    assert study.choose(rows)=='market_normal'
    rows.loc[rows.configuration.eq('market_score_shape'),study.PRIMARY]=.9
    assert study.choose(rows)=='market_score_shape'


def test_choice_rejects_future_or_unequal_or_duplicate_cohorts():
    rows=choice_rows()
    with pytest.raises(ValueError,match='pre-2025'):
        study.choose(pd.concat([rows,rows.iloc[[0]].assign(season=2025,game_id=2025)]))
    with pytest.raises(ValueError,match='share all games'):
        study.choose(rows.iloc[:-1])
    with pytest.raises(ValueError,match='Duplicate'):
        study.choose(pd.concat([rows,rows.iloc[[0]]]))


def synthetic_sources():
    values={'season':2020,'week':1,'home_id':1,'away_id':2,'home_team':'Home','away_team':'Away',
            'home_score':28,'away_score':21,'actual_total':49,'market_total':50.5,
            'market_home_spread':-3.5,'neutral_site':False,'status':'STATUS_FINAL',
            'market_source':'test_provider','source_verified_pregame':True,'adjusted_history_games':6,
            'ratings_training_rows':10,'game_date':'2021-09-04T16:00:00+00:00',
            'ratings_cutoff':'2021-08-30T04:00:00+00:00'}
    cache=pd.DataFrame({**{key:[value]*5008 for key,value in values.items()},'game_id':np.arange(1,5009)})
    cache.loc[0,'season']=2021
    oof=pd.concat([cache.iloc[[0]].assign(candidate=name,projected_total=50.5,residual_sigma=16.)
                   for name in study.calibration.BASES],ignore_index=True)
    return oof,cache


def test_source_identity_accepts_same_facts_with_equivalent_datetime_formats():
    rows,cache=synthetic_sources()
    rows['game_date']='2021-09-04T12:00:00-04:00'
    study.validate_sources(rows,cache)


@pytest.mark.parametrize('field,value',[
    ('market_source','wrong_source'),('market_total',51.),('actual_total',50),('home_id',3),
    ('game_date','2021-09-05T16:00:00+00:00'),('ratings_cutoff','2021-09-01T04:00:00+00:00')])
def test_source_identity_rejects_one_reference_fact_mismatch(field,value):
    rows,cache=synthetic_sources()
    rows.loc[1,field]=value
    with pytest.raises((ValueError,AssertionError)):
        study.validate_sources(rows,cache)


def test_source_identity_rejects_a_false_market_center_and_duplicate_cache():
    rows,cache=synthetic_sources()
    rows.loc[rows.candidate.eq('market_only'),'projected_total']=60.
    with pytest.raises(ValueError,match='center'):
        study.validate_sources(rows,cache)
    cache.loc[1,'game_id']=1
    with pytest.raises(ValueError,match='universe'):
        study.validate_sources(rows,cache)


def forecast_frame():
    return pd.DataFrame([dict(game_id=y,season=y,week=1,game_date=f'{y}-09-04T16:00:00Z',
                       ratings_cutoff=f'{y}-08-25T04:00:00Z',market_source='test',actual_total=49,
                       market_total=50.5,projected_total=50.5,residual_sigma=16.,candidate=name)
                       for name in study.calibration.BASES for y in range(2021,2026)])


def test_annual_fit_receives_strict_prior_labels_and_never_future_labels(monkeypatch):
    captured=[]
    class FakeShape:
        @classmethod
        def fit(cls,scores,pmfs,seasons):
            captured.append((scores.copy(),seasons.copy()))
            return cls()
        def predict_pmf(self,pmfs,seasons):
            assert seasons.tolist()==[2022]
            return pmfs,[{'iterations':0}]
        def metadata(self):
            return {'synthetic':True}
    monkeypatch.setattr(study,'ScoreShape',FakeShape)
    frame=forecast_frame()
    first,_=study.annual_forecasts(frame,[2022])
    frame.loc[frame.season.ge(2023),'actual_total']=200
    second,_=study.annual_forecasts(frame,[2022])
    pd.testing.assert_frame_equal(first,second)
    assert all(seasons.tolist()==[2021] and scores.tolist()==[49] for scores,seasons in captured)
    assert set(first.configuration)==set(study.CONFIGS)


def test_annual_fit_rejects_missing_prior_season_or_unplanned_test_year():
    frame=forecast_frame()
    with pytest.raises(ValueError,match='earlier'):
        study.annual_forecasts(frame.loc[frame.season.ne(2021)],[2022])
    with pytest.raises(ValueError,match='Unplanned'):
        study.annual_forecasts(frame,[2026])


def test_write_new_never_overwrites_a_frozen_result(tmp_path):
    path=tmp_path/'result.json'
    study.write_new(path,{'first':True})
    with pytest.raises(FileExistsError):
        study.write_new(path,{'first':False})
    assert json.loads(path.read_text())=={'first':True}


def test_select_checks_plan_before_data_and_refuses_existing_stage(tmp_path,monkeypatch):
    monkeypatch.setattr(study,'read_plan',lambda root:({},'head'))
    monkeypatch.setattr(study,'load_baselines',lambda root:pytest.fail('Existing stage must not load/further fit'))
    (tmp_path/study.SPACE).mkdir(parents=True)
    with pytest.raises(FileExistsError):
        study.select(tmp_path)


def test_later_check_requires_committed_selection_before_data(tmp_path,monkeypatch):
    monkeypatch.setattr(study,'read_plan',lambda root:({},'head'))
    def missing(root):
        raise ValueError('Selection not committed')
    monkeypatch.setattr(study,'verify_selection',missing)
    monkeypatch.setattr(study,'load_baselines',lambda root:pytest.fail('Cannot load later stage before committed selection'))
    with pytest.raises(ValueError,match='not committed'):
        study.check(tmp_path)


def test_plan_rejects_changed_bytes_before_commit_verification(tmp_path,monkeypatch):
    source=tmp_path/'source'; source.write_text('first')
    plan={'version':study.VERSION,'execution_plan_revision':2,'candidate_order':list(study.CONFIGS),'selection_years':list(study.YEARS),
          'primary_metric':study.PRIMARY,'source_files_sha256':{'source':study.sha(source)},
          'libraries':{},'tracked_files':[]}
    study.write_new(tmp_path/study.PLAN,plan)
    source.write_text('changed')
    monkeypatch.setattr(study,'committed',lambda *args:pytest.fail('Changed input must fail first'))
    with pytest.raises(ValueError,match='Frozen input changed'):
        study.read_plan(tmp_path)


def test_stage_failure_retains_original_attempt_receipts(tmp_path,monkeypatch):
    monkeypatch.setattr(study.subprocess,'check_output',lambda *args,**kwargs:b'synthetic-head\n')
    def fail(root):
        raise ArithmeticError('synthetic solver failure')
    monkeypatch.setattr(study,'select',fail)
    with pytest.raises(ArithmeticError,match='solver failure'):
        study.run_stage(tmp_path,'select')
    folders=list((tmp_path/study.ATTEMPTS).iterdir())
    assert len(folders)==1
    first=(folders[0]/'started.json').read_bytes()
    terminal=json.loads((folders[0]/'finished.json').read_text())
    assert terminal['status']=='failed'
    assert terminal['error_type']=='ArithmeticError'
    assert terminal['started_sha256']==study.sha(folders[0]/'started.json')
    monkeypatch.setattr(study,'select',lambda root:{'status':'synthetic_success'})
    assert study.run_stage(tmp_path,'select')['status']=='synthetic_success'
    assert len(list((tmp_path/study.ATTEMPTS).iterdir()))==2
    assert (folders[0]/'started.json').read_bytes()==first
    assert json.loads((folders[0]/'finished.json').read_text())==terminal


def test_hashed_local_source_is_verified_without_requiring_a_git_blob(tmp_path,monkeypatch):
    local=tmp_path/'local_forecasts.csv'; local.write_text('synthetic,input\n1,2\n')
    tracked=tmp_path/'code.py'; tracked.write_text('# synthetic source\n')
    monkeypatch.setattr(study,'LIBRARIES',())
    plan={'version':study.VERSION,'execution_plan_revision':2,'candidate_order':list(study.CONFIGS),
          'selection_years':list(study.YEARS),'primary_metric':study.PRIMARY,
          'source_files_sha256':{'local_forecasts.csv':study.sha(local),'code.py':study.sha(tracked)},
          'libraries':{},'tracked_files':['code.py']}
    study.write_new(tmp_path/study.PLAN,plan)
    def check_tracked(root,paths):
        assert paths==[study.PLAN,Path('code.py')]
        return 'synthetic-head'
    from pathlib import Path
    monkeypatch.setattr(study,'committed',check_tracked)
    assert study.read_plan(tmp_path)[1]=='synthetic-head'
    local.write_text('changed local forecasts')
    with pytest.raises(ValueError,match='Frozen input changed'):
        study.read_plan(tmp_path)
