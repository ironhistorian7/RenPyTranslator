import {app,BrowserWindow,dialog,ipcMain,nativeTheme,shell,clipboard} from 'electron';
import {spawn,execFile,ChildProcessWithoutNullStreams} from 'node:child_process';
import {existsSync,mkdirSync,readFileSync,writeFileSync,unlinkSync,appendFileSync} from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {helperError} from './helper-errors';
import {jobEvent} from './job-events';
import type {Settings,Snapshot,Request,Job,JobIssue,Event,ModelJob,SupportResult} from './types';

const ROOT=process.env.RPT_TOOL_ROOT || (app.isPackaged?path.dirname(path.dirname(process.execPath)):path.resolve(__dirname,'../..'));
const profile=path.join(ROOT,'data','desktop-profile');
mkdirSync(profile,{recursive:true});
app.setPath('userData',profile);
app.setPath('sessionData',path.join(profile,'session'));
app.setPath('crashDumps',path.join(profile,'crashes'));
app.setAppLogsPath(path.join(ROOT,'logs','desktop'));
app.name='RenPyTranslator';
let window:BrowserWindow|null=null;
let worker:ChildProcessWithoutNullStreams|null=null;
let cancelFile:string|null=null;
let pendingClose=false;
let log='';
let jobLog:string|null=null;
let modelWorker:ChildProcessWithoutNullStreams|null=null,modelCancelFile:string|null=null;
let modelJob:ModelJob={running:false,stage:'',done:0,total:0,error:null};
let job:Job={running:false,canceling:false,exitCode:null,result:null,stage:'대기 중',completed:null,total:null,started:null};
let jobContext:Record<string,unknown>={};
const indexURL=pathToFileURL(path.join(__dirname,'index.html')).href;
const cli=():[string,string[]]=>{
  const python=path.join(ROOT,'.venv','Scripts','python.exe');
  if(!app.isPackaged && existsSync(python)) return [python,['-B','-X','utf8',path.join(ROOT,'app','portable_cli.py')]];
  return [path.join(ROOT,'RenPyTranslator-cli.exe'),[]];
};
function emit(event:Event){if(window&&!window.isDestroyed())window.webContents.send('rpt:event',event);}
function updateJob(values:Partial<Job>){job={...job,...values};emit({type:'job',job});}
function addLog(text:string,visible=true){
  if(visible){log=(log+text).slice(-500000);emit({type:'log',text});}
  if(jobLog){try{appendFileSync(jobLog,text,'utf8');}catch{jobLog=null;}}
}
function requestIssue(error:unknown,operation:string):JobIssue{
  const message=error instanceof Error?error.message:String(error);
  const original=message.match(/(?:^|\n)로그: (.+)(?:\n|$)/)?.[1];
  const first=message.split(/\r?\n/)[0].replace(/^[\w.]+(?:Error|Exception):\s*/, '');
  const title=/[가-힣]/.test(first)&&first.length<200?first:operation==='project'?'폴더 정보를 확인하지 못했습니다.':operation==='start'?'작업을 시작하지 못했습니다.':'프로그램 정보를 불러오지 못했습니다.';
  return {code:'desktop_request',title,detail:title===first?'':first.slice(0,240),state:worker?'warning':'failed',
    error_id:original?path.basename(original,path.extname(original)):null,log_path:original||null};
}
function helper<T>(command:string,payload?:unknown):Promise<T>{
  return new Promise((resolve,reject)=>{
    const [exe,args]=cli();
    const child=execFile(exe,[...args,command],{cwd:ROOT,windowsHide:true,encoding:'utf8',env:{...process.env,RPT_REQUEST_ENCODING:'utf-8'},timeout:45000,maxBuffer:2*1024*1024},(err,stdout,stderr)=>{
      if(err){reject(helperError(ROOT,command,err,stderr));return;}
      try{resolve(JSON.parse(stdout));}catch{reject(helperError(ROOT,command,new Error('프로그램 상태 응답을 읽지 못했습니다.'),stderr));}
    });
    child.stdin?.on('error',()=>{});
    if(payload!==undefined)child.stdin?.end(JSON.stringify(payload),'utf8');
  });
}
let infoPromise:Promise<Snapshot>|null=null;
function info(){if(!infoPromise)infoPromise=helper<Snapshot>('--desktop-info').finally(()=>{infoPromise=null;});return infoPromise;}
function setAppearance(value:unknown){
  if(value!=='system'&&value!=='light'&&value!=='dark')throw new Error('화면 테마를 확인하세요.');
  nativeTheme.themeSource=value;
  updateWindowTheme();
  return nativeTheme.shouldUseDarkColors;
}
function updateWindowTheme(){
  const dark=nativeTheme.shouldUseDarkColors;
  if(window&&!window.isDestroyed()){
    window.setBackgroundColor(dark?'#202124':'#fafafa');
    if(process.platform==='win32')window.setTitleBarOverlay({color:dark?'#25262a':'#f1f3f6',symbolColor:dark?'#f2f2f7':'#25262a',height:40});
    emit({type:'theme',dark});
  }
}
function validateRequest(r:Request){
  if(!r||typeof r.path!=='string'||!r.path.trim())throw new Error('게임 또는 기존 프로젝트 폴더를 선택하세요.');
  if(!Array.isArray(r.tasks)||!r.tasks.length)throw new Error('실행할 작업을 하나 이상 선택하세요.');
  if(r.path.length>4096||r.tasks.some(t=>typeof t!=='string'))throw new Error('작업 설정을 확인하세요.');
  if(typeof r.source!=='boolean'||!r.settings)throw new Error('작업 설정을 확인하세요.');
  if(r.tasks.includes('update')&&(r.source||typeof r.update_source!=='string'||!r.update_source.trim()||r.update_source.length>4096))throw new Error('기존 프로젝트와 업데이트된 게임 폴더를 선택하세요.');
}
async function start(r:Request){
  if(worker||modelWorker)throw new Error('현재 작업이 끝난 뒤 실행하세요.');
  validateRequest(r);
  jobContext={tasks:[...r.tasks],source_language:r.settings.source_language,runtime_id:r.settings.runtime_id||'',inference_preset:r.settings.inference_preset};
  const format=await helper<import('./types').GameFormat>('--desktop-project',{path:r.path,source:r.source});
  if(r.tasks.includes('update')){
    const updated=await helper<import('./types').GameFormat>('--desktop-project',{path:r.update_source,source:true});
    if(!updated.supported||updated.engine!==format.engine)throw new Error('기존 프로젝트와 같은 엔진의 게임 폴더를 선택하세요.');
  }
  const unavailable=format.supported_tasks?r.tasks.filter(t=>!format.supported_tasks!.includes(t)):[];
  if(format.stop_message||unavailable.length){
    await dialog.showMessageBox(window!,{type:'info',title:'게임 지원 안내',message:format.stop_message||'이 게임에서 아직 지원하지 않는 작업이 선택되었습니다.',detail:unavailable.length?'RPG Maker는 번역·재번역·인명 표기·폰트·원문 표시·높이 조정·미번역 복구·정적 패치 검사를 지원합니다.':'',buttons:['확인']});
    return null;
  }
  const check=await helper<{result:null|{title:string;detail:string}}>('--desktop-models',{op:'preflight',tasks:r.tasks,engine:format.engine});
  if(check.result){
    const answer=await dialog.showMessageBox(window!,{type:'warning',title:check.result.title,message:check.result.title,detail:check.result.detail,buttons:['AI 모델로 이동','닫기'],defaultId:0,cancelId:1});
    if(answer.response===0)emit({type:'navigate',page:'models'});
    return null;
  }
  if(worker||modelWorker)throw new Error('현재 작업이 끝난 뒤 실행하세요.');
  const logs=path.join(ROOT,'logs');mkdirSync(logs,{recursive:true});
  jobLog=path.join(logs,`desktop-task-${new Date().toISOString().replace(/[:.]/g,'-')}.log`);
  const control=path.join(ROOT,'data','control');mkdirSync(control,{recursive:true});
  cancelFile=path.join(control,`desktop-${process.pid}-${Date.now()}.cancel`);
  const [exe,args]=cli();
  const proc=spawn(exe,[...args,'--desktop-run'],{cwd:ROOT,windowsHide:true,stdio:'pipe',
    env:{...process.env,RPT_CANCEL_FILE:cancelFile,RPT_REQUEST_ENCODING:'utf-8',RPT_DESKTOP_EVENTS:'1',PYTHONUTF8:'1',PYTHONUNBUFFERED:'1'}});
  worker=proc;
  jobContext={tasks:[...r.tasks],engine:format.engine,source_language:r.settings.source_language,runtime_id:r.settings.runtime_id||'',inference_preset:r.settings.inference_preset};
  job={running:true,canceling:false,exitCode:null,result:null,stage:'작업 준비 중',completed:null,total:null,started:Date.now(),issue:null,recovery:null};
  updateJob({});
  addLog('\n작업을 시작합니다.\n');
  let tail='',errorTail='',structured=false,lastErrorPath:string|null=null;
  function parseLine(line:string){
    if(line.startsWith('RPT_EVENT ')){
      addLog(line+'\n',false);
      try{
        const event=JSON.parse(line.slice('RPT_EVENT '.length));
        if(event.event==='stage'||event.event==='progress')structured=true;
        const change=jobEvent(job,event);if(change)updateJob(change);
      }catch{/* Keep malformed control records in the disk log only. */}
      return;
    }
    addLog(line+'\n');
    if(line.startsWith('Output folder: '))updateJob({result:line.slice(15).trim()});
    if(line.startsWith('Detailed error: '))lastErrorPath=line.slice(16).trim();
    if(structured)return;
    const phase=(stage:string)=>updateJob({stage,phase:stage,phase_label:stage,completed:null,total:null,unit:null,eta_seconds:null,recovery:null});
    const progress=line.match(/^(\d+)\/(\d+) entries, batch/);
    if(progress)updateJob({stage:'번역 중',phase:'translation',completed:Number(progress[1]),total:Number(progress[2]),unit:'entries',updated:Date.now()});
    else if(line.startsWith('Translating '))phase('번역 중');
    else if(line.startsWith('Rendered '))phase('번역 파일 생성 중');
    else if(line.startsWith('Copying '))phase('작업 파일 준비 중');
    else if(line.startsWith('Failed repair:'))phase('미번역 항목 복구 중');
    else if(line.startsWith('Name '))phase('인명 표기 보정 중');
    else if(line.startsWith('In-game hints:'))phase('정답·선택지 힌트 처리 중');
    else if(line.startsWith('Patch check:'))phase('패치 검사 중');
    else if(line.startsWith('Game update:'))phase('업데이트 비교 중');
  }
  proc.stdout.setEncoding('utf8');proc.stderr.setEncoding('utf8');
  proc.stdout.on('data',(text:string)=>{tail+=text;const lines=tail.split(/\r?\n/);tail=lines.pop()||'';for(const line of lines)parseLine(line);});
  proc.stderr.on('data',(text:string)=>{errorTail+=text;const lines=errorTail.split(/\r?\n/);errorTail=lines.pop()||'';for(const line of lines)parseLine(line);});
  proc.stdin.on('error',()=>{});
  proc.stdin.end(JSON.stringify(r),'utf8');
  let finished=false;
  function finish(code:number){
    if(finished)return;finished=true;
    if(tail)parseLine(tail);
    if(errorTail)parseLine(errorTail);
    worker=null;
    const cancelled=code===130;
    const original=lastErrorPath||jobLog;
    const issue=code!==0&&!cancelled&&job.issue?.state!=='failed'?{code:'worker_failed',title:'작업을 완료하지 못했습니다.',detail:'오류 기록에서 자세한 내용을 확인할 수 있습니다.',state:'failed' as const,error_id:original?path.basename(original,path.extname(original)):null,log_path:original}:job.issue;
    updateJob({running:false,canceling:false,exitCode:code,stage:code===0?'완료':cancelled?'취소됨':'작업 실패',finished:Date.now(),recovery:null,eta_seconds:null,issue});
    addLog(code===0?'작업을 마쳤습니다.\n':cancelled?'취소했습니다. 저장된 결과는 유지됩니다.\n':'작업에 실패했습니다. 위 기록을 확인하세요.\n');
    jobLog=null;
    if(cancelFile){try{unlinkSync(cancelFile);}catch{}cancelFile=null;}
    if(pendingClose)app.quit();
  }
  proc.once('error',err=>{addLog(err.message+'\n');updateJob({issue:requestIssue(err,'start')});finish(1);});
  proc.once('close',code=>finish(code??1));
  return job;
}
function cancel(){
  if(worker&&cancelFile&&!job.canceling){writeFileSync(cancelFile,'');updateJob({canceling:true,stage:'취소 및 모델 정리 중',completed:null,total:null,eta_seconds:null,recovery:null});addLog('취소를 요청했습니다. 현재 작업과 모델을 정리합니다.\n');}
  return job;
}
function route(name:string,fn:(...args:any[])=>unknown){
  ipcMain.handle('rpt:'+name,async(event,...args)=>{
    if(event.sender!==window?.webContents||event.senderFrame?.url!==indexURL)return {ok:false,error:'허용되지 않은 요청입니다.'};
    try{return {ok:true,value:await fn(...args)};}catch(error){
      if(['start','project','info'].includes(name)){
        if(!worker&&name!=='info')job={running:false,canceling:false,exitCode:null,result:null,stage:'대기 중',completed:null,total:null,started:null};
        updateJob({issue:requestIssue(error,name)});
        addLog((error instanceof Error?error.message:String(error))+'\n');
      }
      return {ok:false,error:error instanceof Error?error.message:String(error)};
    }
  });
}
route('info',info);
route('project',(r:{path:string;source?:boolean})=>helper('--desktop-project',r));
route('state',()=>({job,log,modelJob,dark:nativeTheme.shouldUseDarkColors}));
route('appearance',setAppearance);
route('browse',async()=>{const r=await dialog.showOpenDialog(window!,{title:'폴더 선택',properties:['openDirectory']});return r.canceled?null:r.filePaths[0];});
route('save',async(s:Settings)=>{if(worker||modelWorker)throw new Error('작업이 끝난 뒤 설정을 저장하세요.');const saved=await helper<Settings>('--desktop-settings',s);setAppearance(saved.theme);return saved;});
route('start',start);
route('cancel',cancel);
route('reset',()=>{
  if(worker||modelWorker)throw new Error('작업이 끝난 뒤 초기화할 수 있습니다.');
  log='';jobLog=null;jobContext={};
  job={running:false,canceling:false,exitCode:null,result:null,stage:'대기 중',completed:null,total:null,started:null};updateJob({});
  return job;
});
route('open',async(which:unknown)=>{
  const errorFolder=job.issue?.log_path?path.dirname(job.issue.log_path):null;
  const target=which==='result'?job.result:which==='logs'?(errorFolder&&existsSync(errorFolder)?errorFolder:path.join(ROOT,'logs')):which==='models'?path.join(ROOT,'models'):null;
  if(which==='models'&&target)mkdirSync(target,{recursive:true});
  if(!target)throw new Error('열 수 있는 결과 폴더가 아직 없습니다.');
  if(!existsSync(target))throw new Error('폴더가 아직 생성되지 않았습니다.');
  const err=await shell.openPath(target);if(err)throw new Error(err);
});
route('support',async(request:{op?:unknown})=>{
  if(!request||!['copy','export'].includes(String(request.op)))throw new Error('진단 작업을 확인하세요.');
  const payload:Record<string,unknown>={op:request.op,log_path:job.issue?.log_path||null,
    job_context:{...jobContext,stage:job.stage,phase:job.phase,issue:job.issue,state:job.issue?.state,
      counts:{completed:job.completed,total:job.total,exit_code:job.exitCode,successful:job.successful,original_kept:job.original_kept,cached:job.cached}}};
  if(request.op==='export'){
    const folder=path.join(ROOT,'logs','reports');mkdirSync(folder,{recursive:true});
    const selected=await dialog.showSaveDialog(window!,{title:'진단 파일 저장',defaultPath:path.join(folder,`RenPyTranslator-diagnostic-${new Date().toISOString().replace(/[:.]/g,'-')}.json`),filters:[{name:'진단 파일',extensions:['json']}]});
    if(selected.canceled||!selected.filePath)return {cancelled:true};
    payload.destination=selected.filePath;
  }
  const result=await helper<SupportResult>('--desktop-support',payload);
  if(result.error)throw new Error(result.error);
  if(request.op==='copy'){
    if(typeof result.text!=='string')throw new Error('복사할 오류 내용을 만들지 못했습니다.');
    clipboard.writeText(result.text);
  }
  return result;
});
route('model',async(r:Record<string,unknown>)=>{
  if(!r||!['inventory','search','details','select','delete'].includes(String(r.op)))throw new Error('모델 작업을 확인하세요.');
  if(['select','delete'].includes(String(r.op))){
    if(worker||modelWorker)throw new Error('작업이 끝난 뒤 모델을 변경하세요.');
    if(r.op==='delete'){
      const answer=await dialog.showMessageBox(window!,{type:'question',title:'모델 삭제',message:'선택한 모델을 삭제할까요?',detail:String(r.model),buttons:['취소','삭제'],defaultId:0,cancelId:0});
      if(answer.response!==1)return (await helper<{result:unknown}>('--desktop-models',{op:'inventory'})).result;
    }
  }
  return (await helper<{result:unknown}>('--desktop-models',r)).result;
});
route('runtime',async(r:Record<string,unknown>)=>{
  if(!r||!['inventory','releases'].includes(String(r.op)))throw new Error('실행 환경 작업을 확인하세요.');
  return (await helper<{result:unknown}>('--desktop-runtimes',r)).result;
});
function updateModel(values:Partial<ModelJob>){modelJob={...modelJob,...values};emit({type:'modelJob',job:modelJob});}
function cancelModel(){if(modelWorker&&modelCancelFile){writeFileSync(modelCancelFile,'');updateModel({stage:'취소 중…'});}}
route('modelCancel',cancelModel);
function startAssetWork(r:Record<string,unknown>,kind:'model'|'runtime'){
  if(worker||modelWorker)throw new Error('현재 작업이 끝난 뒤 실행하세요.');
  if(!r||!(kind==='runtime'?['install']:['download','refresh']).includes(String(r.op)))throw new Error('다운로드 작업을 확인하세요.');
  if(kind==='runtime'&&(typeof r.runtime_id!=='string'||r.runtime_id.length>100))throw new Error('실행 환경 버전을 선택하세요.');
  const control=path.join(ROOT,'data','control');mkdirSync(control,{recursive:true});
  modelCancelFile=path.join(control,`model-${process.pid}-${Date.now()}.cancel`);
  const [exe,args]=cli();
  const proc=spawn(exe,[...args,kind==='runtime'?'--desktop-runtimes':'--desktop-models'],{cwd:ROOT,windowsHide:true,stdio:'pipe',env:{...process.env,RPT_CANCEL_FILE:modelCancelFile,RPT_REQUEST_ENCODING:'utf-8',PYTHONUTF8:'1'}});
  modelWorker=proc;updateModel({kind,running:true,stage:kind==='runtime'||r.op==='download'?'다운로드 준비 중':'모델 확인 중',done:0,total:0,error:null});
  let tail='',errors='';proc.stdout.setEncoding('utf8');proc.stderr.setEncoding('utf8');
  proc.stdout.on('data',(s:string)=>{tail+=s;const lines=tail.split(/\r?\n/);tail=lines.pop()||'';for(const line of lines){try{const item=JSON.parse(line);if(item.stage)updateModel({stage:item.stage,done:item.done||0,total:item.total||0});}catch{}}});
  proc.stderr.on('data',(s:string)=>{errors=(errors+s).slice(-4000);});
  proc.stdin.on('error',()=>{});proc.stdin.end(JSON.stringify(r),'utf8');
  let finished=false;
  const finish=(code:number)=>{if(finished)return;finished=true;modelWorker=null;
    updateModel({running:false,stage:code===0?'완료':code===130?'취소됨':'다운로드 실패',error:code!==0&&code!==130?errors.trim()||'작업을 완료하지 못했습니다.':null});
    if(modelCancelFile){try{unlinkSync(modelCancelFile);}catch{}modelCancelFile=null;}if(pendingClose)app.quit();};
  proc.once('error',e=>{errors=e.message;finish(1);});proc.once('close',code=>finish(code??1));return modelJob;
}
route('modelWork',(r:Record<string,unknown>)=>startAssetWork(r,'model'));
route('runtimeWork',(r:Record<string,unknown>)=>startAssetWork(r,'runtime'));
if(!app.requestSingleInstanceLock()){app.quit();}
else{
  app.on('second-instance',()=>{if(window){if(window.isMinimized())window.restore();window.show();window.focus();}});
  app.whenReady().then(()=>{
    try{setAppearance(JSON.parse(readFileSync(path.join(ROOT,'data','gui-settings.json'),'utf8')).theme||'system');}catch{}
    window=new BrowserWindow({width:1180,height:900,minWidth:920,minHeight:720,title:'RenPyTranslator',show:false,
      autoHideMenuBar:true,titleBarStyle:'hidden',titleBarOverlay:{height:40,color:nativeTheme.shouldUseDarkColors?'#25262a':'#f1f3f6',symbolColor:nativeTheme.shouldUseDarkColors?'#f2f2f7':'#25262a'},
      backgroundColor:nativeTheme.shouldUseDarkColors?'#202124':'#fafafa',
      webPreferences:{preload:path.join(__dirname,'preload.js'),contextIsolation:true,nodeIntegration:false,sandbox:true,webSecurity:true,spellcheck:false}});
    window.setMenu(null);
    window.webContents.setWindowOpenHandler(()=>({action:'deny'}));
    window.webContents.on('will-navigate',event=>event.preventDefault());
    window.webContents.session.setPermissionRequestHandler((_w,_p,cb)=>cb(false));
    window.once('ready-to-show',()=>{window?.show();updateWindowTheme();});
    window.on('close',async event=>{
      if(!worker&&!modelWorker)return;
      event.preventDefault();
      if(pendingClose)return;
      const answer=await dialog.showMessageBox(window!,{type:'question',title:'작업이 진행 중입니다',message:'작업을 취소하고 닫을까요?',detail:'저장된 번역은 유지되며, 사용 중인 모델을 정리한 뒤 닫습니다.',buttons:['계속 작업','취소하고 닫기'],defaultId:0,cancelId:0});
      if(answer.response===1){pendingClose=true;cancel();cancelModel();}
    });
    nativeTheme.on('updated',updateWindowTheme);
    void window.loadFile(path.join(__dirname,'index.html'));
  });
  app.on('window-all-closed',()=>app.quit());
  app.on('before-quit',event=>{if(worker||modelWorker){event.preventDefault();pendingClose=true;cancel();cancelModel();}});
}
