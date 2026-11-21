"""Project-scoped, read-only discovery of bitmap font layout capabilities."""
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from engine import save_json
from kirikiri_containers import open_source,XP3Archive
from kirikiri_font_layout import patch_message_layer,patch_ui_font,tft_hangul_coverage,FontLayoutUnsupported


def _text(data):
    if data.startswith((b'\xff\xfe',b'\xfe\xff')):return data.decode('utf-16')
    try:return data.decode('utf-8-sig')
    except UnicodeError:return data.decode('cp932')


def _direct_faces(sources,unsupported):
    result=set()
    for raw in sources:
        text=_text(raw)
        # Literal registration in a font object's with-block; never execute TJS.
        for match in re.finditer(r'\bwith\s*\(\s*[\w.]+\.font\s*\)\s*\{([^{}]*)\}',text,re.S):
            body=re.sub(r'//[^\r\n]*|/\*.*?\*/','',match[1],flags=re.S)
            faces=re.findall(r'\.face\s*=\s*"([^"\\]+)"\s*;',body)
            fonts=re.findall(r'\.mapPrerenderedFont\s*\(\s*"([^"\\]+\.tft)"\s*\)',body,re.I)
            if len(faces)==len(fonts)==1 and fonts[0].rsplit('/',1)[-1].casefold() in unsupported:
                result.add(faces[0])
    return sorted(result)


def _capture(project,cfg,prior=None):
    from kirikiri_project import _verified,_archive_order
    manifest=_verified(project);info=manifest.get('source',{})
    origin=info.get('container_path') or cfg.get('source')
    if not origin or not Path(origin).exists():return None
    selected={}
    def select(rel,archive,physical):
        base=rel.rsplit('/',1)[-1].casefold()
        if base in ('messagelayer.tjs','prerenderfontex.tjs','custom.tjs','textrender.tjs','utils.tjs') or base.endswith('.tft'):
            selected[rel]=(archive,physical)
    with open_source(origin) as source:
        archives,_=_archive_order(source,info.get('game_root',''))
        for path in archives:
            archive=XP3Archive(source,path)
            for rel in archive.entries:select(rel,archive,rel)
        prefix=info.get('game_root','');prefix=prefix+'/' if prefix else ''
        for path in source.entries:
            if path.startswith(prefix):select(path[len(prefix):],None,path)
        messages=[n for n in selected if n.rsplit('/',1)[-1].casefold()=='messagelayer.tjs']
        wrappers=[n for n in selected if n.rsplit('/',1)[-1].casefold()=='prerenderfontex.tjs']
        if len(messages)!=1 or len(wrappers)!=1:return None
        def read(rel):
            a,p=selected[rel]
            return a.read(p) if a else source.read(p)
        grouped=defaultdict(list)
        for rel in selected:
            if rel.lower().endswith('.tft'):grouped[rel.rsplit('/',1)[-1].casefold()].append(rel)
        unsupported=[];coverage={}
        for base,paths in grouped.items():
            if len(paths)!=1:continue
            try:count=tft_hangul_coverage(read(paths[0]))
            except FontLayoutUnsupported:continue
            coverage[paths[0]]=count
            if count==0:unsupported.append(base)
        directs=_direct_faces([read(n) for n in selected if n.rsplit('/',1)[-1].casefold()=='custom.tjs'],set(unsupported))
        if prior:
            for key,rel in [('message',messages[0]),('prerender',wrappers[0])]:
                old=prior['sources'][key]
                if old['path']!=rel or hashlib.sha256(read(rel)).hexdigest()!=old['sha256']:
                    raise ValueError('글꼴 표시 원본이 변경되었습니다.')
        folder=project/'data/kirikiri-font-layout';folder.mkdir(parents=True,exist_ok=True)
        files={}
        for key,rel in [('message',messages[0]),('prerender',wrappers[0])]:
            raw=read(rel);filename=key+'.tjs';(folder/filename).write_bytes(raw)
            files[key]={'path':rel,'file':filename,'sha256':hashlib.sha256(raw).hexdigest()}
        ui_font_issues=[]
        for key,base in [('textrender','textrender.tjs'),('kinsoku','utils.tjs')]:
            hits=[n for n in selected if n.rsplit('/',1)[-1].casefold()==base]
            if len(hits)==1:
                rel=hits[0];raw=read(rel);filename=key+'.tjs';(folder/filename).write_bytes(raw)
                files[key]={'path':rel,'file':filename,'sha256':hashlib.sha256(raw).hexdigest()}
            elif len(hits)>1: ui_font_issues.append({'renderer':key,'reason':'ambiguous_sources','count':len(hits)})
        direct_sources={}
        for index,rel in enumerate(sorted((n for n in selected if n.rsplit('/',1)[-1].casefold()=='custom.tjs'),key=str.casefold)):
            raw=read(rel);filename='direct-'+str(index)+'.tjs';(folder/filename).write_bytes(raw)
            direct_sources[rel]={'file':filename,'sha256':hashlib.sha256(raw).hexdigest()}
        profile={'version':1,'ui_fonts_checked':True,'direct_ui_checked':True,'direct_sources':direct_sources,'ui_font_issues':ui_font_issues,'sources':files,'unsupported_storages':sorted(unsupported),
                 'unsupported_direct_faces':directs,'hangul_coverage':coverage}
        save_json(project/'data/kirikiri-font-layout.json',profile)
        return profile


