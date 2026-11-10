"""Local errors with stage, request identity and full traceback for GUI and CLI."""
from contextvars import ContextVar
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import threading
import traceback
import uuid

_STATE=ContextVar('rpt_operation',default=None)
_LOCK=threading.Lock()


def runtime_settings(cfg):
    result={k:cfg.get(k) for k in ('engine','engine_variant','source_language','model','endpoint','num_ctx','parallel','kv_cache_type','_hardware_policy','_runtime_log','_runtime_pid','_runtime_env','_runtime_launch','_recovery_run_id','_runtime_generation','_cuda_diagnostic_mode')}
    result['context_txt_policy']='disabled_by_gpu_profile' if cfg.get('context_txt_enabled',True) is False else 'adaptive'
    return result


def stage(name, project=None, cfg=None, reset=False, **details):
    state=dict({} if reset else (_STATE.get() or {}),stage=name,**details)
    if project is not None:state['project']=str(project)
    if cfg is not None:
        state['settings']=runtime_settings(cfg)
    _STATE.set(state)
    try:
        from job_stages import public_stage
        public_stage(name,project,cfg)
    except ImportError:
        pass


def report(exc, cfg=None, rows=None, request_id=None, handled=False):
    """Save one full local record, then publish a concise issue without its body."""
    from support_report import classify_issue,phase_name
    with _LOCK:
        previous=getattr(exc,'_rpt_issue_state',None)
        severity='warning' if handled else 'failed'
        if previous=='failed' or previous==severity:
            return getattr(exc,'_rpt_error_file',None)
        existing=getattr(exc,'_rpt_error_file',None)
        if getattr(exc,'_rpt_issue',None) is None:
            state=dict(_STATE.get() or {})
            if cfg:state['settings']=runtime_settings(cfg)
            state.update(error_type=type(exc).__name__,error=str(exc),traceback=''.join(traceback.format_exception(exc)),
                         request_id=request_id,handled=handled,recorded_at=datetime.now(timezone.utc).isoformat())
            if isinstance(getattr(exc,'error_code',None),str):state['error_code']=exc.error_code
            if rows is not None:state['items']=[{k:r.get(k) for k in ('id','file','block','source_file','source_line','usage','usage_evidence')} for r in rows]
            if hasattr(exc,'context_budget'):state['context_budget']=exc.context_budget
            if hasattr(exc,'http_details'):state['http']=exc.http_details
            if hasattr(exc,'native_launch'):state['native_launch']=exc.native_launch
            if hasattr(exc,'failures'):
                state['validation_failures']=[dict(id=f['row'].get('id'),**{k:v for k,v in f.items() if k!='row'}) for f in exc.failures]
                state['accepted_ids']=[r['id'] for r in exc.output]
                state['metrics']=exc.metrics
            project=Path(state['project']) if state.get('project') else None
            from app_paths import ROOT
            directory=Path(cfg['_trace_dir']) if cfg and cfg.get('_trace_dir') else project/'data' if project else ROOT/'logs'
            path=Path(existing) if existing else directory/'errors'/(datetime.now().strftime('%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:10]+'.json')
            issue=dict(classify_issue(state),state=severity,error_id=path.stem,log_path=str(path))
            phase=phase_name(state.get('stage'))
            if phase:issue['phase']=phase
            counts={}
            if hasattr(exc,'failures'):
                counts={'affected':len(exc.failures),'accepted':len(exc.output)}
            elif rows is not None:counts={'affected':len(rows)}
            if counts:issue['counts']=counts
            state.update(error_id=path.stem,issue=dict(issue),
                         resume='Saved entries are retained. An in-flight unsaved batch may repeat.')
            if not existing:
                try:
                    path.parent.mkdir(parents=True,exist_ok=True)
                    path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
                    exc._rpt_error_file=str(path)
                except Exception:
                    issue['log_path']=None
                    issue['detail']+=' 오류 기록을 저장하지 못했습니다.'
            exc._rpt_issue=issue
        issue=dict(exc._rpt_issue,state=severity)
        exc._rpt_issue_state=severity
    import os
    if os.environ.get('RPT_DESKTOP_EVENTS')=='1':
        try:
            from job_events import emit
            emit('issue',**issue)
        except Exception:
            print(issue['title'],file=sys.stderr,flush=True)
    else:
        print(('경고: ' if handled else '오류: ')+issue['title']+' '+issue['detail'],file=sys.stderr,flush=True)
        if issue.get('log_path'):print('Detailed error: '+issue['log_path'],file=sys.stderr,flush=True)
    return getattr(exc,'_rpt_error_file',None)
