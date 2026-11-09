"""Hy-MT's single-user translation protocol and tool-owned local Ollama server."""
from contextlib import contextmanager
from datetime import datetime,timezone
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time
import urllib.request

DEFAULT_MODEL = 'rpt-hymt2-7b:q6_k'
_MAX_BINARY_HASH_BYTES = 64*1024*1024


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _windows_file_version(path):
    """Read PE version resources without starting the executable."""
    if os.name!='nt':return {}
    import ctypes
    from ctypes import wintypes
    version=ctypes.WinDLL('version',use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes=[wintypes.LPCWSTR,ctypes.POINTER(wintypes.DWORD)]
    version.GetFileVersionInfoSizeW.restype=wintypes.DWORD
    version.GetFileVersionInfoW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p]
    version.GetFileVersionInfoW.restype=wintypes.BOOL
    version.VerQueryValueW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR,
                                   ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(wintypes.UINT)]
    version.VerQueryValueW.restype=wintypes.BOOL
    ignored=wintypes.DWORD();size=version.GetFileVersionInfoSizeW(str(path),ctypes.byref(ignored))
    if not size:return {}
    data=ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(str(path),0,size,data):return {}
    value=ctypes.c_void_p();length=wintypes.UINT()
    if not version.VerQueryValueW(data,'\\',ctypes.byref(value),ctypes.byref(length)):return {}
    class FixedInfo(ctypes.Structure):
        _fields_=[(name,wintypes.DWORD) for name in ('signature','structure_version',
            'file_ms','file_ls','product_ms','product_ls','flags_mask','flags','os',
            'type','subtype','date_ms','date_ls')]
    if length.value<ctypes.sizeof(FixedInfo):return {}
    info=ctypes.cast(value,ctypes.POINTER(FixedInfo)).contents
    if info.signature!=0xFEEF04BD:return {}
    def dotted(ms,ls):return '.'.join(str(v) for v in (ms>>16,ms&65535,ls>>16,ls&65535))
    return {'file_version':dotted(info.file_ms,info.file_ls),
            'product_version':dotted(info.product_ms,info.product_ls)}


@lru_cache(maxsize=128)
def _cached_binary_identity(path,size,mtime_ns):
    record={'path':path,'size':size,'mtime_ns':mtime_ns}
    try:record.update(_windows_file_version(path))
    except Exception as exc:record['version_error']=repr(exc)
    if size>_MAX_BINARY_HASH_BYTES:
        record['hash_skipped']='size_limit'
        return record
    try:
        digest=hashlib.sha256()
        with Path(path).open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
        record['sha256']=digest.hexdigest()
    except OSError as exc:record['hash_error']=repr(exc)
    return record


def binary_identity(path):
    """Cache small native binary identities; never open a model for hashing."""
    path=Path(path)
    if path.suffix.lower() not in ('.exe','.dll'):
        return {'path':str(path),'hash_skipped':'not_native_binary'}
    try:
        path=path.resolve();stat=path.stat()
        return dict(_cached_binary_identity(str(path),stat.st_size,stat.st_mtime_ns))
    except OSError as exc:return {'path':str(path),'identity_error':repr(exc)}


def _write_runtime_record(log,label,record,state):
    try:
        log.write((label+json.dumps(record,ensure_ascii=False)+'\n').encode('utf-8'))
        log.flush()
    except (OSError,ValueError) as exc:state['diagnostic_log_error']=repr(exc)


def is_hy(cfg):
    from model_store import protocol
    return protocol(cfg.get('model'))=='hy'


