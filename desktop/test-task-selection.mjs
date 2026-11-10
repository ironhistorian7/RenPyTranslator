import {_electron as electron} from 'playwright';
import {createRequire} from 'node:module';
import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';

const require=createRequire(import.meta.url);
const desktop=path.dirname(fileURLToPath(import.meta.url)),root=path.dirname(desktop);
const parent=path.join(root,'build','task-selection-ui');
await mkdir(parent,{recursive:true});
const out=await mkdtemp(path.join(parent,'isolated-'));
const runtime=path.join(out,'runtime');
await mkdir(runtime,{recursive:true});
const entry=path.join(out,'mock-main.cjs');

// Load only the real renderer and preload. The production main/CLI is never
// required: every IPC reply is synthetic, including start, project and models.
await writeFile(entry,`
const {app,BrowserWindow,ipcMain}=require('electron');
const fs=require('node:fs'),path=require('node:path');
const runtime=process.env.RPT_TOOL_ROOT;
if(!runtime)throw new Error('Isolated RPT_TOOL_ROOT is required');
fs.mkdirSync(path.join(runtime,'profile'),{recursive:true});
app.setPath('userData',path.join(runtime,'profile'));
app.setPath('sessionData',path.join(runtime,'profile','session'));
app.setPath('crashDumps',path.join(runtime,'profile','crashes'));
app.setAppLogsPath(path.join(runtime,'logs'));
app.commandLine.appendSwitch('disable-background-networking');
const idle={running:false,canceling:false,exitCode:null,result:null,stage:'대기 중',completed:null,total:null,started:null};
const modelJob={running:false,stage:'',done:0,total:0,error:null};
const settings={output:'project',suffix:'-kr',scale:'default',source_language:'english',show_reference:true,language_corner:'right',language_margin:12,theme:'light',gpu_mode:'auto',gpu_ids:[],inference_preset:'auto',inference_overrides:{}};
const values={num_ctx:16384,parallel:2,kv_cache_type:'q8_0',context_txt_enabled:true};
const snapshot={root:runtime,settings,appVersion:'0.2.1',runtimes:[{id:'ollama-0.34.2',name:'Ollama 0.34.2',default:true,installed:true},{id:'ollama-0.40.15',name:'Ollama 0.40.15',installed:true}],tasks:{run:'번역',retranslate:'재번역',update:'게임 업데이트',check:'패치 검사',font:'폰트·메뉴 보정',names:'이름·호칭·입력 보정',failed:'미번역 복구',display:'원문 참고 표시 보정',layout:'대화창·선택지 표시 조정',answers:'입력 정답 표시',routes:'선택지 힌트',language:'언어 전환 패널'},fixes:['font','names','failed','display','layout'],defaultModel:'synthetic-hy',runtime:'Synthetic IPC only',gpus:[{id:'GPU-SYNTHETIC',name:'Synthetic 12GB GPU',total:12288,used:0,utilization:0,driver:'fixture',selectable:true,active:false}],models:[],warnings:[],inference:{presets:[{...values,id:'12gb',minimum_total_mib:11264,support:'measured'}],detected_profile:'12gb',total_mib:12288,support:'measured',recommended:values,effective:values,selected_preset:'auto',overrides:{},reduced_only:false,warnings:[],estimated:false}};
const library={selected:'synthetic-hy',installed:[{id:'synthetic-hy',title:'Synthetic installed model',size:6000000000,ready:true,quantization:'Q6_K',estimatedMemory:8000000000}],pending:[],folder:path.join(runtime,'models')};
const probe=globalThis.taskSelectionProbe={requests:[],runtimeQueries:[],projectQueries:[],unexpected:[],blockedNetwork:[]};
const childProcess=require('node:child_process');
for(const operation of ['spawn','spawnSync','exec','execSync','execFile','execFileSync','fork'])childProcess[operation]=(...args)=>{probe.unexpected.push({operation,args});throw new Error('No child process is permitted in this UI test');};
const handle=(name,fn)=>ipcMain.handle('rpt:'+name,async(_event,...args)=>({ok:true,value:await fn(...args)}));
handle('info',()=>snapshot);
handle('state',()=>({job:idle,modelJob,log:'',dark:false}));
handle('appearance',theme=>theme==='dark');
handle('save',value=>value);
handle('browse',()=>null);
handle('open',()=>null);
handle('reset',()=>idle);
handle('model',request=>{if(request.op==='inventory')return library;if(request.op==='search')return [];if(request.op==='select'){library.selected=request.model;return library;}probe.unexpected.push({operation:'model',request});throw new Error('Unexpected model operation');});
handle('modelWork',request=>{probe.unexpected.push({operation:'modelWork',request});throw new Error('Model work is not permitted');});
handle('modelCancel',()=>modelJob);
handle('runtime',request=>{probe.runtimeQueries.push(request);return [{id:'ollama-0.40.15',name:'Ollama 0.40.15',installed:true,available:true}];});
handle('runtimeWork',request=>{probe.unexpected.push({operation:'runtimeWork',request});throw new Error('No actual download is permitted');});
handle('project',request=>{if(!request.path.startsWith('X:/'))throw new Error('Only invented X:/ paths are permitted');probe.projectQueries.push(request);const rpg=request.path.includes('RPG');return {source:!request.path.endsWith('-project'),game_name:rpg?'InventedRPG':'InventedRenPy',engine:rpg?'rpgmaker':'renpy',engine_label:rpg?'RPG Maker MZ':'Ren’Py',supported:true,supported_tasks:rpg?['run','retranslate','update','check','font','names','failed','display','layout']:null,source_language:null};});
handle('start',request=>{if(!request.path.startsWith('X:/'))throw new Error('Only synthetic paths are permitted');probe.requests.push(JSON.parse(JSON.stringify(request)));return idle;});
handle('cancel',()=>idle);
app.whenReady().then(()=>{const window=new BrowserWindow({show:false,width:1180,height:900,webPreferences:{preload:${JSON.stringify(path.join(desktop,'dist','preload.js'))},contextIsolation:true,nodeIntegration:false,sandbox:false}});window.webContents.setWindowOpenHandler(()=>({action:'deny'}));window.webContents.session.webRequest.onBeforeRequest({urls:['http://*/*','https://*/*']},(details,callback)=>{probe.blockedNetwork.push(details.url);callback({cancel:true});});window.loadFile(${JSON.stringify(path.join(desktop,'dist','index.html'))});});
app.on('window-all-closed',()=>app.quit());
`, 'utf8');

