"""Bound final requests, using only the tokenizer of our owned local runner."""
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import urllib.request
from source_context import token_estimate

CTX = 16384
RESERVE = 128
_LOCK = threading.Lock()
_COUNTERS = {}


def post(endpoint, route, body):
    from model_runtime import track_request
    body = track_request(endpoint, route, body)
    request = urllib.request.Request(endpoint+route, json.dumps(body).encode(), {'Content-Type':'application/json'})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=120) as response:
        return json.load(response)


class Counter:
    def __init__(self, cfg):
        self.endpoint = None; self.cache = {}; self.lock = threading.Lock()
        self.reason = 'No owned tokenizer; conservative estimate (not actual tokens).'
        log = cfg.get('_runtime_log')
        if not log: return
        try:
            # An empty Ollama prompt loads this model without generating text.
            post(cfg['endpoint'], '/api/generate', {'model':cfg['model'], 'prompt':'', 'stream':False,
                 'keep_alive':'30s', 'options':{'num_ctx':cfg.get('num_ctx', CTX)}})
            text = Path(log).read_text(encoding='utf-8', errors='replace')
            ports = re.findall(r'--port\s+(\d+)', text)
            if not ports: raise ValueError('Owned runner did not expose its tokenizer port')
            self.endpoint = 'http://127.0.0.1:'+ports[-1]
            self.count('Tokenizer probe.')
            self.reason = None
        except Exception as exc:
            self.endpoint = None
            self.reason = type(exc).__name__+': '+str(exc)
            print('Tokenizer unavailable; using a conservative estimate. '+self.reason,flush=True)

    def count(self, text):
        if not self.endpoint: return math.ceil(token_estimate(text)*1.35)
        key = hashlib.sha256(text.encode()).hexdigest()
        with self.lock:
            if key not in self.cache:
                self.cache[key] = len(post(self.endpoint, '/tokenize', {'content':text,'add_special':False})['tokens'])
                if len(self.cache)>1024: self.cache.pop(next(iter(self.cache)))
            return self.cache[key]

    @property
    def method(self): return 'owned_model_tokenizer' if self.endpoint else 'conservative_estimate'


def counter(cfg):
    key = (cfg.get('_runtime_log'), cfg.get('endpoint'), cfg.get('model'), cfg.get('num_ctx',CTX))
    with _LOCK:
        if key not in _COUNTERS: _COUNTERS[key] = Counter(cfg)
        return _COUNTERS[key]


def forget(cfg):
    with _LOCK:
        for key in list(_COUNTERS):
            if key[0] == cfg.get('_runtime_log'): del _COUNTERS[key]


def guidance_tokens(context, count):
    notes = {k:context[k] for k in ('translation_guidance','current_state') if context.get(k)}
    return count(json.dumps(notes,ensure_ascii=False)) if notes else 0


class InputBudgetError(RuntimeError): pass


class BatchNeedsSplit(InputBudgetError):
    """Preflight only: retry smaller batches without spending a model call."""