def body(inputs, context, cfg, glossary, retry_note):
    from source_language import instruction
    from label_policy import LABEL_USES, context_for
    rpg=cfg.get('engine')=='rpgmaker'
    if rpg:
        from rpgmaker_protocol import wire_inputs,wire_markers
        inputs=wire_inputs(inputs);retry_note=wire_markers(retry_note)
    labels_only=bool(inputs) and all(item.get('usage') in LABEL_USES for item in inputs)
    if labels_only:
        # Also protect direct callers, not only the regular translation queue.
        context,_=context_for([dict(item,source=item['text']) for item in inputs],context)
    language_instruction=('출력 언어: 한국어. 입력은 한국어 번역 초안이며, 남아 있는 일본어 표현만 한국어로 교정하세요.\n'
                          if rpg and cfg.get('_rpg_correction_mode') and not cfg.get('_rpg_plain_correction') else instruction(cfg))
    prompt = (language_instruction+'자연스러운 한국어로 번역하세요. 의미와 말투를 유지하고 내용을 추가하거나 누락하지 마세요. '
              '설명 없이 번역만 출력하세요. 각 항목의 [ID]를 유지하세요.\n')
    if not rpg:
        prompt+=('글자 색·크기·강조 같은 시각 서식은 프로그램이 처리합니다. 서식 태그를 추가하지 마세요. '
                 '각 [ID]와 번역문만 출력하고 target_id 같은 문맥 메타데이터는 출력하지 마세요.\n')
    if rpg and cfg.get('_rpg_plain_retry'):
        prompt+=('표시 코드는 프로그램이 복원합니다. 번역에 코드나 보호 표시를 추가하지 마세요. '
                 '아래 이름·숫자는 읽기용 값입니다. 표기를 그대로 유지하되 한국어 어순과 조사는 자연스럽게 번역하세요: '
                 +json.dumps(cfg.get('_rpg_plain_values',{}),ensure_ascii=False)+'\n')
        if context.get('layout_reading'):
            prompt+='layout_reading은 나뉜 항목을 이어 읽기 위한 전체 원문입니다. 문맥에 참고하되 각 [ID]에 해당하는 부분만 번역하고 항목을 합치지 마세요.\n'
    if labels_only:
        prompt += ('이 항목들은 화자 이름표 또는 UI 라벨이며 대사가 아닙니다. '
                   '이름표는 이름·직책만, UI 라벨은 해당 표시문구만 번역하세요. '
                   '명칭을 대사나 설명문으로 확장하지 마세요. 화자 접두사·인용 대사·사건을 만들지 마세요. '
                   '용어표의 source/target은 표기 참고입니다. 입력에 없는 내용을 추가하지 마세요.\n')
    else:
        prompt += ('Background Information은 참고 자료이며 번역 대상이 아닙니다. Source Text의 항목만 번역하세요. '
               'source_passage는 원문 순서이며 target_id는 Source Text에서 같은 ID인 항목의 위치입니다. '
               '화자와 앞뒤 원문을 참고해 호칭과 말투를 선택하되, 명시되지 않은 관계나 청자를 단정하지 마세요. '
               '화자 식별자가 다르다는 이유만으로 다른 인물이라고 단정하지 마세요.\n')
    reading={item['id']:item['reading_text'] for item in inputs if item.get('reading_text')}
    if reading:
        prompt+=('reading_text는 화면 좌표·대기 코드와 화면 줄바꿈을 제외한 같은 원문이며 뜻을 읽는 참고입니다. '
                 '표시 코드가 단어 중간에 있어도 하나의 단어·감탄사로 해석하세요. '
                 '번역할 내용은 Source Text와 동일하며 입력의 보호 표시를 보존하세요. '
                 '일반 명사는 뜻으로 번역하고, 실제 고유명사만 음역하세요. 일본어 글자를 번역에 남기지 마세요. '
                 '앞뒤 발화와 원문의 말투를 유지하고 임의로 존댓말과 반말을 바꾸지 마세요.\n')
    if context.get('translation_guidance') and not labels_only:
        prompt += ('translation_guidance의 공통 문체, 파일 배경(file), 현재 장면(scene)과 관련 용어를 해석에 참고하세요. '
                   '대상·화자·관계는 번역할 원문과 현재 장면을 우선하고, 공통 배경의 주인공을 모든 문장의 대상으로 간주하지 마세요. '
                   '장면 설명이 원문과 충돌하면 원문을 따르세요. 용어 표기는 해당 의미일 때 적용하세요. '
                   '파일 배경의 후속 관계나 사건을 이전 구간에 소급하지 마세요. '
                   '공통 문체는 내레이션에 유지하되, 인용된 대화의 말투·관계는 현재 화자와 장면에 맞추세요. '
                   '문맥 설명 자체를 번역하거나 원문에 없는 사실을 보태지 마세요. '
                   '문맥에 후속 사건이 있어도 번역 항목을 합치거나 다른 항목의 내용을 앞당기지 마세요.\n')
    if any(re.search(r'<rpt\d+/>',item['text']) for item in inputs):
        prompt+=('각 항목의 입력에 실제로 있는 <rpt000/> 형식의 표시만 정확히 한 번씩 보존하세요. '
                 'movable_placeholders에 있는 표시는 변수이므로 한국어 어순에 맞춰 이동할 수 있습니다. '
                 '그 외 기능·제어 표시끼리의 순서는 유지하세요. '
                 '표시가 없는 항목에는 표시를 추가하지 마세요. 다른 항목의 표시를 옮기거나 새로 만들지 마세요.\n')
    if rpg and any(re.search(r'⟦RPT\d+⟧',item['text']) for item in inputs):
        prompt+=('⟦RPT숫자⟧는 번역하지 않는 위치 표시이며, [ID] 응답 번호와 다릅니다. 입력에 있는 표시를 각각 한 번 그대로 유지하세요. '
                 'HTML 태그나 다른 기호로 바꾸지 마세요. movable_placeholders에 있는 변수만 이동할 수 있으며, 그 외 표시의 상대 순서는 유지하세요.\n')
    if any(item.get('name_hints') for item in inputs):
        prompt+='name_hints는 이름 표시의 의미입니다. 해당 표시를 이름으로 바꾸지 말고 유지하세요.\n'
    if context.get('person_name_spellings'):
        prompt+='person_name_spellings는 인명 음역표입니다. 인물을 가리킬 때는 일반 glossary보다 이 표기를 우선하고, 국가명이나 일반 명사에는 적용하지 마세요.\n'
    hints = {item['id']:item['name_hints'] for item in inputs if item.get('name_hints')}
    context = dict(context)
    current = context.pop('current_state',None)
    context.pop('_context_audit',None)
    background = dict(context, glossary=glossary, name_hints=hints,
                      target_purposes={item['id']:item.get('usage',item.get('kind','text')) for item in inputs},
                      target_speakers={item['id']:item.get('speaker','unknown') for item in inputs},
                      movable_placeholders={item['id']:item['movable_placeholders'] for item in inputs if item.get('movable_placeholders')})
    if reading:background['reading_text']=reading
    prompt += '\n[Background Information]\n' + json.dumps(background, ensure_ascii=False, separators=(',', ':'))
    if current:
        prompt += '\n\n[Current State — reference only]\n' + current
    if retry_note and (labels_only or rpg):prompt += '\n\n' + retry_note
    prompt += '\n\n[Source Text]\n' + '\n'.join('['+item['id']+'] '+item['text'] for item in inputs)
    if retry_note and not labels_only and not rpg: prompt += '\n\n' + retry_note
    if rpg and cfg.get('_rpg_fragment_correction') and not cfg.get('_rpg_plain_correction'):
        prompt=('입력은 한국어 번역에 일본어가 섞여 남은 구절입니다. 구절 전체의 뜻과 발음을 읽고 일본어 잔류를 자연스러운 한국어로 교정하세요. '
                '⟦RPT숫자⟧는 대기·서식 표시입니다. 표시가 단어 중간에 있어도 의미는 이어지며, 표시 자체는 번호·개수·순서를 그대로 유지하세요. '
                'correction_reading은 표시를 제외한 같은 구절입니다. spelling_context와 source_passage는 참고이며 번역 대상이 아닙니다. '
                '각 [ID]와 수정한 해당 구절만 출력하세요. 뒤의 다른 대사나 설명·인용부호를 덧붙이지 마세요.\n'
                '\n[Background Information]\n'+json.dumps({k:context[k] for k in ('spelling_context','correction_reading','source_passage') if k in context},ensure_ascii=False)+
                '\n\n[Source Text]\n'+'\n'.join('['+item['id']+'] '+item['text'] for item in inputs))
    return {'model':cfg['model'], 'stream':False, 'keep_alive':'30s',
            'messages':[{'role':'user','content':prompt}],
            'options':{'num_ctx':cfg.get('num_ctx',16384), 'num_predict':1800,
                       'temperature':0.7, 'top_p':0.6, 'top_k':20, 'repeat_penalty':1.05, 'seed':42}}


