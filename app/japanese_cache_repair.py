"""Read-only, deterministic repairs of eligible Japanese automatic cache entries."""
import json
from collections import OrderedDict

_TRACE_CACHE = OrderedDict()


def _sent_inputs(request, expected):
    """Verify whole source text, never treat a split reply as a full entry."""
    from numbered_protocol import parse
    for message in reversed(request.get('messages', [])):
        content = message.get('content')
        if not isinstance(content, str):
            continue
        try:
            payload = json.loads(content)
        except ValueError:
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get('items'), list):
            sent = {}
            for item in payload['items']:
                if not isinstance(item, dict) or not isinstance(item.get('text'), str):
                    return {}
                key = str(item.get('id'))
                if key in sent:
                    return {}
                sent[key] = item['text']
            return {key: text for key, text in sent.items()
                    if key in expected and text == expected[key]}
        _, separator, source = content.partition('\n[Source Text]\n')
        if separator:
            sent = parse(source, expected, allow_headerless=len(expected) == 1,
                         allow_multiline=True)
            return {key: text for key, text in sent.items()
                    if key in expected and text.strip() == expected[key].strip()}
    return {}


def _recovered(item, text, method, **metadata):
    value = dict(item, text=text, status='failure_recovered', error=None,
                 _japanese_offline_repair=method, **metadata)
    for key in ('_japanese_residual_language', '_japanese_residue_kind',
                '_rpg_residual_language'):
        value.pop(key, None)
    return value


def numeric_candidate(source, cfg):
    from japanese_output_policy import enabled
    if not enabled(cfg) or cfg.get('_name_preparation'):
        return None
    if any(cfg.get(key) in ('speaker_label', 'speaker', 'name', 'name_label')
           for key in ('_usage', '_kirikiri_usage', '_rpg_usage')):
        return None
    from rpgmaker_names import name_only
    from japanese_output_policy import _name_config
    if name_only(source, _name_config(cfg)):
        return None
    from japanese_numbers import normalize_source
    # The converter accepts only a complete numeric utterance. Control codes,
    # variables, units, prose and ambiguous readings are not stripped to fit it.
    return normalize_source(source)


def restore_kirikiri_layout(source, text, cfg, *, input_format=None):
    """Restore native controls, recovering missing layout breaks only."""
    from kirikiri_tokens import protect, restore, validate
    from kirikiri_ruby import prepare, strip_output_ruby
    from token_recovery import _normalize_tool_markers
    body = prepare(source, cfg)[0] if input_format == 'kirikiri_reading_v2' else source
    _, tokens = protect(body, cfg)
    value = _normalize_tool_markers(text)[0]
    if not validate(source, value, cfg):
        return value
    from kirikiri_layout import recover_linebreak_markers
    recovered = recover_linebreak_markers(body, value, tokens)
    if recovered is not None:
        value = recovered[0]
    value = restore(value, tokens, cfg)
    return strip_output_ruby(source, value, cfg)


