"""Market-only derivative and SDQL normalization; annual source features excluded."""
import argparse
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import re
import pandas as pd

def split(s):
 depth=0;start=0;out=[];quote=None
 for i,c in enumerate(s):
  if quote:
   if c==quote and (not i or s[i-1]!='\\'):quote=None
  elif c in '\"\'':quote=c
  elif c in '[({':depth+=1
  elif c in '])}':
   depth-=1
   if depth < 0: raise ValueError('Unbalanced source row')
  elif c==',' and depth==0:out.append(s[start:i].strip());start=i+1
 if depth != 0 or quote is not None:
  raise ValueError('Unbalanced source row')
 out.append(s[start:].strip());return out

def main(root):
    ROOT=Path(root).resolve();ALT=ROOT/'model/data/raw/alternative'
    p=ALT/'andrew_training_24_25.csv'
    f=pd.read_csv(p,usecols=['id','season','week','home_team','away_team','home_points','away_points','spread','overUnder']).rename(columns={'id':'game_id','overUnder':'market_total','home_team':'cfbd_home_team','away_team':'cfbd_away_team'})
    s=pd.concat([pd.read_parquet(ROOT/f'model/data/raw/sportsdataverse/cfb_schedule_{y}.parquet') for y in (2024,2025)]).drop_duplicates('game_id')
    f=f.merge(s,on=['game_id','season'],suffixes=('_cfbd','')).copy()
    valid=f.status.eq('STATUS_FINAL') & f.home_points.eq(f.home_score)&f.away_points.eq(f.away_score)&f.market_total.between(10,130)&f.spread.between(-80,80)
    f['validated']=valid
    f['actual_total']=f.home_score+f.away_score;f['date']=pd.to_datetime(f.game_date,utc=True);f['start_date']=f.date
    f['market_source']='cfbd_first_provider_identity_not_retained';f['source']='andrew_public_cfbd_csv';f['market_role']='documented_CFBD_closing_field_provider_unknown';f['quote_timestamp']=None;f['timestamp_verified']=False;f['opening_total']=None;f['bookmaker_count']=None
    cols=['game_id','season','week','date','start_date','home_id','away_id','home_team','away_team','home_score','away_score','actual_total','market_total','spread','market_source','source','market_role','quote_timestamp','timestamp_verified','opening_total','bookmaker_count','validated']
    f.loc[valid,cols].to_parquet(ALT/'cfbd_supplement_2024_2025.parquet',index=False)
    meta={'repository':'https://github.com/andrewrpokorny-source/cfb-analytics','commit':'1371e18135e778b41372b860f7045c76027719aa','file_commit':'f7d1ce1e9d5ffca61640b3b0cf8eecea02bbff72','file_publication_time':'2025-12-17T12:35:31Z','source_url':'https://raw.githubusercontent.com/andrewrpokorny-source/cfb-analytics/1371e18135e778b41372b860f7045c76027719aa/cfb_training_data_24_25.csv','sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'license':None,'raw_rows':len(f),'validated_rows':int(valid.sum()),'selected_per_season':f[valid].season.value_counts().sort_index().to_dict(),'missing_totals':int(f.market_total.isna().sum()),'score_orientation_rejections':f.loc[f.home_points.ne(f.home_score)|f.away_points.ne(f.away_score),'game_id'].tolist(),'selection':'Source main.py directly chooses CFBD lines[0], does not store provider, does not impute total. We retain only market fields; all same-season advanced features excluded due lookahead.','known_preselection':'Source drops games missing home/away full-season advanced stats and missing spread. Not complete market universe.','timestamp_limit':'No bookmaker quote timestamps or total-side prices. Commit date is publication after most games, not pregame availability.','close_limit':'CFBD documents closing lines, but this derivative dropped provider identity and cannot verify exact last pregame quote.'}
    (ALT/'andrew_manifest.json').write_text(json.dumps(meta,indent=2));print('ANDREW',json.dumps(meta,indent=2))

    p=ALT/'sdql/ncaafb_2019.csv';raw=p.read_text().splitlines();hdr=split(raw[0]);rs=[split(l) for l in raw[1:] if l.strip()];assert all(len(x)==len(hdr) for x in rs)
    t=pd.DataFrame(rs,columns=hdr)
    for c in ['total','line','points','season','date','week']:t[c]=pd.to_numeric(t[c],errors='coerce')
    a=t.iloc[::2].reset_index(drop=True);b=t.iloc[1::2].reset_index(drop=True)
    assert len(a)==len(b) and a.date.equals(b.date)
    assert (a.total.fillna(-1)==b.total.fillna(-1)).all()
    assert (a.line.fillna(0)+b.line.fillna(0)).abs().lt(.01).all()
    norm=lambda x:re.sub('[^a-z0-9]','',str(x).lower())
    rows=[]
    for i in range(len(a)):
     x,y=a.iloc[i],b.iloc[i]
     rows.append({'source_row_pair':i,'calendar_date':str(int(x.date)),'name_key':'|'.join(sorted([norm(x['full name']),norm(y['full name'])])),'first_team':x['full name'],'second_team':y['full name'],'first_score':x.points,'second_score':y.points,'first_spread':x.line,'market_total':x.total,'first_site':x.site,'second_site':y.site,'season':x.season,'week_sdql':x.week})
    g=pd.DataFrame(rows)
    s=pd.read_parquet(ROOT/'model/data/raw/sportsdataverse/cfb_schedule_2019.parquet')
    s['calendar_date']=pd.to_datetime(s.game_date,utc=True).dt.tz_convert('America/New_York').dt.strftime('%Y%m%d');s['name_key']=['|'.join(sorted([norm(h),norm(a)])) for h,a in zip(s.home_team,s.away_team)]
    ambiguous=int(g.duplicated(['calendar_date','name_key','season'],keep=False).sum())
    g=g.loc[~g.duplicated(['calendar_date','name_key','season'],keep=False)].copy()
    m=g.merge(s,on=['calendar_date','name_key','season'],how='left',validate='one_to_one')
    m['first_is_espn_home']=[norm(a)==norm(b) for a,b in zip(m.first_team,m.home_team)]
    m['mapped_home_score']=m.first_score.where(m.first_is_espn_home,m.second_score);m['mapped_away_score']=m.second_score.where(m.first_is_espn_home,m.first_score)
    m['spread']=m.first_spread.where(m.first_is_espn_home,-m.first_spread)
    m['validated']=m.game_id.notna()&m.status.eq('STATUS_FINAL')&m.mapped_home_score.eq(m.home_score)&m.mapped_away_score.eq(m.away_score)&m.market_total.between(10,130)
    m['actual_total']=m.home_score+m.away_score;m['date']=pd.to_datetime(m.game_date,utc=True);m['start_date']=m.date;m['market_source']='sportsdatabase_sdql_book_unknown';m['source']='sdql_public_archive';m['market_role']='historical_total_quote_time_and_role_unverified';m['quote_timestamp']=None;m['timestamp_verified']=False;m['opening_total']=None;m['bookmaker_count']=None
    m.loc[m.validated,cols].to_parquet(ALT/'sdql_market_games_2019.parquet',index=False)
    meta={'repository':'https://github.com/jampdx/sdql2','commit':'e8783b7f02da6a883904ed2e0bb03d75532e7214','source_url':'https://raw.githubusercontent.com/jampdx/sdql2/e8783b7f02da6a883904ed2e0bb03d75532e7214/Data/ncaafb_2019.csv','sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'repository_license':'GPL-3.0; separate underlying data rights not independently clarified','raw_team_rows':len(t),'paired_games':len(a),'ambiguous_name_date_pairs_excluded':ambiguous,'remaining_distinct_pairs':len(g),'pairs_with_total':int(g.market_total.notna().sum()),'strict_name_date_score_matched_games':int(m.validated.sum()),'pairs_with_opening_total':0,'selection':'Adjacent home/away rows validated opposite spreads, identical dates/totals; exact normalized full names and local calendar date joined to ESPN then scores checked. No fuzzy match.','limitations':['Bookmaker and quote timestamp absent; not certified closing lines.','SDQL2019 has no populated opening total despite column names.','2019 is already reused historical development period.']}
    (ALT/'sdql_manifest.json').write_text(json.dumps(meta,indent=2));print('SDQL',json.dumps(meta,indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1], help='Repository root, containing model/ and scripts/')
    args = parser.parse_args()
    main(args.root)
