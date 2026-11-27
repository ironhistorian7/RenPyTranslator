"""Narrow KAGEX bitmap-font Hangul fallback patch, with fail-closed profiles.

The caller supplies bitmap storage or direct-face names after checking the TFT cmap. This
module neither opens game files nor changes saved font preferences.
"""
import re
import struct


class FontLayoutUnsupported(ValueError):
    pass


def tft_hangul_coverage(data):
    """Return the number of Hangul codepoints in a validated TVP TFT index."""
    if not isinstance(data, bytes) or len(data) < 36 or data[:22] != b'TVP pre-rendered font\x1a' or data[22] not in (0, 1) or data[23] != 2:
        raise FontLayoutUnsupported('Unsupported TFT header')
    count, chars, records = struct.unpack_from('<III', data, 24)
    if count == 0 or chars + count * 2 > len(data) or records + count * 20 > len(data):
        raise FontLayoutUnsupported('Invalid TFT index')
    codes = struct.unpack_from('<%dH' % count, data, chars)
    if any(a >= b for a, b in zip(codes, codes[1:])):
        raise FontLayoutUnsupported('Invalid TFT ordering')
    return sum(0xAC00 <= c <= 0xD7A3 or 0x1100 <= c <= 0x11FF or 0x3130 <= c <= 0x318F for c in codes)


def _tjs_string(value):
    if not isinstance(value, str) or not value or any(ord(c) > 0xffff or ord(c) == 0 for c in value):
        raise FontLayoutUnsupported('Invalid font face')
    return '"' + ''.join('\\x%04x' % ord(c) for c in value) + '"'


