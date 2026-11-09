// Synthetic GPU/status/save IPC only. No inference, game access, model API or user-setting writes.
import {_electron as electron} from 'playwright';
import {createRequire} from 'node:module';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url),root=path.resolve('..');
const env={...process.env,RPT_TOOL_ROOT:root};delete env.ELECTRON_RUN_AS_NODE;
const app=await electron.launch({executablePath:require('electron'),args:[process.cwd()],env});
const page=await app.firstWindow(),errors=[];page.on('pageerror',e=>errors.push(e.message));
try{
  await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].hide());
  await page.waitForFunction(()=>document.body.dataset.ready==='true');
  const initial=await page.evaluate(()=>window.rpt.info());assert(initial.ok);
  const profiles=initial.value.inference.presets;
  async function scenario(total,id){
    const data=structuredClone(initial.value),profile=profiles.find(p=>p.id===id);
    data.settings.inference_preset='auto';data.settings.inference_overrides={};data.settings.gpu_mode='auto';data.settings.gpu_ids=[];
    data.gpus=[{id:'GPU-synthetic',name:'Synthetic NVIDIA GPU',total,used:total-1,utilization:99,driver:'test',selectable:true,active:false}];
    data.models=[];data.inference={presets:profiles,detected_profile:id,total_mib:total,support:profile.support,recommended:profile,effective:profile,selected_preset:'auto',overrides:{},reduced_only:profile.support==='estimated',warnings:[],estimated:profile.support==='estimated'};
    await app.evaluate(({ipcMain},data)=>{
      globalThis.syntheticInfo=data;globalThis.savedInference=null;
      for(const name of ['info','save','model'])ipcMain.removeHandler('rpt:'+name);
      ipcMain.handle('rpt:info',()=>({ok:true,value:globalThis.syntheticInfo}));
      ipcMain.handle('rpt:save',(_e,s)=>{globalThis.savedInference=s;globalThis.syntheticInfo.settings=s;return {ok:true,value:s};});
      ipcMain.handle('rpt:model',(_e,r)=>({ok:true,value:r.op==='search'?[]:{selected:null,installed:[],pending:[],folder:'synthetic'}}));
    },data);
    await page.reload();await page.waitForFunction(()=>document.body.dataset.ready==='true');
    await page.locator('[data-page=models]').click();await page.locator('#inference-advanced summary').click();
    assert.equal(await page.locator('#inference-context').inputValue(),String(profile.num_ctx));
    assert.equal(await page.locator('#inference-workers').inputValue(),String(profile.parallel));
    assert.equal(await page.locator('#inference-txt').isChecked(),profile.context_txt_enabled);
    assert.equal(await page.locator('#inference-warning').isVisible(),false);
    return profile;
  }
  for(const [total,id] of [[6142,'6gb'],[8190,'8gb'],[12282,'12gb']]){
    await scenario(total,id);
    await page.locator('#inference-workers').fill('3');assert(await page.locator('#inference-warning').isVisible());
    await page.locator('#inference-workers').fill('1');assert(await page.locator('#inference-warning').isVisible());
    await page.locator('#inference-reset').click();assert.equal(await page.locator('#inference-warning').isVisible(),false);
    assert.equal(await page.locator('#inference-workers').inputValue(),'2');
  }
  await scenario(14336,'12gb');assert(await page.locator('#inference-estimate').isVisible());
  await page.locator('#inference-workers').fill('3');assert.equal(await page.locator('#inference-warning').isVisible(),false);
  await page.locator('#inference-workers').fill('1');assert(await page.locator('#inference-warning').isVisible());
  for(const [total,id,workers] of [[16380,'16gb',3],[24570,'24gb',4],[32760,'32gb',6]]){
    await scenario(total,id);assert(await page.locator('#inference-estimate').isVisible());
    await page.locator('#inference-workers').fill(String(workers+2));assert.equal(await page.locator('#inference-warning').isVisible(),false);
    await page.locator('#inference-context').fill('32768');assert.equal(await page.locator('#inference-warning').isVisible(),false);
    await page.locator('#inference-workers').fill(String(workers-1));assert(await page.locator('#inference-warning').isVisible());
    await page.locator('#inference-reset').click();assert.equal(await page.locator('#inference-context').inputValue(),'16384');
  }
  await scenario(24570,'24gb');await page.locator('#inference-workers').fill('5');
  await page.locator('#inference-save').click();await page.waitForFunction(()=>document.querySelector('#message').textContent.includes('저장했습니다'));
  const saved=await app.evaluate(()=>globalThis.savedInference);assert.equal(saved.inference_overrides.parallel,5);
  await page.reload();await page.waitForFunction(()=>document.body.dataset.ready==='true');
  await page.locator('[data-page=models]').click();await page.locator('#inference-advanced summary').click();
  assert.equal(await page.locator('#inference-workers').inputValue(),'5');
  await page.locator('#inference-preset').selectOption('12gb');assert.equal(await page.locator('#inference-workers').inputValue(),'2');assert(await page.locator('#inference-warning').isVisible());
  await page.locator('#inference-reset').click();assert.equal(await page.locator('#inference-workers').inputValue(),'4');
  const job={running:true,canceling:false,exitCode:null,result:null,stage:'synthetic',completed:0,total:1,started:Date.now()};
  await app.evaluate(({BrowserWindow},job)=>BrowserWindow.getAllWindows()[0].webContents.send('rpt:event',{type:'job',job}),job);
  await page.waitForFunction(()=>document.querySelector('#inference-workers').disabled);
  assert(await page.locator('#inference-save').isDisabled());
  await app.evaluate(({BrowserWindow},job)=>BrowserWindow.getAllWindows()[0].webContents.send('rpt:event',{type:'job',job:{...job,running:false}}),job);
  await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].setSize(920,720));
  await page.locator('#inference-save').scrollIntoViewIfNeeded();assert(await page.locator('#inference-save').isVisible());
  const out=path.join(root,'build','gpu-preset-ui');await mkdir(out,{recursive:true});
  await page.screenshot({path:path.join(out,'advanced.png')});
  assert.deepEqual(errors,[]);
  await writeFile(path.join(out,'report.json'),JSON.stringify({passed:true,checks:['six capacity presets','total not free VRAM','small-card warnings both directions','large-card increase has no warning','large-card decrease has warning','save/reload overrides','preset reset','busy locking','small window'],inference:false,gameRead:false,userSettingsWritten:false},null,2));
  console.log('GPU preset UI checks passed; no inference or user-setting changes.');
}finally{await app.close();}
