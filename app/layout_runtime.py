# Ren'Py style properties are write-only; never read say_window.ysize/minimum/maximum.
_rpt_layout_saved = False
_rpt_layout_gui_existed = False
_rpt_layout_gui_before = None
_rpt_layout_owner = None
_rpt_layout_block = None


def _rpt_layout_base(obj):
    # Inspect declared properties, including inherited ones, without building a style.
    visited = set()
    while obj is not None and id(obj) not in visited:
        visited.add(id(obj))
        value = None
        for props in getattr(obj, 'properties', []):
            if not isinstance(props, dict):
                continue
            if 'ysize' in props:
                value = props['ysize']
            if 'xysize' in props and isinstance(props['xysize'], (list, tuple)):
                value = props['xysize'][1]
            if 'yminimum' in props:
                value = props['yminimum']
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return value
        parent = getattr(obj, 'parent', None)
        obj = style.get(parent) if parent is not None else None
    return None


def _rpt_apply_layout(active):
    global _rpt_layout_saved, _rpt_layout_gui_existed, _rpt_layout_gui_before
    global _rpt_layout_owner, _rpt_layout_block
    if _rpt_layout_scale is None:
        return
    if _rpt_layout_owner is not None:
        # Remove only the exact property block added by this tool.
        _rpt_layout_owner.properties[:] = [p for p in _rpt_layout_owner.properties if p is not _rpt_layout_block]
        _rpt_layout_owner = None
        _rpt_layout_block = None
    if _rpt_layout_saved:
        if _rpt_layout_gui_existed:
            gui.textbox_height = _rpt_layout_gui_before
        elif hasattr(gui, 'textbox_height'):
            del gui.textbox_height
    if not active:
        return
    if not _rpt_layout_saved:
        _rpt_layout_gui_existed = hasattr(gui, 'textbox_height')
        _rpt_layout_gui_before = getattr(gui, 'textbox_height', None)
        _rpt_layout_saved = True
    owner = style.say_window
    base = _rpt_layout_gui_before
    if not isinstance(base, (int, float)) or isinstance(base, bool) or base <= 0:
        base = _rpt_layout_base(owner)
    if not isinstance(base, (int, float)) or isinstance(base, bool) or base <= 0:
        return
    # A float in 0..1 is a relative height; retain its unit.
    height = base * _rpt_layout_scale if isinstance(base, float) and base <= 1.0 else max(1, int(base * _rpt_layout_scale))
    gui.textbox_height = height
    owner.add_properties({'yminimum': height, 'ymaximum': height})
    _rpt_layout_owner = owner
    _rpt_layout_block = owner.properties[-1]
