# Embedded only in an isolated validation workspace. Python 2.7/3 compatible.
# The probe does not deliberately invoke game dialogue labels, button actions,
# or input setters. Game init/screen callbacks still execute in this workspace.
import io as _rpt_probe_io
import json as _rpt_probe_json
import os as _rpt_probe_os
import time as _rpt_probe_time
import traceback as _rpt_probe_traceback
import renpy as _rpt_probe_engine


def _rpt_probe_event(record):
    record['time'] = _rpt_probe_time.time()
    with _rpt_probe_io.open(_rpt_probe_os.environ['RPT_VALIDATION_EVENTS'], 'a', encoding='utf-8') as stream:
        stream.write(_rpt_probe_json.dumps(record, ensure_ascii=False) + u'\n')


def _rpt_probe_attempt(name, operation, row=None):
    started = _rpt_probe_time.time()
    data = dict(check=name, status='running')
    if row is not None:
        data.update(id=row.get('id'), file=row.get('file'), line=row.get('line'))
    if row is not None and 'interpolation' in row:
        data['interpolation'] = _rpt_probe_interpolation_coverage(row['interpolation'])
    _rpt_probe_event(data)
    try:
        result = operation()
    except Exception as exc:
        if row is not None and 'interpolation' in row:
            # Renderer exceptions can echo substituted values. Never record them.
            data.update(status='failed', error_type=type(exc).__name__,
                        error='Interpolation or rendering failed; details withheld.',
                        phase=getattr(exc, '_rpt_probe_phase', 'interpolation'),
                        diagnostic=_rpt_probe_diagnostic(exc))
            tag = _rpt_probe_diagnostic_tag(exc)
            if tag is not None:
                data['tag'] = tag
        else:
            data.update(status='failed', error_type=type(exc).__name__, error=str(exc),
                        traceback=_rpt_probe_traceback.format_exc())
    else:
        data.update(status='passed')
        if isinstance(result, dict) and result.get('sampled_format_fields'):
            data['sampled_format_fields'] = result['sampled_format_fields']
    data['seconds'] = round(_rpt_probe_time.time() - started, 4)
    _rpt_probe_event(data)


def _rpt_probe_format_slots(text):
    """Find empty UI format fields, keeping escapes, tags and expressions opaque."""
    slots = []
    position = 0
    while position < len(text):
        if text.startswith('{{', position) or text.startswith('[[', position):
            position += 2
            continue
        char = text[position]
        if char == '[':
            # Do not treat an empty Python dictionary in interpolation as a field.
            depth = 1
            quote = None
            position += 1
            while position < len(text) and depth:
                char = text[position]
                if char == '\\':
                    position += 2
                    continue
                if quote:
                    if text.startswith(quote, position):
                        position += len(quote)
                        quote = None
                        continue
                elif char in ('"', "'"):
                    quote = char * 3 if text.startswith(char * 3, position) else char
                    position += len(quote)
                    continue
                elif char == '[':
                    depth += 1
                elif char == ']':
                    depth -= 1
                position += 1
            continue
        if text.startswith('{}', position):
            slots.append(position)
            position += 2
            continue
        if char == '{':
            end = text.find('}', position + 1)
            if end >= 0:
                position = end + 1
                continue
        position += 1
    return slots


def _rpt_probe_sample_text(row):
    text = row['text']
    # These UI templates normally receive .format() arguments before display.
    # Sampling is probe-only; unknown tags and mismatched fields still fail.
    if row.get('kind') != 'string' or 'source' not in row:
        return text, 0
    original = _rpt_probe_format_slots(row['source'])
    if not original:
        return text, 0
    slots = _rpt_probe_format_slots(text)
    if len(slots) != len(original):
        return text, 0
    pieces = []
    start = 0
    for position in slots:
        pieces.extend((text[start:position], u'0'))
        start = position + 2
    pieces.append(text[start:])
    return u''.join(pieces), len(slots)


def _rpt_probe_diagnostic(exc):
    # Only classify known complete messages, never retain exception text.
    import re
    if getattr(exc, '_rpt_probe_unclosed', False):
        return 'unclosed_interpolation'
    message = str(exc)
    if re.match(r"^Unknown text tag '[A-Za-z_][A-Za-z0-9_]*'\.?$", message):
        return 'unknown_text_tag'
    if re.match(r"^'/[A-Za-z_][A-Za-z0-9_]*' closes a text tag that isn't open\.?$", message):
        return 'unexpected_closing_tag'
    if re.match(r"^invalid conversion(?: '[A-Za-z!]'| specifier(?:: '[A-Za-z!]')?)?\.?$", message):
        return 'invalid_conversion'
    return 'unknown'


