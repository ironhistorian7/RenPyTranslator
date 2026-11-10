"""Build a portable ZIP from an explicit asset allowlist; never read projects/games."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from development_setup import prepare_full_model, verify_vendor, ollama_assets
from runtime_assets import inside, load_lock, runtime_executable
from release_archives import package_editions, vendor_file_allowed, runtime_file_allowed, DOCUMENTATION_FILES

ROOT=Path(__file__).resolve().parents[1]
LOCAL_COMPONENTS=('_internal', '_desktop', 'RenPyTranslator-cli.exe', 'RenPyTranslator.exe')


def copy_vendor_assets(root, bundle, copy):
    """Copy pinned runtime code/fonts only, never an upstream sample game tree."""
    inventory = json.loads((root/'vendor/asset-lock.json').read_text(encoding='utf-8'))
    selected = {name: digest for name, digest in inventory['files'].items() if vendor_file_allowed(name)}
    for name in selected: copy(inside(root, name), Path(name))
    upstream_path = root/'vendor/gameloc/upstream.json'
    if upstream_path.is_file():
        upstream = json.loads(upstream_path.read_text(encoding='utf-8'))
        source_files = {name: digest for name, digest in upstream['files'].items()
                        if vendor_file_allowed('vendor/gameloc/'+name)}
        for name in source_files:
            copy(inside(root/'vendor/gameloc', name), Path('vendor/gameloc')/name)
        metadata = bundle/'vendor/gameloc/upstream.json'; metadata.parent.mkdir(parents=True, exist_ok=True)
        metadata.write_text(json.dumps(dict(upstream, files=source_files), ensure_ascii=False, indent=2), encoding='utf-8')
    # The upstream README is optional documentation, not an executable helper.
    readme = root/'vendor/unrpyc/README.md'
    if readme.is_file(): copy(readme, Path('vendor/unrpyc/README.md'))
    metadata = bundle/'vendor/asset-lock.json'; metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text(json.dumps(dict(inventory, files=selected), indent=2), encoding='utf-8')
    copy(root/'vendor/versions.json', Path('vendor/versions.json'))


def _local_entries(root, path):
    """Validate an app component without following links into user assets."""
    entries, pending = [], [Path(path)]
    while pending:
        entry = pending.pop()
        inside(root, entry)
        if entry.is_symlink() or entry.is_junction():
            raise ValueError('Linked local application asset is forbidden: '+str(entry))
        entries.append(entry)
        if entry.is_dir():
            pending.extend(entry.iterdir())
    return entries


def _active_local_processes(root):
    if os.name != 'nt':
        return []
    # Query executable paths only: command lines can contain private game paths.
    command = ("$ErrorActionPreference='Stop'; "
               "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); "
               "Get-CimInstance Win32_Process | "
               "Select-Object ProcessId,Name,ExecutablePath | ConvertTo-Json -Compress")
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                                capture_output=True, text=True, encoding='utf-8', check=True, timeout=15,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        processes = json.loads(result.stdout or '[]')
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise RuntimeError('Cannot check running applications; local publish was not started') from error
    if isinstance(processes, dict):
        processes = [processes]
    active = []
    names = {'renpytranslator.exe', 'renpytranslator-cli.exe', 'renpytranslator-ui.exe'}
    for process in processes:
        executable = process.get('ExecutablePath')
        if executable:
            path = Path(executable).resolve()
            if (path.parent == root and path.name.lower() in names) or any(
                    path.is_relative_to(root/name) for name in ('_internal', '_desktop')):
                active.append(str(process['ProcessId']))
        elif str(process.get('Name', '')).lower() in names:
            # Do not assume an inaccessible app process belongs to another copy.
            active.append(str(process['ProcessId']))
    return active


def _probe_local_path(path):
    if os.name != 'nt':
        return
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel32.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel32.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    # DELETE access, all share modes, OPEN_EXISTING, directory-compatible flag.
    # This changes no bytes and detects handles that would block the rename.
    handle = create(str(path), 0x10000, 7, None, 3, 0x02000000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise OSError(ctypes.get_last_error(), 'Local application file is locked or inaccessible', str(path))
    close(handle)


def _check_local_publish(root):
    active = _active_local_processes(root)
    if active:
        raise RuntimeError('Close RenPyTranslator before local publish (running PID: '+', '.join(active)+')')
    for name in LOCAL_COMPONENTS:
        target = root/name
        if target.exists() or target.is_symlink() or target.is_junction():
            for entry in _local_entries(root, target):
                _probe_local_path(entry)


def _move_local(root, source, target):
    # Check both absolute paths immediately before every move.
    source = inside(root, source)
    target = inside(root, target)
    source.rename(target)


def _remove_local_stage(root, stage):
    stage = inside(root, stage)
    if stage.parent != inside(root, root/'build') or not stage.name.startswith('.local-publish-'):
        raise ValueError('Unexpected local publish staging directory: '+str(stage))
    shutil.rmtree(stage)


def publish_local(bundle):
    """Explicitly replace complete app components, restoring the old set on error."""
    root = ROOT.resolve()
    bundle = inside(root, bundle)
    if bundle != (root/'dist/RenPyTranslator').resolve():
        raise ValueError('Unexpected executable bundle location')
    for name in LOCAL_COMPONENTS:
        source = bundle/name
        if not (source.is_dir() if name.startswith('_') else source.is_file()):
            raise ValueError('Incomplete executable bundle: '+name)
        _local_entries(root, source)
    _check_local_publish(root)
    staging_parent = inside(root, root/'build')
    staging_parent.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.local-publish-', dir=staging_parent))
    previous, pending = stage/'previous', stage/'pending'
    keep_stage = False
    try:
        previous.mkdir()
        pending.mkdir()
        for name in LOCAL_COMPONENTS:
            source, target = bundle/name, pending/name
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
        # Staging can take time. Refuse a newly opened app before moving anything.
        _check_local_publish(root)
        moved, installed = [], []
        try:
            # Retire launchers first; restore them last if a later move fails.
            retirement_order = sorted(LOCAL_COMPONENTS, key=lambda name: name.startswith('_'))
            for name in retirement_order:
                if (root/name).exists():
                    _move_local(root, root/name, previous/name)
                    moved.append(name)
            for name in LOCAL_COMPONENTS:
                _move_local(root, pending/name, root/name)
                installed.append(name)
        except BaseException as error:
            # Keep the backup even if restoration itself is interrupted.
            keep_stage = True
            rollback_errors = []
            for name in reversed(installed):
                try:
                    _move_local(root, root/name, pending/name)
                except Exception as rollback_error:
                    rollback_errors.append(rollback_error)
            for name in reversed(moved):
                try:
                    _move_local(root, previous/name, root/name)
                except Exception as rollback_error:
                    rollback_errors.append(rollback_error)
            if rollback_errors:
                raise RuntimeError('Local publish and rollback failed; recovery files retained at '+str(stage)) from error
            keep_stage = False
            raise
    finally:
        if not keep_stage:
            try:
                _remove_local_stage(root, stage)
            except OSError:
                # A complete new set (or restored old set) is already in place.
                print('Local publish staging cleanup pending: '+str(stage), file=sys.stderr)
    print('LOCAL_EXE '+str(root/'RenPyTranslator.exe'), flush=True)


def publish_build_output(bundle, editions, *, local_only=False):
    if local_only:
        publish_local(bundle)
    else:
        package_editions(ROOT, bundle, editions, export_model)


def export_model(bundle):
    asset=prepare_full_model(ROOT)
    model=asset['manifest']
    for entry in [model['config']]+model['layers']:
        blob=Path('models/ollama/blobs')/entry['digest'].replace(':','-')
        target=bundle/blob;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(ROOT/blob,target)
    manifest=inside(bundle,asset['manifest_path']);manifest.parent.mkdir(parents=True,exist_ok=True)
    manifest.write_text(json.dumps(model),encoding='utf-8')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--edition',choices=('light','full','both'),type=str.lower,default='light')
    parser.add_argument('--local-only',action='store_true',help='Update local EXE/runtime only; do not create release ZIPs')
    args=parser.parse_args(argv)
    verify_vendor(ROOT)
    node=runtime_executable('node',ROOT)
    desktop=ROOT/'desktop'
    subprocess.run([str(node),str(desktop/'node_modules/typescript/bin/tsc'),'--noEmit'],cwd=desktop,check=True)
    subprocess.run([str(node),str(desktop/'build.mjs')],cwd=desktop,check=True)
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--distpath',str(ROOT/'dist'),
                    '--workpath',str(ROOT/'build/work'),str(ROOT/'build/portable.spec')],cwd=ROOT,check=True)
    bundle=ROOT/'dist/RenPyTranslator'
    ui=bundle/'_desktop'
    shutil.copytree(desktop/'node_modules/electron/dist',ui,dirs_exist_ok=True)
    _move_local(ROOT, ui/'electron.exe', ui/'RenPyTranslator-UI.exe')
    ui_app=ui/'resources/app'
    shutil.copytree(desktop/'dist',ui_app,dirs_exist_ok=True)
    (ui_app/'package.json').write_text(json.dumps({'name':'renpy-translator-desktop','version':load_lock(ROOT)['release']['version'],'main':'main.js'}),encoding='utf-8')
    def copy(source,relative):
        if source.is_symlink() or source.is_junction():raise ValueError('Linked build asset is forbidden: '+str(relative))
        target=bundle/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)
    for name in ('translate.ps1','gui.ps1','README.md','LICENSE','runtime-lock.json','THIRD_PARTY_NOTICES.md','licenses/Hy-MT2-LICENSE.txt'):
        copy(ROOT/name,Path(name))
    for source in sorted((ROOT/'docs').iterdir()):
        if source.is_file() and source.name in DOCUMENTATION_FILES:
            copy(source,source.relative_to(ROOT))
    copy_vendor_assets(ROOT,bundle,copy)
    for asset in ollama_assets(load_lock(ROOT)):
        for source in inside(ROOT,asset['directory']).rglob('*'):
            if source.is_file() and runtime_file_allowed(source.relative_to(ROOT)):
                copy(source,source.relative_to(ROOT))
    (bundle/'models').mkdir(exist_ok=True)
    (bundle/'project').mkdir(exist_ok=True)
    (bundle/'data').mkdir(exist_ok=True)
    (bundle/'PORTABLE.txt').write_text('RenPyTranslator portable for Windows x64\n\nGUI: RenPyTranslator.exe\nCLI: RenPyTranslator-cli.exe --help\nMove this entire folder, including _desktop and _internal.\nNo system Python, Node.js, browser or Ollama installation is needed.\nSelect a translation model in the AI Model tab before translating.\nFULL includes Hy Q6_K. LIGHT includes no model: download in the app, or place a single chat/instruct GGUF anywhere under models and refresh the model list.\nGPU use requires a compatible driver. Translation is local.\nProjects, game files and translation caches are NOT included.\nOutput default: project/<game-name>-kr\n',encoding='utf-8')
    (bundle/'models/README.txt').write_text('Download in the AI Model tab, or put a GGUF anywhere below models and refresh.\nSelect and apply a model before translating.\nThe app manages ollama and .downloads subfolders.\n',encoding='utf-8')
    # Include third-party distribution notices from this isolated build environment.
    import importlib.metadata
    notices=bundle/'licenses';notices.mkdir(exist_ok=True)
    for package in ('fonttools','pyinstaller','packaging','altgraph','pefile','pywin32-ctypes'):
        dist=importlib.metadata.distribution(package)
        for item in dist.files or []:
            if any(word in item.name.lower() for word in ('license','copying','notice')):
                path=Path(dist.locate_file(item))
                if path.is_file():shutil.copy2(path,notices/(package+'-'+item.name))
    for name in ('LICENSE.txt','LICENSE'):
        path=Path(sys.base_prefix)/name
        if path.exists():copy(path,Path('licenses/Python-'+name))
    # A fresh PyInstaller collection contains no models. LIGHT never reads models.
    editions=['light','full'] if args.edition=='both' else [args.edition]
    publish_build_output(bundle, editions, local_only=args.local_only)


if __name__=='__main__':main()
