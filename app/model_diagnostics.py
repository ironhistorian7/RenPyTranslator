"""Small model registration facts, without weights, prompts or vocabulary dumps."""
import hashlib
import json
import re
import urllib.request


def _digest(value):
    text=value if isinstance(value,str) else ''
    return {'characters':len(text),'sha256':hashlib.sha256(text.encode('utf-8')).hexdigest()}


def summarize(show, installed=None):
    installed=installed or {}
    result={'available':True}
    details=dict(installed.get('details') or {})
    details.update(show.get('details') or {})
    result['details']={key:details[key] for key in
        ('format','family','families','parameter_size','quantization_level') if key in details}
    info=show.get('model_info') or {}
    result['model_info']={key:value for key,value in info.items()
        if (key in ('general.architecture','general.file_type','general.parameter_count')
            or re.search(r'\.(?:context_length|embedding_length|block_count|head_count|head_count_kv|bos_token_id|eos_token_id|padding_token_id)$',key))
        and isinstance(value,(str,int,float,bool))}
    for key in ('template','system'):
        result[key]=_digest(show.get(key,''))
    result['embedded_template']=_digest(info.get('tokenizer.chat_template',''))
    result['embedded_template']['available_in_api']='tokenizer.chat_template' in info
    template=show.get('template') or ''
    result['template']['has_thinking_switch']=bool(re.search(r'think|reasoning',template,re.I))
    result['template']['has_tool_branch']=bool(re.search(r'\btools?\b',template,re.I))
    allowed={'stop','seed','temperature','top_p','top_k','min_p','repeat_penalty','repeat_last_n',
             'num_ctx','num_predict','presence_penalty','frequency_penalty'}
    parameters={}
    for line in str(show.get('parameters') or '').splitlines():
        parts=line.strip().split(None,1)
        if len(parts)!=2 or parts[0] not in allowed:continue
        key,value=parts
        # The only textual sampling value is a bounded registered stop marker.
        if key=='stop':
            if len(value)<=256:parameters.setdefault(key,[]).append(value)
        else:
            try:parameters[key]=json.loads(value)
            except (TypeError,ValueError):continue
    result['registered_parameters']=parameters
    result['capabilities']=[str(value) for value in show.get('capabilities',[]) if isinstance(value,str)]
    return result


def collect(endpoint, model, installed=None):
    """One metadata-only request per owned runtime; never loads a model."""
    data=json.dumps({'model':model,'verbose':False}).encode('utf-8')
    req=urllib.request.Request(endpoint+'/api/show',data=data,headers={'Content-Type':'application/json'})
    try:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req,timeout=3) as response:show=json.load(response)
        return summarize(show,installed)
    except Exception as exc:
        fallback=summarize({},installed)
        return dict(fallback,available=False,error_type=type(exc).__name__)
