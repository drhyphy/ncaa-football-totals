from copy import deepcopy
import json

import pytest
import requests

from ncaaf_model.availability_role_plan import plan_roles, PRIOR_SELECTION

AS_OF = '2026-09-09T10:00:00Z'


def roster(team):
    return {'rel': ['roster', 'desktop', 'team'],
            'href': f'https://www.espn.com/college-football/team/roster/_/id/{team}'}


def gamecast(game):
    return {'text': 'Gamecast', 'href': f'https://www.espn.com/college-football/game/_/gameId/{game}/away-home'}


def event(game='100', home='183', away='25', date='2026-09-12T19:30Z'):
    competitors = [{'id': team, 'homeAway': side, 'team': {'id': team, 'conferenceId': '1', 'links': [roster(team)]}}
                   for side, team in [('home', home), ('away', away)]]
    return {'id': game, 'date': date, 'season': {'year': 2026}, 'competitions': [
        {'id': game, 'date': date, 'dateValid': True, 'conferenceCompetition': True,
         'groups': {'id': '1', 'name': 'Atlantic Coast Conference', 'isConference': True},
         'status': {'type': {'state': 'pre', 'completed': False}}, 'competitors': competitors}]}


def prior(game, team, date='2026-09-05T16:00Z', opponent='999'):
    return {'id': game, 'gameDate': date, 'homeTeamId': team, 'awayTeamId': opponent,
            'opponent': {'id': opponent}, 'links': [gamecast(game)]}


def summary(target):
    competition=deepcopy(target['competitions'][0])
    for person in competition['competitors']:
        person['team']['links']=[]  # Real header has no roster link.
    return {'header': {'id': target['id'], 'season': {'year': 2026}, 'timeValid': True,
                       'competitions': [competition]},
            'lastFiveGames': [{'team': {'id': person['team']['id']},
                              'events': [prior(str(1000+int(person['team']['id'])), person['team']['id'])]}
                             for person in competition['competitors']]}


def fixture():
    target=event()
    return {'events': [target]}, {target['id']: summary(target)}


def run(inventory=None, summaries=None, **kwargs):
    if inventory is None:
        inventory,summaries=fixture()
    return plan_roles(inventory,summaries,as_of=kwargs.pop('as_of',AS_OF),**kwargs)


def selected(result, kind='gamecast', team='183'):
    return [p for p in result['plans'] if p['kind']==kind and p['team_id']==team]


def reason(result, value, team=None):
    return any(e['reason']==value and (team is None or e['team_id']==team) for e in result['exclusions'])


def pointer(payload, path):
    for key in path.strip('/').split('/'):
        payload=payload[int(key)] if isinstance(payload,list) else payload[key]
    return payload


def test_observed_routes_provenance_and_transport_free_pure_result(monkeypatch):
    inventory,summaries=fixture()
    original=deepcopy((inventory,summaries))
    monkeypatch.setattr(requests,'Session',lambda:pytest.fail('Planner performed HTTP'))
    result=run(inventory,summaries)
    assert result['counts']=={'inventory_events':1,'verified_future_acc_games':1,'targets':1,'excess_targets':0,
                              'roster_requests':2,'prior_team_game_requests':2}
    assert not result['exclusions'] and (inventory,summaries)==original
    for plan in result['plans']:
        assert plan['target_game_ids']==['100']
        values=[]
        for ref in plan['provenance']:
            source=inventory if ref['source']=='inventory' else summaries[ref['summary_game_id']]
            values.append(pointer(source,ref['pointer']))
        assert plan['url'] in values
        if plan['kind']=='gamecast':
            assert plan['prior_context']['selection']==PRIOR_SELECTION
            assert plan['prior_context']['completed_status_verified'] is False
            assert plan['prior_context']['season_basis']=='fixed_date_window_inference'


@pytest.mark.parametrize('path,value',[
    (('season','year'),2025), (('date',),'2026-09-09T10:00Z'),
    (('competitions',0,'date'),'2026-09-12T19:31Z'),
    (('competitions',0,'id'),'101'), (('competitions',0,'dateValid'),False),
    (('competitions',0,'conferenceCompetition'),False),
    (('competitions',0,'groups','id'),'2'),
    (('competitions',0,'groups','name'),'ACC'),
    (('competitions',0,'groups','isConference'),1),
    (('competitions',0,'competitors',1,'team','conferenceId'),'2'),
    (('competitions',0,'competitors',1,'homeAway'),'home'),
    (('competitions',0,'competitors',1,'team','id'),'183'),
    (('competitions',0,'competitors',0,'id'),'25'),
    (('competitions',0,'status','type','state'),'post'),
    (('competitions',0,'status','type','completed'),True),
    (('competitions',0,'status'),None), (('id',),100.)])
def test_invalid_official_target_excluded_before_details(path,value):
    inventory,summaries=fixture()
    current=inventory['events'][0]
    for key in path[:-1]: current=current[key]
    current[path[-1]]=value
    result=run(inventory,summaries)
    assert not result['targets'] and not result['plans'] and result['exclusions']