_TARGET_ID_WRAPPER = re.compile(
    r'\[target_id[ \t]*=[ \t]*(?:"([0-9]+)"|\'([0-9]+)\'|([0-9]+))[ \t]*\]'
    r'(.*?)\[/target_id\]', re.S)
_TARGET_ID_ANGLE_WRAPPER = re.compile(
    r'<target_id[ \t]*=[ \t]*(?:"([0-9]+)"|\'([0-9]+)\'|([0-9]+))[ \t]*>'
    r'(.*?)</target_id>', re.S)
_TARGET_ID_TOKEN = re.compile(
    r'\[target_id[ \t]*=(?!=)|\[target_id[ \t]*$|'
    r'\[/target_id(?=[ \t\]]|$)|</?target_id(?=[\s=/>]|$)')
_INVALID_TARGET_ID = object()


def _unwrap_target_id(value, key, source):
    """Remove one proven envelope, or discard only its malformed protocol row."""
    if isinstance(source,str) and _TARGET_ID_TOKEN.search(source):return value
    if not _TARGET_ID_TOKEN.search(value):return value
    if not isinstance(source,str):return _INVALID_TARGET_ID
    wrapper=(_TARGET_ID_WRAPPER.fullmatch(value) or
             _TARGET_ID_ANGLE_WRAPPER.fullmatch(value))
    if wrapper is None or next(part for part in wrapper.groups()[:3] if part is not None)!=key:
        return _INVALID_TARGET_ID
    text=wrapper[4]
    # Nested, adjacent or mismatched envelopes have no unique outer ownership.
    if _TARGET_ID_TOKEN.search(text):return _INVALID_TARGET_ID
    return text.strip()


