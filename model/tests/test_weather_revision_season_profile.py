"""Offline profile boundaries and shared archive compatibility; no external I/O."""
from datetime import timedelta
import hashlib
import json

import pytest

from ncaaf_model import revision_archive as archive
from ncaaf_model import weather_revision_collector as collector
from ncaaf_model.weather_revision_weather import PREVIOUS_URL, SINGLE_URL
from test_weather_revision_collector import SyntheticSession, environment, event


SEASON_PROTOCOL = 'reports/WEATHER_REVISION_SEASON_COLLECTION_PROTOCOL.md'


@pytest.fixture
def season_environment(environment):
    root, clock = environment
    (root / SEASON_PROTOCOL).write_text('Synthetic separately frozen season protocol.\n')
    return root, clock


def invoke(environment, when, profile='season', games=()):
    root, clock = environment
    clock.value = when
    session = SyntheticSession(root, list(games))
    client = archive.ArchiveClient(root, session=session)
    manifest = collector.collect(root, now=when, client=client, profile=profile)
    return manifest, session


@pytest.mark.parametrize('when,eligible', [
    (collector.SEASON_START - timedelta(microseconds=1), False),
    (collector.SEASON_START, True),
    (collector.SEASON_END - timedelta(microseconds=1), True),
    (collector.SEASON_END, False),
    (collector.SEASON_END + timedelta(days=7), False),
])
def test_season_half_open_window_and_zero_http_outside(season_environment, when, eligible):
    root, _ = season_environment
    result, session = invoke(season_environment, when)
    assert result['status'] == ('no_games' if eligible else 'outside_season')
    assert result['collection_profile'] == 'season'
    assert result['collection_protocol_id'] == 'weather-revision-season-collection-v1'
    assert result['collection_protocol_file'] == SEASON_PROTOCOL
    assert result['collection_window'] == {
        'start_inclusive': '2026-09-16T03:00:00Z',
        'end_exclusive': '2027-02-01T03:00:00Z',
    }
    assert result['scheduled_utc_slots'] == ['01:17', '07:17', '13:17', '19:17']
    assert result['counts']['total_requests'] == int(eligible)
    assert len(session.calls) == int(eligible)
    paths = list((root / 'data/runtime/weather_revisions/runs').glob('*.json'))
    assert len(paths) == 1 and json.loads(paths[0].read_text()) == result
    if eligible:
        assert session.calls[0][0] == collector.SCOREBOARD
        assert result['provenance'][SEASON_PROTOCOL] == hashlib.sha256((root / SEASON_PROTOCOL).read_bytes()).hexdigest()
        assert 'reports/WEATHER_REVISION_CAPTURE_PROTOCOL.md' in result['provenance']
    else:
        assert not result['receipts'] and result['stored_response_bytes'] == 0


def test_default_pilot_still_skips_after_original_end_without_season_protocol(environment):
    root, clock = environment
    clock.value = collector.SEASON_START
    session = SyntheticSession(root, [])
    result = collector.collect(root, now=collector.SEASON_START,
                               client=archive.ArchiveClient(root, session=session))
    assert result['status'] == 'outside_pilot'
    assert result['collection_profile'] == 'pilot'
    assert result['collection_protocol_id'] == 'weather-revision-capture-pilot-v1'
    assert result['collection_window']['end_exclusive'] == '2026-09-16T03:00:00Z'
    assert result['schema_version'] == 'weather-revision-capture-v1'
    assert not session.calls


def test_in_window_default_pilot_needs_only_original_provenance(environment):
    root, clock = environment
    clock.value = collector.START
    session = SyntheticSession(root, [])
    result = collector.collect(root, now=collector.START,
                               client=archive.ArchiveClient(root, session=session))
    assert result['status'] == 'no_games'
    assert result['collection_profile'] == 'pilot'
    assert SEASON_PROTOCOL not in result['provenance']
    assert len(session.calls) == 1


