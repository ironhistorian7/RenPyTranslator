"""Read one diagnostic and build a shareable technical report without its body."""
import json
from pathlib import Path
import re
from task_plan import TASKS


_MESSAGES = {
    'output_limit': ('응답 길이 한도에 도달했습니다.', '완료된 항목은 유지됩니다. 잘린 응답과 남은 항목을 다시 처리해야 합니다.'),
    'missing_response_item': ('일부 항목의 응답 번호가 없습니다.', '완료된 항목은 유지되며 응답하지 않은 항목을 따로 처리합니다.'),
    'permission_denied': ('파일 접근이 거부되었습니다.', '파일을 사용 중인 프로그램과 저장 위치의 접근 권한을 확인하세요.'),
    'missing_file': ('필요한 파일을 찾지 못했습니다.', '선택한 작업 자료와 설치 파일이 있는지 확인하세요.'),
    'model_required': ('사용할 번역 모델을 확인하세요.', 'AI 모델 화면에서 설치된 모델을 선택하세요.'),
    'input_budget': ('요청이 입력·출력 예산을 넘었습니다.', '원문을 자르지 않고 더 작은 항목으로 나누어 처리해야 합니다.'),
    'model_server': ('모델 서버가 요청을 처리하지 못했습니다.', '완료된 항목은 유지됩니다. 서버 오류 기록을 확인하세요.'),
    'server_connection': ('모델 서버에 연결하지 못했습니다.', '서버 실행 상태와 연결 오류 기록을 확인하세요.'),
    'memory_allocation': ('메모리를 할당하지 못했습니다.', '기록에 확인된 메모리 할당 오류가 있습니다.'),
    'model_runtime': ('모델 실행 중 오류가 발생했습니다.', '실행 오류의 상세 원인은 로컬 기록에 남았습니다.'),
    'compile_error': ('문법 검사에 통과하지 못했습니다.', '생성 파일과 원본의 검사 결과를 확인하세요.'),
    'request_encoding': ('작업 요청의 문자 인코딩을 읽지 못했습니다.', '요청 전달과 프로그램 버전을 확인하세요.'),
    'protected_tokens': ('번역 결과의 보호 표시가 일치하지 않습니다.', '변수·제어 표시의 번호, 개수 또는 순서가 달라진 항목이 있습니다.'),
    'validation_failed': ('일부 번역 결과가 검사에 통과하지 못했습니다.', '완료된 결과는 유지되며 문제가 있는 항목을 따로 처리합니다.'),
    'rpg_read_failure': ('일부 작업 자료를 읽지 못했습니다.', '읽지 못한 파일의 기술 정보를 제보에 포함할 수 있습니다.'),
    'unknown': ('작업 중 오류가 발생했습니다.', '상세 기록을 확인하거나 기술 제보를 내보내세요.'),
    'user_report': ('사용자 제보', '현재 작업의 허용된 기술 정보만 포함합니다.'),
}
_PHASES = {'prepare','names','translation','repair','presentation','support','validation','package','hints',
           'startup','helper','model_prepare','render','compile','lint','check'}
_STAGES = {'source inspect and prepare':'prepare','inspect and prepare':'prepare','RPG Maker prepare':'prepare','prepare':'prepare',
           'name preparation':'names','name correction':'names','translation':'translation',
           'translation request':'translation','RPG Maker translation':'translation',
           'failed item repair':'repair','repair':'repair','presentation':'presentation','render':'render',
           'install support':'support','validate':'validation','patch check':'validation','package':'package',
           'RPG Maker patch output':'render',
           'in-game hints':'hints','application startup':'startup','desktop helper':'helper',
           'GPU and model compatibility':'model_prepare','model server startup':'model_prepare'}
_COUNTS = {'completed','total','successful','original_kept','failed','files','items','accepted','affected',
           'retry_count','retries','cached','missing','exit_code','http_status'}
