"""Schedule sidecars identify derived bytes without changing schedule rows."""
from datetime import datetime, timezone
from io import BytesIO
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from ncaaf_model import runtime, sources
from ncaaf_model.storage import sha256_bytes


def frame(ids=(1, 2), score=None):
    return pd.DataFrame([{'game_id':i,'game_date':'2026-09-12T19:30:00Z','home_team':'Home',
                         'away_team':'Away','status':'STATUS_SCHEDULED','home_score':score} for i in ids])


def settings(tmp_path):
    return SimpleNamespace(root=tmp_path,raw_dir=tmp_path/'raw',season=2026,
        data_urls={key:'https://example.invalid/'+key+'/{season}.parquet'
                   for key in ('schedule','betting','power_index','adv_team_gamelog','drives')})


def write_initial(config, data=None, metadata=None):
    path=config.raw_dir/'sportsdataverse/cfb_schedule_2026.parquet'
    path.parent.mkdir(parents=True,exist_ok=True)
    (frame() if data is None else data).to_parquet(path,index=False)
    receipt={'source_url':'https://example.invalid/original.parquet','retrieved_at':'2026-08-01T12:00:00Z',
             'sha256':sha256_bytes(path.read_bytes()),'content_type':'application/octet-stream','last_modified':None}
    if metadata is not None:
        receipt=metadata
    sidecar=path.with_suffix('.parquet.meta.json')
    sidecar.write_text(json.dumps(receipt))
    return path,sidecar,receipt


def payload(game_id=1):
    return {'events':[{'id':str(game_id),'date':'2026-09-12T19:30:00Z','season':{'year':2026,'type':2},'week':{'number':3},
        'competitions':[{'status':{'type':{'name':'STATUS_FINAL'}},'competitors':[
            {'homeAway':'home','score':'31','team':{'id':'10','displayName':'Home'}},
            {'homeAway':'away','score':'20','team':{'id':'11','displayName':'Away'}}]}]}]}


class Response:
    def __init__(self, *, content=None, data=None):
        self.content=content
        self.data=data
        self.headers={'content-type':'application/octet-stream'}
        self.url='https://example.invalid/scoreboard'

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def assert_derived(path, sidecar, operation):
    metadata=json.loads(sidecar.read_text())
    assert metadata['schema_version']==sources.SCHEDULE_DERIVED_SCHEMA
    assert metadata['artifact_type']=='derived_schedule'
    assert metadata['sha256']==sha256_bytes(path.read_bytes())
    assert metadata['operation']==operation
    assert 'source_url' not in metadata and 'retrieved_at' not in metadata
    assert 'not when any retained row became available' in metadata['generation_note']
    return metadata


def parquet_roundtrip(data):
    """The previous writers also used Parquet's existing dtype conversion."""
    buffer=BytesIO()
    data.to_parquet(buffer,index=False)
    buffer.seek(0)
    return pd.read_parquet(buffer)


def test_archive_refresh_current_hash_new_time_raw_receipt_and_exact_original_merge(tmp_path,monkeypatch):
    config=settings(tmp_path)
    previous=frame()
    path,sidecar,old_receipt=write_initial(config,previous)
    previous_hash=sha256_bytes(path.read_bytes())
    incoming=frame((1,),31.)
    incoming_bytes=BytesIO();incoming.to_parquet(incoming_bytes,index=False)
    client=sources.DataClient(config)
    monkeypatch.setattr(client.session,'get',lambda *a,**kw:Response(content=incoming_bytes.getvalue()))
    times=iter(['2026-09-09T12:00:01Z','2026-09-09T12:00:02Z','2026-09-09T12:00:03Z','2026-09-09T12:00:04Z'])
    monkeypatch.setattr(sources,'utc_now',lambda:next(times))
    client.archive_season(2026,refresh=True)
    pd.testing.assert_frame_equal(pd.read_parquet(path),sources.merge_schedule_frames(previous,incoming))
    metadata=assert_derived(path,sidecar,'archive_season_refresh')
    assert metadata['generated_at']=='2026-09-09T12:00:02Z'
    assert metadata['raw_download']['receipt']['retrieved_at']=='2026-09-09T12:00:01Z'
    assert metadata['raw_download']['receipt']['sha256']==sha256_bytes(incoming_bytes.getvalue())
    assert metadata['raw_download']['verification']=='receipt_hash_matches_input_bytes'
    assert metadata['raw_download']['original_bytes']=='not_separately_preserved'
    assert metadata['inputs'][0]['sha256']==previous_hash
    assert metadata['inputs'][0]['raw_download']['receipt']==old_receipt
    assert (metadata['previous_rows'],metadata['incoming_rows'],metadata['merged_rows'])==(2,1,2)


