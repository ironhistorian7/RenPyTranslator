"""Shared Japanese-to-Korean residue policy for automatic output."""
import re
import unicodedata

JAPANESE = re.compile(r'[\u3041-\u3096\u30a1-\u30fa\uff66-\uff9d\u3400-\u9fff\uf900-\ufaff]')
HANGUL = re.compile(r'[\uac00-\ud7a3]')
PLACEHOLDER = re.compile(r'<rpt(\d+)/>')
SMALL_TSU = frozenset('っッｯ')


def enabled(cfg):
    if not cfg or cfg.get('engine', 'renpy') not in ('renpy', 'rpgmaker', 'kirikiri', 'unity'):
        return False
    from source_language import normalize
    try:
        return normalize(cfg.get('source_language')) == 'japanese'
    except ValueError:
        return False


def _masked_tokens(text, cfg):
    from translation import protect
    # Existing wire markers are controls, not generated indices; mask them
    # before protection so a literal marker cannot collide with a new one.
    text = PLACEHOLDER.sub(lambda m: ' ' * len(m.group()), text)
    protected, tokens = protect(text, cfg)
    # Expand each generated placeholder to its original width; all positions in
    # the resulting view therefore still index the original text.
    return PLACEHOLDER.sub(lambda m: ' ' * len(tokens[int(m.group(1))]), protected)


def _name_config(cfg):
    groups = list(cfg.get('_rpg_name_groups', ()) or ())
    names = cfg.get('_person_names', {}) or {}
    if isinstance(names, dict):
        groups.extend((source, target) for source, target in names.items()
                      if isinstance(source, str) and source and isinstance(target, str) and target)
    groups = [tuple(value for value in group if isinstance(value, str) and value)
              for group in groups]
    return dict(cfg, _rpg_name_groups=tuple(group for group in groups if group))


def visible_view(source, target, cfg):
    """Return an offset-preserving view, hiding controls and confirmed names."""
    from rpgmaker_names import mask_names
    source_view = _masked_tokens(source, cfg)
    target_view = _masked_tokens(target, cfg)
    return mask_names(source_view, target_view, _name_config(cfg))


def small_tsu_only_source(source, cfg):
    if not enabled(cfg):
        return False
    visible = visible_view(source, source, cfg)
    japanese = JAPANESE.findall(visible)
    return bool(japanese and all(c in SMALL_TSU for c in japanese) and
                all(c.isspace() or c in SMALL_TSU or
                    unicodedata.category(c)[0] in ('P', 'S') for c in visible))


def small_tsu_punctuation_result(source, target, cfg):
    if not enabled(cfg) or not small_tsu_only_source(source, cfg):
        return False
    original = visible_view(source, source, cfg)
    visible = visible_view(source, target, cfg)
    remaining = ''.join(c for c in visible if c not in SMALL_TSU and not c.isspace())
    if not remaining or any(unicodedata.category(c)[0] not in ('P', 'S') for c in remaining):
        return False
    punctuation = ''.join(c for c in original if unicodedata.category(c)[0] in ('P', 'S'))
    cursor = iter(remaining)
    return bool(punctuation) and all(any(c == d for d in cursor) for c in punctuation)


def classify(source, target, cfg):
    result = dict(kind='not_applicable', hangul_count=0, has_korean=False, japanese_count=0, spans=[])
    if not enabled(cfg):
        return result
    from rpgmaker_names import name_only
    visible = visible_view(source, target, cfg)
    hangul = len(HANGUL.findall(visible))
    # Names must not inflate the residue ratio, but a confirmed Korean name
    # is still Korean text when deciding whether a translation exists.
    has_korean = bool(HANGUL.search(_masked_tokens(target, cfg)))
    matches = list(JAPANESE.finditer(visible))
    spans = []
    for match in matches:
        pos = match.start()
        if spans and spans[-1][1] == pos:
            spans[-1][1] = pos + 1
        else:
            spans.append([pos, pos + 1])
    result.update(hangul_count=hangul, has_korean=has_korean, japanese_count=len(matches), spans=spans)
    if not target.strip():
        result['kind'] = 'no_korean'
    elif small_tsu_punctuation_result(source, target, cfg):
        result['kind'] = 'minor' if matches else 'clean'
    elif (cfg.get('_name_preparation') or
          cfg.get('_kirikiri_usage') in ('speaker_label', 'name') or
          cfg.get('_rpg_usage') in ('speaker_label', 'name') or
          cfg.get('_usage') in ('speaker_label', 'name') or
          name_only(_masked_tokens(source, cfg), _name_config(cfg))):
        result['kind'] = 'clean'
    else:
        # Compare only complete, supported numeric utterances, never extracted
        # fragments of prose or controls. Name exemptions above take precedence.
        from japanese_numbers import compare
        numeric = compare(source, target)
        if numeric:
            result['kind'] = 'clean' if numeric == 'match' else 'numeric_mismatch'
            return result
        if not has_korean:
            if JAPANESE.search(visible_view(source, source, cfg)):
                result['kind'] = 'no_korean'
            else:
                result['kind'] = 'mixed' if matches else 'clean'
        elif not matches:
            result['kind'] = 'clean'
        elif all(m.group() in SMALL_TSU for m in matches):
            result['kind'] = 'minor'
        elif (len(matches) <= 2 and all(end - start == 1 for start, end in spans)
              and len(matches) / (hangul + len(matches)) <= 0.15):
            result['kind'] = 'minor'
        else:
            result['kind'] = 'mixed'
    return result


def prepare_output(source, target, cfg):
    details = classify(source, target, cfg)
    if (not enabled(cfg) or details['kind'] in ('clean', 'not_applicable') or
            not details['japanese_count'] or
            not (details['has_korean'] or small_tsu_punctuation_result(source, target, cfg))):
        return target, details
    positions = [pos for start, end in details['spans'] for pos in range(start, end)]
    if not all(target[pos] in SMALL_TSU for pos in positions):
        return target, details
    removed = set(positions)
    return ''.join(char for pos, char in enumerate(target) if pos not in removed), details


def language_errors(source, target, cfg, details=None):
    """Report language errors from the same residue classification."""
    details = details if details is not None else classify(source, target, cfg)
    if not enabled(cfg) or details['kind'] in ('not_applicable', 'clean'):
        return []
    if details['kind'] == 'numeric_mismatch':
        return ['numeric value mismatch']
    errors = []
    if details['japanese_count']:
        errors.append('untranslated Japanese text')
    if details['kind'] == 'no_korean':
        errors.append('no Korean translation')
    return errors
