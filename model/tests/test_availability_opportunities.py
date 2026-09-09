from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from ncaaf_model.availability_opportunities import designate_opportunities
from ncaaf_model.revision_archive import digest_json


def context(gid='100', kickoff='2026-09-12T19:30:00Z'):
    return {'game_id': gid, 'home_id': '183', 'away_id': '25', 'kickoff': kickoff,
            'home_source_names': {'location': 'Syracuse', 'displayName': 'Syracuse Orange'},
            'away_source_names': {'location': 'California', 'displayName': 'California Golden Bears'}}


def report(phase='Initial', rows=None):
    return {'ReportType': phase, 'games': [{'teamName': 'California', 'rows': rows},
                                         {'teamName': 'Syracuse', 'rows': rows}],
            'footer': {'date': '2026-09-12', 'time': '15:30:00'}, 'conferenceTimeZone': 'ET'}


def run(n=0, phase='Initial', trigger='schedule'):
    start = datetime(2026, 9, 9, 10, 17, tzinfo=timezone.utc) + timedelta(minutes=30*n)
    return {'run_id': str(n+1), 'run_attempt': '1', 'trigger': trigger,
            'source_receipt_verified': True, 'capture_started_at': start.isoformat(),
            'capture_completed_at': (start + timedelta(seconds=3)).isoformat(),
            'report_received_at': (start + timedelta(seconds=1)).isoformat(),
            'report_receipt': f'data/runtime/availability/receipts/{n}.json',
            'game_contexts': [context()], 'raw_reports': {'1182': report(phase)}}


@pytest.mark.parametrize('rows', [None, [], {}, [{'name': 'Missing', 'status': 'Out'}],
                                  [{'name': None, 'status': 'Novel'}], 1, 'malformed'])
def test_designation_precedes_player_row_and_coverage_validation(rows):
    first, later = run(), run(1)
    first['raw_reports']['1182'] = report(rows=rows)
    original = deepcopy([first, later])
    result = designate_opportunities([later, first])
    assert len(result['designations']) == 1
    selected = result['designations'][0]
    assert selected['run_id'] == '1' and selected['phase'] == 'initial'
    assert selected['player_rows_examined'] is selected['quotes_examined'] is False
    assert selected['designation_id'] == digest_json({k: v for k, v in selected.items() if k != 'designation_id'})
    assert [first, later] == original


def test_manual_and_pending_do_not_consume_phase_opportunity():
    result = designate_opportunities([run(0, trigger='manual_local'), run(1, 'Report Pending'), run(2)])
    assert result['designations'][0]['run_id'] == '3'
    assert result['designations'][0]['preceding_scheduled_snapshot_id'] == result['snapshots'][1]['snapshot_id']
    assert result['counts']['scheduled_runs'] == 2


def test_both_phases_first_receipt_then_initial_tie_order_same_fixed_anchor():
    first = run()
    first['raw_reports']['another'] = report('Game Day')
    result = designate_opportunities([first, run(1), run(2, 'Game Day')])
    assert [d['phase'] for d in result['designations']] == ['initial', 'gameday']
    assert len({d['group_kickoff'] for d in result['designations']}) == 1


def test_duplicate_same_phase_permanent_unavailable_no_later_substitute():
    first = run()
    first['raw_reports']['second'] = report()
    result = designate_opportunities([first, run(1)])
    assert result['counts']['ambiguous_opportunities'] == 1
    assert result['designations'][0]['metadata_status'] == 'ambiguous_same_phase_reports'
    assert result['designations'][0]['source_keys'] == ['1182', 'second']
    assert result['designations'][0]['run_id'] == '1'


def test_reschedule_preserves_group_anchor_and_current_kickoff():
    later = run(1, 'Game Day')
    later['game_contexts'][0]['kickoff'] = '2026-09-19T19:30:00Z'
    later['raw_reports']['1182']['footer']['date'] = '2026-09-19'
    result = designate_opportunities([run(), later])
    a, b = result['designations']
    assert a['group_kickoff'] == b['group_kickoff'] == '2026-09-12T19:30:00Z'
    assert b['kickoff'] == '2026-09-19T19:30:00Z'


def test_failed_source_is_immediate_preceding_snapshot():
    middle = run(1)
    middle.update(raw_reports=None, report_received_at=None, report_receipt=None)
    result = designate_opportunities([run(0, 'Report Pending'), middle, run(2)])
    assert result['designations'][0]['preceding_scheduled_snapshot_id'] == result['snapshots'][1]['snapshot_id']
    assert result['snapshots'][1]['matched_report_metadata'] == []