def test_runtime_scoreboard_merge_keeps_legacy_mismatch_unverified_and_rows_unchanged(tmp_path,monkeypatch):
    config=settings(tmp_path)
    previous=frame()
    path,sidecar,receipt=write_initial(config,previous)
    receipt['sha256']='a'*64
    sidecar.write_text(json.dumps(receipt))
    client=sources.DataClient(config)
    def failed_download(*args,**kwargs):
        raise OSError('offline fixture')
    monkeypatch.setattr(client,'_download',failed_download)
    monkeypatch.setattr(client.session,'get',lambda *a,**kw:Response(data=payload()))
    monkeypatch.setattr(runtime,'DataClient',lambda *a,**kw:client)
    monkeypatch.setattr(sources,'utc_now',lambda:'2026-09-09T12:01:00Z')
    actual,diagnostics=runtime.refresh_inputs(config,datetime(2026,9,9,12,tzinfo=timezone.utc))
    expected=sources.merge_schedule_frames(sources.merge_schedule_frames(previous,sources.parse_espn_scoreboard(payload())),sources.parse_espn_scoreboard(payload()))
    pd.testing.assert_frame_equal(actual,expected)
    pd.testing.assert_frame_equal(pd.read_parquet(path),parquet_roundtrip(expected))
    metadata=assert_derived(path,sidecar,'runtime_scoreboard_merge')
    assert metadata['generated_at']=='2026-09-09T12:01:00Z'
    assert metadata['inputs'][0]['metadata_status']=='hash_mismatch'
    assert metadata['raw_download']=={'receipt':receipt,'verification':'unverified_legacy_receipt_hash_mismatch','original_bytes':'unrecovered'}
    assert diagnostics['schedule_fresh'] is True
    assert diagnostics['espn_group_80_games']==1


def test_ensure_odds_schedule_writer_preserves_exact_merge_and_uses_same_provenance(tmp_path,monkeypatch):
    config=settings(tmp_path)
    previous=frame((2,))
    path,sidecar,_=write_initial(config,previous)
    odds=tmp_path/'odds.json';odds.write_text(json.dumps([{'home_team':'Other','away_team':'Away','commence_time':'2026-09-12T19:30:00Z'}]))
    client=sources.DataClient(config)
    data=payload()
    monkeypatch.setattr(client.session,'get',lambda *a,**kw:Response(data=data,content=json.dumps(data).encode()))
    result=client.ensure_schedule_for_odds(odds)
    incoming=pd.concat([sources.parse_espn_scoreboard(data),sources.parse_espn_scoreboard(data)],ignore_index=True).drop_duplicates('game_id',keep='last')
    expected=sources.merge_schedule_frames(previous,incoming)
    pd.testing.assert_frame_equal(pd.read_parquet(path),parquet_roundtrip(expected))
    metadata=assert_derived(path,sidecar,'ensure_schedule_for_odds_scoreboard_merge')
    assert metadata['incoming_rows']==1
    assert result['schedule_rows']==2


