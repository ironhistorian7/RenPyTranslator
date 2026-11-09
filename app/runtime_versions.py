"""Explicit, versioned Ollama bundles; models remain in models/ollama.

Listing installed bundles is offline. Network access happens only in releases()
or install(); neither function selects a runtime or changes an existing bundle.
"""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import re
import shutil
import stat
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import zipfile

_API='https://api.github.com/repos/ollama/ollama/releases'
_ASSET='ollama-windows-amd64.zip'
_META='.rpt-runtime.json'
_VERSION=re.compile(r'(?:ollama-)?v?(\d+\.\d+\.\d+(?:[-+][A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*)?)\Z')
_SHA=re.compile(r'[0-9a-fA-F]{64}\Z')
_CACHE={}
_CACHE_LOCK=threading.Lock()
_INSTALL_LOCK=threading.Lock()


def _root(root=None):
    if root is None:
        from app_paths import ROOT
        root=ROOT
    return Path(root).resolve()


def normalize_id(runtime_id):
    match=_VERSION.fullmatch(str(runtime_id or ''))
    if not match:raise ValueError('Invalid Ollama runtime version: '+str(runtime_id))
    return 'ollama-'+match[1]


def _default(root):
    from runtime_assets import load_lock,inside
    item=load_lock(root)['ollama']
    runtime_id=normalize_id(item['version'])
    return dict(item,id=runtime_id,directory=str(inside(root,item['directory'])))


def _compatibility_hashes(root):
    """Keep trusted legacy bundles downloadable when GitHub has no digest."""
    from runtime_assets import load_lock
    result={}
    for item in load_lock(root).get('ollama_compatibility',[]):
        if not isinstance(item,dict):continue
        try:runtime_id=normalize_id(item['version'])
        except (KeyError,ValueError):continue
        digest=item.get('sha256','')
        if isinstance(digest,str) and _SHA.fullmatch(digest):
            result[runtime_id]=digest.lower()
    return result


def _bundle_complete(directory):
    if not (directory/'ollama.exe').is_file():return False
    libraries=directory/'lib/ollama'
    if not libraries.is_dir():return False
    # Require a native payload as well as the CLI. Never combine DLLs from a
    # different version to turn a partial folder into an installed runtime.
    return any(path.is_file() and path.suffix.lower() in ('.exe','.dll')
               for path in libraries.rglob('*'))


def _record(root,runtime_id,directory,default):
    from runtime_assets import inside
    directory=inside(root,directory)
    if not _bundle_complete(directory):return None
    metadata={}
    path=directory/_META
    if path.is_file():
        try:metadata=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError):metadata={}
    managed=metadata.get('id')==runtime_id and metadata.get('version')==runtime_id[7:]
    return {'id':runtime_id,'version':runtime_id[7:],
            'name':'Ollama '+runtime_id[7:],
            'directory':str(directory),'executable':str(directory/'ollama.exe'),
            'installed':True,'default':runtime_id==default['id'],
            'source':'official_download' if managed else ('bundled' if runtime_id==default['id'] else 'local_folder'),
            'archive_verified':bool(managed and metadata.get('archive_verified')),
            'sha256':metadata.get('sha256') if managed else None,
            'installed_at':metadata.get('installed_at') if managed else None}


def installed(root=None):
    """Return complete local bundles only; do not start Ollama or use the network."""
    root=_root(root);default=_default(root);result={}
    candidate=_record(root,default['id'],default['directory'],default)
    if candidate:result[candidate['id']]=candidate
    base=root/'runtimes'
    if base.is_dir():
        for directory in base.glob('ollama-*'):
            if not directory.is_dir():continue
            try:
                runtime_id=normalize_id(directory.name)
                item=_record(root,runtime_id,directory,default)
            except ValueError:continue
            if item and runtime_id not in result:result[runtime_id]=item
    def order(item):
        version=item['version'].split('-',1)[0].split('+',1)[0]
        return (not item['default'],tuple(-int(v) for v in version.split('.')),item['id'])
    return sorted(result.values(),key=order)


def resolve(runtime_id=None,root=None):
    """Validate a selection and return its complete bundle; never silently fall back."""
    root=_root(root)
    wanted=normalize_id(runtime_id) if runtime_id else _default(root)['id']
    for item in installed(root):
        if item['id']==wanted:return item
    raise ValueError('Ollama runtime is not installed as a complete bundle: '+wanted)


def _open(request,timeout):
    return urllib.request.urlopen(request,timeout=timeout)


def _json(url):
    request=urllib.request.Request(url,headers={'Accept':'application/vnd.github+json',
        'User-Agent':'RenPyTranslator-runtime-manager','X-GitHub-Api-Version':'2022-11-28'})
    with _open(request,30) as response:
        raw=response.read(4*1024*1024+1)
    if len(raw)>4*1024*1024:raise ValueError('Ollama release metadata is too large')
    return json.loads(raw)


def _release_record(release,default,compatibility_hashes=None):
    if not isinstance(release,dict) or release.get('draft'):return None
    try:runtime_id=normalize_id(release['tag_name'])
    except (KeyError,ValueError):return None
    asset=next((a for a in release.get('assets',[]) if a.get('name')==_ASSET),None)
    if asset is None:return None
    version=runtime_id[7:]
    expected_url='https://github.com/ollama/ollama/releases/download/v'+version+'/'+_ASSET
    if asset.get('browser_download_url')!=expected_url:return None
    digest=asset.get('digest') or ''
    sha=digest[7:].lower() if digest.startswith('sha256:') and _SHA.fullmatch(digest[7:]) else None
    if sha is None and runtime_id==default['id'] and _SHA.fullmatch(default.get('sha256','')):
        sha=default['sha256'].lower()
    if sha is None:
        sha=(compatibility_hashes or {}).get(runtime_id)
    return {'id':runtime_id,'version':version,'name':'Ollama '+version,
            'published_at':release.get('published_at'),
            'prerelease':bool(release.get('prerelease')),
            'release_url':'https://github.com/ollama/ollama/releases/tag/v'+version,
            'url':expected_url,'size':asset.get('size'),'sha256':sha,
            'available':sha is not None,
            'unavailable_reason':None if sha else 'Official SHA256 is unavailable for this bundle.'}


