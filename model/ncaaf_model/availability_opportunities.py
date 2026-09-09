"""Designate phase opportunities from verified metadata, before input coverage.

No HTTP, prices, player statuses, labels or fitted artifacts are read here.
Callers supply every archived scheduled invocation in chronological scope,
including failures, and verify original receipts, source code and the bounded
ACC-only official game contexts. The boolean below is an explicit caller trust
boundary, not a replacement for those archive checks.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import re
from zoneinfo import ZoneInfo

from .availability_sources import canonical_name
from .revision_archive import digest_json

VERSION = 'availability-opportunity-designation-v1'
PHASES = {'Initial': 'initial', 'Game Day': 'gameday'}
ORDER = {'initial': 0, 'gameday': 1}
ZONE = ZoneInfo('America/New_York')


def _time(value):
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError) as error:
        raise ValueError('invalid_opportunity_time') from error
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('aware_opportunity_time_required')
    return result.astimezone(timezone.utc)


def _iso(value):
    return _time(value).isoformat().replace('+00:00', 'Z')


def _id(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not re.fullmatch('[1-9][0-9]*', str(value)):
        raise ValueError('canonical_game_or_team_id_required')
    return str(value)


def _contexts(rows):
    if not isinstance(rows, list) or len(rows) > 20:
        raise ValueError('bounded_verified_context_list_required')
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('invalid_verified_context')
        gid, home, away = (_id(row[k]) for k in ('game_id', 'home_id', 'away_id'))
        if gid in result or home == away:
            raise ValueError('duplicate_context_identity')
        names = {}
        for side in ('home', 'away'):
            aliases = row[side + '_source_names']
            aliases = list(aliases.values()) if isinstance(aliases, dict) else aliases
            if not isinstance(aliases, list) or not aliases or any(not isinstance(x, str) or not canonical_name(x) for x in aliases):
                raise ValueError('invalid_official_aliases')
            names[side] = {canonical_name(x) for x in aliases}
        result[gid] = {'context': deepcopy(row), 'kickoff': _time(row['kickoff']), 'names': names}
    return result


def _match_metadata(report, contexts):
    """Read team/phase/footer metadata only; deliberately never read `rows`."""
    if not isinstance(report, dict):
        return None, 'invalid_report_object'
    blocks = report.get('games')
    if not isinstance(blocks, list) or len(blocks) != 2:
        return None, 'invalid_team_blocks'
    try:
        names = [canonical_name(block['teamName']) for block in blocks]
    except (KeyError, TypeError, ValueError):
        return None, 'invalid_source_team_names'
    if not all(names) or names[0] == names[1]:
        return None, 'invalid_source_team_names'
    matched = []
    for gid, row in contexts.items():
        official = row['names']
        if ((names[0] in official['home'] and names[1] in official['away']) or
                (names[1] in official['home'] and names[0] in official['away'])):
            matched.append(gid)
    footer = report.get('footer')
    if not matched:
        return None, 'missing_or_ambiguous_official_pair'
    if not isinstance(footer, dict) or report.get('conferenceTimeZone') != 'ET':
        return None, 'source_clock_context_mismatch'
    timed = []
    for gid in matched:
        local = contexts[gid]['kickoff'].astimezone(ZONE)
        if footer.get('date') == local.strftime('%Y-%m-%d') and footer.get('time') == local.strftime('%H:%M:%S'):
            timed.append(gid)
    if len(timed) != 1:
        return None, 'source_clock_context_mismatch' if not timed else 'missing_or_ambiguous_official_pair'
    return timed[0], None


def designate_opportunities(runs):
    """Return permanent first scheduled (game,phase) designations and snapshots.

    Each run needs run_id/run_attempt/trigger, capture_started_at/completed_at,
    source_receipt_verified=True, game_contexts, raw_reports (dict or None),
    report_received_at and report_receipt (both nullable for a failed source).
    Provenance identifiers are preserved; caller verifies bytes and completeness.
    A missing source never creates a phase, but its scheduled game snapshot stays
    in history so a preceding quote cannot silently skip a failed invocation.
    """
    if not isinstance(runs, list) or any(not isinstance(run, dict) for run in runs):
        raise ValueError('complete_archived_run_list_required')
    identities = set()
    prepared = []
    for run in runs:
        if run.get('source_receipt_verified') is not True:
            raise ValueError('caller_must_verify_source_receipts')
        identity = tuple(run.get(k) for k in ('run_id', 'run_attempt'))
        if any(not isinstance(x, str) or not re.fullmatch('[A-Za-z0-9_-]+', x) for x in identity) or identity in identities:
            raise ValueError('duplicate_or_invalid_capture_identity')
        identities.add(identity)
        start, end = _time(run['capture_started_at']), _time(run['capture_completed_at'])
        if start > end or run.get('trigger') not in ('schedule', 'workflow_dispatch', 'manual_local'):
            raise ValueError('invalid_capture_clocks_or_trigger')
        received = run.get('report_received_at')
        if received is not None and not start <= _time(received) <= end:
            raise ValueError('report_receipt_outside_capture')
        if (run.get('raw_reports') is not None and (not isinstance(run['raw_reports'], dict)
                or received is None or not isinstance(run.get('report_receipt'), str))):
            raise ValueError('original_source_metadata_required')
        prepared.append((start, identity, run, _contexts(run['game_contexts'])))
    prepared.sort(key=lambda item: (item[0], item[1]))
    # The live workflow has serialized captures. Overlapping scheduled runs
    # leave no unique previous observation; do not silently invent that order.
    scheduled = [row for row in prepared if row[2]['trigger'] == 'schedule']
    for previous, current in zip(scheduled, scheduled[1:]):
        if _time(previous[2]['capture_completed_at']) > current[0]:
            raise ValueError('overlapping_scheduled_invocations')

    designations, snapshots, diagnostics = [], [], []
    chosen, anchors, previous_game_snapshot, known_contexts = set(), {}, {}, {}
    for _, identity, run, contexts in prepared:
        matches = {gid: [] for gid in contexts}
        reports = run.get('raw_reports') or {}
        if any(not isinstance(key, str) for key in reports):
            raise ValueError('source_report_keys_must_be_strings')
        for key, report in sorted(reports.items()):
            gid, reason = _match_metadata(report, contexts)
            raw_phase = report.get('ReportType') if isinstance(report, dict) else None
            if reason:
                diagnostics.append({'run_id': identity[0], 'run_attempt': identity[1], 'source_key': key, 'reason': reason})
            elif gid:
                matches[gid].append({'source_key': key, 'raw_phase': raw_phase,
                                     'phase': PHASES.get(raw_phase) if isinstance(raw_phase, str) else None})
        # A failed/empty inventory cannot let a previously observed game skip
        # this scheduled invocation in its previous-quote chronology. Do not
        # expire at the old kickoff: a subsequently rescheduled game must retain
        # the intervening missing observation too. These are metadata barriers,
        # not extra current inventory targets or HTTP requests.
        visible = dict(contexts)
        if run['trigger'] == 'schedule':
            for gid, info in known_contexts.items():
                if gid not in visible:
                    visible[gid] = info
            known_contexts.update(contexts)
        for gid, info in sorted(visible.items(), key=lambda pair: (pair[1]['kickoff'], int(pair[0]))):
            phases = matches.get(gid, [])
            sid = digest_json({'version': VERSION, 'run_id': identity[0], 'run_attempt': identity[1], 'game_id': gid})
            snapshot = {'snapshot_id': sid, 'run_id': identity[0], 'run_attempt': identity[1], 'trigger': run['trigger'],
                'game_id': gid, 'kickoff': _iso(info['kickoff']), 'report_receipt': run.get('report_receipt'),
                'report_received_at': _iso(run['report_received_at']) if run.get('report_received_at') is not None else None,
                'matched_report_metadata': phases, 'current_context_available': gid in contexts,
                'preceding_scheduled_snapshot_id': previous_game_snapshot.get(gid)}
            snapshots.append(snapshot)
            if run['trigger'] != 'schedule':
                continue
            previous_game_snapshot[gid] = sid
            for phase in ORDER:
                selected = [row for row in phases if row['phase'] == phase]
                if not selected or (gid, phase) in chosen:
                    continue
                chosen.add((gid, phase))
                anchors.setdefault(gid, snapshot['kickoff'])
                record = {'schema_version': VERSION, 'game_id': gid, 'phase': phase,
                    'group_kickoff': anchors[gid], 'kickoff': snapshot['kickoff'],
                    'snapshot_id': sid, 'run_id': identity[0], 'run_attempt': identity[1],
                    'report_receipt': snapshot['report_receipt'], 'report_received_at': snapshot['report_received_at'],
                    'source_keys': [row['source_key'] for row in selected],
                    'metadata_status': 'designated' if len(selected) == 1 else 'ambiguous_same_phase_reports',
                    'before_kickoff': _time(snapshot['report_received_at']) < info['kickoff'],
                    'preceding_scheduled_snapshot_id': snapshot['preceding_scheduled_snapshot_id'],
                    'context': deepcopy(info['context']), 'player_rows_examined': False, 'quotes_examined': False}
                record['designation_id'] = digest_json(record)
                designations.append(record)
    return {'schema_version': VERSION, 'designations': designations, 'snapshots': snapshots, 'diagnostics': diagnostics,
            'counts': {'runs': len(prepared), 'scheduled_runs': len(scheduled), 'designated_opportunities': len(designations),
                       'designated_games': len(anchors), 'ambiguous_opportunities': sum(d['metadata_status'] != 'designated' for d in designations)},
            'limitations': ['Caller must enumerate and verify original archives and ACC conference contexts.',
                            'Designation precedes player/role/price/artifact coverage; it is not a forecast or bet.']}
