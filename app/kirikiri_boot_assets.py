"""Prepare title-button boot evidence from approved, read-only game assets."""
import base64
import csv
import hashlib
import io
import json
import re
import subprocess
from pathlib import Path

from kirikiri_containers import open_source, XP3Archive
from kirikiri_ui_assets import approved_source_names, localize_buttons
from kirikiri_support import ui_bank

# Static script; image data is passed only as JSON on stdin.
_DECODE = r'''
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Drawing
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$data=[Console]::In.ReadToEnd() | ConvertFrom-Json
$result=@()
foreach($item in $data.images) {
 $bytes=[Convert]::FromBase64String($item)
 $stream=[IO.MemoryStream]::new($bytes)
 $bitmap=[Drawing.Bitmap]::new($stream)
 try {
  if($bitmap.Width -gt 1024 -or $bitmap.Height -gt 1024) { throw 'Image too large' }
  $pixels=[byte[]]::new($bitmap.Width*$bitmap.Height*4); $i=0
  for($y=0;$y -lt $bitmap.Height;$y++) { for($x=0;$x -lt $bitmap.Width;$x++) {
   $p=$bitmap.GetPixel($x,$y); $pixels[$i]=$p.R; $pixels[$i+1]=$p.G; $pixels[$i+2]=$p.B; $pixels[$i+3]=$p.A; $i+=4
  }}
  $result+=@{width=$bitmap.Width;height=$bitmap.Height;rgba=[Convert]::ToBase64String($pixels)}
 } finally { $bitmap.Dispose(); $stream.Dispose() }
}
@{images=$result} | ConvertTo-Json -Depth 4 -Compress
'''


