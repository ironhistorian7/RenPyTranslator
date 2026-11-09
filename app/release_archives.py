"""Release packaging only: Deflate LIGHT and real multi-volume ZIP FULL."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import time
import urllib.request
import uuid
import zipfile
import zlib
from datetime import datetime

from runtime_assets import inside, load_lock
from development_setup import ollama_assets

GITHUB_ASSET_LIMIT = 2 ** 31
FULL_VOLUME_BYTES = 1_900_000_000
LIGHT_TARGET_BYTES = 2_000_000_000
MANIFEST_NAME = 'bundle-manifest.json'
_PRIVATE_DIRS = frozenset(('.git', '.github', '.venv', '__pycache__', 'testcases',
                         'tests', 'fixtures', 'samples', 'examples', 'saves', 'logs'))
_GAME_SUFFIXES = frozenset(('.rpa', '.rpyc', '.rpym', '.rpymc', '.save', '.evtx', '.dmp', '.log', '.jsonl', '.sqlite', '.sqlite3',
                          '.rpgmvp', '.rpgmvo', '.rpgmvm', '.rgssad', '.rgss2a', '.rgss3a',
                          '.rxdata', '.rvdata', '.rvdata2', '.ldb', '.lmu', '.lmt'))
_MODEL_SUFFIXES = frozenset(('.gguf', '.safetensors', '.ckpt', '.onnx', '.h5'))
_ROOT_FILES = frozenset(('RenPyTranslator.exe', 'RenPyTranslator-cli.exe', 'translate.ps1',
                        'gui.ps1', 'README.md', 'LICENSE', 'runtime-lock.json',
                        'THIRD_PARTY_NOTICES.md', 'PORTABLE.txt', MANIFEST_NAME))
_RUNTIME_RPY = frozenset(('_internal/hints_screen.rpy', '_internal/language_panel.rpy'))
DOCUMENTATION_FILES = frozenset(('usage.md', 'models.md', 'cli.md', 'diagnostics.md',
                                'development.md', 'context-runtime.md', 'updates-and-checks.md',
                                'grok-context-preparation.txt', '01_default.png', '02_advanced.png',
                                '03_ai_model.png', '04_setting.png'))


def vendor_file_allowed(relative):
    """Runtime vendor subset, shared by collection and the final package guard."""
    parts = Path(relative).as_posix().split('/')
    if (len(parts) < 3 or parts[0] != 'vendor' or any(p in ('.', '..') for p in parts) or
            any(p.casefold() in _PRIVATE_DIRS for p in parts)):
        return False
    name = parts[-1]; suffix = Path(name).suffix.lower()
    if parts[1] == 'fonts':
        return len(parts) == 3 and suffix in ('.ttf', '.otf', '.ttc', '.otc', '.txt', '.json')
    if parts[1] == 'unrpyc':
        return ((len(parts) == 3 and name in ('unrpyc.py', 'deobfuscate.py', 'LICENSE', 'README.md')) or
                (len(parts) >= 4 and parts[2] == 'decompiler' and suffix == '.py' and not name.startswith('test_')))
    if parts[1] == 'gameloc':
        return ((len(parts) == 3 and name in ('LICENSE', 'THIRD-PARTY-NOTICES.txt', 'README.md', 'upstream.json')) or
                (len(parts) >= 4 and parts[2] == 'gameloc' and suffix == '.py' and not name.startswith('test_')))
    return False


def runtime_file_allowed(relative):
    parts = Path(relative).as_posix().split('/')
    if any(p.casefold() in _PRIVATE_DIRS for p in parts): return False
    name = parts[-1].casefold()
    return (Path(name).suffix in ('.exe', '.dll', '.bin', '.ptx', '.cubin', '.manifest') or
            name.startswith(('license', 'copying', 'notice')))


def _files(bundle):
    """Do not follow even an empty junction/symlink in the distribution."""
    bundle = Path(bundle)
    if bundle.is_symlink() or bundle.is_junction(): raise ValueError('Linked release bundle is forbidden')
    result = []
    for directory, dirs, files in os.walk(bundle, followlinks=False):
        for name in dirs + files:
            path = Path(directory)/name
            if path.is_symlink() or path.is_junction(): raise ValueError('Linked release asset is forbidden: '+str(path.relative_to(bundle)))
            if path.is_dir() and name.casefold() in _PRIVATE_DIRS:
                raise ValueError('Private/test release directory is forbidden: '+str(path.relative_to(bundle)))
        for name in files: result.append(Path(directory)/name)
    return sorted(result)


def _json(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else default


def _vendor_expected(root):
    inventory = _json(root/'vendor/asset-lock.json', {}).get('files', {})
    upstream = _json(root/'vendor/gameloc/upstream.json', {}).get('files', {})
    result = {name: digest for name, digest in inventory.items() if vendor_file_allowed(name)}
    result.update({'vendor/gameloc/'+name: digest for name, digest in upstream.items()
                   if vendor_file_allowed('vendor/gameloc/'+name)})
    return result


def _model_files(asset):
    result = {}
    manifest = asset.get('manifest', {})
    models = [entry for entry in manifest.get('layers', []) if entry.get('mediaType') == 'application/vnd.ollama.image.model']
    if (len(models) != 1 or models[0].get('digest') != 'sha256:'+asset.get('sha256', '') or
            models[0].get('size') != asset.get('size')):
        raise ValueError('FULL manifest does not identify the one locked HY model')
    for entry in [manifest.get('config', {})] + manifest.get('layers', []):
        digest = entry.get('digest', '')
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest) or not isinstance(entry.get('size'), int):
            raise ValueError('Invalid locked FULL model layer')
        result['models/ollama/blobs/'+digest.replace(':', '-')] = {'sha256': digest[7:], 'bytes': entry['size']}
    path = asset.get('manifest_path', '')
    if not path.startswith('models/ollama/manifests/') or '..' in Path(path).parts:
        raise ValueError('Invalid locked FULL model manifest path')
    result[path] = {'manifest': manifest}
    return result


def audit_bundle(bundle, edition, *, root=None, strict=False, write_manifest=True):
    """Reject private/game/model contamination before creating any ZIP volume."""
    if edition not in ('light', 'full'): raise ValueError('Unknown release edition')
    bundle = Path(bundle)
    if bundle.is_symlink() or bundle.is_junction(): raise ValueError('Linked release bundle is forbidden')
    bundle = bundle.resolve(); trusted = Path(root).resolve() if root is not None else bundle
    lock = _json(trusted/'runtime-lock.json', {})
    asset = lock.get('full_model')
    model_files = _model_files(asset) if edition == 'full' and asset else {}
    expected_vendor = _vendor_expected(trusted) if strict else {}
    runtimes = ollama_assets(lock)
    runtime_dirs = [str(asset['directory']).replace('\\', '/') for asset in runtimes]
    rows = []; seen = set()
    for path in _files(bundle):
        relative = path.relative_to(bundle).as_posix(); parts = relative.split('/'); lower = [p.casefold() for p in parts]
        suffix = path.suffix.lower(); name = lower[-1]
        if any(p in _PRIVATE_DIRS for p in lower): raise ValueError('Private/test release path is forbidden: '+relative)
        if lower[0] in ('data', 'project', 'projects', 'logs', 'config', 'settings', '.downloads'):
            raise ValueError('User/project release data is forbidden: '+relative)
        if (suffix in _GAME_SUFFIXES or suffix in _MODEL_SUFFIXES or
                (suffix == '.rpy' and relative not in _RUNTIME_RPY) or
                name in ('persistent', '.env', 'gui-settings.json', 'project.json', 'project-link.json',
                         'catalog.json', 'translations.jsonl', 'failed-items.json', 'translation-requests.jsonl') or
                re.fullmatch(r'(?:map\d+|actors|commonevents|mapinfos)\.json', name)):
            raise ValueError('Game/private release content is forbidden: '+relative)
        if parts[0] == 'models' and relative != 'models/README.txt' and relative not in model_files:
            raise ValueError(edition.upper()+' bundle unexpectedly contains an unapproved model asset: '+relative)
        if strict:
            if len(parts) == 1 and relative not in _ROOT_FILES: raise ValueError('Unlisted release root file: '+relative)
            if len(parts) > 1 and parts[0] not in ('_internal', '_desktop', 'vendor', 'runtimes', 'licenses', 'docs', 'models'):
                raise ValueError('Unlisted release directory: '+relative)
            if parts[0] == 'vendor' and relative not in ('vendor/asset-lock.json', 'vendor/versions.json', 'vendor/gameloc/upstream.json'):
                if not vendor_file_allowed(relative) or (relative not in expected_vendor and relative != 'vendor/unrpyc/README.md'):
                    raise ValueError('Unlisted vendor release asset: '+relative)
            if parts[0] == 'runtimes' and (not any(relative.startswith(directory+'/') for directory in runtime_dirs) or not runtime_file_allowed(relative)):
                raise ValueError('Unlisted runtime release asset: '+relative)
            if parts[0] == 'docs' and (len(parts) != 2 or parts[-1] not in DOCUMENTATION_FILES):
                raise ValueError('Unlisted documentation release asset: '+relative)
            if parts[0] == 'licenses' and (len(parts) != 2 or not
                    (parts[-1] == 'Hy-MT2-LICENSE.txt' or parts[-1].startswith(('fonttools-', 'pyinstaller-', 'packaging-', 'altgraph-', 'pefile-', 'pywin32-ctypes-', 'Python-')))):
                raise ValueError('Unlisted license release asset: '+relative)
        if relative == MANIFEST_NAME: continue
        row = {'path': relative, 'bytes': path.stat().st_size, 'sha256': sha256(path)}
        if relative in expected_vendor and row['sha256'] != expected_vendor[relative]:
            raise ValueError('Vendored release SHA-256 mismatch: '+relative)
        expected = model_files.get(relative)
        if expected:
            if 'manifest' in expected:
                if _json(path, None) != expected['manifest']: raise ValueError('FULL model manifest differs from the locked HY release')
            elif row['bytes'] != expected['bytes'] or row['sha256'] != expected['sha256']:
                raise ValueError('FULL model blob verification failed: '+relative)
        seen.add(relative); rows.append(row)
    if strict:
        required = {'RenPyTranslator.exe', 'RenPyTranslator-cli.exe', '_desktop/RenPyTranslator-UI.exe',
                    '_desktop/resources/app/main.js', '_desktop/resources/app/preload.js', '_desktop/resources/app/index.html'}
        for asset, directory in zip(runtimes, runtime_dirs):
            if asset.get('executable'): required.add(directory+'/'+asset['executable'])
        if required-seen or not any(name.startswith('_internal/') for name in seen):
            raise ValueError('Required portable runtime assets are missing: '+', '.join(sorted(required-seen)))
        if set(expected_vendor)-seen:
            raise ValueError('Required vendored runtime assets are missing: '+', '.join(sorted(set(expected_vendor)-seen)))
    if strict and edition == 'full' and (not asset or set(model_files)-seen):
        raise ValueError('FULL release is missing verified locked HY model assets')
    result = {'schema': 1, 'edition': edition, 'privacy_audit': 'passed', 'files': rows,
              'totals': {'files': len(rows), 'bytes': sum(row['bytes'] for row in rows)},
              'model_policy': 'locked_full_model' if edition == 'full' else 'no_models',
              'runtimes': [{'directory': directory, 'version': asset.get('version'), 'default': index == 0}
                           for index, (asset, directory) in enumerate(zip(runtimes, runtime_dirs))],
              'checks': ['no game scripts/saves/samples', 'no projects/settings/caches/logs',
                         'no linked assets', 'vendor/runtime allowlists' if strict else 'forbidden paths',
                         'locked HY model only' if edition == 'full' else 'no model payloads']}
    if write_manifest:
        (bundle/MANIFEST_NAME).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('RELEASE_PRIVACY_AUDIT '+edition.upper()+' passed; files '+str(len(rows)), flush=True)
    return result


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _download(asset, target):
    target = Path(target)
    if target.is_file() and sha256(target) == asset['sha256']:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.download')
    request = urllib.request.Request(asset['url'], headers={
        'User-Agent': 'Mozilla/5.0', 'Referer': asset.get('source', asset['url'])})
    print('Packaging tool download: ' + asset['url'], flush=True)
    with urllib.request.urlopen(request, timeout=60) as source, temporary.open('wb') as dest:
        shutil.copyfileobj(source, dest, 1024 * 1024)
    if sha256(temporary) != asset['sha256']:
        raise ValueError('Packaging tool SHA-256 mismatch: ' + str(temporary))
    temporary.replace(target)
    return target


def run_archiver(command, log, *, cwd=None):
    """Keep command, settings, output and exit code without opening a GUI."""
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('a', encoding='utf-8') as stream:
        stream.write('\nCOMMAND ' + json.dumps([str(v) for v in command]) + '\n')
        stream.flush()
        result = subprocess.run([str(v) for v in command], cwd=cwd, stdout=stream,
                                stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        stream.write('\nEXIT ' + str(result.returncode) + '\n')
    if result.returncode:
        raise RuntimeError('Archive tool exit %s; log: %s\n%s' % (
            result.returncode, log, log.read_text(encoding='utf-8', errors='replace')[-3000:]))


def prepare_archiver(root):
    """Unpack pinned official tools in build only; never install/register them."""
    lock = load_lock(root)['build_tools']
    asset = lock['bandizip']
    directory = inside(root, 'build/tools/bandizip-' + asset['version'])
    directory.mkdir(parents=True, exist_ok=True)
    expected = asset['files']
    executable = directory / 'bz.x64.exe'
    if all((directory / name).is_file() and sha256(directory / name) == digest
           for name, digest in expected.items()):
        return executable
    installer = _download(asset, directory / 'BANDIZIP-SETUP-STD-X64.EXE')
    sevenzr = _download(lock['sevenzr'], inside(root, 'build/tools/7zr.exe'))
    # Bandisoft's installer contains a signed executable plus a 7z payload and
    # a separate ZIP of installer scripts. Extract the payload, never run setup.
    binary = installer.read_bytes()
    offset = 0
    while True:
        offset = binary.find(b'7z\xbc\xaf\x27\x1c', offset)
        if offset < 0:
            raise ValueError('Official Bandizip installer: valid 7z payload not found')
        header = binary[offset:offset + 32]
        if len(header) == 32 and zlib.crc32(header[12:]) == struct.unpack_from('<I', header, 8)[0]:
            next_offset, next_size = struct.unpack_from('<QQ', header, 12)
            end = offset + 32 + next_offset + next_size
            if end <= len(binary):
                break
        offset += 1
    payload = directory / 'payload.7z'
    payload.write_bytes(binary[offset:end])
    run_archiver([sevenzr, 'x', payload, '-o' + str(directory), '-y'] + list(expected),
                 inside(root, 'build/archive-tools.log'))
    for name, digest in expected.items():
        if not (directory / name).is_file() or sha256(directory / name) != digest:
            raise ValueError('Extracted packaging tool SHA-256 mismatch: ' + name)
    return executable


def write_light(bundle, archive, *, audit=None):
    audit = audit or audit_bundle(bundle, 'light')
    if audit.get('edition') != 'light':raise ValueError('Release audit edition differs from LIGHT')
    files = _files(bundle)
    expected = {row['path']: row for row in audit['files']}
    if {p.relative_to(bundle).as_posix() for p in files} != set(expected) | {MANIFEST_NAME}:
        raise ValueError('Bundle changed after the release privacy audit')
    total = sum(p.stat().st_size for p in files)
    done = 0
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9, allowZip64=True) as target:
        for folder in ('project/', 'data/', 'models/'):
            target.writestr('RenPyTranslator/' + folder, b'')
        for path in files:
            relative = path.relative_to(bundle)
            # Large DLLs and executables must be compressed too.
            target.write(path, 'RenPyTranslator/' + relative.as_posix())
            done += path.stat().st_size
            if path.stat().st_size > 100_000_000:
                print('LIGHT Deflate-9 %.1f%%: %s' % (done * 100 / total, relative), flush=True)
    if Path(archive).stat().st_size >= GITHUB_ASSET_LIMIT:
        raise ValueError('LIGHT still exceeds the GitHub per-asset limit: ' + str(archive))
    if Path(archive).stat().st_size > LIGHT_TARGET_BYTES:
        print('LIGHT is below 2 GiB, but above the 2 GB target.', flush=True)
    with zipfile.ZipFile(archive) as target:
        names = {info.filename.removeprefix('RenPyTranslator/') for info in target.infolist() if not info.is_dir()}
        if names != set(expected) | {MANIFEST_NAME}: raise ValueError('LIGHT ZIP file inventory mismatch')
        for relative, row in expected.items():
            with target.open('RenPyTranslator/'+relative) as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if digest != row['sha256']: raise ValueError('LIGHT ZIP SHA-256 mismatch: '+relative)
        if json.loads(target.read('RenPyTranslator/'+MANIFEST_NAME)) != audit:
            raise ValueError('LIGHT ZIP bundle manifest mismatch')
    return [Path(archive)]


def write_split(bundle, archive, archiver, log, volume_bytes=FULL_VOLUME_BYTES, *, audit=None, edition='full'):
    audit = audit or audit_bundle(bundle, edition)
    if audit.get('edition') != edition:raise ValueError('Release audit edition differs from '+edition.upper())
    if {p.relative_to(bundle).as_posix() for p in _files(bundle)} != {row['path'] for row in audit['files']} | {MANIFEST_NAME}:
        raise ValueError('Bundle changed after the release privacy audit')
    if not 0 < volume_bytes < GITHUB_ASSET_LIMIT:
        raise ValueError('Each volume must be smaller than 2 GiB')
    print(edition.upper()+' split ZIP: Deflate-9, volume bytes ' + str(volume_bytes), flush=True)
    run_archiver([archiver, 'c', '-y', '-fmt:zip', '-l:9', '-r',
                  '-v:' + str(volume_bytes), archive, bundle], log)
    # Bandizip writes real ZIP disk/offset metadata, not renamed byte slices.
    archive = Path(archive)
    parts = sorted(archive.parent.glob(archive.stem + '.z[0-9]*')) + [archive]
    if not all(p.is_file() and 0 < p.stat().st_size <= volume_bytes for p in parts):
        raise ValueError('Missing or oversized '+edition.upper()+' volume: ' + str(archive))
    if any(p.name != archive.stem + '.z%02d' % (i + 1) for i, p in enumerate(parts[:-1])):
        raise ValueError(edition.upper()+' split ZIP has a missing volume')
    run_archiver([archiver, 't', '-y', archive], log)
    return parts


def write_full(bundle, archive, archiver, log, volume_bytes=FULL_VOLUME_BYTES, *, audit=None):
    return write_split(bundle, archive, archiver, log, volume_bytes, audit=audit, edition='full')


def light_archive_plan(audit, limit=LIGHT_TARGET_BYTES):
    """Conservative Deflate/header upper bound; never compress twice to decide."""
    total = audit['totals']['bytes']; count = audit['totals']['files'] + 1
    upper_bound = total + total//500 + count*512 + 65_536
    split = upper_bound >= min(limit, GITHUB_ASSET_LIMIT)
    return {'split': split, 'writer': 'Bandizip' if split else 'Python zipfile',
            'volume_bytes': FULL_VOLUME_BYTES if split else None,
            'uncompressed_upper_bound': upper_bound,
            'selection_basis': 'uncompressed bytes plus conservative Deflate/header overhead'}


def release_version(root):
    version = load_lock(root)['release']['version']
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Invalid release version in runtime-lock.json')
    return version


def _release_file(path):
    return bool(re.fullmatch(r'RenPyTranslator-(?:light|full)\.(?:zip|z\d+)', path.name)) or path.name in (
        'SHA256SUMS.txt', 'EXTRACT.txt', 'release-manifest.json')


def publish_archives(root, staging, version, editions):
    """Preserve current releases before replacing them, under checked tool paths."""
    root = Path(root).resolve()
    destination = inside(root, 'portable/V' + version)
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6]
    backup = inside(root, 'portable/backup/V' + version + '-' + stamp)
    old_files = []
    for path in destination.iterdir():
        if path.is_file() and _release_file(path):
            if path.name.split('.')[0].removeprefix('RenPyTranslator-') in editions or path.suffix == '.txt' or path.name == 'release-manifest.json':
                old_files.append((path, backup / 'release' / path.name))
    # Also move the obsolete outputs left by the previous dist-based builder.
    for edition in editions:
        path = inside(root, 'dist/RenPyTranslator-' + edition + '.zip')
        if path.is_file():
            old_files.append((path, backup / 'dist' / path.name))
    moved = []
    published = []
    try:
        for source, target in old_files:
            if not source.resolve().is_relative_to(root) or not target.resolve().is_relative_to(backup):
                raise ValueError('Backup path outside the tool root')
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
            moved.append((source, target))
        for source in Path(staging).iterdir():
            if source.is_file() and _release_file(source):
                target = destination / source.name
                source.rename(target)
                published.append(target)
    except BaseException:
        for target in reversed(published):
            target.rename(Path(staging) / target.name)
        for source, target in reversed(moved):
            target.rename(source)
        raise
    if moved:
        (backup / 'backup-manifest.json').write_text(json.dumps([
            {'original': str(source.relative_to(root)), 'backup': str(target.relative_to(backup))}
            for source, target in moved], indent=2), encoding='utf-8')
        print('PREVIOUS_RELEASE_BACKUP ' + str(backup), flush=True)
    return destination


def package_editions(root, bundle, editions, export_model):
    root = Path(root).resolve()
    version = release_version(root)
    destination = inside(root, 'portable/V' + version)
    destination.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    # Even FULL starts from a model-free freshly collected bundle. Fail before
    # downloading packaging tools or touching existing release files.
    initial_audit = audit_bundle(bundle, 'light', root=root, strict=True)
    light_plan = light_archive_plan(initial_audit)
    archiver = prepare_archiver(root) if 'full' in editions or ('light' in editions and light_plan['split']) else None
    # All new volumes must succeed before touching the previous distributions.
    with tempfile.TemporaryDirectory(prefix='.packaging-', dir=destination) as temporary:
        staging = Path(temporary)
        archives = []; audits = {}
        if 'light' in editions:
            audits['light'] = initial_audit
            if light_plan['split']:
                archives.extend(write_split(bundle, staging / 'RenPyTranslator-light.zip', archiver,
                                            inside(root, 'build/light-archive.log'), audit=initial_audit, edition='light'))
            else:
                archives.extend(write_light(bundle, staging / 'RenPyTranslator-light.zip', audit=initial_audit))
        if 'full' in editions:
            export_model(bundle)
            audits['full'] = audit_bundle(bundle, 'full', root=root, strict=True)
            archives.extend(write_full(bundle, staging / 'RenPyTranslator-full.zip', archiver,
                                       inside(root, 'build/full-archive.log'), audit=audits['full']))
        metadata = [{'name': p.name, 'bytes': p.stat().st_size, 'sha256': sha256(p)} for p in archives]
        manifest = {'version': version, 'editions': editions, 'compression': 'ZIP Deflate level 9',
                    'full_volume_bytes': FULL_VOLUME_BYTES, 'files': metadata,
                    'light_split': light_plan['split'] if 'light' in editions else None,
                    'packaging': dict({'light': light_plan} if 'light' in editions else {},
                                      **({'full': {'split': True, 'writer': 'Bandizip', 'volume_bytes': FULL_VOLUME_BYTES}} if 'full' in editions else {})),
                    'elapsed_seconds': round(time.monotonic() - started, 2),
                    'validation': [('Bandizip LIGHT ZIP CRC' if light_plan['split'] else 'LIGHT ZIP CRC + SHA-256') if 'light' in editions else '',
                                   'Bandizip FULL ZIP CRC' if 'full' in editions else ''],
                    'privacy_audits': {edition: {'status': audit['privacy_audit'], 'model_policy': audit['model_policy'],
                                                'files': audit['totals']['files']} for edition, audit in audits.items()},
                    'game_executed': False, 'model_inference_executed': False}
        (staging / 'release-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        (staging / 'SHA256SUMS.txt').write_text(''.join(
            row['sha256'] + '  ' + row['name'] + '\n' for row in metadata), encoding='utf-8')
        (staging / 'EXTRACT.txt').write_text(
            'LIGHT/FULL: 선택한 배포본의 .zip 파일을 받으세요.\n'
            '.z01, .z02 등의 분할 파일이 함께 있으면 모두 같은 폴더에 받으세요.\n'
            '반디집으로 .zip 파일을 열어 폴더 전체를 압축 해제하세요.\n'
            '압축을 푼 RenPyTranslator 폴더에서 RenPyTranslator.exe를 실행하세요.\n\n'
            'LIGHT/FULL: Download the selected edition\'s .zip file.\n'
            'If .z01, .z02, etc. volumes are provided, download ALL of them to the same folder.\n'
            'Open the .zip file with Bandizip and extract the entire folder.\n'
            'Run RenPyTranslator.exe inside the extracted RenPyTranslator folder.\n', encoding='utf-8-sig')
        destination = publish_archives(root, staging, version, editions)
    for row in metadata:
        print('PORTABLE_ASSET ' + str(destination / row['name']) + '; BYTES ' + str(row['bytes']), flush=True)
    return destination
