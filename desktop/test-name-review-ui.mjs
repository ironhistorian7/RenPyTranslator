import assert from 'node:assert/strict';
import {mkdtemp,readFile,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
import {_electron as electron} from 'playwright';
const desktop=path.dirname(fileURLToPath(import.meta.url)),require=createRequire(import.meta.url),dir=await mkdtemp(path.join(tmpdir(),'rpt-name-review-'));
const html=await readFile(path.join(desktop,'src/index.html'),'utf8'),dialog=html.match(/<dialog id="name-review"[\s\S]*?<\/dialog>/)?.[0];assert(dialog);
await writeFile(path.join(dir,'index.html'),`<!doctype html><html><body>${dialog}<script src="bundle.js"></script><script>
let submissions=[],reject=true,cancels=0,reviewUI;
window.rpt={nameReview:async req=>{submissions.push(req);return reject?{ok:false,error:'합성 오류'}:{ok:true,value:{running:false,canceling:false}}},cancel:async()=>{cancels++;return {ok:true,value:{running:false,canceling:true}}}};
reviewUI=new Review.NamesReview(job=>reviewUI.render(job));window.showReview=review=>reviewUI.render({running:true,canceling:false,name_review:review});
</script></body></html>`);
await build({entryPoints:[path.join(desktop,'src/name-review.ts')],outfile:path.join(dir,'bundle.js'),bundle:true,platform:'browser',format:'iife',globalName:'Review'});
await writeFile(path.join(dir,'main.cjs'),`const {app,BrowserWindow}=require('electron');app.whenReady().then(()=>{let w=new BrowserWindow({show:false,webPreferences:{contextIsolation:false,nodeIntegration:false}});w.loadFile(${JSON.stringify(path.join(dir,'index.html'))});});app.on('window-all-closed',()=>app.quit());`);
const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;const app=await electron.launch({executablePath:require('electron'),args:[path.join(dir,'main.cjs')],env});
try{
 const page=await app.firstWindow(),review={version:1,fingerprint:'sample',entries:[{key:'a',source:'Alexander II',proposed:'알렉산더 2세',value:'알렉산더 2세',needs_review:true,reason:'check'},{key:'b',source:'花子',proposed:'하나코',value:'하나코',needs_review:false,reason:''}]};
 await page.waitForFunction(()=>typeof window.showReview==='function');await page.evaluate(review=>window.showReview(review),review);assert.equal(await page.locator('#name-review').evaluate(el=>el.matches(':modal')),true);assert.equal(await page.locator('#name-review-details').evaluate(el=>el.open),false);
 await page.locator('#name-review-needed input').fill('알렉산더2세');await page.evaluate(review=>window.showReview(review),review);assert.equal(await page.locator('#name-review-needed input').inputValue(),'알렉산더2세');
 await page.keyboard.press('Escape');assert.equal(await page.locator('#name-review').evaluate(el=>el.open),true);await page.locator('#name-review-automatic-summary').click();await page.locator('#name-review-automatic input').fill('하나');
 await page.locator('#name-review-submit').click();assert.equal(await page.locator('#name-review-error').textContent(),'합성 오류');assert.deepEqual(await page.evaluate(()=>submissions[0]),{fingerprint:'sample',entries:[{key:'a',value:'알렉산더2세'},{key:'b',value:'하나'}]});
 await page.locator('#name-review-needed button').click();assert.equal(await page.locator('#name-review-needed input').inputValue(),'Alexander II');await page.evaluate(()=>{reject=false});await page.locator('#name-review-submit').click();assert.equal(await page.locator('#name-review').evaluate(el=>el.open),false);
 await page.evaluate(review=>window.showReview(review),review);await page.locator('#name-review-cancel').click();assert.equal(await page.evaluate(()=>cancels),1);assert.equal(await page.locator('#name-review').evaluate(el=>el.open),false);console.log('Name review synthetic UI passed.');
}finally{await app.close();}
