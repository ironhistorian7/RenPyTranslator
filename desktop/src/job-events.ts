import type {Job,JobIssue,JobWarning} from './types';

const numeric=(value:unknown):value is number=>typeof value==='number'&&Number.isFinite(value)&&value>=0;
const text=(value:unknown)=>typeof value==='string'?value:'';
const identifiers=(value:unknown)=>Array.isArray(value)?value.filter((v):v is string=>typeof v==='string'&&/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$/.test(v)):[];

function warnings(job:Job,event:Record<string,unknown>):JobWarning[]{
  const all=[...(job.warnings||[])],code=text(event.code)||'unknown';
  const index=all.findIndex(w=>w.code===code),previous=index<0?undefined:all[index];
  const errorIDs=identifiers(event.error_ids||[event.error_id]);
  if(!numeric(event.occurrences)&&errorIDs.length&&errorIDs.every(id=>previous?.error_ids.includes(id)))return all;
  const itemIDs=[...new Set([...(previous?.item_ids||[]),...identifiers(event.item_ids)])].slice(0,24);
  const next:JobWarning={code,title:text(event.title)||previous?.title||'확인할 사항',
    detail:text(event.detail)||previous?.detail,
    occurrences:numeric(event.occurrences)?Math.max(previous?.occurrences||0,event.occurrences):(previous?.occurrences||0)+(numeric(event.count)?event.count:1),
    item_count:numeric(event.item_count)?Math.max(previous?.item_count||0,event.item_count):previous?.item_count,
    item_ids:itemIDs,error_ids:[...new Set([...(previous?.error_ids||[]),...errorIDs])].slice(-8),
    log_path:text(event.log_path)||previous?.log_path||null};
  if(index<0)all.push(next);else all[index]=next;
  return all;
}

export function warningText(job:Job):string{
  const lines=['RenPyTranslator 작업 요약',`결과: ${job.running?'진행 중':job.exitCode===0?'완료':job.exitCode===130?'취소':'작업 중단'}`];
  if(job.successful!==undefined)lines.push(`번역 완료: ${job.successful}개`);
  if(job.partial!==undefined)lines.push(`부분 번역: ${job.partial}개`);
  if(job.original_kept!==undefined)lines.push(`원문 유지: ${job.original_kept}개`);
  if(job.cached!==undefined)lines.push(`기존 번역 재사용: ${job.cached}개`);
  lines.push('','주의 사항');
  for(const item of job.warnings||[]){
    lines.push(`- ${item.title} [${item.code}]: ${item.occurrences}회${item.item_count!==undefined?` · ${item.item_count}개 항목`:''}`);
    if(item.item_ids.length)lines.push(`  항목 ID${item.item_count!==undefined&&item.item_count>item.item_ids.length?'(예시)':''}: ${item.item_ids.join(', ')}`);
    if(item.error_ids.length)lines.push(`  오류 ID: ${item.error_ids.join(', ')}`);
  }
  return lines.join('\n');
}

export function countSummary(job:Job):string{
  const values=[];
  if(job.successful!==undefined)values.push(`번역 완료 ${job.successful.toLocaleString()}개`);
  if(job.partial!==undefined)values.push(`부분 번역 ${job.partial.toLocaleString()}개`);
  if(job.original_kept)values.push(`원문 유지 ${job.original_kept.toLocaleString()}개`);
  if(job.cached)values.push(`기존 번역 ${job.cached.toLocaleString()}개 재사용`);
  if(job.warnings?.length)values.push(`주의 기록 ${job.warnings.reduce((sum,item)=>sum+item.occurrences,0).toLocaleString()}건`);
  return values.join(' · ');
}

export function jobEvent(job:Job,event:Record<string,unknown>,now=Date.now()):Partial<Job>|null{
  const kind=event.event;
  if(kind==='notice'){
    const message=text(event.text)||text(event.message)||text(event.title)||text(event.label)||text(event.detail);
    return message?{notices:[...new Set([...(job.notices||[]),message])].slice(-10)}:null;
  }
  if(kind==='warning_summary'){
    let next=job;
    for(const item of Array.isArray(event.warnings)?event.warnings:[]){
      if(item&&typeof item==='object')next={...next,warnings:warnings(next,item as Record<string,unknown>)};
    }
    return {warnings:next.warnings||[],updated:now};
  }
  if(kind==='warning'||kind==='issue'&&event.state!=='failed'){
    return {warnings:warnings(job,event),issue:job.issue?.state==='failed'?job.issue:null,updated:now};
  }
  if(kind==='issue'){
    const state=event.state==='retrying'||event.state==='failed'?event.state:'warning';
    const issue:JobIssue={code:text(event.code)||'unknown',title:text(event.title)||'오류가 발생했습니다.',
      detail:text(event.detail),state,error_id:text(event.error_id)||null,log_path:text(event.log_path)||null};
    return {issue,updated:now};
  }
  if(kind==='recovery'){
    const waiting=event.state==='waiting';
    return {stage:waiting?'재시도 대기 중':text(event.label)||job.phase_label||'작업을 다시 시작하는 중',eta_seconds:null,updated:now,
      recovery:{state:waiting?'waiting':'resuming',delay_seconds:numeric(event.delay_seconds)?event.delay_seconds:undefined,
        next_retry_at:text(event.next_retry_at)||null,attempt:numeric(event.attempt)?event.attempt:undefined,
        total_restarts:numeric(event.total_attempts)?event.total_attempts:numeric(event.total_restarts)?event.total_restarts:undefined,
        max_attempts:numeric(event.max_attempts)?event.max_attempts:undefined}};
  }
  if(kind!=='stage'&&kind!=='progress')return null;
  const phase=text(event.phase)||job.phase||'work';
  const changed=phase!==job.phase;
  const label=text(event.label)||(changed?phase:job.phase_label||job.stage);
  const next:Partial<Job>={phase,phase_label:label,stage:label,updated:now,recovery:null};
  if(changed)Object.assign(next,{completed:null,total:null,unit:null,eta_seconds:null});
  if(numeric(event.completed))next.completed=event.completed;
  if(numeric(event.total))next.total=event.total;
  if(typeof event.unit==='string')next.unit=event.unit;
  if(event.eta_seconds===null||numeric(event.eta_seconds))next.eta_seconds=event.eta_seconds;
  if(numeric(event.elapsed_seconds))next.elapsed_seconds=event.elapsed_seconds;
  for(const key of ['successful','partial','original_kept','cached'] as const)if(numeric(event[key]))next[key]=event[key];
  if(job.issue&&job.issue.state!=='failed')next.issue=null;
  return next;
}
