"""Read-only desktop status and explicit task handoff. No implicit game access."""
import csv
import ctypes
from ctypes import wintypes
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.request

from app_paths import ROOT
from hy_backend import DEFAULT_MODEL
from task_plan import TASKS, FIXES, arguments

DEFAULTS = dict(output='project', suffix='-kr', scale='default', language_corner='right',
                language_margin=12, source_language='english', theme='system', gpu_mode='auto', gpu_ids=[])


def preferences():
    try:
        saved = json.loads((ROOT/'data/gui-settings.json').read_text(encoding='utf-8'))
        return dict(DEFAULTS, **{k:v for k,v in saved.items() if k in DEFAULTS})
    except (OSError, ValueError, AttributeError):
        return dict(DEFAULTS)


def validate_settings(data):
    result = dict(DEFAULTS, **{k:v for k,v in data.items() if k in DEFAULTS})
    from source_language import normalize
    result['source_language']=normalize(result['source_language'])
    for key in ('output', 'suffix', 'scale'):
        if not isinstance(result[key], str) or len(result[key]) > 4096:
            raise ValueError('저장 설정을 확인하세요.')
    if result['theme'] not in ('system','light','dark'):
        raise ValueError('화면 테마를 확인하세요.')
    if result['gpu_mode'] not in ('auto','all','selected'):
        raise ValueError('GPU 사용 방식을 확인하세요.')
    ids = result['gpu_ids']
    if not isinstance(ids, list) or any(not isinstance(i,str) or not re.fullmatch(r'GPU-[a-fA-F0-9-]+',i) for i in ids):
        raise ValueError('GPU 장치 목록을 확인하세요.')
    result['gpu_ids'] = list(dict.fromkeys(ids))
    if result['gpu_mode'] == 'selected' and not ids:
        raise ValueError('사용할 NVIDIA GPU를 하나 이상 선택하세요.')
    if result['language_corner'] not in ('left','right') or not 0 <= int(result['language_margin']) <= 300:
        raise ValueError('언어 패널 위치와 여백을 확인하세요.')
    result['language_margin'] = int(result['language_margin'])
    scale = result['scale'].strip() or 'default'
    if scale != 'default' and not .25 <= float(scale) <= 4:
        raise ValueError('높이 배율은 0.25~4 또는 default입니다.')
    result['scale'] = scale
    return result


def save_settings(data):
    from engine import save_json
    result = validate_settings(data)
    save_json(ROOT/'data/gui-settings.json', result)
    return result


def command(args, timeout=5):
    p = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace',
                       timeout=timeout, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if p.returncode:
        raise OSError(p.stderr.strip() or 'Device query failed')
    return p.stdout.lstrip('\ufeff')


def smi_path():
    for path in (Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/nvidia-smi.exe',
                 Path(os.environ.get('ProgramFiles','C:/Program Files'))/'NVIDIA Corporation/NVSMI/nvidia-smi.exe'):
        if path.is_file(): return str(path)
    return shutil.which('nvidia-smi')


def number(value):
    try: return float(value.strip())
    except (ValueError, AttributeError): return None


def nvidia_devices(smi=None):
    smi = smi or smi_path()
    if not smi: return []
    text = command([smi, '--query-gpu=uuid,name,memory.total,memory.used,utilization.gpu,driver_version', '--format=csv,noheader,nounits'])
    devices = []
    for row in csv.reader(io.StringIO(text), skipinitialspace=True):
        if len(row) != 6: continue
        uid,name,total,used,util,driver = row
        devices.append(dict(id=uid.strip(), name=name.strip(), total=number(total), used=number(used),
                            utilization=number(util), driver=driver.strip(), selectable=True, active=None))
    return devices


def gpu_environment(settings):
    mode = settings.get('gpu_mode','auto')
    if mode == 'auto': return {}
    if mode == 'all':
        # Remove masks in this child only. An empty CUDA mask can hide every GPU.
        return dict(OLLAMA_SCHED_SPREAD='1', CUDA_VISIBLE_DEVICES=None, HIP_VISIBLE_DEVICES=None,
                    ROCR_VISIBLE_DEVICES=None, GGML_VK_VISIBLE_DEVICES=None, GPU_DEVICE_ORDINAL=None)
    if mode != 'selected': raise ValueError('Unknown GPU policy')
    ids = settings.get('gpu_ids', [])
    validate_settings(dict(settings, gpu_mode='selected'))
    detected = {d['id'] for d in nvidia_devices()}
    if not set(ids).issubset(detected):
        raise ValueError('선택한 GPU가 연결되어 있지 않습니다. 설정 및 정보에서 장치를 다시 선택하세요.')
    return dict(CUDA_VISIBLE_DEVICES=','.join(ids), OLLAMA_SCHED_SPREAD='1' if len(ids)>1 else '0',
                HIP_VISIBLE_DEVICES='-1', ROCR_VISIBLE_DEVICES='-1', OLLAMA_VULKAN='0')


