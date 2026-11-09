"""Translate input values separately from their questions; reuse only valid values."""
import json
import re
from engine import save_json
from source_language import letters


def items(metadata):
    from name_hints import input_key
    result=list(metadata.get('inputs',[]))
    variables={e.get('variable') for e in result}
    for variable,info in metadata.get('names',{}).items():
        if info.get('kind')=='address' and info.get('origin')=='source' and variable not in variables:
            entry=dict(file='',line=0,variable=variable,prompt='상대에게 사용하는 호칭/애칭',default=info['representative'],kind='address')
            entry['key']=input_key(entry);result.append(entry)
    return result


def group_key(item):
    return (item.get('variable') or '@'+item['key'],item.get('default',''),item.get('kind','text'))


def normalized(text):
    text=re.sub(r'\([^)]*\)','',text)
    return re.sub(r'[^\w]','',text).casefold()


def usable(item,text,prompts):
    if not isinstance(text,str) or not text.strip() or not re.search('[가-힣]',text) or any(c in text for c in '{}[]\r\n'):
        return False
    source=item.get('default','')
    question=prompts.get(item.get('prompt',''),'')
    if question and normalized(question)==normalized(text) and normalized(source)!=normalized(item.get('prompt','')):
        return False
    # A short label cannot turn into the question's sentence frame. Do not
    # apply this rule to sentence defaults, greetings or imperative endings.
    short=bool(re.fullmatch(r"[A-Za-zÀ-ž][A-Za-zÀ-ž '\-]*",source)) and len(source.split())<=3
    if short and (re.search(r'\.{2,}|…',text) or re.search(r'(?:입니다|이에요|예요|이다)[.!?\s]*$',text) or
                  re.search(r'(?:이라고|라고|이라는|라는).*(?:부르|불러|좋아)',text)):
        return False
    return True


def validated(entries,records,prompts):
    groups={}
    for item in entries:
        if item.get('default') and item.get('kind')!='answer':
            groups.setdefault(group_key(item),[]).append(item)
    result={}
    for group in groups.values():
        values=set()
        for item in group:
            record=records.get(item['key'],{})
            text=record.get('text')
            if record.get('source')==item['default'] and all(usable(e,text,prompts) for e in group):values.add(text.strip())
        # Conflicting valid outputs must be resolved once, never by file order.
        if len(values)==1:
            text=values.pop()
            for item in group:result[item['key']]=dict(source=item['default'],text=text,kind=item['kind'],variable=item.get('variable'))
    return result


def translate(project,entries,records,prompts,names,ask,report,cfg=None):
    path=project/'data/input-defaults.json'
    clean=validated(entries,records,prompts)
    pending={}
    for item in entries:
        value=item.get('default','');kind=item.get('kind','text')
        if not value or kind=='answer':continue
        if kind=='name' and value in names:
            clean[item['key']]=dict(source=value,text=names[value],kind=kind,variable=item.get('variable'))
        if item['key'] not in clean and letters(value):pending.setdefault(group_key(item),[]).append(item)
    # Quarantine old question echoes even if the request is cancelled or fails.
    save_json(path,clean)
    queue=list(pending.values())
    if queue:print('Input defaults: %d unique values to translate; duplicate input locations share results'%len(queue),flush=True)
    for attempt in range(2):
        retry=[]
        for offset in range(0,len(queue),4):
            batch=queue[offset:offset+4]
            schema={'type':'object','properties':{str(i):{'type':'string'} for i in range(len(batch))},'required':[str(i) for i in range(len(batch))],'additionalProperties':False}
            from source_language import instruction as language_instruction
            instruction=(language_instruction(cfg or {})+'입력란 기본값 번역 작업입니다. text 필드만 한국어로 번역하세요. '
                         'context는 의미를 구분하는 참고이며 번역 대상이 아닙니다. '
                         '인명은 음역하고 호칭과 일반 명사는 뜻을 번역하세요. '
                         '원문이 단어나 명사구라면 번역도 단어나 명사구로만 쓰세요. '
                         '질문이나 설명 문장을 만들지 마세요. JSON ID별 번역만 반환하세요.\n')
            payload={str(i):dict(text=g[0]['default'],kind=g[0]['kind'],**({'context':g[0].get('prompt','')} if attempt==0 else {})) for i,g in enumerate(batch)}
            result=ask(instruction+json.dumps(payload,ensure_ascii=False),schema)
            for i,group in enumerate(batch):
                text=result.get(str(i)) if isinstance(result,dict) else None
                if not all(usable(e,text,prompts) for e in group):retry.append(group);continue
                for item in group:clean[item['key']]=dict(source=item['default'],text=text.strip(),kind=item['kind'],variable=item.get('variable'))
            save_json(path,clean)
        queue=retry
        if not queue:break
    for group in queue:report['issues'].append({'input_default':group[0]['default'],'reason':'No valid value translation; kept original value instead of question text'})
    if queue:print('Input defaults: %d values kept in original language after bounded retry'%len(queue),flush=True)
    return clean