def releases(root=None,limit=20,refresh=False):
    """Query official Windows x64 releases. Download and selection remain explicit."""
    root=_root(root);limit=max(1,min(int(limit),100));default=_default(root)
    compatibility_hashes=_compatibility_hashes(root)
    now=time.monotonic()
    with _CACHE_LOCK:
        cached=_CACHE.get(limit)
    if cached and not refresh and now-cached[0]<300:raw=cached[1]
    else:
        raw=_json(_API+'?per_page='+str(limit))
        if not isinstance(raw,list):raise ValueError('Invalid Ollama release list')
        with _CACHE_LOCK:_CACHE[limit]=(now,raw)
    local={item['id']:item for item in installed(root)}
    items=[]
    for release in raw:
        item=_release_record(release,default,compatibility_hashes)
        if item is not None:
            item.update(installed=item['id'] in local,default=item['id']==default['id'])
            items.append(item)
    return items


def _emit(progress,phase,**details):
    if progress is not None:progress(dict(phase=phase,**details))


def _download(url,target,size,progress,cancel):
    request=urllib.request.Request(url,headers={'User-Agent':'RenPyTranslator-runtime-manager'})
    digest=hashlib.sha256();received=0;reported=0.0
    with _open(request,60) as response,target.open('wb') as output:
        while True:
            if cancel is not None:cancel()
            block=response.read(1024*1024)
            if not block:break
            output.write(block);digest.update(block);received+=len(block)
            if size and received>size:raise ValueError('Ollama bundle exceeds its official asset size')
            current=time.monotonic()
            if current-reported>=.25:
                _emit(progress,'download',received=received,total=size);reported=current
    if size and received!=size:raise ValueError('Incomplete Ollama bundle download')
    _emit(progress,'download',received=received,total=size)
    return digest.hexdigest()


def _extract(archive,target,cancel):
    names=set()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            if cancel is not None:cancel()
            name=member.filename.replace('\\','/')
            path=PurePosixPath(name)
            if (path.is_absolute() or '..' in path.parts or ':' in name or '\x00' in name
                    or stat.S_ISLNK(member.external_attr>>16)):
                raise ValueError('Unsafe path in Ollama runtime archive')
            destination=target.joinpath(*path.parts)
            if not destination.resolve().is_relative_to(target.resolve()):
                raise ValueError('Ollama archive path leaves the install directory')
            if member.is_dir():destination.mkdir(parents=True,exist_ok=True);continue
            key=name.casefold()
            if key in names:raise ValueError('Duplicate file in Ollama runtime archive')
            names.add(key);destination.parent.mkdir(parents=True,exist_ok=True)
            with bundle.open(member) as source,destination.open('wb') as output:
                shutil.copyfileobj(source,output,1024*1024)
    if _bundle_complete(target):return target
    children=[p for p in target.iterdir() if p.is_dir() and _bundle_complete(p)]
    if len(children)==1:return children[0]
    raise ValueError('The archive does not contain a complete Windows Ollama bundle')


def install(runtime_id,root=None,progress=None,cancel=None):
    """Explicitly install one official full bundle; verify SHA256 before extraction."""
    root=_root(root);runtime_id=normalize_id(runtime_id);default=_default(root)
    with _INSTALL_LOCK:
        for item in installed(root):
            if item['id']==runtime_id:return dict(item,already_installed=True)
        from runtime_assets import inside
        destination=inside(root,'runtimes/'+runtime_id)
        if destination.exists():
            raise ValueError('An incomplete runtime folder already exists: '+str(destination))
        _emit(progress,'metadata',runtime_id=runtime_id)
        release=_json(_API+'/tags/'+urllib.parse.quote('v'+runtime_id[7:],safe=''))
        item=_release_record(release,default,_compatibility_hashes(root))
        if item is None or item['id']!=runtime_id:
            raise ValueError('Official Windows x64 Ollama bundle was not found: '+runtime_id)
        if not item['available']:raise ValueError(item['unavailable_reason'])
        downloads=inside(root,'runtimes/.downloads');downloads.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='rpt-runtime-',dir=downloads) as temporary:
            work=Path(temporary).resolve()
            if not work.is_relative_to(downloads):raise ValueError('Invalid runtime temporary directory')
            archive=work/'bundle.zip';payload=work/'payload';payload.mkdir()
            actual=_download(item['url'],archive,item['size'],progress,cancel)
            _emit(progress,'verify',runtime_id=runtime_id)
            if actual!=item['sha256']:raise ValueError('Ollama runtime SHA256 mismatch')
            _emit(progress,'extract',runtime_id=runtime_id)
            bundle=_extract(archive,payload,cancel)
            metadata=dict(schema=1,id=runtime_id,version=item['version'],source=item['release_url'],
                url=item['url'],sha256=actual,archive_verified=True,
                installed_at=datetime.now(timezone.utc).isoformat())
            (bundle/_META).write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            if cancel is not None:cancel()
            # A complete directory is published only after download, hash and ZIP
            # CRC checks. Existing versions, settings and model files are untouched.
            os.rename(bundle,destination)
        _emit(progress,'complete',runtime_id=runtime_id)
        return dict(resolve(runtime_id,root),already_installed=False)
