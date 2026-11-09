// Isolated portable backend and synthetic metadata. No model or game execution.
import {_electron as electron} from 'playwright';
import {createRequire} from 'node:module';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url),root=path.resolve(process.argv[2]);
assert(root===path.resolve('../dist/RenPyTranslator'));
const samples=path.resolve('../build/rpg-ui-samples');
const source=path.join(samples,'SyntheticMZ'),unsupported=path.join(samples,'Unity');
await mkdir(path.join(source,'data'),{recursive:true});await mkdir(path.join(source,'js'),{recursive:true});
await writeFile(path.join(source,'data/System.json'),'{}');await writeFile(path.join(source,'js/rmmz_core.js'),'// marker');
await mkdir(path.join(unsupported,'Synthetic_Data'),{recursive:true});
const env={...process.env,RPT_TOOL_ROOT:root};delete env.ELECTRON_RUN_AS_NODE;
const app=await electron.launch({executablePath:require('electron'),args:[process.cwd()],env,timeout:30000});
try {
  await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].hide());
  const page=await app.firstWindow(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.waitForFunction(()=>document.body.dataset.ready==='true');
  await page.locator('#source-path').fill(source);await page.locator('#source-path').press('Tab');
  await page.waitForFunction(()=>document.querySelector('#engine-note').textContent.includes('RPG Maker MZ'));
  const result=await page.evaluate(p=>window.rpt.project({path:p,source:true}),source);
  assert.equal(result.ok,true);assert.equal(result.value.engine,'rpgmaker');
  await page.locator('#task-options summary').first().click();
  await page.locator('#repairs summary').click();
  for(const task of ['answers','routes','names'])assert.equal(await page.locator(`[data-task=${task}]`).isVisible(),false);
  assert.equal(await page.locator('[data-task=language]').count(),0);
  assert(await page.locator('[data-task=run]').isEnabled());
  assert(await page.locator('[data-task=retranslate]').isEnabled());
  for(const task of ['font','failed','display','layout'])assert(await page.locator(`[data-task=${task}]`).isEnabled());
  await page.locator('#source-path').fill(unsupported);await page.locator('#source-path').press('Tab');
  await page.waitForFunction(()=>document.querySelector('#engine-note').textContent.includes('Unity'));
  assert.equal(await page.locator('#wizard-next').isDisabled(),true);
  assert.equal((await page.evaluate(()=>window.rpt.state())).value.job.running,false);
  assert.deepEqual(errors,[]);
  console.log('Portable backend + Electron: RPG format, task availability, unsupported-engine stop before model selection passed.');
}finally{await app.close();}
