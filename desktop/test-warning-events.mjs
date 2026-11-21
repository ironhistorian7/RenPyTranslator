import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdtemp,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {EventEmitter} from 'node:events';
import {PassThrough,Writable} from 'node:stream';
import vm from 'node:vm';
import {build} from 'esbuild';

const desktop=path.dirname(fileURLToPath(import.meta.url)),require=createRequire(import.meta.url);
const compiled=await build({entryPoints:[path.join(desktop,'src/job-events.ts')],bundle:true,write:false,platform:'node',format:'cjs'});
const output={exports:{}};vm.runInNewContext(compiled.outputFiles[0].text,{module:output,exports:output.exports});
const {jobEvent,warningText,countSummary}=output.exports;
let job={running:true,exitCode:null,phase:'translation',stage:'번역',started:1,completed:1,total:100};
const event=value=>{job={...job,...jobEvent(job,value,100)};};
event({event:'issue',state:'retrying',code:'format',title:'응답 형식',error_id:'first'});
assert.equal(job.issue,null);assert.equal(job.warnings.length,1);
event({event:'warning',code:'format',title:'응답 형식',occurrences:4,item_count:3,item_ids:['a','b','c'],error_ids:['first','second']});
event({event:'warning',code:'format',title:'응답 형식',occurrences:4,item_count:3,item_ids:['a','b','c']});
assert.equal(job.warnings[0].occurrences,4);
event({event:'progress',phase:'translation',completed:20,total:100,original_kept:3,partial:2});
assert.equal(job.warnings[0].item_count,3);assert.equal(job.original_kept,3);assert.equal(job.partial,2);assert.equal(job.issue,null);
assert.match(countSummary(job),/부분 번역 2개/);assert.match(warningText(job),/부분 번역: 2개/);
event({event:'progress',phase:'translation',partial:0});
assert.equal(job.partial,0);assert.match(countSummary(job),/부분 번역 0개/);assert.match(warningText(job),/부분 번역: 0개/);
event({event:'progress',phase:'translation',partial:-1});assert.equal(job.partial,0);
event({event:'recovery',state:'waiting',attempt:2,max_attempts:3,total_attempts:7,delay_seconds:30});
assert.equal(job.recovery.total_restarts,7);assert.equal(job.recovery.max_attempts,3);assert.equal(job.issue,null);
event({event:'stage',phase:'render',label:'파일 생성'});
assert.equal(job.recovery,null);assert.equal(job.warnings.length,1);
event({event:'warning_summary',warnings:[{code:'original_kept',title:'원문 유지',occurrences:3,item_count:3,item_ids:['a','b','c']}]});
event({event:'issue',state:'failed',code:'stopped',title:'작업 중단',error_id:'fatal'});
assert.equal(job.issue.state,'failed');assert.equal(job.warnings.length,2);
const text=warningText({...job,running:false,exitCode:1});
assert.match(text,/응답 형식 \[format\]: 4회 · 3개 항목/);assert.match(text,/항목 ID: a, b, c/);assert(!text.startsWith('{'));

// Execute production main in a VM with mocked IPC/processes and an isolated
// temporary root. No Electron window, Python helper or child process executes.
const root=await mkdtemp(path.join(tmpdir(),'rpt-warning-test-'));
try{
  const handlers=new Map(),workers=[];let copied='',helperCalls=0;
  const app=Object.assign(new EventEmitter(),{isPackaged:false,setPath(){},setAppLogsPath(){},requestSingleInstanceLock:()=>false,quit(){}});
  const fakeElectron={app,BrowserWindow:class{},ipcMain:{handle:(name,fn)=>handlers.set(name,fn)},nativeTheme:Object.assign(new EventEmitter(),{shouldUseDarkColors:false}),
    dialog:{showMessageBox:async()=>({response:1})},shell:{openPath:async()=>''},clipboard:{writeText:text=>{copied=text;}}};
  const execFile=(_exe,args,_options,callback)=>{
    helperCalls++;const child=new EventEmitter();child.stdin=new Writable({write(_chunk,_encoding,done){done();
      const data=args.at(-1)==='--desktop-project'?{engine:'renpy',supported:true,stop_message:null,supported_tasks:null}:{result:null};
      queueMicrotask(()=>callback(null,JSON.stringify(data),''));}});return child;
  };
  const spawn=()=>{const child=new EventEmitter();child.stdout=new PassThrough();child.stderr=new PassThrough();child.stdin=new Writable({write(_chunk,_encoding,done){done();}});workers.push(child);return child;};
  const main=await build({entryPoints:[path.join(desktop,'src/main.ts')],bundle:true,write:false,platform:'node',format:'cjs',external:['electron']});
  vm.runInNewContext(main.outputFiles[0].text,{require:id=>id==='electron'?fakeElectron:id==='node:child_process'?{execFile,spawn}:require(id),
    process:{...process,env:{...process.env,RPT_TOOL_ROOT:root}},console,__dirname:path.join(desktop,'dist'),Buffer,setTimeout,clearTimeout,URL});
  const sender={sender:undefined,senderFrame:{url:pathToFileURL(path.join(desktop,'dist/index.html')).href}};
  const invoke=(name,...args)=>handlers.get('rpt:'+name)(sender,...args);
  const request={path:'X:/SyntheticOnly',source:true,tasks:['run'],settings:{source_language:'japanese',runtime_id:'',inference_preset:'auto'}};
  assert.equal((await invoke('start',request)).ok,true);
  const emit=record=>workers[0].stdout.write('RPT_EVENT '+JSON.stringify(record)+'\n');
  emit({event:'warning',code:'language',title:'번역 확인',occurrences:6,item_count:2,item_ids:['abc','def'],error_ids:['diagnostic']});
  emit({event:'progress',phase:'translation',completed:5,total:10,original_kept:2});
  assert.equal((await invoke('state')).value.job.issue,null);
  workers[0].emit('close',0);
  const before=helperCalls;assert.equal((await invoke('support',{op:'warnings'})).ok,true);
  assert.equal(helperCalls,before);assert.match(copied,/번역 확인 \[language\]: 6회 · 2개 항목/);assert.match(copied,/abc, def/);
  assert.equal((await invoke('state')).value.job.issue,null);
  await invoke('reset');assert.equal((await invoke('state')).value.job.warnings.length,0);
  const html=await readFile(path.join(desktop,'src/index.html'),'utf8'),renderer=await readFile(path.join(desktop,'src/renderer.ts'),'utf8');
  assert(html.indexOf('id="warning-summary"')>html.indexOf('id="completion-pane"'));
  assert.match(renderer,/window\.rpt\.support\(\{op:'warnings'\}\)/);
  assert.match(renderer,/const issue=job\.issue\?\.state==='failed'\?job\.issue:null/);
  console.log('Warning grouping, persistent counters, recovery state, final-only summary, real clipboard IPC and reset passed with mocked processes.');
}finally{await rm(root,{recursive:true,force:true});}
