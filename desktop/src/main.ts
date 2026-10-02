import {app,BrowserWindow,dialog,ipcMain,nativeTheme,shell} from 'electron';
import {spawn,execFile,ChildProcessWithoutNullStreams} from 'node:child_process';
import {existsSync,mkdirSync,readFileSync,writeFileSync,unlinkSync,appendFileSync} from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import type {Settings,Snapshot,Request,Job,Event,ModelJob} from './types';

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
const indexURL=pathToFileURL(path.join(__dirname,'index.html')).href;
const cli=():[string,string[]]=>{
  const python=path.join(ROOT,'.venv','Scripts','python.exe');
  if(!app.isPackaged && existsSync(python)) return [python,['-B','-X','utf8',path.join(ROOT,'app','portable_cli.py')]];
  return [path.join(ROOT,'RenPyTranslator-cli.exe'),[]];
};
function emit(event:Event){if(window&&!window.isDestroyed())window.webContents.send('rpt:event',event);}
function updateJob(values:Partial<Job>){job={...job,...values};emit({type:'job',job});}
function addLog(text:string){
  log=(log+text).slice(-500000);emit({type:'log',text});
  if(jobLog){try{appendFileSync(jobLog,text,'utf8');}catch{jobLog=null;}}
}
function helper<T>(command:string,payload?:unknown):Promise<T>{
  return new Promise((resolve,reject)=>{
    const [exe,args]=cli();
    const child=execFile(exe,[...args,command],{cwd:ROOT,windowsHide:true,encoding:'utf8',timeout:45000,maxBuffer:2*1024*1024},(err,stdout,stderr)=>{
      if(err){reject(new Error(stderr.trim().split('\n').pop()||err.message));return;}
      try{resolve(JSON.parse(stdout));}catch{reject(new Error('프로그램 상태 응답을 읽지 못했습니다.'));}
    });
    if(payload!==undefined)child.stdin?.end(JSON.stringify(payload));
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
}
async function start(r:Request){
  if(worker||modelWorker)throw new Error('현재 작업이 끝난 뒤 실행하세요.');
  validateRequest(r);
  const check=await helper<{result:null|{title:string;detail:string}}>('--desktop-models',{op:'preflight',tasks:r.tasks});
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
    env:{...process.env,RPT_CANCEL_FILE:cancelFile,PYTHONUTF8:'1',PYTHONUNBUFFERED:'1'}});
  worker=proc;
  updateJob({running:true,canceling:false,exitCode:null,result:null,stage:'작업 준비 중',completed:null,total:null,started:Date.now()});
  addLog('\n작업을 시작합니다.\n');
  let tail='';
  function parseLine(line:string){
    if(line.startsWith('Output folder: '))updateJob({result:line.slice(15).trim()});
    const progress=line.match(/^(\d+)\/(\d+) entries, batch/);
    if(progress)updateJob({stage:'번역 중',completed:Number(progress[1]),total:Number(progress[2])});
    else if(line.startsWith('Translating '))updateJob({stage:'번역 중'});
    else if(line.startsWith('Rendered '))updateJob({stage:'번역 파일 생성 중'});
    else if(line.startsWith('Copying '))updateJob({stage:'작업 파일 준비 중'});
    else if(line.startsWith('Failed repair:'))updateJob({stage:'미번역 항목 복구 중'});
    else if(line.startsWith('Name '))updateJob({stage:'인명 표기 보정 중'});
    else if(line.startsWith('In-game hints:'))updateJob({stage:'정답·선택지 힌트 처리 중'});
  }
  proc.stdout.setEncoding('utf8');proc.stderr.setEncoding('utf8');
  proc.stdout.on('data',(text:string)=>{addLog(text);tail+=text;const lines=tail.split(/\r?\n/);tail=lines.pop()||'';for(const line of lines)parseLine(line);});
  proc.stderr.on('data',(text:string)=>addLog(text));
  proc.stdin.on('error',()=>{});
  proc.stdin.end(JSON.stringify(r));
  let finished=false;
  function finish(code:number){
    if(finished)return;finished=true;
    if(tail)parseLine(tail);
    worker=null;
    const cancelled=code===130;
    updateJob({running:false,canceling:false,exitCode:code,stage:code===0?'완료':cancelled?'취소됨':'작업 실패'});
    addLog(code===0?'작업을 마쳤습니다.\n':cancelled?'취소했습니다. 저장된 결과는 유지됩니다.\n':'작업에 실패했습니다. 위 기록을 확인하세요.\n');
    jobLog=null;
    if(cancelFile){try{unlinkSync(cancelFile);}catch{}cancelFile=null;}
    if(pendingClose)app.quit();
  }
  proc.once('error',err=>{addLog(err.message+'\n');finish(1);});
  proc.once('close',code=>finish(code??1));
  return job;
}
function cancel(){
  if(worker&&cancelFile&&!job.canceling){writeFileSync(cancelFile,'');updateJob({canceling:true,stage:'취소 및 모델 정리 중'});addLog('취소를 요청했습니다. 현재 작업과 모델을 정리합니다.\n');}
  return job;
}
function route(name:string,fn:(...args:any[])=>unknown){
  ipcMain.handle('rpt:'+name,async(event,...args)=>{
    if(event.sender!==window?.webContents||event.senderFrame?.url!==indexURL)return {ok:false,error:'허용되지 않은 요청입니다.'};
    try{return {ok:true,value:await fn(...args)};}catch(error){return {ok:false,error:error instanceof Error?error.message:String(error)};}
  });
}
route('info',info);
route('state',()=>({job,log,modelJob,dark:nativeTheme.shouldUseDarkColors}));
route('appearance',setAppearance);
route('browse',async()=>{const r=await dialog.showOpenDialog(window!,{title:'폴더 선택',properties:['openDirectory']});return r.canceled?null:r.filePaths[0];});
route('save',async(s:Settings)=>{if(worker||modelWorker)throw new Error('작업이 끝난 뒤 설정을 저장하세요.');const saved=await helper<Settings>('--desktop-settings',s);setAppearance(saved.theme);return saved;});
route('start',start);
route('cancel',cancel);
route('open',async(which:unknown)=>{
  const target=which==='result'?job.result:which==='logs'?path.join(ROOT,'logs'):which==='models'?path.join(ROOT,'models'):null;
  if(which==='models'&&target)mkdirSync(target,{recursive:true});
  if(!target)throw new Error('열 수 있는 결과 폴더가 아직 없습니다.');
  if(!existsSync(target))throw new Error('폴더가 아직 생성되지 않았습니다.');
  const err=await shell.openPath(target);if(err)throw new Error(err);
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
function updateModel(values:Partial<ModelJob>){modelJob={...modelJob,...values};emit({type:'modelJob',job:modelJob});}
function cancelModel(){if(modelWorker&&modelCancelFile){writeFileSync(modelCancelFile,'');updateModel({stage:'취소 중…'});}}
route('modelCancel',cancelModel);
route('modelWork',(r:Record<string,unknown>)=>{
  if(worker||modelWorker)throw new Error('현재 작업이 끝난 뒤 실행하세요.');
  if(!r||!['download','refresh'].includes(String(r.op)))throw new Error('모델 작업을 확인하세요.');
  const control=path.join(ROOT,'data','control');mkdirSync(control,{recursive:true});
  modelCancelFile=path.join(control,`model-${process.pid}-${Date.now()}.cancel`);
  const [exe,args]=cli();
  const proc=spawn(exe,[...args,'--desktop-models'],{cwd:ROOT,windowsHide:true,stdio:'pipe',env:{...process.env,RPT_CANCEL_FILE:modelCancelFile,PYTHONUTF8:'1'}});
  modelWorker=proc;updateModel({running:true,stage:r.op==='download'?'다운로드 준비 중':'모델 확인 중',done:0,total:0,error:null});
  let tail='',errors='';proc.stdout.setEncoding('utf8');proc.stderr.setEncoding('utf8');
  proc.stdout.on('data',(s:string)=>{tail+=s;const lines=tail.split(/\r?\n/);tail=lines.pop()||'';for(const line of lines){try{const item=JSON.parse(line);if(item.stage)updateModel({stage:item.stage,done:item.done||0,total:item.total||0});}catch{}}});
  proc.stderr.on('data',(s:string)=>{errors=(errors+s).slice(-4000);});
  proc.stdin.on('error',()=>{});proc.stdin.end(JSON.stringify(r));
  let finished=false;
  const finish=(code:number)=>{if(finished)return;finished=true;modelWorker=null;
    updateModel({running:false,stage:code===0?'완료':code===130?'취소됨 · 다시 다운로드하면 이어받습니다.':'모델 작업 실패',error:code!==0&&code!==130?errors.trim()||'작업을 완료하지 못했습니다.':null});
    if(modelCancelFile){try{unlinkSync(modelCancelFile);}catch{}modelCancelFile=null;}if(pendingClose)app.quit();};
  proc.once('error',e=>{errors=e.message;finish(1);});proc.once('close',code=>finish(code??1));return modelJob;
});
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
