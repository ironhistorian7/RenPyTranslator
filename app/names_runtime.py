# Embedded in Ren'Py. Python 2.7/3 compatible. No inference or variable writes.
try:
    _rpt_name_string_types = (basestring,)
except NameError:
    _rpt_name_string_types = (str,)


def rpt_display_name(value, variable=None):
    if _preferences.language != _rpt_name_language or not isinstance(value, _rpt_name_string_types):
        return value
    info=_rpt_name_inputs.get('variables',{}).get(variable,{})
    if value in info.get('values',{}):return info['values'][value]
    if info and info.get('kind')!='name':return value
    if value in _rpt_name_map:
        return _rpt_name_map[value]
    return _rpt_name_folded.get(value.lower(), value)


_rpt_name_folded = {}
_rpt_name_conflicts = set()
for _rpt_name_original, _rpt_name_translated in _rpt_name_map.items():
    _rpt_name_key = _rpt_name_original.lower()
    if _rpt_name_key in _rpt_name_folded and _rpt_name_folded[_rpt_name_key] != _rpt_name_translated:
        _rpt_name_conflicts.add(_rpt_name_key)
    _rpt_name_folded[_rpt_name_key] = _rpt_name_translated
for _rpt_name_key in _rpt_name_conflicts:
    _rpt_name_folded.pop(_rpt_name_key, None)


def _rpt_name_labels(previous):
    def wrapped(s):
        # Only whole name labels. Sentence variables use rpt_display_name explicitly.
        if _preferences.language == _rpt_name_language and s in _rpt_name_map:
            return _rpt_name_map[s]
        return previous(s) if previous is not None else s
    wrapped._rpt_name_label_wrapper = True
    return wrapped
if not getattr(config.replace_text, '_rpt_name_label_wrapper', False):
    config.replace_text = _rpt_name_labels(config.replace_text)


def _rpt_name_input_entry(prompt):
    entries = _rpt_name_inputs.get('inputs', [])
    get_location = getattr(renpy, 'get_filename_line', None)
    if get_location is not None:
        filename, line = get_location()
        filename = (filename or '').replace('\\', '/')
        def matches(e):
            if not (filename == e['file'] or filename.endswith('/' + e['file'])):
                return False
            if e['line'] <= line <= e.get('end_line', e['line']):
                return True
            # A Python block reports its statement location, not the assignment.
            # Match the prompt too, so a password input in the same block is left alone.
            return (line == e.get('statement_line') and e.get('prompt') and
                    prompt in (e['prompt'], _rpt_name_inputs.get('prompts', {}).get(e['prompt'])))
        found = [e for e in entries if matches(e)]
        # A real location takes precedence; do not match another input by its text.
        return found[0] if len(found) == 1 else None
    found = [e for e in entries if e.get('prompt') and prompt == e['prompt']]
    return found[0] if len(found) == 1 else None


def _rpt_name_input(previous):
    def wrapped(prompt, *args, **kwargs):
        if _preferences.language != _rpt_name_language:
            return previous(prompt, *args, **kwargs)
        entry = _rpt_name_input_entry(prompt)
        if entry is None:
            return previous(prompt, *args, **kwargs)
        # Do not replace an answer hint already attached to this prompt.
        if prompt == entry.get('prompt'):
            prompt = _rpt_name_inputs.get('prompts', {}).get(prompt, prompt)
        original = args[0] if args else kwargs.get('default', u'')
        kind=entry.get('kind','name')
        if kind=='answer':display=original
        elif original==entry.get('default') and entry.get('translated_default'):
            display=entry['translated_default']
        elif kind=='name':display=rpt_display_name(original,entry.get('variable'))
        else:display=original
        if display == original:
            return previous(prompt, *args, **kwargs)
        if args:
            args = (display,) + args[1:]
        else:
            kwargs = dict(kwargs, default=display)
        # ASCII-only allow/exclude filters can reject a localized default. Keep
        # such game-specific constraints intact and use the original default.
        # Ren'Py 7/8: prompt, default, allow, exclude, length.
        allow = args[1] if len(args) > 1 else kwargs.get('allow')
        exclude = args[2] if len(args) > 2 else kwargs.get('exclude', u'{}')
        length = args[3] if len(args) > 3 else kwargs.get('length')
        if ((length is not None and len(display) > length) or
                (allow is not None and any(c not in allow for c in display)) or
                (exclude is not None and any(c in exclude for c in display))):
            if args: args = (original,) + args[1:]
            else: kwargs = dict(kwargs, default=original)
            return previous(prompt, *args, **kwargs)
        result = previous(prompt, *args, **kwargs)
        # Accepting the displayed default preserves the game's original value;
        # any different player input is returned exactly as entered.
        return original if result == display else result
    wrapped._rpt_name_input_wrapper = True
    return wrapped


if not getattr(renpy.input, '_rpt_name_input_wrapper', False):
    renpy.input = _rpt_name_input(renpy.input)


# Older engines resolve interpolation fields as variable paths, not Python
# expressions. Handle only the three calls emitted by our display assembler.
# Leave parsing, escaping, conversions, scope and all other fields to Ren'Py.
# This also covers existing patches and newer engines with expressions disabled.
import re as _rpt_name_re
from renpy.substitutions import formatter as _rpt_name_formatter

_rpt_name_call = _rpt_name_re.compile(
    r'^(rpt_display_name|rpt_reference_name)\((.+)\)$')
_rpt_variable_arg = _rpt_name_re.compile(r'''^(.*),\s*u?(['"])([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\2$''')
_rpt_particle_call = _rpt_name_re.compile(
    u"^rpt_josa\\((.+),\\s*u?(['\"])(은/는|이/가|을/를|과/와|으로/로)\\2\\)$")


def _rpt_name_fields(previous):
    # Some intermediate engines carry (value, scope) into convert_field.
    # Probe the formatter itself, preserving that contract without a version guess.
    marker = object()
    probe_scope = {'_rpt_probe': marker}
    probe, unused = previous('_rpt_probe', (), probe_scope)
    scoped = isinstance(probe, tuple) and len(probe) == 2 and probe[0] is marker and probe[1] is probe_scope
    def resolve(expression, args, kwargs, function, extra=()):
        value, used = previous(expression, args, kwargs)
        scope = value[1] if scoped else None
        value = value[0] if scoped else value
        result = function(value, *extra)
        return ((result, scope) if scoped else result), used
    def wrapped(field, args, kwargs):
        match = _rpt_name_call.match(field)
        if match is not None:
            expression=match.group(2);extra=()
            context=_rpt_variable_arg.match(expression)
            if context and match.group(1)=='rpt_display_name':
                expression=context.group(1);extra=(context.group(3),)
            return resolve(expression, args, kwargs, globals()[match.group(1)], extra)
        # The optional final variable key selects the same address/default map
        # for both name display and particles on engines without expressions.
        particle_field=field;extra=()
        if field.startswith('rpt_josa(') and field.endswith(')'):
            context=_rpt_variable_arg.match(field[9:-1])
            if context:
                particle_field='rpt_josa('+context.group(1)+')';extra=(context.group(3),)
        match = _rpt_particle_call.match(particle_field)
        if match is not None:
            return resolve(match.group(1), args, kwargs, rpt_josa, (match.group(3),)+extra)
        return previous(field, args, kwargs)
    wrapped._rpt_name_fields_wrapper = True
    return wrapped


if not getattr(_rpt_name_formatter.get_field, '_rpt_name_fields_wrapper', False):
    _rpt_name_formatter.get_field = _rpt_name_fields(_rpt_name_formatter.get_field)
