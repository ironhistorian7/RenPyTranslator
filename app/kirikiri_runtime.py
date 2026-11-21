"""Runtime helpers for KAG and KAGEX.

KAG tags: https://krkrz.github.io/krkr2doc/kag3doc/contents/Tags.html
KAGEX: KAGEnvPlayer.tjs process text loop and MessageLayer.tjs setFont.
"""
from kirikiri_tokens import _bracket_end

CAPABILITIES = {'inline_reference': False, 'runtime_language_toggle': False,
                'private_font_registration': False}


def _kagex_plain(source):
    out = []; i = 0
    while i < len(source):
        c = source[i]; i += 1
        if c == '\\':
            if i >= len(source): break
            c = source[i]; i += 1
            if c not in 'ntirw': out.append(c)
        elif c in '$#':
            end = source.find(';', i)
            i = len(source) if end < 0 else end + 1
        elif c == '%':
            if i < len(source):
                flag = source[i]; i += 1
                if flag.isascii() and flag.isdigit() or flag == 'd':
                    while i < len(source) and source[i].isascii() and source[i].isdigit(): i += 1
        elif c == '[':
            end = source.find(']', i)
            i = len(source) if end < 0 else end + 1
        elif c != '\r': out.append(c)
    return ''.join(out)


def _kagex_escape(text):
    return ''.join(('\\' + c) if c in '\\$#%[' else c for c in text)


def _kag_plain(source):
    out = []; i = 0
    while i < len(source):
        if source[i:i+2] == '[[':
            out.append('['); i += 2
        elif source[i] == '[':
            end = _bracket_end(source, i)
            if end is None: break
            i = end
        else:
            out.append(source[i]); i += 1
    return ''.join(out)


def _kag_escape(text):
    # ch with one literal character is a documented KAG tag.
    return text.replace('[', '[ch text="["]').replace('\r\n', '\n').replace('\r', '\n').replace('\n', '[r]')


def _kagex_complete(text):
    i = 0
    while i < len(text):
        c = text[i]; i += 1
        if c == '\\' or c == '%':
            if i == len(text): return False
            flag = text[i]; i += 1
            if c == '%' and (flag.isascii() and flag.isdigit() or flag == 'd'):
                while i < len(text) and text[i].isascii() and text[i].isdigit(): i += 1
        elif c in '$#[':
            end = text.find(']' if c == '[' else ';', i)
            if end < 0: return False
            i = end + 1
    return True


def compose_reference(translated, source, dialect, show_reference=True, cfg=None):
    if dialect not in ('kag', 'kagex_psb'):
        raise ValueError('Unsupported KiriKiri dialect')
    return translated


def patch_config_font(original):
    """Overlay one verified Config.tjs default; preserve every other source byte.

    Existing system-state chDefaultFace, scenario font commands, or a later
    Override.tjs hook may supersede this default. No existing script is executed.
    """
    old = b';userFace = global.SystemDefaultFontFace;'
    # Legacy KiriKiri resolves face names against its enumerated family names.
    # Korean Windows may expose only the localized family, not the English alias.
    replacement = b';userFace = "\\xB9D1\\xC740 \\xACE0\\xB515,Malgun Gothic";'
    if not isinstance(original, bytes) or original.count(old) != 1:
        raise ValueError('Config.tjs does not match the verified font setting')
    return original.replace(old, replacement, 1)
