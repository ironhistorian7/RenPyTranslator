"""Prepare repeatable interpolation cases without loading game code or saves."""
import ast
import math
import os
from pathlib import Path
import re
from textwrap import dedent

from renpy_text_syntax import TOKENS


_NAME = r'(?:[^\W\d]|_)\w*'
_SLOT = re.compile(r'\[(' + _NAME + r')(!q)?\]')
_DECLARATION = re.compile(r'^(?:default|define)(?:[ \t]+[+-]?\d+)?[ \t]+(' + _NAME + r')\s*=\s*(.*)$', re.S)
_INIT = re.compile(r'^init(?:[ \t]+[+-]?\d+)?[ \t]+python\s*:\s*(?:#.*)?$')


def _parts(text):
    parts = []
    names = set()
    unresolved = []
    cursor = 0
    token_end = 0
    unclosed = 0

    def collect(token):
        nonlocal cursor
        value = token.group()
        if not value.startswith('[') or value == '[[':
            return
        match = _SLOT.fullmatch(value)
        if not match:
            # Never evaluate a game object, function, formatted expression, or UI scope.
            unresolved.append('complex_expression')
            return
        name, quoted = match.groups()
        names.add(name)
        parts.extend((text[cursor:token.start()], {'name': name, 'quote': bool(quoted)}))
        cursor = token.end()

    for token in TOKENS.finditer(text):
        unclosed += text.count('[', token_end, token.start())
        token_end = token.end()
        value = token.group()
        if value.startswith('{') and '[' in value:
            # Ren'Py substitutes variables before it interprets tag arguments.
            for argument in TOKENS.finditer(text, token.start() + 1, token.end() - 1):
                collect(argument)
        else:
            collect(token)
    parts.append(text[cursor:])
    return parts, names, unresolved, unclosed + text.count('[', token_end)


def _prepared_scripts(project):
    visited = set()
    for base in (project / 'staging/game', project / 'data/recovered-scripts'):
        if any(path.is_symlink() or path.is_junction() for path in (base, base.parent)):
            continue
        for folder, dirs, files in os.walk(base, followlinks=False):
            directory = Path(folder)
            dirs[:] = [name for name in dirs if name not in {'tl', 'cache', 'saves', '__pycache__'}
                       and not (directory / name).is_junction() and not (directory / name).is_symlink()]
            for name in files:
                path = directory / name
                relative = path.relative_to(base)
                if (path.suffix.lower() not in {'.rpy', '.rpym'} or name.startswith(('zz_rpt_', 'zzzz_rpt_'))
                        or relative in visited or path.is_symlink() or path.is_junction()):
                    continue
                try:
                    text = path.read_text(encoding='utf-8-sig')
                except (OSError, UnicodeError):
                    continue
                visited.add(relative)
                yield text


def _assignments(text, names):
    from name_hints import statements

    current = None
    for _, _, line in statements(text):
        if line.startswith('#'):
            continue
        if line[:1].isspace() or not line.strip():
            if current is not None:
                current.append(line)
            continue
        if current is not None:
            yield from _init_assignments(current, names)
            current = None
        if _INIT.fullmatch(line):
            current = []
        else:
            match = _DECLARATION.fullmatch(line)
            if match and match[1] in names:
                yield match[1], match[2]
    if current is not None:
        yield from _init_assignments(current, names)


def _init_assignments(lines, names):
    try:
        block = ast.parse(dedent('\n'.join(lines)))
    except (SyntaxError, ValueError):
        return
    for statement in block.body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name) and target.id in names:
                    yield target.id, statement.value


def _values(project, names):
    known = {}
    uncertain = set()
    for text in _prepared_scripts(project):
        for name, expression in _assignments(text, names):
            try:
                value = ast.literal_eval(expression)
            except (ValueError, SyntaxError):
                uncertain.add(name)
                continue
            if (type(value) not in (str, int, float, bool) or isinstance(value, float) and not math.isfinite(value)
                    or name in known and (type(value) is not type(known[name]) or value != known[name])):
                uncertain.add(name)
            else:
                known[name] = value
    return {name: value for name, value in known.items() if name not in uncertain}


def prepare_pair(project, original_rows, patched_rows):
    """Attach the same frozen test values to both sides of the existing probe."""
    plans = []
    names = set()
    for original, patched in zip(original_rows, patched_rows):
        if '[' not in original['text'] and '[' not in patched['text']:
            continue
        a, b = (_parts(row['text']) for row in (original, patched))
        used = a[1] | b[1]
        newly_unclosed = patched.get('kind') == 'dialogue' and b[3] > a[3]
        if used or a[2] or b[2] or newly_unclosed:
            plans.append((original, a, patched, b, used, newly_unclosed))
            names.update(used)
    values = _values(project, names) if names else {}
    for original, a, patched, b, used, newly_unclosed in plans:
        frozen = {name: values[name] for name in sorted(used & values.keys())}
        comparable = bool(frozen) and a[1] == b[1] == frozen.keys() and not a[2] and not b[2]
        for row, parsed in ((original, a), (patched, b)):
            parts, _, unresolved, _ = parsed
            row['interpolation'] = dict(parts=parts, values=frozen, unresolved=unresolved,
                                        scenario='initialized_literals', comparable=comparable,
                                        unclosed=bool(row is patched and newly_unclosed))
