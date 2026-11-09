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
    _rpt_probe_event(data)
    try:
        operation()
    except Exception as exc:
        data.update(status='failed', error_type=type(exc).__name__, error=str(exc),
                    traceback=_rpt_probe_traceback.format_exc())
    else:
        data.update(status='passed')
    data['seconds'] = round(_rpt_probe_time.time() - started, 4)
    _rpt_probe_event(data)


def _rpt_probe_text(row):
    # Interpolation is deliberately not evaluated without the game's save state.
    # Markup/font/layout still run in the actual engine and actual game styles.
    text = row['text']
    displayable = _rpt_probe_engine.text.text.Text(text, substitute=False, slow=False,
                                     style='say_dialogue' if row.get('kind') == 'dialogue' else 'choice_button_text')
    renpy.render(displayable, int(config.screen_width), int(config.screen_height), 0, 0)


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
    for index, row in enumerate(data.get('rows', [])):
        _rpt_probe_attempt('text', lambda r=row: _rpt_probe_text(r), row)
        if index and index % 256 == 0:
            renpy.free_memory()
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
