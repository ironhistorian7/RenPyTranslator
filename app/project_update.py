"""Explicit game-version updates, reusing exact prepared translations only.

The old project is read-only. New locations/control flow always come from a
fresh preparation; only validated translation values cross the version boundary.
"""
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from app_paths import ROOT
from engine import save_json

VERSION = 1
READ_VIEW_FILES = (
    'catalog.json', 'source-outline.json', 'ui-policy.json', 'name-hints.json',
    'translations.jsonl', 'recovered-translations.json', 'reviewed-translations.json',
    'name-transliterations.json', 'identity-types.json', 'name-edits.json',
    'semantic-edits.json',
)


def read(path, default):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else default


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _contract(row):
    return (row['source'], row.get('kind', 'string'),
            row.get('usage') or ('dialogue' if row.get('kind') == 'dialogue' else 'text'),
            row.get('speaker') or 'unknown')


def _scope(row, engine):
    filename = str(row.get('source_file') or row.get('file', '')).replace('\\', '/')
    block = str(row.get('block', ''))
    if engine == 'rpgmaker':
        # Command offsets/boundaries change after insertions; event/page/list and
        # indentation remain useful. Repeated branch text still needs anchors.
        parts = block.rsplit(':', 2)
        scope = ':'.join((parts[0], parts[2])) if len(parts) == 3 else block
    elif row.get('kind') == 'dialogue':
        scope = re.sub(r'_[0-9a-f]{8}(?:_\d+)?$', '', block)
    else:
        scope = 'strings:' + str(row.get('usage', 'text'))
    return filename, scope


def _anchors(keys, shared):
    before = []; previous = None
    for key in keys:
        before.append(previous)
        if key in shared: previous = key
    after = [None] * len(keys); following = None
    for i in range(len(keys) - 1, -1, -1):
        after[i] = following
        if keys[i] in shared: following = keys[i]
    return list(zip(before, after))


