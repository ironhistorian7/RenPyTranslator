# Embedded in the additive Ren'Py patch. Keep compatible with Python 2.7 and 3.
_rpt_groups = {}
def _rpt_optional_config(name, default=None):
    try:
        return getattr(config, name)
    except AttributeError:
        return default
    except Exception as error:
        # Older Config proxies raise Exception, not AttributeError, for absent keys.
        if str(error) == 'config.%s is not a known configuration variable.' % name:
            return default
        raise

_rpt_font_transforms = _rpt_optional_config('font_transforms')
_rpt_font_aliases = _rpt_optional_config('font_name_map')
_rpt_old_transform = getattr(preferences, 'font_transform', None)
_rpt_previous_transform = (_rpt_font_transforms or {}).get(_rpt_old_transform)
_rpt_old_aliases = dict(_rpt_font_aliases or {})
_rpt_old_styles = []

def _rpt_font_group(original):
    try:
        return _rpt_groups[original]
    except (KeyError, TypeError):
        pass
    if not isinstance(original, (basestring, FontGroup)):
        return original
    if isinstance(original,basestring) and original.startswith('tl/' + _rpt_language + '/fonts/'):
        return original
    kind = _rpt_fontdata['fonts'].get(original, _rpt_fontdata['fonts'].get('fonts/' + original)) if isinstance(original,basestring) else 'regular'
    name = original.lower().replace(' ', '') if isinstance(original,basestring) else ''
    if kind == 'icon' or any(word in name for word in ('fontawesome', 'materialicons', 'materialsymbols', 'fontello', 'icomoon', 'icofont', 'emoji')):
        return original
    if kind is None:
        kind = 'hand' if any(word in name for word in ('handwriting', 'handwritten', 'cursive', 'brushscript', 'permanentmarker')) else 'regular'
    group = FontGroup()
    for start, end in _rpt_fontdata['coverage'][kind]:
        group.add(_rpt_fontdata['preferred'][kind], start, end)
    for start, end in ((0x1100, 0x11ff), (0x3130, 0x318f), (0xa960, 0xa97f), (0xac00, 0xd7ff)):
        group.add(_rpt_fontdata['fallback'], start, end)
    # Preserve supported Japanese glyphs in the original font. Fill only known gaps.
    missing = _rpt_fontdata.get('japanese_missing', {})
    if isinstance(original,basestring):
        for start, end in missing.get(original, missing.get('fonts/' + original, [])):
            group.add(_rpt_fontdata['fallback'], start, end)
    # Latin, punctuation and game-specific symbols retain the original font.
    try:
        group.add(original, None, None)
    except TypeError:
        group.add(original, 0, 0x10ffff)
    _rpt_groups[original] = group
    return group

def _rpt_transform(original):
    if _rpt_previous_transform is not None:
        original = _rpt_previous_transform(original)
    if _preferences.language != _rpt_language:
        return original
    return _rpt_font_group(original)

def _rpt_language_fonts():
    if _rpt_font_transforms is not None:
        preferences.font_transform = 'rpt_korean' if _preferences.language == _rpt_language else _rpt_old_transform
        return
    # Older engines: replace concrete style fonts and inline font aliases.
    if _preferences.language != _rpt_language:
        for obj, original in _rpt_old_styles:
            obj.font = original
        if _rpt_font_aliases is not None:
            config.font_name_map = dict(_rpt_old_aliases)
        return
    for obj, original in _rpt_old_styles:
        obj.font = _rpt_font_group(original)
    if _rpt_font_aliases is not None:
        for original in _rpt_fontdata['fonts']:
            config.font_name_map[original] = _rpt_font_group(original)

if _rpt_font_transforms is not None:
    _rpt_font_transforms['rpt_korean'] = _rpt_transform
else:
    # Styles with an explicit font are the source of the disappearing Hangul bug.
    for _rpt_style in list(renpy.style.styles.values()):
        try:
            _rpt_value = _rpt_style.font
            if isinstance(_rpt_value, basestring):
                _rpt_old_styles.append((_rpt_style, _rpt_value))
        except (AttributeError, TypeError):
            pass

_rpt_regular = _rpt_fontdata['preferred']['regular']
config.font_replacement_map[(_rpt_regular, True, False)] = (_rpt_fontdata['bold'], False, False)
config.font_replacement_map[(_rpt_regular, True, True)] = (_rpt_fontdata['bold'], False, True)

def _rpt_wrap_menu(previous):
    def wrapped(items, *args, **kwargs):
        if _preferences.language == _rpt_language:
            items = [(_rpt_choices.get(label, label), value) for label, value in items]
        return previous(items, *args, **kwargs)
    return wrapped

# Only story-menu calls receive bilingual captions. UI strings remain single-line.
menu = _rpt_wrap_menu(menu)
if 'nvl_menu' in globals():
    nvl_menu = _rpt_wrap_menu(nvl_menu)
