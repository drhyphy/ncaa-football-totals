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

const order=['raw50','rawridge','context_logit','opponent_logit','context_hgb','opponent_hgb'];
const reports=path.join(__dirname,'../../model/reports');
function fixture() {
  const selection=JSON.parse(fs.readFileSync(path.join(reports,'direct_probability_selection.json'),'utf8'));
  const result=JSON.parse(fs.readFileSync(path.join(reports,'direct_probability_results.json'),'utf8'));
  return {status:'reused_development_probability_score_study',configuration_count:6,candidate_order:order,
    historical_data_reused:true,roi_evaluated:false,live_policy_changes:false,credible_executable_edge_established:false,
    selected_configuration:'rawridge',selection_2021_2024:selection.summary,reused_2025:result.development_2025,
    links:[{name:'Independent audit',url:'https://github.com/drhyphy/ncaa-football-totals/blob/main/model/reports/DIRECT_PROBABILITY_RESEARCH_AUDIT.md'},
           {name:'Unsafe',url:'javascript:alert(1)'}]};
}
async function render(study) {
  const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
  const nodes=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match=>[match[1],new Element('div')]));
  const doc={getElementById:id=>nodes.get(id),createElement:tag=>new Element(tag),createTextNode:text=>{const n=new Element('#text');n.textContent=text;return n;}};
  const board={schema_version:1,status:'ok',generated_at:'2026-09-09T12:00:00Z',date:'2026-09-09',timezone:'America/New_York',
    evidence_status:'research_only',model_version:'fixture',today_picks:[],upcoming_picks:[],forecasts:[],candidates:[],results:[],performance:{},sources:[],limitations:[]};
  const context={window:{document:doc},Date,Intl,URL,setInterval:()=>{},fetch:async url=>({ok:true,json:async()=>
    url.startsWith('data/research.json')?{direct_probability_research:study}:url.startsWith('data/board.json')?board:null})};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../app.js'),'utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  return nodes;
}

test('direct probability card retains six models, both score periods and all four paired checks',async()=>{
  const nodes=await render(fixture());
  assert.equal(nodes.get('direct-probability-study').hidden,false);
  const note=nodes.get('direct-probability-note').textContent;
  assert.match(note,/2021–2024 NLL chose the existing raw ridge before the 2025 check/);
  assert.match(note,/All four new classifiers had worse 2025 NLL and Brier point scores than constant 50\/50/);
  assert.match(note,/No ROI test was run and no profitable edge was established.*No live model or paper policy was added/);
  const scores=nodes.get('direct-probability-table').textContent;
  for(const label of ['Constant 50/50','Existing raw ridge','Context logistic','Opponent logistic','Context tree','Opponent tree']) assert.ok(scores.includes(label));
  for(const value of ['0.691899','0.249375','0.697509','0.252163','0.693147','0.250000']) assert.ok(scores.includes(value));
  assert.match(scores,/2021–24 NLL.*2021–24 Brier.*2025 NLL.*2025 Brier/);
  assert.match(nodes.get('direct-probability-coverage').textContent,/2,406.*428.*403.*777.*798.*852/);
  const comparisons=nodes.get('direct-probability-comparisons').textContent;
  for(const label of ['Opponent logistic − Context logistic','Opponent tree − Context tree','Opponent logistic − Existing raw ridge','Opponent tree − Existing raw ridge']) assert.ok(comparisons.includes(label));
  assert.ok(comparisons.includes('0.000034'));
  assert.match(nodes.get('direct-probability-uncertainty').textContent,/All four local 98.75% intervals include zero.*cannot replace the earlier selection after the fact/);
  assert.equal(nodes.get('direct-probability-links').children.length,1);
  assert.equal(nodes.get('candidate-table').textContent,'Candidate evaluation metrics have not been published.');
});

test('missing, incomplete or promotional direct research cannot render a selected-model card',async()=>{
  const invalid=[null];
  for(const fault of ['missing_model','candidate_order','selected_after_2025','roi','edge','live','count','missing_comparison']) {
    const study=fixture();
    if(fault==='missing_model') delete study.reused_2025.configurations.context_logit;
    if(fault==='candidate_order') study.candidate_order=order.slice().reverse();
    if(fault==='selected_after_2025') study.selected_configuration='opponent_logit';
    if(fault==='roi') study.roi_evaluated=true;
    if(fault==='edge') study.credible_executable_edge_established=true;
    if(fault==='live') study.live_policy_changes=true;
    if(fault==='count') study.reused_2025.games=851;
    if(fault==='missing_comparison') study.reused_2025.comparisons.pop();
    invalid.push(study);
  }
  for(const study of invalid) {
    const nodes=await render(study);
    assert.equal(nodes.get('direct-probability-study').hidden,true);
    assert.equal(nodes.get('direct-probability-table').textContent,'');
  }
});
