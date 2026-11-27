"""Append-only KiriKiri menu catalog migration and output refresh."""
import hashlib
import json
import os
import shutil
from collections import defaultdict
from pathlib import Path, PurePosixPath

from engine import save_json
from kirikiri_ui_csv import is_gallery_list

_UI_NAMES = {'menus.tjs', 'mainwindow.tjs', 'exmenuintf.tjs', 'option.tjs', 'default.tjs'}


def is_ui_source(name):
    name = name.replace('\\', '/').casefold()
    return (is_gallery_list(name) or name.rsplit('/', 1)[-1] in _UI_NAMES or
            name.endswith(('sysscn/override.tjs', 'sysscn/movieqsel.tjs', 'sysscn/stayontop.tjs',
                           'sysscn/stopdeactive.tjs', 'main/custom.tjs', 'main/scnsel.tjs',
                           'main/scnlist.tjs', 'sysscn/browswindow.tjs', 'sysscn/speech.tjs',
                           'sysscn/speech_dlg.tjs', 'system/yesnodialog.tjs', 'sysscn/myyesnodialog.tjs')))


def _snapshot_path(root, rel):
    parts = PurePosixPath(rel).parts
    if not parts or any(p in ('', '.', '..') for p in parts) or PurePosixPath(rel).is_absolute() or '\\' in rel or ':' in rel:
        raise ValueError('Invalid UI source path')
    return root.joinpath(*parts)


def _atomic_json(path, value):
    temp = path.with_name(path.name + '.ui.tmp')
    try:
        save_json(temp, value)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def ensure_ui_catalog(project, cfg, *, dry_run=False):
    """Append discovered UI labels, preserving existing catalog rows and cache."""
    from kirikiri_project import _verified, _sources, _rows, KiriError
    from kirikiri_containers import open_source
    project = Path(project); data = project/'data'
    marker_path = data/'catalog-format.json'
    marker = json.loads(marker_path.read_text(encoding='utf-8-sig')) if marker_path.is_file() else {}
    from source_language import normalize
    japanese_ui = normalize(cfg.get('source_language')) == 'japanese'
    target_version = 6 if japanese_ui else 3
    if marker.get('ui_text', 0) >= target_version:
        return {'added': 0, 'already_current': True}
    manifest = _verified(project)
    catalog = data/'catalog.json'
    rows = json.loads(catalog.read_text(encoding='utf-8-sig'))
    ids = {row['id'] for row in rows}
    slots = {(r['file'], r['location']['start'], r['location']['end']): r
             for r in rows if r.get('usage') == 'ui_label' and is_ui_source(r['file']) and isinstance(r.get('location'), dict)}
    info = manifest.get('source', {})
    additions = []; snapshots = {}; updated = 0
    with open_source(info.get('container_path') or cfg['source']) as source:
        selected, _, _ = _sources(source, info.get('game_root', ''), include_ui=True)
        route_supported = False
        if japanese_ui:
            from kirikiri_route_display import patch_route_display
            customs = [item for item in selected.values() if item[0].replace('\\','/').lower().endswith('main/custom.tjs')]
            if len(customs) == 1:
                _, custom_path, custom_archive = customs[0]
                custom_raw = custom_archive.read(custom_path) if custom_archive else source.read(custom_path)
                try:
                    patch_route_display(custom_raw, {'__rpt_probe__':'__rpt_probe__'})
                    route_supported = True
                except ValueError: pass
        for rel, path, archive in sorted(selected.values(), key=lambda x:x[0].casefold()):
            if not is_ui_source(rel): continue
            raw = archive.read(path) if archive else source.read(path)
            entries = _rows(raw, rel, japanese_ui=japanese_ui) if japanese_ui else _rows(raw, rel)
            if not route_supported: entries = [r for r in entries if not r.get('ui_readonly_volume')]
            if not entries: continue
            target = _snapshot_path(data/'kirikiri-source', rel)
            if target.exists() and target.read_bytes() != raw:
                raise KiriError('UI 원문 스냅샷이 현재 원본과 다릅니다: '+rel)
            snapshots[rel] = raw
            for row in entries:
                slot = (rel, row['location']['start'], row['location']['end'])
                if slot in slots:
                    existing = slots[slot]
                    if existing['id'] != row['id'] or existing['source'] != row['source']:
                        raise KiriError('UI 원문 항목이 변경되었습니다: '+rel)
                    for key in ('native_menu', 'ui_suffix'):
                        if existing.get(key) != row.get(key) or key not in existing:
                            existing[key] = row[key]; updated += 1
                    continue
                if row['id'] in ids: raise KiriError('UI 항목 ID가 중복되었습니다: '+rel)
                additions.append(row); ids.add(row['id']); slots[slot] = row
    result = {'added':len(additions), 'updated':updated, 'files':len(snapshots), 'already_current':False}
    if dry_run: return dict(result, rows=rows+additions)
    # The marker is committed last. An interrupted run can safely resume using
    # identical snapshots and locations; translation records are never changed.
    for rel, raw in snapshots.items():
        target = _snapshot_path(data/'kirikiri-source', rel)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(target.name+'.ui.tmp')
            try:
                temp.write_bytes(raw); os.replace(temp, target)
            finally: temp.unlink(missing_ok=True)
    manifest['files'].update({rel: True for rel in snapshots})
    _atomic_json(data/'kirikiri-manifest.json', manifest)
    if additions or updated:
        backup = data/'catalog.before-ui-text.json'
        if not backup.exists(): shutil.copy2(catalog, backup)
        _atomic_json(catalog, rows+additions)
    _atomic_json(marker_path, dict(marker, engine='kirikiri', ui_text=target_version))
    return result


