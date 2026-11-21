"""Extract conservative KAG scenario text with exact character spans."""
import codecs
import re
from kirikiri_tokens import _bracket_end, protect, readable


def _decode(raw):
    for bom, encoding in ((codecs.BOM_UTF8, 'utf-8'), (codecs.BOM_UTF16_LE, 'utf-16-le'), (codecs.BOM_UTF16_BE, 'utf-16-be')):
        if raw.startswith(bom): return raw[len(bom):].decode(encoding), encoding, bom
    if b'\x00' in raw:
        raise UnicodeDecodeError('kirikiri', raw, 0, len(raw), 'UTF-16 requires a byte-order mark')
    for encoding in (('cp932', 'utf-8') if raw.isascii() else ('utf-8', 'cp932')):
        try: return raw.decode(encoding), encoding, b''
        except UnicodeDecodeError: pass
    raise UnicodeDecodeError('kirikiri', raw, 0, len(raw), 'unsupported or malformed script encoding')


# Only native in-line formatting/timing commands; unknown commands remain boundaries.
_INLINE = frozenset(('font', 'resetfont', 'ruby', 'r', 'wait'))

def _caption_spans(command, base):
    head = re.match(r'\[\s*(link|button)\b', command, re.I)
    if not head: return
    i = head.end(); candidates = []
    while i < len(command) - 1:
        if not command[i:-1].strip(): break
        match = re.match(r'\s+([A-Za-z_][\w-]*)(?:\s*=\s*)?', command[i:])
        if not match: return
        key = match.group(1).lower(); has_value = '=' in match.group(); i += match.end()
        if not has_value: continue
        if i >= len(command) - 1: return
        if command[i] not in "\"'":
            value = re.match(r'[^\s\]]+', command[i:])
            if not value: return
            i += value.end(); continue
        quote = command[i]; i += 1; start = i
        while i < len(command) and command[i] != quote:
            if command[i] in '\\`': return
            i += 1
        if i >= len(command): return
        value = command[start:i]; i += 1
        if key in ('text', 'caption') and value and not any(c in value for c in '&%[]\r\n'):
            candidates.append((base + start, base + i - 1))
    if len(candidates) == 1: yield candidates[0]


def extract_text(rawbytes, path=''):
    text, _, _ = _decode(rawbytes)
    rows = []
    def add(start, end, usage):
        visible = readable(text[start:end]) if usage == 'dialogue' else text[start:end]
        if start < end and visible.strip():
            rows.append({'source': text[start:end], 'usage': usage, 'file': str(path),
                         'location': {'start': start, 'end': end}, 'dialect': 'kag'})
    in_script = False
    offset = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip('\r\n')
        stripped = content.lstrip()
        if in_script:
            if re.search(r'\[\s*endscript\s*\]|^\s*@endscript\b', content, re.I): in_script = False
            offset += len(line); continue
        if not stripped or stripped.startswith((';', '*', '@')):
            if re.match(r'@iscript\b', stripped, re.I): in_script = True
            elif stripped.startswith('@'):
                synthetic = '[' + stripped[1:] + ']'
                for a, b in _caption_spans(synthetic, offset + len(content) - len(stripped)): add(a, b, 'choice')
            offset += len(line); continue
        if stripped.startswith('#'):
            start = len(content) - len(stripped) + 1
            add(offset + start, offset + len(content), 'speaker_label')
            offset += len(line); continue
        i = 0; segment = 0
        while i < len(content):
            if content[i:i+2] == '[[':
                i += 2; continue
            if content[i] != '[':
                i += 1; continue
            end = _bracket_end(content, i)
            if end is None:
                # Do not translate an unterminated command or its remainder.
                add(offset + segment, offset + i, 'dialogue'); segment = len(content); break
            cmd = content[i:end]
            name = re.match(r'\[\s*([A-Za-z_][\w-]*)\b', cmd)
            if name and name.group(1).lower() in _INLINE:
                i = end; continue
            add(offset + segment, offset + i, 'dialogue')
            for a, b in _caption_spans(cmd, offset + i): add(a, b, 'choice')
            if re.match(r'\[\s*iscript\b', cmd, re.I):
                in_script = True
                if re.search(r'\[\s*endscript\s*\]', content[end:], re.I): in_script = False
                segment = len(content); break
            i = end; segment = end
        add(offset + segment, offset + len(content), 'dialogue')
        offset += len(line)
    return rows


def render_text(rawbytes, replacements):
    text, encoding, bom = _decode(rawbytes)
    allowed = {(r['location']['start'], r['location']['end']): r for r in extract_text(rawbytes)}
    edits = []
    for item in replacements:
        if isinstance(item, dict):
            location = item.get('location', item)
            start, end = location['start'], location['end']
            new = item.get('translation', item.get('target'))
            expected = item.get('source')
            reference = item.get('show_reference', False)
            if not isinstance(reference, bool): raise ValueError('Invalid reference flag')
        else:
            start, end, new = item; expected = None; reference = False
        if not isinstance(start, int) or not isinstance(end, int) or not (0 <= start <= end <= len(text)) or not isinstance(new, str):
            raise ValueError('Invalid KAG replacement')
        if (start, end) not in allowed: raise ValueError('Replacement is not an extracted text span')
        if allowed[(start, end)]['usage'] == 'choice' and any(c in new for c in '\"\''):
            raise ValueError('Replacement introduces a quote in an attribute')
        line_start = max(text.rfind('\n', 0, start), text.rfind('\r', 0, start)) + 1
        if not text[line_start:start].strip() and new.lstrip().startswith(('@', '*', ';', '#')):
            raise ValueError('Replacement introduces a KAG directive')
        if expected is not None and text[start:end] != expected: raise ValueError('Stale KAG replacement')
        if any(c in new for c in '\r\n'): raise ValueError('Replacement introduces a line break')
        _, original_controls = protect(text[start:end])
        protected_new, new_controls = protect(new)
        if original_controls != new_controls or '[' in protected_new:
            raise ValueError('KAG control code mismatch')
        if reference:
            if allowed[(start, end)]['usage'] != 'dialogue': raise ValueError('Reference is only supported for dialogue')
            from kirikiri_runtime import compose_reference
            new = compose_reference(new, text[start:end], 'kag')
        edits.append((start, end, new))
    edits.sort()
    if any(a[1] > b[0] for a, b in zip(edits, edits[1:])): raise ValueError('Overlapping KAG replacements')
    if not edits: return rawbytes
    for start, end, new in reversed(edits): text = text[:start] + new + text[end:]
    try: return bom + text.encode(encoding)
    except UnicodeEncodeError:
        if encoding != 'cp932': raise
        return codecs.BOM_UTF16_LE + text.encode('utf-16-le')
