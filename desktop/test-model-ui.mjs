import {_electron as electron} from 'playwright';
import {createRequire} from 'node:module';
import path from 'node:path';
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
const require=createRequire(import.meta.url),root=path.resolve('..');
const env={...process.env,RPT_TOOL_ROOT:root};delete env.ELECTRON_RUN_AS_NODE;
const app=await electron.launch({executablePath:require('electron'),args:[process.cwd()],env});
try{
  const page=await app.firstWindow(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.waitForFunction(()=>!document.querySelector('#start').disabled);
  const inventory=await page.evaluate(()=>window.rpt.model({op:'inventory'}));assert(inventory.ok);assert.equal(inventory.value.selected,null);
  await app.evaluate(({dialog})=>{dialog.showMessageBox=async(...args)=>{globalThis.lastModelDialog=args.at(-1);return {response:0};};});
  await page.locator('#source-path').fill('X:/DO-NOT-READ');await page.locator('#start').click();
  await page.waitForFunction(()=>document.querySelector('#page-title').textContent==='AI 모델');
  const dialog=await app.evaluate(()=>globalThis.lastModelDialog);assert.equal(dialog.buttons[0],'AI 모델로 이동');
  assert.equal((await page.evaluate(()=>window.rpt.state())).value.job.running,false);
  assert.equal(await page.locator('#source-path').inputValue(),'X:/DO-NOT-READ');
  await page.locator('#repo-options button').first().waitFor({timeout:45000});
  assert.match(await page.locator('#repo-options button').first().innerText(),/추천/);
  await page.locator('#repo-options button').first().click();
  await page.locator('#model-facts dd').first().waitFor({timeout:45000});
  assert.match(await page.locator('#file-toggle').innerText(),/Q6_K/);
  await page.locator('#file-toggle').click();
  await page.screenshot({path:path.join(root,'build/electron-ui-review/ai-models-live.png'),animations:'disabled'});
  await page.locator('#file-toggle').click();
  // Only synthetic IPC responses below; no real model selection, removal or download.
  const library=structuredClone(inventory.value);
  await app.evaluate(({ipcMain},library)=>{
    globalThis.testLibrary=library;ipcMain.removeHandler('rpt:model');ipcMain.handle('rpt:model',(_e,r)=>{
      if(r.op==='select')globalThis.testLibrary.selected=r.model;
      if(r.op==='delete')globalThis.testLibrary.installed=globalThis.testLibrary.installed.filter(m=>m.id!==r.model);
      return {ok:true,value:globalThis.testLibrary};
    });
  },library);
  await page.locator('#installed-model').selectOption(library.installed[0].id);await page.locator('#apply-model').click();
  await page.waitForFunction(()=>document.querySelector('#message').textContent.includes('저장했습니다'));
  await page.locator('#installed-model').selectOption('');await page.locator('#apply-model').click();
  await page.waitForFunction(()=>document.querySelector('#message').textContent.includes('해제했습니다'));
  assert.equal((await app.evaluate(()=>globalThis.testLibrary)).selected,null);
  await page.locator('#installed-model').selectOption(library.installed[0].id);await page.locator('#delete-model').click();
  await page.waitForFunction(n=>document.querySelector('#installed-model').options.length===n,library.installed.length);
  await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].setSize(920,720));
  await page.locator('#download-model').scrollIntoViewIfNeeded();
  assert(await page.locator('#download-model').isVisible());assert.deepEqual(errors,[]);
  await writeFile(path.join(root,'build/model-ui-verification.json'),JSON.stringify({passed:true,liveHF:true,checks:['missing model dialog before worker','AI Model redirect preserves path','recommended repo and quantization first','live model details','explicit none selection','model deletion UI','small window scrolling'],gameRead:false,modelDownload:false,actualSelectionChanged:false},null,2));
  console.log('AI Model UI checks passed; live HF metadata, no model downloads or game access.');
}finally{await app.close();}