def refresh_ui_output(project, cfg):
    """Merge UI-only edits into an already verified patch, retaining other bytes."""
    from kirikiri_project import _verified, _store_patch, ensure_patch_layout, KiriError
    from kirikiri_containers import DirectorySource, XP3Archive
    from kirikiri_patch_paths import validate_member_map, plan_patch_members
    from kirikiri_menu_text import render_ui_text
    from kirikiri_ui_csv import DIALECT, render_ui_csv
    from replacements import render_symbol_entries
    from translation import read_catalog, cache
    project = Path(project); _verified(project)
    ledger_path = project/'data/kirikiri-output.json'
    report = json.loads(ledger_path.read_text(encoding='utf-8-sig'))
    if not report.get('patch') or report.get('status') == 'unchanged':
        raise KiriError('기존 출력 패치가 필요합니다.')
    patch = Path(report['patch'])
    if patch.is_symlink() or hashlib.sha256(patch.read_bytes()).hexdigest() != report['sha256']:
        raise KiriError('기존 결과 패치가 변경되었습니다.', 'kirikiri_patch_conflict')
    mapping = validate_member_map(report['files'], report.get('member_map'), report.get('layout'))
    with DirectorySource(patch.parent) as source:
        archive = XP3Archive(source, patch.name)
        if set(archive.entries) != set(mapping): raise KiriError('패치 파일 목록이 일치하지 않습니다.')
        members = {mapping[name]:archive.read(name) for name in archive.entries}
    from kirikiri_font_support import font_layout_overrides, direct_ui_profile, direct_ui_overlay
    layouts=font_layout_overrides(project,cfg)
    font_profile,direct_originals=direct_ui_profile(project,cfg)
    ui_rows = [r for r in read_catalog(project) if r.get('dialect') in ('kirikiri_tjs_ui', DIALECT) and r.get('usage') == 'ui_label']
    known = cache(project, cfg)
    missing = [r['id'] for r in ui_rows if r['id'] not in known]
    if missing: raise KiriError('번역하지 않은 UI 항목이 있습니다: '+str(len(missing)))
    known = render_symbol_entries(ui_rows, known, cfg)
    route_maps = report.get('ui_route_map', {})
    chapter_maps = report.get('ui_chapter_map', {})
    route_text = defaultdict(set)
    chapter_text = defaultdict(set)
    for row in ui_rows:
        if row.get('ui_readonly_volume'):
            route_text[row['source']].add(known[row['id']]['text'])
        elif row['file'].casefold().endswith('main/scnlist.tjs'):
            chapter_text[row['source']].add(known[row['id']]['text'])
    if route_text and not route_maps:
        raise KiriError('표시 전용 경로의 기존 적용 위치를 확인할 수 없습니다.', 'kirikiri_ui_full_render_required')
    for maps, replacements in ((route_maps, route_text), (chapter_maps, chapter_text)):
        for labels in maps.values():
            for source in labels.keys() & replacements.keys():
                values = replacements[source]
                if len(values) == 1:
                    labels[source] = next(iter(values))
    grouped = defaultdict(list)
    for row in ui_rows: grouped[row['file']].append(row)
    for rel in direct_originals: grouped.setdefault(rel,[])
    for rel in route_maps.keys() | chapter_maps.keys(): grouped.setdefault(rel, [])
    edits_by_file = {};direct_by_file={}
    for rel, rows in grouped.items():
        if rel in report.get('ui_assets', {}) or rel in report.get('font_layout', {}):
            raise KiriError('UI 텍스트와 기존 지원 파일 경로가 충돌합니다: '+rel)
        if rel in members and rel not in report.get('ui_text_edits', {}):
            raise KiriError('기존 패치의 UI 파일을 덮어쓸 수 없습니다: '+rel)
        snapshot=_snapshot_path(project/'data/kirikiri-source', rel)
        raw=snapshot.read_bytes() if snapshot.exists() else direct_originals[rel]
        if rel in direct_originals and raw!=direct_originals[rel]:raise KiriError('UI 글꼴 원본이 일치하지 않습니다: '+rel)
        edits = [dict(row, translation=known[row['id']]['text']) for row in rows if known[row['id']]['text'] != row['source'] and not row.get('native_menu')]
        rendered = render_ui_csv(raw, rel, edits) if rel.lower().endswith('.csv') else render_ui_text(raw, edits)
        if rel in route_maps:
            from kirikiri_route_display import patch_route_display
            rendered = patch_route_display(rendered, route_maps[rel])
        if rel in chapter_maps:
            from kirikiri_route_display import patch_chapter_fallback
            rendered = patch_chapter_fallback(rendered, chapter_maps[rel])
        if rel in direct_originals:
            before=rendered;rendered,unresolved=direct_ui_overlay(rendered,font_profile)
            if unresolved:
                from job_events import warning
                warning('kirikiri_direct_font_unresolved','일부 화면 글꼴 경로를 확인하지 못했습니다.',count=unresolved)
            if rendered!=before:direct_by_file[rel]=dict(source_sha256=hashlib.sha256(raw).hexdigest(),unresolved=unresolved,speaker_fonts=True)
        if rendered != raw:
            members[rel] = rendered; edits_by_file[rel] = edits
        elif rel in report.get('ui_text_edits', {}): members.pop(rel, None)
    # Only project-owned UI assets may replace existing archive members.
    # Preserve all other existing members, including story data, byte for byte.
    from kirikiri_support import ui_overrides
    assets = ui_overrides(project, cfg)
    asset_hashes = dict(report.get('ui_assets', {}))
    for rel, raw in assets.items():
        if rel in grouped or rel in report.get('ui_text_edits', {}) or rel in report.get('font_layout', {}):
            raise KiriError('버튼 이미지와 UI 텍스트 또는 글꼴 코드 경로가 충돌합니다: '+rel)
        if rel in members:
            if rel not in asset_hashes:
                raise KiriError('기존 패치의 파일을 이미지로 덮어쓸 수 없습니다: '+rel)
            if hashlib.sha256(members[rel]).hexdigest() != asset_hashes[rel]:
                raise KiriError('기존 패치의 이미지 내용이 일치하지 않습니다: '+rel)
        members[rel] = raw
        asset_hashes[rel] = hashlib.sha256(raw).hexdigest()
    layout_hashes=dict(report.get('font_layout',{}))
    if font_profile:
        for rel,raw in layouts.items():
            if rel in grouped or rel in assets:raise KiriError('UI 텍스트와 글꼴 코드 경로가 충돌합니다: '+rel)
            if rel in members and (rel not in layout_hashes or hashlib.sha256(members[rel]).hexdigest()!=layout_hashes[rel]):
                raise KiriError('기존 글꼴 코드 내용이 일치하지 않습니다: '+rel)
            members[rel]=raw;layout_hashes[rel]=hashlib.sha256(raw).hexdigest()
    profile = ensure_patch_layout(project, cfg)
    packed, new_mapping = plan_patch_members(members, profile['layout'])
    native_rows = canonical_native_rows(ui_rows, known)
    map_bytes = native_map_bytes(native_rows)
    map_path, old_map = write_native_map(patch.parent, report, map_bytes)
    record = dict(report, ui_text_edits=edits_by_file, ui_direct_font=direct_by_file, font_layout=layout_hashes, ui_assets=asset_hashes, layout=profile['layout'], member_map=new_mapping, patch_layout=profile,
                  ui_route_map=route_maps, ui_chapter_map=chapter_maps,
                  ui_native_map=map_record(map_bytes))
    try:
        backup = _store_patch(project, patch, packed, record)
    except BaseException:
        restore_native_map(map_path, old_map)
        raise
    return {'status':'repacked', 'patch':str(patch), 'backup':str(backup) if backup else None, 'files':len(packed)}