def fit(context, cfg, build):
    ctx = copy.deepcopy(context); audit = ctx.pop('_context_audit', {})
    measure = counter(cfg); dropped=[]
    def inspect(candidate):
        body=build(candidate)
        content='\n'.join(m['content'] for m in body['messages'])
        if body.get('format'):content+='\n'+json.dumps(body['format'],ensure_ascii=False)
        size=measure.count(content);output=int(body['options']['num_predict'])
        window=int(body['options']['num_ctx'])
        return body,size,output,window,size+output+RESERVE<=window
    checked=inspect(ctx)
    def metadata():
        return dict(audit,tokenizer=measure.method,tokenizer_warning=measure.reason,
                    txt_budget='adaptive',txt_tokens=guidance_tokens(ctx,measure.count),
                    prompt_content_tokens=checked[1],output_reserved=checked[2],
                    framing_reserved=RESERVE,num_ctx_requested=checked[3],dropped=list(dropped),
                    context_sha256=hashlib.sha256(json.dumps(ctx,sort_keys=True,ensure_ascii=False).encode()).hexdigest())
    if not checked[-1] and cfg.get('_allow_batch_split'):
        exc=BatchNeedsSplit(f'Batch input {checked[1]} + output {checked[2]} + framing {RESERVE} > {checked[3]}; splitting before removing context.')
        exc.context_budget=metadata();raise exc

    def reduce_units(units, setter, part):
        """Find a fitting whole-unit prefix in O(log n) tokenizer requests."""
        nonlocal checked
        if checked[-1] or not units:return
        original=copy.deepcopy(ctx)
        def candidate(keep):
            value=copy.deepcopy(original);setter(value,units[:keep]);return value
        empty=candidate(0);probe=inspect(empty)
        if not probe[-1]:
            keep=0;chosen=empty;checked=probe
        else:
            low=0;high=len(units)-1;keep=0;chosen=empty;checked=probe
            while low<=high:
                middle=(low+high)//2;value=candidate(middle);probe=inspect(value)
                if probe[-1]:
                    keep=middle;chosen=value;checked=probe;low=middle+1
                else:high=middle-1
        removed=units[keep:]
        dropped.append({'part':part,'reason':'request_window','removed_units':len(removed),
                        'kept_units':keep,'removed_sha256':hashlib.sha256(json.dumps(removed,ensure_ascii=False).encode()).hexdigest()})
        ctx.clear();ctx.update(chosen)

    passage=ctx.get('source_passage',[])
    targets=[i for i,item in enumerate(passage) if 'target_id' in item]
    # Include closest neighbors first. Every retained passage is contiguous;
    # internal cached entries between targets are never removed.
    left=min(targets) if targets else len(passage)
    right=max(targets) if targets else len(passage)-1
    edges=sorted([i for i,p in enumerate(passage) if 'text' in p and (i<left or i>right)],
                 key=lambda i:(min(abs(i-t) for t in targets) if targets else len(passage)-i,i))
    near={i for i in (left-1,right+1) if i in edges}
    far=[i for i in edges if i not in near]
    def set_far(value,keep):
        value['source_passage']=[p for i,p in enumerate(passage) if i not in far or i in keep]
    reduce_units(far,set_far,'source_passage_distant')
    for key in ('global','file','scene','terms'):
        value=ctx.get('translation_guidance',{}).get(key)
        if not value:continue
        units=value if isinstance(value,list) else value.splitlines()
        def set_notes(candidate,keep,key=key,is_list=isinstance(value,list)):
            notes=candidate.get('translation_guidance',{})
            if keep:notes[key]=keep if is_list else '\n'.join(keep)
            else:notes.pop(key,None)
            if not notes:candidate.pop('translation_guidance',None)
        reduce_units(units,set_notes,'TXT:'+key)
    # Only after background notes are exhausted can the immediate neighbor go.
    passage=ctx.get('source_passage',[])
    targets=[i for i,item in enumerate(passage) if 'target_id' in item]
    left=min(targets) if targets else len(passage)
    right=max(targets) if targets else len(passage)-1
    edges=[i for i,p in enumerate(passage) if 'text' in p and (i<left or i>right)]
    def set_edges(value,keep):
        value['source_passage']=[p for i,p in enumerate(passage) if i not in edges or i in keep]
    reduce_units(edges,set_edges,'source_passage_nearest')
    if ctx.get('current_state'):
        def set_state(value,keep):
            if not keep:value.pop('current_state',None)
        reduce_units([ctx['current_state']],set_state,'TXT:current_state')
    body,size,output,window,ok=checked
    if not ok:
        exc=InputBudgetError(f'Input does not fit: {size} + output {output} + framing {RESERVE} > {window}. Source text and internal dialogue were not truncated.')
        exc.context_budget=metadata();raise exc
    return body,metadata()