_NUMERIC_SETTINGS = {'num_ctx','parallel','batch_size'}
_STRING_SETTINGS = {'engine','engine_variant','source_language','model','kv_cache_type','context_txt_policy',
                    '_cuda_diagnostic_mode','runtime_id'}
_BUDGET_NUMBERS = {'prompt_content_tokens','actual_prompt_tokens','output_reserved','framing_reserved',
                   'num_ctx_requested','output_tokens','num_predict'}
_TOOL_FILES = {'portable_cli.py','desktop_bridge.py','translation.py','hy_backend.py','request_budget.py',
               'token_recovery.py','rpgmaker_project.py','rpgmaker_tokens.py','rpgmaker_protocol.py',
               'rpgmaker_plain_retry.py','rpgmaker_layout.py','rpgmaker_source_language.py','patch_validation.py',
               'runtime_recovery.py','native_process.py','model_store.py','model_runtime.py','gpu_policy.py',
               'source_context.py','translate_game.py','diagnostics.py','support_report.py','runtime_versions.py',
               'job_events.py','job_stages.py','translation_output.py','translation_segments.py'}
_TOOL_FILES.update(('font_policy.py','packaging.py','engine.py'))
_TYPE = re.compile(r'^[A-Za-z_][A-Za-z0-9_.]{0,79}$')
_FRAME = re.compile(r'File "([^"\r\n]+)", line (\d+)(?:, in ([A-Za-z_][A-Za-z0-9_]*))?')
_PRIVATE = re.compile(r'(?:[A-Za-z]:[/\\]|[/\\]|://|\bBearer\b|(?:api[_-]?key|password|secret|token)\s*[=:]|\bsk-[A-Za-z0-9])',re.I)


def classify_issue(record):
    """Classify verified exception types/codes, without exposing raw messages."""
    failures = record.get('validation_failures',[])
    codes = {item['error_code'] for item in failures if isinstance(item,dict) and isinstance(item.get('error_code'),str)} if isinstance(failures,list) else set()
    if isinstance(record.get('error_code'),str):codes.add(record['error_code'])
    previous = record.get('issue',{})
    if isinstance(previous,dict) and isinstance(previous.get('code'),str):codes.add(previous['code'])
    error_type = str(record.get('error_type','')).rsplit('.',1)[-1]
    error = str(record.get('error',''))
    detail = record.get('http',{})
    http_status = detail.get('status',detail.get('http_status',detail.get('code'))) if isinstance(detail,dict) else None
    if 'rpg_read_failure' in codes or record.get('kind') == 'rpg_extraction':code = 'rpg_read_failure'
    elif 'output_limit' in codes or error_type in ('OutputLimitError','OutputLengthError') or 'reached the output limit' in error:code = 'output_limit'
    elif 'missing_response_item' in codes:code = 'missing_response_item'
    elif error_type == 'PermissionError':code = 'permission_denied'
    elif error_type == 'FileNotFoundError' or record.get('process_code') == 'ENOENT':code = 'missing_file'
    elif error_type in ('UnicodeDecodeError','UnicodeEncodeError'):code = 'request_encoding'
    elif error_type in ('InputBudgetError','BatchNeedsSplit','ItemNeedsSplit'):code = 'input_budget'
    elif error_type in ('SyntaxError','IndentationError','TabError','CompileError','JavaScriptSyntaxError'):code = 'compile_error'
    elif error_type == 'HTTPError' or isinstance(http_status,int):code = 'model_server'
    elif error_type in ('URLError','ConnectionError','ConnectionRefusedError','TimeoutError'):code = 'server_connection'
    elif error_type in ('OutOfMemoryError','MemoryError') or re.search(r'CUDA(?: error)?:\s*out of memory',error,re.I):code = 'memory_allocation'
    elif re.search(r'\bCUDA\b|\bggml_cuda\b',error,re.I):code = 'model_runtime'
    elif error.startswith(('사용할 모델을 선택','선택한 모델을 찾을','번역 모델이 필요')):code = 'model_required'
    elif any(marker in error for marker in ('Protected token mismatch','RPG Maker control code mismatch','unrestored placeholder')):code = 'protected_tokens'
    elif failures or error_type == 'BatchTranslationError':code = 'validation_failed'
    else:code = next((code for code in _MESSAGES if code in codes),'unknown')
    title,message = _MESSAGES[code]
    return {'code':code,'title':title,'detail':message}


