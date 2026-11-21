"""KiriKiri text-only preparation and native XP3 patch export."""
import hashlib
import json
import os
import re
import shutil
import tempfile
import datetime
from collections import defaultdict
from pathlib import Path

from engine import save_json
from kirikiri_containers import open_source, discover_game_roots, XP3Archive, choose_patch_name, write_xp3, ContainerError
from kirikiri_text import extract_text, render_text
from kirikiri_psb import loads, dumps
from kirikiri_scn import extract as extract_scn, apply as apply_scn
from kirikiri_runtime import compose_reference
from kirikiri_tokens import readable
from kirikiri_names import is_alias_table, parse_alias_tables
from kirikiri_patch_paths import plan_patch_members, validate_member_map, inspect_patch_layout, PatchPathError
from kirikiri_source_paths import canonical_patch_member
from kirikiri_ui_assets import KiriUIAssetError
from kirikiri_support import ui_overrides,locale_output,ensure_locale_profile
from kirikiri_font_support import font_layout_overrides


class KiriError(ValueError):
    def __init__(self, message, code='kirikiri_format'):
        super().__init__(message)
        self.error_code = code


def inspect_source(path):
    with open_source(path) as source:
        roots = discover_game_roots(source)
        if not roots:
            return None
        if len(roots) != 1:
            raise ValueError('KiriKiri 게임 폴더가 여러 개입니다. 게임 폴더를 직접 선택하세요.')
        root = roots[0]
        name = Path(root).name if root else Path(source.path).stem if Path(source.path).is_file() else Path(source.path).name
        return {'engine': 'kirikiri', 'engine_label': 'KiriKiri', 'variant': 'KAG/KAGEX',
                'game_name': name, 'source_kind': 'iso' if Path(source.path).is_file() else 'directory',
                'game_root': root, 'container_path': str(source.path), 'supported': True}


def _archive_order(source, root):
    prefix = root + '/' if root else ''
    names = [p for p in source.entries if p.startswith(prefix) and '/' not in p[len(prefix):] and p.lower().endswith('.xp3')]
    standard, patches = [], {}
    for path in names:
        name = path[len(prefix):].lower()
        match = re.fullmatch(r'patch(\d*)\.xp3', name)
        if match:
            suffix = match.group(1)
            if suffix and (suffix.startswith('0') or int(suffix) < 2):
                continue
            number = int(suffix) if suffix else 1
            if number in patches: raise ContainerError('중복된 패치 아카이브 이름입니다.')
            patches[number] = path
        elif not name.startswith('patch'):
            standard.append(path)
    active = []
    num = 1
    while num in patches:
        active.append(patches[num]); num += 1
    return sorted(standard, key=str.casefold) + active, names


def _sources(source, root, include_name_tables=False, include_layout=False, include_ui=False):
    prefix = root + '/' if root else ''
    archives, names = _archive_order(source, root)
    selected = {}; configs = {}; name_tables = {}; loaders = {}
    def loader_file(name):
        return name.rsplit('/',1)[-1].casefold() in ('initialize.tjs','storages.tjs','startup.tjs')
    def capture(name, physical, archive, flat=False):
        from kirikiri_ui_text_support import is_ui_source
        relevant = name.lower().endswith(('.ks','.scn')) or name.rsplit('/',1)[-1].lower() == 'config.tjs' or is_alias_table(name) or loader_file(name) or (include_ui and is_ui_source(name))
        if not relevant: return
        known = [v[0] for group in (selected,configs,name_tables,loaders) for v in group.values()]
        rel = canonical_patch_member(name,known,flat=flat)
        item = (rel,physical,archive)
        if rel.lower().endswith(('.ks','.scn')) or (include_ui and is_ui_source(rel)): selected[rel.casefold()] = item
        if rel.rsplit('/',1)[-1].lower() == 'config.tjs': configs[rel.casefold()] = item
        if is_alias_table(rel): name_tables[rel.casefold()] = item
        if loader_file(rel): loaders[rel.casefold()] = item
    for path in archives:
        archive = XP3Archive(source, path)
        standard_patch = bool(re.fullmatch(r'patch(?:\d+)?\.xp3',path.rsplit('/',1)[-1],re.I))
        flat = standard_patch and inspect_patch_layout(_loader_blobs(source,loaders))['layout'] == 'flat'
        for name, entry in archive.entries.items():
            capture(name,name,archive,flat)
    for path in source.entries:
        if path.startswith(prefix): capture(path[len(prefix):],path,None)
    result = selected, names, configs
    if include_name_tables: result = (*result,name_tables)
    if include_layout: result = (*result,_loader_blobs(source,loaders))
    return result


def _loader_blobs(source, items):
    return {rel: archive.read(physical) if archive else source.read(physical)
            for rel,physical,archive in items.values()}


def _alias_snapshot(source, tables):
    return parse_alias_tables({rel: archive.read(path) if archive else source.read(path)
                               for rel, path, archive in tables.values()})


def _saved_name_aliases(project):
    path = Path(project)/'data/kirikiri-name-aliases.json'
    if not path.is_file(): return None
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if data.get('version') != 1 or not isinstance(data.get('aliases'), dict):
        raise KiriError('이름 별명 자료를 읽을 수 없습니다.', 'kirikiri_format')
    return data


def _uid(path, location, text):
    value = path + '\0' + json.dumps(location, ensure_ascii=False, separators=(',', ':')) + '\0' + text
    return hashlib.sha256(value.encode('utf-8')).hexdigest()[:20]


