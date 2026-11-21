"""Conservative span extraction for selected Unity TextAsset localization formats.

``key_value``: one ``key = value`` entry per line; comments begin with #.
``command``: documented line-oriented dialogue grammar, requiring a companion
reference file. Some parallel, sentence-like pipe tables are recognized only
when several entries are independently corroborated by that reference.
``json``: explicit flat string map with a companion containing matching keys.
``csv``: explicit unquoted key,source,translation table with a companion.
Auto-detection only recognizes multi-entry key/value documents.
This module does not load or write assets.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
import json
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    kind: str


_KV = re.compile(r"^(?P<key>[^\r\n]+?)(?P<sep>\s+=\s+)(?P<value>.*)$")
_COMMAND = re.compile(r"^(?P<prefix>\s*(?:[NYT]\s+|H\s+[^|\r\n]+\s*\|\s*|>\s*(?:\[[^\]\r\n]+\]\s*)?|(?:name|role|seen)\s+(?!\|)))(?P<value>.*)$")
_GIFT = re.compile(r"^(?P<prefix>\s*[^\s|]+\s*\|\s*)(?P<value>[^|\r\n]+?)(?P<suffix>\s*\|\s*[+-]?\d+(?:\.\d+)?\s*)$")
_SUFFIX = re.compile(r"(?P<suffix>\s*\|\s*heart\s+[+-]?\d+\s*)$")
_TOKEN = re.compile(r"\{[^{}\r\n]+\}|%\d*\$?[#0 +\-]*\d*(?:\.\d+)?[a-zA-Z]|\\[a-zA-Z]|<[^<>\r\n]+>")
_TAG = re.compile(r"<[^<>\r\n]+>|\\[a-zA-Z]")
_BRACKET = re.compile(r"[()（）]")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")

# Only unambiguous English number words are evidence for newly introduced
# digits. Identifiers and markup are excluded.
_ONES = dict(zip(('zero one two three four five six seven eight nine ten eleven twelve '
                  'thirteen fourteen fifteen sixteen seventeen eighteen nineteen').split(), range(20)))
_TENS = dict(zip('twenty thirty forty fifty sixty seventy eighty ninety'.split(), range(20, 100, 10)))
_SCALES = {'thousand': 1000, 'million': 1000000, 'billion': 1000000000}
_WORD_NUMBER = re.compile(r"(?<![A-Za-z0-9_])(?:" + '|'.join((*_ONES, *_TENS, 'hundred', *_SCALES, 'and')) + r")(?![A-Za-z0-9_])", re.I)
# Only a single, plainly expressed quantity on each side is compared. Ambiguous
# sentences are left to human review without a numeric warning.
_KOREAN_SCALE_VALUES = {'십': 10, '백': 100, '천': 1000, '만': 10000, '십만': 100000, '백만': 1000000, '천만': 10000000, '억': 100000000, '조': 1000000000000}
_VISIBLE_QUANTITY = re.compile(r'(?<![A-Za-z0-9_.])[-−]?\d+(?:,\d{3})*(?:\.\d+)?(?:십만|백만|천만|십|백|천|만|억|조)?(?![A-Za-z0-9_])')


def _english_scaled_quantities(text: str):
    """Identify explicit scaled amounts without treating every 'one' as a count."""
    text = re.sub(r'\ba(?=\s+(?:hundred|thousand|million|billion)\b)', 'one', text, flags=re.I)
    words = list(_WORD_NUMBER.finditer(text))
    values = []
    i = 0
    while i < len(words):
        total = current = 0
        previous = None
        last_scale = float('inf')
        has_scale = False
        j = i
        while j < len(words):
            word = words[j].group().lower()
            if j > i and not re.fullmatch(r'[\s-]+', text[words[j-1].end():words[j].start()]):
                break
            if word in _ONES:
                n = _ONES[word]
                if previous in ('unit', 'teen') or (previous == 'tens' and n >= 10):
                    break
                current += n
                previous = 'unit' if n < 10 else 'teen'
            elif word in _TENS:
                if previous in ('unit', 'teen', 'tens'):
                    break
                current += _TENS[word]
                previous = 'tens'
            elif word == 'hundred':
                if previous != 'unit' or not 1 <= current <= 9:
                    break
                current *= 100
                previous = 'hundred'
                has_scale = True
            elif word in _SCALES:
                scale = _SCALES[word]
                if not current or scale >= last_scale or previous == 'and':
                    break
                total += current * scale
                current = 0
                last_scale = scale
                previous = 'scale'
                has_scale = True
            else:  # 'and' joins a hundreds or scales expression only.
                if previous not in ('hundred', 'scale') or j + 1 >= len(words):
                    break
                if not re.fullmatch(r'[\s-]+', text[words[j].end():words[j+1].start()]):
                    break
                previous = 'and'
            j += 1
        if has_scale and previous != 'and':
            prefix = text[:words[i].start()]
            sign = -1 if re.search(r'(?:\bminus\s+|\bnegative\s+|[-−])$', prefix, re.I) else 1
            values.append(sign * (total + current))
        i = max(j, i + 1)
    return values


def _visible_quantities(text: str):
    text = _TOKEN.sub(lambda m: ' ' * len(m.group()), text)
    matches = list(_VISIBLE_QUANTITY.finditer(text))
    values = []
    for match in matches:
        raw = match.group().replace(',', '').replace('−', '-')
        suffix = re.search(r'(?:십만|백만|천만|십|백|천|만|억|조)$', raw)
        scale = _KOREAN_SCALE_VALUES[suffix.group()] if suffix else 1
        if suffix:
            raw = raw[:suffix.start()]
        try:
            values.append(Decimal(raw) * scale)
        except InvalidOperation:
            return []
    return values


def quantity_mismatch(original: str, value: str) -> bool:
    """Flag one clearly comparable changed quantity; never reject a translation.

    This deliberately avoids guessing alignments in text with several numbers,
    ordinals, or quantities expressed solely in Korean words.
    """
    old = _visible_quantities(original)
    new = _visible_quantities(value)
    if len(new) != 1:
        return False
    if not old:
        # English cardinal parsing is used only for an explicit scale phrase,
        # not isolated words such as "one" which often serve other functions.
        cleaned = _TOKEN.sub(' ', original)
        if not re.search(r'\b(?:hundred|thousand|million|billion)\b', cleaned, re.I):
            return False
        old = _english_scaled_quantities(cleaned)
    return len(old) == 1 and old[0] != new[0]


def restore_outer_parentheses(original: str, value: str) -> str:
    """Restore one unambiguous missing pair enclosing the entire source."""
    pairs = {'(': ')', '（': '）'}
    if not original or not value or original[0] not in pairs:
        return value
    opening = original[0]
    closing = pairs[opening]
    if original[-1] != closing or any(c in value for c in '()（）'):
        return value
    # Only a single outer pair is eligible; avoid guessing around nested or
    # separate parenthetical clauses, including mixed-width punctuation.
    if any(c in original[1:-1] for c in '()（）'):
        return value
    return opening + value + closing


def protected_tokens(text: str, *, include_numbers: bool = True):
    """Return controls; include legacy numeric markers when restoring old replies."""
    tokens = list(_TOKEN.finditer(text))
    for match in _NUMBER.finditer(text) if include_numbers else ():
        if not any(match.start() < token.end() and token.start() < match.end() for token in tokens):
            tokens.append(match)
    return sorted(tokens, key=lambda match: match.start())


def _parts(value: str):
    tokens = _TOKEN.findall(value)
    return (Counter(x for x in tokens if not _TAG.fullmatch(x)),
            tuple(x for x in tokens if _TAG.fullmatch(x)),
            tuple(_BRACKET.findall(value)),
            tuple(value.count(c) for c in '{}<>='))


def _parse(text: str, mode: str, *, numeric_starts=()) -> list[Span]:
    spans = []
    offset = 0
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        stripped = body.strip()
        if stripped and not stripped.startswith('#'):
            m = _KV.fullmatch(body) if mode == 'key_value' else _COMMAND.fullmatch(body)
            category = 'label'
            if mode == 'command':
                if m:
                    prefix = m.group('prefix').lstrip()
                    category = 'choice' if prefix.startswith('>') else ('name' if prefix.startswith('name ') else 'dialogue')
                else:
                    m = _GIFT.fullmatch(body)
                    category = 'name'
            if m:
                start, end = m.span('value')
                if mode == 'command' and category == 'choice':
                    suffix = _SUFFIX.search(body[start:end])
                    if suffix:
                        end = start + suffix.start()
                # Preserve outer whitespace, including trailing spaces before separators.
                while start < end and body[start].isspace(): start += 1
                while end > start and body[end - 1].isspace(): end -= 1
                if start < end and (offset + start in numeric_starts or
                                    not re.fullmatch(r'[+-]?\d+(?:[.,]\d+)?', body[start:end])):
                    spans.append(Span(offset + start, offset + end, category))
        offset += len(line)
    return spans


def _skeleton(text: str, spans: list[Span]) -> str:
    result = []
    pos = 0
    for span in spans:
        result.extend((text[pos:span.start], '\0'))
        pos = span.end
    result.append(text[pos:])
    return ''.join(result)


def _pipe_pairs(text: str, reference: str):
    """Identify a corroborated group of changed, sentence-like table values.

    Single-token reference values remain protected as possible settings.
    At least three parallel entries are required to establish table context.
    """
    def lines_with_offsets(data):
        out, offset = [], 0
        for line in data.splitlines(keepends=True):
            body = line.rstrip('\r\n')
            if body.strip() and not body.lstrip().startswith('#'):
                out.append((offset, body))
            offset += len(line)
        return out
    left, right = lines_with_offsets(text), lines_with_offsets(reference)
    if len(left) != len(right):
        return [], []
    pattern = re.compile(r'^(?P<prefix>\s*[A-Za-z_][\w.\-]*\s*\|\s*)(?P<value>[^|\r\n]+)$')
    pairs = []
    for (lo, a), (ro, b) in zip(left, right):
        lm, rm = pattern.fullmatch(a), pattern.fullmatch(b)
        if not lm or not rm or lm.group('prefix') != rm.group('prefix'):
            continue
        lv, rv = lm.group('value'), rm.group('value')
        # A two-word source sentence is stronger evidence than a setting,
        # identifier, or number. Keep outer whitespace out of editable spans.
        sentence = r'[^\W\d_]+\s+[^\W\d_]+'
        if (lv == rv or not any(re.search(sentence, value) for value in (lv, rv))
                or not all(re.search(r'[^\W\d_]', value) for value in (lv, rv))):
            continue
        ls, le = lm.span('value'); rs, re_ = rm.span('value')
        while le > ls and a[le - 1].isspace(): le -= 1
        while re_ > rs and b[re_ - 1].isspace(): re_ -= 1
        if ls < le and rs < re_:
            pairs.append((Span(lo + ls, lo + le, 'label'), Span(ro + rs, ro + re_, 'label')))
    if len(pairs) < 3:
        return [], []
    return [p[0] for p in pairs], [p[1] for p in pairs]


def _reference_skeleton(text: str, spans: list[Span]) -> tuple[str, ...]:
    return tuple(line for line in _skeleton(text, spans).splitlines()
                 if line.strip() and not line.lstrip().startswith('#'))


def _json_spans(text: str):
    # Explicitly selected flat string map only; escaped string contents are
    # skipped because raw spans cannot safely stand in for decoded JSON text.
    try:
        pairs = json.loads(text, object_pairs_hook=lambda pairs: pairs)
    except (ValueError, TypeError):
        return [], None
    if (not isinstance(pairs, list) or len(pairs) < 2 or
            any(not isinstance(p, tuple) or len(p) != 2 or
                not isinstance(p[0], str) or not isinstance(p[1], str) or
                not p[0] or not p[1] for p in pairs) or
            len({p[0] for p in pairs}) != len(pairs)):
        return [], None
    tokens = list(re.finditer(r'"(?:[^"\\\x00-\x1f]|\\.)*"', text))
    if len(tokens) != len(pairs) * 2:
        return [], None
    spans = []
    for i, (_, value) in enumerate(pairs):
        token = tokens[i * 2 + 1]
        raw = token.group()[1:-1]
        if raw != value or not raw.strip() or raw != raw.strip():
            return [], None
        spans.append(Span(token.start() + 1, token.end() - 1, 'label'))
    return spans, tuple(p[0] for p in pairs)


def _csv_spans(text: str):
    # Deliberately limited to simple unquoted, explicitly named three-column
    # tables. Quoting, embedded separators, and other schemas are ambiguous.
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip('\r\n') != 'key,source,translation':
        return [], None
    spans, keys, offset = [], [], len(lines[0])
    for line in lines[1:]:
        body = line.rstrip('\r\n')
        cells = body.split(',')
        if (len(cells) != 3 or any(not c or c != c.strip() or '"' in c for c in cells)
                or cells[0] in keys):
            return [], None
        keys.append(cells[0])
        start = offset + len(cells[0]) + len(cells[1]) + 2
        spans.append(Span(start, start + len(cells[2]), 'label'))
        offset += len(line)
    return spans, tuple(keys) if keys else None


def _kv_keys(text: str):
    keys = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        match = _KV.fullmatch(line)
        if not match:
            return None
        key = match.group('key').strip()
        if not key or key in keys:
            return None
        keys.append(key)
    return keys


def extract(text: str, *, reference: str | None = None, kind: str | None = None) -> list[Span]:
    """Return editable spans; unknown and structurally mismatched input yields [].

    A command file requires a reference with exactly matching non-text structure.
    The optional reference also provides that check for key/value files.
    """
    if kind not in (None, 'key_value', 'command', 'json', 'csv'):
        raise ValueError('unsupported text format')
    if kind is None:
        lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')]
        if len(lines) < 2 or not all(_KV.fullmatch(line) for line in lines):
            return []
        kind = 'key_value'
    if kind in ('command', 'json', 'csv') and reference is None:
        return []
    if kind in ('json', 'csv'):
        parser = _json_spans if kind == 'json' else _csv_spans
        spans, keys = parser(text)
        ref, ref_keys = parser(reference)
        if keys is None or keys != ref_keys:
            return []
        if kind == 'csv' and _reference_skeleton(text, spans) != _reference_skeleton(reference, ref):
            return []
        return spans
    spans = _parse(text, kind)
    if kind == 'key_value' and reference is not None:
        keys, ref_keys = _kv_keys(text), _kv_keys(reference)
        if keys is None or ref_keys is None:
            return []
        ref_set = set(ref_keys)
        # Map each extracted value back to the key on its physical line.
        accepted = []
        for span in spans:
            start = text.rfind('\n', 0, span.start) + 1
            match = _KV.fullmatch(text[start:text.find('\n', span.start) if '\n' in text[span.start:] else len(text)].rstrip('\r'))
            if match and match.group('key').strip() in ref_set:
                accepted.append(span)
        return accepted
    if reference is not None:
        ref = _parse(reference, kind)
        if kind == 'command':
            extra, ref_extra = _pipe_pairs(text, reference)
            spans = sorted(spans + extra, key=lambda span: span.start)
            ref = sorted(ref + ref_extra, key=lambda span: span.start)
        if ([s.kind for s in spans] != [s.kind for s in ref]
                or _reference_skeleton(text, spans) != _reference_skeleton(reference, ref)):
            return []
    return spans


def validate_replacement(original: str, value: str, *, kind: str | None = None, manual: bool = False) -> bool:
    """Check a proposed value before round-trip validation of the document."""
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value.splitlines()) != 1 or '\0' in value
            or (kind == 'json' and any(c in value for c in '\\"'))
            or (kind == 'csv' and any(c in value for c in ',\"'))):
        return False
    if kind in (None, 'key_value'):
        before, after = original.split('|'), value.split('|')
        if len(before) != len(after) or any(bool(a.strip()) != bool(b.strip()) for a, b in zip(before, after)):
            return False
    else:
        if '|' in value:
            return False
        before, after = [original], [value]
    keep_parentheses = not manual or kind not in (None, 'key_value', 'json', 'csv')
    for a, b in zip(before, after):
        old, new = _parts(a), _parts(b)
        if (old[:2] != new[:2] or old[3] != new[3] or a.count('\\') != b.count('\\')
                or keep_parentheses and old[2] != new[2]):
            return False
    return True


def replace(text: str, spans: list[Span], replacements: dict[int, str], *, reference: str | None = None, kind: str | None = None, manual_indices=(), rejected=None) -> str:
    """Replace indexed spans only if the document and protected tokens survive."""
    original = extract(text, reference=reference, kind=kind)
    if not original or list(spans) != original:
        raise ValueError('spans do not match verified document')
    if any(not isinstance(i, int) or isinstance(i, bool) or i < 0 or i >= len(spans) for i in replacements):
        raise ValueError('invalid span index')
    out = []
    pos = 0
    offset_delta = 0
    rewritten = []
    for i, span in enumerate(spans):
        old = text[span.start:span.end]
        value = replacements.get(i, old)
        if value != old and not validate_replacement(old, value, kind=kind, manual=i in manual_indices):
            if rejected is None:
                raise ValueError('replacement changes protected structure')
            rejected.append(i)
            value = old
        out.extend((text[pos:span.start], value))
        if kind == 'command':
            start = span.start + offset_delta
            rewritten.append(Span(start, start + len(value), span.kind))
            offset_delta += len(value) - len(old)
        pos = span.end
    out.append(text[pos:])
    result = ''.join(out)
    if kind in (None, 'key_value'):
        # Numeric-only source values are excluded from the catalog. An edited
        # display value can still consist entirely of a number.
        keys = _kv_keys(text)
        if keys is not None and _kv_keys(result) == keys:
            return result
    if kind == 'command':
        # The original extraction already verified the parallel pipe labels
        # against their reference. Replacements cannot introduce a pipe or a
        # line break. An untranslated reference match or a shorter label in the
        # result therefore does not invalidate that verified field.
        commands = set(_parse(text, kind))
        expected = [new for old, new in zip(spans, rewritten) if old in commands]
        if _parse(result, kind, numeric_starts={span.start for span in expected}) != expected:
            raise ValueError('replacement changes document structure')
        return result
    new_spans = extract(result, reference=reference, kind=kind)
    if (len(new_spans) != len(spans) or [s.kind for s in new_spans] != [s.kind for s in spans]
            or _skeleton(text, spans) != _skeleton(result, new_spans)):
        raise ValueError('replacement changes document structure')
    return result
