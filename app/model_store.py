"""Portable model catalogue and local store. Never reads game/project folders."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from app_paths import ROOT

RECOMMENDED = 'tencent/Hy-MT2-7B-GGUF'
RECOMMENDED_FILE = 'HY-MT2-7B-Q6_K.gguf'
HY_TEMPLATE = '{{ range .Messages }}{{ if eq .Role "user" }}<|startoftext|>{{ .Content }}<|extra_0|>{{ else if eq .Role "assistant" }}{{ .Content }}<|eos|>{{ end }}{{ end }}{{ .Response }}'


def read_json(path, default):
    try: return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError): return default


def save(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)


def model_root(): return ROOT/'models'
def records(): return read_json(ROOT/'data/model-library.json',{})
def selected(): return read_json(ROOT/'data/model-choice.json',{}).get('selected')


def bounded(path):
    base=model_root().resolve();path=Path(path)
    if not path.resolve().is_relative_to(base): raise ValueError('모델 폴더 밖의 파일은 처리할 수 없습니다.')
    for part in [path,*path.parents]:
        if part==base:break
        if part.is_symlink() or part.is_junction():raise ValueError('연결된 모델 폴더는 지원하지 않습니다.')
    return path


def manifests():
    base=model_root()/'ollama/manifests'
    if not base.exists():return {}
    return {str(p.relative_to(base/'registry.ollama.ai/library')).replace('\\','/').rsplit('/',1)[0]+':'+p.name:p
            for p in (base/'registry.ollama.ai/library').rglob('*') if p.is_file() and not p.is_symlink()}


def blob(digest):
    if not re.fullmatch(r'sha256:[a-f0-9]{64}',digest):raise ValueError('모델 파일 식별자가 올바르지 않습니다.')
    return bounded(model_root()/'ollama/blobs'/digest.replace(':','-'))


def gguf(path):
    """Read metadata only; skip tokenizer arrays without loading model tensors."""
    values={}
    with Path(path).open('rb') as f:
        def unpack(fmt):
            size=struct.calcsize(fmt);raw=f.read(size)
            if len(raw)!=size:raise ValueError('GGUF 파일이 잘렸습니다.')
            return struct.unpack(fmt,raw)[0]
        def string(keep=True):
            length=unpack('<Q')
            if length>64*1024*1024:raise ValueError('GGUF 메타데이터를 읽을 수 없습니다.')
            if not keep:f.seek(length,1);return None
            return f.read(length).decode('utf-8',errors='replace')
        def value(kind,keep=True,depth=0):
            formats={0:'<B',1:'<b',2:'<H',3:'<h',4:'<I',5:'<i',6:'<f',7:'<?',10:'<Q',11:'<q',12:'<d'}
            if kind in formats:return unpack(formats[kind])
            if kind==8:return string(keep)
            if kind==9 and depth<2:
                item=unpack('<I');count=unpack('<Q')
                if count>10000000:raise ValueError('GGUF 배열이 너무 큽니다.')
                if item in formats:f.seek(struct.calcsize(formats[item])*count,1)
                else:
                    for _ in range(count):value(item,False,depth+1)
                return None
            raise ValueError('지원하지 않는 GGUF 메타데이터입니다.')
        if f.read(4)!=b'GGUF' or unpack('<I') not in (2,3):raise ValueError('지원되는 GGUF 파일이 아닙니다.')
        tensors=unpack('<Q');count=unpack('<Q')
        if not tensors or count>100000:raise ValueError('비어 있거나 손상된 GGUF 파일입니다.')
        for _ in range(count):
            key=string();kind=unpack('<I');keep=key.startswith('general.') or key.endswith(('.context_length','.block_count','.embedding_length','.attention.head_count_kv','.attention.head_count','.attention.key_length','.attention.value_length')) or key in ('tokenizer.chat_template','split.count')
            item=value(kind,keep)
            if keep and item is not None:values[key]=item
        if values.get('split.count',1)>1:raise ValueError('분할 GGUF는 아직 지원하지 않습니다. 단일 GGUF 파일을 선택하세요.')
    return values


def protocol(name):
    return records().get(name,{}).get('protocol') or ('hy' if (name or '').lower().startswith(('rpt-hymt','hy-mt','hymt')) else 'generic')


def local_sources(record):
    sources=list(record.get('local_files',[]))
    if record.get('local_file') and not any(s.get('local_file')==record['local_file'] for s in sources):
        sources.append({k:record.get(k) for k in ('local_file','local_size','local_mtime')})
    return sources


def display_name(name,record):
    # GGUF general.name can be a training checkpoint name. Use the known
    # filename, including for registrations saved by older application builds.
    candidates=[record.get('filename')]+[s.get('local_file') for s in local_sources(record)]
    for filename in candidates:
        if isinstance(filename,str) and filename.lower().endswith('.gguf'):
            return filename.replace('\\','/').rsplit('/',1)[-1]
    return name


def gguf_files():
    """Walk only the portable model tree, without following linked directories."""
    base=model_root()
    if not base.exists():return
    for directory,dirs,files in os.walk(base,followlinks=False):
        allowed=[]
        for name in dirs:
            try:bounded(Path(directory)/name);allowed.append(name)
            except (OSError,ValueError):continue
        dirs[:]=sorted(allowed,key=str.casefold)
        for name in sorted(files,key=str.casefold):
            if Path(name).suffix.lower()!='.gguf':continue
            try:yield bounded(Path(directory)/name)
            except (OSError,ValueError):continue


def inventory():
    entries=[];known=records()
    for name,path in manifests().items():
        try:
            m=read_json(bounded(path),{});layers=m.get('layers',[]);assets=[m['config'],*layers]
            valid=all(blob(x['digest']).is_file() and (not x.get('size') or blob(x['digest']).stat().st_size==x['size']) for x in assets)
            weight=next(x for x in layers if x['mediaType']=='application/vnd.ollama.image.model')
            info=known.get(name,{})
            entries.append(dict(id=name,title=display_name(name,info),size=weight['size'],sha=weight['digest'].removeprefix('sha256:'),ready=valid,
                protocol=protocol(name),source=info.get('repo','수동 설치'),quantization=info.get('quantization',quantization(name)),
                context=info.get('context'),estimatedMemory=estimate(weight['size']),description=info.get('description',''),
                recommended=name=='rpt-hymt2-7b:q6_k' or info.get('recommended',False)))
        except (OSError,ValueError,KeyError,StopIteration):continue
    raw=[]
    registered=[s for entry in entries if entry['ready'] for s in local_sources(known.get(entry['id'],{}))]
    for p in gguf_files():
        try:
            relative=p.relative_to(model_root()).as_posix();stat=p.stat()
            if any(r.get('local_file')==relative and r.get('local_size')==stat.st_size and r.get('local_mtime')==stat.st_mtime_ns for r in registered):continue
            raw.append(relative)
        except (OSError,ValueError):continue
    return dict(selected=selected(),installed=sorted(entries,key=lambda r:(not r['recommended'],r['title'].lower())),pending=raw,folder=str(model_root()))


def problem(name=None):
    name=selected() if name is None else name
    items=inventory()['installed']
    if not name:
        return {'code':'model_required','title':'사용할 모델을 선택해 주세요.' if any(x['ready'] for x in items) else '번역 모델이 필요합니다.',
                'detail':'AI 모델 탭에서 설치된 모델을 선택해 주세요.' if any(x['ready'] for x in items) else 'AI 모델 탭에서 모델을 다운로드하거나 models 폴더에 GGUF 파일을 넣어 주세요.'}
    if not any(x['id']==name and x['ready'] for x in items):
        return {'code':'model_required','title':'선택한 모델을 찾을 수 없습니다.','detail':'AI 모델 탭에서 다른 모델을 선택하거나 다시 다운로드해 주세요.'}
    return None


def require_selected(name=None):
    error=problem(name)
    if error:raise ValueError(error['title']+' '+error['detail'])
    return name or selected()


def choose(name):
    if name:require_selected(name)
    save(ROOT/'data/model-choice.json',{'selected':name or None})
    return inventory()


def needs_model(jobs):return bool(set(jobs)&{'run','retranslate','translate','sample','analyze','review','names','failed'})


def ensure_idle():
    from desktop_bridge import runtime_info
    active,_=runtime_info()
    if active:raise ValueError('모델 작업이 끝난 뒤 다시 시도해 주세요.')


def remove(name):
    ensure_idle();paths=manifests()
    if name not in paths:raise ValueError('설치된 모델을 찾을 수 없습니다.')
    path=bounded(paths[name]);m=read_json(path,{})
    digests={x['digest'] for x in [m['config'],*m['layers']]}
    # Do not remove any blob referenced by another model, including foreign registries.
    shared=set()
    for other in (model_root()/'ollama/manifests').rglob('*'):
        if other.is_file() and other!=path:
            data=read_json(bounded(other),{})
            if 'config' not in data or 'layers' not in data:raise ValueError('다른 모델의 등록 정보를 확인한 뒤 삭제해 주세요.')
            shared.update(x['digest'] for x in [data['config'],*data['layers']])
    library=records();record=library.get(name,{})
    # Delete the manually installed copy only when it still refers to this registered content.
    for source in local_sources(record):
        local=source['local_file'];local_path=bounded(model_root()/local)
        if local_path.is_file() and local_path.stat().st_size==source.get('local_size') and local_path.stat().st_mtime_ns==source.get('local_mtime'):
            if not any(k!=name and any(s.get('local_file')==local for s in local_sources(r)) for k,r in library.items()):local_path.unlink()
    path.unlink()
    for digest in digests-shared:blob(digest).unlink(missing_ok=True)
    library.pop(name,None);save(ROOT/'data/model-library.json',library)
    if selected()==name:choose(None)
    return inventory()


def estimate(size):return round(size*1.12+2*1024**3) if size else None
def quantization(text):
    match=re.search(r'(?i)(IQ\d[_A-Z0-9]*|Q\d[_A-Z0-9]*|BF16|F16|F32)',text)
    return match[0].upper() if match else '미제공'


def hf_id(repo):
    if not isinstance(repo,str) or not re.fullmatch(r'[\w.-]+/[\w.-]+',repo):raise ValueError('모델 저장소 이름을 확인하세요.')
    return repo


def fetch(url,limit=4*1024*1024):
    request=urllib.request.Request(url,headers={'User-Agent':'RenPyTranslator/1.0'})
    try:
        with urllib.request.urlopen(request,timeout=15) as response:
            payload=response.read(limit+1)
            if len(payload)>limit:raise ValueError('모델 정보가 너무 큽니다.')
            return payload
    except urllib.error.HTTPError as exc:
        if exc.code in (401,403):raise ValueError('접근 승인이 필요한 모델입니다. 현재는 공개 다운로드 모델을 지원합니다.') from exc
        raise ValueError('모델 정보를 가져오지 못했습니다. 잠시 후 다시 시도해 주세요.') from exc


def search(query=''):
    query=str(query).strip()[:120]
    url='https://huggingface.co/api/models?'+urllib.parse.urlencode({'search':query,'filter':'gguf','sort':'downloads','direction':-1,'limit':30})
    rows=json.loads(fetch(url));result=[]
    # File hashes also identify manually imported models with no repository record.
    recommended_files=[]
    try:
        data=json.loads(fetch('https://huggingface.co/api/models/'+RECOMMENDED+'?blobs=true'))
        recommended_files=catalogue_files(data,RECOMMENDED)
    except (OSError,ValueError):pass
    # Recommendation is our tested translation model, not a popularity score.
    for row in [{'id':RECOMMENDED},*rows]:
        repo=row.get('id','')
        if '/' not in repo or any(x['id']==repo for x in result):continue
        result.append(dict(id=repo,recommended=repo==RECOMMENDED,downloads=row.get('downloads'),task=row.get('pipeline_tag'),
                           files=recommended_files if repo==RECOMMENDED else []))
    return result


def catalogue_files(data,repo):
    files=[]
    for row in data.get('siblings',[]):
        filename=row.get('rfilename','');lfs=row.get('lfs') or {};size=row.get('size') or lfs.get('size')
        if not filename.lower().endswith('.gguf') or re.search(r'-\d{5}-of-\d{5}\.gguf$',filename):continue
        if any(word in filename.lower() for word in ('mmproj','adapter')):continue
        if Path(filename).is_absolute() or '..' in Path(filename).parts or '\\' in filename:continue
        files.append(dict(name=filename,size=size,sha=lfs.get('sha256'),quantization=quantization(filename),estimatedMemory=estimate(size),
                          recommended=repo==RECOMMENDED and filename==RECOMMENDED_FILE))
    files.sort(key=lambda f:(not f['recommended'],f['size'] or 0,f['name']))
    return files


def installed_match(entry,installed=None):
    if not entry.get('sha') or not entry.get('size'):return None
    if installed is None:installed=inventory()['installed']
    return next((m['id'] for m in installed if m['ready'] and m['sha']==entry['sha'] and m['size']==entry['size']),None)


def details(repo):
    repo=hf_id(repo);data=json.loads(fetch('https://huggingface.co/api/models/'+repo+'?blobs=true'))
    revision=data.get('sha','')
    if not re.fullmatch(r'[a-f0-9]{40}',revision):raise ValueError('모델 버전 정보를 확인할 수 없습니다.')
    card=data.get('cardData') or {};meta=data.get('gguf') or {};files=catalogue_files(data,repo)
    installed=inventory()['installed']
    for entry in files:entry['installedModel']=installed_match(entry,installed)
    try:description=fetch('https://huggingface.co/'+repo+'/raw/'+revision+'/README.md',1024*1024).decode('utf-8',errors='replace')[:16000]
    except (OSError,ValueError):description='모델 설명이 제공되지 않았습니다.'
    return dict(id=repo,revision=revision,author=repo.split('/')[0],license=card.get('license','미제공'),languages=card.get('language',[]),
        parameters=meta.get('total') or (data.get('safetensors') or {}).get('total'),context=meta.get('context_length'),architecture=meta.get('architecture'),
        description=description,files=files,recommended=repo==RECOMMENDED,task=data.get('pipeline_tag'),gated=bool(data.get('gated')))


def progress(stage,done=0,total=0):print(json.dumps(dict(stage=stage,done=done,total=total),ensure_ascii=False),flush=True)
def cancelled():
    name=os.environ.get('RPT_CANCEL_FILE')
    if name and Path(name).exists():raise KeyboardInterrupt


def hash_file(path):
    h=hashlib.sha256();size=path.stat().st_size;done=0;last=0
    with path.open('rb') as f:
        while chunk:=f.read(8*1024*1024):
            cancelled();h.update(chunk);done+=len(chunk)
            if time.monotonic()-last>.25:progress('파일 확인 중',done,size);last=time.monotonic()
    return h.hexdigest()


def register(path,info=None,digest=None,manual=False):
    info=info or {};path=bounded(path);meta=gguf(path)
    name=str(meta.get('general.name',''))+' '+path.name+' '+str(info.get('repo',''))
    hy=bool(re.search(r'(?i)hy[-_]?mt',name))
    if not hy and not meta.get('tokenizer.chat_template'):raise ValueError('대화 형식이 없는 모델입니다. Instruct 또는 Chat GGUF 파일을 선택하세요.')
    digest=digest or hash_file(path);target=blob('sha256:'+digest);target.parent.mkdir(parents=True,exist_ok=True)
    if path!=target and not target.exists():
        try:os.link(path,target)
        except OSError:shutil.copy2(path,target)
    alias=('rpt-hymt-' if hy else 'rpt-local-')+digest[:16]+':latest'
    # Reuse an already registered model with the same weights (including the original Hy alias).
    for existing,p in manifests().items():
        if any(x.get('digest')=='sha256:'+digest and x.get('mediaType')=='application/vnd.ollama.image.model' for x in read_json(p,{}).get('layers',[])):
            alias=existing;break
    if alias not in manifests():
        from hy_backend import runtime
        progress('모델 등록 중')
        with runtime({'model':alias},require_model=False) as cfg:
            body={'model':alias,'files':{info.get('filename',path.name if path.suffix=='.gguf' else 'model.gguf'):'sha256:'+digest},'stream':False}
            if hy:body.update(template=HY_TEMPLATE,parameters={'stop':['<|eos|>'],'temperature':0.7,'top_p':0.6,'top_k':20})
            req=urllib.request.Request(cfg['endpoint']+'/api/create',json.dumps(body).encode(),{'Content-Type':'application/json'})
            try:
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=300) as response:
                    result=json.load(response)
            except urllib.error.HTTPError as exc:
                try:message=json.loads(exc.read()).get('error',str(exc))
                except (ValueError,OSError):message=str(exc)
                raise ValueError('모델을 등록하지 못했습니다: '+message) from exc
            if result.get('error'):raise ValueError(result['error'])
        if alias not in manifests():raise ValueError('모델 등록 파일이 생성되지 않았습니다.')
    library=records();previous=library.get(alias,{})
    arch=meta.get('general.architecture','');record=dict(previous,**info)
    record.update(protocol='hy' if hy else 'generic',context=meta.get(arch+'.context_length'),quantization=quantization(info.get('filename',path.name)),digest=digest)
    if manual:
        relative=path.relative_to(model_root()).as_posix();stat=path.stat()
        sources=[s for s in local_sources(previous) if s.get('local_file')!=relative]
        sources.append(dict(local_file=relative,local_size=stat.st_size,local_mtime=stat.st_mtime_ns))
        record['local_files']=sources
        for key in ('local_file','local_size','local_mtime'):record.pop(key,None)
    record['title']=display_name(alias,record)
    library[alias]=record;save(ROOT/'data/model-library.json',library)
    return alias


def download(repo,filename,revision):
    ensure_idle();info=details(repo)
    if info['revision']!=revision:raise ValueError('모델 파일이 업데이트됐습니다. 목록을 새로고침한 뒤 다시 선택하세요.')
    entry=next((f for f in info['files'] if f['name']==filename),None)
    if not entry or not entry['size'] or not re.fullmatch(r'[a-f0-9]{64}',entry.get('sha') or ''):raise ValueError('파일 크기와 검증 정보를 확인할 수 없습니다.')
    existing=installed_match(entry)
    if existing:
        progress('이미 설치된 모델입니다.')
        return existing
    digest=entry['sha'];target=blob('sha256:'+digest)
    metadata=dict(repo=repo,revision=revision,filename=filename,description=info['description'][:2000],recommended=entry['recommended'])
    if target.is_file() and target.stat().st_size==entry['size']:
        if hash_file(target)==digest:return register(target,metadata,digest)
        raise ValueError('기존 모델 파일이 손상됐습니다. 해당 모델을 삭제한 뒤 다시 다운로드해 주세요.')
    parts=model_root()/'.downloads';parts.mkdir(parents=True,exist_ok=True);part=bounded(parts/(digest+'.part'))
    have=part.stat().st_size if part.exists() else 0
    if have>entry['size']:part.unlink();have=0
    if shutil.disk_usage(parts).free<entry['size']-have+64*1024**2:raise ValueError('모델을 저장할 공간이 부족합니다.')
    url='https://huggingface.co/'+repo+'/resolve/'+revision+'/'+urllib.parse.quote(filename,safe='/')
    if have<entry['size']:
        headers={'User-Agent':'RenPyTranslator/1.0','Accept-Encoding':'identity'}
        if have:headers['Range']='bytes='+str(have)+'-'
        with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=20) as response:
            if response.status==206:
                if not response.headers.get('Content-Range','').startswith('bytes '+str(have)+'-'):raise ValueError('이어받기 위치가 올바르지 않습니다.')
            else:have=0
            with part.open('ab' if have else 'wb') as f:
                last=0
                while chunk:=response.read(1024*1024):
                    cancelled();f.write(chunk);have+=len(chunk)
                    if have>entry['size']:raise ValueError('다운로드 크기가 예상과 다릅니다.')
                    if time.monotonic()-last>.25:progress('다운로드 중',have,entry['size']);last=time.monotonic()
    if have!=entry['size']:raise ValueError('다운로드가 끊겼습니다. 다시 누르면 이어받습니다.')
    if hash_file(part)!=digest:
        part.unlink();raise ValueError('다운로드 파일 검증에 실패했습니다. 다시 다운로드해 주세요.')
    target.parent.mkdir(parents=True,exist_ok=True);part.replace(target)
    return register(target,metadata,digest)


@contextlib.contextmanager
def mutation_lock():
    import msvcrt
    path=ROOT/'data/model-store.lock';path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as f:
        f.seek(0)
        try:msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:raise ValueError('다른 모델 작업이 진행 중입니다.')
        try:yield
        finally:f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)


def dispatch(data):
    op=data.get('op')
    if op=='inventory':return inventory()
    if op=='search':return search(data.get('query',''))
    if op=='details':return details(data.get('repo'))
    if op=='preflight':return problem() if needs_model(data.get('tasks',[])) else None
    if op=='select':
        ensure_idle();return choose(data.get('model'))
    if op in ('delete','download','refresh'):
        with mutation_lock():
            if op=='delete':return remove(data.get('model'))
            if op=='download':return download(data.get('repo'),data.get('file'),data.get('revision'))
            ensure_idle();errors=[]
            for filename in inventory()['pending']:
                cancelled()
                try:register(model_root()/filename,manual=True)
                except Exception as exc:errors.append(filename+': '+str(exc))
            if errors:raise ValueError('\n'.join(errors))
            return inventory()
    raise ValueError('알 수 없는 모델 작업입니다.')