def phase_name(value):
    if not isinstance(value,str):return None
    if value in _PHASES:return value
    if value in _STAGES:return _STAGES[value]
    prefix=value.split('.',1)[0]
    return prefix if prefix in _PHASES else {'validate':'validation','name':'names'}.get(prefix)


def _text(value, limit=160):
    if not isinstance(value,str) or not value or len(value)>limit or _PRIVATE.search(value):return None
    if any(ord(char)<32 for char in value):return None
    return value


def _number(value):
    return value if isinstance(value,(int,float)) and not isinstance(value,bool) and -1 <= value <= 10**12 else None


def _safe_settings(record,context):
    result = {}
    settings = dict(record.get('settings') or {}) if isinstance(record.get('settings'),dict) else {}
    if isinstance(context.get('settings'),dict):settings.update(context['settings'])
    for key in _STRING_SETTINGS:
        value = _text(context.get(key,settings.get(key)))
        if value is not None:result[key.lstrip('_')] = value
    for key in _NUMERIC_SETTINGS:
        value = _number(context.get(key,settings.get(key)))
        if value is not None:result[key] = value
    return result,settings


def _read_diagnostic(root,log_path):
    if not log_path:return {},'none'
    path = Path(log_path)
    if not path.is_absolute():path = root/path
    current=path
    while current != current.parent:
        if current.is_symlink() or current.is_junction():raise ValueError('연결된 파일은 제보 기록으로 읽지 않습니다.')
        current=current.parent
    if not path.is_file():raise ValueError('지원할 오류 기록을 찾지 못했습니다.')
    path = path.resolve()
    helper = path.parent == (root/'logs').resolve() and bool(re.fullmatch(r'desktop-helper-[A-Za-z0-9_.-]+\.log',path.name))
    extraction = path.name == 'rpg-extraction-notices.json' and path.parent.name == 'data'
    error_json = path.suffix.lower() == '.json' and path.parent.name == 'errors' and path.parent.parent.name in ('data','logs')
    if not (helper or extraction or error_json):raise ValueError('지원되는 오류 기록 파일만 제보할 수 있습니다.')
    if path.stat().st_size > 8*1024*1024:raise ValueError('오류 기록이 커서 안전한 작업 요약만 제보해야 합니다.')
    text = path.read_text(encoding='utf-8-sig')
    if helper:
        header,end = json.JSONDecoder().raw_decode(text.lstrip())
        if not isinstance(header,dict) or not str(header.get('command','')).startswith('--desktop-'):
            raise ValueError('지원되는 실행 도우미 기록이 아닙니다.')
        tail = text.lstrip()[end:]
        types = re.findall(r'(?m)^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)):',tail)
        return dict(error_type=types[-1] if types else 'HelperError',error='',traceback=tail,
                    stage='desktop helper',process_code=header.get('code')),'helper'
    record = json.loads(text)
    if not isinstance(record,dict):raise ValueError('오류 기록 형식이 올바르지 않습니다.')
    if extraction:
        if record.get('kind') != 'rpg_extraction' or not isinstance(record.get('issues'),list):
            raise ValueError('작업 자료 읽기 기록 형식이 올바르지 않습니다.')
        return record,'rpg_extraction'
    if not isinstance(record.get('error_type'),str) or not isinstance(record.get('traceback'),str):
        raise ValueError('프로그램이 생성한 오류 기록 형식이 아닙니다.')
    return record,'error_json'