def _trace_candidates(project, rows, ids, cfg):
    """Read saved single/batch replies without running a model or game code."""
    path = project / 'data/translation-requests.jsonl'
    if not ids or not path.is_file():
        return {}
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns, tuple(sorted(ids)),
           cfg.get('model'), bool(cfg.get('_reuse_previous_models')))
    if key in _TRACE_CACHE:
        return _TRACE_CACHE[key]
    from kirikiri_tokens import protect
    from kirikiri_translation import row_config
    from numbered_protocol import parse
    by_id = {row['id']: row for row in rows}
    requests, result = {}, {}
    with path.open(encoding='utf-8-sig') as stream:
        for line in stream:
            try:
                record = json.loads(line)
            except (ValueError, TypeError):
                continue  # An interrupted final diagnostic record is not cache data.
            request_id = record.get('request_id')
            if record.get('event') == 'request':
                items = record.get('items', [])
                selected = {str(i): item['id'] for i, item in enumerate(items)
                            if isinstance(item, dict) and item.get('id') in ids}
                if not selected:
                    continue
                request = record.get('request', {})
                if (request.get('model') != cfg.get('model') and
                        not (cfg.get('_reuse_completed') and cfg.get('_reuse_previous_models'))):
                    continue
                expected = {}
                for i, item in enumerate(items):
                    row = by_id.get(item.get('id')) if isinstance(item, dict) else None
                    if row is None:
                        break
                    expected[str(i)] = protect(row['source'], row_config(cfg, row))[0]
                else:
                    sent = _sent_inputs(request, expected)
                    selected = {wire_id: uid for wire_id, uid in selected.items() if wire_id in sent}
                    if selected:
                        requests[request_id] = (selected, sent)
                continue
            if record.get('event') != 'response' or request_id not in requests:
                continue
            selected, expected = requests.pop(request_id)
            response = record.get('response', {})
            if response.get('done_reason') not in ('stop', 'eos'):
                continue
            content = response.get('message', {}).get('content', '')
            parsed = parse(content, expected, allow_headerless=True, allow_multiline=True)
            for wire_id, uid in selected.items():
                if wire_id in parsed:
                    result.setdefault(uid, []).append((parsed[wire_id], request_id))
    _TRACE_CACHE[key] = result
    while len(_TRACE_CACHE) > 2:
        _TRACE_CACHE.popitem(last=False)
    return result


def repair(project, rows, known, cfg):
    """Project cache remains untouched; return repaired in-memory projections."""
    from japanese_output_policy import enabled, HANGUL
    if not enabled(cfg) or cfg.get('_skip_name_review'):
        return known
    from japanese_resume import exempt
    from japanese_numbers import compare
    from translation import validate_text
    from kirikiri_translation import row_config
    result = dict(known)
    layout_ids = set()
    by_id = {row['id']: row for row in rows}
    for uid, row in by_id.items():
        item = known.get(uid)
        if item is None:
            from translation import fingerprint
            item = dict(id=uid, source=row['source'], text=row['source'],
                        model=cfg.get('model'), fingerprint=fingerprint(cfg), status='source_fallback')
        if (not row or exempt(row, item) or item.get('source') != row['source'] or
                not isinstance(item.get('text'), str)):
            continue
        check_cfg = dict(cfg, _usage=row.get('usage', row.get('kind')))
        if cfg.get('engine') == 'kirikiri':
            check_cfg = row_config(check_cfg, row)
        if (item.get('status') in ('source_fallback', 'partial_translation') or
                not HANGUL.search(item['text'])):
            candidate = numeric_candidate(row['source'], check_cfg)
            if (candidate is not None and
                    (item.get('status') in ('source_fallback', 'partial_translation') or
                     compare(row['source'], item['text']) == 'mismatch') and
                    not validate_text(row['source'], candidate, check_cfg, check_link_content=True)):
                result[uid] = _recovered(item, candidate, 'numeric_value')
                continue
        if (cfg.get('engine') == 'kirikiri' and row.get('dialect') == 'kagex_psb' and
                item.get('status') == 'source_fallback' and
                any(code in str(item.get('error', '')) for code in
                    ('KiriKiri token mismatch', 'KiriKiri control code mismatch'))):
            from kirikiri_ruby import prepare
            if not prepare(row['source'], check_cfg)[2]:
                from kirikiri_tokens import protect
                if any(token in ('\n', '\r', '\r\n') for token in protect(row['source'], check_cfg)[1]):
                    layout_ids.add(uid)
    for uid, candidates in _trace_candidates(project, rows, layout_ids, cfg).items():
        row, item = by_id[uid], result[uid]
        check_cfg = row_config(cfg, row)
        for text, request_id in reversed(candidates):
            try:
                candidate = restore_kirikiri_layout(row['source'], text, check_cfg)
            except ValueError:
                continue
            if not validate_text(row['source'], candidate, check_cfg, check_link_content=True):
                result[uid] = _recovered(item, candidate, 'saved_linebreak', request_id=request_id)
                break
    return result
