"""Conservative, lossless token handling for KiriKiri text."""
import re
from collections import Counter

_MARKER = re.compile(r'<rpt\d{3,}/>')
# KAGEX dynamic expressions and controls are opaque.
_KAGEX = re.compile(r'\\[\s\S]?|\$[^;]*;?|#[^;]*;?|%(?:\d+|d\d*|[\s\S]?)|\[[^\]]*\]?|\r\n|\r|\n')

def _bracket_end(text, start):
    quote = None
    escaped = False
    for i in range(start + 1, len(text)):
        c = text[i]
        if escaped:
            escaped = False
        elif c in ('\\', '`') and quote:
            escaped = True
        elif quote:
            if c == quote: quote = None
        elif c in "\"'": quote = c
        elif c == ']': return i + 1
        elif c in '\r\n': return None
    return None

def _dialect(cfg):
    if isinstance(cfg, str): return cfg
    return (cfg or {}).get('_kirikiri_dialect', (cfg or {}).get('kirikiri_dialect', 'kag'))

def spans(source, dialect='kag'):
    if dialect == 'kirikiri_tjs_ui':
        # UI captions are literal text, not KAG commands. Preserve only actual
        # TJS interpolation fields and physical line breaks in these strings.
        pattern=_MARKER.pattern+r'|\$\{\.?[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\}|\r\n|\r|\n'
        yield from ((m.start(),m.end()) for m in re.finditer(pattern,source))
        return
    if dialect == 'kagex_psb':
        yield from ((m.start(), m.end()) for m in re.compile(_MARKER.pattern + '|' + _KAGEX.pattern).finditer(source))
        return
    i = 0
    while i < len(source):
        marker = _MARKER.match(source, i)
        if marker:
            yield i, marker.end(); i = marker.end(); continue
        if source[i:i+2] == '[[':
            yield i, i + 2; i += 2; continue
        if source[i] == '[':
            end = _bracket_end(source, i)
            if end is not None:
                yield i, end
                i = end
                continue
        if source[i] in '\r\n':
            end = i + (2 if source[i:i+2] == '\r\n' else 1)
            yield i, end
            i = end
            continue
        i += 1

def protect(source, cfg=None):
    dialect = _dialect(cfg)
    tokens, pieces, pos = [], [], 0
    for start, end in spans(source, dialect):
        pieces.extend((source[pos:start], '<rpt%03d/>' % len(tokens)))
        tokens.append(source[start:end]); pos = end
    pieces.append(source[pos:])
    return ''.join(pieces), tokens

def restore(text, tokens, cfg=None):
    matches = _MARKER.findall(text)
    expected = ['<rpt%03d/>' % i for i in range(len(tokens))]
    if Counter(matches) != Counter(expected):
        raise ValueError('KiriKiri token mismatch')
    return _MARKER.sub(lambda m: tokens[int(m.group()[4:-2])], text)

def validate(source, target, cfg=None):
    from kirikiri_ruby import prepare
    source_body, _, _ = prepare(source, cfg)
    _, left = protect(source_body, cfg)
    _, right = protect(target, cfg)
    if _dialect(cfg) == 'kagex_psb':
        literal_escapes = {'\\%', '\\$', '\\#', '\\[', '\\\\'}
        left = [token for token in left if token not in literal_escapes]
        right = [token for token in right if token not in literal_escapes]
    errors = []
    if left != right: errors.append('KiriKiri control code mismatch')
    if source.strip() and not target.strip(): errors.append('empty')
    if Counter(_MARKER.findall(source)) != Counter(_MARKER.findall(target)): errors.append('unrestored placeholder')
    return errors

def readable(source, cfg=None):
    dialect = _dialect(cfg)
    if dialect == 'kagex_psb':
        from kirikiri_runtime import _kagex_plain
        return _kagex_plain(source)
    parts, pos = [], 0
    for start, end in spans(source, dialect):
        parts.append(source[pos:start])
        if source[start:end] == '[[': parts.append('[')
        pos = end
    parts.append(source[pos:])
    return ''.join(parts)