def canonical_native_rows(rows, known):
    """Use a stable shared translation for each visible native menu label.

    User-selected translations take precedence; conflicting user choices cannot
    be represented safely by the native bridge and must be resolved explicitly.
    Inputs and the persisted cache are never modified.
    """
    groups = {}
    for row in rows:
        if not row.get('native_menu'): continue
        source = row['source']
        item = known[row['id']]
        groups.setdefault(source, []).append((row, item))
    output = []
    for source, group in groups.items():
        manual = [(r, item) for r, item in group
                  if item.get('status') == 'manual_translation' or item.get('model') == 'reviewed override']
        values = {item['text'] for _, item in manual}
        if len(values) > 1:
            raise ValueError('Conflicting manual native menu translations: '+repr(source)+' IDs: '+', '.join(r['id'] for r, _ in manual))
        target = (manual[0] if manual else group[0])[1]['text']
        output.extend(dict(row, translation=target) for row, _ in group)
    return output


def native_map_bytes(rows):
    """Build a deterministic map; duplicate source captions must agree."""
    from kirikiri_menu_text import native_caption_pairs
    by_source = defaultdict(list)
    for row in rows:
        if row.get('native_menu'):
            source = row['source']
            inline = row.get('ui_inline_accel')
            if inline:
                index = inline['index']
                source = source[:index] + '&' + source[index:]
            by_source[source + row.get('ui_suffix', '')].append(row)
    for source, group in by_source.items():
        values = {r.get('translation') for r in group}
        if len(values) > 1:
            raise ValueError('Conflicting native menu translations: '+repr(source)+' IDs: '+', '.join(r['id'] for r in group))
    captions = {pair['source']:pair['translation'] for pair in native_caption_pairs(rows)}
    return (json.dumps({'version':1, 'captions':captions}, ensure_ascii=False, sort_keys=True, indent=2)+'\n').encode('utf-8') if captions else None


def map_record(raw):
    return {'file':'ui-native-menus.json', 'sha256':hashlib.sha256(raw).hexdigest()} if raw is not None else None


def write_native_map(output, previous, raw):
    """Overwrite only an unchanged file recorded as owned by this project."""
    output = Path(output); path = output/'ui-native-menus.json'
    prior = previous.get('ui_native_map') or {}
    if path.is_symlink(): raise ValueError('UI 메뉴 매핑 경로가 올바르지 않습니다.')
    old = path.read_bytes() if path.exists() else None
    if old is not None and (prior.get('file') != path.name or hashlib.sha256(old).hexdigest() != prior.get('sha256')):
        raise ValueError('기존 UI 메뉴 매핑 파일이 변경되었거나 다른 작업에서 사용 중입니다.')
    if raw is not None:
        output.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name+'.tmp')
        try:
            temp.write_bytes(raw); os.replace(temp, path)
        finally: temp.unlink(missing_ok=True)
    elif old is not None:
        path.unlink()
    return path, old


def restore_native_map(path, old):
    if old is None: path.unlink(missing_ok=True)
    else:
        temp = path.with_name(path.name+'.tmp')
        try: temp.write_bytes(old); os.replace(temp,path)
        finally: temp.unlink(missing_ok=True)
