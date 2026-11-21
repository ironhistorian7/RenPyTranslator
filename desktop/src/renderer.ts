import type {Settings,Snapshot,Job,JobIssue,Reply,GPU,GameFormat,ModelLibrary,InferencePreview} from './types';
import {ModelsPanel} from './models';
import {NamesReview} from './name-review';
import {TranslationReviewPanel} from './translation-review';
import {InferencePanel} from './inference';
import {inferenceSummary} from './inference-summary';
import {RuntimesPanel} from './runtimes';
import {modelTitle,selectedModelId} from './model-name';
import {selectTask} from './task-selection';
import {countSummary} from './job-events';
const $=<T extends HTMLElement=HTMLElement>(id:string)=>document.getElementById(id) as T;
const input=(id:string)=>$<HTMLInputElement>(id);
const button=(id:string)=>$<HTMLButtonElement>(id);
let page='translate',step=0,showJob=false,source=true,projectGeneration=0;
let snapshot:Snapshot|null=null;
let workDefaults:Settings|null=null;
let settings:Settings={output:'project',suffix:'-kr',scale:'default',language_corner:'right',language_margin:12,source_language:'english',show_reference:true,theme:'system',gpu_mode:'auto',gpu_ids:[],inference_preset:'auto',inference_overrides:{},gpu_overrides:{}};
let job:Job={running:false,canceling:false,exitCode:null,result:null,stage:'대기 중',completed:null,total:null,started:null};
let selected=new Set<string>(['run']);
let refreshing=false,submitting=false,dirty=false;
let gpuSignature='';
let modelBusy=false;
let supportBusy=false;
let restoringLanguage=0;
let gameFormat:GameFormat|null=null;
const models=new ModelsPanel(busy=>{modelBusy=busy;updateControls();},message,renderWizardModels);
const inference=new InferencePanel((preset,overrides)=>{settings.inference_preset=preset;settings.inference_overrides=overrides;changed();},()=>button('save').click(),overrides=>{settings.gpu_overrides=overrides;changed();});
const runtimes=new RuntimesPanel(id=>{settings.runtime_id=id;changed();},busy=>{modelBusy=busy;updateControls();},message,refresh);
const namesReview=new NamesReview(renderJob);
const translationReview=new TranslationReviewPanel(renderJob);
const translations=new Set(['run','retranslate','update']);
const engineList='Ren’Py · RPG Maker MV/MZ · KiriKiri · Unity';
const hasReference=()=>!['kirikiri','unity'].includes(gameFormat?.engine||'');
function taskTitle(key:string){
  if(['kirikiri','unity'].includes(gameFormat?.engine||'')&&key==='check')return '패치 검사 (정적)';
  if(gameFormat?.engine==='rpgmaker'){
    if(key==='names')return '인명 표기 보정';
    if(key==='check')return '패치 검사 (정적)';
  }
  return snapshot?.tasks[key]||key;
}
function modelTaskSelected(){
  const inference=new Set(['run','retranslate','update','translate','sample','analyze','review','names','failed']);
  if(gameFormat?.engine==='rpgmaker')inference.delete('names');
  return [...selected].some(key=>inference.has(key));
}
function deviceModelsReady(){
  if(settings.gpu_mode==='auto'||!snapshot||!models.library)return false;
  const devices=currentInference()?.devices;
  if(!devices||devices.length<2)return false;
  return devices.every(d=>{const id=settings.gpu_overrides?.[d.id]?.model;return !!id&&models.library!.installed.some(m=>m.ready&&m.id===id);});
}
function taskNodes(keys:string[]){
  const nodes:Node[]=[];
  for(const [index,key] of keys.entries()){
    if(index)nodes.push(document.createTextNode(', '));
    const label=document.createElement('span');label.dataset.taskLabel=key;label.textContent=taskTitle(key);
    if(key==='answers'||key==='routes'){const beta=document.createElement('sup');beta.className='beta-badge';beta.textContent='beta';label.append(beta);}
    nodes.push(label);
  }
  return nodes;
}
function message(text:string,error=false){$('message').hidden=!text;$('message').textContent=text;$('message').classList.toggle('error',error);}
function value<T>(reply:Reply<T>):T|undefined{if(!reply.ok){message(reply.error.includes('\n로그: ')?job.issue?.title||reply.error.split('\n')[0]:reply.error,true);return undefined;}return reply.value;}
function applyTheme(dark:boolean){document.documentElement.dataset.theme=dark?'dark':'light';}
function getSettings():Settings{return {...settings,source_language:($('source-language') as HTMLSelectElement).value as Settings['source_language'],output:input('output').value,suffix:input('suffix').value,scale:input('scale').value,language_corner:($('corner') as HTMLSelectElement).value as 'left'|'right',language_margin:Number(input('margin').value),show_reference:input('show-reference').checked};}
let inferenceSerial=0;let inferenceTimer:ReturnType<typeof setTimeout>|undefined;
let liveInference:{key:string;value:InferencePreview}|null=null;
function inferenceKeyFor(s:Settings){return JSON.stringify([s.runtime_id||'',s.gpu_mode,s.gpu_ids,s.inference_preset,s.inference_overrides,s.gpu_overrides||{}]);}
function inferenceKey(){return inferenceKeyFor(settings);}
function currentInference(){return liveInference?.key===inferenceKey()?liveInference.value:snapshot&&inferenceKeyFor(snapshot.settings)===inferenceKey()?snapshot.inference:null;}
function renderInference(){if(snapshot){const current=liveInference?.key===inferenceKey()?{...snapshot,settings:{...settings},inference:liveInference.value}:snapshot;inference.render(settings,current);}}
function requestInference(){
  const serial=++inferenceSerial,key=inferenceKey(),current=structuredClone(settings);
  clearTimeout(inferenceTimer);
  inferenceTimer=setTimeout(async()=>{
    try{const reply=await window.rpt.inferencePreview(current);if(serial!==inferenceSerial||key!==inferenceKey())return;if(reply.ok){liveInference={key,value:reply.value};renderInference();wizardHardware();if(step===3)renderReview();updateControls();}else message(`GPU 설정 미리보기: ${reply.error}`,true);}
    catch(error){if(serial===inferenceSerial)message(String(error),true);}
  },200);
}
function changed(){dirty=true;renderInference();requestInference();wizardHardware();renderSummary();updateControls();preview();}
function fillSettings(s:Settings){settings={...s,gpu_ids:[...s.gpu_ids],gpu_overrides:structuredClone(s.gpu_overrides||{})};($('source-language') as HTMLSelectElement).value=s.source_language||'english';input('output').value=s.output;input('suffix').value=s.suffix;input('scale').value=s.scale;($('corner') as HTMLSelectElement).value=s.language_corner;input('margin').value=String(s.language_margin);input('show-reference').checked=s.show_reference!==false;renderThemeButtons();renderGPUPolicy();preview();}
function renderThemeButtons(){document.querySelectorAll<HTMLButtonElement>('button[data-theme]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.theme===settings.theme)));}
function outputPath(){
  const output=getSettings().output.trim()||'project';
  const root=/^(?:[A-Za-z]:[\\/]|\\\\)/.test(output)?output:(snapshot?.root||'')+'\\'+output;
  let name=gameFormat?.game_name||input('source-path').value.trim().replace(/[\\/]+$/,'').split(/[\\/]/).pop()||'<게임명>';
  const suffix=getSettings().suffix;if(suffix&&name.endsWith(suffix))name=name.slice(0,-suffix.length);
  return root.replace(/[\\/]+$/,'')+'\\'+name+suffix;
}
function preview(){
  $('output-preview').textContent='결과 폴더: '+outputPath();
  $('output-note').textContent=currentTasks().includes('retranslate')?'재번역은 비교용 이름을 붙인 별도 폴더에 저장합니다.':'';
  if(step===3)renderReview();
}
function currentTasks(){return [...selected];}
function renderSummary(){
  const count=[...selected].filter(k=>snapshot?.fixes.includes(k)).length;
  $('repair-count').textContent=count?`${count}개 선택`:'';
  $('selection-summary').replaceChildren(...(selected.size?[document.createTextNode('실행: '),...taskNodes([...selected])]:[document.createTextNode('실행할 작업을 하나 이상 선택하세요.')]));
  $('language-default-note').hidden=gameFormat?.engine!=='renpy'||![...selected].some(key=>translations.has(key));
  $('update-fields').hidden=!selected.has('update');
  document.querySelectorAll<HTMLElement>('[data-task-label="check"], [data-task-label="names"]').forEach(label=>label.textContent=taskTitle(label.dataset.taskLabel!));
  $('wizard-model-note').textContent=modelTaskSelected()&&!selectedModelId(models.library,snapshot?.defaultModel)&&!deviceModelsReady()?'번역 모델을 선택하세요.':'';
  wizardHardware();
  document.querySelector('label[for="source-path"]')!.textContent=selected.has('update')?'기존 프로젝트·결과 폴더':'게임 또는 프로젝트 폴더';
  preview();
}
function renderTasks(s:Snapshot){
  for(const [key,title] of Object.entries(s.tasks)){
    if(key==='language')continue;
    const label=document.createElement('label'),checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.value=key;checkbox.dataset.task=key;checkbox.checked=selected.has(key);
    checkbox.addEventListener('change',()=>{
      selected=selectTask(selected,key,checkbox.checked);
      document.querySelectorAll<HTMLInputElement>('[data-task]').forEach(c=>c.checked=selected.has(c.dataset.task!));
      renderSummary();updateControls();
    });
    label.append(checkbox,...taskNodes([key]));
    $(key==='run'?'basic-tasks':s.fixes.includes(key)?'fixes':'tasks').append(label);
  }
}
function navigate(next:string){
  page=next==='basic'||next==='advanced'?'translate':next;
  document.querySelectorAll<HTMLButtonElement>('[data-page]').forEach(b=>{b.classList.toggle('selected',b.dataset.page===page);if(b.dataset.page===page)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});
  $('page-title').textContent=page==='translate'?'번역':page==='models'?'AI 모델':'설정 및 정보';
  $('work-page').hidden=page!=='translate';$('info-page').hidden=page!=='info';$('models-page').hidden=page!=='models';$('info-actions').hidden=page!=='info';
  message('');renderWizard();
  if(page==='info')void refresh();
  if(page==='models'){void refresh();void models.enter();}
}
function renderWizard(){
  const finished=showJob&&isFinished();
  document.querySelectorAll<HTMLElement>('[data-step]').forEach(p=>p.hidden=showJob||Number(p.dataset.step)!==step);
  $('job-pane').hidden=!showJob||finished;$('wizard-steps').hidden=showJob;
  $('completion-pane').hidden=!finished;button('finish').hidden=!finished;button('back').hidden=finished;
  $('step-count').textContent=finished?(job.exitCode===0?'완료':'작업 실패'):showJob?'작업 진행':`${step+1} / 4`;
  $('run-actions').hidden=finished||!showJob&&step!==3;button('wizard-next').hidden=showJob||step===3;
  button('back').textContent=showJob?'설정으로 돌아가기':'이전';
  document.querySelectorAll<HTMLElement>('#wizard-steps li').forEach((p,i)=>{p.classList.toggle('current',i===step);p.classList.toggle('complete',i<step);p.setAttribute('aria-current',i===step?'step':'false');p.querySelector('span')!.textContent=i<step?'✓':String(i+1);});
  $('engine-badge').hidden=page!=='translate'||!gameFormat;
  $('engine-badge').textContent=gameFormat?.engine_label||'';
  $('renpy-display').hidden=gameFormat?.engine!=='renpy';
  if(finished)renderCompletion();
  if(step===3)renderReview();updateControls();
}
function isFinished(){return !job.running&&job.exitCode!==null&&job.exitCode!==130;}
function renderCompletion(){
  const success=job.exitCode===0;
  $('completion-pane').classList.toggle('failed',!success);
  $('completion-mark').textContent=success?'✓':'!';
  $('completion-title').textContent=success?(selected.has('run')||selected.has('retranslate')?'번역이 완료되었습니다':'작업이 완료되었습니다'):'작업을 완료하지 못했습니다';
  $('completion-description').textContent=success?'결과를 저장했습니다. 확인을 누르면 새 작업을 시작할 수 있습니다.':'저장된 번역은 유지됩니다. 오류 내용을 확인한 뒤 같은 폴더에서 이어갈 수 있습니다.';
  $('completion-counts').textContent=countSummary(job);
  $('completion-notices').textContent=(job.notices||[]).join('\n');
  $('completion-time').textContent=elapsedText();
  renderWarnings();
  $('completion-path').textContent=success&&job.result?job.result:'';
  button('completion-result').hidden=!success||!job.result;
  $('completion-log').textContent=$('log').textContent;
  button('finish').textContent=success?'확인':'로그 폴더 열기';
}
function renderWarnings(){
  const warnings=job.warnings||[];
  $('warning-summary').hidden=!isFinished()||!warnings.length;
  $('warning-list').replaceChildren();
  $('warning-counts').textContent=`${warnings.length}개 유형 · ${warnings.reduce((sum,item)=>sum+item.occurrences,0).toLocaleString()}회 발생`;
  for(const item of warnings){
    const row=document.createElement('div');row.className='warning-row';row.setAttribute('role','listitem');
    const title=document.createElement('strong');title.textContent=item.title;
    const count=document.createElement('span');count.className='warning-row-count';
    count.textContent=`${item.occurrences.toLocaleString()}회`+(item.item_count!==undefined?` · ${item.item_count.toLocaleString()}개 항목`:'');
    const head=document.createElement('div');head.className='warning-row-head';head.append(title,count);row.append(head);
    const meta=document.createElement('p');meta.className='warning-row-meta';const ids=item.item_ids.slice(0,6);
    meta.textContent=item.code+(ids.length?' · 항목 ID: '+ids.join(', ')+(item.item_count!==undefined&&item.item_count>ids.length?` 외 ${(item.item_count-ids.length).toLocaleString()}개`:''):'');
    row.append(meta);$('warning-list').append(row);
  }
}
async function resetWork(){
  const reply=await window.rpt.reset();
  if(!reply.ok){message(reply.error,true);return;}
  projectGeneration++;gameFormat=null;source=true;step=0;showJob=false;
  selected=new Set(['run']);input('source-path').value='';input('update-source').value='';
  document.querySelectorAll<HTMLInputElement>('[data-task]').forEach(c=>c.checked=c.dataset.task==='run');
  document.querySelectorAll<HTMLDetailsElement>('#work-page details').forEach(d=>d.open=false);
  if(workDefaults)fillSettings(structuredClone(workDefaults));
  renderInference();requestInference();
  dirty=false;job=reply.value;$('log').textContent='';$('completion-log').textContent='';$('completion-path').textContent='';$('review').replaceChildren();
  for(const id of ['progress-percent','job-counts','completion-counts','completion-notices','completion-time','warning-counts'])$(id).textContent='';
  $('warning-list').replaceChildren();$('warning-summary').hidden=true;
  $('engine-note').textContent=engineList;
  $('status').textContent='대기 중';$<HTMLProgressElement>('progress').value=0;$('progress').classList.remove('busy');
  renderSummary();navigate('translate');document.querySelector('.wizard-body')!.scrollTop=0;
  renderIssue();renderProgressTime();
  const theme=value(await window.rpt.appearance(settings.theme));if(theme!==undefined)applyTheme(theme);
}
function renderReview(){
  const s=getSettings(),id=selectedModelId(models.library,snapshot?.defaultModel);
  const hardware=currentInference(),devices=hardware?.devices||[];
  const perGPU=s.gpu_mode!=='auto'&&devices.length>=2;
  const common=modelTitle(id,models.library,id===snapshot?.defaultModel?snapshot?.defaultModelDisplayName:undefined);
  const model= !modelTaskSelected()?'사용 안 함':perGPU?devices.map(d=>{
    const assigned=s.gpu_overrides?.[d.id]?.model;
    return `${d.name} · ${assigned?modelTitle(assigned,models.library):'모델 선택 필요'}`;
  }).join(' / '):common;
  const pairs=[['게임',gameFormat?.game_name||input('source-path').value],['실행할 작업',currentTasks().map(taskTitle).join(', ')],['원문 → 번역',(s.source_language==='japanese'?'일본어':'영어')+' → 한국어'],['번역 모델',model],['결과 폴더',outputPath()],['대화 표시',(hasReference()&&s.show_reference?'한국어 + 원문 참고 (55%)':'한국어만')+ (gameFormat?.engine==='unity'?'':' · 높이 '+(s.scale==='default'?'게임 기본값':s.scale+'배'))]];
  if(gameFormat?.engine==='unity')pairs.push(['게임에서 선택할 언어',s.source_language==='japanese'?'일본어':'영어']);
  if(selected.has('update'))pairs.splice(1,0,['새 버전 게임',input('update-source').value]);
  if(modelTaskSelected())pairs.push(['GPU 실행 설정',hardware?inferenceSummary(hardware,s).summary:'GPU 설정 확인 중']);
  const warning=modelTaskSelected()&&hardware?inferenceSummary(hardware,s).warning:'';
  $('review-model-warning').textContent=warning;$('review-model-warning').hidden=!warning;
  $('review').replaceChildren();for(const [key,text] of pairs){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=key;if(key==='실행할 작업')dd.append(...taskNodes(currentTasks()));else dd.textContent=text;$('review').append(dt,dd);}
  $('apply-note').textContent=gameFormat?.engine==='unity'?'결과의 APPLY.txt를 따라 번역 에셋을 원본 게임의 같은 위치에 복사하세요.':gameFormat?.engine==='kirikiri'?'결과의 patch XP3 파일을 게임 실행 파일이 있는 폴더에 복사해 적용하세요.':gameFormat?.engine==='rpgmaker'?'결과의 data·js·fonts 폴더를 원본 게임의 같은 위치에 복사해 적용하세요. www 폴더가 있으면 그 구조를 유지하세요.':'결과의 game 폴더를 원본 게임의 game 폴더에 합쳐 적용하세요.';
}
function renderWizardModels(library:ModelLibrary){
  inference.setLibrary(library);
  const control=$<HTMLSelectElement>('wizard-model');control.replaceChildren(new Option('선택 안 함',''),...library.installed.map(m=>new Option(m.title+(m.ready?'':' · 파일 누락'),m.id)));
  control.value=library.selected||'';
  $('wizard-model-note').textContent=modelTaskSelected()&&!library.selected&&!deviceModelsReady()?'번역 모델을 선택하세요.':'';
  wizardHardware();
  if(snapshot)renderModelNames(snapshot);
  updateControls();if(step===3)renderReview();
}
function wizardHardware(){
  if(!snapshot)return;
  const devices=snapshot.gpus.filter(g=>settings.gpu_mode!=='selected'||settings.gpu_ids.includes(g.id));
  $('wizard-gpu').textContent=devices.map(g=>g.name+(g.total?' · '+gb(g.total):'')).join(' / ')||'GPU 정보 미확인';
  const hardware=currentInference(), summary=hardware?inferenceSummary(hardware,settings):null;
  const hardwareDevices=hardware?.devices||[],dual=settings.gpu_mode!=='auto'&&hardwareDevices.length>=2;
  $('wizard-model-label').hidden=dual;$('wizard-model').hidden=dual;$('wizard-device-models').hidden=!dual;
  $('installed-model-heading').textContent=dual?'설치된 모델':'사용할 번역 모델';
  $('installed-model').setAttribute('aria-label',dual?'설치된 모델':'사용할 번역 모델');
  $('apply-model').hidden=dual;$('model-selection').hidden=dual;
  $('wizard-device-models').textContent=dual?hardwareDevices.map(d=>`${d.name} · ${settings.gpu_overrides?.[d.id]?.model?modelTitle(settings.gpu_overrides[d.id].model,models.library):'모델 선택 필요'}`).join(' / '):'';
  if(dual)$('wizard-model-note').textContent=hardwareDevices.some(d=>!settings.gpu_overrides?.[d.id]?.model||settings.gpu_overrides[d.id].parallel===undefined)?'AI 모델의 고급 설정에서 GPU별 모델과 동시 처리를 입력하세요.':'';
  $('wizard-preset').textContent=summary?.summary||'GPU 설정 확인 중';
  const warning=summary?.warning||'';
  $('wizard-model-warning').hidden=!warning;$('wizard-model-warning').textContent=warning;
}
function updateControls(){
  const locked=job.running||submitting||modelBusy;
  models.locked=job.running||submitting;models.controls();
  runtimes.locked=locked;runtimes.controls();
  inference.controls(locked,dirty,!!snapshot);
  const updateReady=!selected.has('update')||!source&&!!input('update-source').value.trim();
  const ready=!!snapshot&&!locked&&!restoringLanguage&&!!gameFormat?.supported&&!!currentTasks().length&&updateReady&&(!modelTaskSelected()||!!currentInference());
  button('start').disabled=!ready;button('start').hidden=showJob&&job.running;
  button('start').textContent=showJob?'다시 실행':selected.has('update')?'업데이트 번역 시작':selected.has('retranslate')?'재번역 시작':selected.has('run')?'번역 시작':'작업 시작';
  button('wizard-next').disabled=step===0?!ready:!snapshot||locked||!!restoringLanguage;
  button('back').disabled=locked||(!showJob&&step===0);
  button('cancel').hidden=!job.running;button('cancel').disabled=!job.running||job.canceling;
  button('finish').disabled=locked;
  button('save').disabled=!snapshot||locked||!dirty;button('open-result').disabled=!job.result;
  for(const id of ['copy-issue','export-issue','issue-logs','copy-warnings'])button(id).disabled=supportBusy;
  for(const id of ['source-path','source-language','output','suffix','scale','margin','corner','show-reference','browse-source','browse-output','wizard-model','manage-models','update-source','browse-update'])($<HTMLInputElement>(id)).disabled=locked;
  input('show-reference').closest('label')!.hidden=!hasReference();
  input('show-reference').disabled=locked||!hasReference();
  document.querySelectorAll<HTMLInputElement>('input[name=gpu-mode],[data-task],[data-gpu]').forEach(c=>c.disabled=locked||!snapshot);
  document.querySelectorAll<HTMLInputElement>('[data-task]').forEach(c=>{
    const key=c.dataset.task!,unsupported=!!gameFormat?.supported_tasks&&!gameFormat.supported_tasks.includes(key);
    c.parentElement!.hidden=unsupported;
    c.disabled=locked||!snapshot||unsupported;
  });
  document.body.dataset.ready=snapshot?'true':'false';
}
function duration(seconds:number){
  seconds=Math.max(0,Math.floor(seconds));
  const hours=Math.floor(seconds/3600),minutes=Math.floor(seconds%3600/60),rest=seconds%60;
  return hours?`${hours}시간 ${minutes}분`:minutes?`${minutes}분 ${rest}초`:`${rest}초`;
}
function elapsedText(now=Date.now()){
  if(job.started===null)return job.elapsed_seconds!==undefined?'경과 '+duration(job.elapsed_seconds):'';
  const end=job.running?now:job.finished??job.started;
  return '경과 '+duration((end-job.started)/1000);
}
function renderProgressTime(){
  $('elapsed-time').textContent=elapsedText();
  if(!job.running){$('remaining-time').textContent='';return;}
  if(job.recovery?.state==='waiting'){
    const until=job.recovery.next_retry_at?Date.parse(job.recovery.next_retry_at):NaN;
    const remaining=Number.isFinite(until)?Math.max(0,Math.ceil((until-Date.now())/1000)):job.recovery.delay_seconds;
    $('remaining-time').textContent=(job.recovery.total_restarts?`누적 ${job.recovery.total_restarts}회 복구 · `:'')+(job.recovery.attempt?(job.recovery.max_attempts?`재시도 ${job.recovery.attempt}/${job.recovery.max_attempts} · `:`${job.recovery.attempt}번째 재시도 · `):'')+(remaining!==undefined?`${remaining}초 후 재시도`:'재시도 대기 중');
  }else if(job.canceling){$('remaining-time').textContent='작업을 정리하고 있습니다.';
  }else if(job.eta_seconds!==null&&job.eta_seconds!==undefined){
    const remaining=Math.max(0,job.eta_seconds-(Date.now()-(job.updated||Date.now()))/1000);
    $('remaining-time').textContent='이 단계 남은 시간 '+(remaining<60?'1분 미만':`약 ${Math.ceil(remaining/60)}분`);
  }else $('remaining-time').textContent='이 단계 남은 시간 계산 중';
}
function renderIssue(){
  const issue=job.issue?.state==='failed'?job.issue:null;
  $('issue-panel').hidden=!issue;
  if(!issue)return;
  $('issue-panel').dataset.state=issue.state;
  $('issue-state').textContent=job.running?'오류 · 작업 정리 중':'작업 중단';
  $('issue-title').textContent=issue.title;
  $('issue-detail').textContent=issue.detail||'';$('issue-detail').hidden=!issue.detail;
  $('issue-id').textContent=issue.error_id?'오류 ID: '+issue.error_id:'';$('issue-id').hidden=!issue.error_id;
}
function renderJob(next:Job){
  job=next;
  namesReview.render(job);
  translationReview.render(job);
  $('status').textContent=job.stage+(job.running&&job.total!==null&&job.completed!==null?` · ${job.completed.toLocaleString()} / ${job.total.toLocaleString()}`:'');
  const progress=$<HTMLProgressElement>('progress');
  const hasProgress=job.running&&job.total!==null&&job.total>0&&job.completed!==null;
  const percent=hasProgress?Math.min(100,Math.max(0,100*job.completed!/job.total!)):null;
  if(percent!==null)progress.value=percent;else if(job.running)progress.removeAttribute('value');else progress.value=job.exitCode===0?100:0;
  $('progress-percent').textContent=percent===null?'':`${Math.floor(percent)}%`;
  $('job-counts').textContent=countSummary(job);
  progress.classList.toggle('busy',job.running&&!hasProgress);
  renderProgressTime();renderIssue();
  if(job.running||job.exitCode!==null)showJob=true;
  else showJob=false;
  renderWizard();
}
function appendLog(text:string){
  const el=$('log');const bottom=el.scrollHeight-el.scrollTop-el.clientHeight<50;
  el.textContent=((el.textContent||'')+text).slice(-500000);
  if(isFinished())$('completion-log').textContent=el.textContent;
  if(bottom)el.scrollTop=el.scrollHeight;
}
function gb(mb:number|null){return mb===null?'미제공':(mb/1024).toFixed(1)+' GB';}
function gpuNode(g:GPU, models:Snapshot['models']){
  const el=document.createElement('div');el.className='gpu-device';
  const head=document.createElement('div');head.className='gpu-device-head';
  const name=document.createElement('span');name.textContent=g.name;
  const state=document.createElement('span');state.textContent=g.active===true?'번역에 사용 중':g.active===false?'번역 모델 미사용':'실제 사용 여부 미확인';head.append(name,state);
  const details=document.createElement('div');details.className='gpu-details';
  for(const text of [`메모리 ${gb(g.used)} / ${gb(g.total)}`,`사용률 ${g.utilization===null?'미제공':g.utilization+'%'}`,`드라이버 ${g.driver||'미확인'}`]){const span=document.createElement('span');span.textContent=text;details.append(span);}
  if(g.active===true){for(const runtime of models.filter(m=>m.loaded.length&&m.device_id===g.id)){
    const parts=[runtime.device_backend?`실행 백엔드 ${runtime.device_backend.toUpperCase()}`:null,runtime.parallel?`${runtime.parallel}개 작업`:null].filter(Boolean);
    if(parts.length){const span=document.createElement('span');span.textContent=parts.join(' · ');details.append(span);}
  }}
  el.append(head,details);return el;
}
function renderGPUPolicy(){
  document.querySelectorAll<HTMLInputElement>('input[name=gpu-mode]').forEach(c=>c.checked=c.value===settings.gpu_mode);
  $('gpu-select').hidden=settings.gpu_mode!=='selected';
  $('gpu-help').textContent=settings.gpu_mode==='auto'?'GPU를 자동으로 선택합니다.':settings.gpu_mode==='all'?'사용 가능한 GPU를 모두 사용합니다.':'사용할 GPU를 선택하세요.';
  document.querySelectorAll<HTMLInputElement>('[data-gpu]').forEach(c=>c.checked=settings.gpu_ids.includes(c.value));
}
function renderModelNames(s:Snapshot){
  const id=selectedModelId(models.library,s.defaultModel);
  $('default-model').textContent=modelTitle(id,models.library,id===s.defaultModel?s.defaultModelDisplayName:undefined);
  const loaded=s.models.flatMap(runtime=>runtime.loaded.map(model=>({model,runtime})));
  $('active-model').textContent=loaded.length?loaded.map(({model,runtime})=>modelTitle(model.name,models.library,model.display_name)+(runtime.device_name?' · '+runtime.device_name:'')).join(', '):s.models.length?'모델을 준비하고 있습니다.':'실행 중인 모델이 없습니다.';
}
function renderInfo(s:Snapshot){
  $('app-version').textContent=s.appVersion?'v'+s.appVersion:'';
  runtimes.render(s,settings.runtime_id||'');
  renderModelNames(s);$('runtime').textContent=s.runtime+' · 로컬 실행';$('tool-root').textContent=s.root;
  models.updateHardware(s);
  renderInference();requestInference();wizardHardware();
  const loaded=s.models.flatMap(m=>m.loaded);
  $('model-state').textContent=loaded.length?'실행 중':s.models.length?'준비 중':'대기';
  $('model-memory').textContent=loaded.length?loaded.map(m=>`GPU ${m.vram===null?'미제공':(m.vram/1024**3).toFixed(1)+' GB'} / 전체 ${m.size===null?'미제공':(m.size/1024**3).toFixed(1)+' GB'}`).join(' · '):'모델을 실행하면 표시됩니다.';
  $('gpu-count').textContent=`${s.gpus.length}개 장치`;
  $('gpu-list').replaceChildren(...s.gpus.map(g=>gpuNode(g,s.models)));
  if(!s.gpus.length)$('gpu-list').textContent='GPU 정보를 확인할 수 없습니다.';
  const signature=s.gpus.filter(g=>g.selectable).map(g=>g.id).join('|');
  if(signature!==gpuSignature||!$('gpu-select').childNodes.length){
    gpuSignature=signature;$('gpu-select').replaceChildren();
    for(const gpu of s.gpus.filter(g=>g.selectable)){
      const label=document.createElement('label'),c=document.createElement('input');c.type='checkbox';c.value=gpu.id;c.dataset.gpu=gpu.id;c.checked=settings.gpu_ids.includes(gpu.id);
      c.addEventListener('change',()=>{settings.gpu_ids=c.checked?[...new Set([...settings.gpu_ids,gpu.id])]:settings.gpu_ids.filter(i=>i!==gpu.id);changed();});
      label.append(c,document.createTextNode(gpu.name));$('gpu-select').append(label);
    }
    if(!signature)$('gpu-select').textContent='직접 선택 가능한 GPU가 없습니다. 장치와 실행 백엔드를 확인하세요.';
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
    if(info){const first=!snapshot;snapshot=info;if(first){workDefaults=structuredClone(info.settings);fillSettings(info.settings);renderTasks(info);value(await window.rpt.appearance(info.settings.theme));}renderInfo(info);renderSummary();updateControls();}
  }catch(e){message(String(e),true);}finally{refreshing=false;button('refresh').disabled=false;}
}
document.querySelectorAll<HTMLButtonElement>('[data-page]').forEach(b=>b.addEventListener('click',()=>navigate(b.dataset.page!)));
async function restoreProjectLanguage(){
  if(job.running||submitting)return;
  const serial=++projectGeneration,path=input('source-path').value.trim();gameFormat=null;source=true;renderWizard();
  if(!path){$('engine-note').textContent=engineList;return;}
  const before=($('source-language') as HTMLSelectElement).value;
  restoringLanguage++;updateControls();
  try{
    const result=value(await window.rpt.project({path}));
    if(!result||serial!==projectGeneration||path!==input('source-path').value.trim())return;
    gameFormat=result;source=result.source!==false;
    $('engine-note').textContent=result.stop_message||`${result.engine_label} · ${source?'원본 게임':'기존 프로젝트'}${source?'':' · 저장된 번역 사용'}`;
    for(const task of [...selected])if(result.supported_tasks&&!result.supported_tasks.includes(task))selected.delete(task);
    document.querySelectorAll<HTMLInputElement>('[data-task]').forEach(c=>c.checked=selected.has(c.dataset.task!));
    if(result.source_language&&before===($('source-language') as HTMLSelectElement).value){($('source-language') as HTMLSelectElement).value=result.source_language;}
    if(hasReference()&&result.show_reference!==undefined)input('show-reference').checked=result.show_reference;
    if(result.textbox_scale!==undefined)input('scale').value=result.textbox_scale===null?'default':String(result.textbox_scale);
    renderSummary();renderWizard();
  }catch(e){message(String(e),true);}finally{restoringLanguage--;updateControls();}
}
for(const [id,target] of [['browse-source','source-path'],['browse-output','output'],['browse-update','update-source']])button(id).addEventListener('click',async()=>{const p=value(await window.rpt.browse());if(p){input(target).value=p;if(target==='output')changed();else if(target==='update-source'){updateControls();preview();}else{preview();await restoreProjectLanguage();}}});
input('update-source').addEventListener('input',()=>{updateControls();preview();});
for(const id of ['source-language','output','suffix','scale','margin','corner','show-reference'])$(id).addEventListener('input',changed);
input('source-path').addEventListener('input',()=>{projectGeneration++;gameFormat=null;updateControls();preview();});
input('source-path').addEventListener('change',()=>void restoreProjectLanguage());
button('wizard-next').onclick=async()=>{if(step===0&&!gameFormat)await restoreProjectLanguage();if(step===0&&!gameFormat?.supported)return;wizardHardware();step=Math.min(step+1,3);message('');renderWizard();document.querySelector('.wizard-body')!.scrollTop=0;};
button('back').onclick=()=>{if(showJob)showJob=false;else step=Math.max(0,step-1);message('');renderWizard();};
button('manage-models').onclick=()=>navigate('models');
$('wizard-model').onchange=async()=>{submitting=true;updateControls();try{($('installed-model') as HTMLSelectElement).value=($('wizard-model') as HTMLSelectElement).value;await models.select();await refresh();}finally{submitting=false;updateControls();}};
document.querySelectorAll<HTMLButtonElement>('button[data-theme]').forEach(b=>b.addEventListener('click',async()=>{settings.theme=b.dataset.theme as Settings['theme'];const dark=value(await window.rpt.appearance(settings.theme));if(dark!==undefined)applyTheme(dark);renderThemeButtons();changed();}));
document.querySelectorAll<HTMLInputElement>('input[name=gpu-mode]').forEach(c=>c.addEventListener('change',()=>{settings.gpu_mode=c.value as Settings['gpu_mode'];renderGPUPolicy();changed();}));
button('refresh').addEventListener('click',()=>void refresh());
button('save').addEventListener('click',async()=>{
  submitting=true;updateControls();
  try{const saved=value(await window.rpt.save(getSettings()));if(saved){workDefaults=structuredClone(saved);fillSettings(saved);renderInference();requestInference();dirty=false;message('설정을 저장했습니다.');}}
  finally{submitting=false;updateControls();}
});
button('start').addEventListener('click',async()=>{
  message('');submitting=true;updateControls();
  try{const result=value(await window.rpt.start({path:input('source-path').value.trim(),source,tasks:currentTasks(),settings:getSettings(),...(selected.has('update')?{update_source:input('update-source').value.trim()}:{})}));if(result){dirty=false;renderJob(result);}}
  finally{submitting=false;updateControls();}
});
button('cancel').addEventListener('click',async()=>{const state=value(await window.rpt.cancel());if(state)renderJob(state);});
button('open-result').addEventListener('click',async()=>{value(await window.rpt.open('result'));});
button('completion-result').addEventListener('click',async()=>{value(await window.rpt.open('result'));});
button('finish').addEventListener('click',async()=>{
  submitting=true;updateControls();message('');
  try{
    if(job.exitCode!==0){const opened=await window.rpt.open('logs');if(!opened.ok){message(opened.error,true);return;}}
    await resetWork();
  }catch(e){message(String(e),true);}finally{submitting=false;updateControls();}
});
button('open-logs').addEventListener('click',async()=>{value(await window.rpt.open('logs'));});
button('copy-warnings').addEventListener('click',async()=>{
  supportBusy=true;updateControls();
  try{
    const reply=await window.rpt.support({op:'warnings'});
    if(!reply.ok){message(reply.error,true);return;}
    message('주의 사항을 복사했습니다.');
  }catch(error){message(String(error),true);}finally{supportBusy=false;updateControls();}
});
for(const [id,op] of [['copy-issue','copy'],['export-issue','export']] as const)button(id).addEventListener('click',async()=>{
  supportBusy=true;updateControls();
  try{
    const reply=await window.rpt.support({op});
    if(!reply.ok){message(reply.error,true);return;}
    if(!reply.value.cancelled)message(op==='copy'?'오류 내용을 복사했습니다.':'진단 파일을 저장했습니다.');
  }catch(error){message(String(error),true);}finally{supportBusy=false;updateControls();}
});
button('issue-logs').addEventListener('click',async()=>{
  supportBusy=true;updateControls();
  try{
    const opened=await window.rpt.open('logs');
    if(!opened.ok){message(opened.error,true);return;}
    if(!job.running&&job.issue?.state==='failed')await resetWork();
  }catch(error){message(String(error),true);}finally{supportBusy=false;updateControls();}
});
const reportDialog=$<HTMLDialogElement>('error-report');
const reportCopy=button('error-report-copy');
const reportCopyError=$('error-report-copy-error');
function renderErrorReport(issue:JobIssue|null){
  if(!issue){reportDialog.close();return;}
  $('error-report-summary').textContent=issue.title;
  reportCopyError.hidden=true;
  if(!reportDialog.open)reportDialog.showModal();
}
reportDialog.addEventListener('cancel',event=>event.preventDefault());
reportCopy.addEventListener('click',async()=>{
  reportCopy.disabled=true;
  reportCopyError.hidden=true;
  try{
    const reply=await window.rpt.support({op:'copy'});
    if(!reply.ok)throw new Error(reply.error);
    message('오류 내용을 복사했습니다.');
  }catch{
    reportCopyError.hidden=false;
  }finally{
    reportCopy.disabled=false;
    if(reportDialog.open)reportCopy.focus();
  }
});
window.rpt.subscribe(event=>{
  if(event.type==='errorReport')renderErrorReport(event.issue);
  else if(event.type==='log')appendLog(event.text);
  else if(event.type==='job'){renderJob(event.job);if(!event.job.running&&page==='info')void refresh();}
  else if(event.type==='theme')applyTheme(event.dark);
  else if(event.type==='navigate')navigate(event.page);
  else if(event.type==='modelJob'){if(event.job.kind==='runtime')runtimes.job(event.job);else models.job(event.job);}
});
void (async()=>{const state=value(await window.rpt.state());if(state){applyTheme(state.dark);renderJob(state.job);renderErrorReport(state.reportIssue);if(state.modelJob.kind==='runtime')runtimes.job(state.modelJob);else models.job(state.modelJob);if(state.log)$('log').textContent=state.log;}await refresh();await models.refresh();renderWizard();})();
setInterval(()=>{if((page==='info'||page==='models')&&!document.hidden)void refresh();},8000);
setInterval(()=>{if(job.running)renderProgressTime();},1000);
