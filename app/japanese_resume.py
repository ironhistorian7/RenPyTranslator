"""Keep Japanese residue available as a draft; never delete saved translations."""
from japanese_output_policy import enabled, prepare_output


def exempt(row, item):
    return (item.get('status') in ('manual_translation', 'name_review', 'ui_original',
                                   'ui_fixed', 'literal') or
            item.get('model') in ('reviewed override', 'literal format', 'literal whitespace') or
            row.get('usage') in ('speaker_label', 'speaker', 'name', 'name_label'))


def annotate(project, rows, known, cfg):
    """One in-memory pass; render-only callers never schedule model work."""
    if not enabled(cfg) or cfg.get('_skip_name_review'):
        return known
    if cfg.get('engine') == 'kirikiri':
        from name_review import configuration
        cfg = configuration(project, cfg, rows)
    elif cfg.get('engine') not in ('rpgmaker', 'unity'):
        from name_translation import mapping
        cfg = dict(cfg, _person_names=mapping(project))
    from japanese_cache_repair import repair
    result = repair(project, rows, known, cfg)
    for row in rows:
        uid = row['id']; item = result.get(uid)
        if not item or exempt(row, item) or item.get('status') == 'source_fallback':
            continue
        if item.get('source') != row['source'] or not isinstance(item.get('text'), str):
            continue
        check_cfg = dict(cfg, _usage=row.get('usage', row.get('kind')))
        if cfg.get('engine') == 'kirikiri':
            from kirikiri_translation import row_config
            check_cfg = row_config(check_cfg, row)
            from kirikiri_ruby import needs_retranslation, strip_output_ruby
            if needs_retranslation(row['source'], item['text'], check_cfg):
                item = dict(item, text=strip_output_ruby(row['source'], item['text'], check_cfg),
                            _japanese_ruby_pending=True)
            else:
                from kirikiri_ruby import REVISION
                if item.get('_japanese_ruby_revision') == REVISION:
                    item = dict(item)
                    item.pop('_japanese_ruby_pending', None)
        text, details = prepare_output(row['source'], item['text'], check_cfg)
        if text != item['text']:
            # Only small-tsu positions were removed. Names and controls have
            # identical bytes and nonempty visible content is retained.
            item = dict(item, text=text)
            details = dict(details, kind='clean', japanese_count=0, spans=[])
        item = dict(item)
        if details['kind'] not in ('clean', 'not_applicable'):
            item.update(_japanese_residual_language=True,
                        _japanese_residue_kind=details['kind'])
        else:
            item.pop('_japanese_residual_language', None)
            item.pop('_japanese_residue_kind', None)
            if (cfg.get('engine') != 'rpgmaker' and
                    item.get('status') == 'partial_translation' and
                    not item.get('_japanese_ruby_pending')):
                from translation import validate_text
                from token_recovery import has_unrestored_tool_marker
                if (not has_unrestored_tool_marker(row['source'], item['text']) and
                        not validate_text(row['source'], item['text'], check_cfg, check_link_content=True)):
                    item.update(status='failure_recovered', error=None)
        result[uid] = item
    return result


def pending(item):
    return bool(item.get('status') in ('source_fallback', 'partial_translation') or
                item.get('_japanese_residual_language') or item.get('_rpg_residual_language') or
                item.get('_japanese_ruby_pending'))


def _language_failure(item):
    """Accept only recorded classifier errors, excluding the model output."""
    import ast
    error = item.get('error')
    if not isinstance(error, str):
        return False
    reason = error.split('; output=', 1)[0]
    allowed = {'no Korean translation', 'untranslated Japanese text'}
    start = reason.find('[')
    if start >= 0:
        end = reason.find(']', start)
        if end < 0:
            return False
        try:
            errors = ast.literal_eval(reason[start:end + 1])
        except (ValueError, SyntaxError):
            return False
        if (not isinstance(errors, list) or not errors or
                any(not isinstance(value, str) or value not in allowed for value in errors)):
            return False
        # A correction may append another language error after the list.
        tail = reason[end + 1:].strip(' ;')
        return not tail or tail in allowed
    return reason.strip() in allowed


def clean_history_recoveries(rows, known, histories, cfg):
    """Recover an eligible earlier automatic success in memory, never write.

    histories maps IDs to chronological *cache-eligible* raw records, including
    the currently selected record. The caller must apply normal fingerprint,
    source and reuse eligibility before constructing these lists.
    """
    if not enabled(cfg) or cfg.get('_skip_name_review'):
        return known, []
    from japanese_output_policy import classify, HANGUL, _masked_tokens
    from token_recovery import has_unrestored_tool_marker
    from translation import validate_text
    result = dict(known)
    events = []
    for row in rows:
        uid = row['id']
        item = result.get(uid)
        if (not item or exempt(row, item) or item.get('source') != row['source'] or
                item.get('status') not in ('source_fallback', 'partial_translation') or
                not _language_failure(item)):
            continue
        history = histories.get(uid, ())
        if not history:
            continue
        current = history[-1]
        # An overlay, newer success or different selected source must win.
        if any(current.get(key) != item.get(key) for key in
               ('id', 'source', 'text', 'status', 'error', 'fingerprint', 'model')):
            continue
        check_cfg = dict(cfg, _usage=row.get('usage', row.get('kind')))
        if cfg.get('engine') == 'kirikiri':
            from kirikiri_translation import row_config
            check_cfg = row_config(check_cfg, row)
        for prior in reversed(history[:-1]):
            if (prior.get('id') != uid or prior.get('source') != row['source'] or
                    prior.get('status') not in (None, 'accepted', 'translated', 'failure_recovered') or
                    exempt(row, prior) or not isinstance(prior.get('text'), str)):
                continue
            if cfg.get('engine') == 'kirikiri':
                from kirikiri_ruby import prepare, REVISION
                if (prepare(row['source'], check_cfg)[2] and
                        prior.get('_japanese_ruby_revision') != REVISION and
                        prior.get('input_format') != 'kirikiri_reading_v2'):
                    continue
            details = classify(row['source'], prior['text'], check_cfg)
            if (details['kind'] != 'clean' or details['japanese_count'] or
                    not HANGUL.search(_masked_tokens(prior['text'], check_cfg))):
                continue
            if (has_unrestored_tool_marker(row['source'], prior['text']) or
                    validate_text(row['source'], prior['text'], check_cfg, check_link_content=True)):
                continue
            restored = dict(prior, status='failure_recovered', error=None)
            restored.pop('_japanese_residual_language', None)
            restored.pop('_rpg_residual_language', None)
            restored.pop('_japanese_residue_kind', None)
            result[uid] = restored
            events.append(dict(id=uid, before=item['text'], text=prior['text'],
                               source=row['source']))
            break
    return result, events
