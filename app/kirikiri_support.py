"""Optional, project-scoped KiriKiri UI assets and portable locale output."""
import hashlib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

from app_paths import ROOT
from engine import save_json
from kirikiri_containers import open_source,XP3Archive
from kirikiri_ui_assets import approved_source_names,localize_buttons


def _game_executable(folder):
    """Select an unambiguous KiriKiri PE by engine markers, never game name."""
    from portable_locale import pe_architecture
    found=[]
    for path in Path(folder).glob('*.exe'):
        if path.is_symlink():continue
        try:
            if pe_architecture(path) not in ('x86','x64'):continue
            data=path.read_bytes()
            if b'TTVPWindowForm' in data and b'TTVPMainForm' in data:found.append(path)
        except (OSError,ValueError):continue
    return found[0] if len(found)==1 else None


def _game_running(exe):
    import ctypes
    from ctypes import wintypes
    class Entry(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('usage',wintypes.DWORD),('pid',wintypes.DWORD),
                  ('heap',ctypes.c_size_t),('module',wintypes.DWORD),('threads',wintypes.DWORD),
                  ('parent',wintypes.DWORD),('priority',wintypes.LONG),('flags',wintypes.DWORD),
                  ('name',wintypes.WCHAR*260)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes=(wintypes.DWORD,wintypes.DWORD)
    kernel.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
    kernel.Process32FirstW.argtypes=kernel.Process32NextW.argtypes=(wintypes.HANDLE,ctypes.POINTER(Entry))
    kernel.CloseHandle.argtypes=(wintypes.HANDLE,)
    handle=kernel.CreateToolhelp32Snapshot(2,0)
    if handle==ctypes.c_void_p(-1).value:return True
    try:
        entry=Entry();entry.size=ctypes.sizeof(entry)
        more=kernel.Process32FirstW(handle,ctypes.byref(entry))
        while more:
            # Conservatively skip even a same-name executable at another path.
            if entry.name.casefold()==Path(exe).name.casefold():return True
            more=kernel.Process32NextW(handle,ctypes.byref(entry))
        return False
    finally:kernel.CloseHandle(handle)


def ensure_locale_profile(project,cfg=None):
    """Run an evidence-based startup comparison when an installed source exists.

    Unknown UI layouts, ISO-only sources and already-running games are left
    unclassified. Only PID-owned errors plus explicit title rendering can
    enable the portable launcher. This is called for the selected check task.
    """
    from portable_locale import detect_locale_need
    project=Path(project);cfg=cfg or {};path=project/'data/kirikiri-locale-profile.json'
    existing=json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}
    if detect_locale_need(existing)['status']!='unknown':return existing
    folder=Path(cfg.get('source',''))
    if not folder.is_dir():return existing
    exe=_game_executable(folder)
    if exe is None or _game_running(exe):return existing
    signature={'size':exe.stat().st_size,'mtime_ns':exe.stat().st_mtime_ns}
    if existing.get('source_exe')==str(exe) and existing.get('exe_state')==signature:return existing
    from kirikiri_boot_assets import prepare_menu_patterns
    from kirikiri_menu_probe import make_menu_readiness
    patterns=prepare_menu_patterns(project,cfg)
    callbacks=[make_menu_readiness(p) for p in patterns.values() if p]
    if not callbacks:return existing
    def ready(report):
        for callback in callbacks:
            result=callback(report)
            if result.get('ready'):return result
        return {'ready':False}
    from kirikiri_boot_probe import probe_locale_requirement
    result=probe_locale_requirement(exe,timeout=20,readiness=ready,terminate_owned=True)
    profile={'source_exe':str(exe),'target_relative':exe.name,'exe_state':signature,
             'launch_comparison':result}
    save_json(path,profile)
    if result['status']=='unknown':
        from job_events import warning
        warning('kirikiri_locale_unknown','로켈 기동 검사를 완료하지 못했습니다.')
    return profile


def ui_bank():
    return Path(getattr(sys,'_MEIPASS',ROOT))/'assets/kirikiri-ui'


