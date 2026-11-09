"""Unload only models used by this CLI, including on Ctrl+C and exceptions."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import signal
import sys
import urllib.request

_active=ContextVar('rpt_model_session',default=None)

def track_request(endpoint,route,body):
    session=_active.get()
    if session is not None and route in ('/api/chat','/api/generate') and body.get('model'):
        session.add((endpoint.rstrip('/'),body['model']))
        # Fallback for abrupt process termination where Python finally cannot run.
        return dict(body,keep_alive='30s')
    return body

def unload_model(endpoint,model):
    req=urllib.request.Request(endpoint+'/api/generate',
        data=json.dumps({'model':model,'keep_alive':0,'stream':False}).encode(),
        headers={'Content-Type':'application/json'})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req,timeout=15) as response:
        result=json.load(response)
    if result.get('error'):raise RuntimeError(result['error'])

@contextmanager
def model_session():
    models=set();token=_active.set(models)
    handlers={}
    def cancel(signum,frame):raise KeyboardInterrupt
    for name in ('SIGTERM','SIGBREAK'):
        sig=getattr(signal,name,None)
        if sig is not None:
            try:handlers[sig]=signal.signal(sig,cancel)
            except ValueError:pass
    try:
        yield
    finally:
        _active.reset(token)
        # A second Ctrl+C must not interrupt the short unload request.
        previous=None
        try:
            previous=signal.signal(signal.SIGINT,signal.SIG_IGN)
        except ValueError:pass  # A caller may run outside the main thread.
        try:
            for endpoint,model in sorted(models):
                try:
                    unload_model(endpoint,model)
                    print('Unloaded model: '+model,flush=True)
                except Exception as exc:
                    print('Model unload failed for '+model+': '+str(exc)+
                          ' (30s idle expiry remains active)',file=sys.stderr,flush=True)
        finally:
            if previous is not None:signal.signal(signal.SIGINT,previous)
            for sig,handler in handlers.items():signal.signal(sig,handler)
