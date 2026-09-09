"""Synthetic persistence/chronology tests for the scheduled study runner."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import weather_revision_study as runner
from ncaaf_model import weather_revision_study as study
from ncaaf_model.revision_archive import immutable_json

spec = importlib.util.spec_from_file_location('study_fixtures', Path(__file__).parents[2] / 'model/tests/test_weather_revision_study.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def clock():
    return study.utc(fixture.NOW)


def observation():
    _, o = fixture.artifact()
    o.pop('observation_id')
    o['observation_id'] = study.digest(o)
    return o


def decision():
    return {'game_id': '100', 'run_id': 'run', 'run_attempt': '1', 'kickoff': '2025-09-27T22:00:00Z',
            'capture_started_at': '2025-09-26T09:59:00Z'}


def install_fit(base, kind='probability', *, available='2025-09-22T04:01:00Z'):
    target = runner.TARGETS[kind]
    rows, labels, _ = fixture.data(target)
    a = study.fit_pair(rows, labels, cutoff=fixture.CUTOFF, target_kind=target, protocol_sha256=runner.PROTOCOL_SHA)
    directory = base / 'fits' / fixture.CUTOFF.replace(':', '-') / kind
    immutable_json(directory / 'artifact.json', a)
    immutable_json(directory / 'availability.json', {'artifact_sha256': study.digest(a), 'available_at': available})
    return a


def lock(base, o=None, **changes):
    o = observation() if o is None else o
    args = dict(run_id='run', run_attempt='1', event_name='schedule', labels_only=False,
                source={'test': True}, revalidate=lambda: deepcopy(o), now=clock)
    args.update(changes)
    return runner.lock_decision(base, decision(), o, **args)


def test_observation_content_address_cannot_change_and_duplicate_game_cannot_inflate(tmp_path):
    o = observation()
    runner.persist_observation(tmp_path, o)
    assert runner.load_observations(tmp_path) == [o]
    o['features']['temperature_f'] += 1
    with pytest.raises(ValueError, match='content address'):
        runner.persist_observation(tmp_path, o)
    o.pop('observation_id')
    o['observation_id'] = study.digest(o)
    runner.persist_observation(tmp_path, o)
    with pytest.raises(ValueError, match='duplicate'):
        runner.load_observations(tmp_path)


def test_probability_can_forecast_without_movement_model_and_paper_is_immutable(tmp_path):
    install_fit(tmp_path)
    result = lock(tmp_path)
    probability = result['targets']['probability']
    assert probability['status'] == 'forecast_recorded'
    assert probability['paper']['units_risked'] == 1
    assert probability['paper']['decimal_odds'] == 1.95
    assert result['targets']['movement']['status'] == 'unavailable'
    before = (tmp_path / 'decisions/100.json').read_bytes()
    assert lock(tmp_path, run_attempt='2', now=lambda: study.utc('2025-09-26T18:00:00Z')) == result
    assert (tmp_path / 'decisions/100.json').read_bytes() == before


@pytest.mark.parametrize('change', [dict(run_id='different'), dict(run_attempt='2'),
                                   dict(event_name='workflow_dispatch'), dict(labels_only=True)])
def test_forecast_cannot_be_created_by_another_capture_attempt_or_maintenance(tmp_path, change):
    install_fit(tmp_path)
    result = lock(tmp_path, **change)
    assert all(e['prediction'] is None and e['reason'] == 'not_same_live_scheduled_invocation'
               for e in result['targets'].values())


def test_late_artifact_and_changed_final_offer_permanently_abstain(tmp_path):
    install_fit(tmp_path, available='2025-09-26T10:00:01Z')
    result = lock(tmp_path)
    assert result['targets']['probability']['prediction'] is None
    second = tmp_path / 'second'
    install_fit(second)
    changed = observation()
    changed['observed_offers'][0]['under_decimal_odds'] = 2.1
    result = lock(second, revalidate=lambda: changed)
    assert 'changed_after_inference' in result['targets']['probability']['reason']
    assert lock(second)['targets']['probability']['prediction'] is None


def test_second_target_cannot_backdate_probability_paper_lock(tmp_path):
    install_fit(tmp_path)
    install_fit(tmp_path, 'movement')
    current = [study.utc('2025-09-26T10:00:10Z')]
    checks = [0]
    o = observation()
    def recheck():
        checks[0] += 1
        if checks[0] == 2:
            current[0] = study.utc('2025-09-26T10:02:01Z')
        return deepcopy(o)
    result = lock(tmp_path, o, revalidate=recheck, now=lambda: current[0])
    p = result['targets']['probability']
    assert p['prediction'] is not None
    assert p['paper'] is None and p['paper_status'] == 'abstained'
    assert 'stale' in p['paper_reason']


def test_availability_receipt_is_written_after_artifact_and_never_backdated(tmp_path):
    moments = []
    def after_write():
        paths = list((tmp_path / 'fits').glob('**/artifact.json'))
        assert paths
        moments.append(len(paths))
        return study.utc('2025-09-22T04:02:00Z')
    empty = {'final_total': {}, 'market_movement': {}}
    states = runner.fit_week(tmp_path, [], empty, cutoff=study.utc(fixture.CUTOFF), source={}, now=after_write)
    assert moments == [1, 2]
    assert states['probability']['status'] == 'insufficient_training'
    # A later run does not rewrite that week's training decision or timestamps.
    assert runner.fit_week(tmp_path, [], empty, cutoff=study.utc(fixture.CUTOFF), source={},
                           now=lambda: pytest.fail('Must reuse stored receipt')) == states


def test_incomplete_artifact_publication_recovers_with_actual_later_receipt(tmp_path):
    a = install_fit(tmp_path)
    directory = tmp_path / 'fits' / fixture.CUTOFF.replace(':', '-') / 'probability'
    (directory / 'availability.json').unlink()
    assert runner.load_artifact(tmp_path, 'probability', study.utc(fixture.CUTOFF)) == (a, None)
    states = runner.fit_week(tmp_path, [], {'final_total': {}, 'market_movement': {}},
                             cutoff=study.utc(fixture.CUTOFF), source={}, now=clock)
    assert states['probability']['available_at'] == fixture.NOW
    assert lock(tmp_path)['targets']['probability']['prediction'] is None


def test_zero_input_orchestrator_has_no_http_and_no_fitted_model(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'provenance', lambda root: ({}, {'test': True}))
    class Integrity:
        def receipt(self, path):
            pytest.fail('No receipt access is needed without archived runs')
    monkeypatch.setattr(runner, 'load_runs', lambda root, **kwargs: ([], Integrity(), []))
    import requests
    monkeypatch.setattr(requests, 'get', lambda *a, **k: pytest.fail('Empty study must not fetch data'))
    result = runner.execute(tmp_path, run_id='empty', run_attempt='1', now=clock)
    assert result['errors'] == []
    assert result['status'] == 'collecting'
    assert result['observations']['total'] == result['paper']['locked'] == 0
    assert all(m['status'] == 'insufficient_training' for m in result['models'].values())
    assert (tmp_path / 'site/data/weather-revision-study.json').exists()


def test_original_paper_is_regraded_by_append_only_correction(tmp_path):
    install_fit(tmp_path)
    record = lock(tmp_path)
    pred = record['targets']['probability']['prediction']
    label = {'schema_version': 'weather-revision-target-v1', 'target_kind': 'final_total',
             'observation_id': pred['observation_id'], 'game_id': '100',
             'target_available_at': '2025-09-28T02:00:00Z', 'final_total': 40, 'label_under': 1}
    latest = {'final_total': {pred['observation_id']: label}, 'market_movement': {}}
    first, _, _ = runner.score_archive(tmp_path, latest, cutoff='2025-09-29T10:00:00Z')
    original = (tmp_path / 'decisions/100.json').read_bytes()
    label.update(final_total=60, label_under=0, target_available_at='2025-09-29T11:00:00Z')
    second, _, _ = runner.score_archive(tmp_path, latest, cutoff='2025-09-30T10:00:00Z')
    assert first['paper']['full_cohort_roi'] == pytest.approx(.95)
    assert second['paper']['full_cohort_roi'] == -1
    assert len(list((tmp_path / 'grades').glob('*.json'))) == 2
    assert (tmp_path / 'decisions/100.json').read_bytes() == original
