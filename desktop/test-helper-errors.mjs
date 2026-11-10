// Synthetic helper failures only: no Electron, game, model or real app state.
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {mkdtempSync,mkdirSync,writeFileSync,readFileSync,readdirSync,rmSync} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const desktop=path.dirname(fileURLToPath(import.meta.url));
const result=await build({entryPoints:[path.join(desktop,'src/helper-errors.ts')],bundle:true,platform:'node',format:'esm',write:false});
const {helperError}=await import('data:text/javascript;base64,'+Buffer.from(result.outputFiles[0].text).toString('base64'));
const temporary=path.resolve(os.tmpdir());
const root=mkdtempSync(path.join(temporary,'rpt-helper-errors-'));
let checked=0;
try{
  const stderr="Traceback (most recent call last):\n  File \"portable_cli.py\", line 1\nFileNotFoundError: missing Japanese path\n[PYI-123:ERROR] Failed to execute script 'portable_cli' due to unhandled exception!\n";
  const failure=helperError(root,'--desktop-project',new Error('Command failed'),stderr);
  assert(failure.message.startsWith('FileNotFoundError: missing Japanese path\n로그: '));
  const files=()=>readdirSync(path.join(root,'logs'));
  assert.equal(files().length,1);
  assert(readFileSync(path.join(root,'logs',files()[0]),'utf8').endsWith(stderr));
  checked++;

  const existing=path.join(root,'structured-error.json');writeFileSync(existing,'{}');
  const reported='Error stage: desktop helper; UnicodeDecodeError: invalid input\nUnicodeDecodeError: bad UTF-8 byte\nDetailed error: '+existing+'\nProject not yet resolved; no resume status verified.\n';
  const reused=helperError(root,'--desktop-project',new Error('Command failed'),reported);
  assert.equal(reused.message,'UnicodeDecodeError: bad UTF-8 byte\n로그: '+existing);
  assert.equal(files().length,1,'Existing structured report is reused');
  checked++;

  const spawn=helperError(root,'--desktop-info',Object.assign(new Error('spawn ENOENT'),{code:'ENOENT'}),'');
  assert(spawn.message.startsWith('spawn ENOENT\n로그: '));checked++;

  const native=helperError(root,'--desktop-info',new Error('Command failed'),'[PYI-12:ERROR] Failed to load Python DLL\n');
  assert(native.message.startsWith('[PYI-12:ERROR] Failed to load Python DLL\n로그: '));checked++;

  const count=files().length;
  const invalid=helperError(root,'--desktop-info',new Error('프로그램 상태 응답을 읽지 못했습니다.'),'');
  assert(invalid.message.startsWith('프로그램 상태 응답을 읽지 못했습니다.\n로그: '));
  assert.equal(files().length,count+1);checked++;

  const blocked=path.join(root,'blocked');mkdirSync(blocked);writeFileSync(path.join(blocked,'logs'),'not a directory');
  const unsaved=helperError(blocked,'--desktop-project',new Error('Command failed'),stderr);
  assert(unsaved.message.startsWith('FileNotFoundError: missing Japanese path\n로그 저장 실패: '));checked++;

  assert(!files().some(name=>name.startsWith('desktop-task-')),'Helper errors do not create task logs');
  console.log('Helper error checks passed: '+checked);
}finally{
  const resolved=path.resolve(root);
  assert.equal(path.dirname(resolved),temporary);
  assert(path.basename(resolved).startsWith('rpt-helper-errors-'));
  rmSync(resolved,{recursive:true,force:true});
}