def _rpt_probe_diagnostic_tag(exc):
    import re
    diagnostic = _rpt_probe_diagnostic(exc)
    if diagnostic == 'unknown_text_tag':
        match = re.match(r"^Unknown text tag '([A-Za-z_][A-Za-z0-9_]*)'\.?$", str(exc))
    elif diagnostic == 'unexpected_closing_tag':
        match = re.match(r"^'/([A-Za-z_][A-Za-z0-9_]*)' closes a text tag that isn't open\.?$", str(exc))
    else:
        return None
    return match.group(1) if match else None


def _rpt_probe_interpolation_coverage(recipe):
    parts = recipe.get('parts', []) if isinstance(recipe, dict) else []
    values = recipe.get('values', {}) if isinstance(recipe, dict) else {}
    unresolved = recipe.get('unresolved', []) if isinstance(recipe, dict) else []
    slots = [part for part in parts if isinstance(part, dict)] if isinstance(parts, list) else []
    import re
    names = []
    resolved = 0
    for part in slots:
        name = part.get('name')
        try:
            valid_name = isinstance(name, basestring) and re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', name)
        except NameError:
            valid_name = isinstance(name, str) and re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', name)
        if valid_name and name not in names:
            names.append(name)
        try:
            if isinstance(values, dict) and part.get('name') in values:
                resolved += 1
        except TypeError:
            pass
    unresolved_count = len(unresolved) if isinstance(unresolved, list) else 0
    unresolved_count += len(slots) - resolved
    return dict(scenario=recipe.get('scenario', 'initialized_literals') if isinstance(recipe, dict) else 'initialized_literals',
                slots_total=resolved + unresolved_count, slots_resolved=resolved,
                slots_unresolved=unresolved_count, names=names,
                comparable=(unresolved_count == 0 and not bool(recipe.get('unclosed', False)))
                if isinstance(recipe, dict) else False)


def _rpt_probe_interpolated_text(row):
    recipe = row['interpolation']
    if not isinstance(recipe, dict) or not isinstance(recipe.get('parts'), list) or not isinstance(recipe.get('values'), dict):
        raise ValueError('Invalid interpolation recipe')
    if recipe.get('unclosed', False):
        exc = ValueError('Unclosed interpolation')
        exc._rpt_probe_unclosed = True
        raise exc
    try:
        string_types = (basestring,)
        to_text = unicode
        integer_types = (int, long)
    except NameError:
        string_types = (str,)
        to_text = str
        integer_types = (int,)
    values = recipe['values']
    # Sample UI .format fields in literal parts only, before inserting values.
    _, sampled = _rpt_probe_sample_text(row)
    remaining = sampled
    pieces = []
    for part in recipe['parts']:
        if isinstance(part, string_types):
            if remaining:
                positions = _rpt_probe_format_slots(part)
                chunks = []
                start = 0
                for position in positions:
                    if not remaining:
                        break
                    chunks.extend((part[start:position], u'0'))
                    start = position + 2
                    remaining -= 1
                chunks.append(part[start:])
                part = u''.join(chunks)
            pieces.append(part)
            continue
        if (not isinstance(part, dict) or set(part) != set(('name', 'quote'))
                or not isinstance(part['name'], string_types) or not isinstance(part['quote'], bool)):
            raise ValueError('Invalid interpolation slot')
        name = part['name']
        if name not in values:
            pieces.append(u'[' + name + (u'!q' if part['quote'] else u'') + u']')
            continue
        value = values[name]
        if not isinstance(value, string_types + integer_types + (float, bool)):
            raise ValueError('Invalid interpolation value')
        value = to_text(value)
        if part['quote']:
            # Ren'Py !q escapes opening text-tag braces after conversion to text.
            value = value.replace(u'{', u'{{')
        pieces.append(value)
    if remaining:
        raise ValueError('Interpolation parts do not preserve format fields')
    return u''.join(pieces), sampled


def _rpt_probe_text(row):
    phase = 'interpolation'
    try:
        if 'interpolation' in row:
            text, sampled = _rpt_probe_interpolated_text(row)
        else:
            text, sampled = _rpt_probe_sample_text(row)
        phase = 'render'
        displayable = _rpt_probe_engine.text.text.Text(text, substitute=False, slow=False,
                                         style='say_dialogue' if row.get('kind') == 'dialogue' else 'choice_button_text')
        renpy.render(displayable, int(config.screen_width), int(config.screen_height), 0, 0)
        return dict(sampled_format_fields=sampled)
    except Exception as exc:
        try:
            exc._rpt_probe_phase = phase
        except Exception:
            pass
        raise


def _rpt_probe_progress(done, total):
    _rpt_probe_event(dict(check='progress', status='running', done=done, total=total))
    # The modal screen ends its own interaction with a timer/Return action.
    # A timed pause below a modal screen can otherwise wait forever.
    renpy.call_screen('_rpt_validation_progress', done=done, total=total)


