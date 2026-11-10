"""Resolve legacy JSON addresses from saved snapshot structure, keeping cache IDs."""
from collections import defaultdict
import json
from pathlib import Path


PATH_FORMAT = 1


def _legacy_path(parts, unwrap, nested):
    path=''
    for part in parts:
        if nested and (part is unwrap or part=='!'):
            path+='!'
        elif isinstance(part,int):
            path+=f'[{part}]'
        else:
            path+=('.' if path and not (nested and path.endswith('!')) else '')+part
    return path


def _aliases(node, wanted, fmt):
    from gameloc import jsonnest
    result=defaultdict(set)

    def visit(value,parts,depth=0):
        if isinstance(value,str):
            safe=jsonnest.format_path(parts)
            aliases={safe,_legacy_path(parts,jsonnest.UNWRAP,True)}
            if fmt=='json' and not any(part is jsonnest.UNWRAP for part in parts):
                aliases.add(_legacy_path(parts,jsonnest.UNWRAP,False))
            for alias in aliases & wanted:result[alias].add(safe)
            # JSON-valued plugin arguments may be wrapped repeatedly as strings.
            stripped=value.lstrip()
            if depth<8 and stripped[:1] in ('{','['):
                try:inner=json.loads(value)
                except ValueError:return
                if isinstance(inner,(dict,list)):
                    visit(inner,parts+[jsonnest.UNWRAP],depth+1)
        elif isinstance(value,dict):
            for key,child in value.items():visit(child,parts+[key],depth)
        elif isinstance(value,list):
            for index,child in enumerate(value):visit(child,parts+[index],depth)

    visit(node,[])
    return result


def normalize_locations(upstream,snapshot):
    """Change only in-memory ptr/span; read each referenced snapshot file once."""
    if upstream.stats.get('rpt_path_format',0)>=PATH_FORMAT:return 0
    from gameloc.containers import _plugins_doc
    groups=defaultdict(list)
    for entry in upstream.entries:
        for location in entry.locations:
            if location.kind=='loose' and location.fmt in ('json','rmplugins'):
                groups[(location.file,location.fmt)].append(location)
    snapshot=Path(snapshot).resolve()
    texts={};updates=[]
    for (relative,fmt),locations in groups.items():
        if relative not in texts:
            target=(snapshot/relative).resolve()
            if not target.is_relative_to(snapshot) or Path(relative).is_absolute():
                raise ValueError('RPG Maker 작업 주소가 원문 작업 폴더 밖을 가리킵니다: '+relative)
            texts[relative]=target.read_text(encoding='utf-8-sig')
        node=_plugins_doc(texts[relative]) if fmt=='rmplugins' else json.loads(texts[relative])
        wanted={address for loc in locations for address in [loc.ptr,*(loc.span or [])]}
        aliases=_aliases(node,wanted,fmt)
        resolved={}
        for address in wanted:
            candidates=aliases.get(address,set())
            if len(candidates)!=1:
                reason='여러 위치에 대응합니다' if candidates else '위치를 찾을 수 없습니다'
                raise ValueError(f'RPG Maker 기존 JSON 주소가 {reason}: {relative}: {address!r}; 번역 캐시는 유지됩니다.')
            resolved[address]=next(iter(candidates))
        for loc in locations:
            updates.append((loc,resolved[loc.ptr],
                            [resolved[p] for p in loc.span] if loc.span is not None else None))
    changed=0
    for loc,ptr,span in updates:
        changed+=ptr!=loc.ptr or span!=loc.span
        loc.ptr=ptr;loc.span=span
    upstream.stats['rpt_path_format']=PATH_FORMAT
    return changed
