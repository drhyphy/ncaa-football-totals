const test = require('node:test');
const assert = require('node:assert/strict');
const {weatherRevisionHealth} = require('../app.js');

const fixture = {schema_version:'weather-revision-status-v1',collection_only:true,performance_evaluated:false,active_policy_changed:false,
  pilot_start:'2026-09-09T03:00:00Z',pilot_end:'2026-09-16T03:00:00Z',generated_at:'2026-09-09T07:20:00Z',status:'current',
  latest:{capture_completed_at:'2026-09-09T07:20:00Z'}};

test('collection UI obeys the UTC pilot boundary independently of stale status phase', () => {
  assert.equal(weatherRevisionHealth(fixture,new Date('2026-09-09T02:59:59Z')).label,'Scheduled');
  assert.match(weatherRevisionHealth({...fixture,latest:null,generated_at:'2026-09-09T03:00:00Z'},new Date('2026-09-09T03:00:00Z')).label,/Active/);
  assert.equal(weatherRevisionHealth(fixture,new Date('2026-09-16T03:00:00Z')).label,'Ended');
});

test('fresh publication cannot hide stale archive receipt or collection failure', () => {
  const now = new Date('2026-09-09T16:00:00Z');
  assert.equal(weatherRevisionHealth({...fixture,generated_at:now.toISOString()},now).label,'Active · receipts stale');
  assert.equal(weatherRevisionHealth({...fixture,status:'attention'},now).label,'Active · needs attention');
  assert.equal(weatherRevisionHealth(fixture,new Date('2026-09-09T07:22:00Z')).label,'Active · receipts current');
});

test('collection status never presents incompatible policies as an active pilot', () => {
  for (const changed of [{schema_version:1},{collection_only:false},{performance_evaluated:true},{active_policy_changed:true},
                         {pilot_end:'2026-09-30T03:00:00Z'}]) {
    assert.equal(weatherRevisionHealth({...fixture,...changed},new Date('2026-09-09T07:22:00Z')).visible,false);
  }
});
