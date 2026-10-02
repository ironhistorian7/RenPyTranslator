"""Offline, non-cascading edits to visible translated text only."""
import json
import re
from collections import Counter
from engine import save_json

PROTECTED = re.compile(
    r'\{\{|\[\[|\{[^{}]*\}|\[[^\[\]\n]*\]|'
    r'%(?:\([^)]+\))?[#0 +\-]*\d*(?:\.\d+)?[A-Za-z%]|'
    r'https?://[^\s<>]+|'
    r'(?:[A-Za-z]:)?(?:[^\s{}\[\]"<>]+[/\\])+[^\s{}\[\]"<>]*|'
    r'[^\s{}\[\]"<>/\\]+\.(?:png|jpg|jpeg|webp|ogg|mp3|wav|rpyc?|ttf|otf|webm|mp4)\b', re.I)

def load_rules(path):
    if not path.exists():
        return {}
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data,dict) or set(data)!={'replacements'} or not isinstance(data['replacements'],dict):
        raise ValueError('replacements.json must contain {"replacements": {"old": "new"}}')
    for old,new in data['replacements'].items():
        if not isinstance(old,str) or not isinstance(new,str) or not old or not new.strip():
            raise ValueError('Replacement keys and values must be nonempty strings')
        if any(c in old+new for c in '{}[]%\r\n'):
            raise ValueError('Replacement rules cannot contain markup, variables, format fields or newlines')
    return {k:v for k,v in data['replacements'].items() if k!=v}

def replace_visible(text,rules):
    counts=Counter()
    if not rules:return text,counts
    pattern=re.compile('|'.join(re.escape(k) for k in sorted(rules,key=len,reverse=True)))
    def visible(part):
        def change(m):
            counts[m.group()]+=1
            return rules[m.group()]
        return pattern.sub(change,part)
    parts=[];pos=0
    for m in PROTECTED.finditer(text):
        parts.extend((visible(text[pos:m.start()]),m.group()));pos=m.end()
    parts.append(visible(text[pos:]))
    return ''.join(parts),counts

def effective_cache(project,cfg,report=False,apply_ui=True):
    from translation import cache, read_catalog, validate_entry
    rules=load_rules(project/'replacements.json')
    known=cache(project,cfg);counts=Counter();changes=[]
    rows=read_catalog(project)
    if cfg.get('_render_ids') is not None:rows=[r for r in rows if r['id'] in cfg['_render_ids']]
    for row in rows:
        if row['id'] not in known:continue
        before=known[row['id']]['text'];after,hits=replace_visible(before,rules)
        if after==before:continue
        errors=validate_entry(row['source'],dict(known[row['id']],text=after),cfg)
        if errors:raise ValueError(f'Replacement invalidated {row["id"]}: {errors}')
        known[row['id']]=dict(known[row['id']],text=after)
        counts.update(hits)
        changes.append({'id':row['id'],'source':row['source'],'before':before,'after':after,'matches':dict(hits)})
    if report:
        save_json(project/'output/replacements-report.json',{'changed_entries':len(changes),
            'counts':{k:counts[k] for k in rules},'unmatched':[k for k in rules if not counts[k]],'changes':changes})
    from name_translation import apply_names
    known=apply_names(project,rows,known)
    if apply_ui and cfg.get('_fix')!='names':
        from ui_policy import apply_policy
        known=apply_policy(project,rows,known,cached_only=cfg.get('_fix') in ('failed','display','layout'))
    return known
