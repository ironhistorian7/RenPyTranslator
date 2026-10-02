# Embedded in the additive patch. Python 2.7/3; never modify engine files.
import renpy as _rpt_size_engine

_rpt_size_version = tuple(_rpt_size_engine.version_tuple[:2])
_rpt_native_reference_size = ((_rpt_size_version[0] == 7 and _rpt_size_version[1] >= 6)
                              or _rpt_size_version >= (8, 1))


def _rpt_reference_sizes(tokens, text_style):
    # compose closes the Korean styles before the reference. Use the resolved
    # displayable style (say, centered, hovered choice, etc.), not gui.text_size.
    for kind, value in tokens:
        if kind == renpy.TEXT_TAG and value == 'size=_rpt_reference_55':
            value = ('size=*0.55' if _rpt_native_reference_size else
                     'size=' + str(max(1, int(text_style.size * .55))))
        yield kind, value


if not globals().get('_rpt_reference_size_ready', False):
    _rpt_reference_size_ready = True
    _rpt_previous_segment = _rpt_size_engine.text.text.Layout.segment
    def _rpt_size_segment(self, tokens, text_style, renders, text_displayable):
        return _rpt_previous_segment(self, _rpt_reference_sizes(tokens, text_style),
                                     text_style, renders, text_displayable)
    _rpt_size_engine.text.text.Layout.segment = _rpt_size_segment
