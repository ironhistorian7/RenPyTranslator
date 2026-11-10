import type {Job,JobIssue} from './types';

const numeric=(value:unknown):value is number=>typeof value==='number'&&Number.isFinite(value)&&value>=0;
const text=(value:unknown)=>typeof value==='string'?value:'';

export function jobEvent(job:Job,event:Record<string,unknown>,now=Date.now()):Partial<Job>|null{
  const kind=event.event;
  if(kind==='notice'){
    const message=text(event.text)||text(event.message)||text(event.title)||text(event.label)||text(event.detail);
    return message?{notices:[...new Set([...(job.notices||[]),message])].slice(-10)}:null;
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
        next_retry_at:text(event.next_retry_at)||null,attempt:numeric(event.attempt)?event.attempt:undefined}};
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
  for(const key of ['successful','original_kept','cached'] as const)if(numeric(event[key]))next[key]=event[key];
  // A handled transient error belongs in the detailed record after progress
  // resumes. Actual read failures remain visible in the final summary.
  if(job.issue&&job.issue.state!=='failed'&&job.issue.code!=='rpg_read_failure')next.issue=null;
  return next;
}
