"""Independently verify one original weather/price capture; no network or outcomes.

No project modules are imported. The frozen alias dictionary is read as literal
data; cohort, request, weather, quote and timing calculations are reconstructed.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit

UTC = timezone.utc
START = datetime(2026, 9, 9, 3, tzinfo=UTC)
END = datetime(2026, 9, 16, 3, tzinfo=UTC)
BOOKS = {'DraftKings': 'draftkings', 'FanDuel': 'fanduel'}
VARS = ('temperature_2m', 'relative_humidity_2m', 'wind_speed_10m')
CATALOG_SHA = 'eec79812c7faef8d70b21d9ad4018b3d2a71074b27b72508358d71ae56519ce7'


def stamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    assert result.tzinfo is not None, 'Naive timestamp'
    return result.astimezone(UTC)


def iso(value):
    return value.isoformat().replace('+00:00', 'Z')


def sha(body):
    return hashlib.sha256(body).hexdigest()


def digest(value):
    return sha(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())


def close(a, b):
    assert isinstance(a, (int, float)) and not isinstance(a, bool) and math.isfinite(a)
    assert math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-9), 'Numerical reconstruction mismatch'


class Audit:
    def __init__(self, root):
        self.root = root.resolve()
        self.base = self.root / 'data/runtime/weather_revisions'
        self.loaded = {}
        tree = ast.parse((self.root / 'ncaaf_model/teams.py').read_text())
        self.aliases = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                            and any(isinstance(t, ast.Name) and t.id == 'ALIASES' for t in n.targets))

    def name(self, name):
        chars = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().lower().replace('&', 'and')
        simple = ''.join(c for c in chars if c.isascii() and c.isalnum())
        return self.aliases.get(simple, simple)

    def envelope(self, relative):
        if relative in self.loaded:
            return self.loaded[relative]
        path = (self.root / relative).resolve()
        assert path.is_relative_to(self.base / 'receipts'), 'Receipt path outside archive'
        receipt = json.loads(path.read_text())
        assert receipt['schema_version'] == 'raw-http-receipt-v1'
        assert receipt['receipt_path'] == relative
        assert path.name == digest({k: v for k, v in receipt.items() if k != 'receipt_path'}) + '.json'
        assert stamp(receipt['requested_at']) <= stamp(receipt['received_at'])
        parsed = urlsplit(receipt['request']['url'])
        assert parsed.scheme == 'https' and not parsed.query and not parsed.fragment and not parsed.username
        assert not {'apikey', 'api_key', 'key', 'token', 'authorization'} & {k.lower() for k in receipt['request']['params']}
        assert not {'authorization', 'cookie', 'set_cookie'} & set(receipt['response_headers'])
        payload = None
        if receipt['body_path']:
            body_path = (self.root / receipt['body_path']).resolve()
            assert body_path.is_relative_to(self.base / 'bodies')
            body = gzip.decompress(body_path.read_bytes())
            assert sha(body) == receipt['body_sha256']
            assert len(body) == receipt['body_size_bytes']
            try:
                payload = json.loads(body)
            except (UnicodeDecodeError, ValueError):
                assert receipt['transport_error'] == 'invalid_json'
        else:
            assert receipt['transport_error'] or receipt['body_withheld']
        self.loaded[relative] = receipt, payload
        return receipt, payload

    def success(self, relative):
        receipt, payload = self.envelope(relative)
        assert receipt['status_code'] == 200 and receipt['transport_error'] is None
        assert not receipt['body_withheld']
        return receipt, payload

    def identity(self, competition):
        people = competition['competitors']
        assert len(people) == 2 and {p['homeAway'] for p in people} == {'home', 'away'}
        result = {}
        for person in people:
            side, team = person['homeAway'], person['team']
            assert str(team['id']).isdigit() and int(team['id']) > 0 and team['displayName']
            result[side + '_id'] = str(team['id'])
            result[side + '_team'] = team['displayName']
        assert result['home_id'] != result['away_id']
        return result

    def cohort(self, manifest):
        start = stamp(manifest['capture_started_at'])
        receipt, payload = self.success(manifest['inventory_receipt'])
        assert receipt['purpose'] == 'official_frozen_cohort'
        assert receipt['request']['params']['groups'] == 80
        assert stamp(receipt['requested_at']) >= start
        candidates = {}
        for event in payload['events']:
            try:
                gid = str(event['id'])
                assert gid.isdigit() and int(gid) > 0
                assert len(event['competitions']) == 1
                c = event['competitions'][0]
                assert str(c['id']) == gid
                kickoff = stamp(c['date'])
                if not start < kickoff <= start + timedelta(days=7):
                    continue
                assert c['status']['type']['state'] == 'pre' and c.get('dateValid') is not False
                row = {'game_id': gid, 'kickoff': iso(kickoff), **self.identity(c),
                       'inventory_venue_id': str(c.get('venue', {}).get('id', '')),
                       'inventory_neutral_site': c.get('neutralSite')}
                candidates.setdefault(gid, []).append(row)
            except (AssertionError, AttributeError, KeyError, TypeError, ValueError):
                continue
        qualifying = sorted([rows[0] for rows in candidates.values() if len(rows) == 1],
                            key=lambda r: (stamp(r['kickoff']), int(r['game_id'])))
        assert len(qualifying) == manifest['enumerated_games']
        frozen = json.loads((self.root / manifest['cohort_path']).read_text())
        assert frozen['inventory_receipt'] == manifest['inventory_receipt']
        assert stamp(frozen['frozen_at']) >= stamp(receipt['received_at'])
        assert frozen['games'] == qualifying[:150]
        assert manifest['counts']['cohort_games'] == len(frozen['games'])
        if manifest['rows']:
            assert [r['game_id'] for r in manifest['rows']] == [r['game_id'] for r in frozen['games']]
            for row, expected in zip(manifest['rows'], frozen['games']):
                assert all(row[k] == v for k, v in expected.items())
        return frozen

    def context(self, row):
        receipt, payload = self.success(row['summary_receipt'])
        c = payload['header']['competitions'][0]
        assert str(payload['header']['id']) == row['game_id'] == str(c['id'])
        assert len(payload['header']['competitions']) == 1
        identity = self.identity(c)
        assert all(identity[k] == row[k] for k in ('home_id', 'away_id'))
        assert all(self.name(identity[k]) == self.name(row[k]) for k in ('home_team', 'away_team'))
        assert stamp(c['date']) == stamp(row['kickoff'])
        assert c['status']['type']['state'] == 'pre' and c.get('dateValid') is not False
        venue = str(payload['gameInfo']['venue']['id'])
        roof_receipt, roof = self.success(row['roof_receipt'])
        assert str(roof['id']) == venue
        expected = {'game_id': row['game_id'], 'kickoff': row['kickoff'], **identity, 'venue_id': venue,
                    'neutral_site': c.get('neutralSite'), 'state': 'pre', 'indoor': roof.get('indoor')}
        assert row['context'] == expected and row['context_id'] == digest(expected)
        return max(stamp(receipt['received_at']), stamp(roof_receipt['received_at']))

    def weather(self, row, record, catalog, start, previous=False):
        receipt, payload = self.success(record['receipt_path'])
        spec, measurement = record['request_spec'], record['measurement']
        kickoff = stamp(row['kickoff'])
        hours = [kickoff.replace(minute=0, second=0, microsecond=0) + timedelta(hours=i) for i in range(4)]
        vid = row['context']['venue_id']
        assert row['context']['indoor'] is False and row['context']['neutral_site'] is False
        coords = catalog['venues'][vid]
        assert spec['venue'] == {'venue_id': vid, **coords}
        params = {'latitude': coords['latitude'], 'longitude': coords['longitude'], 'models': 'gfs_global',
                  'temperature_unit': 'fahrenheit', 'wind_speed_unit': 'mph', 'timezone': 'GMT'}
        suffix = '_previous_day2' if previous else ''
        params['hourly'] = ','.join(n + suffix for n in VARS)
        mature = hours[-1] - timedelta(hours=48) + timedelta(hours=6)
        if previous:
            endpoint = 'https://previous-runs-api.open-meteo.com/v1/forecast'
            params.update(start_date=kickoff.date().isoformat(), end_date=(kickoff + timedelta(hours=3)).date().isoformat())
            assert start >= mature and stamp(spec['capture_started_at']) >= mature
            assert stamp(receipt['requested_at']) >= mature and stamp(receipt['received_at']) >= mature
            assert spec['context_id'] == row['context_id'] and stamp(spec['kickoff']) == kickoff
            assert measurement['requested_run'] is None and measurement['actual_run_lead_hours'] is None
        else:
            endpoint = 'https://single-runs-api.open-meteo.com/v1/forecast'
            x = start - timedelta(hours=6)
            run = x.replace(hour=6*(x.hour//6), minute=0, second=0, microsecond=0)
            params.update(run=run.strftime('%Y-%m-%dT%H:%M'), forecast_days=8)
            assert stamp(spec['capture_started_at']) == start and stamp(spec['requested_run']) == run
            assert stamp(receipt['requested_at']) >= start
            assert stamp(receipt['requested_at']) >= self.context(row)
        assert receipt['request'] == {'url': endpoint, 'params': params}
        assert spec['source_url'] == endpoint and spec['parameters'] == params
        request_identity = {k: spec[k] for k in ('product', 'source_url', 'parameters', 'venue', 'context_id')}
        if previous:
            request_identity['kickoff'] = spec['kickoff']
        assert digest(request_identity) == spec['request_key'] == measurement['request_key']
        assert stamp(receipt['received_at']) < kickoff
        assert payload['utc_offset_seconds'] == 0 and payload['timezone'] in ('GMT', 'UTC', 'Etc/UTC')
        assert payload['timezone_abbreviation'] in ('GMT', 'UTC')
        hourly = payload['hourly']
        times = [datetime.strptime(t, '%Y-%m-%dT%H:%M').replace(tzinfo=UTC) for t in hourly['time']]
        assert times and all(b-a == timedelta(hours=1) for a, b in zip(times, times[1:]))
        assert payload['hourly_units']['time'] == 'iso8601'
        for variable, allowed in zip(VARS, [('°F',), ('%',), ('mph', 'mp/h')]):
            assert payload['hourly_units'][variable + suffix] in allowed
            assert len(hourly[variable + suffix]) == len(times)
        if not previous:
            assert len(times) == 192 and times[0] == run
            assert measurement['actual_run_lead_hours'] == [(h-run).total_seconds()/3600 for h in hours]
        values = {}
        for variable in VARS:
            valid = hours if variable == 'wind_speed_10m' else hours[:1]
            values[variable] = [hourly[variable + suffix][times.index(h)] for h in valid]
            for v in values[variable]:
                close(v, v)
        assert -150 <= values[VARS[0]][0] <= 160 and 0 <= values[VARS[1]][0] <= 100
        assert all(0 <= w <= 250 for w in values[VARS[2]])
        close(measurement['temperature_f'], values[VARS[0]][0])
        close(measurement['relative_humidity_percent'], values[VARS[1]][0])
        assert measurement['wind_mph_by_hour'] == values[VARS[2]]
        close(measurement['wind_mph_four_hour_mean'], sum(values[VARS[2]])/4)
        assert measurement['valid_hours_utc'] == [iso(h) for h in hours]
        assert stamp(measurement['requested_at']) == stamp(receipt['requested_at'])
        assert stamp(measurement['received_at']) == stamp(receipt['received_at'])
        assert measurement['body_sha256'] == receipt['body_sha256']
        lat, lon = float(payload['latitude']), float(payload['longitude'])
        assert -90 <= lat <= 90 and -180 <= lon <= 180 and -500 <= payload['elevation'] <= 6000
        a, b = math.radians(coords['latitude']), math.radians(lat)
        hav = math.sin((b-a)/2)**2 + math.cos(a)*math.cos(b)*math.sin(math.radians(lon-coords['longitude'])/2)**2
        distance = 2*6371.0088*math.asin(math.sqrt(min(1., max(0., hav))))
        assert distance <= 50
        close(measurement['returned_point']['distance_from_requested_km'], distance)
        close(measurement['returned_point']['latitude'], lat)
        close(measurement['returned_point']['longitude'], lon)
        close(measurement['returned_point']['elevation_m'], payload['elevation'])
        assert not measurement['initialization_independently_verified'] and not measurement['publication_time_independently_verified']

    def quote(self, row, quote):
        receipt, payload = self.success(quote['receipt_path'])
        assert receipt['purpose'] == 'fresh_totals_after_weather'
        assert receipt['request']['url'] == 'https://api.odds-api.io/v3/odds/multi'
        assert quote['provider_event_id'] in receipt['request']['params']['eventIds'].split(',')
        events = payload if isinstance(payload, list) else [payload]
        matches = [e for e in events if isinstance(e, dict) and str(e.get('id')) == quote['provider_event_id']]
        assert len(matches) == 1
        event = matches[0]
        assert event['status'] in ('pending', 'upcoming', 'scheduled', 'prematch')
        assert stamp(event['date']) == stamp(row['kickoff'])
        assert self.name(event['home']) == self.name(row['home_team']) and self.name(event['away']) == self.name(row['away_team'])
        title = next(k for k, v in BOOKS.items() if v == quote['sportsbook'])
        markets = [m for m in event['bookmakers'][title] if m.get('name') == 'Totals']
        assert len(markets) == 1
        market = markets[0]
        assert market.get('period') in (None, 'full_game', 'Full Game', 'FT')
        matching = []
        for offered in market['odds']:
            try:
                if isinstance(offered, dict) and float(offered.get('hdp', 'nan')) == quote['line']:
                    matching.append(offered)
            except (TypeError, ValueError):
                pass
        assert len(matching) == 1
        raw = matching[0]
        assert not any(isinstance(raw[k], bool) for k in ('hdp', 'over', 'under'))
        line, over, under = [float(raw[k]) for k in ('hdp', 'over', 'under')]
        assert all(math.isfinite(x) for x in (line, over, under)) and line > 0 and line*2 == round(line*2) and min(over, under) > 1
        close(quote['line'], line)
        close(quote['over_decimal_odds'], over)
        close(quote['under_decimal_odds'], under)
        assert quote['game_id'] == row['game_id'] and quote['period'] == 'full_game'
        assert quote['requested_at'] == receipt['requested_at'] and quote['observed_at'] == receipt['received_at']
        assert stamp(quote['observed_at']) < stamp(row['kickoff'])
        if market.get('updatedAt'):
            assert stamp(quote['market_updated_at']) == stamp(market['updatedAt']) <= stamp(quote['observed_at']) + timedelta(seconds=5)
        else:
            assert quote['market_updated_at'] is None
        assert quote['body_sha256'] == receipt['body_sha256']
        assert quote['quote_id'] == digest({k: v for k, v in quote.items() if k != 'quote_id'})

    def execute(self, path):
        body = path.read_bytes()
        manifest = json.loads(body)
        assert manifest['schema_version'] == 'weather-revision-capture-v1'
        start, end = [stamp(manifest[k]) for k in ('capture_started_at', 'capture_completed_at')]
        assert START <= start < END and start <= end
        assert len(manifest['receipts']) == len(set(manifest['receipts']))
        receipts = [self.envelope(p)[0] for p in manifest['receipts']]
        assert all(start <= stamp(r['requested_at']) <= stamp(r['received_at']) <= end for r in receipts)
        counts = manifest['counts']
        assert counts['total_requests'] == len(receipts)
        assert counts['failed_requests'] == sum(r['status_code'] != 200 or bool(r['transport_error']) for r in receipts)
        assert manifest['stored_response_bytes'] == sum(r.get('body_size_bytes', 0) for r in receipts)
        catalog_path = self.root / 'data/models/weather_venues_v1.json'
        assert sha(catalog_path.read_bytes()) == CATALOG_SHA
        catalog = json.loads(catalog_path.read_text())
        frozen = self.cohort(manifest)
        quoted, paired, weather = 0, 0, 0
        gaps = []
        for row in manifest['rows']:
            if row['context'] is not None:
                self.context(row)
            if row['single_run'] is not None:
                self.weather(row, row['single_run'], catalog, start)
                weather += 1
            if row['previous_day2'] is not None:
                self.weather(row, row['previous_day2'], catalog, start, previous=True)
            ids = {}
            for quote in row['quotes']:
                self.quote(row, quote)
                assert quote['quote_id'] not in ids
                ids[quote['quote_id']] = quote
                quoted += 1
            if row['pairs']:
                assert row['context_recheck_ok'] and row['single_run']
                recheck, context_body = self.success(row['context_recheck_receipt'])
                original = self.success(row['summary_receipt'])[1]
                c, old = context_body['header']['competitions'][0], original['header']['competitions'][0]
                assert context_body['header']['id'] == original['header']['id']
                assert str(c['id']) == row['game_id'] and c['status']['type']['state'] == 'pre'
                assert self.identity(c) == self.identity(old) and stamp(c['date']) == stamp(old['date'])
                assert c.get('neutralSite') == old.get('neutralSite') and c.get('dateValid') is not False
                assert str(context_body['gameInfo']['venue']['id']) == row['context']['venue_id']
                assert row['context_recheck_received_at'] == recheck['received_at']
                for pair in row['pairs']:
                    quote = ids[pair['quote_id']]
                    measurement = row['single_run']['measurement']
                    assert pair['context_id'] == row['context_id']
                    assert pair['weather_receipt'] == row['single_run']['receipt_path']
                    assert pair['quote_receipt'] == quote['receipt_path']
                    assert pair['context_recheck_receipt'] == row['context_recheck_receipt']
                    assert stamp(measurement['received_at']) <= stamp(recheck['requested_at']) <= stamp(recheck['received_at']) <= stamp(quote['requested_at']) <= stamp(quote['observed_at']) < stamp(row['kickoff'])
                    assert stamp(quote['requested_at']) >= stamp(manifest['weather_stage_completed_at'])
                    gap = (stamp(quote['observed_at'])-stamp(measurement['received_at'])).total_seconds()
                    close(pair['weather_to_quote_seconds'], gap)
                    gaps.append(gap)
                    paired += 1
        rows = manifest['rows']
        rebuilt = {'weather_available_games': weather,
            'mature_comparator_games': sum(bool(r['previous_day2']) for r in rows),
            'two_book_games': sum(len({q['sportsbook'] for q in r['quotes']}) == 2 for r in rows),
            'paired_games': sum(bool(r['pairs']) for r in rows),
            'paired_two_book_games': sum(len({p['sportsbook'] for p in r['pairs']}) == 2 for r in rows)}
        assert all(counts[k] == v for k, v in rebuilt.items())
        odds = sorted([r for r in receipts if urlsplit(r['request']['url']).hostname == 'api.odds-api.io'], key=lambda r: stamp(r['requested_at']))
        assert len(odds) <= 17
        batch_ids = []
        for i, receipt in enumerate(odds):
            if receipt['request']['url'].endswith('/odds/multi'):
                ids = receipt['request']['params']['eventIds'].split(',')
                assert 1 <= len(ids) <= 10 and len(ids) == len(set(ids))
                batch_ids.extend(ids)
                previous = odds[i-1]
                assert int(previous['response_headers']['x_ratelimit_remaining']) > 20
                assert previous['status_code'] not in (401, 403, 429)
        assert len(batch_ids) == len(set(batch_ids)) and len(batch_ids) <= len(frozen['games'])
        if batch_ids:
            inventory = next(r for r in odds if r['request']['url'].endswith('/events'))
            assert int(inventory['response_headers']['x_ratelimit_remaining']) >= math.ceil(len(batch_ids)/10) + 20
            source_events = self.success(inventory['receipt_path'])[1]
            official_keys, source_keys = {}, {}
            for row in manifest['rows']:
                key = self.name(row['home_team']), self.name(row['away_team']), stamp(row['kickoff'])
                official_keys.setdefault(key, []).append(row['game_id'])
            for event in source_events:
                try:
                    if event['status'] != 'pending' or not any(s in str(event.get('league', {})).lower() for s in ('college', 'ncaa')):
                        continue
                    key = self.name(event['home']), self.name(event['away']), stamp(event['date'])
                    source_keys.setdefault(key, []).append(str(event['id']))
                except (KeyError, TypeError, ValueError):
                    continue
            provider_to_game = {source_keys[k][0]: ids[0] for k, ids in official_keys.items()
                                if len(ids) == 1 and len(source_keys.get(k, [])) == 1 and source_keys[k][0].isdigit()}
            assert set(batch_ids) <= set(provider_to_game)
            for row in rows:
                for quote in row['quotes']:
                    assert provider_to_game[quote['provider_event_id']] == row['game_id']
        provenance = {p: {'recorded': h, 'current_matches': (self.root / p).exists() and sha((self.root / p).read_bytes()) == h}
                      for p, h in manifest['provenance'].items()}
        return {'schema_version': 'weather-revision-independent-audit-v1', 'audit_passed': True,
            'run_id': manifest['run_id'], 'run_attempt': manifest['run_attempt'], 'manifest_sha256': sha(body),
            'capture_status': manifest['status'], 'capture_started_at': manifest['capture_started_at'],
            'capture_completed_at': manifest['capture_completed_at'], 'counts_reconstructed': counts,
            'original_receipts_verified': len(self.loaded), 'current_capture_receipts': len(receipts),
            'normalized_quote_pairs_reconstructed': quoted, 'weather_quote_links_reconstructed': paired,
            'weather_quote_gap_seconds': {'minimum': min(gaps), 'maximum': max(gaps)} if gaps else None,
            'odds_requests': len(odds), 'provenance': provenance,
            'no_network': True, 'outcomes_extracted': False, 'performance_evaluated': False,
            'method': 'Independent standard-library reconstruction; no project parser imported. Frozen team aliases read as literal data.',
            'limitation': 'Receipt/schema/measurement/price linkage audit, not forecast accuracy, profitability, publication-time certification, or proof of complete provider coverage.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1] / 'model')
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    paths = sorted((args.root / 'data/runtime/weather_revisions/runs').glob('*.json'))
    path = args.manifest or max(paths, key=lambda p: stamp(json.loads(p.read_text())['capture_started_at']))
    result = Audit(args.root).execute(path)
    result['audit_script_sha256'] = sha(Path(__file__).read_bytes())
    output = args.output or args.root / 'reports/weather_revision_capture_audit.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    report = output.parent / 'WEATHER_REVISION_FIRST_CAPTURE_AUDIT.md'
    report.write_text('# Independent pilot capture receipt audit\n\n'
        f"Run `{result['run_id']}` attempt `{result['run_attempt']}` passed the independent receipt audit. Capture status remains **{result['capture_status']}**.\n\n"
        f"Verified {result['current_capture_receipts']} current HTTP receipts, {result['counts_reconstructed']['weather_available_games']} explicit-run game measurements, "
        f"{result['normalized_quote_pairs_reconstructed']} same-book quote pairs and {result['weather_quote_links_reconstructed']} weather/quote links. "
        f"The collector used {result['odds_requests']} Odds API IO requests. All original body/receipt hashes, retained numeric quotes, game-hour measurements, applicable cohort/timing checks and reported counts matched.\n\n"
        'This is an operational integrity check. It does not establish complete source coverage, certified first publication, forecast accuracy or positive EV. No outcomes were extracted and no policy was changed.\n\n'
        f"Manifest SHA-256: `{result['manifest_sha256']}`. Machine-readable details: [{output.name}]({output.name}).\n")
    print(json.dumps({k: result[k] for k in ('run_id', 'audit_passed', 'current_capture_receipts', 'odds_requests')}, sort_keys=True))


if __name__ == '__main__':
    main()
