# -*- coding: utf-8 -*-
"""Copied into the additive patch; compatible with Ren'Py Python 2 and 3."""
import re as _rpt_re


def rpt_josa(value, pair, variable=None):
    try:
        text_type = unicode
    except NameError:
        text_type = str
    display=globals().get('rpt_display_name')
    resolved=display(value,variable) if display is not None else value
    text = _rpt_re.sub(r'\{[^{}]*\}', '', text_type(resolved)).strip()
    text = text.rstrip(u' \t\r\n.!?,:;\"\'\u2019\u201d)]}')
    # Only known pronunciation is used; spelling alone cannot identify arbitrary names.
    if not (text and 0xAC00 <= ord(text[-1]) <= 0xD7A3):
        text = globals().get('_rpt_pronunciations', {}).get(text.lower(), text)
    if text.lower() == 'john': text = u'존'
    tail = None
    if text and 0xAC00 <= ord(text[-1]) <= 0xD7A3:
        tail = (ord(text[-1]) - 0xAC00) % 28
    if pair == u'으로/로':
        return u'으로(로)' if tail is None else (u'로' if tail in (0, 8) else u'으로')
    forms = pair.split('/')
    if tail is None: return forms[0] + '(' + forms[1] + ')'
    return forms[0] if tail else forms[1]
