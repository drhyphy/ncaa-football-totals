const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

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

const order=['market_only','opponent_adjusted_ridge','pbp_state_ridge'];
function fixture() {
  const result=JSON.parse(fs.readFileSync(path.join(__dirname,'../../model/reports/pbp_state_results_v2.json'),'utf8'));
  const study={status:'reused_development_point_forecast_study',configuration_count:3,candidate_order:order,
    selected_on_2021_2024:'opponent_adjusted_ridge',historical_data_reused:true,no_2026_outcomes:true,
    live_policy_changes:false,probabilities_evaluated:false,roi_evaluated:false,credible_executable_edge_established:false,
    retained_play_rows:762297,feature_rows:5008,audit:{passed:true,fit_count:10,numeric_comparisons:1143},
    links:[{name:'PBP report',url:'https://github.com/drhyphy/ncaa-football-totals/blob/main/model/reports/PBP_STATE_RESULTS_V2.md'},
           {name:'Unsafe',url:'javascript:alert(1)'}]};
  for(const key of ['selection_2021_2024','reused_2025']) {
    const p=result[key].pooled;
    study[key]={games:p.games,configurations:p.metrics,comparisons:p.comparisons};
  }
  return study;
}
async function render(study) {
  const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
  const nodes=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match=>[match[1],new Element('div')]));
  const doc={getElementById:id=>nodes.get(id),createElement:tag=>new Element(tag),createTextNode:text=>{const n=new Element('#text');n.textContent=text;return n;}};
  const board={schema_version:1,status:'ok',generated_at:'2026-09-09T12:00:00Z',date:'2026-09-09',timezone:'America/New_York',evidence_status:'research_only',
    model_version:'fixture',today_picks:[],upcoming_picks:[],forecasts:[],candidates:[],results:[],performance:{},sources:[],limitations:[]};
  const context={window:{document:doc},Date,Intl,URL,setInterval:()=>{},fetch:async url=>({ok:true,json:async()=>
    url.startsWith('data/research.json')?{pbp_state_research:study}:url.startsWith('data/board.json')?board:null})};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../app.js'),'utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  return nodes;
}

test('PBP card retains all three point forecasts, both periods and every paired interval',async()=>{
  const nodes=await render(fixture());
  assert.equal(nodes.get('pbp-study').hidden,false);
  const note=nodes.get('pbp-study-note').textContent;
  assert.match(note,/did not establish an improvement.*2021–2024 selection kept the existing ridge/);
  assert.match(note,/tiny 2025 MSE gain.*interval that includes zero.*2025 MSE remained higher than the market reference/);
  assert.match(note,/No probabilities or ROI were tested, and no live policy was added/);
  const table=nodes.get('pbp-study-table').textContent;
  for(const name of ['Market reference','Existing opponent-adjusted ridge','Ridge + PBP state ratings']) assert.ok(table.includes(name));
  for(const value of ['251.4935','250.4281','250.8194','239.8744','240.9862','240.9426','15.5223','12.5600']) assert.ok(table.includes(value));
  assert.match(table,/2021–24 selection.*2025 reused check/);
  const comparisons=nodes.get('pbp-study-comparisons').textContent;
  for(const value of ['-0.6741','0.3912','1.0682','-0.0436','-0.5416','0.4150','-0.7068','0.5597']) assert.ok(comparisons.includes(value));
  assert.match(comparisons,/Descriptive 95% interval.*Descriptive 99% interval/);
  assert.match(nodes.get('pbp-study-coverage').textContent,/762,297.*5,008.*3,061.*852.*10 residual ridge fits.*1,143.*did not refit/);
  assert.match(nodes.get('pbp-study-uncertainty').textContent,/74 selection weeks and 22 check weeks.*full research search.*no 2026 outcomes/);
  assert.equal(nodes.get('pbp-study-links').children.length,1);
  assert.equal(nodes.get('candidate-table').textContent,'Candidate evaluation metrics have not been published.');
});

test('PBP incomplete, unaudited or promotional records stay hidden without suppressing the board',async()=>{
  const invalid=[null];
  for(const fault of ['model','count','pair','interval','choice','audit','fit_count','edge','roi','probability','live','2026','order']) {
    const study=fixture();
    if(fault==='model') delete study.reused_2025.configurations.market_only;
    if(fault==='count') study.selection_2021_2024.games=3060;
    if(fault==='pair') study.selection_2021_2024.comparisons.pop();
    if(fault==='interval') study.reused_2025.comparisons[1].mse_interval_99=null;
    if(fault==='choice') study.selected_on_2021_2024='pbp_state_ridge';
    if(fault==='audit') study.audit.passed=false;
    if(fault==='fit_count') study.audit.fit_count=9;
    if(fault==='edge') study.credible_executable_edge_established=true;
    if(fault==='roi') study.roi_evaluated=true;
    if(fault==='probability') study.probabilities_evaluated=true;
    if(fault==='live') study.live_policy_changes=true;
    if(fault==='2026') study.no_2026_outcomes=false;
    if(fault==='order') study.candidate_order=order.slice().reverse();
    invalid.push(study);
  }
  for(const study of invalid) {
    const nodes=await render(study);
    assert.equal(nodes.get('pbp-study').hidden,true);
    assert.equal(nodes.get('pbp-study-table').textContent,'');
    assert.equal(nodes.get('candidate-table').textContent,'Candidate evaluation metrics have not been published.');
  }
});
