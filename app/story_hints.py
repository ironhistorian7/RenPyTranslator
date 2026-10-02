"""Evidence-based, local-only hint overlays; never edit translation cache or game logic."""
import ast
from collections import defaultdict
import json
import os
import re
import shutil
import zipfile
from app_paths import ROOT,resource
from engine import save_json
from story_guides import expr,literal,names,answer_items,route_items

GREEN='#66DD88'
RED='#FF7777'
WORDS={'affection':'호감도','aff':'호감도','love':'호감도','trust':'신뢰도','respect':'존중','friendship':'친밀도',
       'relationship':'관계','relation':'관계','score':'점수','points':'점수','point':'점수',
       'money':'돈','gold':'골드','health':'체력','hp':'체력','fear':'공포도','anger':'분노',
       'corruption':'타락도','lust':'욕망','reputation':'평판','route':'루트','path':'루트',
       'romance':'연애','bonus':'추가','extra':'추가','optional':'추가','scene':'장면','event':'이벤트',
       'date':'데이트','locked':'잠금','unlocked':'해금','enabled':'활성화','disabled':'비활성화',
       'open':'열림','closed':'닫힘','available':'가능','flag':'','stat':'','stats':''}
BAD={'wrong','incorrect','fail','failed','failure','lose','retry','denied','invalid','error','bad'}
GOOD={'correct','right','success','successful','accepted','accept','solved','win','won','passed'}


def words(value):
    return re.findall(r'[a-z]+|[가-힣]+|\d+',re.sub(r'([a-z])([A-Z])',r'\1_\2',value).lower())


def display_name(value,people):
    parts=words(value)
    vocabulary=dict(WORDS,**{k.lower():v for k,v in people.items()})
    return ' '.join(vocabulary.get(p,p) for p in parts if vocabulary.get(p,p)) or value


def evidence(e):return {k:e.get(k) for k in ('file','line','label','code')}


def condition_text(guards,people):
    text=' 그리고 '.join(guards)
    text=re.sub(r'\bnot\s*\(([^()]+)\)',r'\1 아님',text)
    text=re.sub(r'\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\b',lambda m:display_name(m[0],people),text)
    return text.replace('True','참').replace('False','거짓').replace('true','참').replace('false','거짓')


def number_delta(e):
    if len(e['variables'])!=1:return None
    node=expr(e['expression']);value=literal(node)
    if e['operator'] in ('Add','Sub'):
        delta=value if e['operator']=='Add' else -value if type(value) in (int,float) else None
    elif e['operator']=='Set' and isinstance(node,ast.BinOp) and isinstance(node.op,(ast.Add,ast.Sub)):
        if ast.unparse(node.left)!=e['variables'][0]:return None
        value=literal(node.right)
        delta=value if isinstance(node.op,ast.Add) else -value if type(value) in (int,float) else None
    else:return None
    return delta if type(delta) in (int,float) and delta else None