def _rows(raw, rel, name_aliases=None, include_defaults=True, *, japanese_ui=False):
    result = []
    if rel.lower().endswith('.tjs'):
        from kirikiri_menu_text import extract_ui_text
        for item in extract_ui_text(raw, rel, japanese_ui=japanese_ui):
            result.append(dict(item, kind='string', speaker='unknown', block=rel,
                               source_file=rel, source_line=1))
    elif rel.lower().endswith('.ks'):
        for item in extract_text(raw, rel):
            line = raw_line(raw, item['location']['start'])
            result.append(dict(item, kind='dialogue' if item['usage'] == 'dialogue' else 'string',
                               speaker='unknown', block=rel, source_file=rel, source_line=line))
    else:
        doc = loads(raw)
        if any(doc.resources):
            raise KiriError('리소스가 포함된 SCN은 텍스트 전용 스냅샷을 지원하지 않습니다: '+rel)
        root = doc.root
        if isinstance(root, dict) and set(root) == {'list','map'} and isinstance(root['list'], list) and isinstance(root['map'], dict):
            return []
        if not isinstance(root, dict) or not isinstance(root.get('scenes'), list):
            raise KiriError('지원하지 않는 SCN 루트 구조: '+rel)
        for scene in root['scenes']:
            if not isinstance(scene, dict) or not isinstance(scene.get('texts', []), list) or not isinstance(scene.get('selects', []), list):
                raise KiriError('지원하지 않는 SCN 장면 구조: '+rel)
            if any(not isinstance(row, list) or len(row) not in (6,9) or not isinstance(row[2], str) for row in scene.get('texts', [])):
                raise KiriError('지원하지 않는 SCN 대사 구조: '+rel)
            if any(not isinstance(row, dict) or not isinstance(row.get('text'), str) for row in scene.get('selects', [])):
                raise KiriError('지원하지 않는 SCN 선택지 구조: '+rel)
        for item in extract_scn(doc.root, include_defaults=include_defaults, name_aliases=name_aliases):
            loc = list(item['location']); usage = item['usage']
            row = {'source': item['text'], 'usage': 'speaker_label' if usage == 'speaker' else usage,
                           'kind': 'dialogue' if usage == 'dialogue' else 'string', 'file': rel,
                           'block': rel + ':' + str(loc[1]), 'speaker': item['speaker_display'] or 'unknown',
                           'source_file': rel, 'source_line': int(loc[3])+1 if len(loc)>3 and isinstance(loc[3], int) else 1,
                           'location': loc, 'dialect': 'kagex_psb',
                           'speaker_internal': item.get('speaker_internal')}
            if item.get('derived_speaker'):
                row.update(derived_speaker=True, speaker_default=item['text'])
            result.append(row)
    for row in result: row['id'] = _uid(rel, row['location'], row['source'])
    return result


def raw_line(raw, offset):
    from kirikiri_text import _decode
    return _decode(raw)[0][:offset].count('\n') + 1


def prepare(project, cfg):
    project = Path(project)
    (project/'data').mkdir(parents=True, exist_ok=True)
    snapshot = project/'data/kirikiri-source'
    if snapshot.exists() or (project/'data/catalog.json').exists() or (project/'data/kirikiri-context').exists():
        raise ValueError('기존 작업 자료가 있습니다. 원문이나 번역 기록을 덮어쓰지 않습니다.')
    info = inspect_source(cfg['source'])
    if info is None: raise ValueError('KiriKiri 게임을 찾지 못했습니다.')
    temp = Path(tempfile.mkdtemp(prefix='kirikiri-', dir=project/'data'))
    context_temp = Path(tempfile.mkdtemp(prefix='kirikiri-context-', dir=project/'data'))
    manifest = {}; rows = []; font = None
    try:
        with open_source(info['container_path']) as source:
            selected, archive_names, configs, name_tables, loader_blobs = _sources(source, info['game_root'], include_name_tables=True,include_layout=True,include_ui=True)
            name_data = _alias_snapshot(source, name_tables)
            patch_layout = inspect_patch_layout(loader_blobs)
            # New projects, like migrations, only expose display-only volume
            # candidates when the matching renderer can be identified safely.
            route_supported = False
            if cfg.get('source_language') in ('japanese','ja'):
                customs = [item for item in selected.values() if item[0].replace('\\','/').lower().endswith('main/custom.tjs')]
                if len(customs) == 1:
                    _, custom_path, custom_archive = customs[0]
                    custom_raw = custom_archive.read(custom_path) if custom_archive else source.read(custom_path)
                    from kirikiri_route_display import patch_route_display
                    try:
                        patch_route_display(custom_raw, {'__rpt_probe__':'__rpt_probe__'})
                        route_supported = True
                    except ValueError: pass
            prefix = info['game_root'] + '/' if info['game_root'] else ''
            context_files = {}
            for subfolder in ('context/', 'game/context/'):
                context_prefix = prefix + subfolder
                for path in source.entries:
                    if path.casefold().startswith(context_prefix.casefold()) and path.lower().endswith('.txt'):
                        rel = path[len(context_prefix):]
                        context_files[rel.casefold()] = (rel, path)
            for rel, path in context_files.values():
                target = context_temp.joinpath(*rel.split('/'))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read(path))
            if len(configs) == 1:
                name, path, archive = next(iter(configs.values()))
                try:
                    config = archive.read(path) if archive else source.read(path)
                    from kirikiri_runtime import patch_config_font
                    patch_config_font(config)
                    (temp/'__rpt_config_snapshot__.tjs').write_bytes(config)
                    font = {'path':name}
                except (ValueError, OSError, KeyError):
                    pass
            for rel, path, archive in sorted(selected.values(), key=lambda v:v[0].casefold()):
                raw = archive.read(path) if archive else source.read(path)
                entries = _rows(raw, rel, name_data['aliases'], japanese_ui=cfg.get('source_language') in ('japanese', 'ja'))
                if not route_supported: entries = [entry for entry in entries if not entry.get('ui_readonly_volume')]
                if not entries:
                    continue
                target = temp.joinpath(*rel.split('/'))
                target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
                manifest[rel] = True
                rows.extend(entries)
        if not rows: raise ValueError('지원되는 KiriKiri 시나리오 문구를 찾지 못했습니다.')
        if len({row['id'] for row in rows}) != len(rows): raise ValueError('시나리오 항목 ID가 중복되었습니다.')
        temp.rename(snapshot)
        context_target = project/'data/kirikiri-context'
        if context_target.exists(): raise KiriError('기존 문맥 자료가 있어 덮어쓰지 않습니다.')
        context_temp.rename(context_target)
        save_json(project/'data/kirikiri-manifest.json', {'files':manifest, 'archives':[p.rsplit('/',1)[-1] for p in archive_names], 'source':info, 'font':font})
        save_json(project/'data/catalog.json', rows)
        save_json(project/'data/kirikiri-name-aliases.json', name_data)
        save_json(project/'data/kirikiri-patch-layout.json',patch_layout)
        save_json(project/'data/catalog-format.json', {'engine':'kirikiri', 'version':1, 'default_names':1, 'ui_text':3})
        cfg.update(engine='kirikiri', engine_label=info['engine_label'], game_name=info['game_name'])
        save_json(project/'project.json', cfg)
    finally:
        if temp.exists(): shutil.rmtree(temp)
        if context_temp.exists(): shutil.rmtree(context_temp)
    print(f'KiriKiri scenario files: {len(manifest)}; entries: {len(rows)}', flush=True)


