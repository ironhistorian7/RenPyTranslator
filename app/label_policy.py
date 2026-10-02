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
    for term in context.get('translation_guidance',{}).get('terms',[]):
        source=term.get('source')
        if isinstance(source,str) and any(re.search(r'(?<!\w)'+re.escape(source)+r'(?!\w)',r['source'],re.I) for r in rows):
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


def expansion_errors(row,target):
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
        return ['label expanded into dialogue or explanation (added sentence)']
    return []
