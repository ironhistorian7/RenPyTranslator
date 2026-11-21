"""Display-only routing labels; original scenario metadata stays unchanged."""
import re
from kirikiri_text import _decode
from kirikiri_menu_text import _escape


def patch_route_display(raw, mapping):
    if not mapping: return raw
    text,encoding,bom=_decode(raw)
    # Only an unambiguous getChapterInfo-backed getRouteText return is eligible.
    pattern=re.compile(r'(function\s+getRouteText\s*\(\s*tag\s*,\s*unknown\s*\)\s*\{(?:(?!\bfunction\b).)*?getChapterInfo\s*\(\s*tag\s*\)(?:(?!\bfunction\b).)*?)with\s*\(\s*info\s*\)\s*return\s*@(["\'])([^\r\n]*?\$\{\.volume\}[^\r\n]*?)\2\s*;',re.S)
    found=list(pattern.finditer(text))
    if len(found)!=1: raise ValueError('저장 화면 경로 표시 위치를 안전하게 확인할 수 없습니다.')
    m=found[0];quote=m.group(2);template=m.group(3)
    if template.count('${.volume}')!=1 or '__rpt_route_volume' in text: raise ValueError('경로 표시 변수가 안전하지 않습니다.')
    assignments=[]
    for source,target in sorted(mapping.items()):
        if not all(isinstance(x,str) and x for x in (source,target)): raise ValueError('경로 표시 이름이 올바르지 않습니다.')
        assignments.append(('else ' if assignments else '')+'if (__rpt_route_volume == "'+_escape(source,'"')+'") __rpt_route_volume = "'+_escape(target,'"')+'";')
    replacement=(m.group(1)+'with (info) { var __rpt_route_volume = .volume; '+
                 ' '.join(assignments)+' return @'+quote+template.replace('${.volume}','${__rpt_route_volume}')+quote+'; }')
    text=text[:m.start()]+replacement+text[m.end():]
    return bom+text.encode(encoding)


def patch_chapter_fallback(raw, mapping):
    """Only translate a saved title when no current chapter metadata exists."""
    if not mapping: return raw
    text,encoding,bom=_decode(raw)
    pattern=re.compile(r'(function\s+getChapterText\s*\(\s*tag\s*,\s*unknown\s*\)\s*\{\s*var\s+info\s*=\s*getChapterInfo\s*\(\s*tag\s*\)\s*;\s*)if\s*\(\s*info\s*===\s*void\s*\)\s*return\s+unknown\s*;')
    found=list(pattern.finditer(text))
    if len(found)!=1: raise ValueError('저장 제목 표시 위치를 안전하게 확인할 수 없습니다.')
    m=found[0];checks=[]
    for source,target in sorted(mapping.items()):
        if not all(isinstance(x,str) and x for x in (source,target)):raise ValueError('저장 제목 표시 이름이 올바르지 않습니다.')
        checks.append('if (unknown == "'+_escape(source,'"')+'") return "'+_escape(target,'"')+'";')
    replacement=m.group(1)+'if (info === void) { '+' '.join(checks)+' return unknown; }'
    text=text[:m.start()]+replacement+text[m.end():]
    return bom+text.encode(encoding)