def reuse_translations(old_rows, new_rows, known, cfg):
    """Pure, deterministic matching. No fuzzy/model matching or location copying."""
    from translation import validate_entry
    from label_policy import expansion_errors
    from source_language import letters
    from translation import TOKENS
    engine = cfg.get('engine', 'renpy')
    old_groups = defaultdict(list); new_groups = defaultdict(list)
    for row in old_rows: old_groups[_scope(row, engine)].append(row)
    for row in new_rows: new_groups[_scope(row, engine)].append(row)
    old_scopes = defaultdict(list); old_sequences = defaultdict(list)
    for key, rows in old_groups.items():
        old_scopes[key[1]].append(key)
        old_sequences[tuple(_contract(r) for r in rows)].append(key)
    valid = {}; rejected = Counter()
    for row in old_rows:
        item = known.get(row['id'])
        if not item or item.get('source') != row['source'] or not isinstance(item.get('text'), str):
            continue
        if item.get('status') == 'source_fallback':
            rejected['previous_failure'] += 1; continue
        if (item['text'] == row['source'] and
                (row.get('kind') == 'dialogue' or row.get('usage') == 'choice') and
                letters(TOKENS.sub('', row['source']))):
            rejected['unchanged_story_text'] += 1; continue
        if validate_entry(row['source'], item, cfg) or expansion_errors(row, item['text']):
            rejected['invalid_previous_translation'] += 1; continue
        valid[row['id']] = item
    imported = []; mappings = []; counts = Counter()
    for group, rows in new_groups.items():
        old_group = group if group in old_groups else None
        if old_group is None:
            sequence = tuple(_contract(r) for r in rows)
            identical = old_sequences.get(sequence, [])
            if (len(identical) == 1 and
                    (identical[0][1] == group[1] or
                     (engine == 'rpgmaker' and len(set(sequence)) >= 2))):
                old_group = identical[0]
            elif engine != 'rpgmaker' and group[1] and not group[1].startswith('strings:'):
                # A named Ren'Py label survives a source-file rename. Generic
                # strings/RPG map scopes are not globally unique scene names.
                candidates = old_scopes.get(group[1], [])
                if len(candidates) == 1: old_group = candidates[0]
        if old_group is None:
            counts['new_or_changed'] += len(rows); continue
        previous = old_groups[old_group]
        old_keys = [_contract(r) for r in previous]; new_keys = [_contract(r) for r in rows]
        oc = Counter(old_keys); nc = Counter(new_keys)
        shared = {key for key in oc if oc[key] == nc[key] == 1}
        old_anchors = _anchors(old_keys, shared); new_anchors = _anchors(new_keys, shared)
        by_key = defaultdict(list); by_neighbors = defaultdict(list); by_anchors = defaultdict(list)
        exact = {}
        for i, row in enumerate(previous):
            exact[row['id']] = row
            if row['id'] not in valid: continue
            key = old_keys[i]
            by_key[key].append(row)
            neighbors = (old_keys[i-1] if i else None,
                         old_keys[i+1] if i+1 < len(old_keys) else None)
            by_neighbors[(key, neighbors)].append(row)
            by_anchors[(key, old_anchors[i])].append(row)
        # Collapse identical values once, so a large repeated passage is linear.
        def choice(candidates):
            values = {valid[r['id']]['text']: r for r in candidates}
            return next(iter(values.values())) if len(values) == 1 else None
        unique_values = {key: choice(items) for key, items in by_key.items()}
        neighbor_values = {key: choice(items) for key, items in by_neighbors.items()}
        anchor_values = {key: choice(items) for key, items in by_anchors.items()}
        for i, row in enumerate(rows):
            key = new_keys[i]; match = None; reason = 'exact_scope_text'
            same_id = exact.get(row['id'])
            # A pointer/ordinal can be recycled by an inserted repeated line.
            # Even unchanged IDs need neighbor evidence when values differ.
            if (same_id and _contract(same_id) == key and same_id['id'] in valid
                    and unique_values.get(key) is not None):
                match = same_id; reason = 'unchanged_id'
            elif key in by_key:
                match = unique_values[key]
                if match is None:
                    neighbors = (new_keys[i-1] if i else None,
                                 new_keys[i+1] if i+1 < len(new_keys) else None)
                    match = neighbor_values.get((key, neighbors))
                    reason = 'exact_neighbors'
                    if match is None and any(v is not None for v in new_anchors[i]):
                        match = anchor_values.get((key, new_anchors[i]))
                        reason = 'unchanged_anchors'
                if match is None:
                    counts['ambiguous'] += 1; continue
            elif key in oc:
                counts['unavailable_previous'] += 1; continue
            else:
                counts['new_or_changed'] += 1; continue
            item = valid[match['id']]
            if old_group != group: reason = 'relocated_scope_' + reason
            imported.append(dict(item, id=row['id'], source=row['source'],
                                 update_origin={'id': match['id'], 'reason': reason}))
            mappings.append({'old_id': match['id'], 'new_id': row['id'], 'reason': reason})
            counts['reused'] += 1
    old_sources = Counter(_contract(r) for r in old_rows)
    new_sources = Counter(_contract(r) for r in new_rows)
    report = dict(version=VERSION, old_entries=len(old_rows), new_entries=len(new_rows),
                  reused=counts['reused'], new_or_changed=counts['new_or_changed'],
                  ambiguous=counts['ambiguous'], unavailable_previous=counts['unavailable_previous'],
                  remaining=len(new_rows)-len(imported),
                  deleted_source_occurrences=sum((old_sources-new_sources).values()),
                  previous_translation_exclusions=dict(rejected), mappings=mappings)
    return imported, report


