"""Positioned message boundaries are layout, not optional translation whitespace."""
import re

POSITION = re.compile(r'\\(?:PX|PY)\[-?\d+\]', re.I)
POSITION_RUN = re.compile(r'(?:\\(?:PX|PY)\[-?\d+\])+', re.I)
STYLE = r'\\(?:(?:FS|C)\[-?\d+\]|[.!|^><${}])'
DISPLAY = r'\\(?:(?:PX|PY|FS|C)\[-?\d+\]|[.!|^><${}])'
LAYOUT_RUN = re.compile(r'(?:\r?\n[ \t]*)+(?=(?:'+STYLE+r')*'+POSITION.pattern+r')(?:'+DISPLAY+r')+', re.I)
BEFORE_POSITION = re.compile(r'\r?\n[ \t]*((?:'+STYLE+r')*)$', re.I)


def repair(source, target):
    """Restore missing newlines at matching position-code occurrences, no text edits."""
    original = list(POSITION.finditer(source)); translated = list(POSITION.finditer(target))
    if [m[0].upper() for m in original] != [m[0].upper() for m in translated]:
        return target, []
    changes = []
    for a, b in zip(original, translated):
        boundary=BEFORE_POSITION.search(source[:a.start()])
        if boundary:
            prefix=boundary[1];index=b.start()-len(prefix)
            if index>=0 and target[index:b.start()].upper()==prefix.upper() and not re.search(r'\r?\n[ \t]*$',target[:index]):
                changes.append({'source_offset': a.start()-len(prefix), 'target_offset': index, 'code': prefix+a[0]})
    for change in reversed(changes):
        index = change['target_offset']; target = target[:index] + '\n' + target[index:]
    return target, changes


def segments(source):
    """Keep position runs outside inference; their preceding newline travels with them."""
    result = []; prefix = ''; cursor = 0
    for match in re.finditer(LAYOUT_RUN.pattern+'|'+POSITION_RUN.pattern,source,re.I):
        body = source[cursor:match.start()]
        newline = re.search(r'(?:\r?\n[ \t]*)+$', body)
        boundary = newline[0] if newline else ''
        if newline:
            body = body[:newline.start()]
        if body:
            result.append({'prefix': prefix, 'source': body}); prefix = ''
        prefix += boundary + match[0]; cursor = match.end()
    body = source[cursor:]
    if body or prefix:
        result.append({'prefix': prefix, 'source': body})
    return result