def _verified(project):
    manifest = json.loads((project/'data/kirikiri-manifest.json').read_text(encoding='utf-8-sig'))
    snapshot = project/'data/kirikiri-source'
    for rel in manifest['files']:
        path = snapshot.joinpath(*rel.split('/'))
        if not path.is_file():
            raise KiriError('작업 원문 파일이 없습니다: '+rel, 'kirikiri_read_failure')
    return manifest


def _font_overlay(project, manifest):
    from kirikiri_runtime import patch_config_font
    from job_events import warning
    font = manifest.get('font')
    if not font:
        warning('kirikiri_font_unverified','한글 글꼴 기본 설정 미적용', detail='확인된 글꼴 설정이 없어 원래 글꼴을 유지합니다.')
        return None
    raw = (project/'data/kirikiri-source/__rpt_config_snapshot__.tjs').read_bytes()
    return font['path'], patch_config_font(raw)


def ensure_name_catalog(project, cfg, *, dry_run=False):
    """Append missing display slots without replacing any existing row or cache."""
    project = Path(project)
    marker_path = project/'data/catalog-format.json'
    marker = json.loads(marker_path.read_text(encoding='utf-8-sig')) if marker_path.is_file() else {}
    if marker.get('default_names') == 1:
        return {'added':0, 'groups':0, 'already_current':True}
    manifest = _verified(project)
    name_data = _saved_name_aliases(project)
    if name_data is None:
        info = manifest.get('source', {})
        try:
            with open_source(info.get('container_path') or cfg['source']) as source:
                _, _, _, tables = _sources(source, info.get('game_root', ''), include_name_tables=True)
                name_data = _alias_snapshot(source, tables)
        except (OSError, ContainerError) as exc:
            name_data = dict(version=1, aliases={}, files=[],
                             issues=[{'code':'source_unavailable', 'error_type':type(exc).__name__}])
    catalog = project/'data/catalog.json'
    rows = json.loads(catalog.read_text(encoding='utf-8-sig'))
    ids = {r['id'] for r in rows}
    locations = {(r.get('file'), tuple(r['location'])) for r in rows
                 if r.get('dialect') == 'kagex_psb' and isinstance(r.get('location'), list)}
    added = []
    for rel in manifest['files']:
        if not rel.lower().endswith('.scn'): continue
        raw = (project/'data/kirikiri-source').joinpath(*rel.split('/')).read_bytes()
        for row in _rows(raw, rel, name_data['aliases'], japanese_ui=cfg.get('source_language') in ('japanese', 'ja')):
            if not row.get('derived_speaker'): continue
            slot = (rel, tuple(row['location']))
            if slot in locations: continue
            if row['id'] in ids: raise KiriError('이름 항목 ID가 중복되었습니다.', 'kirikiri_format')
            ids.add(row['id']); locations.add(slot); added.append(row)
    result = {'added':len(added), 'groups':len({r['source'] for r in added}), 'already_current':False}
    if dry_run:
        return dict(result, rows=rows+added, name_aliases=name_data)
    # Keep a recoverable copy of catalog metadata; translations.jsonl is never written.
    if added:
        backup = project/'data/catalog.before-default-names.json'
        if not backup.exists(): shutil.copy2(catalog, backup)
        temp = catalog.with_name(catalog.name+'.names.tmp')
        try:
            save_json(temp, rows+added)
            os.replace(temp, catalog)
        finally:
            temp.unlink(missing_ok=True)
    save_json(project/'data/kirikiri-name-aliases.json', name_data)
    save_json(marker_path, dict(marker, engine='kirikiri', default_names=1))
    if name_data.get('issues'):
        from job_events import warning
        warning('kirikiri_name_aliases', '일부 이름 별명은 직접 확인이 필요합니다.',
                count=len(name_data['issues']))
    if added: print(f'KiriKiri names: {len(added)} missing display slots; {result["groups"]} name groups', flush=True)
    return result


def ensure_patch_layout(project, cfg=None, *, warn=True):
    project = Path(project)
    path = project/'data/kirikiri-patch-layout.json'
    if path.is_file():
        profile = json.loads(path.read_text(encoding='utf-8-sig'))
        if profile.get('version') != 1 or profile.get('layout') not in ('flat','hierarchical'):
            raise KiriError('패치 경로 설정을 읽을 수 없습니다.', 'kirikiri_format')
    else:
        manifest = _verified(project); info = manifest.get('source', {})
        blobs = {}
        try:
            origin = info.get('container_path') or (cfg or {}).get('source')
            if origin:
                with open_source(origin) as source:
                    _, _, _, blobs = _sources(source,info.get('game_root',''),include_layout=True)
        except (OSError,ContainerError):
            pass
        profile = inspect_patch_layout(blobs)
        save_json(path,profile)
    if warn and not profile.get('verified'):
        from job_events import warning
        warning('kirikiri_patch_layout_unverified','패치 검색 규칙 미확인',
                detail='표준 KAG 패치 경로를 사용합니다.')
    return profile


