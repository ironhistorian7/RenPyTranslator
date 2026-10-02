"""Inventory fonts and their script references; read RPA fonts without extracting games."""
import ast
import hashlib
import io
import re
import shutil
import zlib
from pathlib import PurePosixPath
from fontTools.ttLib import TTFont, TTCollection, TTLibError
from engine import ROOT, IndexUnpickler, save_json

ASSETS=('NanumSquareNeo-Regular.ttf','NanumSquareNeo-Bold.ttf','NanumPenScript-Regular.ttf',
        'NanumSquareNeo-OFL.txt','NanumPen-OFL.txt','NotoSansCJKkr-Regular.otf','OFL.txt')
EXTENSIONS={'.ttf','.otf','.ttc','.otc'}
KOREAN=((0x1100,0x11ff),(0x3130,0x318f),(0xa960,0xa97f),(0xac00,0xd7ff))
ICONS=('fontawesome','materialicons','materialsymbols','fontello','icomoon','icofont','emoji')
HAND=('permanent marker','permanentmarker','handwriting','handwritten','hand script','brush script','cursive','nanum pen')

def ranges(points):
    result=[]
    for n in sorted(set(points)):
        if result and result[-1][1]+1==n:result[-1][1]=n
        else:result.append([n,n])
    return result

def details(font):
    names=font['name']
    family=names.getDebugName(1) or ''
    full=names.getDebugName(4) or family
    description=names.getDebugName(10) or ''
    os2=font.get('OS/2'); panose=getattr(getattr(os2,'panose',None),'bFamilyType',0)
    family_class=(getattr(os2,'sFamilyClass',0)>>8)&255
    hay=(family+' '+full+' '+description).lower()
    if any(x in hay.replace(' ','') for x in ICONS) or panose==5:
        kind,reason='icon','symbol classification/name'
    elif panose==3 or family_class==10:
        kind,reason='hand','PANOSE handwriting / OS2 script'
    elif any(x in hay for x in HAND) or re.search(r'\b(?:script|handwriting|handwritten|calligraphy)\b',hay):
        kind,reason='hand','family/description handwriting match'
    else:kind,reason='regular','default regular (no reliable handwriting evidence)'
    cmap=font.getBestCmap() or {}
    return dict(family=family,name=full,kind=kind,reason=reason,
                hangul_syllables=sum(c in cmap for c in range(0xac00,0xd7a4))),cmap

def archive_fonts(path):
    with path.open('rb') as f:
        header=f.readline(80).split()
        if not header or header[0] not in (b'RPA-2.0',b'RPA-3.0'):return
        offset=int(header[1],16);key=int(header[2],16) if header[0]==b'RPA-3.0' else 0
        f.seek(offset)
        index=IndexUnpickler(io.BytesIO(zlib.decompress(f.read())),encoding='bytes').load()
        for raw,entries in index.items():
            name=raw.decode('utf-8') if isinstance(raw,bytes) else raw
            rel=PurePosixPath(name)
            if rel.suffix.lower() not in EXTENSIONS:continue
            if rel.is_absolute() or '..' in rel.parts:continue
            data=[]
            for entry in entries:
                start,length=entry[0]^key,entry[1]^key
                prefix=entry[2] if len(entry)>2 else b''
                prefix=prefix or b''
                if isinstance(prefix,str):prefix=prefix.encode('latin1')
                f.seek(start);data.append(prefix+f.read(length-len(prefix)))
            yield name,b''.join(data)

