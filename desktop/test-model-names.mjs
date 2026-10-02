import {_electron as electron} from 'playwright';
import {createRequire} from 'node:module';
import {mkdtemp,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url),root=path.resolve('..');
const profile=await mkdtemp(path.join(root,'build','model-name-ui-'));
const env={...process.env,RPT_TOOL_ROOT:root};delete env.ELECTRON_RUN_AS_NODE;
const app=await electron.launch({executablePath:require('electron'),args:[process.cwd(),'--user-data-dir='+profile],env});
try{
  const page=await app.firstWindow(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.waitForFunction(()=>!document.querySelector('#start').disabled);
  const inventory=await page.evaluate(()=>window.rpt.model({op:'inventory'}));assert(inventory.ok);
  const hy=inventory.value.installed.find(m=>m.id==='rpt-hymt2-7b:q6_k');
  assert.equal(hy.title,'HY-MT2-7B-Q6_K.gguf');
  // Keep the actual local inventory; suppress online catalogue requests only.
  await app.evaluate(({ipcMain},library)=>{
    ipcMain.removeHandler('rpt:model');ipcMain.handle('rpt:model',(_e,r)=>({ok:true,value:r.op==='search'?[]:library}));
  },inventory.value);
  await page.locator('[data-page=models]').click();
  await page.waitForFunction(()=>Array.from(document.querySelector('#installed-model').options).some(o=>o.textContent.includes('HY-MT2-7B-Q6_K.gguf')));
  const option=page.locator('#installed-model option[value="rpt-hymt2-7b:q6_k"]');
  assert.match(await option.textContent(),/HY-MT2-7B-Q6_K\.gguf.*rpt-hymt2-7b:q6_k/);
  assert.doesNotMatch(await page.locator('#installed-model').textContent(),/Global_Step_560/);
  await page.locator('#installed-model').selectOption(hy.id);
  assert.equal(await page.locator('#apply-model').isEnabled(),true);
  assert.deepEqual(errors,[]);
  await writeFile(path.join(root,'build/model-name-ui-verification.json'),JSON.stringify({passed:true,label:await option.textContent(),modelSelectedOrChanged:false,gameAccess:false,modelExecution:false},null,2));
  console.log('Actual local HY inventory is rendered with filename + model ID; no registration or selection changes.');
}finally{await app.close();}
