"""Replay the frozen 14-game PBP parser audit independently.

Reads only the fixed allowlist for the two smallest canonical final schedule
IDs in each season 2019–2025; never substitutes a missing game's raw rows.
Verifies frozen plan, source bytes and Git blobs before selective PBP reads.
Parser.prepare_rows is used only for comparison; no helpers, ratings, model
fits, matchup metrics, 2026 data or network calls. Outputs a new local trace
with exclusive creation. Requires retained source archives and Git commit
2277702. Run from the repository with PYTHONPATH=model, for example:

  python scripts/research/audit_pbp_state_parser.py --output /tmp/pbp-replay.json

This audits a frozen historical definition, not a current production policy.
"""
import json,hashlib,math,re,subprocess
from pathlib import Path
from collections import Counter
from datetime import datetime,timezone,timedelta
import argparse
import pandas as pd
cli=argparse.ArgumentParser(description=__doc__)
cli.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[2])
cli.add_argument('--output',type=Path,help='Exclusive-create JSON trace; never overwrites')
args=cli.parse_args()
R=args.root.resolve(); M=R/'model'
output=args.output or M/'data/normalized/pbp_state_v1/parser_audit_replay.json'
if output.exists():raise FileExistsError('Audit output already exists: '+str(output))
PLAN='7a2124bf2de2380423a29d765961dc4b9e79384c82cb2e6ab9551853495c8c69'
COMMIT='2277702'
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
 return h.hexdigest()