def descendants(roots):
    """PID relationships only, no process command lines or unrelated file reads."""
    if os.name != 'nt': return set(roots)
    class Entry(ctypes.Structure):
        _fields_ = [('size',wintypes.DWORD),('usage',wintypes.DWORD),('pid',wintypes.DWORD),
                    ('heap',ctypes.c_size_t),('module',wintypes.DWORD),('threads',wintypes.DWORD),
                    ('parent',wintypes.DWORD),('priority',wintypes.LONG),('flags',wintypes.DWORD),
                    ('exe',wintypes.WCHAR*260)]
    k = ctypes.windll.kernel32
    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k.Process32FirstW.argtypes = [wintypes.HANDLE,ctypes.POINTER(Entry)]
    k.Process32NextW.argtypes = [wintypes.HANDLE,ctypes.POINTER(Entry)]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = k.CreateToolhelp32Snapshot(2,0)
    if handle == ctypes.c_void_p(-1).value: return set()
    parents = {}
    try:
        entry = Entry(); entry.size = ctypes.sizeof(entry)
        more = k.Process32FirstW(handle,ctypes.byref(entry))
        while more:
            parents[entry.pid] = entry.parent
            more = k.Process32NextW(handle,ctypes.byref(entry))
    finally: k.CloseHandle(handle)
    result = set(roots) & parents.keys()
    while True:
        new = {pid for pid,parent in parents.items() if parent in result}-result
        if not new: return result
        result.update(new)


def runtime_info():
    result = []; owners = set()
    folder = ROOT/'data/runtime'
    if not folder.exists(): return result,owners
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for path in folder.glob('*.json'):
        try:
            info = json.loads(path.read_text(encoding='utf-8'))
            endpoint = info['endpoint']
            if not re.fullmatch(r'http://127\.0\.0\.1:\d+', endpoint): continue
            pids = descendants([int(info['pid'])])
            if not pids: continue
            with opener.open(endpoint+'/api/ps',timeout=1) as response:
                loaded = json.load(response).get('models',[])
            owners.update(pids)
            result.append(dict(configured_model=info['model'], loaded=[{
                'name':m.get('name',m.get('model','')), 'size':m.get('size'),
                'vram':m.get('size_vram'), 'quantization':m.get('details',{}).get('quantization_level')
            } for m in loaded], policy=info.get('policy',{})))
        except (OSError,ValueError,KeyError,TypeError): continue
    return result,owners


def snapshot():
    warnings = []; devices = []
    active,owners = runtime_info()
    smi = smi_path()
    if smi:
        try:
            devices = nvidia_devices(smi)
            text = command([smi,'--query-compute-apps=gpu_uuid,pid','--format=csv,noheader,nounits'])
            busy = {row[0].strip() for row in csv.reader(io.StringIO(text)) if len(row)==2 and row[1].strip().isdigit() and int(row[1]) in owners}
            for device in devices: device['active'] = device['id'] in busy
        except (OSError,subprocess.TimeoutExpired) as exc:
            warnings.append('NVIDIA 상태를 확인할 수 없습니다: '+str(exc))
    if os.name == 'nt':
        try:
            shell = str(Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe')
            text = command([shell,'-NoProfile','-NonInteractive','-Command',
                '[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); @(Get-CimInstance Win32_VideoController | Select-Object Name,PNPDeviceID,DriverVersion) | ConvertTo-Json -Compress'],timeout=8)
            adapters = json.loads(text or '[]')
            if isinstance(adapters,dict): adapters=[adapters]
            for gpu in adapters:
                if any(d['name']==gpu['Name'] for d in devices): continue
                devices.append(dict(id=gpu.get('PNPDeviceID',gpu['Name']), name=gpu['Name'],
                    total=None,used=None,utilization=None,driver=gpu.get('DriverVersion'),selectable=False,active=None))
        except (OSError,ValueError,subprocess.TimeoutExpired):
            if not devices: warnings.append('GPU 장치 정보를 읽지 못했습니다. 드라이버를 확인하세요.')
    return dict(root=str(ROOT), settings=preferences(), tasks=TASKS, fixes=sorted(FIXES),
                defaultModel=__import__('model_store').selected() or '', runtime='Ollama '+__import__('runtime_assets').load_lock()['ollama']['version'], models=active, gpus=devices, warnings=warnings)


def run_request(data):
    settings = validate_settings(data.get('settings',{}))
    args = arguments(data.get('tasks',[]),data.get('path',''),data.get('source',False),
        settings['output'],settings['suffix'],settings['scale'],settings['language_corner'],settings['language_margin'],settings['source_language'])
    from model_store import needs_model,require_selected
    if needs_model(data.get('tasks',[])):args+=['--model',require_selected()]
    save_settings(settings)
    print('실행: '+subprocess.list2cmdline(args),flush=True)
    sys.argv = [sys.argv[0]]+args
    from translate_game import main
    main()


def project_language(data,root=None):
    """Read only explicitly selected project metadata, never infer from a script."""
    from source_language import normalize
    root=ROOT if root is None else root
    path=Path(data.get('path',''))
    if not data.get('path'):return {'source_language':None}
    if data.get('source'):
        from automatic import source_key
        key=source_key(path.resolve())
        for parent in (root/'projects',root/'data/projects'):
            registry=parent/'index.json'
            if not registry.is_file():continue
            relative=json.loads(registry.read_text(encoding='utf-8')).get(key)
            if relative:
                candidate=(parent/relative).resolve()
                if not candidate.is_relative_to(parent.resolve()):raise ValueError('Invalid project registry path')
                path=candidate
                break
        else:return {'source_language':None}
    if path.is_dir():
        link=path/'project-link.json'
        if link.is_file():path=(path/json.loads(link.read_text(encoding='utf-8'))['project']).resolve()
        path=path/'project.json'
    if not path.is_file() or path.name!='project.json':return {'source_language':None}
    cfg=json.loads(path.read_text(encoding='utf-8-sig'))
    return {'source_language':normalize(cfg.get('source_language'))}


def read_request():
    text = sys.stdin.read(65537)
    if len(text)>65536: raise ValueError('설정 데이터가 너무 큽니다.')
    result=json.loads(text.lstrip('\ufeff'))
    if not isinstance(result,dict): raise ValueError('설정 형식이 올바르지 않습니다.')
    return result