def _store_patch(project, patch, members, record):
    """Stage, verify and atomically replace a patch together with its ledger."""
    from kirikiri_containers import DirectorySource
    project,patch = Path(project),Path(patch)
    ledger_path = project/'data/kirikiri-output.json'
    previous = json.loads(ledger_path.read_text(encoding='utf-8-sig')) if ledger_path.is_file() else {}
    if patch.is_symlink(): raise KiriError('패치 경로가 올바르지 않습니다.','kirikiri_patch_conflict')
    if patch.exists():
        if (Path(previous.get('patch','')).resolve() != patch.resolve() or
                hashlib.sha256(patch.read_bytes()).hexdigest() != previous.get('sha256')):
            raise KiriError('기존 결과 패치가 변경되어 덮어쓸 수 없습니다.','kirikiri_patch_conflict')
    patch.parent.mkdir(parents=True,exist_ok=True)
    fd,temp_name = tempfile.mkstemp(prefix='.rpt-patch-',suffix='.tmp',dir=patch.parent)
    os.close(fd); os.unlink(temp_name)
    staged = Path(temp_name); staged_ledger = ledger_path.with_name(ledger_path.name+'.patch.tmp')
    backup = None; replaced = False
    try:
        write_xp3(staged,members)
        with DirectorySource(staged.parent) as source:
            packed = XP3Archive(source,staged.name)
            if set(packed.entries) != set(members): raise KiriError('패치 파일 목록이 일치하지 않습니다.')
            for name,content in members.items():
                if packed.read(name) != content: raise KiriError('패치 파일 내용이 일치하지 않습니다: '+name)
        record = dict(record,patch=str(patch),files=sorted(members),sha256=hashlib.sha256(staged.read_bytes()).hexdigest())
        save_json(staged_ledger,record)
        if patch.exists():
            if hashlib.sha256(patch.read_bytes()).hexdigest() != previous['sha256']:
                raise KiriError('기존 결과 패치가 변경되었습니다.','kirikiri_patch_conflict')
            backups = project/'data/backups'; backups.mkdir(parents=True,exist_ok=True)
            backup = Path(tempfile.mkdtemp(prefix='patch-layout-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-',dir=backups))
            shutil.copy2(patch,backup/patch.name)
            shutil.copy2(ledger_path,backup/ledger_path.name)
        os.replace(staged,patch); replaced = True
        os.replace(staged_ledger,ledger_path)
    except BaseException:
        if replaced:
            if backup:
                shutil.copy2(backup/patch.name,patch)
                shutil.copy2(backup/ledger_path.name,ledger_path)
            else: patch.unlink(missing_ok=True)
        raise
    finally:
        staged.unlink(missing_ok=True); staged_ledger.unlink(missing_ok=True)
    return backup


def repack_output(project, cfg=None, *, refresh_support=False):
    """Repackage completed output bytes without reading or changing translations."""
    from kirikiri_containers import DirectorySource
    project = Path(project)
    ledger_path = project/'data/kirikiri-output.json'
    previous = json.loads(ledger_path.read_text(encoding='utf-8-sig'))
    if previous.get('status') == 'unchanged': return {'status':'unchanged','patch':None}
    patch = Path(previous['patch'])
    if patch.is_symlink() or hashlib.sha256(patch.read_bytes()).hexdigest() != previous['sha256']:
        raise KiriError('기존 결과 패치가 변경되었습니다.','kirikiri_patch_conflict')
    old_map = validate_member_map(previous['files'],previous.get('member_map'),previous.get('layout'))
    with DirectorySource(patch.parent) as source:
        archive = XP3Archive(source,patch.name)
        if set(archive.entries) != set(old_map): raise KiriError('패치 파일 목록이 일치하지 않습니다.')
        original = {old_map[name]:archive.read(name) for name in archive.entries}
    support_changed=False
    font_layout={}
    if refresh_support:
        cfg=cfg or json.loads((project/'project.json').read_text(encoding='utf-8-sig'))
        font=_font_overlay(project,_verified(project))
        overlays={} if font is None else {font[0]:font[1]}
        font_layout=font_layout_overrides(project,cfg)
        overlays.update(font_layout)
        support_changed=any(original.get(name)!=raw for name,raw in overlays.items())
        original.update(overlays)
    profile = ensure_patch_layout(project,cfg)
    members,mapping = plan_patch_members(original,profile['layout'])
    if mapping == old_map and previous.get('layout') == profile['layout'] and not support_changed:
        return {'status':'unchanged','patch':str(patch),'layout':profile['layout'],'files':len(members)}
    record = dict(previous,layout=profile['layout'],member_map=mapping,patch_layout=profile)
    if refresh_support:record['font_layout']={name:hashlib.sha256(raw).hexdigest() for name,raw in font_layout.items()}
    backup = _store_patch(project,patch,members,record)
    return {'status':'repacked','patch':str(patch),'backup':str(backup) if backup else None,
            'layout':profile['layout'],'files':len(members)}


