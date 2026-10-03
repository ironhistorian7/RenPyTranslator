"""Release packaging only: Deflate LIGHT and real multi-volume ZIP FULL."""
import hashlib
import json
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

GITHUB_ASSET_LIMIT = 2 ** 31
FULL_VOLUME_BYTES = 1_900_000_000
LIGHT_TARGET_BYTES = 2_000_000_000


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


def write_light(bundle, archive):
    files = sorted(p for p in Path(bundle).rglob('*') if p.is_file())
    if any(p.relative_to(bundle).parts[:2] == ('models', 'ollama') for p in files):
        raise ValueError('LIGHT bundle unexpectedly contains an Ollama model')
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
        bad = target.testzip()
        if bad:
            raise ValueError('LIGHT CRC failure: ' + bad)
    return [Path(archive)]


def write_full(bundle, archive, archiver, log, volume_bytes=FULL_VOLUME_BYTES):
    if not 0 < volume_bytes < GITHUB_ASSET_LIMIT:
        raise ValueError('Each volume must be smaller than 2 GiB')
    print('FULL split ZIP: Deflate-9, volume bytes ' + str(volume_bytes), flush=True)
    run_archiver([archiver, 'c', '-y', '-fmt:zip', '-l:9', '-r',
                  '-v:' + str(volume_bytes), archive, bundle], log)
    # Bandizip writes real ZIP disk/offset metadata, not renamed byte slices.
    archive = Path(archive)
    parts = sorted(archive.parent.glob(archive.stem + '.z[0-9]*')) + [archive]
    if not all(p.is_file() and 0 < p.stat().st_size <= volume_bytes for p in parts):
        raise ValueError('Missing or oversized FULL volume: ' + str(archive))
    if any(p.name != archive.stem + '.z%02d' % (i + 1) for i, p in enumerate(parts[:-1])):
        raise ValueError('FULL split ZIP has a missing volume')
    run_archiver([archiver, 't', '-y', archive], log)
    return parts


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
    archiver = prepare_archiver(root) if 'full' in editions else None
    # All new volumes must succeed before touching the previous distributions.
    with tempfile.TemporaryDirectory(prefix='.packaging-', dir=destination) as temporary:
        staging = Path(temporary)
        archives = []
        if 'light' in editions:
            archives.extend(write_light(bundle, staging / 'RenPyTranslator-light.zip'))
        if 'full' in editions:
            export_model(bundle)
            archives.extend(write_full(bundle, staging / 'RenPyTranslator-full.zip', archiver,
                                       inside(root, 'build/full-archive.log')))
        metadata = [{'name': p.name, 'bytes': p.stat().st_size, 'sha256': sha256(p)} for p in archives]
        manifest = {'version': version, 'editions': editions, 'compression': 'ZIP Deflate level 9',
                    'full_volume_bytes': FULL_VOLUME_BYTES, 'files': metadata,
                    'elapsed_seconds': round(time.monotonic() - started, 2),
                    'validation': ['LIGHT ZIP CRC' if 'light' in editions else '',
                                   'Bandizip FULL ZIP CRC' if 'full' in editions else ''],
                    'game_executed': False, 'model_inference_executed': False}
        (staging / 'release-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        (staging / 'SHA256SUMS.txt').write_text(''.join(
            row['sha256'] + '  ' + row['name'] + '\n' for row in metadata), encoding='utf-8')
        (staging / 'EXTRACT.txt').write_text(
            'LIGHT: RenPyTranslator-light.zip을 열어 폴더 전체를 압축 해제하세요.\n'
            'FULL: RenPyTranslator-full.zip과 모든 .z01, .z02 등의 파일을 같은 폴더에 받으세요.\n'
            '알집 또는 반디집으로 RenPyTranslator-full.zip을 열어 압축을 풀어주세요.\n'
            '압축을 푼 RenPyTranslator 폴더에서 RenPyTranslator.exe를 실행하세요.\n\n'
            'LIGHT: Extract the entire RenPyTranslator-light.zip archive.\n'
            'FULL: Download the .zip and ALL .z01, .z02, etc. volumes to the same folder.\n'
            'Open RenPyTranslator-full.zip with Bandizip or ALZip and extract.\n'
            'Run RenPyTranslator.exe inside the extracted RenPyTranslator folder.\n', encoding='utf-8-sig')
        destination = publish_archives(root, staging, version, editions)
    for row in metadata:
        print('PORTABLE_ASSET ' + str(destination / row['name']) + '; BYTES ' + str(row['bytes']), flush=True)
    return destination
