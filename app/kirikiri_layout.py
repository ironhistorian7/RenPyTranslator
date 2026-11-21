"""Conservative recovery of omitted KAGEX layout newline placeholders."""
import re

from kirikiri_tokens import spans

_MARKER = re.compile(r'<rpt\d{3,}/>')
_LINEBREAKS = {'\n', '\r', '\r\n'}


def recover_linebreak_markers(source, raw_model_text, tokens):
    """Return (text, inserted token indices), or None if recovery is unsafe.

    Only omitted literal linebreak tokens can be inserted. Existing markers must
    be unique, known and in source order. Placement is approximate: an omitted
    linebreak is placed at a nearby whitespace/punctuation boundary within its
    surrounding surviving markers, or at that interval's end.
    """
    source_spans = list(spans(source, 'kagex_psb'))
    if [source[a:b] for a, b in source_spans] != list(tokens):
        return None
    expected = ['<rpt%03d/>' % i for i in range(len(tokens))]
    indices = {marker: i for i, marker in enumerate(expected)}
    matches = list(_MARKER.finditer(raw_model_text))
    if any(m.group() not in indices for m in matches):
        return None
    live = [indices[m.group()] for m in matches]
    if live != sorted(set(live)):
        return None
    missing = [i for i in range(len(tokens)) if i not in set(live)]
    if any(tokens[i] not in _LINEBREAKS for i in missing):
        return None
    if not missing:
        return raw_model_text, []

    # Source and output intervals are bounded by corresponding live markers.
    anchors = [(-1, 0, 0, 0, 0)]
    anchors += [(i, source_spans[i][0], source_spans[i][1], m.start(), m.end())
                for i, m in zip(live, matches)]
    anchors.append((len(tokens), len(source), len(source), len(raw_model_text), len(raw_model_text)))
    insertions = []
    for left, right in zip(anchors, anchors[1:]):
        omitted = range(left[0] + 1, right[0])
        if not omitted:
            continue
        src_start, src_end = left[2], right[1]
        out_start, out_end = left[4], right[3]
        interval = raw_model_text[out_start:out_end]
        # Never insert inside an unprotected control, escape, or marker.
        unsafe = set()
        for a, b in spans(interval, 'kagex_psb'):
            unsafe.update(range(a + 1, b))
        candidates = [p for p in range(1, len(interval))
                      if p not in unsafe and (interval[p - 1].isspace() or
                          interval[p - 1] in '.,!?;:。！？、，；：…)]）】」』')]
        previous = 0
        for i in omitted:
            a, b = source_spans[i]
            if a == src_start:
                pos = 0
            elif b == src_end:
                pos = len(interval)
            else:
                ratio = (a - src_start) / max(1, src_end - src_start)
                viable = [p for p in candidates if p >= previous]
                pos = min(viable, key=lambda p: (abs(p - ratio * len(interval)), p)) if viable else len(interval)
            pos = max(previous, pos)
            previous = pos
            insertions.append((out_start + pos, i))
    result = raw_model_text
    for pos, i in reversed(insertions):
        result = result[:pos] + expected[i] + result[pos:]
    return result, missing
