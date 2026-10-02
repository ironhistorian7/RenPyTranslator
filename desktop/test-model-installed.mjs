import {_electron as electron} from 'playwright';
import {createRequire} from 'node:module';
import {mkdtemp,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url),root=path.resolve('..');
const profile=await mkdtemp(path.join(root,'build','installed-model-ui-'));
const env={...process.env,RPT_TOOL_ROOT:root};delete env.ELECTRON_RUN_AS_NODE;
const app=await electron.launch({executablePath:require('electron'),args:[process.cwd(),'--user-data-dir='+profile],env});
try{
  const page=await app.firstWindow(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.waitForFunction(()=>!document.querySelector('#start').disabled);
  const inventory=await page.evaluate(()=>window.rpt.model({op:'inventory'}));assert(inventory.ok);
  const reply=await page.evaluate(()=>window.rpt.model({op:'details',repo:'tencent/Hy-MT2-7B-GGUF'}));assert(reply.ok,reply.error);
  const detail=reply.value,hy=inventory.value.installed.find(m=>m.id==='rpt-hymt2-7b:q6_k');
  const q6=detail.files.find(f=>f.name==='HY-MT2-7B-Q6_K.gguf');assert.equal(q6.installedModel,hy.id);
  const other=detail.files.find(f=>!f.installedModel);assert(other);
  // Use actual local inventory + official metadata, but simulate deletion only.
  await app.evaluate(({ipcMain},{library,detail})=>{
    globalThis.modelTest={library,detail,workCalls:0};ipcMain.removeHandler('rpt:model');
    ipcMain.handle('rpt:model',(_e,r)=>{
      const t=globalThis.modelTest;
      if(r.op==='search')return {ok:true,value:[{id:t.detail.id,recommended:true,files:t.detail.files}]};
      if(r.op==='details')return {ok:true,value:t.detail};
      if(r.op==='delete')t.library.installed=t.library.installed.filter(m=>m.id!==r.model);
      return {ok:true,value:t.library};
    });
    ipcMain.removeHandler('rpt:modelWork');ipcMain.handle('rpt:modelWork',()=>{globalThis.modelTest.workCalls++;return {ok:false,error:'Must not download in this test'};});
  },{library:inventory.value,detail});
  await page.locator('[data-page=models]').click();
  const repo=page.locator('#repo-options button').first();await repo.waitFor();
  assert.match(await repo.innerText(),/설치됨.*Q6_K/);await repo.click();
  await page.waitForFunction(()=>document.querySelector('#download-model').textContent==='설치됨');
  assert.equal(await page.locator('#download-model').isDisabled(),true);
  await page.evaluate(()=>document.querySelector('#download-model').click());
  assert.equal(await app.evaluate(()=>globalThis.modelTest.workCalls),0);
  await page.locator('#file-toggle').click();
  const q6option=page.locator('#file-options button').filter({hasText:q6.name});
  assert.match(await q6option.innerText(),/설치됨/);
  await page.locator('#file-options button').filter({hasText:other.name}).click();
  assert.equal(await page.locator('#download-model').isEnabled(),true);
  assert.equal(await page.locator('#download-model').innerText(),'다운로드');
  await page.locator('#file-toggle').click();await q6option.click();
  await page.locator('#installed-model').selectOption(hy.id);await page.locator('#delete-model').click();
  await page.waitForFunction(()=>document.querySelector('#download-model').disabled===false);
  assert.equal(await page.locator('#repo-options .installed-badge').count(),0);
  assert.equal(await page.locator('#file-options .installed-badge').count(),0);
  assert.deepEqual(errors,[]);assert.equal(await app.evaluate(()=>globalThis.modelTest.workCalls),0);
  await writeFile(path.join(root,'build/model-installed-ui-verification.json'),JSON.stringify({passed:true,liveOfficialMetadata:true,hashMatch:q6.sha,checks:['recommended repo installed badge','quant-specific badge','duplicate download disabled','other quant downloadable','delete updates badges and button'],realModelsChanged:false,modelDownload:false,gameAccess:false},null,2));
  console.log('Installed badges, duplicate prevention and removal refresh passed; actual HF metadata, no model mutations or downloads.');
}finally{await app.close();}