@pytest.mark.parametrize('path,value',[
    (('header','id'),'101'), (('header','season','year'),2025),
    (('header','timeValid'),False), (('header','competitions',0,'date'),'2026-09-12T20:30Z'),
    (('header','competitions',0,'status','type','state'),'in'),
    (('header','competitions',0,'status','type','completed'),True),
    (('header','competitions',0,'competitors',1,'team','id'),'555'),
    (('header','competitions',0,'competitors',1,'homeAway'),'home')])
def test_mismatched_or_started_summary_blocks_both_team_plans(path,value):
    inventory,summaries=fixture()
    current=summaries['100']
    for key in path[:-1]: current=current[key]
    current[path[-1]]=value
    result=run(inventory,summaries)
    assert len(result['targets'])==1 and not result['plans']
    assert {e['team_id'] for e in result['exclusions']}=={'25','183'}
    assert all(e['stage']=='summary' for e in result['exclusions'])


def test_missing_and_duplicate_summary_keys_are_not_silently_substituted():
    inventory,summaries=fixture()
    assert reason(run(inventory,{}),'missing_or_invalid_summary','183')
    summaries[100]=deepcopy(summaries['100'])
    assert not run(inventory,summaries)['plans']
    assert reason(run(inventory,summaries),'ambiguous_summary_mapping')


def test_target_order_duplicate_ids_and_twenty_target_cap_precede_details():
    targets=[event(str(100+i),str(10+i),str(1000+i),f'2026-09-{10+i//5:02d}T12:00Z') for i in range(23)]
    inventory={'events':list(reversed(targets))}
    summaries={row['id']:summary(row) for row in targets}
    summaries.pop('100')  # This target still consumes one of the first twenty.
    result=run(inventory,summaries)
    assert [g['game_id'] for g in result['targets']]==[str(100+i) for i in range(20)]
    assert result['counts']['excess_targets']==3
    assert len([e for e in result['exclusions'] if e['stage']=='target_cap'])==3
    assert not any('120' in p['target_game_ids'] for p in result['plans'])
    inventory['events'].append(deepcopy(targets[0]))
    result=run(inventory,summaries)
    assert not any(g['game_id']=='100' for g in result['targets'])
    assert len([e for e in result['exclusions'] if e['reason']=='duplicate_inventory_game_id'])==2


def test_literal_unique_roster_ignores_app_links_and_deduplicates_identical_href():
    inventory,summaries=fixture()
    links=inventory['events'][0]['competitions'][0]['competitors'][0]['team']['links']
    links.extend([deepcopy(links[0]),{'rel':['roster','app'],'href':'sportscenter://private?token=ignored'}])
    result=run(inventory,summaries)
    assert len(selected(result,'roster'))==1
    assert len([p for p in selected(result,'roster')[0]['provenance'] if p['source']=='inventory'])==2
    links.append({'rel':['roster'],'href':'https://www.espn.com/college-football/team/roster/_/id/25'})
    result=run(inventory,summaries)
    assert not selected(result,'roster') and reason(result,'ambiguous_roster_link','183')
    # The independently designated prior remains usable despite missing roster.
    assert len(selected(result))==1


@pytest.mark.parametrize('url',[
    'http://www.espn.com/college-football/team/roster/_/id/183',
    'https://www.espn.com/college-football/team/roster/_/id/25',
    'https://www.espn.com/college-football/team/roster/_/id/183?token=x',
    'https://wrong.invalid/college-football/team/roster/_/id/183'])
def test_wrong_or_nonliteral_roster_is_not_constructed(url):
    inventory,summaries=fixture()
    inventory['events'][0]['competitions'][0]['competitors'][0]['team']['links'][0]['href']=url
    result=run(inventory,summaries)
    assert not selected(result,'roster') and reason(result,'invalid_roster_link','183')


@pytest.mark.parametrize('mutation,expected',[
    (lambda e:e.update(links=[]),'missing_gamecast_link'),
    (lambda e:e.update(homeTeamId='777'),'ambiguous_or_nonparticipating_prior_identity'),
    (lambda e:e.update(id='001'),'invalid_canonical_id'),
    (lambda e:e.update(opponent={'id':'888'}),'prior_opponent_identity_mismatch'),
    (lambda e:e.update(season={'year':2025}),'season_mismatch_or_invalid'),
    (lambda e:e['links'][0].update(href='https://www.espn.com/college-football/game/_/gameId/999/away-home'),'invalid_gamecast_link')])
def test_latest_designation_before_identity_or_link_checks_prevents_earlier_fallback(mutation,expected):
    inventory,summaries=fixture()
    events=summaries['100']['lastFiveGames'][0]['events']
    events.insert(0,prior('400','183','2026-08-30T12:00Z'))
    mutation(events[-1])
    result=run(inventory,summaries)
    assert not selected(result) and reason(result,expected,'183')
    assert len(selected(result,team='25'))==1


