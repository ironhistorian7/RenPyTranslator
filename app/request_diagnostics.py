"""Bounded technical summaries; never duplicate prompt or response text."""
from collections import Counter
import hashlib
from itertools import groupby
import json
import re
import urllib.error


_REPEAT_LIMIT=re.compile(r'prediction\s+aborted[\s:;,.-]*token\s+repeat\s+limit\s+reached',re.I)
_RUNTIME_FAULT=re.compile(r'CUDA\s+(?:error|failure)|illegal\s+memory\s+access|device.side\s+assert|'
                          r'out\s+of\s+memory|runner\s+(?:process\s+)?(?:has\s+)?(?:terminated|crashed)|'
                          r'access\s+violation|0xc0000409|0xc0000005',re.I)
_OPTIONS=frozenset(('num_ctx','num_predict','temperature','top_k','top_p','min_p','typical_p',
    'repeat_penalty','repeat_last_n','presence_penalty','frequency_penalty','seed','num_thread',
    'num_gpu','main_gpu','mirostat','mirostat_eta','mirostat_tau','tfs_z','penalize_newline','stop'))


def mark_transport_failure(exc, endpoint, route):
    """Attach bounded network evidence without consuming an HTTP error twice."""
    exc._rpt_transport = True
    exc.transport_endpoint = endpoint
    exc.transport_route = route
    if isinstance(exc, urllib.error.HTTPError) and not hasattr(exc, 'http_details'):
        try:
            raw = exc.read(1048577)
            exc.http_details = dict(status=exc.code, url=exc.url,
                headers=dict(exc.headers or {}), body=raw[:1048576].decode('utf-8', 'replace'),
                body_truncated=len(raw) > 1048576)
        except Exception as read_error:
            exc.http_details = dict(status=exc.code, read_error=str(read_error))
        finally:
            exc.close()
    return exc


def is_token_repeat_limit(exc):
    """Ollama's per-prediction repetition stop is not a crashed model server.

    Inspect the already captured body only. Reading HTTPError's stream here would
    consume the evidence needed by the original request diagnostics.
    """
    details=getattr(exc,'http_details',None)
    if not isinstance(details,dict):return False
    status=getattr(exc,'code',details.get('status'))
    body=details.get('body')
    return status==500 and isinstance(body,str) and bool(_REPEAT_LIMIT.search(body)) and not _RUNTIME_FAULT.search(body)


def classify_request_failure(exc):
    if is_token_repeat_limit(exc):
        return dict(code='token_repeat_limit',category='generation',restart_runtime=False)
    status=getattr(exc,'code',None)
    transport=bool(getattr(exc,'_rpt_transport',False))
    retryable=transport and (not isinstance(exc,urllib.error.HTTPError) or status in (500,502,503,504))
    return dict(code='model_server' if status in (500,502,503,504) else 'request_failed',
                category='transport',restart_runtime=retryable)


def _digest(text):
    raw=text.encode('utf-8','replace')
    return dict(characters=len(text),bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())


def _option_value(value):
    if value is None or isinstance(value,(bool,int,float)):return value
    if isinstance(value,str):return value[:128]
    if isinstance(value,(list,tuple)):
        return [_option_value(item) for item in value[:32] if item is None or isinstance(item,(bool,int,float,str))]
    return None


def request_metadata(body,*,protocol=None,input_count=None):
    """Describe the actual wire request without retaining its text or secrets."""
    body=body if isinstance(body,dict) else {}
    options=body.get('options')
    options=options if isinstance(options,dict) else {}
    messages=body.get('messages')
    messages=messages if isinstance(messages,list) else []
    content=[item.get('content','') for item in messages if isinstance(item,dict)]
    content=[text for text in content if isinstance(text,str)]
    # Length-delimited messages avoid identical hashes for different boundaries.
    digest=hashlib.sha256();prompt_bytes=0
    for text in content:
        raw=text.encode('utf-8','replace');prompt_bytes+=len(raw)
        digest.update(str(len(text)).encode('ascii')+b':');digest.update(raw)
    result=dict(model=str(body.get('model',''))[:256],
                options={key:_option_value(value) for key,value in options.items() if key in _OPTIONS},
                option_keys=sorted(str(key)[:64] for key in options)[:64],messages=len(messages),
                prompt_characters=sum(map(len,content)),
                prompt_bytes=prompt_bytes,prompt_sha256=digest.hexdigest())
    result['roles']=[item.get('role') if item.get('role') in ('system','user','assistant','tool') else 'other'
                     for item in messages if isinstance(item,dict)][:32]
    for key in ('think','stream','keep_alive'):
        value=body.get(key)
        if value is None or isinstance(value,(bool,int,float,str)):result[key]=_option_value(value)
    if isinstance(protocol,str):result['protocol']=protocol[:128]
    if isinstance(input_count,int) and input_count>=0:result['input_count']=input_count
    schema=body.get('format')
    if isinstance(schema,dict):
        properties=schema.get('properties')
        result['format']=dict(kind='schema',sha256=hashlib.sha256(json.dumps(schema,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest(),
                              keys=sorted(str(key)[:64] for key in schema)[:64],
                              property_keys=sorted(str(key)[:64] for key in properties)[:64] if isinstance(properties,dict) else [])
    elif isinstance(schema,str):result['format']=dict(kind=schema[:64])
    else:result['format']=dict(kind='none')
    return result


def _repetition(text):
    tail=text[-4096:]
    best_length=0;best_count=0;best_coverage=0
    for size in range(1,min(256,len(tail)//2)+1):
        unit=tail[-size:];count=1
        while count*size+size<=len(tail) and tail[-(count+1)*size:-count*size]==unit:count+=1
        if count>=2 and count*size>best_coverage:
            best_length=size;best_count=count;best_coverage=count*size
    chunks=[tail[index:index+128] for index in range(0,len(tail)-127,128)]
    lines=[line.strip() for line in tail.splitlines() if line.strip()]
    return dict(analysed_characters=len(tail),tail_sha256=hashlib.sha256(tail.encode('utf-8','replace')).hexdigest(),
                max_character_run=max((sum(1 for _ in group) for _,group in groupby(tail)),default=0),
                max_identical_line_count=max(Counter(lines).values(),default=0),
                max_identical_chunk_count=max(Counter(chunks).values(),default=0),
                suffix_unit_characters=best_length,suffix_repeat_count=best_count)


def response_metadata(response):
    response=response if isinstance(response,dict) else {}
    message=response.get('message')
    message=message if isinstance(message,dict) else {}
    content=message.get('content','');content=content if isinstance(content,str) else ''
    thinking=message.get('thinking','');thinking=thinking if isinstance(thinking,str) else ''
    result={key:response[key] for key in ('eval_count','prompt_eval_count','total_duration','load_duration',
             'prompt_eval_duration','eval_duration') if isinstance(response.get(key),(int,float))}
    if isinstance(response.get('done_reason'),str):result['done_reason']=response['done_reason'][:64]
    result.update(content=_digest(content),thinking=_digest(thinking),repetition=_repetition(content))
    result['scripts']={name:len(re.findall(pattern,content)) for name,pattern in (
        ('hangul',r'[\uac00-\ud7a3]'),('hiragana',r'[\u3040-\u309f]'),
        ('katakana',r'[\u30a0-\u30ff]'),('han',r'[\u3400-\u9fff]'),('latin',r'[A-Za-z]'))}
    return result
