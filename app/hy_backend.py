"""Hy-MT's single-user translation protocol and tool-owned local Ollama server."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time
import urllib.request

DEFAULT_MODEL = 'rpt-hymt2-7b:q6_k'


def is_hy(cfg):
    from model_store import protocol
    return protocol(cfg.get('model'))=='hy'


def body(inputs, context, cfg, glossary, retry_note):
    from label_policy import LABEL_USES, context_for
    labels_only=bool(inputs) and all(item.get('usage') in LABEL_USES for item in inputs)
    if labels_only:
        # Also protect direct callers, not only the regular translation queue.
        context,_=context_for([dict(item,source=item['text']) for item in inputs],context)
    prompt = ('자연스러운 한국어로 번역하세요. 의미와 말투를 유지하고 내용을 추가하거나 누락하지 마세요. '
              '설명 없이 번역만 출력하세요. 각 항목의 [ID]를 유지하세요.\n')
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
                 '그 외 서식·제어 표시끼리의 순서는 유지하세요. '
                 '표시가 없는 항목에는 표시를 추가하지 마세요. 다른 항목의 표시를 옮기거나 새로 만들지 마세요.\n')
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
    prompt += '\n[Background Information]\n' + json.dumps(background, ensure_ascii=False, separators=(',', ':'))
    if current:
        prompt += '\n\n[Current State — reference only]\n' + current
    if retry_note and labels_only:prompt += '\n\n' + retry_note
    prompt += '\n\n[Source Text]\n' + '\n'.join('['+item['id']+'] '+item['text'] for item in inputs)
    if retry_note and not labels_only: prompt += '\n\n' + retry_note
    return {'model':cfg['model'], 'stream':False, 'keep_alive':'30s',
            'messages':[{'role':'user','content':prompt}],
            'options':{'num_ctx':cfg.get('num_ctx',16384), 'num_predict':1800,
                       'temperature':0.7, 'top_p':0.6, 'top_k':20, 'repeat_penalty':1.05, 'seed':42}}


def parse(content):
    parts = re.split(r'(?m)^\[(\d+)\][ \t]*', content.strip())
    values = {}; duplicates = set()
    for i in range(1, len(parts)-1, 2):
        key = parts[i]
        if key in values: duplicates.add(key)
        values[key] = parts[i+1].strip()
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
    stage('model server startup',cfg=cfg)
    from runtime_assets import runtime_executable
    executable = runtime_executable('ollama', ROOT)
    if not executable.exists(): raise RuntimeError('Tool-local Ollama is missing: '+str(executable))
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
    endpoint = 'http://127.0.0.1:'+str(port)
    env = dict(os.environ, OLLAMA_HOST='127.0.0.1:'+str(port), OLLAMA_MODELS=str(ROOT/'models/ollama'),
               OLLAMA_NUM_PARALLEL=str(cfg.get('parallel',2)), OLLAMA_MAX_LOADED_MODELS='1',
               OLLAMA_FLASH_ATTENTION='1', OLLAMA_KV_CACHE_TYPE='q8_0', OLLAMA_NO_CLOUD='1', OLLAMA_KEEP_ALIVE='30s',
               LLAMA_ARG_CACHE_RAM='0', LLAMA_ARG_CTX_CHECKPOINTS='2')
    from desktop_bridge import preferences, gpu_environment
    policy = preferences()
    for key,value in gpu_environment(policy).items():
        if value is None: env.pop(key,None)
        else: env[key]=value
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
        proc = native_popen([str(executable),'serve'], env=env, cwd=executable.parent,
                            library_directory=native_runtime_directory(executable),
                            stdout=log, stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        from cancel_runtime import register,unregister
        register(proc)
        from engine import save_json
        descriptor = ROOT/'data/runtime'/(str(os.getpid())+'.json')
        try:
            launch=proc._rpt_native_launch
            launch['runtime_env']=runtime_env
            launch['server_log']=str(log_path)
            log.write(('RPT native launch: '+json.dumps(launch,ensure_ascii=False)+'\n').encode('utf-8'))
            log.flush()
            save_json(descriptor,dict(pid=proc.pid,endpoint=endpoint,model=cfg['model'],
                      native_launch=launch,
                      policy={k:policy[k] for k in ('gpu_mode','gpu_ids')}))
            for _ in range(100):
                if proc.poll() is not None: raise RuntimeError('Tool-local Ollama exited; see '+str(logs))
                try: api(endpoint, '/api/version'); break
                except (OSError, ValueError): time.sleep(.1)
            else: raise TimeoutError('Tool-local Ollama startup timed out')
            models = api(endpoint, '/api/tags').get('models', [])
            if require_model and not any(m['name'] == cfg['model'] for m in models):
                raise RuntimeError('Model is not installed in the tool model store: '+cfg['model'])
            with model_session():
                yield dict(cfg, endpoint=endpoint,_runtime_log=str(log_path),_runtime_pid=proc.pid,
                           _runtime_launch=launch,
                           _runtime_env=runtime_env)
        except BaseException as exc:
            exc.native_launch=proc._rpt_native_launch
            raise
        finally:
            # Stop only the process tree created above, even on Ctrl+C or HTTP failure.
            try:
                if proc.poll() is None:
                    from native_process import run as native_run
                    native_run(['taskkill','/PID',str(proc.pid),'/T','/F'], capture_output=True,
                                   timeout=10, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    proc.wait(timeout=10)
            finally:
                descriptor.unlink(missing_ok=True)
                unregister(proc)
                from request_budget import forget
                forget(dict(cfg,_runtime_log=str(log_path)))