def _effective_cache(existing, target, cfg):
    """Existing cache readers may emit diagnostics. Give them a disposable view."""
    from automatic import settings
    from translation import cache, read_catalog
    from ui_policy import apply_policy
    from name_translation import apply_names
    with tempfile.TemporaryDirectory(prefix='.update-read-', dir=target/'data') as temporary:
        view = Path(temporary); (view/'data').mkdir()
        for name in READ_VIEW_FILES:
            path = existing/'data'/name
            if path.is_file(): shutil.copy2(path, view/'data'/name)
        for name in ('project.json', 'names.json'):
            path = existing/name
            if path.is_file(): shutil.copy2(path, view/name)
        rows = read_catalog(view)
        active = dict(settings(view, cfg), _reuse_previous_models=True)
        known = cache(view, active)
        if cfg.get('engine') != 'rpgmaker':
            known = apply_policy(view, rows, known, preserve_choices=True, cached_only=True)
            known = apply_names(view, rows, known)
        return rows, known


def _reuse_name_inputs(existing, target, cfg):
    """Reuse source-backed values, rebuilding every new input-location key."""
    counts = dict(identity_types=0, name_spellings=0, input_prompts=0, input_defaults=0)
    if cfg.get('engine') == 'rpgmaker': return counts
    from automatic import script_sources
    from name_hints import ensure_metadata, discover
    from name_translation import collect
    from translation import read_catalog, validate_text
    from identity_policy import signature, VERSION as identity_version
    from input_defaults import items, usable, validated
    rows = read_catalog(target); scripts = script_sources(target)
    metadata = ensure_metadata(target, rows, scripts)
    old_metadata = read(existing/'data/name-hints.json', None)
    if old_metadata is None:
        old_metadata = discover(script_sources(existing), read_catalog(existing))
    inventory = collect(scripts, rows, metadata)
    old_types = read(existing/'data/identity-types.json', {})
    types = {name: item for name, item in old_types.items() if name in inventory and
             item.get('version') == identity_version and item.get('signature') == signature(inventory[name]) and
             item.get('kind') in ('name', 'address', 'role', 'object', 'unknown')}
    if types: save_json(target/'data/identity-types.json', types)
    old_names = read(existing/'data/name-transliterations.json', {})
    names = {name: text for name, text in old_names.get('names', {}).items() if
             types.get(name, {}).get('kind') == 'name' and isinstance(text, str) and
             re.fullmatch(r'[가-힣][가-힣 ·\-]{0,63}', text)}
    if names:
        save_json(target/'data/name-transliterations.json',
                  {'names': names, 'attempted': sorted(names), 'sources': {n: inventory[n] for n in names}})
    old_items = items(old_metadata); new_items = items(metadata)
    old_prompts = read(existing/'data/name-input-prompts.json', {})
    prompt_contracts = {(item.get('prompt', ''), item.get('kind', 'text')) for item in old_items}
    prompts = {}
    for item in new_items:
        source = item.get('prompt', ''); text = old_prompts.get(source)
        if ((source, item.get('kind', 'text')) in prompt_contracts and isinstance(text, str) and
                re.search('[가-힣]', text) and not validate_text(source, text, cfg)):
            prompts[source] = text
    if prompts: save_json(target/'data/name-input-prompts.json', prompts)
    old_defaults = read(existing/'data/input-defaults.json', {})
    # File/line and their hashed keys are intentionally absent from this key.
    def contract(item):
        return tuple(item.get(k, '') for k in ('variable', 'default', 'kind', 'prompt'))
    values = defaultdict(set)
    for item in old_items:
        record = old_defaults.get(item['key'], {})
        if (item.get('kind') != 'answer' and record.get('source') == item.get('default') and
                record.get('kind') == item.get('kind') and record.get('variable') == item.get('variable') and
                usable(item, record.get('text'), old_prompts)):
            values[contract(item)].add(record['text'])
    defaults = {}
    for item in new_items:
        candidates = values.get(contract(item), set())
        if item.get('kind') == 'answer' or len(candidates) != 1: continue
        text = next(iter(candidates))
        if usable(item, text, prompts):
            defaults[item['key']] = dict(source=item['default'], text=text,
                                         kind=item['kind'], variable=item.get('variable'))
    defaults = validated(new_items, defaults, prompts)
    if defaults: save_json(target/'data/input-defaults.json', defaults)
    counts.update(identity_types=len(types), name_spellings=len(names),
                  input_prompts=len(prompts), input_defaults=len(defaults))
    return counts