def render(project, cfg, *, rows=None, known=None):
    from translation import read_catalog, cache
    from output_paths import destination
    project = Path(project)
    cfg = dict(cfg,show_reference=False)
    manifest = _verified(project)
    rows = read_catalog(project) if rows is None else rows
    known = cache(project, cfg) if known is None else known
    missing = [r['id'] for r in rows if r['id'] not in known]
    if missing: raise ValueError(f'아직 번역하지 않은 항목이 {len(missing)}개 있습니다. 기존 번역을 유지하고 이어서 번역하세요.')
    from kirikiri_ui_text_support import native_map_bytes, canonical_native_rows, write_native_map, restore_native_map, map_record
    native_rows = canonical_native_rows(rows, known)
    native_map = native_map_bytes(native_rows)
    from kirikiri_font_support import direct_ui_profile, direct_ui_overlay
    font_layout = font_layout_overrides(project,cfg)
    font_profile,direct_originals = direct_ui_profile(project,cfg)
    grouped = defaultdict(list)
    route_map = {}; chapter_values = defaultdict(set)
    for row in rows:
        if row.get('ui_readonly_volume'):
            value = known[row['id']]['text']
            if value != row['source']:
                if row['source'] in route_map and route_map[row['source']] != value:
                    raise ValueError('동일한 경로 표시에 서로 다른 번역이 있습니다: '+row['source'])
                route_map[row['source']] = value
            continue
        if (cfg.get('source_language') in ('japanese','ja') and row.get('dialect') == 'kirikiri_tjs_ui'
                and row['file'].replace('\\','/').lower().endswith('main/scnlist.tjs')):
            chapter_values[row['source']].add(known[row['id']]['text'])
        grouped[row['file']].append(row)
    chapter_map = {source:next(iter(values)) for source,values in chapter_values.items()
                   if len(values)==1 and next(iter(values)) != source}
    if route_map or chapter_map:
        route_file = next((name for name in manifest['files'] if name.replace('\\','/').lower().endswith('main/custom.tjs')), None)
        route_path = (project/'data/kirikiri-source').joinpath(*route_file.split('/')) if route_file else None
        if route_path and route_path.is_file():
            from kirikiri_route_display import patch_route_display, patch_chapter_fallback
            original_route = route_path.read_bytes()
            for name, mapping, probe in (('route',route_map,patch_route_display),('chapter',chapter_map,patch_chapter_fallback)):
                if mapping:
                    try: probe(original_route, {'__rpt_probe__':'__rpt_probe__'})
                    except ValueError:
                        mapping.clear()
                        from job_events import warning
                        warning('kirikiri_saved_display_unresolved', '저장 화면 표시 위치를 확인하지 못했습니다.', detail=name)
        else:
            route_map.clear(); chapter_map.clear()
        if route_map or chapter_map: grouped.setdefault(route_file, [])
        else: route_file = None
    else: route_file = None
    for rel in direct_originals: grouped.setdefault(rel,[])
    changed = {}; ks_edits = {}; ui_text_edits = {}; ui_direct_font = {}
    for rel, items in grouped.items():
        source_file=(project/'data/kirikiri-source').joinpath(*rel.split('/'))
        raw=source_file.read_bytes() if source_file.exists() else direct_originals[rel]
        if rel in direct_originals and raw!=direct_originals[rel]:raise KiriError('UI 글꼴 원본이 일치하지 않습니다: '+rel)
        if rel.lower().endswith('.tjs'):
            from kirikiri_menu_text import render_ui_text
            edits = [dict(row, translation=known[row['id']]['text']) for row in items
                     if known[row['id']]['text'] != row['source'] and not row.get('native_menu')]
            rendered = render_ui_text(raw, edits)
            if rel == route_file:
                from kirikiri_route_display import patch_route_display
                rendered = patch_route_display(rendered, route_map)
                from kirikiri_route_display import patch_chapter_fallback
                rendered = patch_chapter_fallback(rendered, chapter_map)
            if rel in direct_originals:
                rendered,unresolved=direct_ui_overlay(rendered,font_profile)
                if unresolved:
                    from job_events import warning
                    warning('kirikiri_direct_font_unresolved','일부 화면 글꼴 경로를 확인하지 못했습니다.',count=unresolved)
                base_ui = render_ui_text(raw, edits)
                if rel == route_file:
                    base_ui = patch_route_display(base_ui, route_map)
                    base_ui = patch_chapter_fallback(base_ui, chapter_map)
                if rendered!=base_ui:
                    ui_direct_font[rel]=dict(source_sha256=hashlib.sha256(raw).hexdigest(),unresolved=unresolved)
            if rendered != raw: ui_text_edits[rel] = edits
        elif rel.lower().endswith('.ks'):
            edits = []
            for row in items:
                text = known[row['id']]['text']
                if text == row['source']: continue
                edits.append(dict(row, translation=text, show_reference=row['usage'] == 'dialogue' and cfg.get('show_reference', True)))
            rendered = render_text(raw, edits)
            if edits: ks_edits[rel] = edits
        else:
            doc = loads(raw); replacements = {}; search_values = {}; default_speakers = {}
            for row in items:
                text = known[row['id']]['text']
                if text == row['source']: continue
                if row.get('derived_speaker'):
                    default_speakers[tuple(row['location'])] = row['speaker_internal']
                if row['usage'] == 'dialogue':
                    search = readable(text, {'_kirikiri_dialect':'kagex_psb'})
                    from kirikiri_translation import row_config
                    text = compose_reference(text, row['source'], row['dialect'], cfg.get('show_reference', True), cfg=row_config(cfg,row))
                    search_values[text] = search
                replacements[tuple(row['location'])] = text
            if replacements:
                apply_scn(doc.root, replacements, derive_search=lambda s: search_values[s],
                          default_speakers=default_speakers)
                rendered = dumps(doc)
            else: rendered = raw
        if rendered != raw: changed[rel] = rendered
    buttons = ui_overrides(project,cfg)
    if set(ui_text_edits) & set(buttons): raise KiriError('UI 텍스트와 이미지 경로가 충돌합니다.')
    changed.update(buttons)
    if set(ui_text_edits) & set(font_layout): raise KiriError('UI 텍스트와 글꼴 코드 경로가 충돌합니다.')
    changed.update(font_layout)
    if not changed and native_map is None:
        record = project/'data/kirikiri-output.json'
        if not record.exists(): save_json(record, {'status':'unchanged', 'files':[]})
        print('변경된 시나리오가 없어 기존 결과를 유지합니다.', flush=True)
        return None
    font_overlay = _font_overlay(project, manifest)
    if font_overlay:
        if font_overlay[0] in ui_text_edits: raise KiriError('UI 텍스트와 글꼴 설정 경로가 충돌합니다.')
        changed[font_overlay[0]] = font_overlay[1]
        from job_events import warning
        warning('kirikiri_font_override','한글 글꼴 기본값 적용', detail='맑은 고딕 기본값을 설정했습니다. 게임의 저장된 설정이나 후속 글꼴 지정이 이를 덮어쓸 수 있습니다.')
    profile = ensure_patch_layout(project,cfg)
    changed,member_map = plan_patch_members(changed,profile['layout'])
    # destination() currently uses source basename. Give it the game name for ISO paths,
    # while separately retaining the real source for path overlap checks.
    output_cfg = dict(cfg)
    if cfg.get('game_name'):
        output_cfg['source'] = str(Path(cfg['source']).parent / cfg['game_name'])
    output, _ = destination(project, output_cfg)
    real_source = Path(cfg['source']).resolve()
    resolved = output.resolve()
    if real_source == resolved or real_source.is_relative_to(resolved) or resolved.is_relative_to(real_source):
        raise ValueError('결과 폴더는 원본 게임과 분리해야 합니다.')
    output.mkdir(parents=True, exist_ok=True)
    record = project/'data/kirikiri-output.json'
    previous = json.loads(record.read_text(encoding='utf-8-sig')) if record.exists() else {}
    old_patch = Path(previous['patch']) if previous.get('patch') else None
    if old_patch and old_patch.parent.resolve() == output.resolve():
        patch = old_patch
    else:
        existing = manifest['archives'] + [p.name for p in output.iterdir() if p.is_file()]
        try:
            patch = output/choose_patch_name(existing)
        except (ContainerError, FileExistsError) as exc:
            raise KiriError(str(exc), 'kirikiri_patch_conflict') from exc
    owned = previous if old_patch and old_patch.parent.resolve() == output.resolve() else {}
    map_path, old_map = write_native_map(output, owned, native_map)
    try:
        locale_output(project,output,cfg,check_only=True)
        _store_patch(project,patch,changed,dict(ks_edits=ks_edits,ui_text_edits=ui_text_edits,ui_direct_font=ui_direct_font,ui_route_map=({route_file:route_map} if route_file and route_map else {}),ui_chapter_map=({route_file:chapter_map} if route_file and chapter_map else {}),layout=profile['layout'],member_map=member_map,patch_layout=profile,
                                            ui_native_map=map_record(native_map),
                                            ui_assets={rel:hashlib.sha256(content).hexdigest() for rel,content in buttons.items()},
                                            font_layout={rel:hashlib.sha256(content).hexdigest() for rel,content in font_layout.items()},font_layout_version=2))
    except BaseException:
        restore_native_map(map_path, old_map)
        raise
    save_json(output/'project-link.json', {'project':os.path.relpath(project,output), 'engine':'kirikiri'})
    locale = locale_output(project,output,cfg)
    instruction = (f'같은 버전 게임의 실행 파일(EXE)이 있는 폴더에 {patch.name} 파일을 복사하세요.\n')
    if locale['files']:
        instruction = '이 결과 폴더의 파일과 locale-runtime 폴더를 게임 EXE가 있는 폴더에 복사한 뒤 Play-Korean.exe를 실행하세요.\n'
    (output/'APPLY.txt').write_text(instruction+
        '원본 게임의 기존 patch 파일은 삭제하지 마세요. 적용을 취소하려면 이 도구가 만든 위 패치 파일만 제거하세요.\n', encoding='utf-8')
    print('Output folder: '+str(output), flush=True)
    return patch


