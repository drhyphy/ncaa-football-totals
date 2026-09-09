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

const order=['market_normal','ridge_normal','market_score_shape'];
function fixture() {
  const result=JSON.parse(fs.readFileSync(path.join(__dirname,'../../model/reports/score_shape_results.json'),'utf8'));
  return {status:'reused_development_score_distribution_study',configuration_count:3,candidate_order:order,
    primary_metric:'three_outcome_nll',selected_on_2022_2024:'ridge_normal',historical_data_reused:true,no_2026_outcomes:true,
    live_policy_changes:false,probability_artifact_promoted:false,roi_evaluated:false,credible_executable_edge_established:false,
    actual_integer_line_validation_in_2025:false,audit:{status:'passed',numeric_comparisons:1042299},
    selection_2022_2024:result.selection_2022_2024.pooled,reused_2025:result.reused_2025.pooled,
    links:[{name:'Score-shape report',url:'https://github.com/drhyphy/ncaa-football-totals/blob/main/model/reports/score_shape_results.md'},
           {name:'Unsafe',url:'javascript:alert(1)'}]};
}
async function render(study) {
  const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
  const nodes=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match=>[match[1],new Element('div')]));
  const doc={getElementById:id=>nodes.get(id),createElement:tag=>new Element(tag),createTextNode:text=>{const n=new Element('#text');n.textContent=text;return n;}};
  const board={schema_version:1,status:'ok',generated_at:'2026-09-09T12:00:00Z',date:'2026-09-09',timezone:'America/New_York',evidence_status:'research_only',
    model_version:'fixture',today_picks:[],upcoming_picks:[],forecasts:[],candidates:[],results:[],performance:{},sources:[],limitations:[]};
  const context={window:{document:doc},Date,Intl,URL,setInterval:()=>{},fetch:async url=>({ok:true,json:async()=>
    url.startsWith('data/research.json')?{score_shape_research:study}:url.startsWith('data/board.json')?board:null})};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../app.js'),'utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  return nodes;
}

test('score-shape card retains all three distributions, both periods and every primary interval',async()=>{
  const study=fixture(),nodes=await render(study);
  assert.equal(nodes.get('score-shape-study').hidden,false);
  const note=nodes.get('score-shape-note').textContent;
  assert.match(note,/Exact-score likelihood improved, but no Over\/Under edge was established/);
  assert.match(note,/2022–2024 actual-line NLL kept the existing ridge Normal.*All 2025 actual-line comparison intervals include zero/);
  assert.match(note,/No ROI test was run and no probability artifact or live policy was promoted/);
  const table=nodes.get('score-shape-table').textContent;
  for(const name of ['Market Normal','Existing ridge Normal','Market + score shape']) assert.ok(table.includes(name));
  const comparisons=nodes.get('score-shape-comparisons').textContent;
  for(const period of [study.selection_2022_2024,study.reused_2025]) {
    for(const row of Object.values(period.configurations)) {
      for(const metric of ['three_outcome_nll','exact_score_nll','conditional_brier']) assert.ok(table.includes(row.metrics[metric].toFixed(6)));
    }
    for(const row of period.comparisons.map(c=>c.metrics.three_outcome_nll)) {
      for(const value of [row.difference,...row.interval_95,...row.interval_99]) assert.ok(comparisons.includes(value.toFixed(6)));
    }
  }
  assert.match(table,/2022–24 selection.*2025 reused check/);
  assert.match(comparisons,/Descriptive 95% interval.*Descriptive 99% interval/);
  assert.match(nodes.get('score-shape-coverage').textContent,/2,327.*349 integer lines.*12 actual pushes.*2,315.*852-game.*only half-point lines.*cannot validate actual integer-line push probabilities/);
  assert.match(nodes.get('score-shape-uncertainty').textContent,/secondary measure.*59 selection weeks and 22 check weeks.*full research search.*did not refit ridge/);
  assert.equal(nodes.get('score-shape-links').children.length,1);
  assert.equal(nodes.get('candidate-table').textContent,'Candidate evaluation metrics have not been published.');
});

test('score-shape incomplete, unaudited or promotional records stay hidden without suppressing the board',async()=>{
  const invalid=[null];
  for(const fault of ['model','count','denominator','push','pair','interval','choice','audit','audit_count','edge','roi','promotion','live','2026','push_validation','order']) {
    const study=fixture();
    if(fault==='model') delete study.reused_2025.configurations.market_normal;
    if(fault==='count') study.selection_2022_2024.games=2326;
    if(fault==='denominator') study.selection_2022_2024.configurations.market_score_shape.metric_games.conditional_brier=2327;
    if(fault==='push') study.reused_2025.configurations.market_score_shape.observed_pushes=1;
    if(fault==='pair') study.selection_2022_2024.comparisons.pop();
    if(fault==='interval') study.reused_2025.comparisons[1].metrics.three_outcome_nll.interval_99=null;
    if(fault==='choice') study.selected_on_2022_2024='market_score_shape';
    if(fault==='audit') study.audit.status='failed';
    if(fault==='audit_count') study.audit.numeric_comparisons=0;
    if(fault==='edge') study.credible_executable_edge_established=true;
    if(fault==='roi') study.roi_evaluated=true;
    if(fault==='promotion') study.probability_artifact_promoted=true;
    if(fault==='live') study.live_policy_changes=true;
    if(fault==='2026') study.no_2026_outcomes=false;
    if(fault==='push_validation') study.actual_integer_line_validation_in_2025=true;
    if(fault==='order') study.candidate_order=order.slice().reverse();
    invalid.push(study);
  }
  for(const study of invalid) {
    const nodes=await render(study);
    assert.equal(nodes.get('score-shape-study').hidden,true);
    assert.equal(nodes.get('score-shape-table').textContent,'');
    assert.equal(nodes.get('candidate-table').textContent,'Candidate evaluation metrics have not been published.');
  }
});

test('score-shape interpretation follows score and interval values instead of making a fixed success claim',async()=>{
  const study=fixture();
  study.reused_2025.configurations.market_score_shape.metrics.exact_score_nll=5;
  study.reused_2025.comparisons[0].metrics.three_outcome_nll.interval_95=[.01,.02];
  const nodes=await render(study),note=nodes.get('score-shape-note').textContent;
  assert.doesNotMatch(note,/Exact-score likelihood improved|All 2025 actual-line comparison intervals include zero/);
  assert.match(note,/did not establish an Over\/Under edge.*full actual-line comparison intervals are below/);
});
