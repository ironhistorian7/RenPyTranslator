import {existsSync,mkdirSync,writeFileSync} from 'node:fs';
import path from 'node:path';

let serial=0;

export function helperError(root:string,command:string,error:Error,stderr:string):Error{
  const lines=stderr.split(/\r?\n/).map(line=>line.trim()).filter(Boolean);
  const exception=[...lines].reverse().find(line=>/^(?:[\w.]*Error|[\w.]*Exception|KeyboardInterrupt|SystemExit):/.test(line));
  const stage=lines.find(line=>line.startsWith('Error stage: '));
  const useful=lines.filter(line=>!/^\[PYI-\d+:ERROR\] Failed to execute script .* due to unhandled exception!$/.test(line)
    &&!line.startsWith('Detailed error: ')
    &&!line.startsWith('Saved entries are retained.')
    &&!line.startsWith('Project not yet resolved;'));
  const message=exception||stage||useful.at(-1)||error.message.split(/\r?\n/)[0];
  const existing=[...lines].reverse().find(line=>line.startsWith('Detailed error: '))?.slice('Detailed error: '.length);
  let saved=existing&&existsSync(existing)?existing:'';
  if(!saved){
    try{
      const folder=path.join(root,'logs');mkdirSync(folder,{recursive:true});
      saved=path.join(folder,`desktop-helper-${new Date().toISOString().replace(/[:.]/g,'-')}-${process.pid}-${++serial}.log`);
      const detail=error as Error&{code?:string|number;signal?:string;killed?:boolean};
      writeFileSync(saved,JSON.stringify({command,code:detail.code,signal:detail.signal,killed:detail.killed},null,2)
        +'\n\n'+error.message.split(/\r?\n/)[0]+'\n\n'+stderr,'utf8');
    }catch(logError){
      return new Error(message+'\n로그 저장 실패: '+(logError instanceof Error?logError.message:String(logError)));
    }
  }
  return new Error(message+'\n로그: '+saved);
}