def _source_signature(source, info):
    """Translation-input identities only; no images/audio or unrelated roots."""
    if info['engine'] == 'renpy':
        from workspace_files import plan, input_paths
        names = input_paths(plan(source))
    else:
        from gameloc_vendor import load
        load()
        from gameloc.engines import rpgmaker
        base = rpgmaker.is_rpgmaker(source)
        paths = set(rpgmaker.data_files(base)) | set(rpgmaker.custom_data_files(base))
        paths.update((base/'js').rglob('*.js'))
        if (base/'context').is_dir(): paths.update((base/'context').rglob('*.txt'))
        names = {path.relative_to(source).as_posix() for path in paths}
    from engine import sha
    result = []
    for name in sorted(names):
        path = source/name
        if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(source):
            raise ValueError('연결된 업데이트 원본은 사용할 수 없습니다.')
        # Content identity also distinguishes a repacked update that retained
        # both filename and timestamps. Media outside translation inputs is not
        # visited; RPAs are inputs because their script payload can change.
        result.append((name, path.stat().st_size, sha(path)))
    return _digest(result)


def _prepare_revision(project, cfg, engine):
    if engine == 'rpgmaker':
        from rpgmaker_project import prepare
        prepare(project, cfg)
    else:
        from automatic import inspection, add_literal_templates
        from engine import prepare, seal_stage
        from translation import catalog
        inspection(project, cfg)
        prepare(project, cfg)
        catalog(project, cfg)
        add_literal_templates(project, cfg)
        seal_stage(project)


def _register(project, source, state):
    from automatic import source_key
    key = source_key(source)
    roots = (ROOT/'data/projects', ROOT/'projects')
    if project.parent not in tuple(path.resolve() for path in roots):
        return
    for parent in roots:
        path = parent/'index.json'; index = read(path, {})
        if project.parent == parent.resolve():
            previous = index.get(key)
            if previous != project.name:
                state.setdefault('previous_registry_project', {})[str(path)] = previous
                index[key] = project.name; save_json(path, index)
        elif key in index:
            # resolve_project prefers legacy keys. Remove only this key after
            # retaining its old value; the new main registry owns this revision.
            state.setdefault('previous_registry_project', {})[str(path)] = index.pop(key)
            save_json(path, index)