def objsha(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
assert sha(M/'reports/pbp_state_research_plan.json')==PLAN
plan=json.loads((M/'reports/pbp_state_research_plan.json').read_text())
source_check={}
for rel in ['ncaaf_model/pbp_state_rows.py','reports/PBP_STATE_RESEARCH_PLAN.md','reports/PBP_RESEARCH_SOURCE_INVENTORY.json']:
 data=subprocess.check_output(['git','show',f'{COMMIT}:model/{rel}'],cwd=R)
 source_check[rel]={'recorded_commit_sha256':hashlib.sha256(data).hexdigest(),'current_sha256':sha(M/rel),'expected':plan['source_files_sha256'][rel]}
 assert len(set(source_check[rel].values()))==1
# Freeze validation above precedes all game/play value reads.
from ncaaf_model.pbp_state_rows import RAW_COLUMNS, prepare_rows

def clean(value):
 if isinstance(value,dict):return {k:clean(x) for k,x in value.items()}
 if isinstance(value,list):return [clean(x) for x in value]
 if value is pd.NA or value is pd.NaT:return None
 if isinstance(value,pd.Timestamp):return value.isoformat()
 if isinstance(value,float) and pd.isna(value):return None
 return value

samples=[]
inventory=json.loads((M/'reports/PBP_RESEARCH_SOURCE_INVENTORY.json').read_text())
for source in inventory['sources']:
 year=source['season']
 assert year in range(2019,2026)
 assert sha(R/source['body_path'])==source['body_sha256']
 schedule_path=f'data/raw/sportsdataverse/cfb_schedule_{year}.parquet'
 assert sha(M/schedule_path)==plan['source_files_sha256'][schedule_path]
 schedules=pd.read_parquet(M/schedule_path,columns=['game_id','season','week','game_date','neutral_site','home_id','away_id','status'],dtype_backend='numpy_nullable')
 selected=schedules[(schedules.status=='STATUS_FINAL') & (schedules.game_id>0) & (schedules.home_id>0) & (schedules.away_id>0) & (schedules.home_id!=schedules.away_id) & (schedules.week>=0) & schedules.neutral_site.notna()].sort_values('game_id').head(2)
 assert len(selected)==2
 ids=[int(x) for x in selected.game_id]
 allowed=pd.read_parquet(R/source['body_path'],columns=list(RAW_COLUMNS),filters=[('game_id','in',ids)],dtype_backend='numpy_nullable')
 for game in selected.to_dict('records'):
  frame=allowed[allowed.game_id==game['game_id']].sort_values('sequenceNumber')
  parsed,coverage=prepare_rows(frame,selected)
  samples.append(clean({'schedule':game,'raw':frame.to_dict('records'),'parser':parsed.to_dict('records'),'coverage':coverage,'source_path':source['body_path'],'source_sha256':source['body_sha256']}))
assert len(samples)==14

rush={'Rush','Rushing Touchdown'}
passed={'Pass','Pass Reception','Pass Completion','Pass Incompletion','Passing Touchdown','Pass Reception Touchdown','Sack','Sack Touchdown','Interception','Interception Return','Interception Return Touchdown','Pass Interception','Pass Interception Return','Pass Interception Return Touchdown'}
fumbles={'Fumble Recovery (Own)','Fumble Recovery (Own) Touchdown','Fumble Recovery (Opponent)','Fumble Recovery (Opponent) Touchdown','Fumble Return Touchdown'}
lost={x for x in passed if 'Interception' in x}|{'Sack Touchdown','Fumble Recovery (Opponent)','Fumble Recovery (Opponent) Touchdown','Fumble Return Touchdown'}
offtd={'Rushing Touchdown','Passing Touchdown','Pass Reception Touchdown','Fumble Recovery (Own) Touchdown'}
legal_types=rush|passed|fumbles
flags=['scoringPlay','isPenalty','isTurnover','penalty_flag','penalty_no_play','penalty_offset','kneel_down','kickoff_play','punt_play']
def text(r,k):return (r[k] or '').strip()
def clock(r):
 v=text(r,'clock.displayValue')
 if not re.fullmatch('[0-9]{1,2}:[0-9]{2}',v):return None
 a,b=map(int,v.split(':'));n=60*a+b
 return n if b<60 and 0<=n<=900 else None
def legal(r,home,away):
 typ=text(r,'type.text');orig=text(r,'orig_play_type');txt=text(r,'text');alltext=' '.join([typ,orig,txt]).lower()
 if any(r[k] is True for k in ['isPenalty','penalty_flag','penalty_no_play','penalty_offset']) or re.search(r'\bpenalt(?:y|ies)\b|\bno[ -]?play\b|\bnullified\b',alltext):return 'penalty_or_nullified'
 if r['kneel_down'] is True or re.search(r'\bkneel(?:s|ed|ing)?\b|\btak(?:e|es|ing)\s+a\s+knee\b',alltext):return 'kneel'
 if 'spike' in (typ+' '+orig).lower() or re.search(r'\bspik(?:e|es|ed|ing)\s+(?:the\s+)?ball\b|\bball\s+(?:(?:is|was)\s+)?spiked\b',txt,re.I):return 'spike'
 if r['kickoff_play'] is True or r['punt_play'] is True or re.search(r'kick|punt|field goal|extra point|two[ -]point|\bPAT\b',orig,re.I):return 'special_team_provenance'
 if typ not in legal_types:return 'unsupported_play_type'
 if r['period.number'] not in [1,2,3,4]:return 'nonregulation_or_unknown_period'
 if r['start.down'] not in [1,2,3,4] or not isinstance(r['start.distance'],int) or not 1<=r['start.distance']<=100 or not isinstance(r['start.yardsToEndzone'],int) or not 1<=r['start.yardsToEndzone']<=100:return 'invalid_scrimmage_state'
 if r['start.team.id'] not in [home,away]:return 'invalid_possession_team'
 if clock(r) is None:return 'invalid_clock'
 return None
def pass_indicator(r):
 for k in ['type.text','orig_play_type']:
  if text(r,k) in passed:return 1.
  if text(r,k) in rush:return 0.
 a=bool(re.search(r'\bpass(?:es|ed|ing)?\b|\bsack(?:s|ed)?\b',text(r,'text'),re.I))
 b=bool(re.search(r'\brush(?:es|ed|ing)?\b|\brun(?:s|ning)?\b',text(r,'text'),re.I))
 return float(a) if a!=b else None
def conversion(r,previous,home,away):
 typ=text(r,'type.text');team=r['start.team.id'];end=r['end.team.id']
 if typ in lost or r['isTurnover'] is True:return 0.,'explicit_turnover'
 keys=['homeScore','awayScore'] if team==home else ['awayScore','homeScore']
 deltas=[r[k]-previous[k] if isinstance(r[k],int) and r[k]>=0 else None for k in keys]
 if r['scoringPlay'] is True and deltas[0]==0 and deltas[1] is not None and deltas[1]>0:return 0.,'defensive_score'
 if typ in offtd and r['scoringPlay'] is True and deltas[0] in [6,7,8] and deltas[1]==0:return 1.,'attributed_offensive_td'
 if 'Touchdown' in typ or r['scoringPlay'] is True:return None,'ambiguous_scoring_attribution'
 if end in [home,away] and end!=team:return 0.,'possession_lost'
 if end not in [home,away]:return None,'missing_or_invalid_end_team'
 yards=r['statYardage']
 if not isinstance(yards,int) or not -100<=yards<=100:return None,'missing_or_invalid_stat_yardage'
 return float(yards>=min(r['start.distance'],r['start.yardsToEndzone'])),'retained_yardage'
results=[];all_mismatches=[];samples_cases=[]; totals=Counter(); source_hashes={}
for sample in samples:
 game=sample['schedule'];raw=sample['raw'];actual=sample['parser'];home,away=game['home_id'],game['away_id'];year=game['season'];gid=game['game_id']
 path=sample['source_path']
 if path not in source_hashes:
  source_hashes[path]=sha(R/path);assert source_hashes[path]==sample['source_sha256']
 sp=f'data/raw/sportsdataverse/cfb_schedule_{year}.parquet'
 assert sha(M/sp)==plan['source_files_sha256'][sp]
 assert game['status']=='STATUS_FINAL' and game['week']>=0 and home!=away and game['neutral_site'] in [True,False]
 for k in ['id','sequenceNumber','game_play_number']:
  vals=[r[k] for r in raw];assert all(type(v) is int and v>=(0 if k!='id' else 1) for v in vals);assert len(set(vals))==len(vals)
 assert all(r['season']==year and r['homeTeamId']==home and r['awayTeamId']==away and r['game_id']==gid for r in raw)
 assert all(a['sequenceNumber']<b['sequenceNumber'] and a['game_play_number']<b['game_play_number'] for a,b in zip(raw,raw[1:]))
 reasons=[legal(r,home,away) for r in raw];excluded=Counter();expected=[];attribution=Counter();details=[];clock_missing=Counter();case=[]
 for i,r in enumerate(raw):
  reason=reasons[i]
  if reason is None and i and r['game_play_number']!=raw[i-1]['game_play_number']+1:reason='unavailable_pre_score_after_archived_play_number_gap'
  if reason is None and (not i or any(not isinstance(raw[i-1][k],int) or raw[i-1][k]<0 for k in ['homeScore','awayScore'])):reason='unavailable_previous_post_score'
  if reason is not None:
   excluded[reason]+=1
   details.append({'play_id':r['id'],'game_play_number':r['game_play_number'],'decision':'excluded','reason':reason})
   continue
  prev=raw[i-1];conv,convreason=conversion(r,prev,home,away);attribution[convreason]+=1;seconds=None;clockreason='no_next_archived_row'
  if i+1<len(raw):
   nxt=raw[i+1];clockreason='next_row_not_legal' if reasons[i+1] is not None else None
   if clockreason is None and nxt['game_play_number']!=r['game_play_number']+1:clockreason='archived_play_number_gap'
   if clockreason is None and (not text(r,'drive.id') or text(r,'drive.id')!=text(nxt,'drive.id')):clockreason='missing_or_changed_drive'
   if clockreason is None and (r['period.number']!=nxt['period.number'] or r['start.team.id']!=nxt['start.team.id'] or r['end.team.id']!=r['start.team.id']):clockreason='period_or_possession_boundary'
   if clockreason is None and (text(r,'type.text') in lost or r['isTurnover'] is True or r['scoringPlay'] is True or 'Touchdown' in text(r,'type.text')):clockreason='turnover_or_scoring_boundary'
   if clockreason is None:
    delta=clock(r)-clock(nxt)
    if 0<=delta<=60:seconds=float(delta)
    else:clockreason='clock_decrement_outside_0_60'
  if clockreason:clock_missing[clockreason]+=1
  team=r['start.team.id'];homeoff=team==home;p=r['period.number']
  availability=datetime.fromisoformat(game['game_date']).astimezone(timezone.utc)+timedelta(hours=6)
  exp={'game_id':gid,'season':year,'week':game['week'],'team_id':team,'opponent_id':away if homeoff else home,'available_at':availability.isoformat(),'is_home':homeoff,'neutral_site':game['neutral_site'],'down':r['start.down'],'distance':r['start.distance'],'yards_to_endzone':r['start.yardsToEndzone'],'score_margin':(prev['homeScore']-prev['awayScore'])*(1 if homeoff else -1),'period':p,'half_seconds_remaining':clock(r)+(900 if p in [1,3] else 0),'pass_play':pass_indicator(r),'clock_seconds':seconds,'conversion':conv,'play_id':r['id'],'sequence_number':r['sequenceNumber'],'game_play_number':r['game_play_number'],'drive_id':text(r,'drive.id') or None,'pre_home_score':prev['homeScore'],'pre_away_score':prev['awayScore']}
  expected.append(exp)
  details.append({'play_id':r['id'],'game_play_number':r['game_play_number'],'decision':'included','conversion_attribution':convreason,'clock_missing_reason':clockreason})
  if convreason in ['ambiguous_scoring_attribution','explicit_turnover','attributed_offensive_td'] or (text(r,'type.text').startswith('Fumble') and convreason=='retained_yardage'):
   case.append({'game_id':gid,'play_number':r['game_play_number'],'type':text(r,'type.text'),'original_type':text(r,'orig_play_type'),'text':text(r,'text'),'pre_score':[prev['homeScore'],prev['awayScore']],'post_score':[r['homeScore'],r['awayScore']],'start_team':team,'end_team':r['end.team.id'],'conversion':conv,'reason':convreason,'stat_yards':r['statYardage'],'required_yards':min(r['start.distance'],r['start.yardsToEndzone'])})
 expected_by={r['play_id']:r for r in expected};actual_by={r['play_id']:r for r in actual}; mismatch=[]
 if set(expected_by)!=set(actual_by):mismatch.append({'rowset_difference':sorted(set(expected_by)^set(actual_by))})
 for pid,e in expected_by.items():
  a=actual_by.get(pid)
  if a is None:continue
  for k,v in e.items():
   if a[k]!=v:mismatch.append({'play_id':pid,'field':k,'expected':v,'parser':a[k]})
 cp={k:v for k,v in sample['coverage']['exclusions'].items() if v}
 if cp!=dict(excluded):mismatch.append({'exclusion_counts_expected':dict(excluded),'parser':cp})
 rowresult={'schedule':game,'source_path':path,'source_sha256':source_hashes[path],'raw_allowed_sha256':objsha(raw),'raw_rows':len(raw),'raw_missing':not raw,'output_rows':len(expected),'clock_available':sum(r['clock_seconds'] is not None for r in expected),'conversion_available':sum(r['conversion'] is not None for r in expected),'pass_unknown':sum(r['pass_play'] is None for r in expected),'unknown_helper_flags':{k:sum(r[k] is None for r in raw) for k in flags},'exclusions':dict(excluded),'clock_missing':dict(clock_missing),'conversion_attribution':dict(attribution),'mismatch_count':len(mismatch),'mismatches':mismatch,'independent_rows':expected,'row_decisions':details,'attribution_cases':case,'parser_output_sha256':objsha(actual)}
 results.append(rowresult);all_mismatches.extend([{'game_id':gid,**x} for x in mismatch]);samples_cases.extend(case)
 totals.update({'sampled_games':1,'games_with_rows':bool(raw),'raw_rows':len(raw),'output_rows':len(expected),'clock_available':rowresult['clock_available'],'conversion_available':rowresult['conversion_available'],'pass_unknown':rowresult['pass_unknown'],'state_gap_exclusions':excluded['unavailable_pre_score_after_archived_play_number_gap'],'unknown_helper_flag_cells':sum(rowresult['unknown_helper_flags'].values())})
summary={'schema':'pbp-state-parser-fixed-sample-audit-v1','audited_at':datetime.now(timezone.utc).isoformat(),'frozen_commit':subprocess.check_output(['git','rev-parse',COMMIT],cwd=R,text=True).strip(),'plan_sha256':PLAN,'selection_rule':'Two numerically smallest valid canonical STATUS_FINAL schedule game IDs in each season 2019–2025, selected before PBP value inspection. Missing raw games retained without substitution.','allowed_fields_only':True,'no_model_fits_or_matchup_metrics':True,'independent_reconstruction':'Standalone Python arithmetic and rule implementation, no calls to parser helpers, dataset loader, ratings or study runner; parser.prepare_rows was used only for comparison.','replay_script_sha256':sha(__file__),'source_verification':source_check,'raw_source_hashes':source_hashes,'totals':dict(totals),'mismatch_count':len(all_mismatches),'mismatches':all_mismatches,'games':results,'limitations':['This fixed ID sample is not random or representative; lowest IDs cluster in certain schools/conferences and early-season schedules.','One selected game has no archived rows and remains missing.','Agreement verifies implementation of the frozen state definitions; it does not certify every upstream field or original publication time.','Clock depletion is adjacent recorded game-clock change, not direct snap-to-snap timing; processed archives may omit records.','Historical play archive is a later publisher snapshot; kickoff+6h is an availability proxy, not a verified historical receipt.','No target matchup prediction, betting return, profitability or 2026 data was evaluated.','Frozen prepare_rows docstring retains an outdated short phrase about gaps being retained for efficiency; executable code and formal plan withhold the first state after a gap.']}
offtd={'Rushing Touchdown','Passing Touchdown','Pass Reception Touchdown','Fumble Recovery (Own) Touchdown'}
stats=Counter(); anomalies=[];pergames=[]
for g in samples:
 raw=g['raw'];out={r['play_id']:r for r in g['parser']};loc=Counter()
 for i,r in enumerate(raw):
  if r['type.text'] in offtd:
   loc['raw_offensive_td_labels']+=1
   if r['id'] not in out:loc['offensive_td_excluded_state']+=1
   elif out[r['id']]['conversion'] is None:loc['offensive_td_missing_conversion']+=1
   else:loc['offensive_td_attributed_success' if out[r['id']]['conversion']==1 else 'offensive_td_attributed_failure']+=1
  if not i or not all(isinstance(v[k],int) for v in [raw[i-1],r] for k in ['homeScore','awayScore']):continue
  d=[r[k]-raw[i-1][k] for k in ['homeScore','awayScore']]
  if d==[0,0]:continue
  loc['score_change_rows']+=1;loc['score_change_on_scoring_true' if r['scoringPlay'] else 'score_change_on_scoring_false']+=1
  if any(v<0 for v in d):loc['score_correction_decrease']+=1
  if not r['scoringPlay']:
   nextrows=raw[i+1:i+5]
   anomalies.append({'season':g['schedule']['season'],'game_id':r['game_id'],'play_number':r['game_play_number'],'type':r['type.text'],'delta':d,'previous':{k:raw[i-1][k] for k in ['game_play_number','type.text','homeScore','awayScore']},'current_score':[r['homeScore'],r['awayScore']],'current_parser_pre_score':([out[r['id']]['pre_home_score'],out[r['id']]['pre_away_score']] if r['id'] in out else None),'next_four_rows':[{k:x[k] for k in ['game_play_number','type.text','scoringPlay','homeScore','awayScore']} for x in nextrows]})
 stats.update(loc);pergames.append({'season':g['schedule']['season'],'game_id':g['schedule']['game_id'],'counts':dict(loc)})
for key in ['offensive_td_excluded_state','offensive_td_attributed_failure']:stats.setdefault(key,0)
summary['score_semantics']={'totals':dict(stats),'by_game':pergames,'non_scoring_score_changes':anomalies,'interpretation':'88 of 89 raw offensive TD labels have the expected contemporaneous offensive 6–8 point increase and are conversion successes. One has an earlier premature score increase, so its conversion is unavailable. Five score rollback/restoration episodes around administrative/special-team records can temporarily corrupt lagged score margins. This does not support a systematic pre/post-score inversion; it does establish source noise in the state nuisance variable. No correction or resampling was applied.'}
summary['audit_limits_addendum']='Source score changes are not independently confirmed against official contemporaneous records; the audit identifies internal inconsistency without inventing historical corrections.'
summary['replay_script_path']=Path(__file__).resolve().relative_to(R).as_posix()
output.parent.mkdir(parents=True,exist_ok=True)
with output.open('x') as stream:stream.write(json.dumps(summary,indent=2,allow_nan=False)+'\n')
print(json.dumps({'output':str(output),'sha256':sha(output),'totals':summary['totals'],'mismatch_count':summary['mismatch_count'],'score_semantics':summary['score_semantics']['totals']},indent=2))
