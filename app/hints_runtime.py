# Embedded in Ren'Py 7/8. Keep Python 2.7 compatible; no evaluation of game expressions.
_rpt_hint_index = {}
_rpt_hint_session = None
for _rpt_hint_kind in ('answers', 'routes'):
    _rpt_hint_index[_rpt_hint_kind] = {}
    for _rpt_hint_entry in _rpt_hint_data.get(_rpt_hint_kind, []):
        _rpt_hint_index[_rpt_hint_kind].setdefault(_rpt_hint_entry['line'], []).append(_rpt_hint_entry)


def _rpt_hint_escape(value):
    return value.replace(u'{', u'{{').replace(u'[', u'[[')


def _rpt_hint_active():
    return _preferences.language in (None, _rpt_hint_data['language'])


def _rpt_hint_location():
    filename, line = renpy.get_filename_line()
    return (filename or '').replace('\\', '/'), line


def _rpt_hint_translate(text):
    prompts=globals().get('_rpt_name_inputs',{}).get('prompts',{})
    if text in prompts:return prompts[text]
    # In script scope renpy may be exports, which lacks this alias in 7.3.5.
    translator = getattr(renpy, 'translate_string', None)
    if translator is None:
        from renpy.translation import translate_string as translator
    return translator(text)


def _rpt_hint_match(kind, caption):
    filename, line = _rpt_hint_location()
    candidates = []
    for entry in _rpt_hint_index.get(kind, {}).get(line, []):
        if not (filename == entry['file'] or filename.endswith('/' + entry['file'])):
            continue
        source = entry.get('source')
        if (source is not None and caption != source
                and caption != globals().get('_rpt_choices', {}).get(source)
                and caption != _rpt_hint_translate(source)):
            continue
        if entry['line'] == line:
            candidates.append(entry)
    # Duplicate captions in the same menu are deliberately not conflated.
    entry = candidates[0] if len(candidates) == 1 else None
    if kind == 'routes' and '_rpt_hint_current' in globals():
        return _rpt_hint_current(entry)
    return entry


def _rpt_hint_insert(caption, hint, newline=False):
    # New semantic reference wrapper, followed by the older patch spelling.
    marker = u'\n{rpt_ref=0.55}'
    if marker not in caption:
        legacy = _rpt_layout_re.search(r'\n\{size=[^}]+\}\{cps=0\}\{rpt_ref\}', caption)
        marker = legacy.group() if legacy else u'\n{size=*0.55}'
    head, separator, reference = caption.partition(marker)
    decorated = head + (u'\n' if newline else u' ') + hint
    return decorated + (separator + reference if separator else u'')


def _rpt_hint_label(caption):
    choices = globals().get('_rpt_choices', {})
    if caption in choices:
        return choices[caption]
    return _rpt_hint_translate(caption)


def _rpt_hint_decorate(effect, text, size=None):
    text = _rpt_hint_escape(u'[' + text + u']')
    font = _rpt_hint_data['bold_font'] if effect['bold'] else _rpt_hint_data['font']
    if effect['bold']:
        text = u'{b}' + text + u'{/b}'
    text = u'{font=' + font + u'}{color=' + effect['color'] + u'}' + text + u'{/color}{/font}'
    if size is not None:
        text = u'{size=' + str(size) + u'}' + text + u'{/size}'
    return text


def _rpt_hint_measure(text, width, size):
    displayable = Text(text, style='default', font=_rpt_hint_data['font'], size=size,
                       layout='greedy', line_spacing=0, slow=False)
    return renpy.render(displayable, width, 100000, 0.0, 0.0).get_size()[1]


def _rpt_hint_screen_layout(items, dialogue=None):
    state = _rpt_hint_session
    captions = []
    effects = []
    actions = []
    if dialogue:
        context = []
        for entry in dialogue:
            who = getattr(entry, 'who', None)
            what = getattr(entry, 'what', u'')
            context.append((who + u': ' if who else u'') + what)
        captions.append(u'\n'.join(context)); effects.append([]); actions.append(None)
    for item in items:
        caption = item.caption
        match = next((e for original, label, e in state['entries'] if caption in (original, label)), None)
        captions.append(caption); effects.append(match['hints'] if match else []); actions.append(item.action)
    width, height = int(config.screen_width), int(config.screen_height)
    gui_object = globals().get('gui')
    size = getattr(gui_object, 'choice_button_text_size', None) or getattr(gui_object, 'text_size', None) or int(height * .035)
    size = max(18, int(size))
    # Substitute only for the cache key; Text performs the real substitution itself.
    measured_captions = tuple(renpy.substitute(c) for c in captions)
    key = (width, height, size, measured_captions)
    if state.get('layout_key') != key:
        state['layout'] = _rpt_hint_geometry(captions, effects, width, height, size,
            _rpt_hint_measure, _rpt_hint_decorate,
            lambda caption, hint: _rpt_hint_insert(caption, hint, newline=True))
        state['layout_key'] = key
    layout = state['layout']
    # Menu actions are interaction-local; never persist them in the layout cache.
    rows = [dict(row, action=action) for row, action in zip(layout['rows'], actions)]
    state['details'] = [caption + (u'\n\n' + u'\n'.join(_rpt_hint_decorate(e, e['text']) for e in sorted(row, key=_rpt_hint_priority)) if row else u'')
                        for caption, row in zip(captions, effects)]
    if not 0 <= state['focus'] < len(rows) or rows[state['focus']]['action'] is None:
        state['focus'] = next((i for i, row in enumerate(rows) if row['action'] is not None), 0)
    return dict(layout, rows=rows)


