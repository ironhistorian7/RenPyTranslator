import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdtemp,mkdir,writeFile,readFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {EventEmitter} from 'node:events';
import {PassThrough,Writable} from 'node:stream';
import vm from 'node:vm';
import {build} from 'esbuild';
import {_electron as electron} from 'playwright';

const require=createRequire(import.meta.url),desktop=path.dirname(fileURLToPath(import.meta.url));
const parent=path.join(desktop,'..','build','job-progress-ui');await mkdir(parent,{recursive:true});
const out=await mkdtemp(path.join(parent,'isolated-')),runtime=path.join(out,'runtime');await mkdir(runtime);
const originalError=path.join(runtime,'data','errors','invented-error.json');await mkdir(path.dirname(originalError),{recursive:true});await writeFile(originalError,'{"error_type":"SyntheticError"}');
const handlers=new Map(),helpers=[],workers=[],opened=[];let copied='',supportFails=false,projectFails=false;
const mockApp=Object.assign(new EventEmitter(),{isPackaged:false,setPath(){},setAppLogsPath(){},requestSingleInstanceLock:()=>false,quit(){}});
const execFile=(_exe,args,options,callback)=>{
  const child=new EventEmitter();child.stdin=new Writable({write(chunk,_encoding,done){
    const request=JSON.parse(chunk.toString()),command=args.at(-1);helpers.push({command,request,options});
    let result;
    if(command==='--desktop-project')result={engine:'renpy',engine_label:'RenPy',supported:true,supported_tasks:null,stop_message:null};
    else if(command==='--desktop-models')result={result:null};
    else if(command==='--desktop-support')result=supportFails?{error:'진단용 내보내기 실패',code:'support_unavailable'}:request.op==='copy'?{text:'Synthetic error: invented-error'}:{path:request.destination};
    else throw new Error('Unexpected helper: '+command);
    done();queueMicrotask(()=>projectFails&&command==='--desktop-project'?callback(new Error('Synthetic command failure'),'','ValueError: 합성 폴더 조회 실패\nDetailed error: '+originalError+'\n'):callback(null,JSON.stringify(result),''));
  }});return child;
};
const spawn=(_exe,args,options)=>{
  assert(args.includes('--desktop-run'));const child=new EventEmitter();child.stdout=new PassThrough();child.stderr=new PassThrough();
  child.stdin=new Writable({write(chunk,_encoding,done){child.request=JSON.parse(chunk.toString());done();}});child.options=options;workers.push(child);return child;
};
const fakeElectron={app:mockApp,BrowserWindow:class{},ipcMain:{handle:(id,handler)=>handlers.set(id,handler)},nativeTheme:Object.assign(new EventEmitter(),{shouldUseDarkColors:false}),
  dialog:{showMessageBox:async()=>({response:1}),showSaveDialog:async()=>({canceled:false,filePath:path.join(out,'diagnostic.json')})},
  shell:{openPath:async value=>{opened.push(value);return '';}},clipboard:{writeText:value=>{copied=value;}}};
