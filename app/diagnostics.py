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
    result={k:cfg.get(k) for k in ('model','endpoint','num_ctx','parallel','_runtime_log','_runtime_pid','_runtime_env','_runtime_launch')}
    result['context_txt_policy']='adaptive'
    return result


def stage(name, project=None, cfg=None, reset=False, **details):
    state=dict({} if reset else (_STATE.get() or {}),stage=name,**details)
    if project is not None:state['project']=str(project)
    if cfg is not None:
        state['settings']=runtime_settings(cfg)
    _STATE.set(state)


def report(exc, cfg=None, rows=None, request_id=None, handled=False):
    existing=getattr(exc,'_rpt_error_file',None)
    if existing:
        print('Detailed error: '+existing,file=sys.stderr,flush=True);return existing
    state=dict(_STATE.get() or {})
    if cfg:
        state['settings']=runtime_settings(cfg)
    state.update(error_type=type(exc).__name__,error=str(exc),traceback=''.join(traceback.format_exception(exc)),
                 request_id=request_id,handled=handled,recorded_at=datetime.now(timezone.utc).isoformat())
    if rows is not None:state['items']=[{k:r.get(k) for k in ('id','file','block','source_file','source_line','usage','usage_evidence')} for r in rows]
    if hasattr(exc,'context_budget'):state['context_budget']=exc.context_budget
    if hasattr(exc,'http_details'):state['http']=exc.http_details
    if hasattr(exc,'native_launch'):state['native_launch']=exc.native_launch
    if hasattr(exc,'failures'):
        state['validation_failures']=[{'id':f['row'].get('id'),'error':f['error']} for f in exc.failures]
        state['accepted_ids']=[r['id'] for r in exc.output]
        state['metrics']=exc.metrics
    project=Path(state['project']) if state.get('project') else None
    from app_paths import ROOT
    directory=Path(cfg['_trace_dir']) if cfg and cfg.get('_trace_dir') else project/'data' if project else ROOT/'logs'
    state['resume']='Saved entries are retained. Retry resumes them after this error is resolved; an in-flight unsaved batch may repeat.' if project or (cfg and cfg.get('_trace_dir')) else 'Project not yet resolved; no resume status verified.'
    path=directory/'errors'/(datetime.now().strftime('%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:10]+'.json')
    try:
        with _LOCK:
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
        exc._rpt_error_file=str(path)
    except Exception as log_error:
        print('Could not save error log: '+str(log_error),file=sys.stderr,flush=True)
    print('Error stage: '+state.get('stage','unknown')+'; '+type(exc).__name__+': '+str(exc),file=sys.stderr,flush=True)
    print(state['traceback'],file=sys.stderr,flush=True)
    for failure in state.get('validation_failures',[]):
        print('Item '+str(failure['id'])+': '+failure['error'],file=sys.stderr,flush=True)
    print('Detailed error: '+str(path),file=sys.stderr,flush=True)
    print(state['resume'],file=sys.stderr,flush=True)
    return str(path)