def build_report(root,log_path,job_context=None):
    """Read the named diagnostic only; never scan a project or collect new data."""
    root = Path(root).resolve()
    context = job_context if isinstance(job_context,dict) else {}
    record,kind = _read_diagnostic(root,log_path)
    try:
        version = json.loads((root/'runtime-lock.json').read_text(encoding='utf-8-sig'))['release']['version']
        if not isinstance(version,str) or not re.fullmatch(r'\d+(?:\.\d+){1,3}(?:[-+][A-Za-z0-9.-]+)?',version):version = 'unknown'
    except (OSError,ValueError,KeyError,TypeError):version = 'unknown'
    context_issue=context.get('issue',context)
    code=context_issue.get('code') if isinstance(context_issue,dict) else None
    issue = classify_issue(record) if record else classify_issue({'error_code':code}) if isinstance(code,str) and code in _MESSAGES else dict(zip(('code','title','detail'),('user_report',*_MESSAGES['user_report'])))
    state = context.get('state',record.get('issue',{}).get('state') if isinstance(record.get('issue'),dict) else None)
    issue['state'] = state if state in ('warning','retrying','failed') else 'warning' if record.get('handled') else 'failed' if record else 'warning'
    error_id = record.get('error_id',context.get('error_id',context_issue.get('error_id') if isinstance(context_issue,dict) else None))
    if not error_id and log_path:error_id = Path(log_path).stem
    if isinstance(error_id,str) and re.fullmatch(r'(?:\d{8}-\d{6}-[0-9a-f]{10}|rpt-[0-9a-f]{12,32})',error_id):issue['error_id'] = error_id
    result = {'schema':'renpy-translator-support-v1','app_version':version,'issue':issue,'diagnostic_kind':kind}
    error_type=record.get('error_type')
    if isinstance(error_type,str) and _TYPE.fullmatch(error_type):result['error_type']=error_type
    phase = phase_name(context.get('phase')) or phase_name(record.get('stage'))
    if phase:result['phase'] = phase
    if isinstance(context.get('tasks'),list):
        tasks=list(dict.fromkeys(task for task in context['tasks'] if isinstance(task,str) and task in TASKS))
        if tasks:result['tasks']=tasks
    settings,raw_settings = _safe_settings(record,context)
    if settings:result['settings'] = settings
    counts = {}
    if isinstance(record.get('metrics'),dict):counts.update(record['metrics'])
    if isinstance(context.get('counts'),dict):counts.update(context['counts'])
    if isinstance(record.get('validation_failures'),list):counts['affected'] = len(record['validation_failures'])
    if isinstance(record.get('accepted_ids'),list):counts['accepted'] = len(record['accepted_ids'])
    http=record.get('http',{})
    if isinstance(http,dict):
        status=_number(http.get('status',http.get('http_status',http.get('code'))))
        if status is not None:counts['http_status']=status
    safe_counts = {key:value for key in _COUNTS if (value := _number(counts.get(key))) is not None}
    if safe_counts:result['counts'] = safe_counts
    budget = record.get('context_budget',{})
    if isinstance(budget,dict):
        safe = {key:value for key in _BUDGET_NUMBERS if (value := _number(budget.get(key))) is not None}
        if budget.get('tokenizer') in ('owned_model_tokenizer','conservative_estimate'):safe['tokenizer'] = budget['tokenizer']
        if safe:result['budget'] = safe
    devices = context.get('gpus',raw_settings.get('_hardware_policy',{}).get('devices',[]) if isinstance(raw_settings.get('_hardware_policy'),dict) else [])
    safe_devices = []
    if isinstance(devices,list):
        for device in devices[:8]:
            if not isinstance(device,dict):continue
            safe = {}
            name = _text(device.get('name'))
            total = _number(device.get('total_mib',device.get('total')))
            if name:safe['name'] = name
            if total is not None:safe['total_mib'] = total
            if safe and safe not in safe_devices:safe_devices.append(safe)
    if safe_devices:result['gpus'] = safe_devices
    launch = record.get('native_launch',raw_settings.get('_runtime_launch',{}))
    if isinstance(launch,dict):
        runtime = {key:value for key in ('runtime_id','api_version') if (value := _text(launch.get(key))) is not None}
        if isinstance(launch.get('api_version'),dict):
            value=_text(launch['api_version'].get('version'))
            if value:runtime['api_version']=value
        if runtime:result['runtime'] = runtime
    frames = []
    for filename,line,function in _FRAME.findall(record.get('traceback','')):
        name = filename.replace('\\','/').rsplit('/',1)[-1]
        if name not in _TOOL_FILES:continue
        frame = {'module':name,'line':int(line)}
        if function:frame['function'] = function
        if frame not in frames:frames.append(frame)
    if frames:result['tool_frames'] = frames[-20:]
    if kind == 'rpg_extraction':
        notices = []
        for item in record['issues'][:200]:
            if not isinstance(item,dict):continue
            notice = {}
            for key in ('code','error_type'):
                value = item.get(key)
                if isinstance(value,str) and _TYPE.fullmatch(value):notice[key] = value
            filename = item.get('file','')
            filename = filename.replace('\\','/').rsplit('/',1)[-1] if isinstance(filename,str) else ''
            if filename and len(filename)<=160 and Path(filename).suffix.lower() in ('.json','.csv','.tsv','.txt') and not any(ord(c)<32 for c in filename):notice['file'] = filename
            count = _number(item.get('count'))
            if count is not None:notice['count'] = count
            if notice:notices.append(notice)
        result['read_notices'] = notices
        result.setdefault('counts',{})['files'] = len(record['issues'])
    return result


