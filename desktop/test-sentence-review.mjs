import {_electron as electron} from 'playwright';
import {createRequire} from 'node:module';
import {mkdtemp, mkdir, writeFile, rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';

// Runs the compiled renderer and preload with synthetic IPC only.
const require=createRequire(import.meta.url);
const desktop=path.dirname(fileURLToPath(import.meta.url));
const version=require('./package.json').version;
const dir=await mkdtemp(path.join(os.tmpdir(),'rpt-sentence-ui-'));
const out=process.env.RPT_SENTENCE_SCREENSHOT_DIR || path.join(os.tmpdir(),'rpt-sentence-review');
await mkdir(out,{recursive:true});
const main=path.join(dir,'main.cjs');
await writeFile(main,`
const {app,BrowserWindow,ipcMain}=require('electron');
const fs=require('node:fs'),path=require('node:path');
const root=${JSON.stringify(dir)};
fs.mkdirSync(path.join(root,'profile'),{recursive:true});
app.setPath('userData',path.join(root,'profile'));
app.setPath('sessionData',path.join(root,'profile'));
app.commandLine.appendSwitch('disable-background-networking');
const child=require('node:child_process');
for(const name of ['spawn','spawnSync','exec','execSync','execFile','execFileSync','fork'])child[name]=()=>{throw Error('Child processes forbidden');};
const idle={running:false,canceling:false,exitCode:null,result:null,stage:'대기 중',completed:null,total:null,started:null};
const running={...idle,running:true,stage:'검토 중'};
const settings={output:'project',suffix:'-kr',scale:'default',source_language:'english',show_reference:true,language_corner:'right',language_margin:12,theme:'light',gpu_mode:'auto',gpu_ids:[],inference_preset:'auto',inference_overrides:{},gpu_overrides:{}};
const values={num_ctx:16384,parallel:2,kv_cache_type:'q8_0',context_txt_enabled:true};
const snapshot={root,settings,appVersion:${JSON.stringify(version)},tasks:{run:'번역'},fixes:[],defaultModel:'synthetic',runtime:'Synthetic',gpus:[],models:[],warnings:[],inference:{presets:[],detected_profile:null,total_mib:null,support:'estimated',recommended:values,effective:values,selected_preset:'auto',overrides:{},reduced_only:false,warnings:[],estimated:true}};
const modelJob={running:false,stage:'',done:0,total:0,error:null};
const probe=globalThis.testProbe={replies:[],unexpected:[],blocked:[],fail:false};
globalThis.capture=async file=>{const image=await BrowserWindow.getAllWindows()[0].webContents.capturePage(undefined,{stayHidden:true,stayAwake:true});fs.writeFileSync(file,image.toPNG());};
const handle=(name,fn)=>ipcMain.handle('rpt:'+name,async(_event,...args)=>{try{return {ok:true,value:await fn(...args)}}catch(e){return {ok:false,error:e.message}}});
handle('info',()=>snapshot);handle('state',()=>({job:idle,modelJob,log:'',dark:false}));
handle('appearance',theme=>theme==='dark');handle('save',v=>v);handle('inferencePreview',()=>snapshot.inference);
handle('model',()=>({selected:'synthetic',installed:[],pending:[],folder:root}));
handle('runtime',()=>[]);handle('cancel',()=>idle);handle('reset',()=>idle);
handle('translationReview',reply=>{probe.replies.push(reply);if(probe.fail)throw Error('저장하지 못했습니다. 다시 시도하세요.');return running;});
for(const name of ['start','project','browse','open','modelWork','runtimeWork','modelCancel','support'])handle(name,()=>{probe.unexpected.push(name);throw Error('Unexpected IPC');});
app.whenReady().then(()=>{const win=new BrowserWindow({show:false,width:1180,height:900,webPreferences:{preload:${JSON.stringify(path.join(desktop,'dist','preload.js'))},offscreen:true,contextIsolation:true,nodeIntegration:false,sandbox:false,backgroundThrottling:false}});globalThis.testWindow=win;win.webContents.setWindowOpenHandler(()=>({action:'deny'}));win.webContents.session.webRequest.onBeforeRequest({urls:['http://*/*','https://*/*']},(d,cb)=>{probe.blocked.push(d.url);cb({cancel:true})});win.loadFile(${JSON.stringify(path.join(desktop,'dist','index.html'))});});
app.on('window-all-closed',()=>app.quit());
`);
const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
let app;
try {
  app=await electron.launch({executablePath:require('electron'),args:[main],cwd:desktop,env,timeout:30000});
  const page=await app.firstWindow();page.setDefaultTimeout(10000);
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.waitForFunction(()=>document.body.dataset.ready==='true');
  const emit=review=>app.evaluate(({BrowserWindow},review)=>BrowserWindow.getAllWindows()[0].webContents.send('rpt:event',{type:'job',job:{running:true,canceling:false,exitCode:null,result:null,stage:'검토 중',completed:null,total:null,started:null,translation_review:review}}),review);
  const manual={fingerprint:'manual-dummy',entries:[{id:'manual-1',source:'A small lantern glows.',current:'작은 등불이 빛난다.'}]};
  const review={kind:'sentence',fingerprint:'sentence-dummy',groups:[
    {id:'g1',source:'The sea remembers every story.',scope:'chapter01',speaker:'Mira',listener:'Noah',variants:[{id:'v1',text:'바다는 모든 이야기를 기억해.',count:4,location:'chapter01.rpy:120'},{id:'v2',text:'바다는 모든 이야기를 간직하고 있어.',count:2,location:'chapter01.rpy:284'}]},
    {id:'g2',source:'We will meet again at dawn.',scope:'chapter02',speaker:'Noah',variants:[{id:'v3',text:'새벽에 다시 만나자.',count:3,location:'chapter02.rpy:75'},{id:'v4',text:'동이 틀 때 다시 만나.',count:1,location:'chapter02.rpy:191',manual:true}]}
  ]};
  await emit(manual);await page.locator('#translation-review').waitFor({state:'visible'});
  await emit(review);
  await page.getByRole('heading',{name:'문장 번역 확인'}).waitFor();
  assert.equal(await page.getByLabel('각 위치의 현재 번역 유지').isChecked(),true);
  assert.equal(await page.getByLabel('바다는 모든 이야기를 기억해.').isChecked(),false);
  assert.equal(await page.locator('#translation-review-count').innerText(),'변경 선택 0개');
  await page.getByLabel('바다는 모든 이야기를 기억해.').check();
  await page.getByRole('button',{name:'다음',exact:true}).click();
  assert.equal(await page.getByLabel('각 위치의 현재 번역 유지').isChecked(),true);
  await page.getByRole('button',{name:'이전',exact:true}).click();
  assert.equal(await page.getByLabel('바다는 모든 이야기를 기억해.').isChecked(),true);
  assert.equal(await page.locator('#translation-review-count').innerText(),'변경 선택 1개');
  for(const theme of ['light','dark']){
    await app.evaluate(({BrowserWindow},dark)=>BrowserWindow.getAllWindows()[0].webContents.send('rpt:event',{type:'theme',dark}),theme==='dark');
    await page.waitForFunction(theme=>document.documentElement.dataset.theme===theme,theme);
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    await page.waitForTimeout(300);
    await app.evaluate((_electron,file)=>globalThis.capture(file),path.join(out,theme+'.png'));
  }
  await app.evaluate(()=>{globalThis.testProbe.fail=true});
  await page.locator('#translation-review-submit').click();
  await page.getByRole('alert').filter({hasText:'저장하지 못했습니다'}).waitFor();
  assert.equal(await page.getByLabel('바다는 모든 이야기를 기억해.').isChecked(),true);
  assert.equal(await page.locator('#translation-review-count').innerText(),'변경 선택 1개');
  await app.evaluate(()=>{globalThis.testProbe.fail=false});
  await page.locator('#translation-review-submit').click();
  await page.locator('#translation-review').waitFor({state:'hidden'});
  assert.deepEqual(errors,[]);
  const probe=await app.evaluate(()=>globalThis.testProbe);
  assert.deepEqual(probe.replies,[{kind:'sentence',fingerprint:'sentence-dummy',selections:[{id:'g1',variant:'v1'}]},{kind:'sentence',fingerprint:'sentence-dummy',selections:[{id:'g1',variant:'v1'}]}]);
  assert.deepEqual(probe.unexpected,[]);assert.deepEqual(probe.blocked,[]);
  assert.equal(await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].isVisible()),false);
  console.log('Synthetic sentence review UI passed. Screenshots:',out);
} finally {if(app)await app.close();await rm(dir,{recursive:true,force:true});}
