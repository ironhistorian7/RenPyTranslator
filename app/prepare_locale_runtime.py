"""Prepare verified locale build assets without examining games or projects.

Official archives are pinned; source-code correspondence to the two released
DLLs remains unverified as described in SOURCE-NOTICE.txt. This is a build
preparation step, never an installation or a game launch.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile

try:
    from native_process import run as native_run
except ImportError:
    from .native_process import run as native_run

_RELEASE = ('https://github.com/xupefei/Locale-Emulator/releases/download/v2.5.0.1/Locale.Emulator.2.5.0.1.zip', '808ff584426d52cc775ad6406da00622f454be95bd4c8fbca42eef4b7235ad5c')
_SOURCES = {
    'Locale-Emulator-v2.5.0.1-source.zip': ('https://codeload.github.com/xupefei/Locale-Emulator/zip/db03abf6914beeca09ee975120ff5ce2091c8dca', 'c0763b7e12a9b7b4daf172ce8790afcb0bc702014c8f05186c83f6b65f1c5c39'),
    'Locale-Emulator-Core-2021-08-23-source.zip': ('https://codeload.github.com/xupefei/Locale-Emulator-Core/zip/ae7160dc5deb97947396abcd784f9b98b6ee38b3', '80040eab31970ec1381bfa57a2c00b507501487efac0220dbd3cd8fb37007941'),
}
_DLLS = {'LoaderDll.dll':'82fae0f44f4ca0c9c37907df74cef2415eeB5fae1cf8d4f36f34ffcaf7e3cc0c'.lower(),
         'LocaleEmulator.dll':'c79c175fdad174aa46a72197d148316299a56f950aaab1b84930d09ee1084a88'}
_HELPERS = {
    'locale': ('PortableLocaleLauncher.cs', 'Play-Korean.exe', 'winexe', 'x86', 'System.Windows.Forms.dll', '085db0b1778c7aadfdbd5dfeb8ddd1398049ec0f2ebfb659893e0c1fa4b7c9e3', 'af3c27ddeaad4f157f1fb520f423c3f5b9557433ffbafd9a2854aacf7bdda7f5'),
    'menu': ('MenuBridge.cs', 'MenuBridge.exe', 'winexe', 'anycpu', 'System.Windows.Forms.dll;System.Web.Extensions.dll', '5e26358179884f8b274e61d66c4efdab709200e46eed89d0b10a8beec40214ca', 'a6397d29d0c27c0ecc1000cccced8b3968bcc8b85f1320bdc5288089ce58e817'),
    'probe': ('MsaaProbe.cs', 'MsaaProbe.exe', 'exe', 'anycpu', 'Accessibility.dll', '5a989eab8b14619eed091f750dbd151f62b507d6fb8b55771f1f9e78e25e6bf6', '86766e16d0d60b8233e3f783628d7952adc932c3cb8bb675670cd7e902087bfa'),
}
_NOTICES = ('COPYING', 'COPYING.LESSER', 'PROVENANCE.txt', 'SOURCE-NOTICE.txt')


def _sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()


def _valid(path, digest):
    return Path(path).is_file() and _sha(path) == digest.lower()


def _fetch(path, url, digest):
    """Download to temporary sibling, verify before replacing cached data."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.locale-download-', delete=False) as f:
        temporary=Path(f.name)
        try:
            with urllib.request.urlopen(url, timeout=45) as response:
                total=0
                while True:
                    chunk=response.read(1024*1024)
                    if not chunk: break
                    total += len(chunk)
                    if total > 100*1024*1024: raise ValueError('Locale archive exceeds size limit')
                    f.write(chunk)
        except BaseException:
            f.close(); temporary.unlink(missing_ok=True); raise
    try:
        if not _valid(temporary,digest): raise ValueError('Locale archive SHA-256 mismatch: '+path.name)
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_if_changed(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or _sha(source) != _sha(target):
        shutil.copyfile(source,target)


def _extract_dlls(archive, directory):
    with zipfile.ZipFile(archive) as z:
        for name, digest in _DLLS.items():
            matches=[entry for entry in z.infolist() if entry.filename == name and not entry.is_dir()]
            if len(matches)!=1 or matches[0].file_size>1024*1024:
                raise ValueError('Release does not contain exactly one safe '+name)
            data=z.read(matches[0])
            if hashlib.sha256(data).hexdigest()!=digest: raise ValueError('DLL SHA-256 mismatch: '+name)
            (directory/name).write_bytes(data)


def _compile_helper(root, kind, source, output, target, platform, reference, source_hash, baseline_source, baseline_binary):
    """Skip only if the source and result are bound by a checked fingerprint."""
    stamp=output.with_suffix(output.suffix+'.build.json')
    try: previous=json.loads(stamp.read_text(encoding='utf-8'))
    except (OSError, ValueError): previous={}
    binary_hash=_sha(output) if output.is_file() else None
    if (source_hash==baseline_source and binary_hash==baseline_binary) or (
            previous.get('source_sha256')==source_hash and previous.get('binary_sha256')==binary_hash and binary_hash):
        return False
    if os.name != 'nt': raise OSError('Locale helper compilation requires Windows .NET Framework csc.exe')
    framework=Path(os.environ.get('WINDIR',r'C:\Windows'))/'Microsoft.NET/Framework/v4.0.30319'
    csc=framework/'csc.exe'
    if not csc.is_file(): raise FileNotFoundError('Missing .NET Framework compiler: '+str(csc))
    with tempfile.TemporaryDirectory(prefix='.locale-compile-',dir=output.parent) as temp:
        staged=Path(temp)/output.name
        args=[str(csc),'/nologo','/target:'+target,'/platform:'+platform,'/optimize+',
              '/out:'+str(staged)] + ['/reference:'+str(framework/ref) for ref in reference.split(';')] + [str(source)]
        native_run(args,check=True,timeout=60,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if not staged.is_file(): raise RuntimeError('Locale helper compilation produced no executable')
        digest=_sha(staged)
        os.replace(staged,output)
    stamp.write_text(json.dumps({'source_sha256':source_hash,'binary_sha256':digest},indent=2),encoding='utf-8')
    return True


def ensure_runtime(root):
    """Ensure exactly the assets consumed by the portable build are present."""
    root=Path(root)
    assets=root/'app/locale_assets'; locale=root/'runtimes/locale'; probe=root/'runtimes/probe'
    locale.mkdir(parents=True,exist_ok=True); probe.mkdir(parents=True,exist_ok=True)
    downloads=[]; compiled=[]
    if not all(_valid(locale/name,digest) for name,digest in _DLLS.items()):
        archive=locale/'release.zip'
        if not _valid(archive,_RELEASE[1]):
            _fetch(archive,*_RELEASE); downloads.append(archive.name)
        _extract_dlls(archive,locale)
    for name,(url,digest) in _SOURCES.items():
        if not _valid(locale/name,digest):
            _fetch(locale/name,url,digest); downloads.append(name)
    for name in _NOTICES:
        _copy_if_changed(assets/name,locale/name)
    for kind,(src,name,target,platform,reference,base_src,base_exe) in _HELPERS.items():
        directory=probe if kind=='probe' else locale
        _copy_if_changed(assets/src,directory/src)
        source_hash=_sha(assets/src)
        if _compile_helper(root,kind,directory/src,directory/name,target,platform,reference,source_hash,base_src,base_exe): compiled.append(name)
    return {'downloaded':downloads,'compiled':compiled}