def _check(project, cfg=None):
    from kirikiri_containers import DirectorySource
    project = Path(project); manifest=_verified(project)
    report = json.loads((project/'data/kirikiri-output.json').read_text(encoding='utf-8-sig'))
    if report.get('status') == 'unchanged': return {'status':'passed', 'patch':None, 'files':0, 'unchanged':True}
    path = Path(report['patch'])
    if hashlib.sha256(path.read_bytes()).hexdigest() != report['sha256']: raise ValueError('패치 파일이 변경되었습니다.')
    member_map = validate_member_map(report['files'],report.get('member_map'),report.get('layout'))
    native = report.get('ui_native_map')
    if native:
        if native.get('file') != 'ui-native-menus.json': raise KiriError('UI 메뉴 매핑 경로가 올바르지 않습니다.')
        map_path = path.parent/native['file']
        if map_path.is_symlink() or not map_path.is_file() or hashlib.sha256(map_path.read_bytes()).hexdigest() != native.get('sha256'):
            raise KiriError('UI 메뉴 매핑 파일이 변경되었거나 없습니다.')
        try:
            value = json.loads(map_path.read_text(encoding='utf-8-sig'))
            if value.get('version') != 1 or not isinstance(value.get('captions'),dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in value['captions'].items()):
                raise ValueError('invalid captions')
        except (ValueError, AttributeError): raise KiriError('UI 메뉴 매핑 파일 형식이 올바르지 않습니다.')
    if not set(report.get('ui_text_edits',{})).issubset(member_map.values()):
        raise KiriError('패치에 선언된 UI 텍스트가 누락되었습니다.')
    if not set(report.get('ui_assets',{})).issubset(member_map.values()):
        raise KiriError('패치에 선언된 버튼 이미지가 누락되었습니다.')
    if not set(report.get('font_layout',{})).issubset(member_map.values()):
        raise KiriError('패치에 선언된 글꼴 표시 코드가 누락되었습니다.')
    if not set(report.get('ui_direct_font',{})).issubset(report.get('ui_text_edits',{})):
        raise KiriError('UI 글꼴 코드가 누락되었습니다.')
    expected_layout={};font_profile=None;direct_originals={}
    if report.get('font_layout') or report.get('ui_direct_font'):
        check_cfg=cfg or json.loads((project/'project.json').read_text(encoding='utf-8-sig'))
        expected_layout=font_layout_overrides(project,check_cfg)
        declared=set(report.get('font_layout',{}))
        if not declared.issubset(expected_layout) or (report.get('font_layout_version')==2 and declared!=set(expected_layout)):
            raise KiriError('글꼴 표시 코드의 원본을 확인할 수 없습니다.')
        expected_layout={name:expected_layout[name] for name in declared}
        from kirikiri_font_support import direct_ui_profile
        font_profile,direct_originals=direct_ui_profile(project,check_cfg)
    profile = ensure_patch_layout(project,cfg,warn=False)
    if profile.get('verified') and profile['layout'] == 'flat' and any('/' in name for name in member_map):
        raise KiriError('게임이 검색하지 않는 패치 하위 경로가 있습니다. 표시 파일을 다시 생성하세요.','kirikiri_format')
    default_slots = defaultdict(list)
    for row in json.loads((project/'data/catalog.json').read_text(encoding='utf-8-sig')):
        if row.get('derived_speaker') and row.get('usage') == 'speaker_label':
            default_slots[row['file']].append(row)
    with DirectorySource(path.parent) as source:
        archive = XP3Archive(source, path.name)
        if sorted(archive.entries) != report['files']: raise ValueError('패치 파일 목록이 일치하지 않습니다.')
        for member in archive.entries:
            actual = archive.read(member)
            name = member_map[member]
            snapshot = project/'data/kirikiri-source'
            if name.lower().endswith('.ks'):
                original = snapshot.joinpath(*name.split('/')).read_bytes()
                if render_text(original, report['ks_edits'][name]) != actual:
                    raise KiriError('KAG 명령 또는 텍스트 구조 불일치: '+name)
            elif name.lower().endswith('.scn'):
                original = loads(snapshot.joinpath(*name.split('/')).read_bytes())
                modified = loads(actual)
                allowed = [tuple(item['location']) for item in extract_scn(original.root)]
                for row in default_slots[name]:
                    loc = tuple(row['location'])
                    if (len(loc) != 5 or loc[0] != 'scenes' or loc[2] != 'texts' or loc[4] != 1):
                        raise KiriError('기본 이름 표시 위치가 올바르지 않습니다: '+name)
                    before = original.root[loc[0]][loc[1]][loc[2]][loc[3]]
                    after = modified.root[loc[0]][loc[1]][loc[2]][loc[3]]
                    if before[0] != row['speaker_internal'] or before[1] is not None:
                        raise KiriError('기본 이름의 원본 ID 또는 표시 위치가 달라졌습니다: '+name)
                    if after[1] is not None and not isinstance(after[1], str):
                        raise KiriError('기본 이름 표시값이 올바르지 않습니다: '+name)
                    allowed.append(loc)
                for location in allowed:
                    before = original.root; after = modified.root
                    for part in location[:-1]: before = before[part]; after = after[part]
                    before[location[-1]] = after[location[-1]]
                    if len(location) >= 4 and location[2] == 'texts' and location[-1] == 2 and len(before) == 9:
                        if isinstance(before[7], str):before[7] = after[7]
                        if isinstance(before[8], str):before[8] = after[8]
                if original.root != modified.root or original.names != modified.names or original.resources != modified.resources:
                    raise KiriError('SCN 원본 구조 또는 제어 필드 불일치: '+name)
            elif name in report.get('ui_text_edits',{}):
                from kirikiri_menu_text import render_ui_text, extract_ui_text
                original_path=snapshot.joinpath(*name.split('/'))
                original=original_path.read_bytes() if original_path.exists() else direct_originals[name]
                direct=report.get('ui_direct_font',{}).get(name)
                if direct and (name not in direct_originals or direct_originals[name]!=original or direct.get('source_sha256')!=hashlib.sha256(original).hexdigest()):
                    raise KiriError('UI 글꼴 원본이 일치하지 않습니다: '+name)
                allowed = {r['id']:r for r in _rows(original, name, japanese_ui=True)}
                edits = report['ui_text_edits'][name]
                for edit in edits:
                    base = allowed.get(edit.get('id'))
                    if base is None or any(edit.get(k) != base.get(k) for k in ('source','location','ui_suffix','dialect','usage','native_menu','ui_readonly_volume')):
                        raise KiriError('UI 텍스트 위치가 원본과 다릅니다: '+name)
                expected=render_ui_text(original, edits)
                route_map=report.get('ui_route_map',{}).get(name)
                if route_map:
                    from kirikiri_route_display import patch_route_display
                    expected=patch_route_display(expected,route_map)
                chapter_map=report.get('ui_chapter_map',{}).get(name)
                if chapter_map:
                    from kirikiri_route_display import patch_chapter_fallback
                    expected=patch_chapter_fallback(expected,chapter_map)
                if direct:
                    from kirikiri_font_support import direct_ui_overlay
                    expected,unresolved=direct_ui_overlay(expected,font_profile)
                    if unresolved!=direct.get('unresolved'):raise KiriError('UI 글꼴 미확인 수가 일치하지 않습니다: '+name)
                if expected != actual:
                    raise KiriError('UI 코드 또는 텍스트 구조 불일치: '+name)
            elif name in report.get('ui_assets',{}):
                if hashlib.sha256(actual).hexdigest()!=report['ui_assets'][name]:
                    raise KiriError('버튼 이미지 내용이 일치하지 않습니다: '+name)
            elif name in expected_layout:
                if actual!=expected_layout[name]:raise KiriError('글꼴 표시 코드가 일치하지 않습니다: '+name)
            elif name==manifest.get('font',{}).get('path'):
                if actual!=_font_overlay(project,manifest)[1]:raise KiriError('한글 글꼴 설정이 일치하지 않습니다: '+name)
    return {'status':'passed', 'patch':str(path), 'files':len(report['files'])}


