"""Conservative RPG Maker request preparation; never rewrite dialogue content."""
from collections import defaultdict
import re

from rpgmaker_names import _plain_name, _slot, _sources
from rpgmaker_tokens import READING_CONTROLS


def configuration(cfg, rows, known=None):
    """Collect only schema-anchored names, rejecting conflicting mappings."""
    if cfg.get('engine') != 'rpgmaker':
        return cfg
    from label_policy import expansion_errors
    known = known or {}
    approved = cfg.get('_person_names', {})
    approved = approved if isinstance(approved, dict) else {}
    candidates = defaultdict(set)
    conflicts = set()
    for row in rows:
        if _slot(row) not in ('actor', 'speaker'):
            continue
        forms = {s for s in _sources(row) if _plain_name(s) and s == s.strip()}
        if not forms:
            continue
        explicit = {approved[s] for s in forms if s in approved and _plain_name(approved[s])}
        if len(explicit) > 1:
            conflicts.update(forms)
            continue
        item = known.get(row.get('id'), {})
        if not isinstance(item, dict):
            item = {}
        cached = item.get('text')
        anchored = item.get('source') in forms
        valid_cache = (anchored and not item.get('error') and
                       item.get('status') not in ('source_fallback', 'ui_original', 'error', 'failed') and
                       _plain_name(cached) and
                       not expansion_errors(dict(row, usage='speaker_label'), cached))
        target = next(iter(explicit)) if explicit else cached if valid_cache else None
        for form in forms:
            if target is not None:
                candidates[form].add(target)
            else:
                candidates[form]  # Raw fallback only if no approved target exists.
    result = {}
    for form, targets in candidates.items():
        if form not in conflicts and len(targets) <= 1:
            result[form] = next(iter(targets)) if targets else form
    return dict(cfg, _rpg_display_names=result)


def prepare(source, cfg, usage='dialogue', speaker=None):
    """Extract a proven plain first-line speaker only when a body follows."""
    plan = dict(text=source, prefix='', header='', source_header='', separator='', suffix='')
    if usage != 'dialogue' or not isinstance(source, str):
        return plan
    match = re.match(r'^([ \t]*)([^\r\n]*?)([ \t]*)(\r\n|\n|\r)', source)
    if not match or not source[match.end():].strip():
        return plan
    leading, name, trailing, separator = match.groups()
    names = cfg.get('_rpg_display_names', {})
    if not isinstance(names, dict) or name not in names or not _plain_name(name):
        return plan
    target = names[name]
    if not _plain_name(target):
        return plan
    if isinstance(speaker, str) and speaker.strip() and speaker.strip().casefold() not in ('?', 'none', 'unknown', 'unknown speaker', 'なし', '不明'):
        speaker = speaker.strip()
        if names.get(speaker, speaker) != target:
            return plan
    body = source[match.end():]
    body_first = re.split(r'\r\n|\n|\r', body, maxsplit=1)[0]
    body_starts_name = names.get(body_first, body_first) == target
    return dict(text=body, prefix=leading, header=target,
                source_header=name, separator=separator, suffix=trailing,
                body_starts_name=body_starts_name)


def restore(text, plan):
    """Restore the extracted header while leaving translated body untouched."""
    if not plan.get('header'):
        return text
    if not plan.get('body_starts_name'):
        echoed = re.match(r'^' + re.escape(plan['header']) + r'(?:\r\n|\n|\r)', text)
        if echoed:
            text = text[echoed.end():]
    return plan['prefix'] + plan['header'] + plan['suffix'] + plan['separator'] + text


def reading_text(source, cfg=None):
    """Return a semantic reference only when documented controls were removed."""
    if not READING_CONTROLS.search(source):
        return None
    return READING_CONTROLS.sub('', source)


def clean_context(context):
    """Remove stale source/draft references, retaining user-supplied context."""
    stale = {'source_passage', 'old_draft', 'draft', 'original_source',
             'reading_text', 'spelling_context', 'correction_reading'}
    clean = {key: value for key, value in (context or {}).items() if key not in stale}
    dropped = [{'part': key, 'reason': 'clean_retry'} for key in sorted(stale) if key in (context or {})]
    return clean, dropped
