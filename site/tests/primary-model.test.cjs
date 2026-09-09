const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const {primaryModel, primaryPicks, primaryResults, candidateTracking} = require('../app.js');
const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
const script = fs.readFileSync(path.join(__dirname, '../app.js'), 'utf8');
const now = new Date('2026-09-12T12:00:00Z');
const primary = 'opponent_adjusted_ridge', structural = 'opponent_adjusted_structural', price = 'market_price_reference', weather = 'published_weather_under';
const emptyPaper = {bets:0, pending:0, wins:0, losses:0, pushes:0, profit_units:0, roi:null, roi_95_low:null, roi_95_high:null};
const forecast = {forecast_entries:12, games:0, pending:12, abstentions:11, mae:null, rmse:null, brier:null, log_loss:null, probability_scoring_games:0};
function pick(candidate, name, game='1', kickoff='2026-09-12T19:00:00Z') {
  return {candidate, model_version:'v4', game_id:game, away_team:name, home_team:'Home', kickoff,
    side:'over', line:50.5, american_odds:-110, sportsbook:'Book', projected_total:53, consensus_total:50.5,
    win_probability:.56, push_probability:0, expected_value:.06, robust_ev:.02, quote_time:'2026-09-12T11:45:00Z', recorded_at:'2026-09-12T11:46:00Z', result:'pending', profit_units:null};
}
function fixture() {
  const ridge=pick(primary,'PRIMARY TODAY'), other=pick(structural,'OTHER TODAY','2');
  return {schema_version:1,status:'ok',generated_at:'2026-09-12T11:45:00Z',date:'2026-09-12',model_version:'v4',
    primary_model:{candidate:primary,label:'Market-anchored opponent-adjusted ridge',version:'v4',status:'active_paper',description:'Market total plus opponent-adjusted team effects.'},
    today_picks:[other,ridge],upcoming_picks:[pick(price,'OTHER FUTURE','3','2026-09-13T19:00:00Z'),pick(primary,'PRIMARY FUTURE','4','2026-09-13T19:00:00Z')],
    performance:{...emptyPaper,candidate:primary,model_version:'v4',pending:1},
    results:[ridge,other,{...pick(primary,'OLD VERSION','5'),model_version:'v3',profit_units:900,result:'win'}],
    candidate_tracking:[
      {candidate:primary,label:'Market-anchored opponent-adjusted ridge',role:'primary',status:'active_paper',model_version:'v4',performance:{...emptyPaper,pending:1},forecast_performance:forecast},
      {candidate:structural,label:'Structural blend',role:'comparison',status:'active_paper',model_version:'v4',performance:emptyPaper,forecast_performance:forecast},
      {candidate:price,label:'Market-price reference',role:'comparison',status:'active_paper',model_version:'v4',performance:emptyPaper,forecast_performance:forecast},
      {candidate:weather,label:'Weather Under',role:'comparison',status:'active_paper',model_version:'weather-v1',performance:emptyPaper,forecast_performance:null}],
    registered_evaluation:{cohort_start:'2026-09-09T04:00:00Z',cohort_end:'2027-02-01T04:59:59Z',formal_evaluation_at:'2027-02-08T12:00:00Z',interim_status:'descriptive_only',report_url:'data/prospective-evaluation.json'},
    weather_strategy:{version:'weather-v1',status:'ok',as_of:'2026-09-12T11:45:00Z',today_picks:[],performance:emptyPaper,results:[]}};
}
class Element {
  constructor(tag) {this.tagName=tag;this.children=[];this.attributes={};this._text='';}
  set textContent(value) {this._text=String(value);this.children=[];}
  get textContent() {return this._text+this.children.map(child=>child.textContent).join('');}
  get firstChild() {return this.children[0];}
  append(...nodes) {this.children.push(...nodes);}
  replaceChildren(...nodes) {this._text='';this.children=nodes;}
  setAttribute(name,value) {this.attributes[name]=value;}
  getAttribute(name) {return name==='href'?this.href:this.attributes[name];}
  addEventListener() {}
}
async function render(board) {
  const nodes=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match=>[match[1],new Element('div')]));
  const doc={getElementById:id=>nodes.get(id),createElement:tag=>new Element(tag),createTextNode:text=>{const node=new Element('#text');node.textContent=text;return node;}};
  let clock=now.getTime(),tick;
  class ClockDate extends Date {constructor(...args){super(...(args.length?args:[clock]));}static now(){return clock;}}
  vm.runInNewContext(script,{window:{document:doc},Date:ClockDate,Intl,URL,
    fetch:async url=>({ok:true,json:async()=>url.startsWith('data/board.json')?board:{}}),setInterval:fn=>{tick=fn;}});
  await new Promise(resolve=>setImmediate(resolve));
  return {nodes, advance(ms){clock+=ms;tick();}};
}

