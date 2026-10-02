"""Build a portable ZIP from an explicit asset allowlist; never read projects/games."""
import argparse
import json
import filecmp
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
from development_setup import prepare_full_model, verify_vendor
from runtime_assets import inside, load_lock, runtime_executable

ROOT=Path(__file__).resolve().parents[1]


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


def write_archive(bundle, edition):
    archive=ROOT/'dist'/('RenPyTranslator-'+edition+'.zip');temp=archive.with_suffix('.zip.tmp')
    files=[p for p in bundle.rglob('*') if p.is_file()]
    total=sum(p.stat().st_size for p in files);done=0
    with zipfile.ZipFile(temp,'w',allowZip64=True) as z:
        for relative in ('project/','data/','models/'):
            z.writestr('RenPyTranslator/'+relative,b'')
        for path in files:
            relative=path.relative_to(bundle);size=path.stat().st_size
            compression=zipfile.ZIP_STORED if size>100_000_000 or 'blobs' in relative.parts else zipfile.ZIP_DEFLATED
            z.write(path,'RenPyTranslator/'+relative.as_posix(),compress_type=compression,compresslevel=1)
            done+=size
            if size>100_000_000:print(edition+' ZIP %.1f%%: %s'%(done*100/total,relative),flush=True)
    temp.replace(archive)
    print('PORTABLE_ZIP '+str(archive)+'; BYTES '+str(archive.stat().st_size),flush=True)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--edition',choices=('light','full','both'),type=str.lower,default='light')
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
    (ui_app/'package.json').write_text(json.dumps({'name':'renpy-translator-desktop','version':'1.0.0','main':'main.js'}),encoding='utf-8')
    def copy(source,relative):
        target=bundle/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)
    for name in ('translate.ps1','gui.ps1','README.md','runtime-lock.json','THIRD_PARTY_NOTICES.md','licenses/Hy-MT2-LICENSE.txt','docs/grok-context-preparation.txt','docs/context-runtime.md'):
        copy(ROOT/name,Path(name))
    for area in ('vendor/fonts','vendor/unrpyc',load_lock(ROOT)['ollama']['directory']):
        for source in (ROOT/area).rglob('*'):
            if not source.is_file() or '__pycache__' in source.parts or '.git' in source.parts:continue
            if source.name in ('runtime.zip','verification.json','asset-verification.json') or source.name.endswith('.setup-part') or source.suffix in ('.pyc','.pyo'):continue
            copy(source,source.relative_to(ROOT))
    copy(ROOT/'vendor/asset-lock.json',Path('vendor/asset-lock.json'))
    copy(ROOT/'vendor/versions.json',Path('vendor/versions.json'))
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
    if args.edition in ('light','both'):
        write_archive(bundle,'light')
    if args.edition in ('full','both'):
        export_model(bundle)
        write_archive(bundle,'full')
    publish_local(bundle)


if __name__=='__main__':main()