def update_project(existing_project, cfg, new_source):
    """Return (new project, config, report); caller locks the existing project.

    This prepares and imports only. The ordinary run pipeline translates remaining
    rows and renders the new revision; it must call mark_complete after success.
    """
    from game_engine import detect
    from automatic import settings
    from translation import read_catalog, fingerprint
    existing = Path(existing_project).resolve(strict=True)
    source = Path(new_source)
    if source.is_symlink() or source.is_junction():
        raise ValueError('연결된 업데이트 원본은 사용할 수 없습니다.')
    source = source.resolve(strict=True)
    oldcfg = read(existing/'project.json', {})
    engine = oldcfg.get('engine', 'renpy')
    required = ('data/catalog.json', 'data/rpgmaker-project.json', 'data/rpg-source') if engine == 'rpgmaker' else ('data/catalog.json', 'data/templates', 'staging/game')
    if not oldcfg or not all((existing/name).exists() for name in required):
        raise ValueError('업데이트에 사용할 기존 번역 작업 자료를 찾지 못했습니다.')
    if source == existing or source.is_relative_to(existing) or existing.is_relative_to(source):
        raise ValueError('업데이트 원본과 작업 폴더는 서로 분리해 주세요.')
    info = detect(source)
    if not info.get('supported') or info['engine'] != engine:
        raise ValueError('기존 프로젝트와 같은 지원 엔진의 새 게임 폴더를 선택해 주세요.')
    print('Game update: comparing inputs',flush=True)
    signature = _source_signature(source, info)
    public = {k: v for k, v in dict(oldcfg, **cfg).items() if not k.startswith('_')}
    for name in ('imported_cache_fingerprint', 'comparison_label', 'update_base_project',
                 'expected_engine', 'expected_scripts', 'expected_dialogue'):
        public.pop(name, None)
    public.update(source=str(source), engine=engine)
    key = _digest([str(source).casefold(), signature, public.get('model'), public.get('source_language'), public.get('language')])
    owner = _digest(str(existing).casefold())[:16]
    indexfile = ROOT/'data/updates'/owner/'index.json'
    index = read(indexfile, {})
    target = None
    if key in index:
        candidate = (indexfile.parent/index[key]).resolve()
        if candidate.parent != existing.parent:
            raise ValueError('Invalid update project path')
        state = read(candidate/'data/update.json', {})
        if state.get('key') == key and state.get('base_project') == str(existing):
            target = candidate
    if target is None:
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        target = existing.with_name(existing.name+'-update-'+stamp)
        target.mkdir(); (target/'data').mkdir()
        public['comparison_label'] = '업데이트-'+stamp
        public['update_base_project'] = str(existing)
        save_json(target/'project.json', public)
        state = {'version': VERSION, 'key': key, 'status': 'preparing', 'created': stamp,
                 'base_project': str(existing), 'new_source': str(source),
                 'source_signature': signature, 'engine': engine}
        save_json(target/'data/update.json', state)
        index[key] = os.path.relpath(target, indexfile.parent); save_json(indexfile, index)
    else:
        public = read(target/'project.json', {})
        state = read(target/'data/update.json', {})
    if state['status'] == 'preparing':
        print('Game update: preparing revision',flush=True)
        _prepare_revision(target, public, engine)
        public = read(target/'project.json', public)
        state['status'] = 'importing'; save_json(target/'data/update.json', state)
    if state['status'] == 'importing':
        old_rows, known = _effective_cache(existing, target, oldcfg)
        new_rows = read_catalog(target)
        active = settings(target, public)
        if oldcfg.get('source_language', 'english') != public.get('source_language', 'english'):
            known = {}
        imported, report = reuse_translations(old_rows, new_rows, known, active)
        for item in imported:
            item['fingerprint'] = fingerprint(active)
            item['update_origin']['project'] = str(existing)
        cachefile = target/'data/translations.jsonl'; temporary = cachefile.with_suffix('.jsonl.tmp')
        temporary.write_text(''.join(json.dumps(item, ensure_ascii=False)+'\n' for item in imported), encoding='utf-8')
        temporary.replace(cachefile)
        for name in ('replacements.json', 'names.json'):
            if (existing/name).is_file(): shutil.copy2(existing/name, target/name)
        # Presentation preferences are portable; derived locations/hints/context
        # indexes, failure indexes and output ownership must be regenerated.
        if (existing/'data/tool-options.json').is_file():
            shutil.copy2(existing/'data/tool-options.json', target/'data/tool-options.json')
        reviewed = read(existing/'data/reviewed-translations.json', {})
        accepted = {item['source']: item['text'] for item in imported if item.get('model') == 'reviewed override' and item['source'] in reviewed}
        if accepted: save_json(target/'data/reviewed-translations.json', accepted)
        if oldcfg.get('source_language', 'english') == public.get('source_language', 'english'):
            report['auxiliary_reuse'] = _reuse_name_inputs(existing, target, active)
        report.update(base_project=str(existing), new_project=str(target), new_source=str(source),
                      original_project_modified=False, source_signature=signature)
        save_json(target/'data/update-report.json', report)
        state['status'] = 'pending'; save_json(target/'data/update.json', state)
    _register(target, source, state)
    save_json(target/'data/update.json', state)
    report = read(target/'data/update-report.json', {})
    print('Game update: project '+str(target), flush=True)
    print('Game update: reused {reused}; remaining {remaining}; ambiguous {ambiguous}; deleted {deleted_source_occurrences}'.format(**report), flush=True)
    return target, public, report


def mark_complete(project):
    path = Path(project)/'data/update.json'
    state = read(path, {})
    if state:
        state['status'] = 'complete'
        state['completed'] = datetime.now().isoformat(timespec='seconds')
        save_json(path, state)