def bound_value(node,bindings):
    """Small literal/boolean interpreter. Does not eval game code or call its functions."""
    if node is None:return None
    key=ast.unparse(node)
    if key in bindings:return bindings[key]
    if isinstance(node,ast.Constant):return node.value
    if isinstance(node,ast.BinOp):
        left=bound_value(node.left,bindings);right=bound_value(node.right,bindings)
        if type(left) not in (int,float) or type(right) not in (int,float):return None
        if isinstance(node.op,ast.Add):return left+right
        if isinstance(node.op,ast.Sub):return left-right
        if isinstance(node.op,ast.Mult):return left*right
    if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.USub,ast.UAdd)):
        value=bound_value(node.operand,bindings)
        if type(value) in (int,float):return -value if isinstance(node.op,ast.USub) else value
    if isinstance(node,(ast.List,ast.Tuple,ast.Set)):
        values=[bound_value(v,bindings) for v in node.elts]
        return values if all(v is not None for v in values) else None
    if isinstance(node,ast.UnaryOp) and isinstance(node.op,ast.Not):
        value=bound_value(node.operand,bindings)
        return None if value is None else not value
    if isinstance(node,ast.BoolOp):
        values=[bound_value(n,bindings) for n in node.values]
        if isinstance(node.op,ast.And):
            if any(v is not None and not v for v in values):return False
            return None if None in values else all(values)
        if any(v is not None and bool(v) for v in values):return True
        return None if None in values else False
    if isinstance(node,ast.Compare) and len(node.ops)==1:
        left=bound_value(node.left,bindings);right=bound_value(node.comparators[0],bindings)
        if left is None or right is None:return None
        try:
            op=node.ops[0]
            if isinstance(op,ast.Eq):return left==right
            if isinstance(op,ast.NotEq):return left!=right
            if isinstance(op,ast.In):return left in right
            if isinstance(op,ast.NotIn):return left not in right
            if isinstance(op,ast.Gt):return left>right
            if isinstance(op,ast.GtE):return left>=right
            if isinstance(op,ast.Lt):return left<right
            if isinstance(op,ast.LtE):return left<=right
            if isinstance(op,ast.Is):return left is right
            if isinstance(op,ast.IsNot):return left is not right
        except (TypeError,ValueError):return None
    if isinstance(node,ast.Call):
        if isinstance(node.func,ast.Attribute) and not node.args and not node.keywords:
            value=bound_value(node.func.value,bindings)
            if isinstance(value,str) and node.func.attr in ('strip','lstrip','rstrip','lower','upper','casefold'):
                return getattr(value,node.func.attr)()
        if isinstance(node.func,ast.Name) and node.func.id in ('int','str') and len(node.args)==1 and not node.keywords:
            value=bound_value(node.args[0],bindings)
            if value is not None:
                try:return int(value) if node.func.id=='int' else str(value)
                except (TypeError,ValueError):return None
    return None


def infer_answers(events):
    groups=defaultdict(list);branches=defaultdict(list);assignments=defaultdict(list)
    for e in events:
        if e['kind']=='condition':groups[e['group']].append(e)
        for bid in e['branch_ids']:branches[bid].append(e)
        if e['kind'] in ('assignment','input'):
            for v in e['variables']:assignments[v].append(e)
    constants={v:es[0]['value'] for v,es in assignments.items() if len(es)==1 and es[0]['kind']=='assignment' and es[0]['value'] is not None}
    result=[]
    for item in answer_items(events):
        # Names/free text do not become answers merely because a string is compared.
        variables=set(item['variables'])
        for t in item['transforms']:variables.update(t['variables'])
        def outcome(check):
            good=False;bad=False
            for e in branches[check['id']]:
                # Do not promote an outcome nested under another untested condition.
                if e['branch_ids'][-1]!=check['id']:continue
                if e['kind'] in ('jump','call'):
                    ws=set(words(e['target']));bad |= bool(ws & BAD) or e['target']==item['label'];good |= bool(ws & GOOD)
                elif e['kind']=='assignment' and e['value'] is True:
                    ws=set(words(' '.join(e['variables'])));bad |= bool(ws & BAD);good |= bool(ws & GOOD)
                elif e['kind']=='dialogue':
                    value=(e['text'] or '').lower();ws=set(words(value))
                    negative=bool(ws & BAD) or bool(re.search(r'not (?:right|correct)|try again|틀렸|오답|실패',value))
                    bad |= negative
                    good |= not negative and bool(ws & GOOD or re.search(r'정답|맞았|성공',value))
            return -1 if bad else 1 if good else 0
        local=[c for c in item['checks'] if c['file']==item['file'] and c['label']==item['label']]
        candidates=[]
        for check in local:
            tree=expr(check['condition'])
            for node in ast.walk(tree) if tree else []:
                if not isinstance(node,ast.Compare):continue
                operands=[node.left]+node.comparators
                if not any(names(ast.unparse(n)) & variables for n in operands):continue
                for n in operands:
                    if names(ast.unparse(n)) & variables:continue
                    value=bound_value(n,constants)
                    if isinstance(value,(list,tuple)):candidates.extend(v for v in value if type(v) in (str,int))
                    elif type(value) in (str,int):candidates.append(value)
        accepted=[];proof=[]
        for candidate in dict.fromkeys(candidates):
            bindings=dict(constants);valid=True
            # Replace only the input call in a copied AST, then interpret known normalization.
            node=expr(item['expression'])
            class InputValue(ast.NodeTransformer):
                def visit_Call(self,n):
                    if ast.unparse(n.func)=='renpy.input':return ast.Constant(str(candidate))
                    return self.generic_visit(n)
            value=bound_value(ast.fix_missing_locations(InputValue().visit(node)),bindings)
            if value is None:continue
            for v in item['variables']:bindings[v]=value
            for t in item['transforms']:
                value=bound_value(expr(t['expression']),bindings)
                if value is None:valid=False;break
                for v in t['variables']:bindings[v]=value
            if not valid:continue
            for check in local:
                if bound_value(expr(check['condition']),bindings) is not True:continue
                own=outcome(check);siblings=groups[check['group']]
                # A successful branch, or the sole alternative to an explicit failure.
                success=own==1 or (own==0 and len(siblings)==2 and any(outcome(s)==-1 for s in siblings if s is not check))
                if success:
                    accepted.append(str(candidate));proof.append(evidence(check));break
        if accepted:
            result.append({'file':item['file'],'line':item['execution_line'],'source':item['prompt'],
                           'values':list(dict.fromkeys(accepted)),'evidence':proof})
    return result