def test_failed_inventory_keeps_known_future_game_in_previous_quote_chronology():
    middle = run(1)
    middle.update(game_contexts=[], raw_reports=None, report_received_at=None, report_receipt=None)
    result = designate_opportunities([run(0, 'Report Pending'), middle, run(2)])
    missing = result['snapshots'][1]
    assert missing['current_context_available'] is False
    assert result['designations'][0]['preceding_scheduled_snapshot_id'] == missing['snapshot_id']


@pytest.mark.parametrize('phase', ['Report Pending', 'Update', 'initial', '', None, {}])
def test_unrecognized_phases_cannot_designate(phase):
    assert designate_opportunities([run(0, phase)])['designations'] == []


@pytest.mark.parametrize('change', ['team', 'footer', 'timezone', 'duplicate_team', 'missing_footer'])
def test_metadata_mismatch_not_falsely_canonicalized(change):
    current = run()
    r = current['raw_reports']['1182']
    if change == 'team': r['games'][0]['teamName'] = 'Cal'
    elif change == 'footer': r['footer']['time'] = '15:31:00'
    elif change == 'timezone': r['conferenceTimeZone'] = 'EST'
    elif change == 'duplicate_team': r['games'][0]['teamName'] = 'Syracuse'
    else: del r['footer']
    result = designate_opportunities([current])
    assert not result['designations'] and len(result['diagnostics']) == 1


@pytest.mark.parametrize('change', ['unverified', 'duplicate_run', 'overlap', 'clock', 'report_clock',
                                   'naive', 'duplicate_context', 'bad_team_id', 'bad_game_id', 'bad_alias'])
def test_invalid_trust_context_or_chronology_rejected(change):
    first, second = run(), run(1)
    if change == 'unverified': first['source_receipt_verified'] = False
    elif change == 'duplicate_run': second = deepcopy(first)
    elif change == 'overlap': first['capture_completed_at'] = second['capture_completed_at']
    elif change == 'clock': first['capture_completed_at'] = '2026-09-09T00:00:00Z'
    elif change == 'report_clock': first['report_received_at'] = '2026-09-09T00:00:00Z'
    elif change == 'naive': first['capture_started_at'] = '2026-09-09T10:17:00'
    elif change == 'duplicate_context': first['game_contexts'].append(context())
    elif change == 'bad_team_id': first['game_contexts'][0]['home_id'] = True
    elif change == 'bad_game_id': first['game_contexts'][0]['game_id'] = '0100'
    else: first['game_contexts'][0]['home_source_names'] = ['']
    with pytest.raises(ValueError): designate_opportunities([first, second])


def test_empty_archive_and_large_ids():
    assert designate_opportunities([])['counts']['designated_opportunities'] == 0
    current = run()
    current['game_contexts'][0]['game_id'] = '9007199254740993'
    assert designate_opportunities([current])['designations'][0]['game_id'] == '9007199254740993'


def test_footer_disambiguates_repeated_same_team_pair_but_identical_events_fail():
    current = run()
    current['game_contexts'].append(context('101', '2026-09-19T19:30:00Z'))
    assert designate_opportunities([current])['designations'][0]['game_id'] == '100'
    current['game_contexts'][1]['kickoff'] = current['game_contexts'][0]['kickoff']
    assert not designate_opportunities([current])['designations']


def test_failed_capture_after_old_kickoff_is_not_skipped_when_game_reschedules():
    captures = [run(0), run(1), run(2, 'Game Day')]
    for item, clock in zip(captures, ['18:35', '18:48', '19:18']):
        start = datetime.fromisoformat('2026-09-12T'+clock+':00+00:00')
        item.update(capture_started_at=start.isoformat(),
                    capture_completed_at=(start+timedelta(seconds=3)).isoformat(),
                    report_received_at=(start+timedelta(seconds=1)).isoformat())
    captures[0]['game_contexts'][0]['kickoff'] = '2026-09-12T18:45:00Z'
    captures[0]['raw_reports']['1182']['footer']['time'] = '14:45:00'
    captures[1].update(game_contexts=[], raw_reports=None, report_received_at=None, report_receipt=None)
    captures[2]['game_contexts'][0]['kickoff'] = '2026-09-12T20:00:00Z'
    captures[2]['raw_reports']['1182']['footer']['time'] = '16:00:00'
    result = designate_opportunities(captures)
    first, missing, last = result['snapshots']
    assert missing['run_id'] == '2' and not missing['current_context_available']
    assert last['preceding_scheduled_snapshot_id'] == missing['snapshot_id']
    assert result['designations'][1]['preceding_scheduled_snapshot_id'] != first['snapshot_id']
