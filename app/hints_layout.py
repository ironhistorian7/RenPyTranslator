# Embedded in Ren'Py 7/8 as well as imported by synthetic tests. Python 2.7 syntax.
import re as _rpt_layout_re


def _rpt_hint_priority(effect):
    text = effect['text']
    if any(word in text for word in (u'닫힘', u'차단', u'열림', u'해금')):
        return 0
    if _rpt_layout_re.search(r'[+-]\d', text):
        return 1
    if effect.get('kind') == 'scene' or text.startswith(u'장면:'):
        return 2
    return 3


def _rpt_hint_compact(effect):
    text = effect['text']
    # Keep the qualification, not the potentially enormous boolean expression.
    if text.endswith(u'일 때)') and u' (' in text:
        return text.split(u' (', 1)[0] + u' (조건부)'
    return text


def _rpt_hint_geometry(captions, effects, width, height, size, measure, decorate, insert):
    """Measure actual text wrapping. The two viewports never overlap.

    measure(text, width, size) is supplied by Ren'Py, not a character-count guess.
    Long base choices remain intact and reachable through the choices viewport.
    """
    margin = max(8, int(min(width, height) * .04))
    gap = max(6, int(size * .4))
    padding = max(5, int(size * .25))
    scrollbar = max(12, int(size * .6))
    area_w = max(80, width - margin * 2)
    area_h = max(80, height - margin * 2)
    hint_size = max(14, int(size * .8))
    ordered = [sorted(row, key=_rpt_hint_priority) for row in effects]
    full = [insert(caption, u' '.join(decorate(e, e['text'], hint_size) for e in row)) if row else caption
            for caption, row in zip(captions, ordered)]
    full_width = max(40, area_w - padding * 2 - scrollbar)

    def heights(texts, text_width):
        return [max(size + padding * 2, int(measure(text, text_width, size)) + padding * 2) for text in texts]

    def total(values):
        return sum(values) + gap * max(0, len(values) - 1)

    full_heights = heights(full, full_width)
    panel = None
    list_rect = (margin, margin, area_w, area_h)
    texts = full
    row_heights = full_heights
    more = [False] * len(captions)
    if total(full_heights) > area_h:
        # Wide displays put details beside choices; narrow displays use a lower pane.
        if area_w >= size * 38:
            panel_w = int(area_w * .36)
            list_w = area_w - panel_w - gap
            list_rect = (margin, margin, list_w, area_h)
            panel = (margin + list_w + gap, margin, panel_w, area_h)
        else:
            panel_h = max(size * 3, int(area_h * .27))
            panel_h = min(panel_h, int(area_h * .4))
            list_h = area_h - panel_h - gap
            list_rect = (margin, margin, area_w, list_h)
            panel = (margin, margin + list_h + gap, area_w, panel_h)
        text_width = max(40, list_rect[2] - padding * 2 - scrollbar)
        texts = list(captions)
        row_heights = heights(texts, text_width)
        chosen = [[] for caption in captions]
        # Round-robin by priority stops one verbose option consuming the whole menu.
        candidates = sorted(((_rpt_hint_priority(e), j, i, e) for i, row in enumerate(ordered)
                             for j, e in enumerate(row)), key=lambda v: v[:3])
        for priority, j, i, effect in candidates:
            candidate = chosen[i] + [decorate(effect, _rpt_hint_compact(effect), hint_size)]
            rendered = insert(captions[i], u' '.join(candidate))
            new_height = max(size + padding * 2, int(measure(rendered, text_width, size)) + padding * 2)
            if total(row_heights) - row_heights[i] + new_height <= list_rect[3]:
                chosen[i] = candidate
                texts[i] = rendered
                row_heights[i] = new_height
        more = [len(chosen[i]) < len(ordered[i]) or any(_rpt_hint_compact(e) != e['text'] for e in ordered[i])
                for i in range(len(captions))]
    else:
        # Small menus retain a central placement instead of moving to the top edge.
        list_rect = (margin, margin + max(0, (area_h - total(row_heights)) // 2), area_w, total(row_heights))
    text_width = max(40, list_rect[2] - padding * 2 - scrollbar)
    offset = 0
    rows = []
    for i, text in enumerate(texts):
        rows.append({'caption': text, 'height': row_heights[i], 'y': offset, 'more': more[i]})
        offset += row_heights[i] + gap
    return {'rows': rows, 'list': list_rect, 'panel': panel, 'padding': padding,
            'gap': gap, 'scrollbar': scrollbar, 'text_width': text_width,
            'content_height': total(row_heights), 'size': size, 'hint_size': hint_size}
