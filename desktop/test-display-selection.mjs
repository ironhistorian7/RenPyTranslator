// Pure task/model helpers and production model panel in a tiny DOM stub.
// No Electron, process launch, helper, inventory, network or model execution.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';
import {build} from 'esbuild';

const desktop=path.dirname(fileURLToPath(import.meta.url));
async function module(name,globals={}){
  const compiled=await build({entryPoints:[path.join(desktop,'src',name)],bundle:true,write:false,platform:'node',format:'cjs'});
  const output={exports:{}};vm.runInNewContext(compiled.outputFiles[0].text,{module:output,exports:output.exports,...globals});return output.exports;
}
const {selectTask}=await module('task-selection.ts');
let selection=new Set(['run']);
selection=selectTask(selection,'failed',true);selection=selectTask(selection,'check',true);
assert.deepEqual([...selection],['run','failed','check']);
for(const key of ['names','font','display','layout'])selection=selectTask(selection,key,true);
assert(selection.has('run'));
selection=selectTask(selection,'retranslate',true);
assert(!selection.has('run'));assert(selection.has('failed'));assert(selection.has('check'));
selection=selectTask(selection,'update',true);
assert(!selection.has('retranslate'));assert(selection.has('update'));assert(selection.has('names'));
selection=selectTask(selection,'update',false);
assert(!selection.has('run'));assert(selection.has('failed'));assert(selection.has('check'));

const library={selected:'rpt-local-a1:latest',installed:[
  {id:'rpt-local-a1:latest',title:'North Instruct-IQ2_XS.gguf',size:2000000000,ready:true},
  {id:'rpt-local-b2:latest',title:'Delta Translate-Q6_K.gguf',size:3000000000,ready:true}
],pending:[],folder:'Synthetic models'};
const {modelTitle,selectedModelId}=await module('model-name.ts');
assert.equal(modelTitle(library.selected,library),'North Instruct-IQ2_XS.gguf');
assert.equal(modelTitle('rpt-local-b2:latest',library),'Delta Translate-Q6_K.gguf');
assert.equal(modelTitle('unlisted:latest',library,'Fallback Instruct.gguf'),'Fallback Instruct.gguf');
assert.equal(modelTitle('unlisted:latest',library),'unlisted:latest');
assert.equal(selectedModelId({...library,selected:null},'old-selected:latest'),null);
assert.equal(modelTitle(selectedModelId({...library,selected:null},'old-selected:latest'),library),'선택 안 함');

class Element{
  textContent='';value='';hidden=false;disabled=false;children=[];
  replaceChildren(...nodes){this.children=nodes;}
  querySelectorAll(){return [];}
}
const elements=new Map(),document={getElementById:id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);}};
class Option{constructor(text,value){this.textContent=text;this.text=text;this.value=value;}}
const {ModelsPanel}=await module('models.ts',{document,Option});
let notification;
const panel=new ModelsPanel(()=>{},()=>{},value=>{notification=value;});
panel.renderLibrary(library);
assert.equal(document.getElementById('installed-model').value,library.selected);
assert.match(document.getElementById('model-selection').textContent,/North Instruct-IQ2_XS\.gguf/);
assert(!document.getElementById('model-selection').textContent.includes('rpt-local-'));
const options=document.getElementById('installed-model').children;
assert.equal(options[1].value,'rpt-local-a1:latest');assert.match(options[1].text,/North Instruct-IQ2_XS\.gguf/);assert(!options[1].text.includes('rpt-local-'));
assert.equal(notification,library);

const renderer=await readFile(path.join(desktop,'src/renderer.ts'),'utf8');
assert.match(renderer,/selected=selectTask\(selected,key,checkbox.checked\)/);
assert.match(renderer,/modelTitle\(id,models.library/);
assert.match(renderer,/modelTitle\(model.name,models.library,model.display_name\)/);
assert(!renderer.includes('s.fixes.includes(key))for(const k of translations)selected.delete(k)'));
console.log('Combined translation/repair/check selection, standalone repairs, filename labels, empty selection and retained backend aliases passed with synthetic data.');
