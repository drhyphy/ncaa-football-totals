"""Run the frozen prospective revision study, separately from existing policies.

The only forecasts permitted here belong to this scheduled collector invocation.
Historical captures can supply training data, but cannot acquire predictions.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from ncaaf_model import weather_revision_dataset as dataset
from ncaaf_model import weather_revision_study as study
from ncaaf_model.revision_archive import immutable_json, load_envelope
from ncaaf_model.weather_revision_evaluation import evaluate
from weather_revision_model_inventory import inventory, load_runs, profile
from weather_revision_study_labels import collect_labels, load_latest_labels

BASE = Path('model/data/runtime/weather_revision_study')
PROTOCOL = Path('model/reports/weather_revision_prospective_protocol.json')
PROTOCOL_SHA = '6c19b41a585e40b3b4bef8e2b3e3203347e3ad9c9209d8ef98e509b8d2daae3f'
TARGETS = {'probability': 'final_total', 'movement': 'market_movement'}
SOURCES = ['scripts/weather_revision_study.py', 'scripts/weather_revision_study_labels.py',
           'scripts/weather_revision_model_inventory.py',
           'model/ncaaf_model/weather_revision_dataset.py',
           'model/ncaaf_model/weather_revision_study.py',
           'model/ncaaf_model/weather_revision_evaluation.py']


def clock():
    return datetime.now(timezone.utc)


def atomic_status(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.study-', delete=False, mode='w') as f:
        temporary = Path(f.name)
        json.dump(value, f, sort_keys=True, indent=2, allow_nan=False)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, path)


def provenance(root):
    body = (root / PROTOCOL).read_bytes()
    if hashlib.sha256(body).hexdigest() != PROTOCOL_SHA:
        raise ValueError('Frozen prospective protocol changed')
    protocol = json.loads(body)
    for relative, expected in protocol['source_sha256'].items():
        if hashlib.sha256((root / 'model' / relative).read_bytes()).hexdigest() != expected:
            raise ValueError('Frozen study source changed: ' + relative)
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    hashes = {}
    for relative in SOURCES:
        body = (root / relative).read_bytes()
        committed = subprocess.check_output(['git', 'show', commit + ':' + relative], cwd=root,
                                            stderr=subprocess.DEVNULL)
        if body != committed:
            raise ValueError('Study implementation must be committed before real execution: ' + relative)
        hashes[relative] = hashlib.sha256(body).hexdigest()
    return protocol, {'git_commit': commit, 'source_sha256': hashes, 'protocol_sha256': PROTOCOL_SHA}


def read_record(path, *, named_by_digest=False):
    value = json.loads(path.read_text())
    if named_by_digest and path.stem != study.digest(value):
        raise ValueError('Immutable study record digest mismatch: ' + path.name)
    return value


def persist_observation(base, observation):
    expected = study.digest({k: v for k, v in observation.items() if k != 'observation_id'})
    if observation['observation_id'] != expected:
        raise ValueError('Observation content address mismatch')
    immutable_json(base / 'observations' / (expected + '.json'), observation)


def load_observations(base):
    result, games = [], set()
    for path in sorted((base / 'observations').glob('*.json')):
        o = read_record(path)
        if (o['observation_id'] != path.stem or path.stem != study.digest({k: v for k, v in o.items() if k != 'observation_id'})
                or str(o['game_id']) in games):
            raise ValueError('Changed or duplicate designated observation')
        games.add(str(o['game_id']))
        result.append(o)
    return result


def scheduled_runs(runs):
    chosen = []
    for run in runs:
        m = run['manifest']
        _, begin, end = profile(m)
        if m.get('trigger') == 'schedule' and begin <= study.utc(m['capture_started_at']) < end:
            chosen.append(run)
    return sorted(chosen, key=lambda r: (study.utc(r['manifest']['capture_started_at']),
                                         str(r['manifest']['run_id']), str(r['manifest']['run_attempt'])))


def load_artifact(base, name, cutoff):
    directory = base / 'fits' / study.iso(cutoff).replace(':', '-') / name
    artifact_path, receipt_path = directory / 'artifact.json', directory / 'availability.json'
    if not artifact_path.exists():
        return None, None
    artifact = read_record(artifact_path)
    if artifact['cutoff'] != study.iso(cutoff) or artifact['target_kind'] != TARGETS[name]:
        raise ValueError('Stored artifact belongs to another week or target')
    if artifact['protocol_sha256'] != PROTOCOL_SHA:
        raise ValueError('Stored artifact uses another protocol')
    if not receipt_path.exists():
        return artifact, None  # Interrupted publication is unavailable, never backdated.
    receipt = read_record(receipt_path)
    if receipt['artifact_sha256'] != study.digest(artifact):
        raise ValueError('Stored artifact changed after availability receipt')
    return artifact, receipt['available_at']


def fit_week(base, observations, latest, *, cutoff, source, now=clock):
    states = {}
    for name, target in TARGETS.items():
        artifact, available = load_artifact(base, name, cutoff)
        directory = base / 'fits' / study.iso(cutoff).replace(':', '-') / name
        if artifact is None:
            artifact = study.fit_pair(observations, latest[target], cutoff=cutoff,
                                      target_kind=target, protocol_sha256=PROTOCOL_SHA)
            artifact['implementation'] = source
            immutable_json(directory / 'artifact.json', artifact)
        if available is None:
            # The model body already exists; this actual timestamp cannot precede its write.
            available = study.iso(now())
            immutable_json(directory / 'availability.json', {'artifact_sha256': study.digest(artifact),
                                                            'available_at': available})
        states[name] = {k: artifact[k] for k in ('status', 'games', 'completed_weeks', 'cutoff')}
        states[name]['available_at'] = available
    return states


def lock_decision(base, decision, observation, *, run_id, run_attempt, event_name,
                  labels_only, source, revalidate, now=clock):
    gid = str(decision['game_id'])
    if not re.fullmatch(r'[1-9][0-9]*', gid):
        raise ValueError('Noncanonical designated game identifier')
    path = base / 'decisions' / (gid + '.json')
    if path.exists():
        saved = read_record(path)
        identity = ('game_id', 'run_id', 'run_attempt', 'kickoff', 'capture_started_at')
        if (any(saved['designation'][k] != decision[k] for k in identity) or
                (observation and saved.get('observation_id') and saved['observation_id'] != observation['observation_id'])):
            raise ValueError('A previously locked designation changed')
        return saved
    result = {'schema_version': 'weather-revision-decision-v1', 'designation': decision,
              'observation_id': observation['observation_id'] if observation else None,
              'recorded_at': study.iso(now()), 'implementation': source,
              'invocation': {'run_id': str(run_id), 'run_attempt': str(run_attempt), 'event_name': event_name},
              'targets': {}}
    live = (not labels_only and event_name == 'schedule' and str(decision['run_id']) == str(run_id)
            and str(decision['run_attempt']) == str(run_attempt))
    for name, target in TARGETS.items():
        entry = {'status': 'unavailable', 'prediction': None, 'paper': None}
        try:
            if observation is None:
                raise ValueError('designated_inputs_unavailable')
            if not live:
                raise ValueError('not_same_live_scheduled_invocation')
            if name == 'probability' and observation['reference_line'] % 1 != .5:
                raise ValueError('probability_requires_half_point_contract')
            artifact, available = load_artifact(base, name, study.weekly_cutoff(now()))
            if artifact is None or available is None:
                raise ValueError('weekly_artifact_not_yet_available')
            prediction = study.make_prediction(observation, artifact, available_at=available,
                                                recorded_at=now, protocol_sha256=PROTOCOL_SHA)
            # A new decode and hash check of the original batch and context after inference.
            checked = revalidate()
            if checked != observation:
                raise ValueError('original_offer_or_context_changed_after_inference')
            paper = study.paper_selection(observation, prediction, locked_at=now) if name == 'probability' else None
            entry.update(status='forecast_recorded', prediction=prediction, paper=paper,
                         paper_status='locked' if paper else ('no_positive_modeled_ev' if name == 'probability' else 'not_a_betting_policy'))
        except (ValueError, KeyError, TypeError, OSError, EOFError) as error:
            entry['reason'] = str(error)
        result['targets'][name] = entry
    # The movement model must not backdate an earlier probability offer lock.
    probability = result['targets']['probability']
    if probability.get('paper'):
        try:
            if revalidate() != observation:
                raise ValueError('original_offer_or_context_changed_before_atomic_lock')
            probability['paper'] = study.paper_selection(observation, probability['prediction'], locked_at=now)
        except (ValueError, KeyError, TypeError, OSError, EOFError) as error:
            probability.update(paper=None, paper_status='abstained', paper_reason=str(error))
    immutable_json(path, result)
    return result


def score_archive(base, latest, *, cutoff):
    forecasts, papers = [], []
    for path in sorted((base / 'decisions').glob('*.json')):
        record = read_record(path)
        for name, entry in record['targets'].items():
            prediction, paper = entry.get('prediction'), entry.get('paper')
            if not prediction:
                continue
            label = latest[TARGETS[name]].get(prediction['observation_id'])
            grade = study.grade_prediction(prediction, label, paper) if label else None
            if grade:
                immutable_json(base / 'grades' / (study.digest(grade) + '.json'), grade)
            item = {'prediction': prediction, 'label': label, 'grade': grade}
            forecasts.append(item)
            if paper:
                papers.append({**item, 'paper': paper})
    result = evaluate(forecasts, papers, report_cutoff=cutoff)
    if study.utc(cutoff) >= study.REPORT_AT:
        immutable_json(base / 'final_evaluation.json', result)
    else:
        atomic_status(base / 'interim_evaluation.json', result)
    return result, forecasts, papers


def execute(root, *, run_id, run_attempt, labels_only=False, now=clock, event_name=None):
    root = Path(root).resolve()
    base = root / BASE
    started = study.utc(now())
    event_name = os.environ.get('GITHUB_EVENT_NAME', 'manual_local') if event_name is None else event_name
    status = {'schema_version': 'weather-revision-study-status-v1',
              'generated_at': study.iso(started), 'protocol_id': 'weather-revision-prospective-v1-20260909',
              'status': 'collecting', 'observations': {'total': 0, 'probability': 0, 'movement': 0},
              'models': {n: {'status': 'unavailable', 'games': 0, 'completed_weeks': 0, 'cutoff': None} for n in TARGETS},
              'forecasts': {'probability': 0, 'movement': 0},
              'paper': {'locked': 0, 'settled': 0, 'unresolved': 0, 'profit_units': None},
              'experimental_picks': [],
              'evidence': {'edge_established': False, 'prospective': True}, 'errors': []}
    try:
        protocol, source = provenance(root)
        runs, integrity, failures = load_runs(root, verify=False)
        status['errors'].extend(failures)
        ordered = scheduled_runs(runs)
        by_identity = {(str(r['manifest']['run_id']), str(r['manifest']['run_attempt'])): i for i, r in enumerate(ordered)}
        if len(by_identity) != len(ordered):
            raise ValueError('Duplicate scheduled capture identity')
        def envelope(relative):
            result = load_envelope(root / 'model', relative)
            integrity.receipt(relative)
            return result
        def verify_run(run):
            if run['integrity'].get('deferred'):
                run['integrity'] = integrity.verify(root / run['path'], run['manifest'], run['cohort'])
        def process(d):
            o = None
            rebuild = None
            if d['source_receipt_verified_input_eligible']:
                i = by_identity[(str(d['run_id']), str(d['run_attempt']))]
                if i < 1:
                    raise ValueError('Designated pair has no immediate predecessor')
                previous, current = ordered[i - 1], ordered[i]
                def rebuild(d=d, previous=previous, current=current):
                    return dataset.build_observation(d, previous, current, envelope=envelope)
                try:
                    o = rebuild()
                    persist_observation(base, o)
                except (ValueError, KeyError, TypeError, OSError, EOFError) as error:
                    status['errors'].append({'game_id': d['game_id'], 'stage': 'observation', 'reason': str(error)})
            lock_decision(base, d, o, run_id=run_id, run_attempt=run_attempt, event_name=event_name,
                          labels_only=labels_only, source=source, revalidate=rebuild, now=now)
        # Cheap complete enumeration preserves first designation and every failed-run barrier.
        # Only this invocation and its immediate predecessor need full validation before inference.
        current_key = (str(run_id), str(run_attempt))
        if not labels_only and event_name == 'schedule' and current_key in by_identity:
            index = by_identity[current_key]
            verify_run(ordered[index])
            if index:
                verify_run(ordered[index - 1])
            live = inventory(runs, integrity.receipt, complete_enumeration=not failures,
                             validate_run_identities={current_key}, include_diagnostics=False)
            for d in live['decisions']:
                if (str(d['run_id']), str(d['run_attempt'])) == current_key:
                    process(d)
        # All historical source checks, reconstruction, fitting and grading occur afterward.
        for run in runs:
            verify_run(run)
        selections = inventory(runs, integrity.receipt, complete_enumeration=not failures)
        for d in selections['decisions']:
            process(d)
        observations = load_observations(base)
        status['observations'] = {'total': len(observations),
                                  'probability': sum(o['reference_line'] % 1 == .5 for o in observations),
                                  'movement': len(observations)}
        collected = collect_labels(root, observations, ordered, envelope=envelope, now=now(),
                                   complete_enumeration=not failures)
        status['labels'] = collected['counts']
        status['coverage_gaps'] = collected.get('unavailable_targets', [])
        status['errors'].extend(collected['errors'])
        cutoff = study.weekly_cutoff(now())
        training = load_latest_labels(root, cutoff=cutoff)
        status['errors'].extend(training['errors'])
        if started < study.END:
            status['models'] = fit_week(base, observations, training, cutoff=cutoff, source=source, now=now)
        report_cutoff = min(study.utc(now()), study.REPORT_AT)
        latest = load_latest_labels(root, cutoff=report_cutoff, inclusive=True)
        status['errors'].extend(latest['errors'])
        evaluation, forecasts, papers = score_archive(base, latest, cutoff=report_cutoff)
        status['forecasts'] = {name: sum(f['prediction']['target_kind'] == target for f in forecasts)
                               for name, target in TARGETS.items()}
        settled = [p for p in papers if p['grade'] is not None]
        status['paper'] = {'locked': len(papers), 'settled': len(settled), 'unresolved': len(papers) - len(settled),
                           'profit_units': sum(p['grade']['profit_units'] for p in settled) if settled else None}
        obs_by_id = {o['observation_id']: o for o in observations}
        for row in papers:
            paper = row['paper']
            if study.utc(paper['kickoff']) > study.utc(now()):
                o = obs_by_id[paper['observation_id']]
                status['experimental_picks'].append({**paper,
                    'home_team': o['context']['home_team'], 'away_team': o['context']['away_team']})
        status['experimental_picks'].sort(key=lambda p: (p['kickoff'], p['game_id']))
        status['status'] = ('evaluation_complete' if report_cutoff == study.REPORT_AT else
                            'fitted' if any(m['status'] == 'fitted' for m in status['models'].values()) else 'collecting')
        status['report_path'] = ('model/data/runtime/weather_revision_study/final_evaluation.json' if report_cutoff == study.REPORT_AT
                                 else 'model/data/runtime/weather_revision_study/interim_evaluation.json')
        status['implementation'] = source
    except (ValueError, KeyError, TypeError, OSError, EOFError, subprocess.CalledProcessError) as error:
        status['errors'].append({'stage': 'runner', 'reason': str(error)})
    if status['errors']:
        status['status'] = 'attention'
    status['generated_at'] = study.iso(now())
    atomic_status(root / 'site/data/weather-revision-study.json', status)
    invocation = {'run_id': str(run_id), 'run_attempt': str(run_attempt), 'event_name': event_name,
                  'labels_only': labels_only, 'started_at': study.iso(started), 'status': status}
    immutable_json(base / 'runs' / (study.digest(invocation) + '.json'), invocation)
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--run-id', default=os.environ.get('GITHUB_RUN_ID', 'local'))
    parser.add_argument('--run-attempt', default=os.environ.get('GITHUB_RUN_ATTEMPT', '1'))
    parser.add_argument('--labels-only', action='store_true')
    args = parser.parse_args()
    result = execute(args.root, run_id=args.run_id, run_attempt=args.run_attempt, labels_only=args.labels_only)
    print(json.dumps({k: result[k] for k in ('status', 'observations', 'models', 'forecasts', 'paper', 'errors')}, indent=2))
    return 1 if result['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
