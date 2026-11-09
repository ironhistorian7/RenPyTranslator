"""Fetch standalone official SDKs into build only; never access game folders."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import urllib.request
import zipfile
import hashlib
import json

root = Path(__file__).resolve().parent / 'renpy-validation'
def fetch(version):
    name = 'renpy-' + version + '-sdk.zip'
    path = root / name
    url = 'https://www.renpy.org/dl/' + version + '/' + name
    if not path.exists():
        print('Downloading ' + version, flush=True)
        urllib.request.urlretrieve(url, str(path))
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            dest = (root / info.filename).resolve()
            assert dest.is_relative_to(root.resolve())
        z.extractall(root)
    print('SDK ready ' + version, flush=True)
    return dict(version=version, url=url, sha256=hashlib.sha256(path.read_bytes()).hexdigest())

if __name__ == '__main__':
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(fetch, ('7.7.3','8.2.3','8.3.7','8.4.1')))
    (root/'compat-downloads.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
