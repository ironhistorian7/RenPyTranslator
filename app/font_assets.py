"""Download redistributable fonts into the tool, never the Windows font directory."""
import hashlib
import json
import urllib.request
from pathlib import Path
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1] / 'vendor/fonts'
BASE = 'https://hangeul.pstatic.net/hangeul_static/webfont/NanumSquareNeo/'
FILES = {
    'NanumSquareNeo-Regular.ttf': BASE+'NanumSquareNeoTTF-bRg.ttf',
    'NanumSquareNeo-Bold.ttf': BASE+'NanumSquareNeoTTF-cBd.ttf',
    'NanumPenScript-Regular.ttf': 'https://raw.githubusercontent.com/google/fonts/main/ofl/nanumpenscript/NanumPenScript-Regular.ttf',
    'NanumPen-OFL.txt': 'https://raw.githubusercontent.com/google/fonts/main/ofl/nanumpenscript/OFL.txt',
}

def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    records = {}
    for name, url in FILES.items():
        path = ROOT/name
        if not path.exists():
            data = urllib.request.urlopen(url, timeout=60).read()
            path.write_bytes(data)
        data = path.read_bytes()
        records[name] = dict(url=url, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
    # Naver's common license explicitly lists NanumSquareNeo; its binary has the
    # 2022 copyright but no name ID 13. Include that notice and the standard OFL.
    with TTFont(ROOT/'NanumSquareNeo-Regular.ttf') as font:
        table = font['name']
        license_text=(ROOT/'NanumPen-OFL.txt').read_text(encoding='utf-8')
        license_text=license_text[license_text.index('SIL OPEN FONT LICENSE Version 1.1'):]
        notice = table.getDebugName(0) or ''
        notice+='\nCopyright (c) 2010, NAVER Corporation. Reserved Font Name: NanumSquareNeo.\n'
        notice+='Official license: https://help.naver.com/service/30016/contents/18088?osType=PC&lang=ko\n'
        (ROOT/'NanumSquareNeo-OFL.txt').write_text(notice+'\n\n'+license_text, encoding='utf-8')
    (ROOT/'nanum-sources.json').write_text(json.dumps(records, indent=2), encoding='utf-8')
    print('Downloaded Nanum Neo Regular/Bold and Nanum Pen, with OFL notices.')

if __name__ == '__main__': main()
