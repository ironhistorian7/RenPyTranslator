"""Local candidate classification; no scripts, inference or filesystem access."""
import re

from source_language import normalize
from translation import FORMATS, HANGUL, TOKENS, validate_text


def visible(text):
    return ' '.join(FORMATS.sub('', TOKENS.sub('', text)).split())


def names_from(rows, cfg, spellings=()):
    names=set()
    for row in rows:
        if row.get('speaker_name'):names.add(visible(row['speaker_name']).casefold())
        if row.get('usage') in ('speaker_label','name','name_label'):
            names.add(visible(row['source']).casefold())
    for value in spellings:
        if isinstance(value,str):names.add(visible(value).casefold())
    for key,info in cfg.get('_names',{}).items():
        if isinstance(info,dict):
            for field in ('representative','default','source'):
                value=info.get(field)
                if isinstance(value,str):names.add(visible(value).casefold())
        elif isinstance(info,str):names.add(visible(info).casefold())
    for value in cfg.get('_person_names',{}):
        if isinstance(value,str):names.add(visible(value).casefold())
    return names


def nonlanguage_reference(text):
    """URLs, paths and identifier keys can legitimately contain English words."""
    if re.fullmatch(r'(?i)(?:(?:https?|ftp)://|www\.|mailto:)\S+',text):return 'url'
    if (re.match(r'(?i)[a-z]:[\\/]',text) or text.startswith(('\\','/')) or
            (not any(c.isspace() for c in text) and
             (text.startswith(('./','../')) or re.search(r'\S[\\/]\S',text)))):
        return 'path'
    if re.fullmatch(r'[A-Za-z_$][A-Za-z0-9_$]*(?:[._/-][A-Za-z0-9_$]+)+',text):
        return 'code_key'
    return None


def intentional(row, entry, ui):
    """Explicit policy takes priority over broad language-quality checks."""
    from ui_policy import standard
    source=row['source']
    if entry and entry.get('model')=='reviewed override':return 'reviewed_override'
    if (row['kind']=='string' and source not in ui.get('choices',[]) and
            (source in ui.get('menu',{}) or
             (row.get('file','').endswith('common.rpy') and
              standard(source,ui.get('source_language','english')) is not None))):
        return 'menu_policy'
    if entry and (entry.get('status') in ('ui_original','ui_fixed','literal') or
                  entry.get('model') in ('literal format','literal whitespace')):
        return 'literal_or_policy'
    bare=visible(source)
    if not any(c.isalpha() for c in bare):return 'nonlinguistic'
    return nonlanguage_reference(bare)


def is_name(row, names):
    return (row.get('usage') in ('speaker_label','name','name_label') or
            visible(row['source']).casefold() in names)


def unchanged_english(row, entry, ui, names):
    usage=row.get('usage',row['kind'])
    story=(usage in ('dialogue','choice') or
           (usage==row['kind']=='string' and row['source'] in ui.get('choices',[])))
    if not story or is_name(row,names):return False
    target=entry.get('text')
    if not isinstance(target,str):return False
    visible_target=visible(target)
    if HANGUL.search(visible_target):return False
    source=visible(row['source'])
    if source!=visible_target:return False
    words=re.findall(r"[A-Za-z]+(?:['’-][A-Za-z]+)*",source)
    return len(words)>=2 and any(word[0].islower() for word in words)


def classify(row, entry, cfg, ui, names=(), *, indexed=False):
    """Return only candidate/status diagnostics; never emit source/output text."""
    if entry is not None and entry.get('source')!=row['source']:
        return {'excluded':'cache_source_mismatch'}
    reason=intentional(row,entry,ui)
    if reason:return {'excluded':reason}
    if entry is None:
        if not indexed and is_name(row,names):return {'excluded':'name'}
        return {'origin':'missing_cache','errors':[]}
    if (not indexed and entry.get('status') not in ('source_fallback','failure_recovered') and
            is_name(row,names)):
        return {'excluded':'name'}
    text=entry.get('text')
    errors=validate_text(row['source'],text,cfg,check_link_content=True) if isinstance(text,str) else ['translation is not a string']
    if entry.get('status')=='source_fallback':return {'origin':'source_fallback','errors':errors}
    if errors:return {'origin':'validation_error','errors':errors}
    if normalize(cfg.get('source_language'))=='english' and unchanged_english(row,entry,ui,names):
        return {'origin':'unchanged_english','errors':[]}
    if entry.get('status')=='failure_recovered':
        body=visible(text)
        if any(c.isalpha() for c in body) and not HANGUL.search(body):
            return {'origin':'validation_error','errors':['no Korean translation']}
        return {'origin':'saved_recovery','errors':[]}
    if isinstance(text,str) and not HANGUL.search(text) and visible(row['source'])==visible(text):
        return {'excluded':'short_or_nonstory_original'}
    return {}
