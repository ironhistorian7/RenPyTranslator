"""Conservative Japanese KAGEX ruby extraction; no scenario execution."""
import re
from collections import Counter

REVISION = 1
# KAGEX ruby readings are kana, optionally followed by the number of extra
# characters covered. Restrict recognition rather than guessing command tags.
_READING = re.compile(r'^([\u3041-\u3096\u309d-\u309f\u30a1-\u30fa\u30fd-\u30ff\uff66-\uff9f\u30fc]+)(?:,([0-9]+))?$')


def enabled(cfg):
    if not isinstance(cfg, dict) or cfg.get('engine') != 'kirikiri':
        return False
    if cfg.get('_kirikiri_dialect', cfg.get('kirikiri_dialect', 'kag')) != 'kagex_psb':
        return False
    from source_language import normalize
    try:
        return normalize(cfg.get('source_language')) == 'japanese'
    except ValueError:
        return False


def prepare(source, cfg):
    """Return body, reading/base records, and exact source span metadata.

    Only complete tags with an immediately following literal base of the
    declared length qualify. Other controls and malformed constructs survive.
    """
    if not enabled(cfg) or not isinstance(source, str):
        return source, [], []
    from kirikiri_tokens import spans
    controls = list(spans(source, 'kagex_psb'))
    occupied = set()
    for a, b in controls:
        occupied.update(range(a, b))
    records = []
    for start, end in controls:
        token = source[start:end]
        if not (token.startswith('[') and token.endswith(']')):
            continue
        match = _READING.fullmatch(token[1:-1])
        if not match:
            continue
        digits = match.group(2)
        if digits and len(digits) > 6:
            continue
        length = int(digits or '0') + 1
        base_end = end + length
        if base_end > len(source) or any(i in occupied for i in range(end, base_end)):
            continue
        base = source[end:base_end]
        if not base or any(c.isspace() or c in '[]<>\r\n' for c in base):
            continue
        records.append({'reading': match.group(1), 'base': base,
                        'start': start, 'end': end, 'base_end': base_end,
                        'token': token})
    parts = []; pos = 0
    for record in records:
        parts.append(source[pos:record['start']]); pos = record['end']
    parts.append(source[pos:])
    return ''.join(parts), [dict(reading=r['reading'], base=r['base']) for r in records], records


def strip_output_ruby(source, target, cfg):
    """Remove only exact ruby tags established by this source, respecting escapes."""
    if not isinstance(target, str):
        return target
    _, _, plan = prepare(source, cfg)
    if not plan:
        return target
    from kirikiri_tokens import spans
    remaining = Counter(r['token'] for r in plan)
    parts = []; pos = 0
    for start, end in spans(target, 'kagex_psb'):
        token = target[start:end]
        if remaining[token]:
            parts.append(target[pos:start]); pos = end
            remaining[token] -= 1
    parts.append(target[pos:])
    return ''.join(parts)


def needs_retranslation(source, target, cfg):
    """Legacy cached output with source ruby attached needs a fresh translation."""
    return isinstance(target, str) and strip_output_ruby(source, target, cfg) != target
