import {_electron as electron} from 'playwright';
import {build} from 'esbuild';
import {createRequire} from 'node:module';
import {mkdtemp,readFile,writeFile,rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
const require=createRequire(import.meta.url),dir=await mkdtemp(path.join(os.tmpdir(),'rpt-review-test-'));
let app;
try {
  const html=await readFile('src/index.html','utf8');
  const dialog=html.match(/<dialog id="translation-review"[\s\S]*?<\/dialog>/)?.[0];
  assert(dialog);
  await writeFile(path.join(dir,'fixture.html'),`<!doctype html><html><body>${dialog}</body></html>`);
  await writeFile(path.join(dir,'main.cjs'),`const {app,BrowserWindow}=require('electron');app.commandLine.appendSwitch('disable-gpu');app.whenReady().then(()=>{const w=new BrowserWindow({show:false,webPreferences:{contextIsolation:true}});w.loadFile(${JSON.stringify(path.join(dir,'fixture.html'))});});`);
  const compiled=await build({stdin:{contents:"import {TranslationReviewPanel} from './src/translation-review';window.TranslationReviewPanel=TranslationReviewPanel;",resolveDir:process.cwd(),loader:'ts'},bundle:true,write:false,format:'iife',platform:'browser',logLevel:'silent'});
  const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
  app=await electron.launch({executablePath:require('electron'),args:[path.join(dir,'main.cjs')],env,timeout:30000});
  const page=await app.firstWindow();await page.addScriptTag({content:compiled.outputFiles[0].text});
  await page.evaluate(()=>{
    window.submissions=[];window.cancels=0;
    window.rpt={translationReview:async payload=>{window.submissions.push(payload);return {ok:true,value:{running:false}};},cancel:async()=>{window.cancels++;return {ok:true,value:{running:false}};}};
    window.panel=new window.TranslationReviewPanel(()=>{});
    window.panel.render({running:true,canceling:false,translation_review:{fingerprint:'synthetic',entries:[{id:'1',source:'source one',current:'original'},{id:'2',source:'source two',current:'draft'},{id:'3',source:'source three',current:'keep'}]}});
  });
  const rows=page.locator('.translation-review-row');
  await rows.nth(0).locator('textarea').fill('');
  assert.equal(await rows.nth(0).locator('input').isChecked(),true);
  const value='  첫 줄 <tag>\n둘째 줄  ';
  await rows.nth(1).locator('textarea').fill(value);
  assert.equal(await rows.nth(1).locator('input').isChecked(),true);
  await rows.nth(2).locator('textarea').fill('edited but skipped');await rows.nth(2).locator('input').uncheck();
  await page.locator('#translation-review-submit').click();await page.waitForFunction(()=>window.submissions.length===1);
  assert.deepEqual(await page.evaluate(()=>window.submissions[0]),{fingerprint:'synthetic',entries:[{id:'1',apply:true,text:''},{id:'2',apply:true,text:value},{id:'3',apply:false,text:'edited but skipped'}]});
  await page.locator('#translation-review-cancel').click();await page.waitForFunction(()=>window.cancels===1);
  assert.equal(await page.evaluate(()=>window.submissions.length),1);
  console.log('Translation review isolated frontend: input, empty, multiline, skip, submit and cancel passed');
} finally {if(app)await app.close();await rm(dir,{recursive:true,force:true});}
