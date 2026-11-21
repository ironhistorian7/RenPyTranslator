import {_electron as electron} from 'playwright';
import {createRequire} from 'node:module';
import {mkdir,mkdtemp,writeFile,readFile,access,unlink} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url),root=path.resolve('..'),base=path.join(root,'build','name-review-flow');
await mkdir(base,{recursive:true});const out=await mkdtemp(path.join(base,'run-')),runtime=path.join(out,'runtime');await mkdir(runtime);
const runner=path.join(out,'worker.py');await writeFile(runner,`import json,sys\nfrom pathlib import Path\nsys.path.insert(0,${JSON.stringify(path.join(root,'app'))})\nimport name_review_control\nr=json.load(sys.stdin); p=Path(r['path'])\ntry:\n name_review_control.run(p,{'engine':'rpgmaker','model':'synthetic','source_language':'english'})\n (p/'sentinel').write_text('confirmed',encoding='utf-8')\nexcept KeyboardInterrupt:\n sys.exit(130)\n`);
const makeProject=async name=>{const project=path.join(out,name),data=path.join(project,'data');await mkdir(data,{recursive:true});await writeFile(path.join(data,'catalog.json'),JSON.stringify([{id:'a',source:'Alexander II',file:'www/data/Actors.json',kind:'string',usage:'speaker_label',location:{fmt:'json',ptr:'[1].name'}},{id:'b',source:'Mira',file:'www/data/Actors.json',kind:'string',usage:'speaker_label',location:{fmt:'json',ptr:'[2].name'}}]));await writeFile(path.join(data,'name-proposals.json'),JSON.stringify({a:{source:'Alexander II',model:'synthetic',text:'Alexander II'},b:{source:'Mira',model:'synthetic',text:'미라'}}));return project;};
const first=await makeProject('first'),second=await makeProject('cancel');
const env={...process.env,RPT_TOOL_ROOT:runtime};delete env.ELECTRON_RUN_AS_NODE;
const app=await electron.launch({executablePath:require('electron'),args:[process.cwd()],env,timeout:30000});
const exists=async p=>access(p).then(()=>true,()=>false);
try{
 await app.evaluate(({BrowserWindow,ipcMain},{root,runner})=>{
  BrowserWindow.getAllWindows()[0].hide();
  const settings={output:'project',suffix:'-kr',scale:'default',source_language:'english',show_reference:true,language_corner:'right',language_margin:12,theme:'light',gpu_mode:'auto',gpu_ids:[],inference_preset:'auto',inference_overrides:{}};
  const v={num_ctx:16384,parallel:2,kv_cache_type:'q8_0',context_txt_enabled:true};const snapshot={root,settings,tasks:{run:'번역'},fixes:[],defaultModel:'synthetic',runtime:'test',gpus:[],models:[],warnings:[],inference:{presets:[{...v,id:'test',minimum_total_mib:1,support:'measured'}],detected_profile:'test',total_mib:1,support:'measured',recommended:v,effective:v,selected_preset:'auto',overrides:{},reduced_only:false,warnings:[],estimated:false}};
  const handle=(id,fn)=>{ipcMain.removeHandler('rpt:'+id);ipcMain.handle('rpt:'+id,async(_,...args)=>({ok:true,value:await fn(...args)}));};
  handle('info',()=>snapshot);handle('model',()=>({selected:'synthetic',installed:[],pending:[],folder:root}));handle('runtime',()=>[]);
  const req=process.getBuiltinModule('node:module').createRequire(process.cwd()+'/test.js'),cp=req('node:child_process'),{EventEmitter}=req('node:events'),{Writable}=req('node:stream');const exec=cp.execFile,spawn=cp.spawn;
  cp.execFile=(exe,args,opts,cb)=>{if(!args.includes('--desktop-project')&&!args.includes('--desktop-models'))return exec(exe,args,opts,cb);const p=new EventEmitter();p.stdin=new Writable({write(chunk,enc,done){done();setImmediate(()=>cb(null,JSON.stringify(args.includes('--desktop-models')?{result:null}:{engine:'rpgmaker',supported:true,supported_tasks:['run'],stop_message:null}),''));}});return p;};
  cp.spawn=(exe,args,opts)=>args.includes('--desktop-run')?spawn(req('node:path').join(root,'.venv','Scripts','python.exe'),[runner],opts):spawn(exe,args,opts);
 },{root,runner});
 const page=await app.firstWindow();const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.reload();await page.waitForFunction(()=>document.body.dataset.ready==='true');
 const request=project=>({path:project,source:true,tasks:['run'],settings:{output:'project',suffix:'-kr',scale:'default',source_language:'english',show_reference:true,language_corner:'right',language_margin:12,theme:'light',gpu_mode:'auto',gpu_ids:[],inference_preset:'auto',inference_overrides:{}}});
 const start=async p=>{const reply=await page.evaluate(r=>window.rpt.start(r),request(p));assert.equal(reply.ok,true,JSON.stringify(reply));};
 const waitDone=async()=>{for(let i=0;i<200;i++){const s=await page.evaluate(()=>window.rpt.state());if(s.ok&&!s.value.job.running)return s.value.job;await new Promise(resolve=>setTimeout(resolve,100));}throw new Error('worker did not finish');};
 const modal=page.locator('#name-review');await start(first);await modal.waitFor({state:'visible',timeout:20000});assert.equal(await exists(path.join(first,'sentinel')),false);
 const input=page.locator('#name-review-needed input').first();await input.fill('');await page.locator('#name-review-submit').click();await page.waitForFunction(()=>document.querySelector('#name-review-error')?.textContent.includes('이름을 입력'),null,{timeout:20000});assert.equal(await input.inputValue(),'');assert.equal(await exists(path.join(first,'sentinel')),false);assert.equal(await exists(path.join(first,'data/name-review.json')),false);
 await input.fill('알렉산더2세');await page.locator('#name-review-submit').click();await waitDone();assert.equal(await exists(path.join(first,'sentinel')),true);const saved=JSON.parse(await readFile(path.join(first,'data/name-review.json'),'utf8'));assert.equal(saved.decisions['rpg:a'],'알렉산더2세');
 await page.evaluate(()=>{window.reviewEvents=[];window.rpt.subscribe(e=>{if(e.type==='job'&&e.job.name_review)window.reviewEvents.push(e.job.name_review.fingerprint);});});await unlink(path.join(first,'sentinel'));await start(first);await waitDone();assert.equal(await exists(path.join(first,'sentinel')),true);assert.equal(await modal.isVisible(),false);assert.deepEqual(await page.evaluate(()=>window.reviewEvents),[]);
 await start(second);await modal.waitFor({state:'visible',timeout:20000});await page.locator('#name-review-cancel').click();await waitDone();assert.equal(await exists(path.join(second,'sentinel')),false);assert.equal(await exists(path.join(second,'data/name-review.json')),false);assert.deepEqual(errors,[]);console.log('Actual Electron IPC + Python name review: waiting, invalid edit, numeric approval, resume and cancel passed.');
}finally{await app.close();}
