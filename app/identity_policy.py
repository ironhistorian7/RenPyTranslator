"""Cached semantic roles; a UI string or a quoted speaker is not proof of a name."""
import hashlib
import json
import re
from engine import save_json

VERSION=1

def signature(evidence):
    return hashlib.sha256(json.dumps(evidence,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def classify(project,inventory,ask):
    path=project/'data/identity-types.json'
    saved=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    pending=[]
    for name,evidence in inventory.items():
        key=signature(evidence)
        default_kinds={e.get('kind') for e in evidence if e.get('kind') in ('name default','address default')}
        if len(default_kinds)==1:
            saved[name]={'kind':'name' if 'name default' in default_kinds else 'address','signature':key,'version':VERSION};continue
        if saved.get(name,{}).get('signature')==key and saved[name].get('version')==VERSION:continue
        pending.append(name)
    for offset in range(0,len(pending),8):
        batch=pending[offset:offset+8]
        schema={'type':'object','properties':{str(i):{'type':'string','enum':['name','address','role','object','unknown']} for i in range(len(batch))},'required':[str(i) for i in range(len(batch))],'additionalProperties':False}
        prompt=('게임의 표시 명칭을 분류하세요. name=실제 고유 인명, address=상대에게 쓰는 호칭/애칭, '
                'role=직업/관계명, object=사물/장소/일반 용어, unknown=판단 불가. '
                'Character나 따옴표 화자에는 자동차 소리 등도 들어갈 수 있으므로 그것만으로 인명으로 판단하지 마세요. '
                '이름을 번역하지 말고 JSON ID별 분류만 반환하세요.\n')
        result=ask(prompt+json.dumps({str(i):{'label':n,'evidence':inventory[n][:8]} for i,n in enumerate(batch)},ensure_ascii=False),schema)
        for i,name in enumerate(batch):
            kind=result.get(str(i)) if isinstance(result,dict) else None
            if kind in ('name','address','role','object','unknown'):
                saved[name]={'kind':kind,'signature':signature(inventory[name]),'version':VERSION}
        save_json(path,saved)
    save_json(path,saved)
    return saved

def accepted(saved,types,name):
    if name in types:return types[name].get('kind')=='name'
    evidence=saved.get('sources',{}).get(name)
    # Legacy maps without provenance remain usable. Recorded weak evidence is
    # quarantined until the user runs the existing names repair.
    if evidence is None:return True
    from name_hints import input_kind
    return any(e.get('kind')=='name default' and input_kind(e.get('variable',''),'')=='name' for e in evidence)

def repair_terms(project,rows,known,saved,types,ask,cfg):
    """Only sentences containing a rejected legacy spelling go to the model."""
    from translation import protect,restore,validate_text
    path=project/'data/semantic-edits.json'
    edits=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    rejected={n:v for n,v in saved.get('names',{}).items() if types.get(n,{}).get('kind') in ('address','role','object')}
    # Old short UI translations were promoted to a global glossary. Their
    # provenance is retained only for targeted correction, never for reuse.
    for row in rows:
        entry=known.get(row['id'])
        if row['kind']=='string' and entry and row['source'] in rejected:
            rejected[row['source']]=entry['text']
    pending=[]
    for row in rows:
        entry=known.get(row['id'])
        if not entry or entry.get('status')=='source_fallback':continue
        from source_language import occurrences
        terms={n:v for n,v in rejected.items() if occurrences(row['source'].casefold(),n.casefold()) and v and v in entry['text']}
        if not terms:continue
        key=signature(terms)
        old=edits.get(row['id'],{})
        if old.get('source')==row['source'] and old.get('before')==entry['text'] and old.get('signature')==key:continue
        pending.append((row,entry,terms,key))
    for offset in range(0,len(pending),4):
        batch=pending[offset:offset+4]
        schema={'type':'object','properties':{str(i):{'type':'string'} for i in range(len(batch))},'required':[str(i) for i in range(len(batch))],'additionalProperties':False}
        protected=[protect(e['text']) for r,e,t,k in batch]
        payload={str(i):{'source':r['source'],'translation':protected[i][0],'suspect_spellings':t} for i,(r,e,t,k) in enumerate(batch)}
        prompt=('원문 문맥을 보고 일반 명사나 호칭에 잘못 적용된 인명 음역만 교정하세요. '
                '실제 인명을 가리키면 그대로 두세요. 무관한 번역·말투를 바꾸지 말고 필요한 조사만 함께 고치세요. '
                'JSON ID별 교정된 번역문을 반환하세요.')
        if any(t for s,t in protected):prompt+=' 입력의 <rpt000/> 표시는 그대로 보존하세요.'
        result=ask(prompt+'\n'+json.dumps(payload,ensure_ascii=False),schema)
        for i,(row,entry,terms,key) in enumerate(batch):
            try:
                text=restore(result[str(i)],protected[i][1])
                if validate_text(row['source'],text,cfg):continue
            except (ValueError,KeyError,TypeError):continue
            edits[row['id']]={'source':row['source'],'before':entry['text'],'after':text,'signature':key}
        save_json(path,edits)
    return edits
