"""Project-approved, source-anchored terminology applied to effective text only."""
import json
import re
from pathlib import Path


def apply(project, rows, known, cfg):
    from source_language import normalize
    if cfg.get('engine') != 'kirikiri' or normalize(cfg.get('source_language')) != 'japanese': return known
    path=Path(project)/'data/reviewed-terms.json'
    if not path.is_file(): return known
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data,dict) or not isinstance(data.get('rules'),list):
        raise ValueError('검토 용어 파일의 rules 형식이 올바르지 않습니다.')
    rules=[]
    for rule in data['rules']:
        if not isinstance(rule,dict): raise ValueError('검토 용어 규칙이 올바르지 않습니다.')
        source=rule.get('source_literals');variants=rule.get('target_variants');target=rule.get('translation');exclude=rule.get('exclude_source_literals',[])
        if (not isinstance(source,list) or not source or not isinstance(variants,list) or not variants
            or not isinstance(exclude,list) or not isinstance(target,str) or not target.strip()
            or any(not isinstance(v,str) or not v for v in source+variants+exclude)
            or any(c in target for c in '{}[]%\r\n')):
            raise ValueError('검토 용어의 원문, 번역 또는 제외어가 올바르지 않습니다.')
        rules.append((source,variants,target,exclude))
    if not rules:return known
    from kirikiri_ruby import prepare
    from kirikiri_tokens import readable, spans
    from kirikiri_translation import row_config
    result=dict(known)
    for row in rows:
        uid=row.get('id');item=result.get(uid)
        if not item or not isinstance(item.get('text'),str):continue
        row_cfg=row_config(cfg,row)
        body,_,_=prepare(row.get('source',''),row_cfg)
        visible=readable(body,row_cfg)
        replacements={}
        for source,variants,target,exclude in rules:
            if any(v in visible for v in source) and not any(v in visible for v in exclude):
                for variant in variants:
                    if variant in replacements and replacements[variant]!=target:
                        raise ValueError('같은 원문에 상충하는 검토 용어가 있습니다: '+variant)
                    replacements[variant]=target
        if not replacements:continue
        pattern=re.compile('|'.join(re.escape(v) for v in sorted(replacements,key=lambda v:(-len(v),v))))
        def replace_visible(part):return pattern.sub(lambda match:replacements[match.group()],part)
        text=item['text'];pieces=[];pos=0
        for start,end in spans(text,row_cfg.get('_kirikiri_dialect','kag')):
            pieces.extend((replace_visible(text[pos:start]),text[start:end]));pos=end
        pieces.append(replace_visible(text[pos:]))
        updated=''.join(pieces)
        if updated!=text:result[uid]=dict(item,text=updated)
    return result