def parse(content, expected_inputs=None):
    """Parse [ID] responses; optional sources enable exact wrapper cleanup.

    expected_inputs maps numeric string IDs to original, unprotected input text.
    Without that provenance the historical parser behavior remains unchanged.
    An unproven or malformed response envelope discards only its own item.
    """
    parts = re.split(r'(?m)^\[(\d+)\][ \t]*', content.strip())
    values = {}; duplicates = set();seen=set()
    for i in range(1, len(parts)-1, 2):
        key = parts[i]
        if key in seen: duplicates.add(key)
        seen.add(key)
        value=parts[i+1].strip()
        if expected_inputs is not None:value=_unwrap_target_id(value,key,expected_inputs.get(key))
        if value is not _INVALID_TARGET_ID:values[key]=value
    for key in duplicates: values.pop(key, None)
    return values


def api(endpoint, route):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(endpoint+route, timeout=2) as response: return json.load(response)


@contextmanager
def runtime(cfg, require_model=True):
    """No global settings; an occupied port belongs to someone else."""
    from engine import ROOT
    from model_runtime import model_session
    from diagnostics import stage
    exit_state={'started_utc':_utc_now(),'timestamp_utc':None,'server_pid':None,
                'process_role':'ollama_server','runner_exit_status_source':'server_log_or_windows_event',
                'alive_before_cleanup':None,'cleanup_action':'none',
                'process_returncode':None,'kill_returncode':None,'kill_error':None,
                'termination':'not_started','cleanup_finished_utc':None}
    # The caller, yielded active config and recovery's shallow copy share this
    # object, including failures before __enter__ can return an active config.
    cfg['_runtime_exit']=exit_state
    from desktop_bridge import preferences,gpu_environment
    policy=preferences()
    # Missing means follow the app selection. An explicit empty/None value means
    # the pinned default. Freeze before model preparation can register a variant.
    if 'runtime_id' not in cfg:cfg['runtime_id']=policy.get('runtime_id') or ''
    if require_model:
        from gpu_policy import configure,prepare_model
        cfg=configure(cfg)
        stage('GPU and model compatibility',cfg=cfg)
        try:cfg=prepare_model(cfg)
        except Exception:
            stage('GPU and model compatibility',cfg=cfg)
            raise
    stage('model server startup',cfg=cfg)
    from runtime_assets import runtime_executable
    selected_runtime=cfg.get('runtime_id')
    executable = (runtime_executable('ollama',ROOT,runtime_id=selected_runtime)
                  if selected_runtime else runtime_executable('ollama', ROOT))
    if not executable.exists(): raise RuntimeError('Tool-local Ollama is missing: '+str(executable))
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
    endpoint = 'http://127.0.0.1:'+str(port)
    env = dict(os.environ, OLLAMA_HOST='127.0.0.1:'+str(port), OLLAMA_MODELS=str(ROOT/'models/ollama'),
               OLLAMA_NUM_PARALLEL=str(cfg.get('parallel',2)), OLLAMA_MAX_LOADED_MODELS='1',
               OLLAMA_FLASH_ATTENTION='1', OLLAMA_KV_CACHE_TYPE=cfg.get('kv_cache_type','q8_0'), OLLAMA_NO_CLOUD='1', OLLAMA_KEEP_ALIVE='30s',
               LLAMA_ARG_CACHE_RAM='0', LLAMA_ARG_CTX_CHECKPOINTS='2')
    for key,value in gpu_environment(policy).items():
        if value is None: env.pop(key,None)
        else: env[key]=value
    if cfg.get('_hardware_cuda_device'):
        env.update(CUDA_VISIBLE_DEVICES=cfg['_hardware_cuda_device'],OLLAMA_SCHED_SPREAD='0')
    diagnostic_mode='blocking' if os.environ.get('RPT_CUDA_DIAGNOSTICS','').strip().lower()=='blocking' else 'off'
    if diagnostic_mode=='blocking':env['CUDA_LAUNCH_BLOCKING']='1'
    cfg['_cuda_diagnostic_mode']=diagnostic_mode
    runtime_env={k:env[k] for k in ('OLLAMA_HOST','OLLAMA_MODELS','OLLAMA_NUM_PARALLEL',
        'OLLAMA_MAX_LOADED_MODELS','OLLAMA_FLASH_ATTENTION','OLLAMA_KV_CACHE_TYPE',
        'OLLAMA_NO_CLOUD','OLLAMA_KEEP_ALIVE','OLLAMA_LLM_LIBRARY','OLLAMA_GPU_OVERHEAD',
        'LLAMA_ARG_CACHE_RAM','LLAMA_ARG_CTX_CHECKPOINTS','CUDA_VISIBLE_DEVICES',
        'CUDA_LAUNCH_BLOCKING','GGML_CUDA_DISABLE_GRAPHS','OLLAMA_SCHED_SPREAD',
        'OLLAMA_VULKAN','GGML_VK_VISIBLE_DEVICES') if k in env}
    logs = ROOT/'logs'; logs.mkdir(exist_ok=True)
    log_path=logs/('hy-server-'+str(os.getpid())+'-'+str(port)+'.log')
    with log_path.open('ab') as log:
        from native_process import popen as native_popen, native_runtime_directory
        library_directory=native_runtime_directory(executable)
        try:
            proc = native_popen([str(executable),'serve'], env=env, cwd=executable.parent,
                                library_directory=library_directory,
                                stdout=log, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except BaseException as exc:
            exit_state.update(timestamp_utc=_utc_now(),cleanup_finished_utc=_utc_now(),
                              launch_error=repr(exc))
            exc.runtime_exit=exit_state
            _write_runtime_record(log,'RPT runtime exit: ',exit_state,exit_state)
            raise
        from cancel_runtime import register,unregister
        from engine import save_json
        descriptor = ROOT/'data/runtime'/(str(os.getpid())+'.json')
        exit_state['server_pid']=proc.pid
        registered=False
        try:
            register(proc);registered=True
            launch=proc._rpt_native_launch
            launch['runtime_env']=runtime_env
            launch['server_log']=str(log_path)
            launch['started_utc']=exit_state['started_utc']
            launch['run_id']=cfg.get('_recovery_run_id')
            launch['runtime_generation']=cfg.get('_runtime_generation')
            launch['runtime_id']=selected_runtime or executable.parent.name
            launch['cuda_diagnostic_mode']=diagnostic_mode
            launch['startup_timeout_seconds']=60 if diagnostic_mode=='blocking' else None
            launch['argv']=[str(executable),'serve']
            launch['executable_identity']=binary_identity(executable)
            if library_directory:
                launch['native_library_identities']=[binary_identity(Path(library_directory)/name)
                    for name in ('vcruntime140.dll','vcruntime140_1.dll','msvcp140.dll')]
            cfg['_runtime_launch']=launch
            cfg['_runtime_exit']=exit_state
            if cfg.get('_hardware_policy'):launch['hardware_policy']=cfg['_hardware_policy']
            log.write(('RPT native launch: '+json.dumps(launch,ensure_ascii=False)+'\n').encode('utf-8'))
            log.flush()
            save_json(descriptor,dict(pid=proc.pid,endpoint=endpoint,model=cfg['model'],
                      native_launch=launch,
                      hardware_policy=cfg.get('_hardware_policy'),
                      policy={k:policy[k] for k in ('gpu_mode','gpu_ids')}))
            diagnostic_deadline=time.monotonic()+60 if diagnostic_mode=='blocking' else None
            for _ in range(600 if diagnostic_mode=='blocking' else 100):
                if proc.poll() is not None: raise RuntimeError('Tool-local Ollama exited; see '+str(logs))
                try: api_version=api(endpoint, '/api/version'); break
                except (OSError, ValueError):
                    if diagnostic_deadline is not None and time.monotonic()>=diagnostic_deadline:
                        raise TimeoutError('Tool-local Ollama startup timed out (CUDA blocking diagnostic)')
                    time.sleep(.1)
            else: raise TimeoutError('Tool-local Ollama startup timed out')
            models = api(endpoint, '/api/tags').get('models', [])
            if require_model and not any(m['name'] == cfg['model'] for m in models):
                raise RuntimeError('Model is not installed in the tool model store: '+cfg['model'])
            installed=next((m for m in models if m.get('name')==cfg['model']),{})
            launch['api_version']=api_version.get('version')
            launch['model_identity']={'requested':cfg['model'],'name':installed.get('name'),
                                      'digest':installed.get('digest'),'size':installed.get('size')}
            _write_runtime_record(log,'RPT runtime ready: ',
                {'timestamp_utc':_utc_now(),'server_pid':proc.pid,'run_id':cfg.get('_recovery_run_id'),
                 'runtime_generation':cfg.get('_runtime_generation'),
                 'api_version':launch['api_version'],'model_identity':launch['model_identity']},exit_state)
            save_json(descriptor,dict(pid=proc.pid,endpoint=endpoint,model=cfg['model'],
                      native_launch=launch,hardware_policy=cfg.get('_hardware_policy'),
                      policy={k:policy[k] for k in ('gpu_mode','gpu_ids')}))
            with model_session():
                yield dict(cfg, endpoint=endpoint,_runtime_log=str(log_path),_runtime_pid=proc.pid,
                           _runtime_launch=launch,
                           _runtime_exit=exit_state,
                           _runtime_env=runtime_env)
        except BaseException as exc:
            exc.native_launch=proc._rpt_native_launch
            exc.runtime_exit=exit_state
            raise
        finally:
            # Stop only the process tree created above, even on Ctrl+C or HTTP failure.
            cleanup_error=None
            exit_state['timestamp_utc']=_utc_now()
            try:
                try:code=proc.poll()
                except BaseException as exc:
                    code=None;exit_state['final_poll_error']=repr(exc)
                    if cleanup_error is None:cleanup_error=exc
                exit_state['alive_before_cleanup']=code is None
                if code is None:
                    exit_state.update(cleanup_action='taskkill_owned_tree',termination='application_cleanup')
                    from native_process import run as native_run
                    try:
                        killed=native_run(['taskkill','/PID',str(proc.pid),'/T','/F'], capture_output=True,
                                         timeout=10, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                        exit_state['kill_returncode']=killed.returncode if isinstance(killed.returncode,int) else None
                    except BaseException as exc:
                        exit_state['kill_error']=repr(exc)
                        raise
                    proc.wait(timeout=10)
                else:exit_state['termination']='natural_exit'
            except BaseException as exc:
                cleanup_error=exc
                exit_state.update(termination='cleanup_failed',cleanup_error=repr(exc))
            finally:
                try:code=proc.poll()
                except BaseException as exc:
                    code=None;exit_state['final_poll_error']=repr(exc)
                    if cleanup_error is None:cleanup_error=exc
                exit_state['process_returncode']=code if isinstance(code,int) else None
                for label,cleanup in (
                        ('descriptor',lambda:descriptor.unlink(missing_ok=True)),
                        ('registration',lambda:unregister(proc) if registered else None)):
                    try:cleanup()
                    except BaseException as exc:
                        exit_state[label+'_cleanup_error']=repr(exc)
                        if cleanup_error is None:cleanup_error=exc
                try:
                    from request_budget import forget
                    forget(dict(cfg,_runtime_log=str(log_path)))
                except BaseException as exc:
                    exit_state['request_budget_cleanup_error']=repr(exc)
                    if cleanup_error is None:cleanup_error=exc
                if cleanup_error is not None:
                    exit_state.update(termination='cleanup_failed',cleanup_error=repr(cleanup_error))
                exit_state['cleanup_finished_utc']=_utc_now()
                _write_runtime_record(log,'RPT runtime exit: ',exit_state,exit_state)
            if cleanup_error is not None:
                cleanup_error.runtime_exit=exit_state
                cleanup_error.native_launch=proc._rpt_native_launch
                raise cleanup_error
