const test = require('node:test');
const assert = require('node:assert/strict');
const {weatherSeasonHealth, weatherRevisionHealth} = require('../app.js');

const fixture = {schema_version:'weather-revision-season-status-v1',collection_profile:'season',
  collection_only:true,performance_evaluated:false,active_policy_changed:false,
  season_start:'2026-09-16T03:00:00Z',season_end:'2027-02-01T03:00:00Z',
  generated_at:'2026-09-16T07:20:00Z',status:'current',latest:{capture_completed_at:'2026-09-16T07:20:00Z'}};

test('season status obeys its own exact boundaries and cannot extend the pilot', () => {
  assert.equal(weatherSeasonHealth(fixture,new Date('2026-09-16T02:59:59Z')).label,'Scheduled');
  assert.equal(weatherSeasonHealth(fixture,new Date('2026-09-16T07:21:00Z')).label,'Active · receipts current');
  assert.equal(weatherSeasonHealth(fixture,new Date('2027-02-01T03:00:00Z')).label,'Ended');
  assert.equal(weatherRevisionHealth(fixture,new Date('2026-09-16T07:21:00Z')).visible,false);
});

test('season receipt failures and staleness never become confidence evidence', () => {
  const now = new Date('2026-09-16T07:21:00Z');
  assert.equal(weatherSeasonHealth({...fixture,status:'attention'},now).label,'Active · needs attention');
  assert.equal(weatherSeasonHealth(fixture,new Date('2026-09-16T16:00:00Z')).label,'Active · receipts stale');
  assert.equal(weatherSeasonHealth({...fixture,latest:null},now).label,'Active · awaiting capture');
  assert.match(weatherSeasonHealth(fixture,now).message,/Fitting readiness and evidence of profitability are separate/);
});

test('season status rejects incompatible profiles, windows and performance claims', () => {
  for (const changed of [{collection_profile:'pilot'},{season_end:'2027-03-01T03:00:00Z'},
      {performance_evaluated:true},{active_policy_changed:true},{collection_only:false}]) {
    assert.equal(weatherSeasonHealth({...fixture,...changed}).visible,false);
  }
});
