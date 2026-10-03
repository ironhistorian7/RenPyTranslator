// Run against an explicitly relocated distribution; no game or model work.
import {_electron as electron} from 'playwright';
import path from 'node:path';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const root=path.resolve(process.argv[2]);
const env={...process.env,PATH:path.join(process.env.SystemRoot,'System32')};
delete env.ELECTRON_RUN_AS_NODE;delete env.RPT_TOOL_ROOT;
const app=await electron.launch({executablePath:path.join(root,'_desktop','RenPyTranslator-UI.exe'),env,timeout:30000});
try{
  const page=await app.firstWindow();
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.waitForFunction(()=>!document.querySelector('#start').disabled);
  const info=await page.evaluate(()=>window.rpt.info());assert.equal(info.ok,true);assert.equal(info.value.root,root);
  assert.equal(await app.evaluate(({app})=>app.isPackaged),true);
  const profile=await app.evaluate(({app})=>app.getPath('userData'));assert.equal(profile,path.join(root,'data','desktop-profile'));
  await page.locator('[data-page=info]').click();
  await page.locator('button[data-theme=dark]').click();
  await page.locator('input[name=gpu-mode][value=all]').check();
  await page.locator('#save').click();
  await page.waitForFunction(()=>document.querySelector('#message').textContent.includes('설정을 저장했습니다'));
  const saved=JSON.parse(await readFile(path.join(root,'data','gui-settings.json'),'utf8'));
  assert.equal(saved.theme,'dark');assert.equal(saved.gpu_mode,'all');
  await page.locator('[data-page=advanced]').click();assert.equal(await page.locator('#start').isDisabled(),true);
  const library=await page.evaluate(()=>window.rpt.model({op:'inventory'}));assert.equal(library.ok,true);assert.equal(library.value.selected,null);assert.deepEqual(library.value.installed,[]);
  await app.evaluate(({dialog})=>{dialog.showMessageBox=async()=>({response:0});});
  await page.locator('[data-page=basic]').click();await page.locator('#source-path').fill('X:/DO-NOT-READ');
  // Project-language restoration runs when the path field loses focus.
  // Wait for that metadata-only operation before testing the Start action.
  await page.locator('#source-path').press('Tab');
  await page.waitForFunction(()=>!document.querySelector('#start').disabled);
  await page.locator('#start').click();
  await page.waitForFunction(()=>document.querySelector('#page-title').textContent==='AI 모델');
  assert.equal((await page.evaluate(()=>window.rpt.state())).value.job.running,false);
  const remote=await page.evaluate(()=>window.rpt.model({op:'details',repo:'tencent/Hy-MT2-7B-GGUF'}));assert.equal(remote.ok,true);assert(remote.value.files.some(f=>f.recommended));
  assert.deepEqual(errors,[]);
  console.log('Relocated packaged Electron + frozen Python: UI, hardware info, settings persistence, missing-model redirect, live HF API and portable profile OK; no game/model execution.');
}finally{await app.close();}