def test_repeated_derived_writes_do_not_nest_parents_or_upgrade_legacy_claim(tmp_path):
    config=settings(tmp_path)
    path,sidecar,receipt=write_initial(config)
    receipt.update(sha256='b'*64,merge_policy='legacy merge')
    sidecar.write_text(json.dumps(receipt))
    snapshots=[]
    for i in range(12):
        source=sources.schedule_artifact_input(path,'previous_schedule')
        sources.write_derived_schedule(path,frame(),operation='synthetic_repeated_merge',inputs=[source])
        metadata=json.loads(sidecar.read_text())
        snapshots.append(metadata)
        assert metadata['raw_download']['verification']=='unverified_legacy_derived_receipt'
        assert metadata['raw_download']['original_bytes']=='unrecovered'
        assert 'inputs' not in metadata['inputs'][0]
    assert snapshots[-1]['inputs'][0]['metadata_status']=='matches_input_bytes'
    assert max(len(json.dumps(m)) for m in snapshots)-min(len(json.dumps(m)) for m in snapshots)<200
    assert snapshots[-1]['raw_download']==snapshots[0]['raw_download']


@pytest.mark.parametrize('bad', [[],None,'metadata',42,{'sha256':['wrong']},
    {'source_url':[],'retrieved_at':'2026-09-01T00:00:00Z','sha256':'a'*64},
    {'source_url':'https://example.invalid/raw','retrieved_at':0,'sha256':'a'*64},
    {'source_url':'https://example.invalid/raw','retrieved_at':'2026-09-01T00:00:00','sha256':'a'*64}])
def test_malformed_legacy_sidecars_never_become_raw_download_receipts(tmp_path,bad):
    config=settings(tmp_path)
    path,sidecar,_=write_initial(config)
    sidecar.write_text(json.dumps(bad))
    original=pd.read_parquet(path)
    snapshot=sources.schedule_artifact_input(path,'previous_schedule')
    sources.write_derived_schedule(path,original,operation='synthetic_merge',inputs=[snapshot])
    metadata=assert_derived(path,sidecar,'synthetic_merge')
    assert metadata['raw_download'] is None
    assert metadata['inputs'][0]['metadata_status']=='missing_or_invalid'
    pd.testing.assert_frame_equal(pd.read_parquet(path),original)


@pytest.mark.parametrize('bad_verification',['invented_verified_status',[],{'verified':True},None])
def test_malformed_inherited_receipt_cannot_flatten_into_verified_raw_metadata(tmp_path,bad_verification):
    path,sidecar,receipt=write_initial(settings(tmp_path))
    sources.write_derived_schedule(path,frame(),operation='synthetic_merge',inputs=[sources.schedule_artifact_input(path,'previous_schedule')])
    metadata=json.loads(sidecar.read_text())
    metadata.update(source_url=receipt['source_url'],retrieved_at=receipt['retrieved_at'])
    metadata['raw_download']['verification']=bad_verification
    sidecar.write_text(json.dumps(metadata))
    snapshot=sources.schedule_artifact_input(path,'previous_schedule')
    assert snapshot['artifact_type']=='derived_schedule'
    assert snapshot['raw_download'] is None


def test_atomic_parquet_failure_does_not_replace_existing_sidecar(tmp_path,monkeypatch):
    path,sidecar,_=write_initial(settings(tmp_path))
    before=(path.read_bytes(),sidecar.read_bytes())
    def fail_write(*args,**kwargs):
        raise OSError('synthetic write failure')
    snapshot=sources.schedule_artifact_input(path,'previous_schedule')
    monkeypatch.setattr(pd.DataFrame,'to_parquet',fail_write)
    with pytest.raises(OSError): sources.write_derived_schedule(path,frame((3,)),operation='synthetic_merge',inputs=[snapshot])
    assert (path.read_bytes(),sidecar.read_bytes())==before
    assert list(path.parent.glob('tmp*.parquet'))==[]
