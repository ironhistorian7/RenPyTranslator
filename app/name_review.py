"""Review snapshots for proven name slots. Never alters the translation cache."""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re

VERSION = 1
HAN = re.compile(r'[\u3400-\u9fff\uf900-\ufaff]')
KANA = re.compile(r'[\u3041-\u30ff]')
QUALIFIER = re.compile(r'^(.+?)([（(].+[）)].*)$')


def _read(path, default):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else default


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def _valid(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 200 and not any(c in value for c in '{}[]\\\r\n')


def _entry(key, source, proposal, aliases=(), reading=None, basis=None, slot=None, manual_override=None):
    proposal = proposal if _valid(proposal) else source
    # Kana from another structured name field is evidence only when unique.
    uncertain = bool(HAN.search(source) and not reading)
    remaining = any(c.isalpha() and not ('가' <= c <= '힣' or 'ㄱ' <= c <= 'ㆎ') for c in proposal)
    reason = 'reading_unknown' if uncertain else ('untranslated_letters' if remaining else ('no_automatic_result' if proposal == source and not proposal.isdigit() else ''))
    return dict(key=key, source=source, proposed=proposal,
                automatic=proposal, needs_review=bool(reason), reason=reason, reading=reading, basis=basis,
                aliases=sorted(set(a for a in aliases if _valid(a))), slot=slot,
                manual_override=manual_override)



def _kirikiri_slot(row):
    if row.get('usage') not in ('speaker_label', 'speaker'): return False
    if row.get('dialect') == 'kagex_psb':
        loc = row.get('location')
        return (isinstance(loc, (list, tuple)) and len(loc) == 5 and
                loc[0] == 'scenes' and loc[2] == 'texts' and loc[4] == 1)
    return row.get('dialect') == 'kag'


def _valid_kirikiri(value):
    return _valid(value) and not any(c in value for c in '$%#')

def _raw(project, cfg):
    from translation import read_catalog, cache
    rows = read_catalog(project)
    result = []
    overrides = _read(project/'names.json', {}).get('overrides', {})
    if not isinstance(overrides, dict):
        raise ValueError('이름 설정이 올바르지 않습니다.')
    if cfg.get('engine') == 'kirikiri':
        known = cache(project, dict(cfg, _skip_name_review=True))
        proposals = _read(project/'data/name-proposals.json', {})
        groups = defaultdict(list)
        for row in rows:
            if _kirikiri_slot(row) and _valid_kirikiri(row.get('source')):
                groups[row['source']].append(row)
        for source, members in sorted(groups.items()):
            members = sorted(members, key=lambda row: str(row['id']))
            row = members[0]; uid = str(row['id'])
            automatic = proposals.get(uid, {})
            proposal = None
            for member in members:
                candidate = proposals.get(str(member['id']), {})
                if candidate.get('source') == source and candidate.get('model') == cfg.get('model') and _valid_kirikiri(candidate.get('text')):
                    proposal = candidate['text']; break
            if proposal is None:
                for member in members:
                    item = known.get(str(member['id']), {})
                    if item.get('source') == source and item.get('status') not in ('source_fallback', 'ui_original') and _valid_kirikiri(item.get('text')):
                        proposal = item['text']; break
            manual = overrides.get(source)
            if _valid_kirikiri(manual): proposal = manual
            if not _valid_kirikiri(proposal): proposal = None
            entry = _entry('kirikiri:' + uid, source, proposal, [source], slot='speaker', manual_override=manual)
            entry['members'] = [str(member['id']) for member in members]
            if (not _valid_kirikiri(manual) and automatic.get('source') == source and
                    automatic.get('model') == cfg.get('model') and automatic.get('error')):
                entry.update(needs_review=True, reason='automatic_failed')
            result.append(entry)
    elif cfg.get('engine') == 'rpgmaker':
        from rpgmaker_names import _slot, _sources
        known = cache(project, dict(cfg, _skip_name_review=True))
        proposals = _read(project/'data/name-proposals.json', {})
        slots = [(row, _slot(row)) for row in rows]
        actors = [(str(row['id']), {a for a in _sources(row) if _valid(a)})
                  for row, slot in slots if slot == 'actor' and _valid(row.get('source'))]
        owners = defaultdict(set)
        for uid, aliases in actors:
            for alias in aliases: owners[alias].add(uid)
        speakers = defaultdict(list)
        for row, slot in slots:
            if slot != 'speaker' or not _valid(row.get('source')): continue
            aliases = frozenset(a for a in _sources(row) if _valid(a))
            possible = set().union(*(owners[a] for a in aliases)) if aliases else set()
            if len(possible) == 1 and any(uid in possible and aliases <= actor_aliases for uid, actor_aliases in actors):
                continue
            # A source shared by different actors is never broadcast.
            group = (row['source'], tuple(sorted(aliases)), str(row['id']) if len(possible) > 1 else '')
            speakers[group].append(row)
        selected = [(row, slot, None) for row, slot in slots if slot == 'actor']
        for group in sorted(speakers):
            members = sorted(speakers[group], key=lambda row: str(row['id']))
            selected.append((members[0], 'speaker', [str(row['id']) for row in members]))
        for row, slot, members in selected:
            source = row.get('source')
            if slot not in ('actor', 'speaker') or not _valid(source):
                continue
            aliases = _sources(row)
            kana = sorted(a for a in aliases if KANA.search(a) and not HAN.search(a))
            latin = sorted(a for a in aliases if a != source and re.fullmatch(r"[A-Za-z][A-Za-z .'-]*", a))
            reading = kana[0] if len(kana) == 1 else (latin[0] if not kana and len(latin) == 1 else None)
            item = known.get(row['id'], {})
            automatic = proposals.get(row['id'], {})
            proposal = automatic.get('text') if (automatic.get('source') == source and automatic.get('model') == cfg.get('model')) else None
            if proposal is None and item.get('status') not in ('source_fallback', 'ui_original'):
                proposal = item.get('text')
            manual = overrides.get(source)
            if _valid(manual): proposal = manual
            entry = _entry('rpg:' + str(row['id']), source, proposal, aliases,
                           reading, 'structured_alias' if reading else None, slot, manual)
            if (not _valid(manual) and automatic.get('source') == source
                    and automatic.get('model') == cfg.get('model') and automatic.get('error')):
                entry.update(needs_review=True, reason='automatic_failed')
            if members is not None: entry['members'] = members
            result.append(entry)
    else:
        from identity_policy import accepted
        hints = _read(project/'data/name-hints.json', {})
        saved = _read(project/'data/name-transliterations.json', {})
        types = _read(project/'data/identity-types.json', {})
        seen = set()
        for expr, source in sorted(hints.get('fixed_name_sources', {}).items()):
            info = hints.get('names', {}).get(expr, {})
            if not _valid(source) or info.get('kind') in ('address', 'role', 'text', 'answer') or types.get(source, {}).get('kind') in ('address', 'role', 'object', 'unknown'):
                continue
            seen.add(source)
            proposal = overrides.get(source, saved.get('names', {}).get(source))
            result.append(_entry('renpy:fixed:' + expr, source, proposal, [source], slot='fixed', manual_override=overrides.get(source)))
        for source, info in sorted(types.items()):
            if source in seen or info.get('kind') != 'name' or not _valid(source):
                continue
            if not accepted(saved, types, source):
                continue
            result.append(_entry('renpy:name:' + source, source, overrides.get(source, saved.get('names', {}).get(source)), [source], slot='name', manual_override=overrides.get(source)))
    result.sort(key=lambda e: e['key'])
    if len({e['key'] for e in result}) != len(result):
        raise ValueError('이름 검토 항목이 중복되었습니다.')
    return result


def build_review(project, cfg):
    project = Path(project)
    entries = _raw(project, cfg)
    saved = _read(project/'data/name-review.json', {})
    # Existing snapshots are trusted only after their own integrity and override
    # checks, and only while an original same-source name slot still exists.
    if cfg.get('engine') == 'kirikiri':
        old_entries, old_decisions = _saved(project)
        candidates = defaultdict(list)
        current_by_source = {e['source']: e for e in entries}
        for old in old_entries:
            if not old.get('key', '').startswith('kirikiri:'): continue
            current = current_by_source.get(old.get('source'))
            if not current or old.get('slot') != 'speaker': continue
            old_members = old.get('members', [old['key'][len('kirikiri:'):]])
            value = old_decisions.get(old['key'])
            if (isinstance(old_members, list) and set(old_members) & set(current['members'])
                    and _valid_kirikiri(value)):
                candidates[old['source']].append((old, value))
        # A snapshot containing retained decisions uses stable historical keys.
        # Reuse it verbatim only when every underlying group is unchanged.
        def comparable(entry):
            excluded = {'key', 'retained_decision'}
            if 'retained_decision' in entry: excluded |= {'needs_review', 'reason'}
            return {k: v for k, v in entry.items() if k not in excluded}
        old_by_source = {e.get('source'): e for e in old_entries if e.get('key', '').startswith('kirikiri:')}
        if (len(old_entries) == len(entries) == len(old_by_source) and
                all(e['source'] in old_by_source and
                    comparable(old_by_source[e['source']]) == comparable(
                        dict(e, retained_decision=old_by_source[e['source']]['retained_decision'])
                        if 'retained_decision' in old_by_source[e['source']] else e)
                    for e in entries)):
            entries = [dict(e) for e in old_entries]
            entries.sort(key=lambda e: e['key'])
        raw_fingerprint = _hash(entries)
        exact = saved.get('version') == VERSION and saved.get('fingerprint') == raw_fingerprint
        if not exact:
            for entry in entries:
                choices = candidates.get(entry['source'], [])
                if not choices or len({value for _, value in choices}) != 1: continue
                old, value = sorted(choices, key=lambda pair: pair[0]['key'])[0]
                # A former representative ID may now belong to another source.
                # Only retain its key while that ID still belongs to this group.
                old_uid = old['key'][len('kirikiri:'):]
                if old_uid in entry['members']:
                    entry['key'] = old['key']
                entry['retained_decision'] = value
                entry['needs_review'] = False
                entry['reason'] = ''
            entries.sort(key=lambda e: e['key'])
    fingerprint = _hash(entries)
    confirmed = saved.get('fingerprint') == fingerprint and saved.get('version') == VERSION
    decisions = saved.get('decisions', {}) if confirmed else {}
    for entry in entries:
        entry['value'] = decisions.get(entry['key'], entry.get('retained_decision', entry['source'] if entry['reason'] in ('reading_unknown','automatic_failed') else entry['proposed']))
    return dict(version=VERSION, fingerprint=fingerprint, entries=entries, confirmed=confirmed)


def apply_review(project, cfg, payload):
    project = Path(project)
    current = build_review(project, cfg)
    if not isinstance(payload, dict) or payload.get('version') != VERSION or payload.get('fingerprint') != current['fingerprint']:
        raise ValueError('이름 목록이 변경되었습니다. 다시 확인해 주세요.')
    submitted = payload.get('entries')
    if not isinstance(submitted, list) or len(submitted) != len(current['entries']):
        raise ValueError('이름 목록을 모두 확인해 주세요.')
    expected = {e['key']: e for e in current['entries']}
    decisions = {}
    for entry in submitted:
        if not isinstance(entry, dict):
            raise ValueError('이름 검토 항목이 올바르지 않습니다.')
        key = entry.get('key')
        if key not in expected or key in decisions:
            raise ValueError('이름 검토 항목이 없거나 중복되었습니다.')
        if {k:v for k,v in entry.items() if k != 'value'} != {k:v for k,v in expected[key].items() if k != 'value'}:
            raise ValueError('이름 항목이 변경되었습니다. 다시 확인해 주세요.')
        value = entry.get('value')
        if not _valid(value) or key.startswith('kirikiri:') and not _valid_kirikiri(value):
            raise ValueError('이름을 입력해 주세요. 제어 문자는 사용할 수 없습니다.')
        decisions[key] = value
    from engine import save_json
    saved = dict(version=VERSION, fingerprint=current['fingerprint'], decisions=decisions,
                 entries=[{k:v for k,v in e.items() if k != 'value'} for e in current['entries']])
    save_json(project/'data/name-review.json', saved)
    return dict(saved, confirmed=True)


def _saved(project):
    project = Path(project)
    data = _read(project/'data/name-review.json', {})
    entries = data.get('entries', [])
    if data.get('version') != VERSION or _hash(entries) != data.get('fingerprint'):
        return [], {}
    overrides = _read(project/'names.json', {}).get('overrides', {})
    if not isinstance(overrides, dict): return [], {}
    entries = [e for e in entries if e.get('manual_override') == overrides.get(e.get('source'))]
    return entries, data.get('decisions', {})


def confirmed_map(project):
    entries, decisions = _saved(project)
    project = Path(project)
    hints = _read(project/'data/name-hints.json', {})
    types = _read(project/'data/identity-types.json', {})
    groups = defaultdict(set)
    for e in entries:
        value = decisions.get(e['key'])
        key = e['key']; source = e['source']
        if key.startswith('renpy:fixed:'):
            expr = key[len('renpy:fixed:'):]
            info = hints.get('names', {}).get(expr, {})
            if hints.get('fixed_name_sources', {}).get(expr) != source or info.get('kind') in ('address', 'role', 'text', 'answer'):
                continue
            if types.get(source, {}).get('kind') in ('address', 'role', 'object', 'unknown'): continue
        elif key.startswith('renpy:name:'):
            if types.get(source, {}).get('kind') != 'name': continue
        else:
            continue
        if _valid(value): groups[source].add(value)
    return {source: next(iter(values)) for source, values in groups.items() if len(values) == 1}


def _anchored(project, rows):
    from rpgmaker_names import _slot, _sources
    entries, decisions = _saved(project)
    by_id = {uid: e for e in entries if e['key'].startswith('rpg:')
             for uid in e.get('members', [e['key'][4:]])}
    matched = []
    for row in rows:
        uid = str(row.get('id'))
        e = by_id.get(uid)
        if not e or e['source'] != row.get('source') or e['slot'] != _slot(row): continue
        if set(e['aliases']) != {a for a in _sources(row) if _valid(a)}: continue
        value = decisions.get(e['key'])
        if _valid(value): matched.append((uid, e, value))
    return by_id, matched


def rpg_terms(project, rows):
    """Return unambiguous actor aliases and safe individual validation groups."""
    _, matched = _anchored(project, rows)
    candidates = defaultdict(set)
    owners = defaultdict(set)
    actor_owners = defaultdict(set)
    actor_aliases = {alias for _, e, _ in matched if e['slot'] == 'actor' for alias in e['aliases']}
    for uid, e, value in matched:
        if e['slot'] == 'actor':
            for alias in e['aliases']:
                candidates[alias].add(value); owners[alias].add(uid); actor_owners[alias].add(uid)
            # A source and target both having a parenthesized state prove the
            # corresponding base forms; no guessed suffix stripping.
            sm = QUALIFIER.fullmatch(e['source']); tm = QUALIFIER.fullmatch(value)
            if sm and tm:
                candidates[sm[1]].add(tm[1]); owners[sm[1]].add(uid)
                actor_owners[sm[1]].add(uid)
        elif not any(alias in actor_aliases for alias in e['aliases']):
            # One representative owns an explicitly anchored speaker group.
            owner = e['key'][4:]
            for alias in e['aliases']:
                candidates[alias].add(value); owners[alias].add(owner)
    canonical = {s:next(iter(v)) for s,v in candidates.items() if len(v) == 1 and
                 (len(owners[s]) == 1 or owners[s] == actor_owners[s])}
    return dict(terms=canonical, groups=[(s, t) for s, t in sorted(canonical.items())],
                ambiguous_sources=sorted(s for s in candidates if s not in canonical))



def _kirikiri_anchored(project, rows):
    entries, decisions = _saved(project)
    by_id = {uid: e for e in entries if e['key'].startswith('kirikiri:')
             for uid in e.get('members', [e['key'][len('kirikiri:'):]])}
    matched = []
    for row in rows:
        uid = str(row.get('id')); entry = by_id.get(uid)
        if not entry or not _kirikiri_slot(row) or row.get('source') != entry['source']: continue
        value = decisions.get(entry['key'])
        if _valid_kirikiri(value): matched.append((uid, entry, value))
    # Migration can add null-default display slots after a review was saved.
    # Extend only a still-anchored, unambiguous speaker decision to those new
    # derived slots; never infer names from dialogue or unrelated rows.
    anchored = defaultdict(set)
    representatives = {}
    for _, entry, value in matched:
        anchored[entry['source']].add(value)
        representatives[entry['source']] = entry
    for row in rows:
        uid = str(row.get('id')); source = row.get('source')
        if uid in by_id or not row.get('derived_speaker') or not _kirikiri_slot(row): continue
        choices = anchored.get(source, set())
        if len(choices) == 1:
            matched.append((uid, representatives[source], next(iter(choices))))
    return by_id, matched


def kirikiri_terms(project, rows):
    _, matched = _kirikiri_anchored(project, rows)
    candidates = defaultdict(set)
    for uid, entry, value in matched: candidates[entry['source']].add(value)
    terms = {source: next(iter(values)) for source, values in candidates.items() if len(values) == 1}
    return dict(terms=terms, ambiguous_sources=sorted(s for s in candidates if s not in terms))

def configuration(project, cfg, rows, *, prepare_names=False):
    """Supply approved, anchored names to translation validation without cache access."""
    is_kirikiri = cfg.get('engine') == 'kirikiri'
    result = kirikiri_terms(project, rows) if is_kirikiri else rpg_terms(project, rows)
    terms = dict(cfg.get('_person_names', {}))
    for source in result['ambiguous_sources']:
        terms.pop(source, None)
    terms.update(result['terms'])
    if is_kirikiri:
        cfg = dict(cfg, _person_names=terms)
        if prepare_names and not cfg.get('_name_preparation') and not cfg.get('_skip_name_review'):
            from source_language import normalize
            if normalize(cfg.get('source_language')) == 'japanese':
                from kirikiri_name_readings import collect
                from kirikiri_reviewed_terms import approved_aliases
                aliases = approved_aliases(project)
                nameplates = dict(result['terms'])
                for source in result['ambiguous_sources']:
                    aliases.pop(source, None)
                for source in aliases.keys() & nameplates.keys():
                    if aliases[source] != nameplates[source]:
                        aliases.pop(source)
                        nameplates.pop(source)
                cfg['_kirikiri_name_index'] = collect(rows, cfg, approved_names=aliases,
                                                      nameplate_names=nameplates)
        return cfg
    groups = list(cfg.get('_rpg_name_groups', ()))
    groups.extend(result['groups'])
    return dict(cfg, _person_names=terms, _rpg_name_groups=tuple(groups))


def overlay(project, rows, known, *, engine=None):
    if engine is None:
        entries, _ = _saved(project)
        engine = 'kirikiri' if any(e.get('key', '').startswith('kirikiri:') for e in entries) else 'rpgmaker'
    if engine == 'kirikiri':
        _, matched = _kirikiri_anchored(project, rows)
        effective = dict(known)
        for uid, entry, value in matched:
            effective[uid] = dict(known.get(uid, {}), id=uid, source=entry['source'], text=value, status='name_review', error=None)
        report = kirikiri_terms(project, rows)
        return effective, dict(updated_ids=[uid for uid, _, _ in matched], ambiguous_sources=report['ambiguous_sources'], cache_modified=False)
    from rpgmaker_names import _slot
    by_id, matched = _anchored(project, rows)
    effective = dict(known)
    for uid, e, value in matched:
        effective[uid] = dict(known.get(uid, {}), id=uid, source=e['source'], text=value, status='name_review', error=None)
    report = rpg_terms(project, rows)
    canonical = report['terms']
    updated = []
    for row in rows:
        uid = str(row.get('id'))
        if uid in by_id or _slot(row) != 'speaker': continue
        target = canonical.get(row.get('source'))
        if target is not None:
            effective[uid] = dict(known.get(uid, {}), id=uid, source=row['source'], text=target, status='name_review', error=None)
            updated.append(uid)
    return effective, dict(updated_ids=updated, ambiguous_sources=report['ambiguous_sources'], cache_modified=False)
