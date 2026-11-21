"""Read KAGEX character display-name aliases without executing game data."""
import csv
import io


def is_alias_table(rel):
    """Recognize the engine's character voice table by its file name."""
    return isinstance(rel, str) and rel.replace('\\', '/').rsplit('/', 1)[-1].casefold() in {
        'charvoice.csv', 'charvoice.txt'
    }


def _literal(value):
    return (isinstance(value, str) and bool(value.strip())
            and not value.lstrip().startswith('&')
            and not any(ord(c) < 32 or ord(c) == 127 for c in value))


def _decode(data):
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return data.decode('utf-16')
    try:
        return data.decode('utf-8-sig')
    except UnicodeDecodeError:
        return data.decode('cp932')


def parse_alias_tables(blobs):
    """Parse literal fifth-column aliases; conflicting IDs are never resolved."""
    aliases, issues, files = {}, [], []
    blocked = set()
    for source in sorted(blobs, key=str.casefold):
        if not is_alias_table(source):
            continue
        files.append(source)
        try:
            content = _decode(blobs[source])
        except (UnicodeError, TypeError, AttributeError):
            issues.append({'code': 'decode_error', 'source': source})
            continue
        lines = [line for line in content.splitlines() if line.strip() and not line.lstrip().startswith(('#', ';', '//'))]
        if not lines:
            continue
        # The first two columns are required; later columns are optional.
        # Prefer the delimiter that separates the first record into fields.
        def score(delimiter):
            try:
                fields = next(csv.reader([lines[0]], delimiter=delimiter, strict=True))
                return (len(fields) >= 2, len(fields))
            except csv.Error:
                return (False, 0)
        delimiter = '\t' if score('\t') > score(',') else ','
        try:
            rows = csv.reader(io.StringIO('\n'.join(lines)), delimiter=delimiter, strict=True)
            for row in rows:
                line = rows.line_num
                if len(row) < 2:
                    issues.append({'code': 'malformed_row', 'source': source, 'line': line})
                    continue
                name = row[0].strip()
                alias = row[4].strip() if len(row) >= 5 else ''
                if alias.casefold() == 'namealias':
                    continue
                if not _literal(name):
                    issues.append({'code': 'invalid_name', 'source': source, 'line': line})
                    continue
                key = name.casefold()
                if alias and not _literal(alias):
                    issues.append({'code': 'dynamic_alias' if alias.lstrip().startswith('&') else 'invalid_alias',
                                   'source': source, 'line': line, 'name': name})
                    # An unresolved definition makes this ID ambiguous, even if
                    # another table also provides a literal definition.
                    blocked.add(key)
                    aliases.pop(key, None)
                    continue
                value = alias or name
                if key in blocked:
                    continue
                if key in aliases and aliases[key] != value:
                    issues.append({'code': 'conflicting_alias', 'source': source, 'line': line, 'name': name})
                    blocked.add(key)
                    del aliases[key]
                else:
                    aliases[key] = value
        except csv.Error:
            issues.append({'code': 'malformed_table', 'source': source})
    return {'version': 1, 'aliases': aliases, 'issues': issues, 'files': files}


def resolve_default_name(internal, aliases):
    """Return the literal alias once, or the original internal name."""
    if not _literal(internal):
        return None
    value = aliases.get(internal.casefold()) if hasattr(aliases, 'get') else None
    display = value if _literal(value) else internal
    if any(c in display for c in '{}[]\\$%#'):
        return None
    return display