def _rpt_probe_active_menus(screen):
    seen = set()
    def action(value):
        if isinstance(value, (list, tuple)):
            for child in value:
                action(child)
        elif type(value).__name__ == 'ShowMenu':
            target = value.screen or globals().get('_game_menu_screen')
            if not target or target in seen:
                return
            seen.add(target)
            exists = (renpy.has_screen(target) or renpy.has_label(target)
                      or renpy.has_label(target + '_screen'))
            reference = next((item for item in globals().get('_rpt_probe_targets', [])
                              if item['target'] == target), {})
            _rpt_probe_event(dict(check='menu_target', status='passed' if exists else 'failed',
                                  error_type=None if exists else 'MissingMenuTarget',
                                  error=None if exists else 'Missing ShowMenu target: ' + target,
                                  target=target, file=reference.get('file'), line=reference.get('line')))
    def visit(displayable):
        # After per_interact, an insensitive button has clicked=None. Merely
        # inspect active built-in menu actions; never invoke any game action.
        clicked = getattr(displayable, 'clicked', None)
        if clicked is not None:
            action(clicked)
    if screen is not None and getattr(screen, 'child', None) is not None:
        screen.child.visit_all(visit)


def _rpt_probe_show(name, arguments):
    renpy.show_screen(name, **arguments)
    try:
        renpy.pause(.05, hard=True)
        _rpt_probe_active_menus(renpy.get_screen(name))
    finally:
        renpy.hide_screen(name)


def _rpt_probe_main():
    with _rpt_probe_io.open(_rpt_probe_os.environ['RPT_VALIDATION_INPUT'], 'r', encoding='utf-8') as stream:
        data = _rpt_probe_json.load(stream)
    _rpt_probe_event(dict(check='startup', status='passed', engine=list(_rpt_probe_engine.version_tuple)))
    # Match the engine's ordinary main-menu context without entering any story.
    _rpt_probe_engine.game.context()._main_menu = True
    globals()['main_menu'] = True
    if data.get('language') is not None:
        _rpt_probe_attempt('language', lambda: renpy.change_language(data['language']))
    globals()['_rpt_probe_targets'] = data.get('menu_targets', [])
    for name, arguments in [('main_menu', {}), ('say', {'who': None, 'what': u'Validation text.'}),
                            ('input', {'prompt': u'Validation input.'})]:
        if renpy.has_screen(name):
            _rpt_probe_attempt('screen:' + name, lambda n=name, a=arguments: _rpt_probe_show(n, a))
        else:
            _rpt_probe_event(dict(check='screen:' + name, status='not_available'))
    if renpy.has_screen('choice'):
        def choice():
            renpy.show_screen('_rpt_validation_return', value=0)
            try:
                renpy.display_menu([(u'Validation choice.', 0), (u'Long validation choice. ' * 8, 1)])
            finally:
                renpy.hide_screen('_rpt_validation_return')
        _rpt_probe_attempt('screen:choice', choice)
    else:
        _rpt_probe_event(dict(check='screen:choice', status='not_available'))
    rows = data.get('rows', [])
    try:
        _rpt_probe_progress(0, len(rows))
        last_update = _rpt_probe_time.time()
        shown = 0
        for index, row in enumerate(rows):
            _rpt_probe_attempt('text', lambda r=row: _rpt_probe_text(r), row)
            if index and index % 256 == 0:
                renpy.free_memory()
            if _rpt_probe_time.time() - last_update >= .25:
                shown = index + 1
                _rpt_probe_progress(shown, len(rows))
                last_update = _rpt_probe_time.time()
        if shown != len(rows):
            _rpt_probe_progress(len(rows), len(rows))
    finally:
        renpy.hide_screen('_rpt_validation_progress')
    _rpt_probe_event(dict(check='complete', status='passed'))


def _rpt_probe_install_lint_capture():
    # Older supported engines do not implement lint --error-code. Capture their
    # actual report() calls in this workspace instead of treating exit 0 as clean.
    import renpy.lint as lint
    original = getattr(lint, 'report', None)
    if original is None:
        _rpt_probe_event(dict(check='lint_capture', status='not_available'))
        return
    def report(message, *args):
        result = original(message, *args)
        node = getattr(lint, 'report_node', None)
        _rpt_probe_event(dict(check='lint_issue', status='failed', error_type='LintIssue',
                              error=message % args if args else message,
                              file=getattr(node, 'filename', None), line=getattr(node, 'linenumber', None)))
        return result
    lint.report = report
    _rpt_probe_event(dict(check='lint_capture', status='passed'))


_rpt_probe_install_lint_capture()
