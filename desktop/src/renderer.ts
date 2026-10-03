import type {Settings,Snapshot,Job,Reply,GPU} from './types';
import {ModelsPanel} from './models';
const $=<T extends HTMLElement=HTMLElement>(id:string)=>document.getElementById(id) as T;
const input=(id:string)=>$<HTMLInputElement>(id);
const button=(id:string)=>$<HTMLButtonElement>(id);
let page='basic',workMode='basic';
let snapshot:Snapshot|null=null;
let settings:Settings={output:'project',suffix:'-kr',scale:'default',language_corner:'right',language_margin:12,source_language:'english',theme:'system',gpu_mode:'auto',gpu_ids:[]};
let job:Job={running:false,canceling:false,exitCode:null,result:null,stage:'대기 중',completed:null,total:null,started:null};
let selected=new Set<string>();
let refreshing=false,submitting=false,dirty=false;
let gpuSignature='';
let modelBusy=false;
let restoringLanguage=0;
const models=new ModelsPanel(busy=>{modelBusy=busy;updateControls();},message);
function message(text:string,error=false){$('message').hidden=!text;$('message').textContent=text;$('message').classList.toggle('error',error);}
function value<T>(reply:Reply<T>):T|undefined{if(!reply.ok){message(reply.error,true);return undefined;}return reply.value;}
function applyTheme(dark:boolean){document.documentElement.dataset.theme=dark?'dark':'light';}
function getSettings():Settings{return {...settings,source_language:($('source-language') as HTMLSelectElement).value as Settings['source_language'],output:input('output').value,suffix:input('suffix').value,scale:input('scale').value,language_corner:($('corner') as HTMLSelectElement).value as 'left'|'right',language_margin:Number(input('margin').value)};}
function changed(){dirty=true;updateControls();preview();}
function fillSettings(s:Settings){settings={...s,gpu_ids:[...s.gpu_ids]};($('source-language') as HTMLSelectElement).value=s.source_language||'english';input('output').value=s.output;input('suffix').value=s.suffix;input('scale').value=s.scale;($('corner') as HTMLSelectElement).value=s.language_corner;input('margin').value=String(s.language_margin);renderThemeButtons();renderGPUPolicy();preview();}
function renderThemeButtons(){document.querySelectorAll<HTMLButtonElement>('button[data-theme]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.theme===settings.theme)));}
function preview(){
  if(!snapshot)return;
  const s=getSettings();const output=s.output.trim()||'project';
  const root=/^(?:[A-Za-z]:[\\/]|\\\\)/.test(output)?output:snapshot.root+'\\'+output;
  const isSource=(document.querySelector<HTMLInputElement>('input[name=source]:checked')?.value==='source');
  const name=isSource&&input('source-path').value.trim()?input('source-path').value.trim().replace(/[\\/]+$/,'').split(/[\\/]/).pop():'<게임명>';
  input('output-preview').value=root.replace(/[\\/]+$/,'')+'\\'+name+s.suffix;
}
function currentTasks(){return workMode==='basic'?['run']:[...selected];}
function renderSummary(){
  const count=[...selected].filter(k=>snapshot?.fixes.includes(k)).length;
  $('repair-count').textContent=count?`${count}개 선택`:'';
  $('selection-summary').textContent=selected.size?'실행: '+[...selected].map(k=>snapshot?.tasks[k]||k).join(', '):'실행할 작업을 하나 이상 선택하세요.';
}
function renderTasks(s:Snapshot){
  for(const [key,title] of Object.entries(s.tasks)){
    const label=document.createElement('label');const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.value=key;checkbox.dataset.task=key;
    checkbox.addEventListener('change',()=>{
      checkbox.checked?selected.add(key):selected.delete(key);
      if(checkbox.checked&&(key==='run'||key==='retranslate')){
        const other=key==='run'?'retranslate':'run';selected.delete(other);
        const control=document.querySelector<HTMLInputElement>(`[data-task="${other}"]`);if(control)control.checked=false;
      }
      renderSummary();updateControls();
    });
    label.append(checkbox,document.createTextNode(title));$(s.fixes.includes(key)?'fixes':'tasks').append(label);
  }
}
function navigate(next:string){
  if(next==='advanced'&&workMode!=='advanced'){
    selected.clear();document.querySelectorAll<HTMLInputElement>('[data-task]').forEach(c=>c.checked=false);($('repairs') as HTMLDetailsElement).open=false;
    $('advanced-options').scrollTop=0;
  }
  if(next==='basic'||next==='advanced')workMode=next;
  page=next;
  document.querySelectorAll<HTMLButtonElement>('[data-page]').forEach(b=>{b.classList.toggle('selected',b.dataset.page===page);if(b.dataset.page===page)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});
  $('page-title').textContent=page==='basic'?'기본':page==='advanced'?'고급 설정':page==='models'?'AI 모델':'설정 및 정보';
  $('work-page').hidden=page==='info'||page==='models';$('info-page').hidden=page!=='info';$('models-page').hidden=page!=='models';$('run-actions').hidden=page==='info'||page==='models';$('info-actions').hidden=page!=='info';
  $('basic-options').hidden=page!=='basic';$('advanced-options').hidden=page!=='advanced';$('selection-summary').hidden=page!=='advanced';
  $('work-page').classList.toggle('advanced',page==='advanced');
  message('');renderSummary();updateControls();
  if(page==='info')void refresh();
  if(page==='models'){void refresh();void models.enter();}
}
function updateControls(){
  const locked=job.running||submitting||modelBusy;
  models.locked=job.running||submitting;models.controls();
  button('start').disabled=!snapshot||locked||restoringLanguage>0||!currentTasks().length;
  button('start').textContent=workMode==='advanced'?'선택 작업 실행':'번역 시작';
  button('cancel').disabled=!job.running||job.canceling;
  button('save').disabled=!snapshot||locked||!dirty;
  button('open-result').disabled=!job.result;
  for(const id of ['source-path','source-language','output','suffix','scale','margin','corner','browse-source','browse-output'])($<HTMLInputElement>(id)).disabled=locked;
  document.querySelectorAll<HTMLInputElement>('input[name=source],input[name=gpu-mode],[data-task],[data-gpu]').forEach(c=>c.disabled=locked||!snapshot);
}
function renderJob(next:Job){
  job=next;
  $('status').textContent=job.stage+(job.running&&job.total!==null&&job.completed!==null?` · ${job.completed.toLocaleString()} / ${job.total.toLocaleString()}`:'');
  const progress=$<HTMLProgressElement>('progress');
  const hasProgress=job.running&&job.total!==null&&job.total>0&&job.completed!==null;
  progress.value=hasProgress?100*job.completed!/job.total!:job.exitCode===0?100:0;
  progress.classList.toggle('busy',job.running&&!hasProgress);
  updateControls();
}
function appendLog(text:string){
  const el=$('log');const bottom=el.scrollHeight-el.scrollTop-el.clientHeight<50;
  el.textContent=((el.textContent||'')+text).slice(-500000);
  if(bottom)el.scrollTop=el.scrollHeight;
}
function gb(mb:number|null){return mb===null?'미제공':(mb/1024).toFixed(1)+' GB';}
function gpuNode(g:GPU){
  const el=document.createElement('div');el.className='gpu-device';
  const head=document.createElement('div');head.className='gpu-device-head';
  const name=document.createElement('span');name.textContent=g.name;
  const state=document.createElement('span');state.textContent=g.active===true?'번역에 사용 중':g.active===false?'번역 모델 미사용':'실제 사용 여부 미확인';head.append(name,state);
  const details=document.createElement('div');details.className='gpu-details';
  for(const text of [`메모리 ${gb(g.used)} / ${gb(g.total)}`,`사용률 ${g.utilization===null?'미제공':g.utilization+'%'}`,`드라이버 ${g.driver||'미확인'}`]){const span=document.createElement('span');span.textContent=text;details.append(span);}
  el.append(head,details);return el;
}
function renderGPUPolicy(){
  document.querySelectorAll<HTMLInputElement>('input[name=gpu-mode]').forEach(c=>c.checked=c.value===settings.gpu_mode);
  $('gpu-select').hidden=settings.gpu_mode!=='selected';
  $('gpu-help').textContent=settings.gpu_mode==='auto'?'자동 선택은 모델이 한 GPU에 들어가면 해당 장치를 우선 사용합니다.':settings.gpu_mode==='all'?'실행 엔진이 지원하는 GPU에 모델을 나눠 올립니다. 장치 조합에 따라 지원 여부와 속도가 달라집니다.':'NVIDIA GPU를 선택하세요. 두 개 이상 선택하면 선택한 장치에 모델을 분산합니다.';
  document.querySelectorAll<HTMLInputElement>('[data-gpu]').forEach(c=>c.checked=settings.gpu_ids.includes(c.value));
}
function renderInfo(s:Snapshot){
  $('default-model').textContent=s.defaultModel||'선택 안 함';$('runtime').textContent=s.runtime+' · 로컬 실행';$('tool-root').textContent=s.root;
  models.updateHardware(s);
  const loaded=s.models.flatMap(m=>m.loaded);
  $('active-model').textContent=loaded.length?loaded.map(m=>m.name).join(', '):s.models.length?'모델을 준비하고 있습니다.':'실행 중인 모델이 없습니다.';
  $('model-state').textContent=loaded.length?'실행 중':s.models.length?'준비 중':'대기';
  $('model-memory').textContent=loaded.length?loaded.map(m=>`GPU ${m.vram===null?'미제공':(m.vram/1024**3).toFixed(1)+' GB'} / 전체 ${m.size===null?'미제공':(m.size/1024**3).toFixed(1)+' GB'}`).join(' · '):'모델을 실행하면 표시됩니다.';
  $('gpu-count').textContent=`${s.gpus.length}개 장치`;
  $('gpu-list').replaceChildren(...s.gpus.map(gpuNode));
  if(!s.gpus.length)$('gpu-list').textContent='GPU 정보를 확인할 수 없습니다.';
  const signature=s.gpus.filter(g=>g.selectable).map(g=>g.id).join('|');
  if(signature!==gpuSignature||!$('gpu-select').childNodes.length){
    gpuSignature=signature;$('gpu-select').replaceChildren();
    for(const gpu of s.gpus.filter(g=>g.selectable)){
      const label=document.createElement('label'),c=document.createElement('input');c.type='checkbox';c.value=gpu.id;c.dataset.gpu=gpu.id;c.checked=settings.gpu_ids.includes(gpu.id);
      c.addEventListener('change',()=>{settings.gpu_ids=c.checked?[...new Set([...settings.gpu_ids,gpu.id])]:settings.gpu_ids.filter(i=>i!==gpu.id);changed();});
      label.append(c,document.createTextNode(gpu.name));$('gpu-select').append(label);
    }
    if(!signature)$('gpu-select').textContent='직접 선택 가능한 NVIDIA 장치가 없습니다. 자동 또는 모두 사용을 선택하세요.';
  }
  const warnings=[...s.warnings];
  if(settings.gpu_mode==='selected'&&settings.gpu_ids.some(id=>!s.gpus.some(g=>g.id===id)))warnings.push('이전에 선택한 GPU가 없습니다. 연결된 장치를 다시 선택하세요.');
  $('device-warning').hidden=!warnings.length;$('device-warning').textContent=warnings.join('\n');
  renderGPUPolicy();updateControls();
}
async function refresh(){
  if(refreshing)return;refreshing=true;button('refresh').disabled=true;
  try{
    const info=value(await window.rpt.info());
    if(info){const first=!snapshot;snapshot=info;if(first){fillSettings(info.settings);renderTasks(info);value(await window.rpt.appearance(info.settings.theme));}renderInfo(info);renderSummary();updateControls();}
  }catch(e){message(String(e),true);}finally{refreshing=false;button('refresh').disabled=false;}
}
document.querySelectorAll<HTMLButtonElement>('[data-page]').forEach(b=>b.addEventListener('click',()=>navigate(b.dataset.page!)));
async function restoreProjectLanguage(){
  if(job.running||submitting)return;
  const path=input('source-path').value.trim();
  if(!path)return;
  const source=document.querySelector<HTMLInputElement>('input[name=source]:checked')?.value==='source';
  const before=($('source-language') as HTMLSelectElement).value;
  restoringLanguage++;updateControls();
  try{
    const result=value(await window.rpt.project({path,source}));
    if(result?.source_language&&path===input('source-path').value.trim()&&source===(document.querySelector<HTMLInputElement>('input[name=source]:checked')?.value==='source')&&before===($('source-language') as HTMLSelectElement).value&&!job.running&&!submitting){
      ($('source-language') as HTMLSelectElement).value=result.source_language;changed();
    }
  }finally{restoringLanguage--;updateControls();}
}
for(const [id,target] of [['browse-source','source-path'],['browse-output','output']])button(id).addEventListener('click',async()=>{const p=value(await window.rpt.browse());if(p){input(target).value=p;if(target==='output')changed();else{preview();await restoreProjectLanguage();}}});
for(const id of ['source-language','output','suffix','scale','margin','corner'])$(id).addEventListener('input',changed);
input('source-path').addEventListener('input',preview);
input('source-path').addEventListener('change',()=>void restoreProjectLanguage());
document.querySelectorAll<HTMLInputElement>('input[name=source]').forEach(c=>c.addEventListener('change',()=>{preview();void restoreProjectLanguage();}));
document.querySelectorAll<HTMLButtonElement>('button[data-theme]').forEach(b=>b.addEventListener('click',async()=>{settings.theme=b.dataset.theme as Settings['theme'];const dark=value(await window.rpt.appearance(settings.theme));if(dark!==undefined)applyTheme(dark);renderThemeButtons();changed();}));
document.querySelectorAll<HTMLInputElement>('input[name=gpu-mode]').forEach(c=>c.addEventListener('change',()=>{settings.gpu_mode=c.value as Settings['gpu_mode'];renderGPUPolicy();changed();}));
button('refresh').addEventListener('click',()=>void refresh());
button('save').addEventListener('click',async()=>{
  submitting=true;updateControls();
  try{const saved=value(await window.rpt.save(getSettings()));if(saved){fillSettings(saved);dirty=false;message('설정을 저장했습니다. GPU 설정은 다음 작업부터 적용됩니다.');}}
  finally{submitting=false;updateControls();}
});
button('start').addEventListener('click',async()=>{
  message('');submitting=true;updateControls();
  try{const result=value(await window.rpt.start({path:input('source-path').value.trim(),source:document.querySelector<HTMLInputElement>('input[name=source]:checked')?.value==='source',tasks:currentTasks(),settings:getSettings()}));if(result){dirty=false;renderJob(result);}}
  finally{submitting=false;updateControls();}
});
button('cancel').addEventListener('click',async()=>{const state=value(await window.rpt.cancel());if(state)renderJob(state);});
button('open-result').addEventListener('click',async()=>{value(await window.rpt.open('result'));});
button('open-logs').addEventListener('click',async()=>{value(await window.rpt.open('logs'));});
window.rpt.subscribe(event=>{if(event.type==='log')appendLog(event.text);else if(event.type==='job'){renderJob(event.job);if(!event.job.running&&page==='info')void refresh();}else if(event.type==='theme')applyTheme(event.dark);else if(event.type==='navigate')navigate(event.page);else if(event.type==='modelJob')models.job(event.job);});
void (async()=>{const state=value(await window.rpt.state());if(state){applyTheme(state.dark);renderJob(state.job);models.job(state.modelJob);if(state.log)$('log').textContent=state.log;}await refresh();})();
setInterval(()=>{if((page==='info'||page==='models')&&!document.hidden)void refresh();},8000);