def static_check(project, cfg=None):
    try:
        result = _check(project, cfg)
        return dict(result, failures=[])
    except (OSError, ValueError, KeyError) as exc:
        return {'status':'failed', 'failures':[str(exc)]}


def comparison(project, cfg):
    from datetime import datetime
    if not (project/'data/catalog.json').is_file() or not (project/'data/kirikiri-source').is_dir():
        raise ValueError('재번역할 KiriKiri 작업 자료가 없습니다.')
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    target = project.with_name(project.name+'-retranslate-'+stamp)
    (target/'data').mkdir(parents=True)
    for name in ('kirikiri-source', 'kirikiri-context', 'catalog.json', 'catalog-format.json', 'kirikiri-manifest.json', 'kirikiri-name-aliases.json', 'kirikiri-patch-layout.json', 'kirikiri-ui-overrides', 'kirikiri-ui-overrides.json', 'kirikiri-locale-profile.json', 'kirikiri-font-layout', 'kirikiri-font-layout.json', 'tool-options.json', 'name-review.json', 'name-proposals.json', 'reviewed-terms.json'):
        src = project/'data'/name; dst = target/'data'/name
        if src.is_dir(): shutil.copytree(src, dst)
        elif src.is_file(): shutil.copy2(src, dst)
    for name in ('replacements.json','names.json'):
        src = project/name
        if src.is_file(): shutil.copy2(src, target/name)
    newcfg = dict(cfg, comparison_label='재번역-'+stamp)
    newcfg.pop('imported_cache_fingerprint', None)
    save_json(target/'project.json', newcfg)
    save_json(target/'data/retranslation.json', {'status':'pending','model':cfg.get('model'),'created':stamp})
    return target


