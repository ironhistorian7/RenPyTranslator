# Embedded in the patch. Python 2.7/3 compatible. No game state is changed.
def rpt_reference_name(value):
    # Reverse only a confirmed, unambiguous name value, never an entire sentence.
    candidates = [source for source, translated in globals().get('_rpt_name_map', {}).items() if translated == value]
    return candidates[0] if len(candidates) == 1 else value

def _rpt_keep_reference(tag, argument, contents):
    if argument == '0.55':
        return [(renpy.TEXT_TAG, 'size=_rpt_reference_55')] + contents + [(renpy.TEXT_TAG, '/size')]
    return contents

config.custom_text_tags['rpt_ref'] = _rpt_keep_reference
if not globals().get('_rpt_reference_ready', False):
    _rpt_reference_ready = True
    _rpt_previous_custom_tags = renpy.text.text.Text.apply_custom_tags

    def _rpt_expand_reference(part, apply_original):
        return apply_original(part)

    def _rpt_isolate_reference_replacement(replace_text, game_replace_text):
        from threading import local
        scope = local()

        def replace(value):
            if not getattr(scope, 'reference', False):
                return replace_text(value)
            return game_replace_text(value) if game_replace_text is not None else value

        def expand(part, apply_original):
            previous = getattr(scope, 'reference', False)
            scope.reference = True
            try:
                return apply_original(part)
            finally:
                scope.reference = previous

        config.replace_text = replace
        return expand

    # Installed after the RPT menu and name callbacks. Keep the game's own
    # callback and let Ren'Py process its registered tags in both text portions.
    if globals().get('_rpt_has_replace_text') and config.replace_text is not None:
        _rpt_expand_reference = _rpt_isolate_reference_replacement(config.replace_text, _rpt_previous_replace)

    def _rpt_reference_tokens(tokens, apply_original):
        def close_styles(part):
            # Custom tags have already been expanded by the engine, so a game
            # tag shadowing a built-in cannot be mistaken for an open style.
            paired = set(('a', 'b', 'i', 'u', 's', 'color', 'alpha', 'font',
                          'size', 'cps', 'k', 'outlinecolor', 'plain', 'rb',
                          'rt', 'art', 'alt', 'noalt'))
            stack = []
            for kind, value in part:
                if kind != renpy.TEXT_TAG:
                    continue
                name = value.lstrip('/').split('=', 1)[0]
                if name not in paired:
                    continue
                if value.startswith('/'):
                    if stack and stack[-1] == name:
                        stack.pop()
                else:
                    stack.append(name)
            return part + [(renpy.TEXT_TAG, '/' + name) for name in reversed(stack)]

        def apply_pending(part):
            # 6.99.0 predates config.replace_text. Apply our label/menu callback
            # here while original-reference tokens remain outside the callback.
            replacement = globals().get('_rpt_text_replacer')
            if replacement is not None:
                part = [(kind, replacement(value) if kind == renpy.TEXT_TEXT else value)
                        for kind, value in part]
            return apply_original(part)
        result = []
        pending = []
        reference = False
        sized_reference = False
        for kind, value in tokens:
            if kind == renpy.TEXT_TAG and value in ('rpt_ref', 'rpt_ref=0.55'):
                # Migrate the exact old tool wrapper, even if its size was wrong.
                # Other size tags in the game's text are left alone.
                if (value == 'rpt_ref' and len(pending) >= 2
                        and pending[-2][0] == renpy.TEXT_TAG
                        and pending[-2][1].startswith('size=')
                        and pending[-1] == (renpy.TEXT_TAG, 'cps=0')):
                    pending[-2] = (renpy.TEXT_TAG, 'size=_rpt_reference_55')
                expanded = apply_pending(pending)
                # Only the current wrapper uses runtime closing. Legacy
                # wrappers already contain their own size and cps delimiters.
                result.extend(close_styles(expanded) if value == 'rpt_ref=0.55' else expanded)
                pending = []
                reference = True
                sized_reference = value == 'rpt_ref=0.55'
                if sized_reference:
                    result.append((renpy.TEXT_TAG, 'size=_rpt_reference_55'))
            elif kind == renpy.TEXT_TAG and value == '/rpt_ref':
                if reference:
                    result.extend(_rpt_expand_reference(pending, apply_original))
                    pending = []
                if sized_reference:
                    result.append((renpy.TEXT_TAG, '/size'))
                reference = False
                sized_reference = False
            else:
                pending.append((kind, value))
        result.extend(_rpt_expand_reference(pending, apply_original) if reference else apply_pending(pending))
        return result
    # Ren'Py 7.3 uses an instance method; newer engines use a static method.
    if isinstance(vars(renpy.text.text.Text).get('apply_custom_tags'), staticmethod):
        def _rpt_custom_tags(tokens):
            return _rpt_reference_tokens(tokens, _rpt_previous_custom_tags)
        renpy.text.text.Text.apply_custom_tags = staticmethod(_rpt_custom_tags)
    else:
        def _rpt_custom_tags(self, tokens):
            return _rpt_reference_tokens(tokens, lambda part: _rpt_previous_custom_tags(self, part))
        renpy.text.text.Text.apply_custom_tags = _rpt_custom_tags
