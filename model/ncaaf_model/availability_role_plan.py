"""Pure, label-free planning from already observed official ESPN payloads.

No network, source receipts, role caches, player classification or outcomes are
read here. The caller must retain and verify the payloads' original receipts.
JSON pointers identify source fields; they do not certify receipt timing.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import re

from .availability_role_archive import validate_url

VERSION = 'availability-role-request-plan-v1'
MAX_TARGETS = 20
SEASON = 2026
SEASON_START = '2026-08-01T00:00:00Z'
SEASON_END = '2027-02-01T00:00:00Z'
PRIOR_SELECTION = 'latest dated entry in observed prior-game list'


class Invalid(ValueError):
    """A fixed, non-source-derived exclusion reason."""


def _id(value):
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]*', value):
        raise Invalid('invalid_canonical_id')
    return value


def _time(value):
    if not isinstance(value, str):
        raise Invalid('invalid_aware_date')
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise Invalid('invalid_aware_date') from None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise Invalid('invalid_aware_date')
    return stamp.astimezone(timezone.utc)


def _iso(value):
    return value.isoformat().replace('+00:00', 'Z')


def _season(value):
    year = value.get('year') if isinstance(value, dict) else value
    if type(year) is not int or year != SEASON:
        raise Invalid('season_mismatch_or_invalid')


def _competition(value, game_id, kickoff):
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise Invalid('nonunique_competition')
    competition = value[0]
    if _id(competition.get('id')) != game_id or _time(competition.get('date')) != kickoff:
        raise Invalid('competition_identity_or_date_mismatch')
    state = competition.get('status', {}).get('type', {})
    if (not isinstance(state, dict) or state.get('state') != 'pre'
            or state.get('completed') is True or competition.get('dateValid') is False):
        raise Invalid('not_verified_pregame')
    competitors = competition.get('competitors')
    if not isinstance(competitors, list) or len(competitors) != 2:
        raise Invalid('nonunique_home_away')
    sides = {}
    for index, person in enumerate(competitors):
        if not isinstance(person, dict) or person.get('homeAway') not in ('home', 'away'):
            raise Invalid('nonunique_home_away')
        side = person['homeAway']
        team = person.get('team')
        if side in sides or not isinstance(team, dict):
            raise Invalid('nonunique_home_away')
        team_id = _id(team.get('id'))
        if 'id' in person and _id(person['id']) != team_id:
            raise Invalid('competitor_team_id_mismatch')
        sides[side] = (team_id, index, team)
    if sides['home'][0] == sides['away'][0]:
        raise Invalid('nonunique_home_away')
    return competition, sides


def _target(event, as_of):
    if not isinstance(event, dict):
        raise Invalid('invalid_event')
    game_id, kickoff = _id(event.get('id')), _time(event.get('date'))
    _season(event.get('season'))
    if kickoff <= as_of:
        raise Invalid('not_future')
    competition, sides = _competition(event.get('competitions'), game_id, kickoff)
    groups = competition.get('groups')
    if (competition.get('conferenceCompetition') is not True or not isinstance(groups, dict)
            or _id(groups.get('id')) != '1' or groups.get('name') != 'Atlantic Coast Conference'
            or groups.get('isConference') is not True
            or any(_id(team.get('conferenceId')) != '1' for _, _, team in sides.values())):
        raise Invalid('not_verified_acc_conference_matchup')
    return {'game_id': game_id, 'kickoff': _iso(kickoff), 'season': SEASON,
            'home_id': sides['home'][0], 'away_id': sides['away'][0]}, sides


def _summary(payload, target):
    if not isinstance(payload, dict) or not isinstance(payload.get('header'), dict):
        raise Invalid('missing_or_invalid_summary')
    header = payload['header']
    if _id(header.get('id')) != target['game_id'] or header.get('timeValid') is False:
        raise Invalid('summary_identity_or_time_mismatch')
    _season(header.get('season'))
    _, sides = _competition(header.get('competitions'), target['game_id'], _time(target['kickoff']))
    if any(sides[side][0] != target[side+'_id'] for side in ('home', 'away')):
        raise Invalid('summary_team_mismatch')


def _link(links, *, kind, team_id=None, game_id=None):
    if not isinstance(links, list):
        raise Invalid('missing_'+kind+'_link')
    found = {}
    for index, item in enumerate(links):
        if not isinstance(item, dict):
            continue
        rel = item.get('rel', [])
        rel = rel if isinstance(rel, list) else []
        href = item.get('href')
        if 'app' in rel or (isinstance(href, str) and href.startswith('sportscenter:')):
            continue
        relevant = kind in rel if kind == 'roster' else ('gamecast' in rel or item.get('text') == 'Gamecast')
        if not relevant:
            continue
        if not isinstance(href, str):
            raise Invalid('invalid_'+kind+'_link')
        found.setdefault(href, []).append(index)
    if len(found) != 1:
        raise Invalid(('missing_' if not found else 'ambiguous_')+kind+'_link')
    url, indices = next(iter(found.items()))
    try:
        validate_url(url, kind=kind, team_id=team_id, game_id=game_id)
    except ValueError:
        raise Invalid('invalid_'+kind+'_link') from None
    return url, indices


def _prior(summary, team_id, as_of):
    groups = summary.get('lastFiveGames')
    if not isinstance(groups, list):
        raise Invalid('missing_prior_game_list')
    matched = []
    for index, group in enumerate(groups):
        if not isinstance(group, dict) or not isinstance(group.get('team'), dict):
            continue
        try:
            matches = _id(group['team'].get('id')) == team_id
        except Invalid:
            matches = False
        if matches:
            matched.append((index, group))
    if len(matched) != 1:
        raise Invalid('missing_or_ambiguous_prior_team_group')
    group_index, group = matched[0]
    events = group.get('events')
    if not isinstance(events, list):
        raise Invalid('missing_prior_game_list')
    dated = []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise Invalid('undated_prior_entry_prevents_selection')
        try:
            when = _time(event.get('gameDate'))
        except Invalid:
            # An undated entry might be the newest; do not silently select an
            # older event merely because it has easier metadata.
            raise Invalid('undated_prior_entry_prevents_selection') from None
        if _time(SEASON_START) <= when < _time(SEASON_END) and when < as_of:
            dated.append((when, index, event))
    if not dated:
        raise Invalid('no_current_season_prior_entry')
    latest = max(item[0] for item in dated)
    designated = [item for item in dated if item[0] == latest]
    if len(designated) != 1:
        raise Invalid('ambiguous_latest_prior_date')
    when, event_index, event = designated[0]
    # Designation above deliberately precedes identity and link validity.
    game_id, home_id, away_id = map(_id, (event.get('id'), event.get('homeTeamId'), event.get('awayTeamId')))
    duplicates = sum(1 for row in events if isinstance(row, dict) and str(row.get('id')) == game_id)
    if duplicates != 1 or home_id == away_id or team_id not in (home_id, away_id):
        raise Invalid('ambiguous_or_nonparticipating_prior_identity')
    if 'opponent' in event:
        opponent = event['opponent']
        other = away_id if team_id == home_id else home_id
        if not isinstance(opponent, dict) or _id(opponent.get('id')) != other:
            raise Invalid('prior_opponent_identity_mismatch')
    season_basis = 'fixed_date_window_inference'
    if 'season' in event:
        _season(event['season'])
        season_basis = 'source_explicit_season'
    url, indices = _link(event.get('links'), kind='gamecast', team_id=team_id, game_id=game_id)
    pointer = f'/lastFiveGames/{group_index}/events/{event_index}'
    return {'game_id': game_id, 'home_id': home_id, 'away_id': away_id, 'game_date': _iso(when),
            'season': SEASON, 'season_basis': season_basis, 'selection': PRIOR_SELECTION,
            'completed_status_verified': False}, url, pointer, indices


def plan_roles(inventory_payload, summaries, *, as_of, season=SEASON):
    """Return bounded deduplicated requests and exclusions; never fetch URLs.

    ``summaries`` maps canonical game IDs (integer or string) to original
    summary objects. All JSON pointers are relative to their named original
    payload. Target designation/cap precedes summary, roster and prior checks.
    """
    now = _time(as_of)
    if type(season) is not int or season != SEASON:
        raise ValueError('This frozen planner supports season 2026 only')
    if not isinstance(inventory_payload, dict) or not isinstance(inventory_payload.get('events'), list) or not isinstance(summaries, dict):
        raise ValueError('Inventory events list and summary mapping required')
    lookup, duplicate_summaries = {}, set()
    for key, value in summaries.items():
        key = _id(key)
        if key in lookup:
            duplicate_summaries.add(key)
        lookup[key] = value
    events = inventory_payload['events']
    id_counts = Counter(str(event.get('id')) for event in events if isinstance(event, dict))
    exclusions, candidates = [], []

    def exclude(stage, reason, game_id=None, team_id=None, pointer=None):
        exclusions.append({'stage': stage, 'reason': reason, 'game_id': game_id,
                           'team_id': team_id, 'pointer': pointer})

    for index, event in enumerate(events):
        pointer, game_id = f'/events/{index}', None
        try:
            if isinstance(event, dict):
                game_id = _id(event.get('id'))
            if id_counts[game_id] != 1:
                raise Invalid('duplicate_inventory_game_id')
            target, sides = _target(event, now)
            candidates.append((target, sides, pointer))
        except (Invalid, AttributeError, TypeError) as error:
            exclude('inventory', str(error) if isinstance(error, Invalid) else 'malformed_inventory_metadata', game_id, pointer=pointer)
    candidates.sort(key=lambda row: (_time(row[0]['kickoff']), int(row[0]['game_id'])))
    for target, _, pointer in candidates[MAX_TARGETS:]:
        exclude('target_cap', 'twenty_game_cap', target['game_id'], pointer=pointer)
    retained = candidates[:MAX_TARGETS]
    plans, keys, conflicts = [], {}, set()

    def add(plan):
        key = (plan['kind'], plan['team_id'], plan['game_id'])
        if key in conflicts:
            exclude('deduplication', 'conflicting_prior_context', plan['target_game_ids'][0], plan['team_id'])
            return
        if key not in keys:
            keys[key] = plan
            plans.append(plan)
            return
        existing = keys[key]
        identity_fields = ('game_id', 'home_id', 'away_id', 'game_date', 'season')
        context_conflict = any(existing.get('prior_context', {}).get(field) != plan.get('prior_context', {}).get(field)
                               for field in identity_fields)
        if existing['url'] != plan['url'] or context_conflict:
            plans.remove(existing)
            conflicts.add(key)
            for game in existing['target_game_ids']+plan['target_game_ids']:
                exclude('deduplication', 'conflicting_prior_context', game, plan['team_id'])
            return
        for game in plan['target_game_ids']:
            if game not in existing['target_game_ids']:
                existing['target_game_ids'].append(game)
        existing['provenance'].extend(plan['provenance'])
        if plan.get('prior_context', {}).get('season_basis') == 'source_explicit_season':
            existing['prior_context']['season_basis'] = 'source_explicit_season'

    for target, sides, pointer in retained:
        game = target['game_id']
        try:
            if game in duplicate_summaries:
                raise Invalid('ambiguous_summary_mapping')
            summary = lookup.get(game)
            _summary(summary, target)
        except (Invalid, AttributeError, TypeError) as error:
            reason = str(error) if isinstance(error, Invalid) else 'malformed_summary_metadata'
            for side in ('home', 'away'):
                exclude('summary', reason, game, target[side+'_id'], '/header')
            continue
        context = {'source': 'summary', 'summary_game_id': game, 'target_game_id': game, 'pointer': '/header/competitions/0'}
        for side in ('home', 'away'):
            team, competitor_index, team_payload = sides[side]
            links_pointer = pointer+f'/competitions/0/competitors/{competitor_index}/team/links'
            try:
                url, indices = _link(team_payload.get('links'), kind='roster', team_id=team)
                add({'kind': 'roster', 'url': url, 'team_id': team, 'game_id': None, 'target_game_ids': [game],
                     'provenance': [context]+[{'source': 'inventory', 'target_game_id': game, 'pointer': links_pointer+f'/{index}/href'} for index in indices]})
            except Invalid as error:
                exclude('roster', str(error), game, team, links_pointer)
            try:
                prior, url, prior_pointer, indices = _prior(summary, team, now)
                add({'kind': 'gamecast', 'url': url, 'team_id': team, 'game_id': prior['game_id'],
                     'target_game_ids': [game], 'prior_context': prior,
                     'provenance': [context, {'source': 'summary', 'summary_game_id': game, 'target_game_id': game,
                                             'pointer': prior_pointer, 'season_basis': prior['season_basis']}]+
                        [{'source': 'summary', 'summary_game_id': game, 'target_game_id': game, 'pointer': prior_pointer+f'/links/{index}/href'} for index in indices]})
            except Invalid as error:
                exclude('prior', str(error), game, team, '/lastFiveGames')
    return {'schema_version': VERSION, 'as_of': _iso(now), 'season': season,
            'season_window': {'start_inclusive': SEASON_START, 'end_exclusive': SEASON_END},
            'targets': [target for target, _, _ in retained], 'plans': plans, 'exclusions': exclusions,
            'counts': {'inventory_events': len(events), 'verified_future_acc_games': len(candidates),
                       'targets': len(retained), 'excess_targets': max(0, len(candidates)-MAX_TARGETS),
                       'roster_requests': sum(p['kind']=='roster' for p in plans),
                       'prior_team_game_requests': sum(p['kind']=='gamecast' for p in plans)},
            'limitations': ['Original payload receipts and their availability must be verified by the caller.',
                            'Prior selection is the '+PRIOR_SELECTION+'; list completeness and completed status are not established.',
                            'Missing explicit prior season uses the fixed operational date window, not source season certification.',
                            'No outcomes, player roles, future starters or model eligibility are inferred.']}