const main=(await build({entryPoints:[path.join(desktop,'src','main.ts')],bundle:true,write:false,platform:'node',format:'cjs',external:['electron']})).outputFiles[0].text;
const environment={...process.env,RPT_TOOL_ROOT:runtime};delete environment.RPT_DESKTOP_EVENTS;
vm.runInNewContext(main,{require:id=>id==='electron'?fakeElectron:id==='node:child_process'?{execFile,spawn}:require(id),process:{...process,env:environment},console,__dirname:path.join(desktop,'dist'),Buffer,setTimeout,clearTimeout,URL},{filename:'synthetic-main.cjs'});
const event={sender:undefined,senderFrame:{url:pathToFileURL(path.join(desktop,'dist','index.html')).href}};
const invoke=(name,...args)=>handlers.get('rpt:'+name)(event,...args);
const state=async()=>(await invoke('state')).value;
const settings={output:'project',suffix:'-kr',scale:'default',source_language:'english',show_reference:true,language_corner:'right',language_margin:12,theme:'light',gpu_mode:'auto',gpu_ids:[],inference_preset:'auto',inference_overrides:{}};
const request={path:'X:/InventedRenPy',source:true,tasks:['run'],settings};
assert.equal((await invoke('start',request)).ok,true);
assert.equal(workers[0].options.env.RPT_DESKTOP_EVENTS,'1');assert(helpers.every(call=>!call.options.env.RPT_DESKTOP_EVENTS));
const emit=(record,stream='stdout')=>workers.at(-1)[stream].write('RPT_EVENT '+JSON.stringify(record)+'\n');
emit({event:'stage',phase:'translation',label:'번역 중'});
emit({event:'progress',phase:'translation',label:'번역 중',completed:8,total:16,unit:'entries',eta_seconds:120,successful:7,original_kept:1,cached:20});
workers[0].stdout.write('999/999 entries, batch 0.1s\n');
assert.equal((await state()).job.completed,8);assert.equal((await state()).job.total,16);
assert(!(await state()).log.includes('RPT_EVENT'));assert((await state()).log.includes('999/999'));
emit({event:'issue',code:'connection_reset',title:'연결이 끊겼습니다.',state:'retrying',error_id:'invented-error',log_path:originalError});
emit({event:'recovery',state:'waiting',next_retry_at:new Date(Date.now()+30000).toISOString(),delay_seconds:30,attempt:2});
assert.equal((await state()).job.recovery.state,'waiting');assert.equal((await state()).job.eta_seconds,null);
emit({event:'stage',phase:'render',label:'번역 파일 생성 중'});
assert.equal((await state()).job.completed,null);assert.equal((await state()).job.total,null);assert.equal((await state()).job.issue,null);assert.equal((await state()).job.original_kept,1);
emit({event:'notice',code:'rpg_js_candidates',text:'플러그인 안의 번역 후보를 확인했습니다.'});
assert.equal((await state()).job.notices.length,1);
emit({event:'issue',code:'synthetic_error',title:'합성 오류가 발생했습니다.',detail:'실제 게임을 실행하지 않은 검사입니다.',state:'failed',error_id:'invented-error',log_path:originalError},'stderr');
workers[0].emit('close',1);
let current=await state();assert.equal(current.job.issue.error_id,'invented-error');assert(current.job.finished);assert.equal(current.job.running,false);
assert.equal((await invoke('support',{op:'copy'})).ok,true);assert.equal(copied,'Synthetic error: invented-error');
assert.equal(helpers.at(-1).request.log_path,originalError);assert.equal(helpers.at(-1).request.job_context.engine,'renpy');
supportFails=true;assert.equal((await invoke('support',{op:'export'})).ok,false);assert.equal((await state()).job.issue.error_id,'invented-error');
supportFails=false;assert.equal((await invoke('support',{op:'export'})).value.path,path.join(out,'diagnostic.json'));
await invoke('open','logs');assert.equal(opened.at(-1),path.dirname(originalError));
await invoke('reset');assert.equal((await state()).job.issue,undefined);assert.equal((await state()).job.original_kept,undefined);
await invoke('start',request);workers[1].stdout.write('4/8 entries, batch 0.1s\n');assert.equal((await state()).job.completed,4);
workers[1].stdout.write('Rendered 8 entries\n');assert.equal((await state()).job.completed,null);workers[1].emit('close',0);
await invoke('reset');projectFails=true;assert.equal((await invoke('start',request)).ok,false);current=await state();assert.equal(current.job.running,false);assert.equal(current.job.exitCode,null);assert.equal(current.job.issue.log_path,originalError);
console.log('Production main: structured/fallback progress, helper failures, copy/export and exact log folders passed with mocked processes.');

