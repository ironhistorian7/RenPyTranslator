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
    def _rpt_reference_tokens(tokens, apply_original):
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
                result.extend(apply_original(pending))
                pending = []
                reference = True
                sized_reference = value == 'rpt_ref=0.55'
                if sized_reference:
                    result.append((renpy.TEXT_TAG, 'size=_rpt_reference_55'))
            elif kind == renpy.TEXT_TAG and value == '/rpt_ref':
                if sized_reference:
                    result.append((renpy.TEXT_TAG, '/size'))
                reference = False
                sized_reference = False
            elif reference:
                # Reference tokens bypass every global text/name replacement.
                result.append((kind, value))
            else:
                pending.append((kind, value))
        result.extend(apply_original(pending))
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
