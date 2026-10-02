"""One local metadata registration; no game or model inference."""
import json
from pathlib import Path
import sys
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from hy_backend import runtime
from model_store import HY_TEMPLATE,manifests,read_json,blob
alias='rpt-registration-smoke:latest'
assert alias not in manifests()
weight=next(x for x in read_json(manifests()['rpt-hymt2-7b:q6_k'],{})['layers'] if x['mediaType']=='application/vnd.ollama.image.model')
original=blob(weight['digest']);before=original.stat().st_size
with runtime({'model':alias,'parallel':1},require_model=False) as cfg:
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def call(route,body,method='POST'):
        req=urllib.request.Request(cfg['endpoint']+route,json.dumps(body).encode(),{'Content-Type':'application/json'},method=method)
        with opener.open(req,timeout=30) as r:return json.load(r) if r.status!=200 or route!='/api/delete' else r.read()
    try:
        result=call('/api/create',{'model':alias,'files':{'model.gguf':weight['digest']},'template':HY_TEMPLATE,'stream':False})
        assert result.get('status')=='success',result
        assert alias in manifests()
        print('Real Ollama GGUF registration OK; no inference.')
    finally:
        if alias in manifests():call('/api/delete',{'model':alias},'DELETE')
assert alias not in manifests()
assert original.is_file() and original.stat().st_size==before
print('Temporary alias removed; original shared model preserved; owned server stopped.')