def ui_overrides(project,cfg=None):
    from kirikiri_project import _verified,_archive_order
    project=Path(project); record=project/'data/kirikiri-ui-overrides.json'
    folder=project/'data/kirikiri-ui-overrides'
    saved={'files':{}};result={}
    if record.is_file():
        saved=json.loads(record.read_text(encoding='utf-8-sig'))
        for rel,entry in saved.get('files',{}).items():
            path=folder/entry['file']
            if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
                raise ValueError('버튼 이미지 경로가 올바르지 않습니다.')
            content=path.read_bytes()
            if hashlib.sha256(content).hexdigest()!=entry['sha256']:
                raise ValueError('저장된 버튼 이미지가 변경되었습니다.')
            result[rel]=content
    bank=ui_bank(); names=[name for name in approved_source_names(bank) if name not in result]
    if not names:return result
    approved={name.casefold():name for name in names}
    manifest=_verified(project); info=manifest.get('source',{})
    selected={}; originals={}
    origin=info.get('container_path') or (cfg or {}).get('source')
    if not origin or not Path(origin).exists():return result
    with open_source(origin) as source:
        archives,_=_archive_order(source,info.get('game_root',''))
        for archive_path in archives:
            archive=XP3Archive(source,archive_path)
            standard=bool(re.fullmatch(r'patch(?:\d+)?\.xp3',archive_path.rsplit('/',1)[-1],re.I))
            for physical in archive.entries:
                canonical=approved.get(physical.casefold())
                if canonical is None and standard and '/' not in physical:
                    possible=[n for n in names if n.rsplit('/',1)[-1].casefold()==physical.casefold()]
                    if len(possible)==1:canonical=possible[0]
                if canonical is not None:selected[canonical]=(archive,physical)
        prefix=info.get('game_root',''); prefix=prefix+'/' if prefix else ''
        for physical in source.entries:
            if physical.startswith(prefix):
                canonical=approved.get(physical[len(prefix):].casefold())
                if canonical is not None:selected[canonical]=(None,physical)
        for canonical,(archive,physical) in selected.items():
            originals[canonical]=archive.read(physical) if archive else source.read(physical)
    overrides,report=localize_buttons(originals,bank)
    if overrides:
        folder.mkdir(parents=True,exist_ok=True); files=dict(saved['files'])
        for rel,content in sorted(overrides.items()):
            filename=hashlib.sha256(content).hexdigest()+'.png'; (folder/filename).write_bytes(content)
            files[rel]={'file':filename,'sha256':hashlib.sha256(content).hexdigest()}
        save_json(record,{'version':1,'files':files,'report':report})
    result.update(overrides)
    return result


def locale_output(project,output,cfg=None,*,check_only=False):
    from portable_locale import install_portable_locale,install_portable_ui_launcher,detect_locale_need
    project=Path(project); output=Path(output)
    profile_path=project/'data/kirikiri-locale-profile.json'
    profile=json.loads(profile_path.read_text(encoding='utf-8-sig')) if profile_path.is_file() else {}
    decision=detect_locale_need(profile)
    native_ui=(output/'ui-native-menus.json').is_file()
    if decision['status']!='required' and not native_ui:return dict(decision,files=[])
    source_exe=profile.get('source_exe')
    target=profile.get('target_relative')
    if not source_exe or not target:
        source=Path((cfg or {}).get('source',''))
        executable=_game_executable(source) if source.is_dir() else None
        if executable is None:raise ValueError('메뉴 실행기를 만들 게임 실행 파일을 확인하지 못했습니다.')
        source_exe=str(executable);target=executable.name
    with tempfile.TemporaryDirectory(prefix='locale-output-',dir=project/'data') as temp:
        staged=Path(temp)
        installer=install_portable_ui_launcher if native_ui else install_portable_locale
        result=installer(staged,target,source_exe,profile)
        receipt_path=project/'data/kirikiri-locale-output.json'
        previous=json.loads(receipt_path.read_text(encoding='utf-8-sig')) if receipt_path.is_file() else {'files':{}}
        for rel in result['files']:
            dest=output/rel
            if dest.is_symlink() or not dest.resolve().is_relative_to(output.resolve()):
                raise ValueError('로켈 출력 경로가 올바르지 않습니다: '+rel)
            if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest()!=previous['files'].get(rel):
                raise ValueError('기존 로켈 실행 파일과 충돌합니다: '+rel)
        if check_only:return result
        receipt={}
        for rel in result['files']:
            dest=output/rel; dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(staged/rel,dest)
            receipt[rel]=hashlib.sha256(dest.read_bytes()).hexdigest()
        save_json(receipt_path,{'version':1,'files':receipt,'status':decision['status']})
        return result
