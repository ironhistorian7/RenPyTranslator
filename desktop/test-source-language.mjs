// Isolated compiled-backend UI check. Only synthetic project metadata is read.
import {_electron as electron} from 'playwright';
import {createRequire} from 'node:module';
import {mkdir,writeFile,copyFile} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url);
const root=path.resolve(process.argv[2]);
assert(root.includes(path.join('build','local-source-language','dist')));
await copyFile('../runtime-lock.json',path.join(root,'runtime-lock.json'));
const project=path.join(root,'data','projects','Synthetic');
const output=path.join(root,'project','Synthetic-kr');
await mkdir(project,{recursive:true});await mkdir(output,{recursive:true});
await writeFile(path.join(project,'project.json'),JSON.stringify({source_language:'japanese'}));
await writeFile(path.join(output,'project-link.json'),JSON.stringify({project:'../../data/projects/Synthetic'}));
const env={...process.env,RPT_TOOL_ROOT:root};delete env.ELECTRON_RUN_AS_NODE;
const app=await electron.launch({executablePath:require('electron'),args:[process.cwd()],env,timeout:30000});
try{
  const page=await app.firstWindow();const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.waitForFunction(()=>!document.querySelector('#start').disabled);
  assert.deepEqual(await page.locator('#source-language option').allTextContents(),['영어','일본어']);
  await page.locator('input[name=source][value=project]').check();
  await page.locator('#source-path').fill(output);await page.locator('#source-path').press('Tab');
  await page.waitForFunction(()=>document.querySelector('#source-language').value==='japanese'&&!document.querySelector('#start').disabled);
  const metadata=await page.evaluate(p=>window.rpt.project({path:p,source:false}),output);
  assert.deepEqual(metadata,{ok:true,value:{source_language:'japanese'}});
  await page.locator('[data-page=advanced]').click();
  assert.equal(await page.locator('#source-language').inputValue(),'japanese');
  assert.equal(await page.locator('#start').isDisabled(),true);
  await page.locator('[data-page=basic]').click();
  await page.locator('#source-language').selectOption('english');
  await app.evaluate(({ipcMain})=>{
    ipcMain.removeHandler('rpt:start');
    ipcMain.handle('rpt:start',(_event,request)=>{
      globalThis.sourceLanguageTest=request;
      return {ok:true,value:{running:true,canceling:false,exitCode:null,result:null,stage:'synthetic',completed:0,total:1,started:Date.now()}};
    });
  });
  await page.locator('#start').click();
  const request=await app.evaluate(()=>globalThis.sourceLanguageTest);
  assert.equal(request.settings.source_language,'english');
  assert.deepEqual(request.tasks,['run']);
  assert.equal(await page.locator('#source-language').isDisabled(),true);
  assert.deepEqual(errors,[]);
  console.log('Compiled backend + Electron: explicit English/Japanese selection, project restoration, advanced mode and worker payload OK. No translation/game/model execution.');
}finally{await app.close();}
