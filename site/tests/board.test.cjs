const test = require('node:test');
const assert = require('node:assert/strict');
const {health, currentPicks, currentWeatherPicks, weatherHealth, weatherMeasurements, currentHedges, evidenceState, dateKey, dedupeResults, safeUrl, percent} = require('../app.js');
const now = new Date('2026-09-12T12:00:00Z');
const board = {schema_version: 1, generated_at: '2026-09-12T10:30:00Z', date: '2026-09-12', status: 'ok'};
const pick = {game_id: '1', kickoff: '2026-09-12T19:00:00Z', quote_time: '2026-09-12T10:25:00Z', robust_ev: .03};
test('fresh board makes current pregame selections available', () => {assert.equal(health(board, now).usable, true); assert.equal(currentPicks(board, [pick], now).length, 1);});
test('stale or invalid publications cannot expose current picks', () => {
  for (const generated_at of ['2026-09-11T09:59:00Z', 'invalid', null, '2026-09-12T12:06:00Z']) assert.equal(currentPicks({...board, generated_at}, [pick], now).length, 0);
});
test('data failures and previous-day publications suppress picks', () => {
  assert.equal(health({...board, status:'unavailable'}, now).usable, false);
  assert.equal(currentPicks({...board, date:'2026-09-11'}, [pick], now).length, 0);
});
test('started games, future-day games and stale quotes are not today picks', () => {
  for (const changed of [{kickoff:'2026-09-12T11:00:00Z'}, {kickoff:'2026-09-13T19:00:00Z'}, {quote_time:'2026-09-11T09:00:00Z'}, {quote_time:null}]) assert.equal(currentPicks(board, [{...pick, ...changed}], now).length, 0);
});
test('upcoming section contains future Eastern dates only', () => {
  assert.equal(currentPicks(board, [pick], now, false).length, 0);
  assert.equal(currentPicks(board, [{...pick,kickoff:'2026-09-13T19:00:00Z'}], now, false).length, 1);
});
test('Eastern dates handle UTC midnight and daylight saving transitions', () => {
  assert.equal(dateKey('2026-09-13T01:00:00Z'), '2026-09-12');
  assert.equal(dateKey('2026-11-01T05:30:00Z'), '2026-11-01');
  assert.equal(dateKey('2026-11-01T06:30:00Z'), '2026-11-01');
});
test('forward rows retain first recorded candidate/game and keep other candidates', () => {
  const rows = dedupeResults([{game_id:'1',candidate:'a',recorded_at:'2026-09-02T00:00:00Z', line:52}, {game_id:'1',candidate:'a',recorded_at:'2026-09-01T00:00:00Z', line:50}, {game_id:'1',candidate:'b',recorded_at:'2026-09-01T00:00:00Z'}]);
  assert.equal(rows.length, 2); assert.equal(rows.find(row => row.candidate==='a').line, 50);
});
test('unsafe source links are rejected and missing metrics are not rendered as zeros', () => { assert.equal(safeUrl('javascript:alert(1)'), null); assert.equal(safeUrl('data:text/html,hello'), null); assert.equal(safeUrl('https://example.com'), 'https://example.com'); assert.equal(percent(null), '—'); assert.equal(percent(0), '0.0%'); });
test('hedge scenarios require fresh observations for both legs and a future kickoff', () => {
  const hedge = {both_recently_observed:true, kickoff:'2026-09-12T19:00:00Z', over:{observed_at:'2026-09-12T11:45:00Z'}, under:{observed_at:'2026-09-12T11:45:00Z'}, execution_confirmed:false};
  const makeBoard = row => ({...board,market_opportunities:{arbitrages:[row]}});
  assert.equal(currentHedges(makeBoard(hedge),now).length,1);
  assert.equal(currentHedges(makeBoard({...hedge,under:{observed_at:'2026-09-12T10:45:00Z'}}),now).length,0);
  assert.equal(currentHedges(makeBoard({...hedge,kickoff:'2026-09-12T11:00:00Z'}),now).length,0);
  assert.equal(currentHedges(makeBoard({...hedge,both_recently_observed:false}),now).length,0);
});
test('prior evidence quarantine warning does not hide replacement-data metrics', () => {
  const replacement = evidenceState({historical_evidence_status:'replacement_development_only',prior_historical_evidence_quarantined:true});
  assert.equal(replacement.showWarning,true);
  assert.equal(replacement.hideMetrics,false);
  assert.equal(replacement.replacement,true);
  const old = evidenceState({historical_evidence_status:'quarantined_market_provenance'});
  assert.equal(old.showWarning,true);
  assert.equal(old.hideMetrics,true);
});

const weatherPick = {...pick, side:'under', line:47.5, decimal_odds:1.91, quote_time:'2026-09-12T11:45:00Z', win_probability:null, expected_value:null};
const weatherBoard = {...board, weather_strategy:{status:'ok',as_of:'2026-09-12T11:45:00Z',today_picks:[weatherPick]}};
test('weather paper rule can display a valid current under without fabricated probabilities', () => {
  assert.equal(weatherHealth(weatherBoard,now).usable,true);
  const rows=currentWeatherPicks(weatherBoard,now);
  assert.equal(rows.length,1); assert.equal(rows[0].win_probability,null); assert.equal(rows[0].expected_value,null);
});
test('weather refresh must succeed independently and on the current Eastern date', () => {
  for (const changed of [{status:'unavailable'}, {as_of:null}, {as_of:'2026-09-11T20:00:00Z'}, {as_of:'2026-09-12T12:06:00Z'}]) assert.equal(currentWeatherPicks({...weatherBoard,weather_strategy:{...weatherBoard.weather_strategy,...changed}},now).length,0);
  assert.equal(currentWeatherPicks({...weatherBoard,status:'unavailable'},now).length,0);
  assert.equal(currentWeatherPicks({...weatherBoard,generated_at:'2026-09-10T20:00:00Z'},now).length,0);
});
test('weather selections expire with their quote and cannot expose started games or invalid prices', () => {
  for (const changed of [{kickoff:'2026-09-12T11:59:00Z'}, {kickoff:'2026-09-13T19:00:00Z'}, {quote_time:'2026-09-12T10:59:00Z'}, {quote_time:null}, {side:'over'}, {line:null}, {decimal_odds:1.8}]) assert.equal(currentWeatherPicks({...weatherBoard,weather_strategy:{...weatherBoard.weather_strategy,today_picks:[{...weatherPick,...changed}]}},now).length,0);
});
test('weather selections use the Eastern day and do not duplicate a game', () => {
  const midnight={...weatherPick,kickoff:'2026-09-13T01:00:00Z'};
  assert.equal(currentWeatherPicks({...weatherBoard,weather_strategy:{...weatherBoard.weather_strategy,today_picks:[midnight,{...midnight,line:48.5}]}},now).length,1);
});
test('weather cards read the nested runtime measurements without treating missing values as zero', () => {
  assert.deepEqual(weatherMeasurements({weather:{feature_status:'available',weather:{wind_mph:9.5,temperature_f:61.2,relative_humidity_percent:71}}}),{wind_mph:9.5,temperature_f:61.2,relative_humidity_pct:71});
  assert.equal(weatherMeasurements({weather:{feature_status:'unavailable'}}).wind_mph,undefined);
  assert.equal(percent(weatherMeasurements({}).relative_humidity_pct),'—');
});
