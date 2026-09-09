"""Normalize pinned public CFBD lines; never fabricate missing quote fields."""
import argparse
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import pandas as pd
import numpy as np

def main(root):
    ROOT=Path(root).resolve()
    ALT=ROOT/'model/data/raw/alternative'; SRC=ALT/'cfbd_public'
    PIN='59f7f0ef813b229894757619901c49855a08ac1a'
    BOOKS=['Bovada','DraftKings','William Hill (New Jersey)','ESPN Bet','Caesars','SugarHouse','Caesars (Pennsylvania)','Caesars (Colorado)']
    PRIORITY=['consensus']+BOOKS
    rows=[]; manifest=[]
    for season in range(2020,2024):
     p=SRC/f'lines_{season}.json'; payload=json.loads(p.read_text())
     manifest.append({'path':str(p.relative_to(ROOT)), 'source_url':f'https://raw.githubusercontent.com/jasperfriis-cuni/cfb-market-efficiency/{PIN}/raw_data/{p.name}', 'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size,'game_objects':len(payload),'quote_timestamp_available':False})
     for g in payload:
      base={'game_id':g['id'],'season':g['season'],'week':g['week'],'season_type':g['seasonType'],'date':g['startDate'],'home_id':g['homeTeamId'],'away_id':g['awayTeamId'],'home_team':g['homeTeam'],'away_team':g['awayTeam'],'home_classification':g.get('homeClassification'),'away_classification':g.get('awayClassification'),'home_score':g.get('homeScore'),'away_score':g.get('awayScore')}
      for q in g['lines']:
       rows.append({**base,'provider':q['provider'],'market_total':q.get('overUnder'),'opening_total':q.get('overUnderOpen'),'spread':q.get('spread'),'opening_spread':q.get('spreadOpen'),'home_moneyline':q.get('homeMoneyline'),'away_moneyline':q.get('awayMoneyline'),'quote_timestamp':None,'timestamp_verified':False})
    q=pd.DataFrame(rows)
    for c in ['game_id','season','week','home_id','away_id','home_score','away_score','market_total','opening_total','spread','opening_spread','home_moneyline','away_moneyline']:q[c]=pd.to_numeric(q[c],errors='coerce')
    q['date']=pd.to_datetime(q.date,utc=True)
    q['actual_total']=q.home_score+q.away_score
    assert not q.duplicated(['game_id','provider']).any()
    sched=pd.concat([pd.read_parquet(ROOT/f'model/data/raw/sportsdataverse/cfb_schedule_{y}.parquet') for y in range(2020,2024)],ignore_index=True).drop_duplicates('game_id')
    sched['game_date']=pd.to_datetime(sched.game_date,utc=True)
    sched=sched.rename(columns={'home_id':'espn_home_id','away_id':'espn_away_id','home_score':'espn_home_score','away_score':'espn_away_score','game_date':'espn_date','home_team':'espn_home_team','away_team':'espn_away_team'})
    qc=q.merge(sched[['game_id','espn_home_id','espn_away_id','espn_home_score','espn_away_score','espn_date','espn_home_team','espn_away_team','status']],on='game_id',how='left',validate='many_to_one')
    qc['id_match']=qc.home_id.eq(qc.espn_home_id)&qc.away_id.eq(qc.espn_away_id)
    qc['score_match']=qc.home_score.eq(qc.espn_home_score)&qc.away_score.eq(qc.espn_away_score)
    qc['date_difference_hours']=(qc.date-qc.espn_date).dt.total_seconds()/3600
    qc['validated']=qc.id_match & qc.score_match & qc.status.eq('STATUS_FINAL') & qc.date_difference_hours.abs().lt(24)
    qc.to_parquet(ALT/'cfbd_provider_quotes.parquet',index=False)
    valid=qc[qc.validated & qc.market_total.between(10,130) & qc.spread.between(-80,80) & qc.provider.isin(PRIORITY)].copy()
    valid['priority']=valid.provider.map({p:i for i,p in enumerate(PRIORITY)})
    selected=valid.sort_values(['game_id','priority']).drop_duplicates('game_id').drop(columns='priority')
    agg=qc[qc.market_total.between(10,130)].groupby('game_id').agg(provider_count=('provider','nunique'),bookmaker_count=('provider',lambda p:p[p.isin(BOOKS)].nunique()),provider_total_min=('market_total','min'),provider_total_max=('market_total','max'))
    selected=selected.merge(agg,on='game_id',how='left').rename(columns={'provider':'market_source'})
    for provider,key in [('Bovada','bovada'),('consensus','consensus'),('DraftKings','draftkings'),('William Hill (New Jersey)','william_hill_nj')]:
     extra=qc[qc.provider.eq(provider)][['game_id','market_total','opening_total','spread','opening_spread']].rename(columns={c:f'{key}_{c}' for c in ['market_total','opening_total','spread','opening_spread']})
     selected=selected.merge(extra,on='game_id',how='left')
    selected['market_role']='archived_final_line_unverified_close_time'
    selected['opening_total_source']=selected.market_source.where(selected.opening_total.notna())
    selected['source']='public_cfbd_archive'
    selected['home_team_cfbd']=selected.home_team;selected['away_team_cfbd']=selected.away_team
    selected['home_team']=selected.espn_home_team; selected['away_team']=selected.espn_away_team
    selected['start_date']=selected.date
    selected=selected.sort_values(['date','game_id']).reset_index(drop=True)
    selected.to_parquet(ALT/'cfbd_market_games.parquet',index=False)
    clean_path=ROOT/'model/data/normalized/clean_historical_features.parquet'
    old_ids=set(pd.read_parquet(clean_path).game_id) if clean_path.exists() else None
    summary={}
    for year,g in qc.drop_duplicates('game_id').groupby('season'):
     s=selected[selected.season.eq(year)]
     summary[str(year)]={'raw_game_objects':len(json.loads((SRC/f'lines_{year}.json').read_text())),'games_with_provider_rows':len(g),'games_with_total':int(qc[qc.season.eq(year)&qc.market_total.notna()].game_id.nunique()),'validated_selected_games':len(s),'fbs_vs_fbs':int((s.home_classification.eq('fbs')&s.away_classification.eq('fbs')).sum()),'at_least_one_fbs':int((s.home_classification.eq('fbs')|s.away_classification.eq('fbs')).sum()),'new_vs_existing_clean':int((~s.game_id.isin(old_ids)).sum()) if old_ids is not None else None,'selected_same_source_open_close_pairs':int(s.opening_total.notna().sum()),'bovada_open_close_pairs':int((s.bovada_market_total.notna()&s.bovada_opening_total.notna()).sum()),'source_counts':s.market_source.value_counts().to_dict(),'raw_ids_not_in_local_schedule':int(g.espn_home_id.isna().sum()),'id_mismatch':int((g.espn_home_id.notna()&~g.id_match).sum()),'score_mismatch':int((g.espn_home_id.notna()&~g.score_match).sum()),'date_difference_ge24h':int(g.date_difference_hours.abs().ge(24).sum())}
    qc.drop_duplicates('game_id').loc[lambda d:~d.validated].to_csv(ALT/'cfbd_validation_exceptions.csv',index=False)
    meta={'normalized_at_utc':datetime.now(timezone.utc).isoformat(),'source_repository':'https://github.com/jasperfriis-cuni/cfb-market-efficiency','source_commit':PIN,'repository_license':None,'upstream':'https://api.collegefootballdata.com/api/betting','timestamps':'No quote timestamps exist; retrieval time is not quote time. Open and final fields do not prove tradable 06:30 prices.','selection_priority':PRIORITY,'selection_rule':'first source with plausible final total and spread, strict ID/score/date validation, opening fields only from identical source','summary':summary,'manifest':manifest,'selected_games':len(selected),'provider_quotes':len(qc)}
    (ALT/'cfbd_manifest.json').write_text(json.dumps(meta,indent=2))
    print(json.dumps(meta,indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1], help='Repository root, containing model/ and scripts/')
    args = parser.parse_args()
    main(args.root)