test('primary identity cannot be changed by a comparison metadata row',()=>{
  const board=fixture();board.primary_model={candidate:price,label:'Other model',version:'wrong'};
  assert.equal(primaryModel(board).candidate,primary);
  assert.equal(primaryModel(board).model_version,'v4');
  assert.match(primaryModel(board).label,/opponent-adjusted ridge/);
});
test('mixed candidate picks, old versions and stale boards cannot enter primary selections',()=>{
  const board=fixture();
  board.today_picks.push({...pick(primary,'OLD MODEL'),model_version:'v3'});
  assert.deepEqual(primaryPicks(board,board.today_picks,now).map(row=>row.away_team),['PRIMARY TODAY']);
  assert.deepEqual(primaryPicks(board,board.upcoming_picks,now,false).map(row=>row.away_team),['PRIMARY FUTURE']);
  assert.equal(primaryPicks({...board,status:'unavailable'},board.today_picks,now).length,0);
  assert.equal(primaryPicks({...board,generated_at:'2026-09-10T00:00:00Z'},board.today_picks,now).length,0);
});
test('primary prospective results retain only its current candidate/version and first game entry',()=>{
  const board=fixture();board.results.push({...board.results[0],line:90.5,recorded_at:'2026-09-12T11:50:00Z'});
  const rows=primaryResults(board);
  assert.equal(rows.length,1);assert.equal(rows[0].candidate,primary);assert.equal(rows[0].line,50.5);
});
test('all four active trackers remain present without promoting research rows or fabricating metrics',()=>{
  const board=fixture();board.candidate_tracking.push({candidate:'availability_draft',role:'primary',performance:{roi:9}});
  assert.deepEqual(candidateTracking(board).map(row=>row.candidate),[primary,structural,price,weather]);
  const fallback=candidateTracking({model_version:'v4'});
  assert.equal(fallback.length,4);assert.ok(fallback.every(row=>row.performance===null));
  assert.equal(fallback[3].forecast_performance,null);
});
test('legacy board metrics stay version/candidate specific and never use historical development ROI',()=>{
  const board={model_version:'v4',performance:{candidate:price,model_version:'v4',roi:7},
    candidate_performance:{[structural]:{...emptyPaper,candidate:structural,model_version:'v4',pending:3}},
    forecast_performance:[{candidate:primary,model_version:'v3',games:500},{...forecast,candidate:primary,model_version:'v4'}],
    candidates:[{candidate:primary,roi:9}],weather_strategy:{version:'weather-v1',performance:emptyPaper}};
  const rows=candidateTracking(board);
  assert.equal(rows[0].performance,null);assert.equal(rows[0].forecast_performance.games,0);
  assert.equal(rows[1].performance.pending,3);assert.equal(rows[3].performance.roi,null);
});
test('DOM keeps primary picks and records isolated while comparison trackers remain visible at zero',async()=>{
  const {nodes,advance}=await render(fixture());
  assert.equal(nodes.get('primary-model-name').textContent,'Market-anchored opponent-adjusted ridge');
  assert.match(nodes.get('today-picks').textContent,/PRIMARY TODAY/);assert.doesNotMatch(nodes.get('today-picks').textContent,/OTHER TODAY/);
  assert.match(nodes.get('upcoming-picks').textContent,/PRIMARY FUTURE/);assert.doesNotMatch(nodes.get('upcoming-picks').textContent,/OTHER FUTURE/);
  assert.match(nodes.get('results-table').textContent,/PRIMARY TODAY/);assert.doesNotMatch(nodes.get('results-table').textContent,/OTHER TODAY|OLD VERSION|900/);
  for(const name of ['Structural blend','Market-price reference','Weather Under']) assert.ok(nodes.get('candidate-tracking-table').textContent.includes(name));
  assert.match(nodes.get('candidate-tracking-table').textContent,/0–0–0/);
  assert.doesNotMatch(nodes.get('candidate-tracking-table').textContent,/Market-anchored opponent-adjusted ridge/);
  assert.match(nodes.get('other-candidate-records').textContent,/OTHER TODAY/);
  assert.match(nodes.get('other-current-picks').textContent,/OTHER TODAY/);
  assert.match(nodes.get('other-current-picks').textContent,/OTHER FUTURE/);
  assert.match(nodes.get('primary-forecast-summary').textContent,/12 recorded games.*0 settled.*12 pending/);
  assert.match(nodes.get('prospective-evaluation-link').textContent,/Interim.*descriptive only/);
  assert.equal(nodes.get('prospective-evaluation-link').href,'data/prospective-evaluation.json');
  advance(27*60*60*1000);
  assert.equal(nodes.get('pick-count').textContent,'0');
  assert.doesNotMatch(nodes.get('other-current-picks').textContent,/OTHER TODAY|OTHER FUTURE/);
});
test('empty board displays all comparison trackers and unavailable forward returns honestly',async()=>{
  const board=fixture();board.today_picks=[];board.upcoming_picks=[];board.results=[];
  const {nodes}=await render(board);
  assert.match(nodes.get('today-picks').textContent,/No qualifying picks today/);
  assert.match(nodes.get('performance-stats').textContent,/Realized ROI—/);
  assert.match(nodes.get('candidate-tracking-table').textContent,/Structural blend.*Market-price reference.*Weather Under/);
  assert.match(nodes.get('other-candidate-records').textContent,/No paper ledger entries/);
});
test('HTML puts primary picks, watchlist and record ahead of weather/research with preserved unique IDs',()=>{
  const order=['id="today"','id="upcoming"','id="results"','id="other-candidates"','id="weather"','id="evidence"'];
  const offsets=order.map(id=>html.indexOf(id));assert.ok(offsets.every((v,i)=>v>=0 && (!i||v>offsets[i-1])));
  const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);assert.equal(new Set(ids).size,ids.length);
  assert.match(html,/<details id="weather-details"[^>]*>/);assert.match(html,/<details id="research-details"[^>]*>/);
  assert.doesNotMatch(html,/<details id="(?:weather-details|research-details)"[^>]*\bopen\b/);
  assert.match(html,/Availability is an inactive research draft/);
});
