"""Text-only translation hooks for KAG and KAGEX; never execute script code."""
import re

from kirikiri_tokens import protect, readable, validate as validate_controls


def row_config(cfg, row):
    return dict(cfg, _kirikiri_dialect=row.get('dialect', 'kag'),
                _kirikiri_usage=row.get('usage', row.get('kind')))


def ui_instruction(items,cfg):
    if cfg.get('engine')!='kirikiri' or not items or not all(item.get('usage')=='ui_label' for item in items):return ''
    return ('These are visual-novel interface controls, not story text. Interpret words in their UI function: '
            'message-window visibility, text-display speed and delays, saved-game/bookmark slots, '
            'screen size and keyboard bindings. '
            'In legacy KAG terminology, ノーウェイト means no waiting/instant text, not font weight; '
            '栞 is a saved-game bookmark, not a category: following it loads a save, inserting it saves the game. '
            'Hiding the message means hiding the dialogue window, not deleting its contents. '
            'Use concise Korean interface labels.')


def validate(source, target, cfg, *, language_details=None):
    if not isinstance(target, str):
        return ['translation is not a string']
    errors = validate_controls(source, target, cfg)
    if errors:
        return errors
    before, after = readable(source, cfg), readable(target, cfg)
    if re.search(r'<think>|</think>|```', after):
        errors.append('model commentary')
    if any(c.isalpha() for c in before) and not any(c.isalnum() for c in after):
        errors.append('punctuation-only translation')
    # Names may deliberately retain their original spelling. They are reviewed
    # separately, before dialogue; internal speaker identifiers never enter here.
    if cfg.get('_name_preparation') or cfg.get('_kirikiri_usage') == 'speaker_label':
        return errors
    names = cfg.get('_person_names', {})
    if before.strip() in names or before.strip() in names.values():
        return errors
    from japanese_output_policy import enabled as japanese_enabled, language_errors, small_tsu_punctuation_result
    if japanese_enabled(cfg):
        if small_tsu_punctuation_result(source,target,cfg):
            errors=[error for error in errors if error!='punctuation-only translation']
        errors.extend(language_errors(source,target,cfg,language_details))
    elif (not re.search('[가-힣]', after) and
            ((len(re.findall(r'[A-Za-z]+', before)) >= 5) or
             (cfg.get('source_language') in ('japanese', 'ja') and
              len(re.findall(r'[\u3041-\u30ff\u3400-\u9fff]', before)) >= 5))):
        errors.append('no Korean translation')
    return errors


def prepare_context(context, cfg):
    if not context.get('source_passage'):
        return context
    return dict(context, source_passage=[dict(item, text=readable(item['text'], cfg))
        if isinstance(item.get('text'), str) else item for item in context['source_passage']])


def retry_instruction(rows, cfg):
    protected = any(protect(row['source'], row_config(cfg, row))[1] for row in rows)
    return ('Translate each complete input into Korean. Return only its translation. '
            'Do not include surrounding context, explanations or new control codes.' +
            (' Preserve each supplied placeholder exactly once and in its original order.' if protected else ''))
