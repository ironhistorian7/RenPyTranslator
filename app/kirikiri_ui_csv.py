"""Read the visible fields of standard KAGEX gallery lists."""
import codecs
import csv
import re
from pathlib import PurePosixPath

from kirikiri_text import _decode


_LISTS = {
    'soundlist.csv': (4, 4, (2, 3)),
    'movielist.csv': (5, 7, (4,)),
    'scenelist.csv': (5, 7, (4,)),
}
_FIELD = re.compile(r'(?:^|,)("(?:[^"]|"")*"|[^",]*)')
DIALECT = 'kirikiri_csv_ui'


def is_gallery_list(name):
    return PurePosixPath(name).name.casefold() in _LISTS


def extract_ui_csv(raw, name):
    basename = PurePosixPath(name).name.casefold()
    if basename not in _LISTS:
        return []
    minimum, maximum, visible = _LISTS[basename]
    text, _, _ = _decode(raw)
    offset = 0
    entries = []
    for number, physical in enumerate(text.splitlines(keepends=True), 1):
        line = physical.rstrip('\r\n')
        start = offset
        offset += len(physical)
        if not line or line.lstrip().startswith(('#', '"#', ';', '//')):
            continue
        try:
            fields = next(csv.reader([line], strict=True))
        except csv.Error:
            continue
        spans = list(_FIELD.finditer(line))
        if not minimum <= len(fields) <= maximum or len(spans) != len(fields) or spans[-1].end() != len(line):
            continue
        if not fields[0].strip() or (basename == 'soundlist.csv' and not fields[1].strip().isdigit()):
            continue
        for column in visible:
            value = fields[column]
            if not value.strip():
                continue
            a, b = spans[column].span(1)
            entries.append(dict(source=value, usage='ui_label', dialect=DIALECT, file=name,
                                source_line=number, location={'start': start+a, 'end': start+b},
                                native_menu=False, ui_suffix=''))
    return entries


def render_ui_csv(raw, name, edits):
    by_span = {(row['location']['start'], row['location']['end']): row['source']
               for row in extract_ui_csv(raw, name)}
    text, encoding, bom = _decode(raw)
    last = len(text)
    for row in sorted(edits, key=lambda item: item['location']['start'], reverse=True):
        a, b = row['location']['start'], row['location']['end']
        value = row['translation']
        if b > last or by_span.get((a, b)) != row['source'] or any(c in value for c in '\r\n\0'):
            raise ValueError('갤러리 목록의 표시 필드가 올바르지 않습니다: ' + name)
        quoted = text[a:b].startswith('"') or any(c in value for c in ',"')
        encoded = '"' + value.replace('"', '""') + '"' if quoted else value
        text = text[:a] + encoded + text[b:]
        last = a
    try:
        return bom + text.encode(encoding)
    except UnicodeEncodeError:
        return codecs.BOM_UTF16_LE + text.encode('utf-16-le')
