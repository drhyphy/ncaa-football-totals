"""Independent original-byte audit of an ACC capture; no project imports or HTTP.

Reads only source bodies, receipts, frozen Git blobs and collection metadata.
Does not interpret player rows, label movement/outcomes, fit or grade a model.
"""
import argparse
import ast
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import unicodedata
from zoneinfo import ZoneInfo


def sha(body):
    return hashlib.sha256(body).hexdigest()


def canonical(value):
    return sha(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())


def time(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    assert result.tzinfo is not None, 'Naive time'
    return result.astimezone(timezone.utc)


def audit(root, manifest_path):
    root = Path(root).resolve(); model = root/'model'; zone = ZoneInfo('America/New_York')
    manifest_path = Path(manifest_path).resolve(); raw = manifest_path.read_bytes(); m = json.loads(raw)
    archive = model/'data/runtime/availability'
    assert manifest_path.parent == archive/'runs' and not manifest_path.is_symlink()
    assert m['schema_version'] == 'acc-availability-collector-v1' and m['models_fitted'] == m['verified_completed_reports'] == 0
    start, end = time(m['capture_started_at']), time(m['capture_completed_at'])
    assert time('2026-09-09T08:00Z') <= start < time('2026-09-16T07:00Z') and start <= end
    assert manifest_path.name == f"{m['run_id']}-{m['run_attempt']}.json"
    def original(path, folder):
        p = model/path
        assert not p.is_symlink() and p.resolve().parent == archive/folder and '..' not in Path(path).parts
        return p.read_bytes()
    blobs = {}
    for path, digest in {**{'model/'+k: v for k,v in m['source_hashes'].items()}, '.github/workflows/availability.yml': m['workflow_sha256']}.items():
        body = subprocess.check_output(['git', 'show', f"{m['git_commit']}:{path}"], cwd=root)
        assert sha(body) == digest, 'Recorded Git source mismatch: '+path
        blobs[path] = body
    tree = ast.parse(blobs['model/ncaaf_model/teams.py'])
    aliases = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'ALIASES' for t in n.targets))
    def norm(value):
        text = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode().lower().replace('&', 'and')
        key = re.sub('[^a-z0-9]+', '', text)
        return aliases.get(key, key)
    receipts, bodies, failed = {}, {}, []
    assert len(set(m['receipts'])) == len(m['receipts']) <= 27
    for reference in m['receipts']:
        r = json.loads(original(reference, 'receipts'))
        assert r['receipt_path'] == reference and Path(reference).name == canonical({k:v for k,v in r.items() if k != 'receipt_path'})+'.json'
        assert start <= time(r['requested_at']) <= time(r['received_at']) <= end
        body = gzip.decompress(original(r['body_path'], 'bodies'))
        assert sha(body) == r['body_sha256'] and len(body) == r['body_size_bytes']
        receipts[reference], bodies[reference] = r, json.loads(body)
        if r['status_code'] != 200 or r.get('body_complete', True) is not True or any(r.get(k) for k in ('transport_error','capture_error','parse_error','http_error')):
            failed.append(reference)
        request = r['request']
        assert not {'apiKey','token','authorization'} & set(request.get('params', {}))
        if request.get('method') == 'POST':
            assert request['url'].startswith('https://app.hdintelligence.com/api/')
            assert json.loads(request['body_utf8']) == request['json'] and sha(request['body_utf8'].encode()) == request['body_sha256']
    cohort_bytes = original(m['cohort_path'], 'cohorts'); assert sha(cohort_bytes) == m['cohort_sha256']
    cohort = json.loads(cohort_bytes); inventory = bodies[cohort['inventory_receipt']]; reconstructed = []
    all_ids = [str(e.get('id')) for e in inventory['events']]
    for event in inventory['events']:
        if len(event.get('competitions', [])) != 1: continue
        c = event['competitions'][0]; gid = str(event['id']); kickoff = time(c['date']); people = c.get('competitors', [])
        if not (start < kickoff <= start+timedelta(days=7)) or c['status']['type']['state'] != 'pre' or c.get('dateValid') is False: continue
        assert gid.isdigit() and int(gid)>0 and str(c['id'])==gid and len(people)==2 and {p['homeAway'] for p in people}=={'home','away'}
        if all_ids.count(gid) != 1: continue
        g = {'game_id':gid, 'kickoff':kickoff.isoformat().replace('+00:00','Z'), 'inventory_venue_id':str(c.get('venue',{}).get('id','')), 'inventory_neutral_site':c.get('neutralSite')}
        for p in people:
            side, team = p['homeAway'], p['team']; assert str(team['id']).isdigit() and int(team['id'])>0
            g.update({side+'_id':str(team['id']), side+'_team':team['displayName'], side+'_source_names':{k:v for k in ('location','displayName','shortDisplayName','abbreviation') if isinstance(v:=team.get(k),str) and v.strip()}})
        assert g['home_id'] != g['away_id']; reconstructed.append(g)
    reconstructed.sort(key=lambda g:(time(g['kickoff']),int(g['game_id'])))
    assert reconstructed[:150] == cohort['games'] and len(cohort['games']) == m['official_games']
    source_receipt = receipts[m['current_receipt']]; reports = bodies[m['current_receipt']]
    assert m['source_status']=='captured' and m['current_receipt'] not in failed
    assert time(cohort['frozen_at']) <= min(time(r['requested_at']) for r in receipts.values() if r['request'].get('method')=='POST')
    assert source_receipt['request']['url']=='https://app.hdintelligence.com/api/get-publish-public' and source_receipt['request']['json']=={'sport':'Football','organization':'ACC','conference':'ACC'}
    access = [p for p,r in receipts.items() if r['request']['url']=='https://app.hdintelligence.com/api/public-load']; assert len(access)==1 and bodies[access[0]].get('public') is True
    assert time(receipts[access[0]]['received_at']) <= time(source_receipt['requested_at'])
    report_facts, matched = [], set()
    for key,r in reports.items():
        recorded = next(x for x in m['reports'] if x['source_key']==key)
        names = [x['teamName'] for x in r['games']]; pair = set(map(norm,names)); candidates=[]
        for g in cohort['games']:
            h,a = ({norm(v) for v in g[s+'_source_names'].values()} for s in ('home','away'))
            if len(pair)==2 and any(x in h and y in a for x in pair for y in pair if x!=y): candidates.append(g)
        assert len(candidates)==1, 'Ambiguous raw report pair'; g=candidates[0]; local=time(g['kickoff']).astimezone(zone)
        assert r['conferenceTimeZone']=='ET' and r['footer']['date']==local.strftime('%Y-%m-%d') and r['footer']['time']==local.strftime('%H:%M:%S')
        assert recorded['game_id']==g['game_id'] and recorded['context_id']==canonical(g) and recorded['completion_verified'] is False
        assert recorded['state']==('pending' if r['ReportType']=='Report Pending' else 'nonpending_completion_unverified')
        assert recorded['source_row_items']==[len(t['rows']) for t in r['games']]
        for field,raw_field in (('source_publish_date','publishDate'),('source_posted_time','postedTime'),('report_type','ReportType')): assert recorded[field]==r.get(raw_field)
        matched.add(g['game_id']); report_facts.append({'source_key':key,'game_id':g['game_id'],'teams':names,'state':recorded['state'],'raw_row_lengths':recorded['source_row_items'],'source_game_date':r['footer']['date'],'source_game_time':r['footer']['time'],'source_timezone':r['conferenceTimeZone'],'source_publish_date':r.get('publishDate'),'source_posted_time':r.get('postedTime')})
    expected = [g for g in cohort['games'] if g['game_id'] in matched][:20]
    assert [r['game_id'] for r in m['rows']]==[g['game_id'] for g in expected]
    quote_facts=[]
    for row,g in zip(m['rows'],expected):
        context=row['context_receipt']; assert context==receipts[context['receipt_path']]
        h=bodies[context['receipt_path']]['header']; c=h['competitions'][0]
        assert str(h['id'])==str(c['id'])==g['game_id'] and len(h['competitions'])==1 and time(c['date'])==time(g['kickoff']) and c['status']['type']['state']=='pre' and c.get('dateValid') is not False
        assert {p['homeAway']:str(p['team']['id']) for p in c['competitors']}=={'home':g['home_id'],'away':g['away_id']} and row['context_verified'] is True
        recovered=[]
        for q in row['quotes']:
            qr=receipts[q['receipt_path']]; values=bodies[q['receipt_path']]; events=values if isinstance(values,list) else [values]
            raw_event=[e for e in events if str(e['id'])==q['provider_event_id']]; assert len(raw_event)==1; e=raw_event[0]
            assert norm(e['home'])==norm(g['home_team']) and norm(e['away'])==norm(g['away_team']) and time(e['date'])==time(g['kickoff']) and e['status'] in {'pending','upcoming','scheduled','prematch'}
            title={'draftkings':'DraftKings','fanduel':'FanDuel'}[q['sportsbook']]; markets=[x for x in e['bookmakers'][title] if x['name']=='Totals']; assert len(markets)==1
            market=markets[0]; assert market.get('period') in (None,'full_game','Full Game','FT')
            lines=[x for x in market['odds'] if float(x['hdp'])==q['line']]; assert len(lines)==1; x=lines[0]
            assert all(math.isfinite(float(x[k])) for k in ('hdp','under','over')) and min(float(x['under']),float(x['over']))>1 and float(x['hdp'])>0 and float(x['hdp'])*2==round(float(x['hdp'])*2)
            assert (float(x['under']),float(x['over']))==(q['under_decimal_odds'],q['over_decimal_odds']) and q['body_sha256']==qr['body_sha256'] and q['quote_id']==canonical({k:v for k,v in q.items() if k!='quote_id'})
            assert (time(market['updatedAt']) if market.get('updatedAt') else None)==(time(q['market_updated_at']) if q['market_updated_at'] else None)
            assert q['requested_at']==qr['requested_at'] and q['observed_at']==qr['received_at']
            assert time(source_receipt['requested_at'])<=time(source_receipt['received_at'])<=time(context['requested_at'])<=time(context['received_at'])<=time(q['requested_at'])<=time(q['observed_at'])<time(g['kickoff'])
            link=[p for p in row['pairs'] if p['quote_id']==q['quote_id']]; assert len(link)==1 and link[0]['report_receipt']==m['current_receipt'] and link[0]['quote_receipt']==q['receipt_path']
            gap=(time(q['observed_at'])-time(source_receipt['received_at'])).total_seconds(); assert abs(link[0]['report_to_quote_seconds']-gap)<1e-9
            recovered.append(q['quote_id']); quote_facts.append({k:q[k] for k in ('game_id','sportsbook','line','under_decimal_odds','over_decimal_odds','market_updated_at','requested_at','observed_at')}|{'report_to_quote_seconds':gap})
        assert sorted(recovered)==sorted(p['quote_id'] for p in row['pairs'])
    counts={'source_reports':len(reports),'pending_reports':sum(r['state']=='pending' for r in report_facts),'nonpending_unverified_reports':sum(r['state']=='nonpending_completion_unverified' for r in report_facts),'matched_games':len(expected),'context_verified_games':len(expected),'paired_games':sum(bool(r['pairs']) for r in m['rows']),'paired_two_book_games':sum(len({p['sportsbook'] for p in r['pairs']})==2 for r in m['rows']),'total_requests':len(receipts)}
    assert counts==m['counts'] and sum('api.odds-api.io' in r['request']['url'] for r in receipts.values())<=4
    quota=[{'url':r['request']['url'],'status_code':r['status_code'],'requested_at':r['requested_at'],'received_at':r['received_at'],
            'quota_headers':{k:v for k,v in r['response_headers'].items() if 'ratelimit' in k}}
           for r in receipts.values() if 'api.odds-api.io' in r['request']['url']]
    if not any(r['url'].endswith('/odds/multi') for r in quota): assert not quote_facts
    return {'status':'passed','audit_kind':'independent_original_bytes_no_project_imports','manifest':manifest_path.relative_to(root).as_posix(),'manifest_sha256':sha(raw),'recorded_git_commit':m['git_commit'],'recorded_git_sources_verified':True,'cohort_sha256':m['cohort_sha256'],'capture_started_at':m['capture_started_at'],'capture_completed_at':m['capture_completed_at'],'capture_status':m['status'],'counts':counts,'failed_requests':failed,'receipt_body_hashes_verified':len(receipts),'current_body_sha256':source_receipt['body_sha256'],'current_received_at':source_receipt['received_at'],'current_cache_headers':source_receipt['response_headers'],'capture_failures':m['failures'],'quote_diagnostics':m.get('quote_diagnostics',[]),'quota_receipts':quota,'reports':report_facts,'quotes':quote_facts,'outcomes_or_models_evaluated':False,'edge_established':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--root',type=Path,required=True); parser.add_argument('--manifest',type=Path,required=True); parser.add_argument('--output',type=Path,required=True); args=parser.parse_args()
    result=audit(args.root,args.manifest)
    with args.output.open('x') as handle: json.dump(result,handle,indent=2,allow_nan=False); handle.write('\n')
    print(json.dumps({'status':result['status'],'counts':result['counts'],'output':str(args.output)}))
