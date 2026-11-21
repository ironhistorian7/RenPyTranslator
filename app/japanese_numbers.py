"""Conservative conversion of standalone Japanese cardinal-number utterances.

This deliberately does not interpret units, dates, variables, or prose.
"""
import re
import unicodedata

_DIGITS = {'零': 0, '〇': 0, '一': 1, '二': 2, '三': 3, '四': 4,
           '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
_KANA = {'ろっぴゃく': '六百', 'はっぴゃく': '八百',
         'いっせん': '一千', 'はっせん': '八千',
         'れい': '零', 'ぜろ': '零', 'いち': '一', 'に': '二',
         'さん': '三', 'よん': '四', 'し': '四', 'ご': '五',
         'ろく': '六', 'なな': '七', 'しち': '七', 'はち': '八',
         'きゅう': '九', 'く': '九', 'じゅう': '十', 'ひゃく': '百',
         'びゃく': '百', 'ぴゃく': '百', 'せん': '千', 'ぜん': '千'}
_KANA_RE = re.compile('|'.join(sorted(_KANA, key=len, reverse=True)))
_NUMBER = re.compile(r'[0-9０-９零〇一二三四五六七八九十百千ぁ-ゖァ-ヺｦ-ﾟ]+')
_SEPARATORS = frozenset(' \t\r\n、，,。.!！?？「」『』()（）[]［］【】〈〉《》“”‘’"\'')


def _value(token):
    if token.isascii() and token.isdigit():
        # Leading zeroes can be identifiers, not cardinal numbers.
        return int(token) if token == '0' or not token.startswith('0') else None
    if any(c.isascii() and c.isdigit() for c in token):
        return None
    token = ''.join(chr(ord(c) - 0x60) if 'ァ' <= c <= 'ヶ' else c for c in token)
    # A final small tsu is an utterance marker; elsewhere it is ambiguous.
    token = token.removesuffix('っ')
    token = _KANA_RE.sub(lambda m: _KANA[m.group()], token)
    if not token or any(c not in _DIGITS and c not in '十百千' for c in token):
        return None
    if len(token) == 1 and token in _DIGITS:
        return _DIGITS[token]
    total, pending, last_unit = 0, None, 10000
    for c in token:
        if c in _DIGITS:
            if pending is not None or _DIGITS[c] == 0:
                return None
            pending = _DIGITS[c]
        else:
            unit = {'十': 10, '百': 100, '千': 1000}[c]
            if unit >= last_unit:
                return None
            total += (pending if pending is not None else 1) * unit
            pending, last_unit = None, unit
    return total + (pending or 0)


def _parse(text, require_japanese=False, arabic_only=False):
    if re.search(r'\d[.．]\d', text):
        return None
    values, output, end, has_japanese = [], [], 0, False
    for match in _NUMBER.finditer(text):
        gap = text[end:match.start()]
        if any(c not in _SEPARATORS and unicodedata.normalize('NFKC', c) not in _SEPARATORS for c in gap):
            return None
        token = unicodedata.normalize('NFKC', match.group())
        japanese = not (token.isascii() and token.isdigit())
        if arabic_only and japanese:
            return None
        value = _value(token)
        if value is None:
            return None
        values.append(value)
        output.extend((gap, str(value)))
        has_japanese |= japanese
        end = match.end()
    tail = text[end:]
    if not values or any(c not in _SEPARATORS and unicodedata.normalize('NFKC', c) not in _SEPARATORS for c in tail):
        return None
    if require_japanese and not has_japanese:
        return None
    # A lone kana syllable can be a particle, suffix or interjection. A unit,
    # kanji numeral or an explicit sequence supplies the numeric evidence.
    if (require_japanese and len(values) == 1 and values[0] < 10 and
            not any(c in _DIGITS or c in '十百千' for c in text)):
        return None
    output.append(tail)
    return values, ''.join(output)


def normalize_source(text):
    """Return a safe Arabic-number candidate, or None when not unambiguous."""
    parsed = _parse(text, require_japanese=True)
    return parsed[1] if parsed else None


def compare(source, target):
    """Return match/mismatch only for supported source and Arabic target."""
    left = _parse(source, require_japanese=True)
    right = _parse(target, arabic_only=True)
    if left is None or right is None:
        return None
    return 'match' if left[0] == right[0] else 'mismatch'
