const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const {weatherStudyHealth,weatherStudyReportUrl,upcomingStudyPositions} = require('../app.js');

const fixture = {
  schema_version:'weather-revision-study-status-v1', protocol_id:'weather-revision-prospective-v1-20260909',
  generated_at:'2026-09-09T07:20:00Z', status:'collecting',
  observations:{total:0,probability:0,movement:0},
  models:{probability:{status:'not_fitted',games:0,completed_weeks:0,cutoff:null},movement:{status:'not_fitted',games:0,completed_weeks:0,cutoff:null}},
  forecasts:{probability:0,movement:0},paper:{locked:0,settled:0,unresolved:0,profit_units:null},
  evidence:{edge_established:false,prospective:true},errors:[]
};

test('zero-input study reports collecting and an experimental fitting minimum', () => {
  const state=weatherStudyHealth(fixture,new Date('2026-09-09T07:21:00Z'));
  assert.equal(state.label,'Collecting model inputs');
  assert.match(state.message,/60 eligible games and two completed weeks/);
  assert.match(state.message,/has not established a betting edge/);
});

test('fitted and completed states do not assert an edge', () => {
  const fitted=weatherStudyHealth({...fixture,status:'fitted'},new Date('2026-09-09T07:21:00Z'));
  assert.equal(fitted.label,'Experimental models fitted');
  assert.match(fitted.message,/does not establish/);
  const finished=weatherStudyHealth({...fixture,status:'evaluation_complete'},new Date('2027-02-09T07:21:00Z'));
  assert.equal(finished.label,'Evaluation complete');
  assert.match(finished.message,/full report and its uncertainty/);
});

test('stale, future and failed status cannot masquerade as current study health', () => {
  assert.equal(weatherStudyHealth(fixture,new Date('2026-09-09T16:00:00Z')).label,'Refresh overdue');
  assert.equal(weatherStudyHealth(fixture,new Date('2026-09-09T06:00:00Z')).label,'Status unavailable');
  assert.equal(weatherStudyHealth({...fixture,status:'attention'},new Date('2026-09-09T07:21:00Z')).label,'Needs attention');
  assert.equal(weatherStudyHealth({...fixture,errors:['failed']},new Date('2026-09-09T07:21:00Z')).label,'Needs attention');
});

test('study contract rejects unsupported evidence claims and incompatible files', () => {
  for(const change of [{schema_version:'weather-revision-status-v1'},{protocol_id:null},
      {status:'profitable'}, {evidence:{edge_established:true,prospective:true}},
      {evidence:{edge_established:false,prospective:false}}]) {
    assert.equal(weatherStudyHealth({...fixture,...change}).visible,false);
  }
});

test('only fixed published study reports are mapped to the source repository',()=>{
  const approved='model/data/runtime/weather_revision_study/final_evaluation.json';
  assert.equal(weatherStudyReportUrl(approved),`https://github.com/drhyphy/ncaa-football-totals/blob/main/${approved}`);
  for(const bad of ['javascript:alert(1)','model/../../secrets','https://example.com/report',null]) assert.equal(weatherStudyReportUrl(bad),null);
});

test('upcoming archived positions preserve first entry without presenting stale quotes as offers',()=>{
  const row={game_id:'1',policy_id:'weather-revision-probability-v1-20260909',side:'under',line:54.5,decimal_odds:1.91,
    kickoff:'2026-09-11T19:00:00Z',recorded_at:'2026-09-09T07:20:00Z',quote_observed_at:'2026-09-09T07:19:00Z'};
  const now=new Date('2026-09-09T12:00:00Z');
  const rows=[row,{...row,line:56.5,recorded_at:'2026-09-09T07:22:00Z'}, {...row,game_id:'2',kickoff:'2026-09-09T11:00:00Z'},
    {...row,game_id:'3',recorded_at:'2026-09-10T07:20:00Z'}, {...row,game_id:'4',quote_observed_at:'2026-09-09T07:21:00Z'},
    {...row,game_id:'5',policy_id:'existing_policy'}];
  const actual=upcomingStudyPositions({...fixture,experimental_picks:rows},now);
  assert.equal(actual.length,1);assert.equal(actual[0].line,54.5);
});

class Element {
  constructor(tag) {this.tagName=tag;this.children=[];this.attributes={};this._text='';}
  set textContent(text) {this._text=String(text);this.children=[];}
  get textContent() {return this._text+this.children.map(child=>child.textContent).join('');}
  get firstChild() {return this.children[0];}
  append(...nodes) {this.children.push(...nodes);}
  replaceChildren(...nodes) {this._text='';this.children=nodes;}
  setAttribute(name,value) {this.attributes[name]=value;}
  getAttribute(name) {return name==='href'?this.href:this.attributes[name];}
  addEventListener() {}
}

test('actual DOM path keeps revision study separate, unavailable returns null, and rejects unsafe links',async()=>{
  const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
  const nodes=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element('div')]));
  const doc={getElementById:id=>nodes.get(id),createElement:tag=>new Element(tag),createTextNode:text=>{const n=new Element('#text');n.textContent=text;return n;}};
  let clock=Date.parse('2026-09-09T07:21:00Z'),tick;
  class ClockDate extends Date {constructor(...args){super(...(args.length?args:[clock]));}static now(){return clock;}}
  const board={schema_version:1,status:'ok',generated_at:fixture.generated_at,date:'2026-09-09'};
  const study={...fixture,report_path:'javascript:alert(1)',models:{...fixture.models,probability:{...fixture.models.probability,status:'<img src=x onerror=alert(1)>'}}};
  const context={window:{document:doc},Date:ClockDate,Intl,URL,fetch:async url=>({ok:true,json:async()=>url.startsWith('data/weather-revision-study.json')?study:board}),setInterval:callback=>{tick=callback;}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../app.js'),'utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(nodes.get('weather-revision-study').hidden,false);
  assert.equal(nodes.get('weather-study-label').textContent,'Collecting model inputs');
  assert.match(nodes.get('weather-study-counts').textContent,/0 recorded observations.*0 probability inputs.*0 movement inputs/);
  assert.match(nodes.get('weather-study-models').textContent,/Exact-total probability.*Six-hour line movement/);
  assert.match(nodes.get('weather-study-models').textContent,/<Img Src=X Onerror=Alert\(1\)>/);
  assert.doesNotMatch(nodes.get('weather-study-paper').textContent,/0\.00 units/);
  assert.match(nodes.get('weather-study-paper').textContent,/profit \/ loss is not available/);
  assert.match(nodes.get('weather-study-positions').textContent,/No upcoming paper positions/);
  assert.match(html,/paper records, not currently available offers/);
  assert.equal(nodes.get('weather-study-links').children.length,1);
  assert.match(html,/four existing paper policies and their ledgers remain separate/);
  assert.match(nodes.get('performance-stats').textContent,/Settled paper bets0/);
  clock+=9*3600000;tick();
  assert.equal(nodes.get('weather-study-label').textContent,'Refresh overdue');
});