def _decode_images(images):
    from native_process import run as native_run
    script=base64.b64encode(_DECODE.encode('utf-16le')).decode('ascii')
    payload=json.dumps({'images':[base64.b64encode(x).decode('ascii') for x in images]})
    result=native_run(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',script],
                          input=payload, text=True, encoding='utf-8', stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          timeout=30, check=True, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    parsed=json.loads(result.stdout)['images']
    if len(parsed)!=len(images): raise ValueError('Image decoder returned an incomplete result')
    out=[]
    for item in parsed:
        width,height=item['width'],item['height']; rgba=base64.b64decode(item['rgba'],validate=True)
        if not (0 < width <= 1024 and 0 < height <= 1024 and len(rgba)==width*height*4):
            raise ValueError('Invalid decoded image')
        out.append((width,height,rgba))
    return out


def _decode_text(data):
    if data.startswith((b'\xff\xfe',b'\xfe\xff')):
        try:return data.decode('utf-16')
        except UnicodeError:return ''
    for encoding in ('utf-8-sig','cp932'):
        try: return data.decode(encoding)
        except UnicodeError: pass
    return ''


def _canvas(data):
    text=re.sub(r'/\*.*?\*/', '', _decode_text(data), flags=re.S)
    # Literal assignments only. Ambiguous or computed values remain unknown.
    values=[]
    for key in ('scWidth','scHeight'):
        matches=re.findall(r'(?m)^\s*;?\s*(?:var\s+)?'+key+r'\s*=\s*(\d+)\s*;',text)
        if len(matches)!=1 or not 0<int(matches[0])<=16384: return None
        values.append(int(matches[0]))
    return values


def _positions(data, prefix):
    types={}; files=[]
    for row in csv.reader(io.StringIO(_decode_text(data))):
        try:
            if len(row)>=7 and row[0].strip()=='type' and row[2].strip()=='button':
                key=row[1].strip(); xy=tuple(int(v.strip()) for v in row[3:7])
                if key in types: types[key]=None
                else: types[key]=xy
            elif len(row)>=8 and row[0].strip()=='file' and row[7].strip()=='normal':
                files.append((row[1].strip(),row[2].strip(),tuple(int(v.strip()) for v in row[3:7])))
        except ValueError: continue
    result={}
    for key,name,(dx,dy,w,h) in files:
        if key not in types or types[key] is None or '/' in name or '\\' in name: continue
        x,y,_,_=types[key]; result[(prefix+'/'+name+'.png').casefold()]=(x+dx,y+dy,w,h)
    return result


def _pattern(name, pos, decoded):
    x,y,w,h=pos; width,height,rgba=decoded
    if (width,height)!=(w,h): return None
    core=[]; transparent=set()
    for yy in range(height):
        for xx in range(width):
            p=(yy*width+xx)*4; rgb=tuple(rgba[p:p+3]); alpha=rgba[p+3]
            if alpha>=254: core.append((xx,yy,*rgb))
            elif alpha==0: transparent.add((xx,yy))
    if len(core)<12 or len(transparent)<12: return None
    # Nearby unique transparent pixels make a locally comparable contrast pair.
    pairs=[]; used=set()
    for cx,cy,r,g,b in core:
        choices=[(abs(dx)+abs(dy),cx+dx,cy+dy) for dx in range(-10,11) for dy in range(-10,11)
                 if abs(dx)+abs(dy)<=10 and (cx+dx,cy+dy) in transparent and (cx+dx,cy+dy) not in used]
        if not choices: continue
        _,tx,ty=min(choices); used.add((tx,ty)); pairs.append(((cx,cy,r,g,b),(tx,ty)))
        if len(pairs)==64: break
    if len(pairs)<12: return None
    return {'name':name,'x':x,'y':y,'width':w,'height':h,
            'samples':[list(c) for c,_ in pairs],
            'contrasts':[[c[0],c[1],t[0],t[1]] for c,t in pairs]}


def prepare_menu_patterns(project, cfg=None, bank=None):
    """Return {'original': patterns, 'localized': patterns}; None means unknown.

    Reads only approved normal PNG members, corresponding title CSVs and Config.tjs.
    Does not execute game code or write project/source data.
    """
    from kirikiri_project import _verified, _archive_order
    project=Path(project); bank=Path(bank) if bank else ui_bank()
    names=[n for n in approved_source_names(bank,boot_probe_only=True) if n.casefold().endswith('%button;normal.png')]
    targets={n.casefold():n for n in names}
    csvnames={n.split('@',1)[0]+'.csv' for n in names if '@' in n}
    for name in csvnames: targets[name.casefold()]=name
    targets['config.tjs']='Config.tjs'
    manifest=_verified(project)
    if manifest.get('font',{}).get('path'):
        targets[manifest['font']['path'].casefold()]='Config.tjs'
    info=manifest.get('source',{}); origin=info.get('container_path') or (cfg or {}).get('source')
    if not origin or not Path(origin).exists(): return {'original':None,'localized':None}
    selected={}
    def capture(name, archive, physical, flat=False):
        key=name.casefold(); canonical=targets.get(key)
        if canonical is None and flat:
            candidates=[v for k,v in targets.items() if k.rsplit('/',1)[-1]==key]
            if len(candidates)==1: canonical=candidates[0]
        if canonical is not None: selected[canonical]=(archive,physical)
    with open_source(origin) as source:
        archives,_=_archive_order(source,info.get('game_root',''))
        for path in archives:
            archive=XP3Archive(source,path)
            flat=bool(re.fullmatch(r'patch(?:\d+)?\.xp3',path.rsplit('/',1)[-1],re.I))
            for name in archive.entries: capture(name,archive,name,flat)
        prefix=info.get('game_root',''); prefix=prefix+'/' if prefix else ''
        for name in source.entries:
            if name.startswith(prefix): capture(name[len(prefix):],None,name)
        blobs={name:(archive.read(physical) if archive else source.read(physical))
               for name,(archive,physical) in selected.items()}
    canvas=_canvas(blobs.get('Config.tjs',b''))
    if not canvas: return {'original':None,'localized':None}
    positions={}
    for csvname in csvnames:
        if csvname in blobs: positions.update(_positions(blobs[csvname],csvname.rsplit('/',1)[0]))
    originals={name:blobs[name] for name in names if name in blobs and name.casefold() in positions}
    localized,_=localize_buttons(originals,bank)
    originals={name:data for name,data in originals.items() if name in localized}
    # Rendering reuses a saved override snapshot when present; match those exact
    # bytes rather than assuming the current bank matches an earlier render.
    record=project/'data/kirikiri-ui-overrides.json'; folder=project/'data/kirikiri-ui-overrides'
    if record.is_file():
        try:
            saved=json.loads(record.read_text(encoding='utf-8-sig'))['files']; cached={}
            for name in originals:
                if name not in saved: continue
                entry=saved[name]; path=folder/entry['file']
                if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()): raise ValueError('Unsafe cached image')
                data=path.read_bytes()
                if hashlib.sha256(data).hexdigest()!=entry['sha256']: raise ValueError('Changed cached image')
                cached[name]=data
            localized={name:cached.get(name,data) for name,data in originals.items()}
        except (OSError, ValueError, KeyError, TypeError): localized={}
    if not originals: return {'original':None,'localized':None}
    entries=[(kind,name,data) for kind,source in (('original',originals),('localized',localized)) for name,data in source.items()]
    try: decoded=_decode_images([entry[2] for entry in entries])
    except (OSError, subprocess.SubprocessError, ValueError, KeyError): return {'original':None,'localized':None}
    groups={'original':[],'localized':[]}; used={'original':set(),'localized':set()}
    for (kind,name,_),pixels in zip(entries,decoded):
        p=_pattern(name,positions[name.casefold()],pixels)
        if p and 0<=p['x'] and 0<=p['y'] and p['x']+p['width']<=canvas[0] and p['y']+p['height']<=canvas[1]:
            points={(p['x']+s[0],p['y']+s[1]) for s in p['samples']}
            points.update((p['x']+s[2],p['y']+s[3]) for s in p['contrasts'])
            if not points & used[kind]:
                groups[kind].append(p); used[kind].update(points)
    return {kind:({'canvas':canvas,'buttons':buttons} if len(buttons)>=2 else None) for kind,buttons in groups.items()}