def infer_routes(events,people,selected=None,contexts=None):
    result=[];by_label=defaultdict(list);conditions=defaultdict(list)
    for e in events:
        if e['label']:by_label[e['label']].append(e)
        if e['kind']=='condition':
            for v in e['variables']:conditions[v].append(e)
    choices=route_items(events)
    groups=defaultdict(list);paths={}
    for choice in choices:
        groups[(choice['file'],choice['menu_line'])].append(choice)
        # Follow a short label chain, stopping at the next player decision.
        pending=[(e['target'],e['guards']) for e in choice['direct'] if e['kind'] in ('jump','call') and e['static']]
        reached={}
        while pending and len(reached)<24:
            label,guards=pending.pop(0)
            if label in reached:continue
            reached[label]=[]
            for e in by_label.get(label,[]):
                if e['kind']=='choice':break
                reached[label].append(dict(e,guards=list(dict.fromkeys(guards+e['guards']))))
                if e['kind'] in ('jump','call') and e['static']:
                    pending.append((e['target'],list(dict.fromkeys(guards+e['guards']))))
                    if e['kind']=='jump' and not e['guards']:break
                if e['kind']=='return' and not e['guards']:break
        paths[choice['id']]=reached
    for choice in choices:
        if selected is not None and choice['id'] not in selected:continue
        others=[c for c in groups[(choice['file'],choice['menu_line'])] if c['id']!=choice['id']]
        reached=paths[choice['id']]
        shared=set().union(*(set(paths[c['id']]) for c in others)) if others else set()
        exclusive={k:v for k,v in reached.items() if k not in shared}
        effects=choice['direct']+[e for es in exclusive.values() for e in es if e['kind'] in ('assignment','call')]
        if contexts is not None:
            effects=[e for e in contexts[choice['id']] if e['kind'] in ('assignment','call')]
        # Repeated calls and several updates on one path are a cumulative effect.
        totals={};other=[]
        for effect in effects:
            delta=number_delta(effect) if effect['kind']=='assignment' else None
            if delta is None:other.append(effect);continue
            key=(effect['variables'][0],tuple(effect['guards']))
            if key not in totals:totals[key]=[effect,0]
            totals[key][1]+=delta
        effects=[dict(e,operator='Add',expression=repr(total),value=total) for e,total in totals.values() if total]+other
        hints=[];seen=set()
        def add(text,positive=True,bold=False,e=None):
            guards=[g for g in (e or {}).get('guards',[]) if g not in choice['guards']]
            base=text
            if guards:text+=' ('+condition_text(guards,people)+'일 때)'
            key=(text,positive,bold)
            if key not in seen:
                seen.add(key);hints.append(dict(text=text,base_text=base,guards=guards,color=GREEN if positive else RED,bold=bold,evidence=evidence(e or choice)))
        for e in effects:
            if e['kind']=='assignment':
                delta=number_delta(e)
                if delta is not None:
                    add(display_name(e['variables'][0],people)+' '+format(delta,'+g'),delta>0,e=e);continue
                if len(e['variables'])!=1 or e['operator']!='Set' or type(e['value']) not in (bool,int):continue
                var=e['variables'][0];tokens=set(words(var))
                for check in conditions[var]:
                    matched=bound_value(expr(check['condition']),{var:e['value']})
                    if matched is None:continue
                    matched=bool(matched)
                    for edge in check['branches']:
                        if edge['branch_ids'][-1]!=check['id']:continue
                        target=set(words(edge['target']))
                        if not edge['static'] or edge['target'] not in by_label:continue
                        # The else/failure destination is not the route being gated.
                        is_route=bool(target&{'route','path','romance'})
                        is_scene=bool(target&{'scene','bonus','extra','optional','date','event'})
                        if is_route and matched is False:
                            add(display_name(edge['target'],people)+' 닫힘',False,True,dict(e,guards=e['guards']+check['guards']))
                        elif is_scene and matched is True:
                            add('추가 장면: '+display_name(edge['target'],people),True,True,e)
                        elif is_route and matched is True:
                            add(display_name(edge['target'],people)+' 열림',True,False,dict(e,guards=e['guards']+check['guards']))
            elif e['kind']=='call' and e['static'] and set(words(e['target'])) & {'scene','bonus','extra','optional','date','event'}:
                body=by_label.get(e['target'],[])
                # An explicit optional scene call that returns, not an arbitrary jump.
                if any(x['kind']=='dialogue' for x in body) and any(x['kind']=='return' for x in body):
                    add('추가 장면: '+display_name(e['target'],people),True,True,e)
        # A branch with unique dialogue that rejoins a sibling's direct destination.
        sibling_roots={e['target'] for c in others for e in c['direct'] if e['kind']=='jump' and e['static']}
        for e in choice['direct']:
            if e['kind']=='jump' and e['static'] and e['target'] in exclusive and sibling_roots & set(reached):
                if any(x['kind']=='dialogue' for x in exclusive[e['target']]):
                    add('추가 장면',True,True,e)
        if hints:
            result.append(dict(file=choice['file'],line=choice['menu_line'],source=choice['title'],hints=hints))
    return result