def format_report(report):
    """Produce a ready-to-paste post from the already sanitized report fields."""
    settings=report.get('settings',{})
    issue=report.get('issue',{})
    code=issue.get('code','user_report')
    title=_MESSAGES.get(code,_MESSAGES['unknown'])[0]
    version=report.get('app_version')
    lines=['### RenPyTranslator 오류 제보','',
           '- 프로그램: RenPyTranslator'+(' '+version if version and version!='unknown' else ''),
           '- 오류: '+title+' (`'+code+'`)']
    if report.get('error_type'):lines.append('- 예외 형식: `'+report['error_type']+'`')
    states={'warning':'경고','retrying':'재시도 중','failed':'실패'}
    if issue.get('state') in states:lines.append('- 상태: '+states[issue['state']])
    phases={'prepare':'작업 준비','names':'이름 표기 처리','translation':'번역','repair':'미완료 항목 복구',
            'presentation':'표시 파일 생성','render':'표시 파일 생성','support':'지원 파일 설치',
            'validation':'검사','package':'패키지 생성','hints':'게임 내 안내 처리',
            'startup':'프로그램 시작','helper':'작업 요청 확인','model_prepare':'모델 준비',
            'compile':'문법 검사','lint':'정적 검사','check':'검사'}
    if report.get('phase') in phases:lines.append('- 발생 단계: '+phases[report['phase']])
    if issue.get('error_id'):lines.append('- 오류 ID: `'+issue['error_id']+'`')
    engine=settings.get('engine')
    if engine:
        label={'rpgmaker':'RPG Maker','renpy':'Ren’Py'}.get(engine,engine)
        if settings.get('engine_variant'):label+=' '+settings['engine_variant']
        lines.append('- 엔진: '+label)
    if settings.get('source_language'):
        lines.append('- 원문 언어: '+{'english':'영어','japanese':'일본어','en':'영어','ja':'일본어'}.get(settings['source_language'],settings['source_language']))
    if settings.get('model'):lines.append('- 모델: '+settings['model'])
    if report.get('tasks'):lines.append('- 선택 작업: '+', '.join(TASKS[task] for task in report['tasks'] if task in TASKS))
    options=[]
    for key,label in (('num_ctx','컨텍스트'),('parallel','동시 작업'),('batch_size','배치 크기'),
                      ('kv_cache_type','KV 캐시'),('context_txt_policy','문맥 정책'),('cuda_diagnostic_mode','CUDA 진단')):
        if key in settings:options.append(label+' '+str(settings[key]))
    if options:lines.append('- 설정: '+', '.join(options))
    for device in report.get('gpus',[]):
        label=device.get('name','GPU')
        if 'total_mib' in device:label+=' ('+str(device['total_mib'])+' MiB)'
        lines.append('- GPU: '+label)
    runtime=report.get('runtime',{})
    if runtime:lines.append('- 런타임: '+', '.join(str(runtime[key]) for key in ('runtime_id','api_version') if key in runtime))
    counts=report.get('counts',{})
    labels={'completed':'처리 완료','total':'전체','successful':'번역 완료','original_kept':'원문 유지',
            'failed':'실패','files':'파일','items':'요청 항목','accepted':'유지된 항목','affected':'문제 항목',
            'retry_count':'재시도','retries':'재시도','cached':'기존 저장 항목','missing':'누락',
            'exit_code':'종료 코드','http_status':'HTTP 상태'}
    if counts:lines.append('- 작업 수치: '+', '.join(labels[key]+' '+str(counts[key]) for key in labels if key in counts))
    budget=report.get('budget',{})
    values=[]
    for key,label in (('prompt_content_tokens','입력 내용'),('actual_prompt_tokens','확인된 입력'),
                      ('output_reserved','출력 여유'),('num_ctx_requested','컨텍스트 한도'),
                      ('framing_reserved','형식 여유'),('output_tokens','출력'),('num_predict','출력 한도')):
        if key in budget:values.append(label+' '+str(budget[key]))
    if values:lines.append('- 토큰 예산: '+', '.join(values))
    if budget.get('tokenizer'):lines.append('- 입력량 산정: '+{'owned_model_tokenizer':'사용 중인 모델의 토크나이저','conservative_estimate':'보수적 추정'}.get(budget['tokenizer'],budget['tokenizer']))
    frames=report.get('tool_frames',[])
    if frames:lines.append('- 도구 호출 위치: '+', '.join(frame['module']+':'+str(frame['line']) for frame in frames))
    notices=report.get('read_notices',[])
    for item in notices[:10]:
        values=[item[key] for key in ('file','error_type','code') if key in item]
        if 'count' in item:values.append(str(item['count'])+'건')
        lines.append('- 자료 읽기 진단: '+', '.join(values))
    if len(notices)>10:lines.append('- 추가 자료 읽기 진단: '+str(len(notices)-10)+'건')
    return '\n'.join(lines)


def export_report(root,log_path,destination,job_context=None):
    """Create one new JSON report; never overwrite an existing user or log file."""
    if not isinstance(destination,(str,Path)) or not str(destination) or '://' in str(destination) or str(destination).startswith('\\\\'):
        raise ValueError('로컬 JSON 제보 파일의 저장 위치를 선택하세요.')
    target = Path(destination)
    if target.suffix.lower() != '.json':raise ValueError('제보 파일은 JSON 형식으로 저장하세요.')
    report = build_report(root,log_path,job_context)
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('x',encoding='utf-8') as stream:stream.write(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return str(target.resolve())


def failure_message(exc):
    if isinstance(exc,FileExistsError):return '같은 이름의 파일이 있습니다. 다른 이름으로 저장하세요.'
    if isinstance(exc,PermissionError):return '저장 위치의 접근 권한을 확인하세요.'
    return '제보 정보를 만들지 못했습니다. 오류 기록과 저장 위치를 확인하세요.'
