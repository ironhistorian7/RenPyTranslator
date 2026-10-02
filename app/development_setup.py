"""Bootstrap source development and prepare pinned release assets; no game access."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time
import urllib.request
import zipfile

from runtime_assets import inside, load_lock

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def check_file(path, expected):
    if sha256(path) != expected:
        raise ValueError('SHA-256 mismatch: ' + str(path))


def download(asset, target):
    """Stream a pinned asset; keep partial downloads for a subsequent setup."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        check_file(target, asset['sha256'])
        return target
    partial = target.with_name(target.name + '.part')
    for attempt in range(3):
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {'User-Agent': 'RenPyTranslator-setup'}
        if offset:
            headers['Range'] = 'bytes=' + str(offset) + '-'
        try:
            request = urllib.request.Request(asset['url'], headers=headers)
            with urllib.request.urlopen(request, timeout=90) as response:
                resumed = offset and response.status == 206
                if resumed and not response.headers.get('Content-Range', '').startswith('bytes ' + str(offset) + '-'):
                    raise ValueError('Unexpected download range: ' + asset['url'])
                print('Downloading ' + target.name + (' (resuming)' if resumed else ''), flush=True)
                with partial.open('ab' if resumed else 'wb') as stream:
                    shutil.copyfileobj(response, stream, 1024 * 1024)
            check_file(partial, asset['sha256'])
            partial.replace(target)
            return target
        except ValueError:
            partial.unlink(missing_ok=True)
            raise
        except OSError:
            if attempt == 2:
                raise
            time.sleep(2)
    raise RuntimeError('Download did not complete: ' + target.name)


def verify_vendor(root=ROOT):
    inventory = json.loads((root / 'vendor/asset-lock.json').read_text(encoding='utf-8'))
    for name, expected in inventory['files'].items():
        check_file(inside(root, name), expected)
    license_info = load_lock(root)['full_model']['license']
    check_file(inside(root, license_info['path']), license_info['sha256'])
    print('Vendored fonts, unrpyc and licenses verified.', flush=True)


def ensure_runtime(name, root=ROOT, cache=None):
    asset = load_lock(root)[name]
    directory = inside(root, asset['directory'])
    receipt = directory / 'asset-verification.json'
    if receipt.is_file():
        state = json.loads(receipt.read_text(encoding='utf-8'))
        if state.get('archive_sha256') == asset['sha256'] and state.get('files'):
            for relative, expected in state['files'].items():
                check_file(inside(directory, relative), expected)
            if (directory / asset['executable']).is_file():
                print(name + ' ' + asset['version'] + ': verified installed files.', flush=True)
                return directory
    cache = Path(cache) if cache else root / 'runtimes/.downloads'
    archive = cache / (name + '-' + asset['version'] + '.zip')
    old_archive = directory / 'runtime.zip'
    if old_archive.is_file() and not archive.exists():
        check_file(old_archive, asset['sha256'])
        archive = old_archive
    else:
        download(asset, archive)
    expected_files = {}
    with zipfile.ZipFile(archive) as contents:
        for member in contents.infolist():
            if member.is_dir():
                continue
            if not member.filename.startswith(asset['prefix']):
                raise ValueError('Unexpected runtime ZIP prefix: ' + member.filename)
            relative = member.filename[len(asset['prefix']):]
            target = inside(directory, relative)
            # The verified archive is also the source of each installed-file hash.
            digest = hashlib.sha256()
            with contents.open(member) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
            expected = digest.hexdigest()
            if not target.is_file() or sha256(target) != expected:
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name + '.setup-part')
                with contents.open(member) as source, temporary.open('wb') as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                check_file(temporary, expected)
                temporary.replace(target)
            expected_files[relative] = expected
    receipt.write_text(json.dumps({'archive_sha256': asset['sha256'], 'files': expected_files}, indent=2), encoding='utf-8')
    if not (directory / asset['executable']).is_file():
        raise RuntimeError('Runtime executable was not installed: ' + name)
    print(name + ' ' + asset['version'] + ': archive and installed files verified.', flush=True)
    return directory


def prepare_full_model(root=ROOT):
    """Prepare the one approved HY release without starting Ollama or loading a GPU."""
    asset = load_lock(root)['full_model']
    blobs = root / 'models/ollama/blobs'
    large_blob = blobs / ('sha256-' + asset['sha256'])
    download(asset, large_blob)
    if large_blob.stat().st_size != asset['size']:
        raise ValueError('Unexpected FULL model size: ' + str(large_blob))
    for digest, content in asset['text_blobs'].items():
        path = blobs / digest.replace(':', '-')
        if not path.is_file():
            path.write_bytes(content.encode('utf-8'))
        check_file(path, digest.partition(':')[2])
    manifest = inside(root, asset['manifest_path'])
    if not manifest.exists():
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps(asset['manifest']), encoding='utf-8')
    print('FULL HY Q6_K verified; no model/server execution.', flush=True)
    return asset


def prepare_frontend(root=ROOT, cache=None):
    asset = load_lock(root)['node']
    node_dir = inside(root, asset['directory'])
    node = node_dir / asset['executable']
    desktop = root / 'desktop'
    cache = Path(cache) if cache else root / 'runtimes/.downloads'
    environment = dict(os.environ)
    environment['PATH'] = str(node_dir) + os.pathsep + environment.get('PATH', '')
    environment.update(npm_config_cache=str(cache / 'npm'), ELECTRON_CACHE=str(cache / 'electron'), PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD='1')
    # npm ci always uses the committed lock file and only the local Node process.
    subprocess.run([str(node), str(node_dir / 'node_modules/npm/bin/npm-cli.js'), 'ci', '--no-audit', '--no-fund'], cwd=desktop, env=environment, check=True)
    # Recent Electron packages expose an explicit installer rather than postinstall.
    # It verifies the archive using the checksums shipped in the locked npm package.
    environment['electron_config_cache'] = str(cache / 'electron')
    subprocess.run([str(node), str(desktop / 'node_modules/electron/install.js')], cwd=desktop, env=environment, check=True)
    if not (desktop / 'node_modules/electron/dist/electron.exe').is_file():
        raise RuntimeError('Local Electron was not installed.')
    subprocess.run([str(node), str(desktop / 'node_modules/typescript/bin/tsc'), '--noEmit'], cwd=desktop, env=environment, check=True)
    subprocess.run([str(node), str(desktop / 'build.mjs')], cwd=desktop, env=environment, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, help='Reuse verified download cache during isolated development checks')
    parser.add_argument('--assets-only', action='store_true', help='Verify/install runtimes and vendored files only')
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12) or struct.calcsize('P') != 8 or sys.prefix == sys.base_prefix:
        raise RuntimeError('Run setup.ps1 with a repository-local Python 3.12 x64 venv.')
    if not args.assets_only:
        subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', str(ROOT / 'requirements-build.txt')], cwd=ROOT, check=True)
    verify_vendor()
    for name in ('node', 'ollama'):
        ensure_runtime(name, cache=args.cache)
    if not args.assets_only:
        prepare_frontend(cache=args.cache)
    print('Ready. GUI: .\\gui.ps1 -SourceMode; default build: .\\build.ps1 (LIGHT). Models/settings/caches were not changed.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        raise SystemExit(1)
