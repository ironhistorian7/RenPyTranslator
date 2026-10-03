"""Local names, input localization and bounded edits; original translation cache is immutable."""
import ast
from contextlib import ExitStack
import hashlib
import json
import re
from script_literals import literal_eval
from urllib.parse import urlparse
from engine import save_json
from name_hints import discover as name_hints, literal, ensure_metadata, INTERPOLATION
from translation import QUOTED,TOKENS,FORMATS,read_catalog,cache,request
from source_language import letters, occurrences as name_occurrences, instruction as language_instruction

# Generic roles are not personal names. No game-specific characters are embedded here.
ROLES={'narrator','unknown','man','woman','girl','boy','everyone','player','father','mother','dad','mom',
       'husband','wife','sister','brother','guard','clerk','cashier','waitress','bartender','maid',
       'voice','house','statue','spider','bee','knife','prince','nun','loudspeaker','reply','old man',
       'tenant','landlord','partner'}

def visible(text):return TOKENS.sub('',text).strip()

def personal(text):
    text=visible(text)
    return (1<=len(text)<=64 and text.casefold() not in ROLES and
            letters(text) and all(c.isalpha() or c in " .’'·-" for c in text) and
            len(text.split())<=4)

def collect(scripts,rows,metadata=None):
    found={}
    hints=metadata if metadata is not None else name_hints(scripts,rows)
    # Exactly the same resolved defaults drive collection and output rendering.
    for expr,name in sorted(hints.get('fixed_name_sources',{}).items()):
        if personal(name):
            info=hints['names'].get(expr,{})
            strong=info.get('kind')=='name' and info.get('origin')=='source'
            found.setdefault(name,[]).append({'variable':expr,'kind':'name default' if strong else 'Character','context':'Resolved character default: '+expr})
    for expr,info in hints['names'].items():
        name=info['representative']
        if info['origin']=='source' and info.get('kind')=='address' and personal(name):
            found.setdefault(name,[]).append({'variable':expr,'kind':info.get('kind','name')+' default'})
    for row in rows:
        name=visible(row.get('speaker_name',''))
        if personal(name):found.setdefault(name,[]).append({'file':row.get('file'),'kind':'quoted speaker','context':row['source'][:160]})
    return found

def read(path,default):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else default

def mapping(project):
    saved=read(project/'data/name-transliterations.json',{})
    from identity_policy import accepted
    types=read(project/'data/identity-types.json',{})
    automatic={n:v for n,v in saved.get('names',{}).items() if accepted(saved,types,n)}
    overrides=read(project/'names.json',{'overrides':{}}).get('overrides',{})
    for source,target in overrides.items():
        if not isinstance(source,str) or not isinstance(target,str) or not target.strip() or any(c in target for c in '{}[]\r\n'):
            raise ValueError('names.json overrides must map names to plain text')
    return dict(automatic,**overrides)

