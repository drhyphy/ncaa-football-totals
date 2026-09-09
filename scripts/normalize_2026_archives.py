"""Freeze existing 2026 pregame quote archives without reading final scores.

Reads locally captured source files only; never downloads or evaluates weather.
Receipt metadata and raw SHA-256 are preserved, not inferred from file names.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from collections import Counter

import numpy as np
import pandas as pd


def number(x):
    try:
        y = float(x)
        return y if np.isfinite(y) else None
    except (TypeError, ValueError):
        return None


def decimal_price(american):
    value = number(american)
    if value is None or abs(value) < 100:
        return None
    return 1 + (value/100 if value > 0 else 100/abs(value))


def timestamp(value):
    result = pd.to_datetime(value, utc=True, errors='coerce')
    return None if pd.isna(result) else result


def validate_quote_frame(frame):
    """Fail closed on malformed normalized inputs, without consulting outcomes."""
    required = {'game_id', 'season', 'week', 'home_id', 'away_id', 'book', 'line',
                'over_price', 'under_price', 'over_decimal_odds', 'under_decimal_odds',
                'observed_at', 'kickoff', 'market_updated_at', 'source_sha256',
                'receipt_hash_matches', 'outcomes_loaded', 'hours_to_kickoff',
                'within14days', 'same_eastern_gameday_after0630'}
    forbidden = {'actual_total', 'home_score', 'away_score', 'result', 'winner',
                 'profit_units', 'weather_flag', 'weather_eligible'}
    if frame.empty or required - set(frame) or forbidden & set(frame):
        raise ValueError('Empty, incomplete, or outcome-contaminated quote schema')
    if not frame.receipt_hash_matches.eq(True).all() or not frame.outcomes_loaded.eq(False).all():
        raise ValueError('Receipt hash or outcome-separation flag invalid')
    for key in ('game_id', 'season', 'week', 'home_id', 'away_id'):
        values = pd.to_numeric(frame[key], errors='coerce')
        if not (np.isfinite(values) & values.eq(values.round()) & values.gt(0)).all():
            raise ValueError('Invalid integral identity field: ' + key)
    if not frame.season.eq(2026).all() or frame.home_id.eq(frame.away_id).any():
        raise ValueError('Wrong season or identical opponents')
    observed = pd.to_datetime(frame.observed_at, utc=True, errors='coerce')
    kickoff = pd.to_datetime(frame.kickoff, utc=True, errors='coerce')
    updated = pd.to_datetime(frame.market_updated_at, utc=True, errors='coerce')
    if observed.isna().any() or kickoff.isna().any() or (observed >= kickoff).any():
        raise ValueError('Receipt must precede kickoff')
    if (frame.market_updated_at.notna() & updated.isna()).any() or (updated > observed).any():
        raise ValueError('Invalid or future market update')
    hours = (kickoff-observed).dt.total_seconds()/3600
    eastern_o = observed.dt.tz_convert('America/New_York')
    eastern_k = kickoff.dt.tz_convert('America/New_York')
    same_day = eastern_o.dt.date.eq(eastern_k.dt.date) & (eastern_o.dt.hour*60+eastern_o.dt.minute).ge(390)
    if (not np.allclose(frame.hours_to_kickoff, hours) or
            not frame.within14days.eq(hours.le(336)).all() or
            not frame.same_eastern_gameday_after0630.eq(same_day).all()):
        raise ValueError('Stored horizon flags disagree with actual receipt')
    lines = pd.to_numeric(frame.line, errors='coerce')
    if not (np.isfinite(lines) & lines.between(10, 120) & (lines*2).eq((lines*2).round())).all():
        raise ValueError('Invalid full-game total')
    for side in ('over', 'under'):
        expected = frame[side+'_price'].map(decimal_price)
        actual = pd.to_numeric(frame[side+'_decimal_odds'], errors='coerce')
        if expected.isna().any() or not np.allclose(expected, actual, rtol=1e-12, atol=1e-12):
            raise ValueError('Invalid or inconsistent paired price')
    if not frame.source_sha256.astype(str).str.fullmatch('[0-9a-f]{64}').all():
        raise ValueError('Invalid source hash')
    if frame.book.isna().any() or frame.book.astype(str).str.strip().eq('').any():
        raise ValueError('Missing sportsbook')
    if frame.duplicated(['source_sha256', 'game_id', 'observed_at', 'book']).any():
        raise ValueError('Conflicting or duplicated bookmaker snapshot')


def main(root, source_root):
    root, source_root = Path(root).resolve(), Path(source_root).resolve()
    sys.path.insert(0, str(root/'model'))
    from ncaaf_model.teams import normalize_team
    # Deliberately request no status, scores, winners, weather or model outcomes.
    columns = ['game_id','season','week','game_date','home_id','away_id','home_team','away_team','neutral_site']
    schedule_path = root/'model/data/raw/sportsdataverse/cfb_schedule_2026.parquet'
    schedule = pd.read_parquet(schedule_path, columns=columns).drop_duplicates('game_id')
    schedule['kickoff'] = pd.to_datetime(schedule.game_date, utc=True)
    schedule['home_key'] = schedule.home_team.map(normalize_team)
    schedule['away_key'] = schedule.away_team.map(normalize_team)
    by_id = schedule.set_index('game_id')
    quotes, files, errors = [], [], Counter()
    for source, directory in [('the_odds_api','the_odds_api'),('espn_scoreboard','espn_scoreboard')]:
        for path in sorted((source_root/'data/raw'/directory).glob('*.json')):
            if '.meta.' in path.name:
                continue
            meta_path = path.with_suffix('.meta.json')
            if not meta_path.exists():
                errors['missing_metadata'] += 1
                continue
            meta = json.loads(meta_path.read_text())
            observed = timestamp(meta.get('retrieved_at'))
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            matched_hash = digest == meta.get('sha256')
            if observed is None or not matched_hash:
                errors['bad_receipt_or_hash'] += 1
                continue
            body = json.loads(raw)
            # First manually initialized snapshot predates many source updates.
            # Quarantine entire file by content chronology, never guess a fix.
            future_updates = 0
            if source == 'the_odds_api':
                for event in body:
                    for book in event.get('bookmakers', []):
                        for market in book.get('markets', []):
                            update = timestamp(market.get('last_update') or book.get('last_update'))
                            future_updates += int(update is not None and update > observed)
            stat = path.stat()
            file_meta = {'source':source,'source_path':str(path),'source_sha256':digest,
                         'observed_at':observed.isoformat(),'metadata_sha256':hashlib.sha256(meta_path.read_bytes()).hexdigest(),
                         'receipt_hash_matches':True,'source_updates_after_receipt':future_updates,
                         'filesystem_mtime':datetime.fromtimestamp(stat.st_mtime,timezone.utc).isoformat(),
                         'filesystem_birthtime':datetime.fromtimestamp(stat.st_birthtime,timezone.utc).isoformat() if hasattr(stat,'st_birthtime') else None,
                         'receipt_evidence':'local_metadata_sha256_and_filesystem_dates; raw archive not committed to Git; not independently timestamp-attested'}
            files.append(file_meta)
            if future_updates:
                errors['source_file_chronology_conflict'] += 1
                continue
            iterable = body if source == 'the_odds_api' else body.get('events', [])
            for event in iterable:
                normalized = []
                if source == 'the_odds_api':
                    kickoff = timestamp(event.get('commence_time'))
                    matches = schedule.loc[schedule.home_key.eq(normalize_team(event.get('home_team'))) & schedule.away_key.eq(normalize_team(event.get('away_team')))]
                    if kickoff is not None:
                        matches = matches.loc[(matches.kickoff-kickoff).abs().lt(pd.Timedelta(hours=1))]
                    if kickoff is None or len(matches) != 1:
                        errors['event_mapping_missing_or_ambiguous'] += 1
                        continue
                    game = matches.iloc[0]
                    for book in event.get('bookmakers', []):
                        markets = {x.get('key'):x for x in book.get('markets', [])}
                        total = markets.get('totals')
                        if not total:
                            continue
                        raw_outcomes = total.get('outcomes', [])
                        outcomes = {x.get('name'):x for x in raw_outcomes}
                        if len(raw_outcomes) != 2 or set(outcomes) != {'Over','Under'} or outcomes['Over'].get('point') != outcomes['Under'].get('point'):
                            errors['unpaired_total'] += 1
                            continue
                        spread = next((x.get('point') for x in markets.get('spreads',{}).get('outcomes',[]) if x.get('name') == event.get('home_team')), None)
                        normalized.append({'book':book.get('key'),'line':number(outcomes['Over'].get('point')),
                            'over_price':number(outcomes['Over'].get('price')),'under_price':number(outcomes['Under'].get('price')),
                            'market_updated_at':timestamp(total.get('last_update') or book.get('last_update')),
                            'market_home_spread':number(spread),'archived_event_id':event.get('id'),'source_prestate':'odds_feed_event_before_kickoff'})
                else:
                    gid = number(event.get('id'))
                    if gid is None or int(gid) not in by_id.index:
                        continue
                    game = by_id.loc[int(gid)].copy(); game['game_id'] = int(gid)
                    for comp in event.get('competitions', []):
                        kickoff = timestamp(comp.get('date') or event.get('date'))
                        status = comp.get('status') or event.get('status', {})
                        if kickoff is None or status.get('type',{}).get('state') != 'pre':
                            continue
                        home = next((x for x in comp.get('competitors',[]) if x.get('homeAway')=='home'),{})
                        away = next((x for x in comp.get('competitors',[]) if x.get('homeAway')=='away'),{})
                        if number(home.get('team',{}).get('id')) != game.home_id or number(away.get('team',{}).get('id')) != game.away_id:
                            errors['espn_team_id_mismatch'] += 1
                            continue
                        if abs((game.kickoff-kickoff).total_seconds()) >= 3600:
                            errors['espn_kickoff_mismatch'] += 1
                            continue
                        for odd in comp.get('odds', []):
                            provider = odd.get('provider', {})
                            if str(provider.get('id')) != '100' or 'draftkings' not in str(provider.get('name','')).lower():
                                errors['espn_provider_not_approved'] += 1
                                continue
                            total = odd.get('total') or {}
                            over = (total.get('over') or {}).get('close') or {}
                            under = (total.get('under') or {}).get('close') or {}
                            ol = number(re.sub(r'^[oOuU]','',str(over.get('line',''))))
                            ul = number(re.sub(r'^[oOuU]','',str(under.get('line',''))))
                            if ol is None or ol != ul or ol != number(odd.get('overUnder')):
                                errors['espn_unpaired_total'] += 1
                                continue
                            spread = ((odd.get('pointSpread') or {}).get('home') or {}).get('close') or {}
                            normalized.append({'book':'draftkings','line':ol,'over_price':number(over.get('odds')),
                                'under_price':number(under.get('odds')),'market_updated_at':None,'market_home_spread':number(spread.get('line')),
                                'archived_event_id':str(int(gid)),'source_prestate':'explicit_espn_pre; DraftKings100; nested_total_close_is_current_at_receipt_not_final_closing_claim'})
                if kickoff is None or observed >= kickoff:
                    errors['receipt_not_before_kickoff'] += len(normalized)
                    continue
                for quote in normalized:
                    if quote['line'] is None or decimal_price(quote['over_price']) is None or decimal_price(quote['under_price']) is None:
                        errors['invalid_line_or_price'] += 1
                        continue
                    update = quote['market_updated_at']
                    if update is not None and update > observed:
                        errors['quote_updated_after_receipt'] += 1
                        continue
                    eastern = kickoff.tz_convert('America/New_York')
                    observed_eastern = observed.tz_convert('America/New_York')
                    same_day = eastern.date() == observed_eastern.date()
                    after0630 = observed_eastern.hour*60+observed_eastern.minute >= 390
                    hours = (kickoff-observed).total_seconds()/3600
                    quotes.append({**quote,'game_id':int(game.game_id),'season':int(game.season),'week':int(game.week),
                        'observed_at':observed,'kickoff':kickoff,'home_team':game.home_team,'away_team':game.away_team,
                        'home_id':int(game.home_id),'away_id':int(game.away_id),'neutral_site':bool(game.neutral_site),
                        'over_decimal_odds':decimal_price(quote['over_price']),'under_decimal_odds':decimal_price(quote['under_price']),
                        'source':source,'source_path':str(path),'source_sha256':digest,
                        'receipt_evidence':file_meta['receipt_evidence'],'receipt_hash_matches':True,
                        'market_timestamp_present':update is not None,'hours_to_kickoff':hours,
                        'same_eastern_gameday_after0630':same_day and after0630,
                        'within14days':hours<=14*24,'outcomes_loaded':False})
    if not quotes:
        raise ValueError('No valid paired quotes; exclusions: ' + json.dumps(dict(errors), sort_keys=True))
    frame = pd.DataFrame(quotes).sort_values(['observed_at','game_id','source','book']).reset_index(drop=True)
    # Multiple scoreboards can carry identical same-book records at one receipt;
    # preserve quote disagreements but remove byte-identical observation copies.
    frame = frame.drop_duplicates(['source','game_id','observed_at','book','line','over_price','under_price'])
    validate_quote_frame(frame)
    directory = root/'model/data/raw/alternative'
    directory.mkdir(parents=True,exist_ok=True)
    path = directory/'archived_2026_paired_quotes.parquet'
    frame.to_parquet(path,index=False)
    now = pd.Timestamp.now(tz='UTC')
    eligible = frame.loc[frame.within14days]
    same_day = eligible.loc[eligible.same_eastern_gameday_after0630]
    past = eligible.loc[eligible.kickoff < now]
    manifest = {'version':'2026-archived-paired-quotes-feasibility-v1','created_at':now.isoformat(),
        'no_outcomes_loaded':True,'no_weather_evaluated':True,'primary_input_root':str(source_root),
        'mapping_schedule':str(schedule_path),'mapping_schedule_sha256':hashlib.sha256(schedule_path.read_bytes()).hexdigest(),
        'selection':'All valid paired full-game totals, exact IDs or unique canonical team/kickoff match; receipt strictly before kickoff; no outcome/forecast selection. Initial chronology-conflicted file excluded as a whole.',
        'files':files,'errors_or_exclusions':dict(errors),'artifact':str(path),'artifact_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'counts':{'paired_records':len(frame),'games':int(frame.game_id.nunique()),'within14days_games':int(eligible.game_id.nunique()),
                  'past_kickoff_games_within14days':int(past.game_id.nunique()),'same_day_after0630_games':int(same_day.game_id.nunique()),
                  'past_same_day_after0630_games':int(same_day.loc[same_day.kickoff<now,'game_id'].nunique()),
                  'source_records':frame.source.value_counts().to_dict(),'book_records':frame.book.value_counts().to_dict()},
        'same_day_game_manifest':same_day.sort_values('observed_at').drop_duplicates('game_id')[['game_id','home_team','away_team','kickoff','observed_at','hours_to_kickoff','source','source_path']].assign(kickoff=lambda f:f.kickoff.astype(str),observed_at=lambda f:f.observed_at.astype(str)).to_dict('records'),
        'limitations':['Raw source archives were not committed before games; local metadata, SHA256, filesystem times and source market timestamps support but do not cryptographically attest capture time.',
            'A sibling daily-board Git commit bearing a Sep1 author/committer date preserves Aug31 consensus totals, not individual bookmaker prices or raw-file hashes. Git dates are mutable; no independent server timestamp has been verified.',
            'Actual snapshots usually around09:00Eastern; same-day check uses actual receipt and does not pretend06:30execution. Some earlier-than-gameday quotes are multiple days old.',
            'This is a previously archived2026quote check for a subsequently frozen weather hypothesis, not a strategy registered prospectively before these games.']}
    (directory/'archived_2026_quote_manifest.json').write_text(json.dumps(manifest,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'counts':manifest['counts'],'exclusions':dict(errors),'same_day_games':[r['game_id'] for r in manifest['same_day_game_manifest']]},indent=2))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--source-root',type=Path,required=True,help='Original model workspace containing data/raw/the_odds_api and espn_scoreboard')
    args=parser.parse_args();main(args.root,args.source_root)
