"""Fixed-model market-source sensitivity; never overwrites primary model artifacts."""
import argparse
from pathlib import Path
import sys
import json
import hashlib
import pandas as pd
import numpy as np

def main(root, market_file=None):
    ROOT=Path(root).resolve(); MODEL=ROOT/'model'; sys.path.insert(0,str(MODEL))
    from ncaaf_model.opponent_model import load_market_games,load_history,adjusted_features,fit_artifact,projections,metric
    history=load_history(MODEL,range(2019,2026)); market=load_market_games(MODEL)
    import ncaaf_model.opponent_model as om
    fingerprint=hashlib.sha256(Path(om.__file__).read_bytes()+pd.util.hash_pandas_object(market,index=False).values.tobytes()+pd.util.hash_pandas_object(history,index=False).values.tobytes()).hexdigest()[:16]
    cache=MODEL/f'data/normalized/opponent_features_verified_{fingerprint}.parquet'
    primary=pd.read_parquet(cache) if cache.exists() else adjusted_features(market,history)
    supp=pd.read_parquet(Path(market_file) if market_file else MODEL/'data/raw/alternative/cfbd_supplement_2024_2025.parquet')
    schedules=pd.concat([pd.read_parquet(MODEL/f'data/raw/sportsdataverse/cfb_schedule_{y}.parquet')[['game_id','neutral_site','status']] for y in (2024,2025)])
    supp=supp.merge(schedules,on='game_id',validate='one_to_one'); supp['game_date']=supp.date; supp['market_home_spread']=supp.spread
    # Structural ratings and timing are independent of the line; reuse exactly.
    overlap=primary.loc[primary.game_id.isin(supp.game_id)].copy()
    sup_index=supp.set_index('game_id'); alternate=overlap.copy()
    for col in ['market_total','market_home_spread','market_source']:alternate[col]=alternate.game_id.map(sup_index[col])
    for base in ['score','drive','clock']:alternate[f'adjusted_{base}_minus_market']=alternate[f'adjusted_{base}_total']-alternate.market_total
    alternate['abs_spread']=alternate.market_home_spread.abs()
    extra=supp.loc[~supp.game_id.isin(primary.game_id)]
    if len(extra):alternate=pd.concat([alternate,adjusted_features(extra,history)],ignore_index=True)
    rows=[];summary={}
    for year in (2024,2025):
     train=primary.loc[primary.season < year]
     artifact=fit_artifact(train); sigma=float(np.sqrt(np.mean((train.actual_total-train.market_total)**2)))
     groups={'cfbd_derivative_all':alternate.loc[alternate.season.eq(year)&alternate.adjusted_history_games.ge(5)].copy(), 'cfbd_derivative_shared':alternate.loc[alternate.season.eq(year)&alternate.game_id.isin(overlap.game_id)&alternate.adjusted_history_games.ge(5)].copy(), 'verified_provider_shared':overlap.loc[overlap.season.eq(year)&overlap.adjusted_history_games.ge(5)].copy()}
     summary[str(year)]={'training_games':artifact['training_games'],'training_seasons':artifact['training_seasons'],'sigma':sigma,'sources':{}}
     for source, frame in groups.items():
      summary[str(year)]['sources'][source]={}
      for candidate,pred in {'market_only':frame.market_total.to_numpy(float),**projections(frame,artifact)}.items():
       f=frame.copy();f['candidate']=candidate;f['comparison_source']=source;f['projected_total']=pred;f['residual_sigma']=sigma
       summary[str(year)]['sources'][source][candidate]=metric(f);rows.append(f)
     print(json.dumps({'year':year,'summary':summary[str(year)]}),flush=True)
    allpred=pd.concat(rows,ignore_index=True)
    pooled={}
    for (source,candidate),f in allpred.groupby(['comparison_source','candidate']):pooled.setdefault(source,{})[candidate]=metric(f)
    diffs=overlap[['game_id','season','market_total']].merge(supp[['game_id','market_total']],on='game_id',suffixes=('_verified','_derivative'));diffs['absolute_difference']=(diffs.market_total_verified-diffs.market_total_derivative).abs()
    report={'status':'reused_development_source_sensitivity_not_profitability_proof','primary_data_fingerprint':fingerprint,'supplement_manifest':'model/data/raw/alternative/andrew_manifest.json' if market_file is None else None,'supplement_market_file':str(Path(market_file).resolve()) if market_file else 'model/data/raw/alternative/cfbd_supplement_2024_2025.parquet','specification':'Two existing opponent candidates unchanged; fit only primary prior seasons; fold sigma prior primary residual RMS; test alternate market values. Historical EV3%/stress1% gates unchanged.','market_role':'CFBD first-provider derivative, provider identity and quote times not retained; no offered total prices; all profits assume -110.','feature_reuse':'Prior-outcome structural estimates copied for identical games and weekly cutoffs; only line-relative features recomputed. Non-overlap games recomputed using identical fixed model. No annual CSV stats used.','line_discrepancies':{'shared_games':len(diffs),'mean_absolute_difference':float(diffs.absolute_difference.mean()),'exact_match':int(diffs.absolute_difference.eq(0).sum()),'at_least_3_points':int(diffs.absolute_difference.ge(3).sum()),'at_least_5_points':int(diffs.absolute_difference.ge(5).sum()),'maximum_difference':float(diffs.absolute_difference.max())},'by_season':summary,'pooled':pooled}
    (MODEL/'reports/source_sensitivity.json').write_text(json.dumps(report,indent=2)+'\n')
    allpred.to_parquet(MODEL/'data/raw/alternative/source_sensitivity_predictions.parquet',index=False)
    lines=['# Historical market-source sensitivity','','The same two fixed opponent models were trained on prior seasons of the repaired primary market archive. Test games were repriced with independently published CFBD-derived lines. No model parameters, feature definitions, gates, or thresholds were chosen from these results. The derivative CSV lacks provider identities and timestamps; every ROI below assumes -110 rather than recorded total-side prices. These are reused development diagnostics.','','| Cohort/source | Candidate | Games | MAE change vs own market | Bets | Assumed ROI | 95% week bootstrap ROI |','|---|---|---:|---:|---:|---:|---|']
    for source,candidates in pooled.items():
     for candidate,m in candidates.items():
      roi='—' if m['assumed_minus110_roi'] is None else f"{m['assumed_minus110_roi']:.2%}"
      interval=m['roi_week_bootstrap_95']; ci='—' if interval is None else f'{interval[0]:+.2%} to {interval[1]:+.2%}'
      lines.append(f"| {source} | {candidate} | {m['games']} | {m['mae_delta']:+.3f} | {m['bets']} | {roi} | {ci} |")
    lines+=['','`shared` rows compare exactly the same games; the all-derivative cohort also includes games excluded by primary-provider availability. Do not compare all-derivative and primary-shared as if composition were identical.','', 'The underlying source chooses CFBD lines[0] and discards provider identity. It drops games without season-end advanced statistics or spread, so coverage selection is imperfect. Its annual advanced statistics have lookahead and were excluded entirely. Neither source reproduces a 06:30 ET available quote or offered payout. A difference in line alone can change both model features and which wagers cross the fixed gate.', '', 'All 2019–2025 periods have been reused in research; none is an untouched prospective test. Individual bootstrap intervals do not correct for all research selection.']
    (MODEL/'reports/source_sensitivity.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'pooled':pooled,'discrepancies':report['line_discrepancies']},indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1], help='Repository root, containing model/ and scripts/')
    parser.add_argument('--market-file', type=Path, help='Optional normalized market-only Parquet; defaults to CFBD derivative')
    args = parser.parse_args()
    main(args.root, args.market_file)