def digest(names):
    return hashlib.sha256(json.dumps(names,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def edit_digest(names,source):
    return digest({n:v for n,v in names.items() if occurrences(visible(source),n)})

def apply_names(project,rows,known):
    names=mapping(project)
    edits=read(project/'data/name-edits.json',{})
    semantic=read(project/'data/semantic-edits.json',{})
    for row in rows:
        entry=known.get(row['id'])
        if not entry:continue
        source=row['source'];bare=visible(source)
        correction=semantic.get(row['id'],{})
        if correction.get('source')==source and correction.get('before')==entry['text'] and correction.get('after')!=entry['text'] and entry.get('status')!='source_fallback':
            entry=dict(entry,text=correction['after'],status='semantic_correction')
            known[row['id']]=entry
        # Exact registered name labels, with their formatting left intact.
        if row['kind']=='string' and bare in names:
            text=source.replace(bare,names[bare],1)
            known[row['id']]=dict(entry,text=text,status='name_transliteration')
            continue
        # Name edits cannot turn an untranslated sentence into a completed one.
        # Ignore legacy saved edits too, without modifying the translation cache.
        if entry.get('status') == 'source_fallback':
            continue
        edit=edits.get(row['id'])
        if edit and edit.get('source')==source and edit.get('before')==entry['text'] and edit.get('digest')==edit_digest(names,source):
            if edit['after'] != entry['text']:
                known[row['id']]=dict(entry,text=edit['after'],status='name_correction')
    return known

def occurrences(text,name):
    return name_occurrences(text,name)

def candidate_spans(source,target,names,aliases):
    """Only known old spellings in rows that actually mention a registered name."""
    spans=[];protected=[m.span() for pattern in (TOKENS,FORMATS) for m in pattern.finditer(target)]
    for name,new in names.items():
        if not occurrences(visible(source),name):continue
        for old in set(aliases.get(name,[]))|{name,new}:
            if not old:continue
            for m in re.finditer(re.escape(old),target):
                a,b=m.span()
                if any(a<end and b>start for start,end in protected):continue
                # Do not match Ariel inside another name, or 인도 inside 인도네시아.
                if a and target[a-1].isalnum():continue
                tail=target[b:]
                if tail and tail[0].isalnum() and not re.match(r'^(?:은|는|이|가|을|를|의|와|과|에게|한테|에서|에|도|만|으로|로|야|아)(?:\W|$)',tail):continue
                if old==new:
                    end,replacement=particle_replacement(target,b,new)
                    if end==b or replacement==target[b:end]:continue
                elif target[max(0,a):].startswith(new):continue
                spans.append({'name':name,'old':old,'new':new,'start':a,'end':b})
    return sorted(spans,key=lambda x:(x['start'],x['end'],x['name']))

def particle_replacement(text,end,name):
    from display_text import PARTICLE,PAIRS
    from josa_runtime import rpt_josa
    closing=re.match(r'(?:\{/(?:b|i|u|s|color|font|size|a)\})*',text[end:]).group()
    match=PARTICLE.match(text,end+len(closing))
    if not match:return end,''
    return match.end(),closing+rpt_josa(name,PAIRS[match[1]])


def replace_selected(text,spans,selected):
    if not isinstance(selected,list):return text
    edits=[]
    for index in selected:
        if not isinstance(index,int) or isinstance(index,bool) or not 0<=index<len(spans):continue
        span=spans[index]
        if any(span['start']<b and span['end']>a for a,b,_ in edits):continue
        end,particle=particle_replacement(text,span['end'],span['new'])
        edits.append((span['start'],end,span['new']+particle))
    for a,b,new in sorted(edits,reverse=True):text=text[:a]+new+text[b:]
    return text

def run(project,cfg):
    """No runtime starts unless a new name or an unresolved candidate needs it."""
    from automatic import script_sources
    from hy_backend import runtime,is_hy
    from model_runtime import model_session
    rows=read_catalog(project);known=cache(project,cfg)
    scripts=script_sources(project)
    metadata=ensure_metadata(project,rows,scripts)
    inventory=collect(scripts,rows,metadata)
    from input_defaults import items as default_items,translate as translate_defaults
    input_items=default_items(metadata)
    promptsfile=project/'data/name-input-prompts.json'
    prompts=read(promptsfile,{})
    originals={e['prompt'] for e in metadata.get('inputs',[]) if e.get('prompt')}
    for row in rows:
        entry=known.get(row['id'])
        if row['source'] in originals and entry and entry.get('status')!='source_fallback' and re.search('[가-힣]',entry['text']):
            prompts[row['source']]=entry['text']
    namesfile=project/'names.json'
    if not namesfile.exists():save_json(namesfile,{'overrides':{}})
    saved=read(project/'data/name-transliterations.json',{'names':{},'attempted':[]})
    automatic=saved.get('names',{});attempted=set(saved.get('attempted',[]))
    overrides=read(namesfile,{}).get('overrides',{})
    names=mapping(project)
    defaultsfile=project/'data/input-defaults.json'
    input_defaults=read(defaultsfile,{})
    report={'model_calls':0,'discovered':len(inventory),'new_names':0,'corrected_items':0,'issues':[],
            'scope':'names, input prompts/defaults and affected dialogue only; no full retranslation'}
    edits=read(project/'data/name-edits.json',{})
    with ExitStack() as stack:
        active=None
        def ask(prompt,schema):
            nonlocal active
            if active is None:
                if not is_hy(cfg) and urlparse(cfg.get('endpoint','')).hostname not in ('127.0.0.1','localhost','::1'):
                    raise ValueError('Name processing requires a local model endpoint')
                stack.enter_context(model_session())
                active=stack.enter_context(runtime(dict(cfg,parallel=1)))
            report['model_calls']+=1
            response=request(active['endpoint'],'/api/chat',{'model':active['model'],'stream':False,
                'think':False,'format':schema,'keep_alive':'30s',
                'options':{'num_ctx':active.get('num_ctx',8192),'num_predict':900,'temperature':0,'seed':42},
                'messages':[{'role':'user','content':prompt}]})
            if response.get('done_reason')=='length':return {}
            try:return json.loads(response['message']['content'])
            except (ValueError,KeyError,TypeError):return {}
        from identity_policy import classify,repair_terms
        types=classify(project,inventory,ask)
        names=mapping(project)
        pending=[n for n in inventory if types.get(n,{}).get('kind')=='name' and n not in names]
        for offset in range(0,len(pending),8):
            batch=pending[offset:offset+8]
            schema={'type':'object','properties':{str(i):{'type':'string'} for i in range(len(batch))},
                    'required':[str(i) for i in range(len(batch))],'additionalProperties':False}
            prompt=('다음은 게임 스크립트에서 추출한 인물의 고유 이름이다. 각 이름을 발음대로 한국어로 음역하라. '
                '뜻풀이, 국가명 번역, 직업명 번역은 금지한다. 예: 인물 India는 인디아, 국가 인도가 아니다. '
                '이름에 없는 성이나 호칭을 추가하지 마라. 한자 이름은 제공된 읽기 근거가 있으면 우선하고 없으면 가장 일반적인 읽기를 사용하라. JSON의 ID별 값에는 음역한 이름만 써라.\n'+language_instruction(cfg)+
                json.dumps(dict(enumerate(batch)),ensure_ascii=False))
            result=ask(prompt,schema)
            for i,name in enumerate(batch):
                value=result.get(str(i)) if isinstance(result,dict) else None
                attempted.add(name)
                if isinstance(value,str) and re.fullmatch(r'[가-힣][가-힣 ·\-]{0,63}',value.strip()):
                    automatic[name]=value.strip();report['new_names']+=1
                    if re.search(r'[\u3400-\u9fff]',name):
                        report.setdefault('reading_notes',[]).append({'name':name,'spelling':value.strip(),
                            'basis':'model-inferred reading; not independently verified'})
                else:report['issues'].append({'name':name,'reason':'No usable transliteration; kept original result'})
            save_json(project/'data/name-transliterations.json',{'names':automatic,'attempted':sorted(attempted),'sources':inventory})
            print('Name transliteration: %d/%d'%(min(offset+8,len(pending)),len(pending)),flush=True)
        # Input prompts are often Python strings absent from Ren'Py's catalog.
        # Translate only newly discovered input prompts, not the full script.
        missing_prompts=sorted(p for p in originals if p not in prompts and letters(TOKENS.sub('',p)))
        from translation import protect,restore,validate_text
        for offset in range(0,len(missing_prompts),4):
            batch=missing_prompts[offset:offset+4]
            protected=[protect(p) for p in batch]
            schema={'type':'object','properties':{str(i):{'type':'string'} for i in range(len(batch))},
                    'required':[str(i) for i in range(len(batch))],'additionalProperties':False}
            instruction=language_instruction(cfg)+'텍스트 입력 안내문을 자연스러운 한국어로 번역하세요. 답이나 이름을 만들어 넣지 마세요. JSON ID별 번역만 반환하세요.'
            if any(tokens for text,tokens in protected):instruction+=' 입력에 있는 <rpt000/> 형식 표시는 그대로 보존하세요.'
            result=ask(instruction+'\n'+json.dumps({str(i):v[0] for i,v in enumerate(protected)},ensure_ascii=False),schema)
            for i,prompt in enumerate(batch):
                try:
                    value=restore(result[str(i)],protected[i][1])
                    if validate_text(prompt,value,cfg):raise ValueError('Invalid input prompt translation')
                    prompts[prompt]=value
                except (KeyError,TypeError,ValueError):
                    report['issues'].append({'input_prompt':prompt,'reason':'Kept original prompt'})
            save_json(promptsfile,prompts)
        names=mapping(project)
        translate_defaults(project,input_items,input_defaults,prompts,names,ask,report,cfg=cfg)
        semantic={}
        if not cfg.get('_names_only'):
            semantic=repair_terms(project,rows,known,saved,types,ask,cfg)
        aliases={n:set() for n in names}
        for row in rows:
            bare=visible(row['source'])
            if row['kind']=='string' and bare in aliases and row['id'] in known:
                old=visible(known[row['id']]['text'])
                if len(old)<=64:aliases[bare].add(old)
        candidates=[]
        for pos,row in enumerate(rows):
            if cfg.get('_names_only'):break
            entry=known.get(row['id'])
            if not entry or (row['kind']=='string' and visible(row['source']) in names):continue
            if entry.get('status') == 'source_fallback':continue
            if row['kind'] not in ('dialogue','string'):continue
            correction=semantic.get(row['id'],{})
            if correction.get('source')==row['source'] and correction.get('before')==entry['text']:
                entry=dict(entry,text=correction['after'])
            spans=candidate_spans(row['source'],entry['text'],names,aliases)
            if not spans:continue
            old=edits.get(row['id'])
            if old and old.get('version')==2 and old.get('decided',True) and old.get('source')==row['source'] and old.get('before')==entry['text'] and old.get('digest')==edit_digest(names,row['source']):continue
            nearby=[r['source'][:250] for r in rows[max(0,pos-1):pos+2] if r.get('file')==row.get('file')]
            candidates.append((row,entry,spans,nearby))
        print('Name correction candidates: %d (other dialogue is not sent to the model)'%len(candidates),flush=True)
        # Small bounded prompts; ID selection never rewrites unrelated sentence text.
        for offset in range(0,len(candidates),4):
            batch=candidates[offset:offset+4]
            payload={str(i):{'source':r['source'],'translation':e['text'],'nearby':nearby,
                        'candidates':{str(j):s for j,s in enumerate(spans)}} for i,(r,e,spans,nearby) in enumerate(batch)}
            schema={'type':'object','properties':{str(i):{'type':'array','items':{'type':'integer'}} for i in range(len(batch))},
                    'required':[str(i) for i in range(len(batch))],'additionalProperties':False}
            answer=ask('기존 번역에서 인물 이름 음역만 교정한다. 원문과 문맥을 보고 각 후보 위치가 실제 인물을 '
                '가리키는 경우에만 후보 번호를 선택하라. 국가명·일반 명사·판단 불가인 후보는 선택하지 마라. '
                '예: 인물 India와 국가 India가 함께 있으면 인물을 가리키는 인도만 선택한다. '
                'JSON으로 각 항목 ID에 후보 번호 배열을 반환하라. 고칠 것이 없으면 빈 배열.\n'+json.dumps(payload,ensure_ascii=False),schema)
            for i,(row,entry,spans,_) in enumerate(batch):
                selected=answer.get(str(i)) if isinstance(answer,dict) else None
                after=replace_selected(entry['text'],spans,selected)
                if selected is None:report['issues'].append({'id':row['id'],'reason':'Model could not decide; kept existing text'})
                edits[row['id']]={'source':row['source'],'before':entry['text'],'after':after,
                    'digest':edit_digest(names,row['source']),'decided':isinstance(selected,list),'version':2}
                report['corrected_items']+=after!=entry['text']
            save_json(project/'data/name-edits.json',edits)
            print('Name correction: %d/%d'%(min(offset+4,len(candidates)),len(candidates)),flush=True)
    updated=apply_names(project,rows,dict(known))
    changed={i for i,e in updated.items() if i in known and e['text']!=known[i]['text']}
    # Re-render variable display expressions offline, even when cached Korean
    # dialogue itself needs no name correction. Raw cache entries are untouched.
    from fixed_names import fields
    changed.update(r['id'] for r in rows if r['id'] in known and
                   any(key in metadata.get('display_variables',metadata['names']) or key in metadata.get('fixed_name_sources',{}) for _,_,key in fields(known[r['id']]['text'])))
    save_json(promptsfile,prompts)
    report['applied_items']=len(changed)
    save_json(project/'output/names-report.json',report)
    return changed,report

def install_support(project,cfg,rows,known):
    """Update name display/choice payloads without replacing any font settings."""
    names=mapping(project)
    game=project/'staging/game';lang=cfg['language']
    path=game/'tl'/lang/'_rpt_presentation.json'
    if path.exists():
        data=read(path,{})
        from display_text import compose
        from translation import display_metadata
        metadata=display_metadata(project,cfg,rows)
        for row in rows:
            if row['source'] in data.get('choices',{}) and row['id'] in known:
                data['choices'][row['source']]=compose(dict(row,kind='dialogue'),known[row['id']],metadata)
        data.setdefault('pronunciations',{}).update({n.lower():v for n,v in names.items()})
        save_json(path,data)
    install_runtime(project,cfg)


def install_runtime(project,cfg):
    """Install display helpers from saved metadata only; no model/script scan."""
    from app_paths import resource
    names=mapping(project);game=project/'staging/game';lang=cfg['language']
    metadata=read(project/'data/name-hints.json',{})
    namepath='tl/'+lang+'/_rpt_names.json'
    inputpath='tl/'+lang+'/_rpt_name_inputs.json'
    save_json(game/namepath,names)
    from input_defaults import items as default_items,validated
    prompts=read(project/'data/name-input-prompts.json',{})
    defaults=validated(default_items(metadata),read(project/'data/input-defaults.json',{}),prompts)
    inputs=[];variables={}
    for item in metadata.get('inputs',[]):
        item=dict(item);record=defaults.get(item.get('key'),{})
        if record.get('source')==item.get('default'):
            item['translated_default']=record.get('text')
        inputs.append(item)
        variable=item.get('variable')
        if variable:
            info=variables.setdefault(variable,{'kind':item.get('kind','name'),'values':{}})
            if item.get('translated_default') and item.get('kind')!='answer':
                info['values'][item['default']]=item['translated_default']
    for record in defaults.values():
        variable=record.get('variable')
        if variable and record.get('kind')!='answer':
            variables.setdefault(variable,{'kind':record['kind'],'values':{}})['values'][record['source']]=record['text']
    save_json(game/inputpath,{'inputs':inputs,'variables':variables,
                             'prompts':prompts})
    code='''# Name display overlay; no font or layout changes.
init 1100 python:
    import json as _rpt_name_json
    _rpt_name_map = _rpt_name_json.loads(renpy.file(NAMEFILE).read().decode('utf-8'))
    _rpt_name_language = LANGUAGE
    _rpt_name_inputs = _rpt_name_json.loads(renpy.file(INPUTFILE).read().decode('utf-8'))
'''.replace('NAMEFILE',json.dumps(namepath)).replace('LANGUAGE',json.dumps(lang)).replace('INPUTFILE',json.dumps(inputpath))
    # Install the current particle helper during display repair too, even when
    # the old Korean/font support file is intentionally retained.
    helper=resource('josa_runtime.py').read_text(encoding='utf-8')+'\n'+resource('names_runtime.py').read_text(encoding='utf-8')
    code+=''.join('    '+line+'\n' for line in helper.splitlines())
    (game/'zz_rpt_names.rpy').write_text(code,encoding='utf-8')
