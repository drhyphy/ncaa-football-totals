const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

// Small DOM surface checks the real rendering path without another dependency.
class Element {
  constructor(tag) { this.tagName=tag; this.children=[]; this.attributes={}; this._text=''; }
  set textContent(text) { this._text=String(text); this.children=[]; }
  get textContent() { return this._text+this.children.map(child=>child.textContent).join(''); }
  get firstChild() { return this.children[0]; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this._text=''; this.children=nodes; }
  setAttribute(name,value) { this.attributes[name]=value; }
  getAttribute(name) { return name==='href' ? this.href : this.attributes[name]; }
  addEventListener() {}
}

test('weather DOM separates development evidence from forward results and expires current cards', async () => {
  const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
  const nodes=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match=>[match[1],new Element('div')]));
  const doc={getElementById:id=>nodes.get(id),createElement:tag=>new Element(tag),createTextNode:text=>{const n=new Element('#text');n.textContent=text;return n;}};
  let clock=Date.parse('2026-09-12T12:00:00Z'), tick;
  class ClockDate extends Date { constructor(...args) {super(...(args.length?args:[clock]));} static now(){return clock;} }
  const fixture={schema_version:1,status:'ok',generated_at:'2026-09-12T11:45:00Z',date:'2026-09-12',weather_strategy:{status:'ok',as_of:'2026-09-12T11:45:00Z',version:'weather-under-v1',forecast_count:8,qualifying_count:1,evidence:{bets:85,wins:55,losses:30,roi:.235294,roi_95_low:.014186,roi_95_high:.439173,price_assumption:-110},performance:{bets:0,pending:0,wins:0,losses:0,pushes:0,roi:null},results:[],today_picks:[{game_id:'123',side:'under',away_team:'Away',home_team:'Home',kickoff:'2026-09-12T19:00:00Z',line:47.5,decimal_odds:1.91,american_odds:-110,sportsbook:'Book',quote_time:'2026-09-12T11:45:00Z',win_probability:null,expected_value:null,weather:{weather:{wind_mph:9.5,temperature_f:61.2,relative_humidity_percent:71}}}]}};
  const researchFixture={weather_shadow:{statistical_robustness:{published_result:fixture.weather_strategy.evidence,all_covered_week_cluster_t:{multiplicity_sensitivity:[{hypothetical_family_size:4,bonferroni_95_family_interval:[-.0415228339,.5121110692]}]}}}};
  researchFixture.weather_shadow.archived_2026_replay={prospective_model_performance:false,cohorts:{two_book_the_odds_api:{settled_price_eligible_covered_games:35,calendar_week_blocks:1,rule:{bets:0,roi:null}},single_draftkings_espn_sensitivity:{settled_price_eligible_covered_games:35,calendar_week_blocks:1,rule:{bets:0,roi:null}}}};
  researchFixture.archived_2026_scoring_replay={version:'2026-priced-replay-v1',prospective_model_performance:false,exact_0630_replay:false,evaluated_at:'2026-09-09T01:22:40Z',cohorts:{connected_two_books:{snapshots:Array.from({length:14},(_,i)=>({source_sha256:`fixture-${i}`,observed_at:new Date(Date.parse('2026-08-20T13:00:00Z')+i*86400000).toISOString(),games:8})),positions:{opponent_adjusted_ridge:{bets:2,pending:2,wins:1,losses:1,pushes:0,roi:-.037037037,profit_units:-.074074074},opponent_adjusted_structural:{bets:11,pending:5,wins:5,losses:6,pushes:0,roi:-.1212995758,profit_units:-1.334295334}}}}};
  const context={window:{document:doc},Date:ClockDate,Intl,URL,fetch:async url=>({ok:true,json:async()=>url.startsWith('data/research.json')?researchFixture:fixture}),setInterval:callback=>{tick=callback;}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../app.js'),'utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(nodes.get('weather-pick-count').textContent,'1');
  const card=nodes.get('weather-picks').textContent;
  assert.match(card,/Under 47.5/);assert.match(card,/61.2°F/);assert.match(card,/71.0%/);
  assert.doesNotMatch(card,/Projected total|Stressed modeled EV/);
  assert.match(nodes.get('weather-evidence').textContent,/85 hypothetical bets/);
  assert.match(nodes.get('weather-robustness-note').textContent,/-4.15% to \+51.21%, crossing zero/);
  assert.match(nodes.get('weather-robustness-note').textContent,/search count is unknown/);
  assert.match(nodes.get('weather-2026-note').textContent,/0 qualifying bets in 35 covered two-book games/);
  assert.match(nodes.get('weather-2026-note').textContent,/ROI is unavailable/);
  assert.match(nodes.get('weather-2026-note').textContent,/cohorts are not pooled/);
  assert.match(nodes.get('weather-performance-stats').textContent,/No settled forward return/);
  assert.match(nodes.get('weather-results-table').textContent,/No weather paper selections/);
  assert.equal(nodes.get('archived-replay').hidden,false);
  assert.match(nodes.get('archived-replay-summary').textContent,/14 actual archive snapshots/);
  assert.match(nodes.get('archived-replay-table').textContent,/-3.70%/);
  assert.match(nodes.get('archived-replay-table').textContent,/-12.13%/);
  assert.equal(nodes.get('archived-replay-times').children.length,14);
  assert.match(nodes.get('performance-stats').textContent,/Settled paper bets0/);
  clock+=2*60*60*1000;tick();
  assert.equal(nodes.get('weather-pick-count').textContent,'0');
  assert.doesNotMatch(nodes.get('weather-picks').textContent,/Under 47.5/);
  assert.match(nodes.get('weather-evidence').textContent,/85 hypothetical bets/);
});