const env={...process.env,RPT_TOOL_ROOT:runtime};
delete env.ELECTRON_RUN_AS_NODE;
const app=await electron.launch({executablePath:require('electron'),args:[entry],cwd:desktop,env,timeout:30000});
const completed=[],errors=[];
completed.push=(...items)=>{console.log(items.join('\n'));return Array.prototype.push.apply(completed,items);};
try{
  const page=await app.firstWindow({timeout:15000});
  page.setDefaultTimeout(10000);
  page.on('pageerror',error=>errors.push(error.message));
  const ready=async()=>{await page.waitForFunction(()=>document.body.dataset.ready==='true');await page.waitForFunction(()=>document.querySelector('#wizard-model').value==='synthetic-hy');};
  const fresh=async()=>{await page.reload();await ready();};
  const choose=async folder=>{
    await page.locator('#source-path').fill(folder);
    await page.locator('#source-path').press('Tab');
    await page.waitForFunction(({rpg})=>document.querySelector('#engine-note').textContent.includes(rpg?'RPG Maker MZ':'Ren’Py'),{rpg:folder.includes('RPG')});
    await page.waitForFunction(()=>!document.querySelector('#source-path').disabled);
  };
  const openOther=async()=>{await page.locator('#task-options').evaluate(details=>{details.open=true;});};
  const openRepairs=async()=>{await openOther();await page.locator('#repairs').evaluate(details=>{details.open=true;});};
  const checked=task=>page.locator('[data-task='+task+']').isChecked();
  const enabled=task=>page.locator('[data-task='+task+']').isEnabled();
  const capture=async()=>{
    const before=await app.evaluate(()=>globalThis.taskSelectionProbe.requests.length);
    for(let index=0;index<3;index++)await page.locator('#wizard-next').click();
    await page.locator('#start').click();
    await page.waitForFunction(()=>!document.querySelector('#start').disabled);
    const requests=await app.evaluate(()=>globalThis.taskSelectionProbe.requests);
    assert.equal(requests.length,before+1,'Exactly one mock start request is sent');
    return requests.at(-1);
  };
  const backToGame=async()=>{for(let index=0;index<3;index++)await page.locator('#back').click();};
  await ready();
  await choose('X:/InventedRenPy-source');
  assert.equal(await page.locator('#task-options').getAttribute('open'),null);
  assert.equal(await page.locator('#basic-tasks [data-task=run]').isVisible(),true);
  assert.equal(await checked('run'),true);
  assert.equal(await page.locator('#language-default-note').isVisible(),true);
  assert.match(await page.locator('#language-default-note').innerText(),/언어 전환 패널.*포함/);
  assert.equal(await page.locator('#tasks [data-task=run]').count(),0);
  assert.equal(await page.locator('[data-task=language]').count(),0,'The GUI does not expose a standalone language-panel checkbox');
  completed.push('Basic translation and included RenPy language-panel note remain visible with other settings collapsed');

  await openOther();
  for(const task of ['retranslate','answers','routes']){
    assert.equal(await page.locator('#tasks [data-task='+task+']').isVisible(),true,task);
    assert.equal(await enabled(task),true,task+' is available for an original source folder');
  }
  completed.push('Original RenPy source enables retranslation, input answers and choice hints in other settings');
  await page.locator('[data-task=retranslate]').check();
  assert.equal(await checked('run'),false);
  assert.equal(await page.locator('#language-default-note').isVisible(),true);
  const retranslate=await capture();
  assert.equal(retranslate.source,true);
  assert.equal(retranslate.path,'X:/InventedRenPy-source');
  assert.deepEqual(retranslate.tasks,['retranslate']);
  await backToGame();
  await page.locator('[data-task=answers]').check();
  await page.locator('[data-task=routes]').check();
  const withHints=await capture();
  assert.equal(withHints.source,true);
  assert.deepEqual(withHints.tasks,['retranslate','answers','routes']);
  assert.equal(withHints.tasks.includes('run'),false);
  completed.push('Original RenPy source accepts retranslation alone or with answers/routes, without default-run fallback');

  const fixes=['font','names','failed','display','layout'];
  for(const task of fixes){
    await fresh();await choose('X:/InventedRenPy-'+task+'-source');
    await openRepairs();
    assert.equal(await page.locator('[data-task=language]').count(),0);
    for(const fix of fixes)assert.equal(await enabled(fix),true,fix+' is enabled for a RenPy source folder');
    await page.locator('[data-task=retranslate]').check();
    await page.locator('[data-task='+task+']').check();
    assert.equal(await checked('run'),false);
    assert.equal(await checked('retranslate'),false);
    assert.equal(await page.locator('#language-default-note').isVisible(),false);
    // Changing to another original folder must not discard its chosen repair.
    await choose('X:/InventedRenPy-'+task+'-second-source');
    assert.equal(await checked(task),true);
    const repair=await capture();
    assert.equal(repair.source,true);
    assert.deepEqual(repair.tasks,[task]);
    assert.equal(repair.tasks.includes('language'),false);
  }
  completed.push('All five existing RenPy fixes remain enabled on source folders, survive source changes and submit independently without run/retranslate');

  await fresh();await choose('X:/InventedRenPy-switch-source');
  await openRepairs();
  for(const task of [...fixes,'answers','routes'])await page.locator('[data-task='+task+']').check();
  await choose('X:/InventedRPG-source');
  for(const task of ['answers','routes']){
    assert.equal(await page.locator('[data-task='+task+']').isVisible(),false,task+' is hidden for RPG Maker');
    assert.equal(await checked(task),false,task+' is removed when switching engines');
  }
  assert.equal(await page.locator('[data-task=language]').count(),0);
  assert.equal(await page.locator('#language-default-note').isVisible(),false);
  const sharedFixes=['font','names','failed','display','layout'];
  for(const task of sharedFixes){
    assert.equal(await enabled(task),true,task+' remains enabled for an RPG Maker source');
    assert.equal(await checked(task),true,task+' remains selected after switching engines');
  }
  assert.equal(await page.locator('[data-task-label=names]').first().innerText(),'인명 표기 보정');
  const rpg=await capture();
  assert.equal(rpg.source,true);
  assert.deepEqual(rpg.tasks,sharedFixes);
  assert.equal(rpg.tasks.some(task=>['run','retranslate','answers','routes','language'].includes(task)),false);
  completed.push('Switching to RPG Maker removes unsupported hints and retains five supported source-folder fixes including name spelling');

  await fresh();await choose('X:/InventedRPG-default-source');
  assert.equal(await checked('run'),true);
  assert.equal(await page.locator('#language-default-note').isVisible(),false);
  await choose('X:/InventedRenPy-return-source');
  assert.equal(await page.locator('#task-options').getAttribute('open'),null);
  assert.equal(await checked('run'),true);
  assert.equal(await page.locator('#basic-tasks [data-task=run]').isVisible(),true);
  assert.equal(await page.locator('#language-default-note').isVisible(),true);
  assert.equal(await page.locator('[data-task-label=names]').first().textContent(),'이름·호칭·입력 보정');
  completed.push('Returning from RPG Maker to RenPy restores its name-correction label and included-panel note');


  await fresh();await choose('X:/InventedRenPy-old-project');await openOther();
  assert.equal(await page.locator('#app-version').innerText(),'v0.2.1');
  for(const task of ['answers','routes']){
    const badge=page.locator('[data-task='+task+']').locator('..').locator('sup.beta-badge');
    assert.equal(await badge.innerText(),'beta');
    assert.equal(await badge.evaluate(node=>getComputedStyle(node).verticalAlign),'super');
  }
  await page.locator('[data-task=update]').check();
  assert.equal(await checked('run'),false);
  assert.equal(await page.locator('#update-fields').isVisible(),true);
  assert.equal(await page.locator('#wizard-next').isEnabled(),false);
  await page.locator('#update-source').fill('X:/InventedRenPy-new-source');
  const update=await capture();
  assert.equal(update.source,false);assert.deepEqual(update.tasks,['update']);
  assert.equal(update.path,'X:/InventedRenPy-old-project');
  assert.equal(update.update_source,'X:/InventedRenPy-new-source');
  completed.push('Game update requires the old project plus a separate new source and submits update only');
  await fresh();await choose('X:/InventedRenPy-check-project');await openOther();
  await page.locator('[data-task=run]').uncheck();await page.locator('[data-task=check]').check();
  const check=await capture();assert.deepEqual(check.tasks,['check']);
  completed.push('Patch check submits independently without translation');
  await page.locator('[data-page=info]').click();
  await page.locator('#runtime-select').selectOption('ollama-0.40.15');
  assert.equal(await page.locator('#save').isEnabled(),true);
  await page.locator('#save').click();
  await page.locator('#runtime-releases').evaluate(button=>button.closest('details').open=true);
  await page.locator('#runtime-releases').click();
  await page.waitForFunction(()=>document.querySelector('#runtime-release').options.length===2);
  await page.locator('#runtime-release').selectOption('ollama-0.40.15');
  assert.equal(await page.locator('#runtime-download').innerText(),'설치됨');
  assert.equal(await page.locator('#runtime-download').isEnabled(),false);
  assert.equal(await page.locator('body').innerText().then(text=>text.includes('전체 줄거리 사전 분석은 자동 실행하지 않습니다.')),false);
  completed.push('Runtime selection is editable and installed releases cannot be downloaded again');

  assert.deepEqual(errors,[]);
  assert.equal(await page.evaluate(()=>typeof window.require),'undefined');
  assert.equal(await page.evaluate(()=>typeof window.process),'undefined');
  const probe=await app.evaluate(()=>globalThis.taskSelectionProbe);
  assert.deepEqual(probe.unexpected,[]);
  assert.deepEqual(probe.blockedNetwork,[]);
  const visible=await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows().some(window=>window.isVisible()));
  assert.equal(visible,false,'The synthetic Electron window stays hidden');
  const report={passed:true,checks:completed,requestCount:probe.requests.length,
    requests:probe.requests,projectQueries:probe.projectQueries,
    realMainLoaded:false,workerStarted:false,gameOrModelExecuted:false,
    sourcePathsInvented:true,rendererErrors:errors,isolatedToolRoot:runtime};
  await writeFile(path.join(out,'report.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify({passed:true,checks:completed,requestCount:probe.requests.length,report:path.join(out,'report.json')},null,2));
}catch(error){console.error(error);throw error;}finally{
  await app.close();
}
