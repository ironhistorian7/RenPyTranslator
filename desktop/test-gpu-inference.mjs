import assert from 'node:assert/strict';
import {build} from 'esbuild';
import vm from 'node:vm';
class Element {
  constructor(tag='div'){this.tag=tag;this.children=[];this.events={};this.value='';this.checked=false;this.hidden=false;this.disabled=false;this.textContent='';}
  append(...children){for(const child of children){child.parent=this;this.children.push(child);}}
  replaceChildren(...items){this.children=items;this.options=items;}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);}
  addEventListener(type,fn){this.events[type]=fn;}
  setAttribute(){}
  fire(type){this.events[type]?.();}
}
const nodes=new Map();
const document={activeElement:null,getElementById(id){if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);},createElement(tag){return new Element(tag);},createTextNode(text){const e=new Element('#text');e.textContent=text;return e;}};
class Option extends Element{constructor(text,value){super('option');this.text=text;this.value=value;}}
const compiled=await build({entryPoints:['src/inference.ts'],bundle:true,write:false,platform:'node',format:'cjs'});
const module={exports:{}};vm.runInNewContext(compiled.outputFiles[0].text,{module,exports:module.exports,document,Option});
const basic={num_ctx:8192,parallel:1,kv_cache_type:'q8_0',context_txt_enabled:false};
const high={...basic,num_ctx:16384,parallel:2,context_txt_enabled:true};
const presets=[{...basic,id:'6gb',minimum_total_mib:5888,support:'capacity_simulation'},{...high,id:'12gb',minimum_total_mib:11264,support:'measured'}];
let settings={gpu_mode:'all',gpu_ids:[],inference_preset:'auto',inference_overrides:{},gpu_overrides:{}};
const devices=[{id:'GPU-A',name:'Same GPU',total:5888,selectable:true},{id:'GPU-B',name:'Same GPU',total:12288,selectable:true}];
const preview={presets,detected_profile:'6gb',total_mib:5888,recommended:basic,effective:basic,devices:devices.map((g,i)=>({id:g.id,name:g.name,total_mib:g.total,recommended:i?high:basic,effective:i?high:basic,warnings:[]}))};
const snapshot={settings:{...settings},gpus:devices,inference:preview};
const panel=new module.exports.InferencePanel((preset,overrides)=>{settings={...settings,inference_preset:preset,inference_overrides:overrides};panel.render(settings,snapshot);},()=>{},overrides=>{settings={...settings,gpu_overrides:overrides};panel.render(settings,snapshot);});
panel.render(settings,snapshot);panel.controls(false,false,true);
const cards=()=>document.getElementById('inference-devices').children;
const parts=card=>({title:card.children[0],workers:card.children[1].children[1],kv:card.children[2].children[1],applied:card.children[3],reset:card.children[5]});
let a=parts(cards()[0]),b=parts(cards()[1]);
assert.match(a.title.textContent,/GPU-A/);assert.match(b.title.textContent,/GPU-B/);
assert.match(a.applied.textContent,/처리 1/);assert.match(b.applied.textContent,/처리 2/);
a.workers.value='4';document.activeElement=a.workers;a.workers.fire('input');assert.equal(settings.gpu_overrides['GPU-A'].parallel,4);assert.equal(a.workers.value,'4');
b.kv.value='f16';b.kv.fire('change');assert.equal(settings.gpu_overrides['GPU-B'].kv_cache_type,'f16');assert.equal(settings.gpu_overrides['GPU-A'].parallel,4);
settings={...settings,inference_overrides:{parallel:3,kv_cache_type:'q4_0'}};panel.render(settings,snapshot);assert.match(a.applied.textContent,/처리 4 · KV 캐시 q4_0/);assert.match(b.applied.textContent,/처리 3 · KV 캐시 f16/);
const saved=JSON.stringify(settings);settings=JSON.parse(saved);panel.render(settings,snapshot);assert.equal(b.kv.value,'f16');assert.equal(a.workers.value,'4');
a.reset.fire('click');assert.equal(settings.gpu_overrides['GPU-A'],undefined);assert.equal(settings.gpu_overrides['GPU-B'].kv_cache_type,'f16');
panel.controls(true,true,true);assert.equal(a.workers.disabled,true);assert.equal(b.kv.disabled,true);assert.equal(b.reset.disabled,true);
panel.controls(false,true,true);assert.equal(b.kv.disabled,false);
settings={...settings,gpu_mode:'selected',gpu_ids:['GPU-B']};panel.render(settings,snapshot);assert.equal(cards().length,1);assert.match(parts(cards()[0]).title.textContent,/GPU-B/);assert.equal(settings.gpu_overrides['GPU-B'].kv_cache_type,'f16');
console.log('GPU threshold, independent overrides, inheritance, reload, focus, reset, selection, and lock passed.');
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const interpreter=process.env.RPT_TEST_PYTHON||fileURLToPath(new URL(process.platform==='win32'?'../.venv/Scripts/python.exe':'../.venv/bin/python',import.meta.url));
const real=JSON.parse(execFileSync(interpreter,['-B','-c',`import sys,json
sys.path.insert(0,'../app')
from gpu_policy import inference_preview
p={'gpu_mode':'all','gpu_ids':[],'inference_preset':'auto','inference_overrides':{'parallel':3},'gpu_overrides':{'GPU-A':{'parallel':4},'GPU-B':{'kv_cache_type':'f16'}}}
d=[{'id':'GPU-A','name':'Same GPU','vendor':'nvidia','total':5888,'selectable':True},{'id':'GPU-B','name':'Same GPU','vendor':'nvidia','total':16128,'selectable':True},{'id':'GPU-C','name':'Small GPU','vendor':'nvidia','total':5887,'selectable':True}]
print(json.dumps(inference_preview(d,p,environment={})))`],{encoding:'utf8'}));
settings={gpu_mode:'all',gpu_ids:[],inference_preset:'auto',inference_overrides:{parallel:3},gpu_overrides:{'GPU-A':{parallel:4},'GPU-B':{kv_cache_type:'f16'}}};
const realSnapshot={settings:{...settings},gpus:devices,inference:real};panel.render(settings,realSnapshot);assert.equal(cards().length,2);
for(const [i,device] of real.devices.entries()){
  const card=cards().find(c=>parts(c).title.textContent.includes(device.id)), p=parts(card);assert.equal(p.applied.textContent,`적용: 동시 처리 ${device.effective.parallel} · KV 캐시 ${device.effective.kv_cache_type}`);
  assert.equal(card.children[4].textContent,device.warnings.join(' '));
}
console.log('Synthetic GPU frontend values and warnings match actual backend preview, including 5888 MiB threshold.');