// Renderer: an isolated hidden window with synthetic IPC; no production main,
// helpers, shell, game, local model or network request may execute.
const values={num_ctx:16384,parallel:2,kv_cache_type:'q8_0',context_txt_enabled:true};
const snapshot={root:runtime,settings,tasks:{run:'번역',names:'이름·호칭·입력 보정',font:'폰트·메뉴 보정',failed:'미번역 복구',display:'원문 참고 표시 보정',layout:'대화창·선택지 표시 조정'},fixes:['font','names','failed','display','layout'],defaultModel:'synthetic',runtime:'Synthetic IPC only',gpus:[],models:[],warnings:[],inference:{presets:[{...values,id:'12gb',minimum_total_mib:11264,support:'measured'}],detected_profile:'12gb',total_mib:12288,support:'measured',recommended:values,effective:values,selected_preset:'auto',overrides:{},reduced_only:false,warnings:[],estimated:false}};
const mockMain=path.join(out,'renderer-main.cjs');
await writeFile(mockMain,`
const {app,BrowserWindow,ipcMain}=require('electron'),fs=require('node:fs'),path=require('node:path');
const base=process.env.RPT_TOOL_ROOT;fs.mkdirSync(base,{recursive:true});app.setPath('userData',path.join(base,'renderer-profile'));app.setPath('sessionData',path.join(base,'renderer-session'));app.setAppLogsPath(path.join(base,'renderer-logs'));app.commandLine.appendSwitch('disable-background-networking');
const snapshot=${JSON.stringify(snapshot)},idle={running:false,canceling:false,exitCode:null,result:null,stage:'대기 중',completed:null,total:null,started:null};let job={...idle},win;
globalThis.uiProbe={support:[],opened:[],unexpected:[],network:[]};
const cp=require('node:child_process');for(const op of ['spawn','spawnSync','exec','execSync','execFile','execFileSync','fork'])cp[op]=()=>{globalThis.uiProbe.unexpected.push(op);throw new Error('Child processes forbidden');};
const handle=(name,fn)=>ipcMain.handle('rpt:'+name,async(_event,...args)=>({ok:true,value:await fn(...args)}));
handle('info',()=>snapshot);handle('state',()=>({job,modelJob:{running:false},log:'',dark:false}));handle('appearance',()=>false);handle('save',value=>value);
handle('model',r=>r.op==='search'?[]:{selected:'synthetic',installed:[],pending:[],folder:base});handle('project',()=>({source:true,game_name:'Invented',engine:'renpy',engine_label:'RenPy',supported:true,stop_message:null,supported_tasks:null,source_language:null}));
handle('reset',()=>{job={...idle};return job;});handle('open',which=>{globalThis.uiProbe.opened.push(which);return null;});
handle('support',r=>{globalThis.uiProbe.support.push(r);return r.op==='copy'?{text:'Synthetic error'}:{path:base+'/diagnostic.json'};});
handle('start',()=>job);handle('cancel',()=>job);handle('runtime',()=>[]);handle('browse',()=>null);
globalThis.publishJob=value=>{job={...job,...value};win.webContents.send('rpt:event',{type:'job',job});};
app.whenReady().then(()=>{win=new BrowserWindow({show:false,width:1050,height:800,webPreferences:{preload:${JSON.stringify(path.join(desktop,'dist','preload.js'))},contextIsolation:true,nodeIntegration:false,sandbox:false}});win.webContents.setWindowOpenHandler(()=>({action:'deny'}));win.webContents.session.webRequest.onBeforeRequest({urls:['http://*/*','https://*/*']},(details,callback)=>{globalThis.uiProbe.network.push(details.url);callback({cancel:true});});win.loadFile(${JSON.stringify(path.join(desktop,'dist','index.html'))});});app.on('window-all-closed',()=>app.quit());
`,'utf8');
const env={...process.env,RPT_TOOL_ROOT:path.join(out,'ui-runtime')};delete env.ELECTRON_RUN_AS_NODE;
const application=await electron.launch({executablePath:require('electron'),args:[mockMain],cwd:desktop,env,timeout:30000});
try{
  const page=await application.firstWindow(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.waitForFunction(()=>document.body.dataset.ready==='true');
  await application.evaluate(()=>globalThis.publishJob({running:true,exitCode:null,started:Date.now()-65000,updated:Date.now(),phase:'translation',stage:'번역 중',completed:42,total:100,eta_seconds:100,successful:40,original_kept:2,cached:30}));
  await page.waitForFunction(()=>document.querySelector('#progress-percent').textContent==='42%');
  assert.match(await page.locator('#elapsed-time').textContent(),/1분/);assert.match(await page.locator('#remaining-time').textContent(),/이 단계 남은 시간/);
  assert.match(await page.locator('#job-counts').textContent(),/원문 유지 2개/);assert.equal(await page.locator('#job-details').getAttribute('open'),null);
  await application.evaluate(()=>globalThis.publishJob({phase:'render',stage:'번역 파일 생성 중',completed:null,total:null,eta_seconds:null,updated:Date.now()}));
  await page.waitForFunction(()=>!document.querySelector('#progress').hasAttribute('value'));assert.equal(await page.locator('#progress-percent').textContent(),'');
  await application.evaluate(()=>globalThis.publishJob({stage:'재시도 대기 중',recovery:{state:'waiting',attempt:2,next_retry_at:new Date(Date.now()+25000).toISOString(),delay_seconds:25}}));
  await page.waitForFunction(()=>document.querySelector('#remaining-time').textContent.includes('2번째 재시도'));assert.match(await page.locator('#remaining-time').textContent(),/초 후 재시도/);
  await application.evaluate(()=>globalThis.publishJob({recovery:null,issue:{code:'synthetic',title:'합성 오류가 발생했습니다.',detail:'실제 도구를 실행하지 않은 오류입니다.',state:'failed',error_id:'invented-error',log_path:null}}));
  await page.waitForFunction(()=>!document.querySelector('#issue-panel').hidden);assert.match(await page.locator('#issue-state').textContent(),/정리 중/);
  await page.locator('#copy-issue').click();await page.waitForFunction(()=>document.querySelector('#message').textContent.includes('복사했습니다'));
  await page.locator('#export-issue').click();await page.waitForFunction(()=>document.querySelector('#message').textContent.includes('저장했습니다'));
  await application.evaluate(()=>globalThis.publishJob({running:false,exitCode:1,stage:'작업 실패',finished:Date.now()}));
  await page.waitForFunction(()=>!document.querySelector('#completion-pane').hidden);
  const frozen=await page.locator('#completion-time').textContent();await page.waitForTimeout(1100);assert.equal(await page.locator('#completion-time').textContent(),frozen);
  assert.match(await page.locator('#completion-counts').textContent(),/원문 유지 2개/);
  await page.locator('#issue-logs').click();await page.waitForFunction(()=>document.querySelector('#step-count').textContent==='1 / 4');
  assert.equal(await page.locator('#issue-panel').isVisible(),false);assert.equal(await page.locator('#job-counts').textContent(),'');assert.equal(await page.locator('#remaining-time').textContent(),'');
  const probe=await application.evaluate(()=>globalThis.uiProbe);assert.deepEqual(probe.support,[{op:'copy'},{op:'export'}]);assert.deepEqual(probe.opened,['logs']);assert.deepEqual(probe.unexpected,[]);assert.deepEqual(probe.network,[]);assert.deepEqual(errors,[]);
  console.log('Renderer: phase percent/ETA, retry countdown, error controls, frozen completion time and reset passed with synthetic IPC.');
}finally{await application.close();}