def write_support(project,cfg,state,root):
    game=root/'game';relative='tl/'+cfg['language']+'/rpt_hints'
    folder=game/relative;folder.mkdir(parents=True,exist_ok=True)
    files=[]
    for name in ('NanumSquareNeo-Regular.ttf','NanumSquareNeo-Bold.ttf','NanumSquareNeo-OFL.txt'):
        source=ROOT/'vendor/fonts'/name;dest=folder/name
        if not dest.exists() or dest.stat().st_size!=source.stat().st_size:shutil.copy2(source,dest)
        files.append(dest)
    state=dict(state,language=cfg['language'],font=relative+'/NanumSquareNeo-Regular.ttf',bold_font=relative+'/NanumSquareNeo-Bold.ttf')
    save_json(folder/'hints.json',state);files.append(folder/'hints.json')
    helper='\n'.join(resource(name).read_text(encoding='utf-8') for name in ('hints_layout.py','hints_conditions.py','hints_runtime.py'))
    code='# RenPyTranslator optional in-game hints; no story-state changes.\ninit 1300 python:\n'
    code+='    import json as _rpt_hint_json\n    _rpt_hint_data = _rpt_hint_json.loads(renpy.file('+json.dumps(relative+'/hints.json')+').read().decode("utf-8"))\n'
    code+=''.join('    '+line+'\n' for line in helper.splitlines())
    code+='\n'+resource('hints_screen.rpy').read_text(encoding='utf-8')
    addon=game/'zz_rpt_hints.rpy';addon.write_text(code,encoding='utf-8');files.append(addon)
    return files


