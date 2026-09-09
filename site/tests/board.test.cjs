const test = require('node:test');
const assert = require('node:assert/strict');
const {health, currentPicks, currentHedges, evidenceState, dateKey, dedupeResults, safeUrl, percent} = require('../app.js');
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
