from pathlib import Path
import hashlib
import json
import urllib.request

root = Path(__file__).resolve().parents[1] / 'vendor' / 'fonts'
root.mkdir(parents=True, exist_ok=True)
base = 'https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/'
files = {'NotoSansCJKkr-Regular.otf': 'OTF/Korean/NotoSansCJKkr-Regular.otf', 'OFL.txt': 'LICENSE'}
records = {}
for name, path in files.items():
    data = urllib.request.urlopen(base + path, timeout=90).read()
    (root / name).write_bytes(data)
    records[name] = {'url': base + path, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes':len(data)}
    print(name, len(data), flush=True)
(root / 'sources.json').write_text(json.dumps(records, indent=2), encoding='utf-8')