def restore(project,cfg):
    path=project/'data/story-hints.json'
    if path.exists():write_support(project,cfg,json.loads(path.read_text(encoding='utf-8')),project/'staging')


def install(project,cfg,kinds,events,warnings):
    from output_paths import destination
    statefile=project/'data/story-hints.json'
    state=json.loads(statefile.read_text(encoding='utf-8')) if statefile.exists() else {'answers':[],'routes':[]}
    from name_translation import mapping
    people=mapping(project)
    for e in events:
        if e['kind']!='assignment' or not e['declaration']:continue
        node=expr(e['expression'])
        if isinstance(node,ast.Call) and ast.unparse(node.func).split('.')[-1] in ('Character','DynamicCharacter') and node.args:
            person=literal(node.args[0])
            if isinstance(person,str):
                for alias in e['variables']:people.setdefault(alias,people.get(person,person))
    from scene_hints import update
    report=update(project,cfg,kinds,events,people,state)
    state['language']=cfg['language']
    save_json(statefile,state)
    # A hints-only project must not masquerade as a fully prepared game staging tree.
    root=project/'staging' if (project/'staging').exists() else project/'data/hint-patch'
    files=write_support(project,cfg,state,root)
    output,label=destination(project,cfg);output.mkdir(parents=True,exist_ok=True)
    targets={p.relative_to(root).as_posix():p for p in files}
    archive=output/(label+'.zip');temporary=archive.with_suffix('.zip.tmp')
    instruction=output/'힌트 적용방법.txt'
    instruction.write_text('선택지·입력 정답 표시 패치\n\n이 결과의 game 폴더를 게임 실행 파일 옆에 복사하여 합칩니다. 기존 한국어 패치 위에 적용할 수 있습니다.\n초록색: 증가/긍정, 빨간색: 감소/부정, 굵은 빨강: 루트 차단, 굵은 초록: 장면 안내.\n입력 안내의 정답은 표시된 원문 그대로 입력합니다. 코드에서 확인하지 못한 효과와 정답은 표시하지 않습니다.\n장면 안내는 선택지 고유 대사를 로컬 AI로 요약한 내용입니다. 조건이 붙으면 해당 조건에서의 내용입니다.\n기존 번역 캐시는 변경하지 않으며 게임을 실행하지 않습니다. 완료된 분석·요약은 재사용합니다.\n힌트 전체 제거: game/zz_rpt_hints.rpy 및 같은 이름의 .rpyc를 제거합니다.\n',encoding='utf-8-sig')
    targets['힌트 적용방법.txt']=instruction
    # Preserve the existing patch archive without inspecting/re-rendering translations.
    with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as destzip:
        if archive.exists():
            with zipfile.ZipFile(archive) as previous:
                for info in previous.infolist():
                    if info.filename not in targets:destzip.writestr(info,previous.read(info))
        for rel,p in targets.items():destzip.write(p,rel)
    temporary.replace(archive)
    for rel,p in targets.items():
        target=output/rel;target.parent.mkdir(parents=True,exist_ok=True)
        if p.resolve()!=target.resolve():shutil.copy2(p,target)
    save_json(output/'project-link.json',{'project':os.path.relpath(project,output)})
    save_json(project/'output/hints-report.json',{'answers':len(state['answers']),'choices':len(state['routes']),
        'warnings':warnings,'translation_cache_changed':False,'game_executed':False,**report})
    print('Hint patch: %d answer prompts, %d choices. Unresolved effects omitted.'%(len(state['answers']),len(state['routes'])),flush=True)
    print('Output folder: '+str(output),flush=True)
    print(archive,flush=True)