def inventory(project):
    from automatic import script_sources
    uses=[]
    for file,text in script_sources(project).items():
        for line_no,line in enumerate(text.splitlines(),1):
            if line.lstrip().startswith('#'):continue
            if re.search(r'\b\w*font\b|\{font=|\.(?:ttf|otf|ttc|otc)',line,re.I):
                uses.append(dict(file=file,line=line_no,code=line.strip()))
    game=project/'staging/game';files={}
    from workspace_files import external_archives
    for p in sorted([*game.glob('*.rpa'),*external_archives(project)],reverse=True):
        for name,data in archive_fonts(p):files[name]=(data,p.name)
    for p in game.rglob('*'):
        if p.suffix.lower() in EXTENSIONS and 'tl' not in p.relative_to(game).parts:
            files[p.relative_to(game).as_posix()]=(p.read_bytes(),'loose')
    common=project/'staging/renpy/common'
    for p in common.rglob('*') if common.exists() else []:
        if p.suffix.lower() in EXTENSIONS:files.setdefault(p.relative_to(common).as_posix(),(p.read_bytes(),'engine'))
    fonts={};problems=[]
    for name,(data,origin) in files.items():
        try:
            collection=TTCollection(io.BytesIO(data)) if data[:4]==b'ttcf' else None
            members=collection.fonts if collection else [TTFont(io.BytesIO(data))]
            for i,font in enumerate(members):
                info,_=details(font)
                info.update(origin=origin,sha256=hashlib.sha256(data).hexdigest())
                fonts[(str(i)+'@'+name) if collection else name]=info
                font.close()
        except Exception as exc:problems.append(dict(font=name,error=str(exc)))
    # Link literal references to actual files while keeping expressions for runtime resolution.
    for use in uses:
        refs=re.findall(r'["\']([^"\']+\.(?:ttf|otf|ttc|otc))["\']|\{font=([^}]+)\}',use['code'],re.I)
        names=[a or b for a,b in refs]
        use['resolved']=[n for n in names if n in fonts or 'fonts/'+n in fonts]
        use['unresolved']=[n for n in names if n not in use['resolved']]
        use['dynamic']=not names
    return dict(fonts=fonts,uses=uses,errors=problems)

def installed(project,language):
    """Cheap check of the prepared patch, not a whole-game or pixel inspection."""
    import json
    game=project/'staging/game'
    try:
        support=(game/'zz_rpt_korean.rpy').read_text(encoding='utf-8')
        if 'presentation_runtime' not in support and 'def _rpt_font_group(' not in support:return None
        if '\n    _rpt_language_fonts()\n' not in support:return None
        data=json.loads((game/'tl'/language/'_rpt_presentation.json').read_text(encoding='utf-8'))['fonts']
        if not all(k in data for k in ('fonts','coverage','preferred','fallback','bold')):return None
        if not all(data['coverage'].get(k) for k in ('regular','hand')):return None
        paths=list(data['preferred'].values())+[data['fallback'],data['bold']]
        for name in paths:
            path=(game/name).resolve()
            if not path.is_relative_to(game.resolve()) or not path.is_file():return None
            with TTFont(path,lazy=True) as font:
                cmap=font.getBestCmap() or {}
                if not all(ord(c) in cmap for c in '가나다'):return None
        return data
    except (OSError,ValueError,KeyError,TypeError,AttributeError,TTLibError):return None

def install(project,language):
    ready=installed(project,language)
    if ready is not None:
        print('Font check passed: keeping installed fonts; skipping inventory/copy.',flush=True)
        return ready
    target=project/'staging/game/tl'/language/'fonts';target.mkdir(parents=True,exist_ok=True)
    for name in ASSETS:
        source=ROOT/'vendor/fonts'/name
        if not source.exists():raise ValueError('Missing font asset; run app/font_assets.py: '+name)
        shutil.copy2(source,target/name)
    report=inventory(project)
    prefix='tl/'+language+'/fonts/'
    preferred={};coverage={}
    for kind,name in [('regular',ASSETS[0]),('hand',ASSETS[2])]:
        with TTFont(target/name) as font:
            info,cmap=details(font)
            coverage[kind]=ranges(c for c in cmap if any(a<=c<=b for a,b in KOREAN))
            preferred[kind]=prefix+name
            report.setdefault('replacement_fonts',{})[kind]=info
    for info in report['fonts'].values():
        info['replacement']=preferred.get(info['kind'],'keep original icon font')
    save_json(project/'output/font-report.json',report)
    return dict(fonts={k:v['kind'] for k,v in report['fonts'].items()},coverage=coverage,
                preferred=preferred,fallback=prefix+'NotoSansCJKkr-Regular.otf',
                bold=prefix+'NanumSquareNeo-Bold.ttf')
