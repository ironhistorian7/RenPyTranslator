"""Build a portable ZIP from an explicit asset allowlist; never read projects/games."""
import argparse
import json
import filecmp
import os
from pathlib import Path
import shutil
import subprocess
import sys
from development_setup import prepare_full_model, verify_vendor, ollama_assets
from runtime_assets import inside, load_lock, runtime_executable
from release_archives import package_editions, vendor_file_allowed, runtime_file_allowed, DOCUMENTATION_FILES

ROOT=Path(__file__).resolve().parents[1]


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


def publish_local(bundle):
    """Install only the built launchers/runtime beside the existing tool assets."""
    bundle = bundle.resolve()
    if bundle != (ROOT/'dist/RenPyTranslator').resolve():
        raise ValueError('Unexpected executable bundle location')
    # Never copy project, data, model or source trees back from the distribution.
    # The existing tool already owns those assets; publish only the application runtimes.
    def copy_changed(source, target):
        # An open Electron window maps unchanged runtime files. Do not rewrite
        # those files merely to publish a Python/backend-only update.
        if not Path(target).is_file() or not filecmp.cmp(source,target,shallow=False):
            shutil.copy2(source,target)
        return target
    shutil.copytree(bundle/'_internal', ROOT/'_internal', dirs_exist_ok=True,copy_function=copy_changed)
    shutil.copytree(bundle/'_desktop', ROOT/'_desktop', dirs_exist_ok=True,copy_function=copy_changed)
    for name in ('RenPyTranslator-cli.exe', 'RenPyTranslator.exe'):
        copy_changed(bundle/name, ROOT/name)
    print('LOCAL_EXE '+str(ROOT/'RenPyTranslator.exe'), flush=True)


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
    (ui/'electron.exe').rename(ui/'RenPyTranslator-UI.exe')
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
    if not args.local_only:package_editions(ROOT,bundle,editions,export_model)
    publish_local(bundle)


if __name__=='__main__':main()