def test_tied_dates_duplicate_ids_and_unknown_dates_never_select_earlier_game():
    inventory,summaries=fixture()
    events=summaries['100']['lastFiveGames'][0]['events']
    events.append(prior('401','183'))
    assert reason(run(inventory,summaries),'ambiguous_latest_prior_date','183')
    events[-1]=prior(events[0]['id'],'183','2026-08-30T12:00Z')
    assert reason(run(inventory,summaries),'ambiguous_or_nonparticipating_prior_identity','183')
    events[-1]=prior('401','183','unknown')
    assert reason(run(inventory,summaries),'undated_prior_entry_prevents_selection','183')


def test_date_window_boundaries_asof_exclusion_and_explicit_season():
    inventory,summaries=fixture()
    events=summaries['100']['lastFiveGames'][0]['events']
    events[:]=[prior('500','183','2026-07-31T23:59:59Z'),prior('501','183','2026-08-01T00:00:00Z'),
               prior('502','183',AS_OF),prior('503','183','2027-02-01T00:00:00Z')]
    result=run(inventory,summaries)
    assert selected(result)[0]['game_id']=='501'
    events[1]['season']={'year':2026}
    assert selected(run(inventory,summaries))[0]['prior_context']['season_basis']=='source_explicit_season'
    events.pop(1)
    assert reason(run(inventory,summaries),'no_current_season_prior_entry','183')


def test_missing_or_ambiguous_prior_groups_are_per_team():
    inventory,summaries=fixture()
    groups=summaries['100']['lastFiveGames']
    groups.append(deepcopy(groups[0]))
    result=run(inventory,summaries)
    assert not selected(result) and selected(result,team='25')
    assert reason(result,'missing_or_ambiguous_prior_team_group','183')
    groups[:]=[groups[1]]
    assert reason(run(inventory,summaries),'missing_or_ambiguous_prior_team_group','183')


def test_deduplicate_team_rosters_and_prior_pairs_across_targets_retaining_all_pointers():
    first,second=event(),event('101',date='2026-09-19T19:30Z')
    result=run({'events':[second,first]},{'100':summary(first),'101':summary(second)})
    assert len(result['plans'])==4
    for plan in result['plans']:
        assert plan['target_game_ids']==['100','101']
        assert {p['target_game_id'] for p in plan['provenance']}=={'100','101'}


def test_conflicting_deduplicated_prior_context_is_excluded_for_all_targets():
    first,second=event(),event('101',date='2026-09-19T19:30Z')
    summaries={'100':summary(first),'101':summary(second)}
    summaries['101']['lastFiveGames'][0]['events'][0]['gameDate']='2026-09-05T17:00Z'
    result=run({'events':[first,second]},summaries)
    assert not selected(result) and len(selected(result,'roster'))==1
    assert {e['game_id'] for e in result['exclusions'] if e['reason']=='conflicting_prior_context'}=={'100','101'}


def test_agreeing_explicit_season_corroboration_does_not_create_deduplication_conflict():
    first,second=event(),event('101',date='2026-09-19T19:30Z')
    summaries={'100':summary(first),'101':summary(second)}
    summaries['101']['lastFiveGames'][0]['events'][0]['season']={'year':2026}
    result=run({'events':[first,second]},summaries)
    plan=selected(result)[0]
    assert plan['target_game_ids']==['100','101'] and not result['exclusions']
    assert plan['prior_context']['season_basis']=='source_explicit_season'
    assert {p.get('season_basis') for p in plan['provenance'] if 'season_basis' in p}=={
        'fixed_date_window_inference','source_explicit_season'}


class OutcomePoison(dict):
    def get(self,key,*args):
        if key.lower() in {'score','gameresult','hometeamscore','awayteamscore','winner','completed','status'}:
            raise AssertionError('Outcome field inspected')
        return super().get(key,*args)


def test_outcome_poison_and_input_order_cannot_change_prior_choice_or_escape_into_output():
    inventory,summaries=fixture()
    baseline=run(inventory,summaries)
    for group in summaries['100']['lastFiveGames']:
        group['events']=[OutcomePoison(dict(row,score='SENSITIVE',gameResult='SENSITIVE',homeTeamScore=999)) for row in group['events']]
        group['events'].reverse()
    result=run(inventory,summaries)
    assert result==baseline and 'SENSITIVE' not in json.dumps(result)


@pytest.mark.parametrize('as_of,season',[
    ('2026-09-09T10:00:00',2026), ('nonsense',2026), (AS_OF,2027), (AS_OF,True), (AS_OF,'2026')])
def test_invalid_planner_parameters_rejected(as_of,season):
    with pytest.raises(ValueError): run(as_of=as_of,season=season)


def test_timezone_equivalence_and_large_canonical_id_preservation():
    inventory,summaries=fixture()
    assert run(inventory,summaries,as_of='2026-09-09T06:00:00-04:00')==run(inventory,summaries)
    large=2**53+99
    target=event(str(large),str(large+1),str(large+2))
    result=run({'events':[target]},{large:summary(target)})
    assert result['targets'][0]['game_id']==str(large)
    assert result['targets'][0]['home_id']==str(large+1)