def main(args, parser, info):
    from automatic import resolve_project, settings
    from task_plan import plan
    from output_paths import apply_options
    from model_store import require_job_model
    from translate_game import project_lock
    from translation import read_catalog, cache, translate
    from contextlib import nullcontext, ExitStack
    from diagnostics import stage
    jobs = plan(args.tasks) if args.command == 'tasks' else [args.command]
    if args.command == 'repair':
        if not args.fix: parser.error('repair requires --fix')
        jobs = ['font','names','failed'] if args.fix == 'all' else [args.fix]
    unsupported = set(jobs) - {'run','retranslate','failed','names','display','check','prepare','status','catalog','verify-source','translate','sample'}
    if unsupported:
        raise ValueError('KiriKiri에서 아직 구현되지 않은 작업입니다: '+', '.join(sorted(unsupported)))
    stage('KiriKiri prepare')
    project, base = resolve_project(args.source, args.project, args.model, args.source_language, engine='kirikiri')
    if getattr(args,'runtime_id',None) is not None: base['runtime_id'] = args.runtime_id
    base.update(engine='kirikiri', engine_label=info.get('engine_label','KiriKiri'))
    if info.get('game_name'): base['game_name'] = info['game_name']
    save_json(project/'project.json', base)
    if set(jobs) & {'failed','names','display'} and not set(jobs) & {'run','retranslate','translate'}:
        if not (project/'data/catalog.json').is_file() or not (project/'data/kirikiri-source').is_dir() or not (project/'data/translations.jsonl').is_file():
            raise ValueError('기존 KiriKiri 작업 자료와 번역 기록이 필요합니다.')
    need_model = bool(set(jobs) & {'run','retranslate','translate','sample','failed'})
    if 'names' in jobs and not need_model:
        from name_review_control import pending_kirikiri
        need_model = bool(pending_kirikiri(project,base))
    if need_model:
        args.model = require_job_model(jobs,args.model or base.get('model'),runtime_id=base.get('runtime_id'))
        base['model'] = args.model
        save_json(project/'project.json',base)
    if need_model:
        from model_runtime import model_session
        session = model_session()
    else: session = nullcontext()
    with session, project_lock(project), ExitStack() as extra_session:
        if 'retranslate' in jobs:
            project = comparison(project, base)
            base = json.loads((project/'project.json').read_text(encoding='utf-8-sig'))
            jobs = ['run' if j == 'retranslate' else j for j in jobs]
        cfg = apply_options(project, settings(project,base), args.output, args.suffix, args.textbox_scale, False)
        if jobs == ['check']:
            stage('KiriKiri patch check',project,cfg)
            ensure_locale_profile(project,cfg)
            result = static_check(project, cfg)
            if result['status'] != 'passed': raise ValueError('패치 검사 실패: '+'; '.join(result['failures']))
            print('Output folder: '+str(Path(result['patch']).parent if result.get('patch') else project), flush=True)
            return
        if not (project/'data/catalog.json').is_file():
            if not set(jobs) & {'run','prepare'}: raise ValueError('먼저 번역을 진행하세요.')
            stage('KiriKiri prepare',project,cfg)
            prepare(project, cfg)
        if jobs == ['prepare']: return
        if jobs == ['status']:
            rows = read_catalog(project); known = cache(project,cfg)
            print(json.dumps({'engine':'kirikiri','total':len(rows),'cached':len(known)})); return
        if jobs == ['catalog']:
            print(project/'data/catalog.json'); return
        if jobs == ['verify-source']:
            _verified(project); print('Prepared KiriKiri source files exist'); return
        if set(jobs) & {'run','translate','failed'}:
            from kirikiri_ui_text_support import ensure_ui_catalog
            ensure_ui_catalog(project,cfg)
        if set(jobs) & {'run','translate','failed','names'}:
            stage('KiriKiri names',project,cfg)
            ensure_name_catalog(project,cfg)
            if 'names' in jobs and not need_model:
                from name_review_control import pending_kirikiri
                if pending_kirikiri(project,cfg):
                    cfg['model'] = require_job_model(jobs,args.model or cfg.get('model'),runtime_id=cfg.get('runtime_id'))
                    base['model'] = cfg['model']; save_json(project/'project.json',base)
                    from model_runtime import model_session
                    extra_session.enter_context(model_session())
                    need_model = True
        if need_model:
            from gpu_policy import configure
            cfg = configure(cfg)
        if set(jobs) & {'run','translate','failed','names'}:
            from name_review_control import prepare_kirikiri, run as review_names
            prepare_kirikiri(project,cfg)
            review_names(project,cfg,force='names' in jobs)
        if set(jobs) & {'run','translate','sample','failed'}:
            stage('KiriKiri translation',project,cfg)
            if set(jobs) & {'run','translate','sample'}:
                translate(project,cfg,'sample' in jobs)
            if 'failed' in jobs:
                from failed_repair import run as repair_failed
                repair_failed(project,cfg)
        if jobs == ['sample']: return
        rows = read_catalog(project); known = cache(project,cfg)
        from translation_review import run as review_translations
        if set(jobs) & {'run','translate','failed'}:
            review_translations(project,cfg,rows=rows,known=known)
            known = cache(project,cfg)
        stage('KiriKiri patch output',project,cfg)
        if 'check' in jobs:ensure_locale_profile(project,cfg)
        render(project,cfg,rows=rows,known=known)
        stage('KiriKiri patch check',project,cfg)
        result = static_check(project,cfg)
        if result['status'] != 'passed': raise ValueError('패치 검사 실패: '+'; '.join(result['failures']))
        if (project/'data/retranslation.json').exists():
            from retranslation import complete
            complete(project)


def _critical_errors(function):
    from functools import wraps
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except KiriError:
            raise
        except PatchPathError as exc:
            raise KiriError(str(exc), 'kirikiri_patch_conflict' if exc.error_code == 'kirikiri_patch_conflict' else 'kirikiri_format') from exc
        except KiriUIAssetError as exc:
            raise KiriError(str(exc),'kirikiri_format') from exc
        except (OSError, KeyError) as exc:
            raise KiriError(str(exc), 'kirikiri_read_failure') from exc
        except (ValueError, UnicodeError) as exc:
            raise KiriError(str(exc), 'kirikiri_format') from exc
    return wrapped

inspect_source = _critical_errors(inspect_source)
prepare = _critical_errors(prepare)
render = _critical_errors(render)
ensure_name_catalog = _critical_errors(ensure_name_catalog)
ensure_patch_layout = _critical_errors(ensure_patch_layout)
repack_output = _critical_errors(repack_output)
from kirikiri_ui_text_support import ensure_ui_catalog, refresh_ui_output