def _rpt_hint_focus(index):
    state = _rpt_hint_session
    if not state or state['focus'] == index:
        return
    state['focus'] = index
    state['detail_adjustment'].change(0)
    layout = state.get('layout')
    if layout and 0 <= index < len(layout['rows']):
        row = layout['rows'][index]
        adjustment = state['list_adjustment']
        if row['y'] >= adjustment.value + layout['list'][3] or row['y'] + row['height'] <= adjustment.value:
            adjustment.change(row['y'])
    renpy.restart_interaction()


def _rpt_hint_detail(index):
    details = (_rpt_hint_session or {}).get('details', [])
    return details[index] if 0 <= index < len(details) else u''


def _rpt_hint_menu(previous):
    def wrapped(items, *args, **kwargs):
        global _rpt_hint_session
        if not _rpt_hint_active():
            return previous(items, *args, **kwargs)
        entries = [(caption, _rpt_hint_label(caption) if caption else caption,
                    _rpt_hint_match('routes', caption) if caption and value is not None else None)
                   for caption, value in items]
        # In script scope `renpy` IS renpy.exports (defaultstore.py), not the
        # top-level Python package. Test harnesses may expose an exports member.
        exports = getattr(renpy, 'exports', renpy)
        # Use the engine's normal display_menu flow, retaining ChoiceReturn actions,
        # disabled choices, rollback and NVL scope. Only the screen name is temporary.
        if (any(e for caption, label, e in entries) and exports is not None
                and hasattr(exports, 'menu_kwargs') and hasattr(renpy, 'has_screen')
                and renpy.has_screen('_rpt_hint_choice')):
            previous_session = _rpt_hint_session
            previous_kwargs = exports.menu_kwargs
            previous_args = exports.menu_args
            _rpt_hint_session = {'entries': entries, 'focus': 0, 'details': [],
                                 'list_adjustment': ui.adjustment(), 'detail_adjustment': ui.adjustment()}
            exports.menu_kwargs = dict(previous_kwargs or {}, screen='_rpt_hint_choice')
            # get_menu_args ignores menu_kwargs when menu_args is None (direct calls).
            if previous_args is None:
                exports.menu_args = ()
            if kwargs.get('_args') is not None or kwargs.get('_kwargs') is not None:
                kwargs = dict(kwargs, _kwargs=dict(kwargs.get('_kwargs') or {}, screen='_rpt_hint_choice'))
            try:
                return previous(items, *args, **kwargs)
            finally:
                exports.menu_kwargs = previous_kwargs
                exports.menu_args = previous_args
                _rpt_hint_session = previous_session
        updated = []
        for (caption, value), (_, label, entry) in zip(items, entries):
            if entry:
                # Legacy non-screen menus cannot host a detail pane; do not grow them
                # without bound. Modern screen menus always take the measured path.
                compact = sorted(entry['hints'], key=_rpt_hint_priority)[:2]
                hints = [_rpt_hint_decorate(e, _rpt_hint_compact(e)) for e in compact
                         if len(_rpt_hint_compact(e)) <= 48]
                caption = _rpt_hint_insert(label, u' '.join(hints)) if hints else label
            updated.append((caption, value))
        return previous(updated, *args, **kwargs)
    wrapped._rpt_hint_wrapper = True
    return wrapped


def _rpt_hint_input(previous):
    def wrapped(prompt, *args, **kwargs):
        entry = _rpt_hint_match('answers', prompt) if _rpt_hint_active() else None
        if entry:
            answer = _rpt_hint_escape(u'정답: ' + u' / '.join(entry['values']))
            # The original-language guard also prevents name replacement inside literal answers.
            if globals().get('_rpt_reference_ready', False):
                answer = u'{rpt_ref}' + answer + u'{/rpt_ref}'
            answer = u'{font=' + _rpt_hint_data['font'] + u'}' + answer + u'{/font}'
            prompt = _rpt_hint_insert(_rpt_hint_translate(prompt), answer, newline=True)
        return previous(prompt, *args, **kwargs)
    wrapped._rpt_hint_wrapper = True
    return wrapped


if not getattr(menu, '_rpt_hint_wrapper', False):
    menu = _rpt_hint_menu(menu)
if 'nvl_menu' in globals() and not getattr(nvl_menu, '_rpt_hint_wrapper', False):
    nvl_menu = _rpt_hint_menu(nvl_menu)
if not getattr(renpy.input, '_rpt_hint_wrapper', False):
    renpy.input = _rpt_hint_input(renpy.input)
