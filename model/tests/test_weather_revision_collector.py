"""Synthetic end-to-end collection and failure paths; no external requests."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import threading

import pytest

from ncaaf_model import revision_archive as archive
from ncaaf_model import weather_revision_collector as collector
from ncaaf_model.weather_revision_weather import SINGLE_URL, PREVIOUS_URL


START = datetime(2026,9,11,9,tzinfo=timezone.utc)
KICKOFF = datetime(2026,9,12,18,tzinfo=timezone.utc)


def event(game_id=401,kickoff=KICKOFF,venue_id='3793'):
    game_id = int(game_id)
    return {'id':str(game_id),'competitions':[{'id':str(game_id),'date':kickoff.isoformat(),
        'dateValid':True,'status':{'type':{'state':'pre'}},'neutralSite':False,
        'venue':{'id':venue_id},'competitors':[
            {'homeAway':'home','team':{'id':str(1000+game_id*2),'displayName':f'Home {game_id} University'}},
            {'homeAway':'away','team':{'id':str(1001+game_id*2),'displayName':f'Away {game_id} University'}}]}]}


def summary(game):
    return {'header':{'id':game['id'],'competitions':deepcopy(game['competitions'])},
        'gameInfo':{'venue':deepcopy(game['competitions'][0]['venue'])}}


def provider(game,provider_id=None):
    c = game['competitions'][0]
    names = {p['homeAway']:p['team']['displayName'] for p in c['competitors']}
    return {'id':provider_id or int(game['id'])+10000,'date':c['date'],
        'home':names['home'],'away':names['away'],'status':'pending','league':{'name':'NCAA Football'},
        'bookmakers':{book:[{'name':'Totals','updatedAt':(START-timedelta(hours=1)).isoformat(),
            'odds':[{'hdp':50.5,'over':1.95357,'under':1.91111}]}] for book in ('DraftKings','FanDuel')}}


def weather_payload(params):
    suffix = '_previous_day2' if 'previous_day2' in params['hourly'] else ''
    if suffix:
        start = datetime.fromisoformat(params['start_date']).replace(tzinfo=timezone.utc)
        stop = datetime.fromisoformat(params['end_date']).replace(tzinfo=timezone.utc)+timedelta(days=1)
        count = int((stop-start).total_seconds()/3600)
    else:
        start = datetime.fromisoformat(params['run']).replace(tzinfo=timezone.utc)
        count = 192
    return {'latitude':params['latitude'],'longitude':params['longitude'],'elevation':200.,
        'utc_offset_seconds':0,'timezone':'GMT','timezone_abbreviation':'GMT',
        'hourly_units':{'time':'iso8601','temperature_2m'+suffix:'°F',
            'relative_humidity_2m'+suffix:'%','wind_speed_10m'+suffix:'mp/h'},
        'hourly':{'time':[(start+timedelta(hours=i)).strftime('%Y-%m-%dT%H:%M') for i in range(count)],
            'temperature_2m'+suffix:[60.]*count,'relative_humidity_2m'+suffix:[70.]*count,
            'wind_speed_10m'+suffix:[10.]*count}}


class Clock:
    def __init__(self,value):
        self.value = value
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            value = self.value
            self.value += timedelta(milliseconds=100)
        return value.isoformat().replace('+00:00','Z')


class Response:
    def __init__(self,payload,status=200,remaining=100):
        self.status_code = status
        self.content = json.dumps(payload,allow_nan=False).encode()
        self.headers = {'Content-Type':'application/json','X-RateLimit-Remaining':str(remaining)}


class SyntheticSession:
    def __init__(self,root,games):
        self.root = root
        self.games = {g['id']:deepcopy(g) for g in games}
        self.inventory = {'events':deepcopy(games)}
        self.feed = {str(int(g['id'])+10000):provider(g) for g in games}
        self.summary_counts = {}
        self.calls = []
        self.before_context = None
        self.bad_weather = False
        self.summary_invalid = False
        self.recheck_change = None
        self.selected = ['DraftKings','FanDuel']
        self.remaining = 100
        self.status_by_url = {}

    def get(self,url,params,**kwargs):
        self.calls.append((url,deepcopy(params)))
        status = self.status_by_url.get(url,200)
        if url == collector.SCOREBOARD:
            payload = self.inventory
        elif url == collector.SUMMARY:
            frozen = list((self.root/'data/runtime/weather_revisions/cohorts').glob('*.json'))
            assert frozen, 'Cohort must be persisted before context/weather availability is observed'
            if self.before_context:
                self.before_context(json.loads(frozen[-1].read_text()))
            game_id = str(params['event'])
            number = self.summary_counts.get(game_id,0)+1
            self.summary_counts[game_id] = number
            payload = summary(self.games[game_id])
            if self.summary_invalid:
                payload = []
            elif number % 2 == 0 and self.recheck_change:
                self.recheck_change(payload)
        elif url.startswith(collector.VENUES):
            payload = {'id':url.rsplit('/',1)[-1],'indoor':False}
        elif url in {SINGLE_URL,PREVIOUS_URL}:
            payload = weather_payload(params)
            if self.bad_weather and url == SINGLE_URL:
                payload['hourly_units']['wind_speed_10m'] = 'km/h'
        elif url == collector.ODDS+'/bookmakers/selected':
            payload = {'bookmakers':self.selected}
        elif url == collector.ODDS+'/events':
            payload = list(self.feed.values())
        elif url == collector.ODDS+'/odds/multi':
            payload = [self.feed[identity] for identity in params['eventIds'].split(',')]
        else:
            raise AssertionError('Unexpected external endpoint: '+url)
        return Response(payload,status,self.remaining)


@pytest.fixture
def environment(tmp_path,monkeypatch):
    root = tmp_path/'project'/'model'
    root.mkdir(parents=True)
    for relative in ('ncaaf_model/weather_revision_collector.py','ncaaf_model/weather_revision_weather.py',
                     'ncaaf_model/revision_archive.py','ncaaf_model/teams.py','reports/WEATHER_REVISION_CAPTURE_PROTOCOL.md'):
        path = root/relative
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('Synthetic provenance fixture; no executable source or observations.\n')
    catalog = {'schema_version':'weather-venues-v1','venues':{'3793':{'latitude':41.66,'longitude':-91.55}},
               'coordinates_source':'https://example.invalid/frozen-synthetic-catalog'}
    path = root/'data/models/weather_venues_v1.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(catalog))
    monkeypatch.setattr(collector,'CATALOG_SHA',hashlib.sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr(collector,'load_dotenv',lambda path:None)
    monkeypatch.setenv('ODDS_API_IO_KEY','synthetic-test-token')
    for name in ('GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT','GITHUB_EVENT_NAME','GITHUB_SHA'):
        monkeypatch.delenv(name,raising=False)
    clock = Clock(START)
    monkeypatch.setattr(collector,'timestamp',clock)
    monkeypatch.setattr(archive,'timestamp',clock)
    return root,clock


def run(root,games=None,configure=None,now=START):
    session = SyntheticSession(root,games or [event()])
    if configure:
        configure(session)
    client = archive.ArchiveClient(root,session=session)
    result = collector.collect(root,now=now,client=client)
    return result,session,client


def test_end_to_end_freezes_cohort_then_weather_context_recheck_and_fresh_quotes(environment):
    root,_ = environment
    result,session,client = run(root)
    assert result['status'] == 'ok'
    assert result['counts']['cohort_games'] == result['counts']['paired_two_book_games'] == 1
    assert result['counts']['mature_comparator_games'] == 1
    row = result['rows'][0]
    assert row['context_recheck_ok']
    assert {q['sportsbook'] for q in row['quotes']} == {'draftkings','fanduel'}
    assert row['quotes'][0]['over_decimal_odds'] == 1.95357
    assert row['quotes'][0]['under_decimal_odds'] == 1.91111
    assert all(p['weather_to_quote_seconds'] >= 0 for p in row['pairs'])
    weather = [r for r in client.receipts if r['purpose'].startswith('weather_')]
    recheck = [r for r in client.receipts if r['purpose']=='official_context_after_weather']
    quotes = [r for r in client.receipts if r['purpose']=='fresh_totals_after_weather']
    assert max(collector._time(r['received_at']) for r in weather) <= min(collector._time(r['requested_at']) for r in recheck)
    assert max(collector._time(r['received_at']) for r in recheck) <= min(collector._time(r['requested_at']) for r in quotes)
    frozen = json.loads((root/result['cohort_path']).read_text())
    assert [g['game_id'] for g in frozen['games']] == [r['game_id'] for r in result['rows']]
    assert not (root/'ledger').exists()
    for receipt in client.receipts:
        loaded = archive.load_envelope(root,receipt['receipt_path'])
        assert loaded['receipt']['body_sha256'] == receipt['body_sha256']


def test_weather_coverage_cannot_replace_games_beyond_the_frozen_cap(environment,monkeypatch):
    root,_ = environment
    monkeypatch.setattr(collector,'MAX_GAMES',2)
    games = [event(403,KICKOFF+timedelta(hours=2)),event(402,KICKOFF+timedelta(hours=1),'9999'),event(401)]
    result,session,_ = run(root,games)
    assert result['counts']['cohort_games'] == 2
    assert [r['game_id'] for r in result['rows']] == ['401','402']
    assert {e['game_id'] for e in result['cohort_exclusions']} == {'403'}
    assert 'venue_not_in_frozen_catalog' in result['rows'][1]['missingness']
    assert not any(url==collector.SUMMARY and p['event']=='403' for url,p in session.calls)
    assert result['rows'][1]['quotes']  # missing weather does not suppress quote collection
    assert not result['rows'][1]['pairs']


def test_two_games_in_one_venue_share_single_request_only_within_capture(environment):
    root,clock = environment
    games = [event(401),event(402,KICKOFF+timedelta(hours=2))]
    first,s1,_ = run(root,games)
    assert sum(url==SINGLE_URL for url,_ in s1.calls) == 1
    assert first['counts']['paired_two_book_games'] == 2
    later = START+timedelta(hours=1)
    clock.value = later
    second,s2,_ = run(root,games,now=later)
    assert sum(url==SINGLE_URL for url,_ in s2.calls) == 1
    assert first['rows'][0]['single_run']['receipt_path'] != second['rows'][0]['single_run']['receipt_path']


def test_repeated_invocation_cannot_overwrite_manifest_or_make_more_requests(environment):
    root,_ = environment
    first,_,_ = run(root)
    path = root/'data/runtime/weather_revisions/runs'/f"{first['run_id']}-{first['run_attempt']}.json"
    original = path.read_bytes()
    session = SyntheticSession(root,[event()])
    with pytest.raises(ValueError,match='Invocation already archived'):
        collector.collect(root,now=START,client=archive.ArchiveClient(root,session=session))
    assert not session.calls
    assert path.read_bytes()==original


def test_day2_cache_keeps_original_mature_receipt_and_does_not_refetch(environment):
    root,clock = environment
    first,s1,_ = run(root)
    original = first['rows'][0]['previous_day2']
    assert original and not original['cached']
    assert sum(url==PREVIOUS_URL for url,_ in s1.calls) == 1
    later = START+timedelta(hours=1)
    clock.value = later
    second,s2,_ = run(root,now=later)
    cached = second['rows'][0]['previous_day2']
    assert cached['cached']
    assert cached['receipt_path'] == original['receipt_path']
    assert cached['measurement']['received_at'] == original['measurement']['received_at']
    assert not any(url==PREVIOUS_URL for url,_ in s2.calls)


def test_comparator_cache_chooses_earliest_instant_not_lexical_timestamp(environment):
    root,clock = environment
    first,session,_ = run(root)
    original = first['rows'][0]['previous_day2']
    spec = original['request_spec']
    # The original whole-second ISO timestamp sorts after this later fractional
    # timestamp lexically. Choosing chronology must parse the actual instants.
    original_time = collector._time(original['measurement']['received_at'])
    assert original_time.microsecond==0
    clock.value = original_time+timedelta(milliseconds=10)
    client = archive.ArchiveClient(root,session=session)
    later = client.fetch(spec['source_url'],spec['parameters'],purpose='synthetic_later_comparator')
    manifest = deepcopy(first)
    manifest['rows'][0]['previous_day2']['receipt_path'] = later['receipt']['receipt_path']
    archive.immutable_json(root/'data/runtime/weather_revisions/runs/zz-later.json',manifest)
    cached = collector._cached_comparators(root,root/'data/runtime/weather_revisions')
    assert cached[spec['request_key']]['receipt_path']==original['receipt_path']


@pytest.mark.parametrize('change',['kickoff','team_id','venue','state'])
def test_context_change_after_weather_keeps_quotes_but_prevents_pairing(environment,change):
    root,_ = environment
    def configure(session):
        def mutate(payload):
            c = payload['header']['competitions'][0]
            if change=='kickoff': c['date'] = (KICKOFF+timedelta(minutes=30)).isoformat()
            elif change=='team_id': c['competitors'][0]['team']['id'] = '99999'
            elif change=='venue': payload['gameInfo']['venue']['id'] = '9999'
            else: c['status']['type']['state'] = 'in'
        session.recheck_change = mutate
    result,_,_ = run(root,configure=configure)
    row = result['rows'][0]
    assert result['status'] == 'partial'
    assert row['single_run'] and row['quotes'] and not row['pairs']
    assert 'context_changed_or_recheck_failed' in row['missingness']


def test_weather_parse_failure_is_partial_with_retained_fresh_quotes_and_archive(environment):
    root,_ = environment
    result,_,client = run(root,configure=lambda s:setattr(s,'bad_weather',True))
    assert result['status']=='partial'
    row = result['rows'][0]
    assert row['single_run'] is None and row['quotes'] and not row['pairs']
    assert 'single_run_validation_failed' in row['missingness']
    assert result['counts']['failed_requests']==0  # parsing error is distinct from HTTP failure
    assert any(r['purpose']=='weather_single_run' for r in client.receipts)


@pytest.mark.parametrize('when',[collector.START-timedelta(seconds=1),collector.END])
def test_outside_pilot_performs_no_http_and_archives_explicit_skip(environment,when):
    root,_ = environment
    result,session,_ = run(root,now=when)
    assert result['status']=='outside_pilot'
    assert session.calls==[]
    assert result['counts']['total_requests']==0
    paths = list((root/'data/runtime/weather_revisions/runs').glob('*.json'))
    assert len(paths)==1 and json.loads(paths[0].read_text())['status']=='outside_pilot'


def test_low_quota_stops_before_odds_batch_and_reports_partial(environment):
    root,_ = environment
    result,session,_ = run(root,configure=lambda s:setattr(s,'remaining',collector.QUOTA_RESERVE))
    assert result['status']=='partial'
    assert not any(url==collector.ODDS+'/odds/multi' for url,_ in session.calls)
    assert any(f['reason']=='selected_books_quota_reserve' for f in result['failures'])


def test_quote_requested_before_final_context_recheck_cannot_be_paired(environment,monkeypatch):
    root,_ = environment
    original = collector.collect_quotes
    def stale_request(client,rows,key,start):
        quotes,failures = original(client,rows,key,start)
        rechecked = collector._time(rows[0]['context_recheck_received_at'])
        for quote in quotes:
            quote['requested_at'] = (rechecked-timedelta(milliseconds=50)).isoformat()
        return quotes,failures
    monkeypatch.setattr(collector,'collect_quotes',stale_request)
    result,_,_ = run(root)
    row = result['rows'][0]
    assert row['single_run'] and row['context_recheck_ok'] and row['quotes']
    assert not row['pairs']
    assert result['counts']['paired_games']==0


def test_failed_inventory_is_failed_not_empty_success(environment):
    root,_ = environment
    result,session,_ = run(root,configure=lambda s:s.status_by_url.update({collector.SCOREBOARD:503}))
    assert result['status']=='failed'
    assert result['counts']['failed_requests']==1
    assert len(session.calls)==1
    assert not list((root/'data/runtime/weather_revisions/cohorts').glob('*.json'))


def test_malformed_summary_becomes_explicit_partial_not_uncaught_exception(environment):
    root,_ = environment
    result,_,_ = run(root,configure=lambda s:setattr(s,'summary_invalid',True))
    assert result['status']=='partial'
    assert 'official_context_invalid' in result['rows'][0]['missingness']
    assert result['rows'][0]['quotes']


def test_true_empty_inventory_and_malformed_rows_have_different_status(environment):
    root,_ = environment
    result,_,_ = run(root,configure=lambda s:setattr(s,'inventory',{'events':[]}))
    assert result['status']=='no_games'
    rows,exclusions,enumerated = collector.parse_cohort({'events':[None]},START)
    assert not rows and exclusions and enumerated==0


def test_official_cohort_cap_and_duplicate_identity_are_independent_of_source_coverage():
    games = [event(1000+i,KICKOFF+timedelta(seconds=i)) for i in range(151)]
    rows,exclusions,enumerated = collector.parse_cohort({'events':list(reversed(games))},START)
    assert len(rows)==150 and enumerated==151
    assert rows[0]['game_id']=='1000'
    assert exclusions[-1]['game_id']=='1150'
    rows,exclusions,_ = collector.parse_cohort({'events':[event(),event()]},START)
    assert not rows
    assert exclusions==[{'game_id':'401','reason':'duplicate_official_event'}]


def quote_fixture():
    game = collector.parse_cohort({'events':[event()]},START)[0][0]
    quote = provider(event())
    receipt = {'requested_at':START.isoformat(),'received_at':(START+timedelta(seconds=1)).isoformat(),
               'receipt_path':'data/runtime/weather_revisions/receipts/quote.json','body_sha256':'a'*64}
    return quote,{str(quote['id']):game},receipt


def test_each_odds_response_is_restricted_to_its_requested_batch(environment):
    root,_ = environment
    games = [event(500+i,KICKOFF+timedelta(minutes=i)) for i in range(11)]
    rows = collector.parse_cohort({'events':games},START)[0]
    session = SyntheticSession(root,games)
    get = session.get
    def extra_events(url,params,**kwargs):
        result = get(url,params,**kwargs)
        if url==collector.ODDS+'/odds/multi':
            return Response(list(session.feed.values()))  # provider erroneously repeats other-batch events
        return result
    session.get = extra_events
    client = archive.ArchiveClient(root,session=session)
    quotes,failures = collector.collect_quotes(client,rows,'synthetic-test-token',START)
    assert not failures
    assert len(quotes)==22
    assert len({q['quote_id'] for q in quotes})==22
    assert all(sum(q['game_id']==row['game_id'] for q in quotes)==2 for row in rows)
    assert sum(url==collector.ODDS+'/odds/multi' for url,_ in session.calls)==2


@pytest.mark.parametrize('alias',[50.,'50.0','050.000'])
def test_numeric_equivalent_duplicate_lines_are_ambiguous(alias):
    payload,targets,receipt = quote_fixture()
    payload['bookmakers']['DraftKings'][0]['odds'] = [
        {'hdp':50,'over':1.95,'under':1.91},{'hdp':alias,'over':1.96,'under':1.92}]
    quotes,failures = collector.parse_quote_pairs(payload,targets,receipt)
    assert not [q for q in quotes if q['sportsbook']=='draftkings']
    assert [q for q in quotes if q['sportsbook']=='fanduel']
    assert failures


@pytest.mark.parametrize('change',['kickoff','home','duplicate_event','duplicate_market','partial_pair'])
def test_quote_identity_and_same_book_same_line_pair_are_required(change):
    payload,targets,receipt = quote_fixture()
    if change=='kickoff': payload['date'] = (KICKOFF+timedelta(minutes=1)).isoformat()
    elif change=='home': payload['home'] = 'Unrelated Home University'
    elif change=='duplicate_event': payload = [payload,deepcopy(payload)]
    elif change=='duplicate_market': payload['bookmakers']['DraftKings'] *= 2
    else: del payload['bookmakers']['DraftKings'][0]['odds'][0]['under']
    quotes,failures = collector.parse_quote_pairs(payload,targets,receipt)
    if change in {'duplicate_market','partial_pair'}:
        assert not [q for q in quotes if q['sportsbook']=='draftkings']
    else:
        assert not quotes
    assert failures


@pytest.mark.parametrize('status,expected',[('ok',0),('no_games',0),('outside_pilot',0),('partial',2),('failed',2)])
def test_cli_exit_code_preserves_partial_or_failed_pipeline_state(monkeypatch,capsys,status,expected):
    monkeypatch.setattr(collector,'collect',lambda root:{'run_id':'synthetic','status':status,'counts':{}})
    monkeypatch.setattr('sys.argv',['weather_revision_collector','--root','.'])
    assert collector.main()==expected
    assert json.loads(capsys.readouterr().out)['status']==status