def font_layout_overrides(project,cfg=None):
    project=Path(project);cfg=cfg or {}
    if cfg.get('source_language','english')!='japanese':return {}
    record=project/'data/kirikiri-font-layout.json'
    profile=json.loads(record.read_text(encoding='utf-8-sig')) if record.is_file() else _capture(project,cfg)
    if not profile:return {}
    def checked_sources(current):
        if current.get('version')!=1:raise ValueError('글꼴 표시 설정의 형식을 확인할 수 없습니다.')
        folder=project/'data/kirikiri-font-layout';raw={}
        for key,item in current['sources'].items():
            path=folder/item['file']
            if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):raise ValueError('글꼴 표시 원본 경로가 올바르지 않습니다.')
            raw[key]=path.read_bytes()
            if hashlib.sha256(raw[key]).hexdigest()!=item['sha256']:raise ValueError('글꼴 표시 원본이 변경되었습니다.')
        return raw
    raw=checked_sources(profile)
    if not profile.get('ui_fonts_checked') or not profile.get('direct_ui_checked'):
        upgraded=_capture(project,cfg,prior=profile)
        if upgraded:
            profile=upgraded;raw=checked_sources(profile)
        else:
            from job_events import warning
            warning('kirikiri_ui_font_source_unavailable','백로그와 화면 글꼴 원본을 확인하지 못했습니다.')
    try:
        rendered=patch_message_layer(raw['message'],raw['prerender'],profile['unsupported_storages'],profile['unsupported_direct_faces'])
    except FontLayoutUnsupported:
        from job_events import warning
        warning('kirikiri_font_layout_unknown','한글 글꼴 표시 호환성을 확인하지 못했습니다.')
        return {}
    if profile.get('ui_font_issues'):
        from job_events import warning
        warning('kirikiri_ui_font_sources_ambiguous','일부 화면 글꼴 경로를 구분하지 못했습니다.',count=len(profile['ui_font_issues']))
    result={profile['sources']['message']['path']:rendered}
    for key in ('textrender','kinsoku'):
        if key not in raw: continue
        try:
            result[profile['sources'][key]['path']]=patch_ui_font(raw[key],key,profile['unsupported_storages'],profile['unsupported_direct_faces'])
        except FontLayoutUnsupported:
            from job_events import warning
            warning('kirikiri_ui_font_layout_unknown','일부 화면 글꼴 표시 호환성을 확인하지 못했습니다.')
    return result


def direct_ui_profile(project,cfg):
    """Return validated direct UI originals and font evidence after layout capture."""
    project=Path(project)
    if cfg.get('source_language','english')!='japanese':return None,{}
    record=project/'data/kirikiri-font-layout.json'
    if not record.is_file():return None,{}
    profile=json.loads(record.read_text(encoding='utf-8-sig'))
    if profile.get('version')!=1 or not profile.get('direct_ui_checked'):return None,{}
    folder=project/'data/kirikiri-font-layout';originals={}
    for rel,item in profile.get('direct_sources',{}).items():
        path=folder/item['file']
        if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):raise ValueError('글꼴 표시 원본 경로가 올바르지 않습니다.')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=item['sha256']:raise ValueError('글꼴 표시 원본이 변경되었습니다.')
        originals[rel]=raw
    return profile,originals


def direct_ui_overlay(raw,profile):
    from kirikiri_font_layout import patch_direct_ui_font
    if not profile:return raw,0
    return patch_direct_ui_font(raw,profile['unsupported_storages'],profile['unsupported_direct_faces'])
