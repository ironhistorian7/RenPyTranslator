"""Source-backed label purposes, minimal context and obvious expansion checks.

No inference, game execution, length caps, or edits to saved translations.
"""
import json
from pathlib import Path
import re

LABEL_USES = frozenset(('speaker_label', 'ui_label'))


def annotate(rows, structure=None, policy=None, hints=None):
    structure=structure or {};policy=policy or {};hints=hints or {}
    characters={s for s in structure.get('characters',{}).values()
                if isinstance(s,str) and s!='(dynamic or unnamed)'}
    characters.update(r['speaker_name'] for r in rows if r.get('speaker_name'))
    choices=set(policy.get('choices',[])) | set(hints.get('choices',[]))
    choices.update(s for label in structure.get('labels',{}).values() for s in label.get('choices',[]))
    controls={r['source'] for r in structure.get('screen_literals',[])
              if r.get('widget') in ('textbutton','label')}
    menu=policy.get('menu',{})
    result=[]
    for original in rows:
        row=dict(original);source=row['source']
        if row.get('kind')=='dialogue':
            row['usage']='dialogue'
        elif source in choices:
            row.update(usage='choice',usage_evidence='source menu choice')
        elif source in characters:
            row.update(usage='speaker_label',usage_evidence='Character definition or quoted speaker')
        elif source in controls or source in menu:
            row.update(usage='ui_label',usage_evidence='screen control or menu policy')
        result.append(row)
    return result


def for_project(project,rows):
    def read(name):
        path=Path(project)/'data'/name
        return json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}
    return annotate(rows,read('source-outline.json'),read('ui-policy.json'),read('name-hints.json'))


def label_batch(rows):
    return bool(rows) and all(r.get('usage') in LABEL_USES for r in rows)


def context_for(rows,context):
    if not label_batch(rows):return dict(context),{'mode':'story_or_text'}
    # Keep spellings only, not narrative explanations of the terms themselves.
    terms=[]
    from source_language import occurrences
    for term in context.get('translation_guidance',{}).get('terms',[]):
        source=term.get('source')
        if isinstance(source,str) and any(occurrences(r['source'].casefold(),source.casefold()) for r in rows):
            terms.append({k:term[k] for k in ('source','target') if k in term})
    result={'translation_guidance':{'terms':terms}} if terms else {}
    for key in ('variable_identities','person_name_spellings'):
        if context.get(key):result[key]=context[key]
    audit=dict(context.get('_context_audit',{}))
    if audit:
        # The file hashes reflect what is actually retained in this request.
        files=audit.get('files',{})
        audit.update(scope='',files={k:v for k,v in files.items() if k=='terms.txt' and terms},preselection_dropped=[])
        result['_context_audit']=audit
    excluded=[k for k,v in context.items() if k not in ('_context_audit','translation_guidance','variable_identities','person_name_spellings') and v]
    excluded += ['translation_guidance.'+k for k,v in context.get('translation_guidance',{}).items() if k!='terms' and v]
    return result,{'mode':'labels_only','excluded_story_parts':excluded,'term_spellings':len(terms)}


def expansion_errors(row,target,cfg=None):
    """Reject additions of dialogue, not long names or natural label wording."""
    if row.get('usage') not in LABEL_USES:return []
    visible=lambda s:re.sub(r'\{[^{}]*\}|\[[^\[\]\n]*\]','',s).strip()
    source=visible(row['source']);text=visible(target)
    if '\n' in text and '\n' not in source:
        return ['label expanded into dialogue or explanation (added line break)']
    if not any(q in source for q in ('"','“','”','「','」','『','』')) and any(q in text for q in ('"','“','”','「','」','『','』')):
        return ['label expanded into dialogue or explanation (added quotation)']
    # Sentence-like source captions may legitimately translate as sentences.
    if re.search(r'[.!?。？！]\s*$',source):return []
    # Do not reject command labels such as 계속하세요, or names with colons.
    if re.search(r'(?:습니다|습니까|입니다|이에요|예요|어요|아요|합니다)[.!?。？！]$',text):
        if row.get('usage')=='ui_label' and cfg is not None:
            # Unity UI strings can be complete captions even when the source
            # omitted terminal punctuation. Do not excuse added speech, quotes,
            # line breaks, or multiple sentences under this exception.
            if (cfg.get('engine')=='unity' and re.search(r'[가-힣]', text)
                    and re.fullmatch(r'[^.!?。？！\r\n]+[.!?。？！]', text)):
                return []
            from rpgmaker_output_policy import enabled
            if enabled(cfg):return []
        return ['label expanded into dialogue or explanation (added sentence)']
    return []


def unchanged_choice_errors(row,target,cfg=None):
    """An unchanged natural-language choice is pending, not a translated name."""
    cfg=cfg or {}
    if (cfg.get('_name_preparation') or row.get('usage')!='choice'
            or cfg.get('source_language','english') not in ('english','en')
            or target!=row['source']):return []
    from rpgmaker_names import name_only
    names=cfg.get('_person_names',{})
    if row['source'].strip() in names or row['source'].strip() in names.values() or name_only(row['source'],cfg):return []
    visible=re.sub(r'\{[^{}]*\}|\[[^\[\]\n]*\]|<[^<>]*>','',row['source'])
    if re.search(r'https?://|[\\/]',visible):return []
    return ['no Korean translation'] if len(re.findall(r'[A-Za-z]{2,}',visible))>=2 else []


def automatic_english_errors(row,target,cfg=None):
    """Catch English prose left without Korean in automatic row validation.

    Restrict this inference to dialogue and choices: unknown labels may be
    names. Confirmed names and technical strings are not evidence of prose.
    """
    cfg=cfg or {}
    if (cfg.get('_name_preparation') or cfg.get('source_language','english') not in ('english','en')
            or row.get('usage') not in ('dialogue','choice') or not isinstance(target,str)):
        return []
    source=row.get('source','')
    if not isinstance(source,str):return []
    from rpgmaker_names import name_only
    names=cfg.get('_person_names',{}) or {}
    if source.strip() in names or source.strip() in names.values() or name_only(source,cfg):return []

    def visible(text):
        text=re.sub(r'\\[A-Za-z]+(?:\[-?\d+\])?|\\[.!|^><{}]', '', text)
        text=re.sub(r'\{[^{}]*\}|\[[^\[\]\n]*\]|<[^<>]*>', '', text)
        return re.sub(r'%(?:\([^)]+\))?[-+#0-9.]*[a-zA-Z]', '', text)

    source=visible(source);target=visible(target)
    if re.search(r'[\uac00-\ud7a3\u1100-\u11ff\u3130-\u318f]',target):return []
    # URLs, filesystem paths and program identifiers need no translation.
    if re.search(r'https?://|[\\/]|\b\w+_\w+\b|(?:\w+\.)+\w+\b',source,re.I):return []
    words=re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?",source)
    if len(words)<2 or all(word.isupper() for word in words):return []
    return ['no Korean translation']
