"""Compare saved translations of the same sentence within one speaker's scope."""
from collections import defaultdict
import re
import uuid

from source_context import LABEL_HASH, SourceContext


_ENDING = re.compile(r'[.!?。！？…]+')
_CLOSING = ' \t\u3000"\'”’」』】）》）)'
_UNKNOWN_SPEAKERS = {'unknown', 'unknown speaker', 'none', '???', '?', '불명', '不明'}
_INELIGIBLE = {'source_fallback', 'partial_translation', 'ui_original', 'ui_fixed', 'literal'}


def _speaker(value):
    if isinstance(value, str) and value.strip() and value.strip().casefold() not in _UNKNOWN_SPEAKERS:
        return value.strip()
    return ''


def _single_sentence(source, cfg):
    from translation import PLACEHOLDER, protect
    if '\n' in source or '\r' in source:
        return False
    visible = PLACEHOLDER.sub('', protect(source, cfg)[0]).rstrip(_CLOSING)
    endings = list(_ENDING.finditer(visible))
    return (not endings or (len(endings) == 1 and endings[0].end() == len(visible))) and any(
        char.isalpha() for char in visible)


def _location(row):
    file = row.get('source_file') or row.get('file', '')
    line = row.get('source_line')
    if not isinstance(line, int) and isinstance(row.get('line'), int):
        line = row['line'] + 1
    return f'{file} · {line}행' if isinstance(line, int) else file


def build(project, cfg, rows, known):
    """Separate review groups from the row IDs needed only when saving a choice."""
    engine = cfg.get('engine', 'renpy')
    dialogue = [row for row in rows if row.get('kind') == 'dialogue' and row.get('usage', 'dialogue') == 'dialogue']
    candidates = defaultdict(list)
    from translation import HANGUL
    for row in dialogue:
        uid = row['id']
        item = known.get(uid, {})
        source = row['source']
        text = item.get('text')
        speaker = _speaker(row.get('speaker_internal')) or _speaker(row.get('speaker'))
        block = LABEL_HASH.sub('', row.get('block') or '')
        if (not speaker or not block or not row.get('file') or not isinstance(text, str)
                or not HANGUL.search(text) or text == source or item.get('source') != source
                or item.get('status') in _INELIGIBLE or item.get('error')
                or any(item.get(flag) for flag in ('_rpg_residual_language', '_japanese_residual_language',
                                                   '_japanese_ruby_pending', '_unity_numeric_review'))):
            continue
        listener = _speaker(row.get('listener'))
        candidates[row['file'], block, row.get('context_scope'),
                   speaker, listener, row.get('dialect'), row.get('unity_format'), source].append((row, item))

    review = {'kind': 'sentence', 'fingerprint': uuid.uuid4().hex, 'groups': []}
    saved_groups = {}
    repeated = [group for group in candidates.values() if len({item['text'] for _, item in group}) > 1]
    scopes = None
    if repeated and engine == 'renpy':
        # Use the prepared script's control-flow boundaries without reading the
        # original game or reloading/relogging any contextual TXT guidance.
        try:
            scopes = SourceContext(dialogue, project, cfg, load_notes=False)
        except (OSError, UnicodeError):
            return review, saved_groups
    for group in repeated:
        partitions = defaultdict(list)
        for row, item in group:
            partitions[scopes.keys[row['id']] if scopes else None].append((row, item))
        for matches in partitions.values():
            first, _ = matches[0]
            variants = defaultdict(list)
            editable = []
            for row, item in matches:
                fixed = item.get('status') in ('manual_translation', 'name_review') or item.get('model') == 'reviewed override'
                variants[item['text']].append((row, fixed))
                if not fixed:
                    editable.append((row, item['text']))
            if len(variants) < 2 or not editable:
                continue
            active = cfg
            if engine == 'kirikiri':
                from kirikiri_translation import row_config
                active = row_config(cfg, first)
            if not _single_sentence(first['source'], active):
                continue
            choices = []
            for text, uses in variants.items():
                choices.append({'id': uses[0][0]['id'], 'text': text, 'count': len(uses),
                                'location': _location(uses[0][0]), 'manual': any(fixed for _, fixed in uses),
                                'editable_count': sum(not fixed for _, fixed in uses)})
            uid = first['id']
            scope = first.get('context_scope') or LABEL_HASH.sub('', first['block'])
            review['groups'].append({'id': uid, 'source': first['source'], 'scope': scope,
                                     'speaker': _speaker(first.get('speaker')) or _speaker(first.get('speaker_internal')),
                                     'listener': _speaker(first.get('listener')), 'variants': choices})
            saved_groups[uid] = editable
    return review, saved_groups


def selected_rows(cfg, review, saved_groups, reply):
    """Resolve only explicitly chosen groups and validate each changed value once."""
    from translation import fingerprint, validate_entry
    if (not isinstance(reply, dict) or reply.get('kind') != 'sentence'
            or reply.get('fingerprint') != review['fingerprint'] or not isinstance(reply.get('selections'), list)):
        raise ValueError('문장 검토 목록을 다시 확인하세요.')
    groups = {group['id']: group for group in review['groups']}
    seen = set()
    records = []
    cache_fingerprint = fingerprint(cfg)
    for selection in reply['selections']:
        uid = selection.get('id') if isinstance(selection, dict) else None
        if not isinstance(uid, str) or uid not in groups or uid in seen:
            raise ValueError('문장 검토 항목을 다시 확인하세요.')
        seen.add(uid)
        group = groups[uid]
        if set(selection) == {'id', 'variant'} and isinstance(selection['variant'], str):
            text = next((item['text'] for item in group['variants'] if item['id'] == selection['variant']), None)
        elif set(selection) == {'id', 'text'} and isinstance(selection['text'], str):
            text = selection['text']
        else:
            text = None
        row = saved_groups[uid][0][0]
        location = _location(row)
        if text is None or not text.strip() or '\0' in text or len(text.splitlines()) != 1 or '\n' in text or '\r' in text:
            raise ValueError(f'{location} · 한 줄 번역문을 선택하거나 입력하세요.')
        changed = [candidate for candidate, current in saved_groups[uid] if text != current]
        if not changed:
            continue
        issues = validate_entry(row['source'], dict(row, text=text), cfg)
        if issues:
            raise ValueError(f'{location} · 선택한 번역문의 형식이나 언어를 확인하세요.')
        records.extend({'id': candidate['id'], 'source': candidate['source'], 'text': text,
                        'model': cfg['model'], 'fingerprint': cache_fingerprint, 'status': 'manual_translation'}
                       for candidate in changed)
    return records