def test_missing_season_protocol_fails_before_http(environment):
    result, session = invoke(environment, collector.SEASON_START)
    assert result['status'] == 'failed'
    assert result['collection_profile'] == 'season'
    assert result['failures'][0]['reason'] == 'collector_failed'
    assert not session.calls and result['counts']['total_requests'] == 0


@pytest.mark.parametrize('profile', ['continuation', 'Pilot', '', None, {}])
def test_unknown_profile_rejected_without_requests_or_run_files(environment, profile):
    root, _ = environment
    session = SyntheticSession(root, [])
    with pytest.raises(ValueError, match='Unknown collection profile'):
        collector.collect(root, now=collector.SEASON_START,
                          client=archive.ArchiveClient(root, session=session), profile=profile)
    assert not session.calls
    assert not list((root / 'data/runtime/weather_revisions/runs').glob('*.json'))


def test_season_reuses_original_mature_pilot_receipt_but_refetches_single_run(season_environment):
    root, _ = season_environment
    pilot_time = collector.END - timedelta(hours=1)
    kickoff = collector.SEASON_START + timedelta(days=1, hours=9)
    game = event(kickoff=kickoff)
    first, first_session = invoke(season_environment, pilot_time, profile='pilot', games=[game])
    assert first['status'] == 'ok'
    assert sum(url == PREVIOUS_URL for url, _ in first_session.calls) == 1
    pilot_path = root / 'data/runtime/weather_revisions/runs' / f"{first['run_id']}-1.json"
    pilot_bytes = pilot_path.read_bytes()
    first_comparator = first['rows'][0]['previous_day2']
    assert first_comparator['cached'] is False

    second, second_session = invoke(season_environment, collector.SEASON_START, games=[game])
    assert second['status'] == 'ok'
    assert second['collection_profile'] == 'season'
    assert second['counts']['paired_two_book_games'] == 1
    assert second['requested_run'] == first['requested_run']
    assert sum(url == SINGLE_URL for url, _ in second_session.calls) == 1
    assert all(url != PREVIOUS_URL for url, _ in second_session.calls)
    comparator = second['rows'][0]['previous_day2']
    assert comparator['cached'] is True
    assert comparator['receipt_path'] == first_comparator['receipt_path']
    assert comparator['measurement'] == first_comparator['measurement']
    assert second['rows'][0]['single_run']['receipt_path'] != first['rows'][0]['single_run']['receipt_path']
    assert pilot_path.read_bytes() == pilot_bytes


def test_profile_does_not_allow_overwriting_an_existing_invocation(season_environment, monkeypatch):
    monkeypatch.setenv('GITHUB_RUN_ID', 'synthetic-shared-identity')
    first, _ = invoke(season_environment, collector.END - timedelta(seconds=1), profile='pilot')
    root, _ = season_environment
    path = root / 'data/runtime/weather_revisions/runs/synthetic-shared-identity-1.json'
    before = path.read_bytes()
    session = SyntheticSession(root, [])
    with pytest.raises(ValueError, match='Invocation already archived'):
        collector.collect(root, now=collector.SEASON_START, profile='season',
                          client=archive.ArchiveClient(root, session=session))
    assert first['collection_profile'] == 'pilot'
    assert path.read_bytes() == before and not session.calls


@pytest.mark.parametrize('status,exit_code', [('ok', 0), ('no_games', 0), ('outside_season', 0), ('partial', 2), ('failed', 2)])
def test_cli_explicit_season_profile_and_status(monkeypatch, capsys, status, exit_code):
    called = []

    def fake_collect(root, **kwargs):
        called.append(kwargs)
        return {'run_id': 'synthetic', 'status': status, 'counts': {}}

    monkeypatch.setattr(collector, 'collect', fake_collect)
    monkeypatch.setattr('sys.argv', ['weather_revision_collector', '--root', '.', '--profile', 'season'])
    assert collector.main() == exit_code
    assert called == [{'profile': 'season'}]
    assert json.loads(capsys.readouterr().out)['status'] == status