def _mask_comments(source):
    """Blank comments while retaining offsets and quoted strings."""
    out = list(source)
    i, quote = 0, None
    while i < len(source):
        c = source[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in ("\"", "'"):
            quote = c
            i += 1
            continue
        if source.startswith('//', i):
            end = source.find('\n', i)
            if end < 0:
                end = len(source)
            for j in range(i, end): out[j] = ' '
            i = end
            continue
        if source.startswith('/*', i):
            end = source.find('*/', i + 2)
            if end < 0:
                raise FontLayoutUnsupported('Unterminated source comment')
            end += 2
            for j in range(i, end):
                if source[j] not in '\r\n': out[j] = ' '
            i = end
            continue
        i += 1
    return ''.join(out)


def _unique_replace(source, pattern, replacement):
    matcher = re.compile(pattern, re.MULTILINE | re.DOTALL)
    found = list(matcher.finditer(_mask_comments(source)))
    if len(found) != 1:
        raise FontLayoutUnsupported('MessageLayer profile not uniquely recognized')
    match = matcher.match(source, found[0].start())
    if match is None or match.end() != found[0].end():
        raise FontLayoutUnsupported('Unsupported comments in matched source')
    change = replacement(match) if callable(replacement) else replacement
    return source[:match.start()] + change + source[match.end():]


def patch_message_layer(original, prerender_source=None, unsupported_storages=(), unsupported_direct_faces=()):
    """Return patched source bytes; reject unrecognized engine profiles.

    unsupported_storages must contain only TFT basenames whose indices were
    checked and found to contain no Hangul. Direct faces require equivalent
    static evidence. The legacy English alias is handled independently. Source
    is preserved byte-for-byte
    outside the small ASCII insertion sites.
    """
    if not isinstance(original, bytes) or (prerender_source is not None and not isinstance(prerender_source, bytes)):
        raise FontLayoutUnsupported('Expected original source bytes')
    if unsupported_storages and not prerender_source:
        raise FontLayoutUnsupported('Bitmap font source is required')
    # Verify the API that maps virtual face to a bitmap rather than assuming it.
    if unsupported_storages:
        profile = _mask_comments(prerender_source.decode('latin1'))
        checks = (
            r'function\s+fontFilterFace\s*\(prop,\s*value\)\s*\{[^{}]*curInfo\s*=\s*value\s*!=\s*""\s*\?\s*fontInfo\[value\]\s*:\s*void',
            r'function\s+fontChanged\s*\(prop,\s*value\)\s*\{[^{}]*this\[prop\]\s*=\s*value',
            r'function\s+getCurrentTag\s*\(\)\s*\{[^{}]*curSizes\s*=\s*curInfo\[num\]',
            r'function\s+searchNearSize\s*\(size\)',
            r'propertyHook\s*\(\s*"face"\s*,\s*this\s*,\s*"fontFilterFace"\s*\)',
            r'propertyHook\s*\(\s*"height"\s*,\s*this\s*,\s*"fontChanged"\s*\)',
        )
        if any(not re.search(pattern, profile, re.DOTALL) for pattern in checks):
            raise FontLayoutUnsupported('Unsupported prerender font profile')
    storages = sorted(set(f.casefold() for f in unsupported_storages))
    if any('/' in f or '\\' in f for f in storages):
        raise FontLayoutUnsupported('TFT names must be unique basenames')
    cond = ' || '.join('storage == ' + _tjs_string(f) for f in storages) or 'false'
    direct = sorted(set(unsupported_direct_faces) | {'Malgun Gothic'})
    fallback = _tjs_string('맑은 고딕,Malgun Gothic')
    face_cond = ' || '.join('face == ' + _tjs_string(f) for f in direct)
    helper = r'''
    function __rptNeedsHangulFont(lay, text) {
        var hangul = false;
        for (var i = 0; i < text.length; i++) {
            var c = text.charAt(i);
            if ((c >= "\xAC00" && c <= "\xD7A3") ||
                (c >= "\x1100" && c <= "\x11FF") ||
                (c >= "\x3130" && c <= "\x318F")) { hangul = true; break; }
        }
        if (!hangul) return false;
        var face = lay.font.face;
        if (DIRECT_CONDITION) return true;
        if (typeof global.PreRenderFontEx == "undefined") return false;
        if (typeof lay._preRenderFontEx != "Object") return false;
        var ex = lay._preRenderFontEx;
        var info = ex.curInfo;
        if (info === void || info.ttf) return false;
        var tag = ex.getCurrentTag();
        if (tag === void || ex.curSizes === void || !ex.curSizes.count) return false;
        var size = ex.height;
        if (size < 0) size = -size;
        var pfont = info[tag + size];
        if (pfont === void) pfont = info[tag + ex.searchNearSize(size)];
        if (pfont === void || pfont.storage === void) return false;
        var storage = Storages.extractStorageName(pfont.storage).toLowerCase();
        return (FACE_CONDITION);
    }
'''.replace('FACE_CONDITION', cond).replace('DIRECT_CONDITION', face_cond).encode('ascii')
    # Restrict edits to class/function anchors and unambiguous code fragments.
    try:
        source = original.decode('latin1')
        text_helper = helper.decode('ascii')
        for marker in ('class NameLayer extends RelativeLayer', 'class MessageLayer extends AnimKAGLayerTransMove'):
            pat = r'(?m)^' + re.escape(marker) + r'\s*\{'
            source = _unique_replace(source, pat, lambda m: m.group(0) + text_helper)
        pat = r'(\bfunction\s+processNameText\s*\(text\)\s*\{\s*if\s*\(text\s*!=\s*""\)\s*\{)(.*?)(\bvisible\s*=\s*true\s*;)(\s*\}\s*else\s*\{)'
        def name_edit(m):
            body=m.group(2)+m.group(3)
            if not all(x in body for x in ('font.getTextWidth(text)', 'draw(x, y, text)')) or re.search(r'\breturn\b', body):
                raise FontLayoutUnsupported('Unrecognized name rendering')
            return m.group(1) + '\n            var __rptFace = font.face;\n            var __rptChange = __rptNeedsHangulFont(this, text);\n            if (__rptChange) font.face = ' + fallback + ';\n            try {' + body + '\n            } catch (__rptError) {\n                if (__rptChange) font.face = __rptFace;\n                throw __rptError;\n            }\n            if (__rptChange) font.face = __rptFace;' + m.group(4)
        source = _unique_replace(source, pat, name_edit)
        pat = r'(\bvar\s+llfont\s*=\s*ll\.font\s*;)(.*?)(\badjustAlign\s*\(\)\s*if\s*\(align\s*>=\s*0\)\s*;)'
        def body_edit(m):
            body=m.group(2)+m.group(3)
            if not all(x in body for x in ('llfont.getTextWidth(ch)', 'drawTextToLayer(ll, dx, dy, ch, chColor)', 'cw += pitch')) or re.search(r'\breturn\b',body):
                raise FontLayoutUnsupported('Unrecognized body rendering')
            return m.group(1) + '\n        var __rptFace = llfont.face;\n        var __rptChange = __rptNeedsHangulFont(ll, ch);\n        if (__rptChange) llfont.face = ' + fallback + ';\n        try {' + body + '\n        } catch (__rptError) {\n            if (__rptChange) llfont.face = __rptFace;\n            throw __rptError;\n        }\n        if (__rptChange) llfont.face = __rptFace;'
        source = _unique_replace(source, pat, body_edit)
        return source.encode('latin1')
    except UnicodeEncodeError as e:
        raise FontLayoutUnsupported('Unsupported source encoding') from e


def _ui_helper(unsupported_storages, unsupported_direct_faces):
    """Same conservative bitmap/face detection as MessageLayer, for UI layers."""
    storages=sorted(set(x.casefold() for x in unsupported_storages))
    if any('/' in x or '\\' in x for x in storages):
        raise FontLayoutUnsupported('TFT names must be unique basenames')
    cond=' || '.join('storage == '+_tjs_string(x) for x in storages) or 'false'
    faces=sorted(set(unsupported_direct_faces)|{'Malgun Gothic'})
    face_cond=' || '.join('face == '+_tjs_string(x) for x in faces)
    return r'''
function __rptUiNeedsHangulFont(lay, text) {
    if (typeof text != "String") return false;
    var hangul = false;
    for (var i = 0; i < text.length; i++) {
        var c = text.charAt(i);
        if ((c >= "\xAC00" && c <= "\xD7A3") ||
            (c >= "\x1100" && c <= "\x11FF") ||
            (c >= "\x3130" && c <= "\x318F")) { hangul = true; break; }
    }
    if (!hangul) return false;
    var face = lay.font.face;
    if (DIRECT_CONDITION) return true;
    if (typeof global.PreRenderFontEx == "undefined") return false;
    if (typeof lay._preRenderFontEx != "Object") return false;
    var ex = lay._preRenderFontEx, info = ex.curInfo;
    if (info === void || info.ttf) return false;
    var tag = ex.getCurrentTag();
    if (tag === void || ex.curSizes === void || !ex.curSizes.count) return false;
    var size = ex.height;
    if (size < 0) size = -size;
    var pfont = info[tag + size];
    if (pfont === void) pfont = info[tag + ex.searchNearSize(size)];
    if (pfont === void || pfont.storage === void) return false;
    var storage = Storages.extractStorageName(pfont.storage).toLowerCase();
    return (FACE_CONDITION);
}
'''.replace('DIRECT_CONDITION',face_cond).replace('FACE_CONDITION',cond)


def patch_ui_font(original, kind, unsupported_storages=(), unsupported_direct_faces=()):
    """Patch an identified TextRender or Kinsoku profile; no arbitrary scripts."""
    if not isinstance(original,bytes):raise FontLayoutUnsupported('Expected original source bytes')
    source=original.decode('latin1')
    helper_name='__rptTextNeedsHangulFont' if kind=='textrender' else '__rptKinsokuNeedsHangulFont'
    helper=_ui_helper(unsupported_storages,unsupported_direct_faces).replace('__rptUiNeedsHangulFont',helper_name)
    fallback=_tjs_string('맑은 고딕,Malgun Gothic')
    if kind=='textrender':
        pattern=r'\bfunction\s+draw\s*\(layer,\s*ox,\s*oy,\s*width,\s*height,\s*text,\s*autoIndent\s*=\s*1,\s*base\s*=\s*void\)\s*\{'
        masked=_mask_comments(source)
        matches=list(re.finditer(pattern,masked,re.S))
        if len(matches)!=1:raise FontLayoutUnsupported('Unsupported TextRender draw profile')
        start=matches[0].start(); next_method=re.search(r'\bfunction\s+drawAll\s*\(',masked[matches[0].end():])
        if not next_method:raise FontLayoutUnsupported('Missing TextRender drawAll')
        section=masked[start:matches[0].end()+next_method.start()]
        if not re.search(r'render\s*\(width,\s*height,\s*layer\.font,\s*text,\s*autoIndent,\s*base\)',section) or not re.search(r'drawAll\s*\(layer,\s*ox,\s*oy\)',section):
            raise FontLayoutUnsupported('Unsupported TextRender draw body')
        for anchor in (r'font\.getTextWidth\s*\(',r'function\s+drawCh\s*\(layer,',r'var\s+dt\s*=\s*layer\.drawText'):
            if not re.search(anchor,masked,re.S):raise FontLayoutUnsupported('Unsupported TextRender profile')
        wrapper=('function draw(layer, ox, oy, width, height, text, autoIndent=1, base=void) {\n'
                 '    var __rptFace = layer.font.face;\n'
                 '    var __rptChange = __rptTextNeedsHangulFont(layer, text);\n'
                 '    if (__rptChange) layer.font.face = '+fallback+';\n'
                 '    var __rptResult;\n    try { __rptResult = __rptOriginalDraw(layer, ox, oy, width, height, text, autoIndent, base); }\n'
                 '    catch (__rptError) { if (__rptChange) layer.font.face = __rptFace; throw __rptError; }\n'
                 '    if (__rptChange) layer.font.face = __rptFace;\n    return __rptResult;\n}\n')
        source=_unique_replace(source,pattern,lambda m:wrapper+m.group(0).replace('function draw','function __rptOriginalDraw',1))
        source=_unique_replace(source,r'(?m)^class\s+TextRender\s*\{',lambda m:helper+'\n'+m.group(0))
    elif kind=='kinsoku':
        pattern=r'\bfunction\s+drawTextKinsokuRect\s*\(layer,\s*text,\s*elm\)\s*\{'
        masked=_mask_comments(source);matches=list(re.finditer(pattern,masked,re.S))
        if len(matches)!=1:raise FontLayoutUnsupported('Unsupported Kinsoku profile')
        start=matches[0].start();next_function=re.search(r'(?m)^function\s+\w+\s*\(',masked[matches[0].end():])
        if not next_function:raise FontLayoutUnsupported('Unsupported Kinsoku boundary')
        end=matches[0].end()+next_function.start();section=source[start:end];masked_section=_mask_comments(section)
        if not all(x in masked_section for x in ('gtw(cur + ch)','dt(layer, dx, y, text','font.face')):
            raise FontLayoutUnsupported('Unsupported Kinsoku body')
        begin=r'\bvar\s+cur\s*=\s*""\s*,\s*lines\s*=\s*\[\]\s*,\s*len\s*=\s*text\.length\s*;'
        pre=('var __rptFace = font.face;\n'
             '    var __rptChange = typeof elm.gettextwidth != "Object" && __rptKinsokuNeedsHangulFont(layer, text);\n'
             '    if (__rptChange) font.face = '+fallback+';\n'
             '    var __rptLines;\n    try {\n')
        section=_unique_replace(section,begin,lambda m:pre+m.group(0))
        close=('__rptLines = lines;\n    } catch (__rptError) {\n'
               '        if (__rptChange) font.face = __rptFace;\n        throw __rptError;\n    }\n'
               '    if (__rptChange) font.face = __rptFace;\n    return __rptLines;')
        section=_unique_replace(section,r'\breturn\s+lines\s*;',close)
        source=source[:start]+helper+'\n'+section+source[end:]
    else:raise ValueError('Unknown UI renderer')
    try:return source.encode('latin1')
    except UnicodeEncodeError as e:raise FontLayoutUnsupported('Unsupported source encoding') from e


def _mask_strings(source):
    masked=list(source);i=0
    while i<len(masked):
        if masked[i] in ('"', "'"):
            quote=masked[i];masked[i]=' ';i+=1
            while i<len(masked):
                c=masked[i];masked[i]=' '
                if c=='\\' and i+1<len(masked):i+=1;masked[i]=' '
                elif c==quote:i+=1;break
                i+=1
        else:i+=1
    return ''.join(masked)


_NAME_HAS_HANGUL = r'''
function __rptSpeakerHasHangul(text) {
    if (typeof text != "String") return false;
    for (var i = 0; i < text.length; i++) {
        var c = text.charAt(i);
        if ((c >= "\xAC00" && c <= "\xD7A3") ||
            (c >= "\x1100" && c <= "\x11FF") ||
            (c >= "\x3130" && c <= "\x318F")) return true;
    }
    return false;
}
'''


def _name_draw_override(forward):
    fallback = _tjs_string('맑은 고딕,Malgun Gothic')
    return (
        f'\n        var __rptNameFace = font.face, __rptNameResult;\n'
        f'        font.face = {fallback};\n'
        f'        try {{ __rptNameResult = {forward}; }}\n'
        '        catch(e) { font.face = __rptNameFace; throw e; }\n'
        '        font.face = __rptNameFace;\n'
        '        return __rptNameResult;\n'
    )


def _speaker_font_changes(source, uncommented, masked, unsupported_direct_faces):
    """Keep a proven name-layer subclass from undoing Hangul font measurement."""
    unsupported = set(unsupported_direct_faces)
    if not unsupported:
        return []
    classes = re.finditer(r'\bclass\s+\w+\s+extends\s+(\w+)\s*\{', masked)
    draw = re.compile(
        r'\bfunction\s+draw\s*(?:\(\s*(?P<x>\w+)\s*,\s*(?P<y>\w+)\s*,\s*(?P<text>\w+)\s*\)\s*)?'
        r'(?P<open>\{)\s*font\.face\s*=\s*(?P<quote>["\'])(?P<face>[^"\'\\\r\n]+)(?P=quote)\s*;\s*'
        r'return\s+super\.draw\s*\(\s*(?P<forward>[^()]*)\)\s*;\s*\}'
    )
    changes = []
    for decl in classes:
        if not decl[1].endswith('NameLayer'):
            continue
        start = decl.end()
        depth = 1
        for brace in re.finditer(r'[{}]', masked[start:]):
            depth += 1 if brace[0] == '{' else -1
            if depth == 0:
                end = start + brace.start()
                break
        else:
            continue
        body = masked[start:end]
        # A subclass that already customizes measurement needs a separate profile.
        if re.search(r'\bfunction\s+processNameText\b', body):
            continue
        methods = list(re.finditer(r'\bfunction\s+draw\b', body))
        if len(methods) != 1:
            continue
        method = start + methods[0].start()
        prefix = masked[start:method]
        if prefix.count('{') != prefix.count('}'):
            continue
        match = draw.match(uncommented, method, end)
        if not match or match['face'] not in unsupported:
            continue
        params = (match['x'], match['y'], match['text']) if match['text'] else ('x', 'y', '__rptSpeakerText')
        x, y, text = params
        forward = match['forward']
        if re.sub(r'\s+', '', forward) not in ('...', ','.join(params)):
            continue
        original_body = source[match.end('open'):match.end()-1]
        replacement = (
            f'function draw({x}, {y}, {text}) {{\n'
            f'        if (!__rptSpeakerHasHangul({text})) {{{original_body}}}'
            + _name_draw_override(f'super.draw({forward})') + '    }\n'
            '    function processNameText(__rptSpeakerText) {\n'
            '        if (!__rptSpeakerHasHangul(__rptSpeakerText)) return super.processNameText(...);'
            + _name_draw_override('super.processNameText(...)') + '    }'
        )
        changes.append((method, match.end(), replacement))
    return changes


def patch_direct_ui_font(original, unsupported_storages=(), unsupported_direct_faces=(), *, speaker_fonts=True):
    """Wrap recognized direct draw and name-layer calls; return unresolved count."""
    if not isinstance(original,bytes):raise FontLayoutUnsupported('Expected original source bytes')
    source=original.decode('latin1');uncommented=_mask_comments(source);masked=_mask_strings(uncommented)
    scope_at={};braces=[]
    for match in re.finditer(r'[{}]',masked):
        pos=match.start()
        if match.group()=='{':
            prefix=masked[max(0,pos-160):pos]
            m=re.search(r'\bwith\s*\(\s*([A-Za-z_][\w]*)\s*\)\s*$',prefix)
            barrier=re.search(r'(?:\bwith\s*\([^()]*\)|\bfunction(?:\s+\w+)?\s*\([^()]*\))\s*$',prefix)
            braces.append(m.group(1) if m else False if barrier else None)
        elif braces:braces.pop()
        current=None
        for value in reversed(braces):
            if value is False:break
            if value:current=value;break
        scope_at[pos]=current
    import bisect
    positions=list(scope_at)
    def scope(pos):
        index=bisect.bisect_right(positions,pos)-1
        return scope_at[positions[index]] if index>=0 else None
    changes=[];unresolved=0
    edge=re.compile(r'\bEdgeShadowDrawText\s*\(\s*\.drawText\s*,\s*([A-Za-z_]\w*)\s*,')
    edge_spans=[]
    for m in edge.finditer(masked):
        target=m.group(1)
        if scope(m.start())==target:
            changes.append((m.start(),m.end(),'__rptEdgeDraw('+target+','));edge_spans.append((m.start(),m.end()))
        else:unresolved+=1
    for m in re.finditer(r'(?<![\w])\.drawText\s*\(',masked):
        if any(a<=m.start()<b for a,b in edge_spans):continue
        target=scope(m.start())
        if target:changes.append((m.start(),m.end(),'__rptDirectDraw('+target+','))
        else:unresolved+=1
    unresolved+=len(list(re.finditer(r'\b[A-Za-z_]\w*\.drawText\s*\(',masked)))
    names=_speaker_font_changes(source,uncommented,masked,unsupported_direct_faces) if speaker_fonts else []
    if not changes and not names:return original,unresolved
    direct_changes=bool(changes)
    for start,end,replacement in reversed(sorted(changes+names)):
        source=source[:start]+replacement+source[end:]
    if not direct_changes:
        return (_NAME_HAS_HANGUL+'\n'+source).encode('latin1'),unresolved
    helper=_ui_helper(unsupported_storages,unsupported_direct_faces).replace('__rptUiNeedsHangulFont','__rptCustomNeedsHangulFont')
    fallback=_tjs_string('맑은 고딕,Malgun Gothic')
    helper+='''
function __rptDirectDraw(lay, x, y, text, *) {
    var face = lay.font.face, change = __rptCustomNeedsHangulFont(lay, text), result;
    if (change) lay.font.face = FALLBACK;
    try { result = lay.drawText(x, y, text, *); }
    catch(e) { if (change) lay.font.face = face; throw e; }
    if (change) lay.font.face = face;
    return result;
}
function __rptEdgeDraw(lay, x, y, text, *) {
    var face = lay.font.face, change = __rptCustomNeedsHangulFont(lay, text), result;
    if (change) lay.font.face = FALLBACK;
    try { result = EdgeShadowDrawText(lay.drawText, lay, x, y, text, *); }
    catch(e) { if (change) lay.font.face = face; throw e; }
    if (change) lay.font.face = face;
    return result;
}
'''.replace('FALLBACK',fallback)
    if names:helper+=_NAME_HAS_HANGUL
    return (helper+'\n'+source).encode('latin1'),unresolved
