"""Static script extraction; optional local scene summaries are handled separately."""
import ast
from collections import defaultdict, deque
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import tokenize

from engine import save_json, extract_scripts, owned_run
from app_paths import cli_command
from script_literals import parse_expression, parse_script

QUOTED = r'''(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')'''


def expr(code):
    try: return parse_expression(code).body
    except (SyntaxError, ValueError): return None


def names(code):
    tree = expr(code)
    return {ast.unparse(n) for n in ast.walk(tree) if isinstance(n,(ast.Name,ast.Attribute,ast.Subscript))} if tree else set()


def literal(node):
    try: return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError): return None


def logical_lines(text):
    """Join bracket continuations and discard comments, without evaluating code."""
    pending = ''
    start = 0
    for number, line in enumerate(text.splitlines(), 1):
        if not pending: start = number
        pending += line + '\n'
        try:
            tokens = list(tokenize.generate_tokens(io.StringIO(pending).readline))
        except (tokenize.TokenError, IndentationError):
            # A malformed/unhandled statement is reported below, not executed.
            if len(pending.splitlines()) < 80: continue
            yield start, pending.strip(), 0
            pending = ''; continue
        comments = {t.start[0]: t.start[1] for t in tokens if t.type == tokenize.COMMENT}
        lines = pending.splitlines()
        clean = ' '.join(s[:comments.get(i, len(s))].strip() for i,s in enumerate(lines,1))
        indent = len(lines[0].expandtabs(4)) - len(lines[0].expandtabs(4).lstrip())
        if clean: yield start, clean, indent
        pending = ''
    if pending.strip(): yield start, pending.strip(), 0


def parse(scripts):
    events = []; warnings = []
    for filename, text in sorted(scripts.items()):
        stack = []; chains = {}; chain_starts = {}; label = None; global_label = None
        for number, code, indent in logical_lines(text):
            while stack and stack[-1]['indent'] >= indent: stack.pop()
            for level in list(chains):
                if level > indent: del chains[level]
            branch = re.match(r'^(if|elif)\s+(.+):$',code)
            otherwise = code == 'else:'
            if not branch and not otherwise: chains.pop(indent,None)
            m = re.match(r'^label\s+([\w.]+)(?:\([^)]*\))?\s*:', code)
            if indent==0 and not m and re.match(r'^(?:screen|init|transform|python|default|define|style|image)\b',code):label=None
            if m:
                label = m[1]
                if label.startswith('.') and global_label: label = global_label+label
                else: global_label = label
                stack = []; chains = {}
            guards = [s['guard'] for s in stack if s.get('guard')]
            choices = [s['choice'] for s in stack if s.get('choice')]
            event = dict(file=filename,line=number,label=label,code=code,guards=guards,choices=choices,indent=indent,
                         menu_line=next((s['menu_line'] for s in reversed(stack) if s.get('menu')),None),
                         execution_line=next((s['python_line'] for s in stack if 'python_line' in s),number),
                         branch_ids=[s['branch_id'] for s in stack if s.get('branch_id')])
            if m:
                events.append(dict(event,kind='label',name=label)); continue
            if branch or otherwise:
                prior = chains.get(indent,[])
                if branch and branch[1]=='if': prior = [];chain_starts[indent]=number
                condition = branch[2] if branch else None
                guard = ' and '.join(['not ('+c+')' for c in prior]+(['('+condition+')'] if condition else [])) or '(unmatched else)'
                chains[indent] = prior+([condition] if condition else [])
                branch_id=filename+':'+str(number)
                stack.append(dict(indent=indent,guard=guard,branch_id=branch_id))
                events.append(dict(event,kind='condition',id=branch_id,condition=guard,
                                   group=filename+':'+str(chain_starts.get(indent,number)),variables=sorted(names(guard))))
                continue
            if re.match(r'^menu(?:\s+[^:]+)?\s*:',code):
                events.append(dict(event,kind='menu'))
                stack.append(dict(indent=indent,menu=True,menu_line=number)); continue
            if code in ('python:','python hide:'):
                stack.append(dict(indent=indent,python_line=number));continue
            m = re.match('^('+QUOTED+r')(?:\s+if\s+(.+?))?\s*:\s*$',code)
            if m and any(s.get('menu') for s in stack):
                title = literal(expr(m[1]))
                identifier = filename+':'+str(number)
                guard = m[2]
                events.append(dict(event,kind='choice',id=identifier,title=title,
                                   guards=guards+([guard] if guard else [])))
                stack.append(dict(indent=indent,choice=identifier,guard=guard)); continue
            m = re.match(r'^(jump|call)\s+(.+?)(?:\s+from\s+\w+)?$',code)
            screen_call = re.match(r'^call\s+screen\s+(\w+)',code)
            if screen_call:
                events.append(dict(event,kind='screen_call',target=screen_call[1]));continue
            if label and re.match(r'^(while|for)\b',code):
                events.append(dict(event,kind='unsupported_flow'));continue
            from story_media import media_event
            media = media_event(code)
            if label and media:
                events.append(dict(event,**media));continue
            if m:
                target = m[2]
                static = bool(re.fullmatch(r'[\w.]+(?:\([^)]*\))?',target)) and not target.startswith('expression ')
                if static:
                    target = target.split('(')[0]
                    if target.startswith('.') and global_label: target = global_label+target
                events.append(dict(event,kind=m[1],target=target,static=static)); continue
            if code=='return':events.append(dict(event,kind='return'));continue
            spoken=re.match(r'^(?:([\w.]+)(?:\s+\w+)*\s+)?('+QUOTED+r')(?:\s+(?:with\s+\w+|nointeract))?\s*$',code)
            if spoken:
                events.append(dict(event,kind='dialogue',speaker=spoken[1] or '',text=literal(expr(spoken[2]))));continue
            py = re.sub(r'^(?:\$\s*|default\s+|define\s+)','',code)
            try: tree = parse_script(py)
            except SyntaxError:
                if 'renpy.input' in code or code.startswith(('input ','python ','python:')):
                    warnings.append(dict(event,reason='화면 입력 또는 별도 Python 블록: 지원되는 내부 대입문만 분석'))
                continue
            for node in tree.body:
                if not isinstance(node,(ast.Assign,ast.AugAssign,ast.AnnAssign)): continue
                targets = node.targets if isinstance(node,ast.Assign) else [node.target]
                if node.value is None: continue
                variables = [ast.unparse(t) for t in targets]
                inputs = [n for n in ast.walk(node.value) if isinstance(n,ast.Call) and ast.unparse(n.func)=='renpy.input']
                if inputs:
                    call = inputs[0]
                    prompt = literal(call.args[0]) if call.args else None
                    if prompt is None:
                        prompt = next((literal(k.value) for k in call.keywords if k.arg=='prompt'),None)
                    events.append(dict(event,kind='input',variables=variables,prompt=prompt,
                                       expression=ast.unparse(node.value)))
                else:
                    events.append(dict(event,kind='assignment',variables=variables,
                                       expression=ast.unparse(node.value),value=literal(node.value),
                                       operator=type(node.op).__name__ if isinstance(node,ast.AugAssign) else 'Set',
                                       declaration=code.startswith(('default ','define '))))
    branches=defaultdict(list)
    for e in events:
        if e['kind'] in ('jump','call'):
            for branch_id in e['branch_ids']:branches[branch_id].append(e)
    for e in events:
        if e['kind']=='condition':e['branches']=branches[e['id']]
    from story_media import enrich,navigation
    enrich(scripts,events)
    navigation(scripts,events)
    return events,warnings


def answer_items(events):
    checks = [e for e in events if e['kind']=='condition']
    declarations = defaultdict(list)
    for e in events:
        if e['kind']=='assignment' and e['declaration'] and e['value'] is not None:
            for name in e['variables']: declarations[name].append(e)
    result = []
    positions={id(e):i for i,e in enumerate(events)}
    for entry in (e for e in events if e['kind']=='input'):
        variables = set(entry['variables']); transforms = []
        end_line=None
        # Follow local aliases/normalization until the next input overwrites them.
        for e in events[positions[id(entry)]+1:]:
            if e['file']!=entry['file'] or e['label']!=entry['label']: break
            if e['kind']=='input' and variables.intersection(e['variables']):end_line=e['line'];break
            if e['kind']=='assignment' and names(e['expression']) & variables:
                variables.update(e['variables']); transforms.append(e)
        matched = []
        for check in checks:
            if not variables.intersection(check['variables']): continue
            if check['file']==entry['file'] and check['label']==entry['label']:
                if check['line']<=entry['line'] or (end_line and check['line']>=end_line):continue
            tree = expr(check['condition']); candidates = []
            for node in ast.walk(tree) if tree else []:
                if not isinstance(node,ast.Compare): continue
                operands = [node.left]+node.comparators
                if not any(names(ast.unparse(n)) & variables for n in operands): continue
                for n in operands:
                    value = literal(n)
                    if isinstance(value,(str,int,float,list,tuple,set)):
                        candidates.append(dict(value=repr(value),basis='코드의 비교값'))
                    elif isinstance(n,ast.Name) and n.id not in variables:
                        for d in declarations[n.id]:
                            candidates.append(dict(value=repr(d['value']),basis='초기 선언값; 실행 중 변경 가능',file=d['file'],line=d['line']))
            matched.append(dict(check,candidates=candidates))
        result.append(dict(entry,checks=matched,transforms=transforms))
    return result


def route_items(events, limit=80):
    by_label = defaultdict(list); conditions = defaultdict(list)
    for e in events:
        if e['label']: by_label[e['label']].append(e)
        if e['kind']=='condition':
            for v in e['variables']: conditions[v].append(e)
    result=[]
    for choice in (e for e in events if e['kind']=='choice'):
        direct=[e for e in by_label[choice['label']] if choice['id'] in e['choices'] and e['kind'] in ('assignment','jump','call')]
        affected=set(v for e in direct if e['kind']=='assignment' for v in e['variables'])
        dependencies={ (c['file'],c['line']):c for v in affected for c in conditions.get(v,[]) }
        queue=deque(e['target'] for e in direct if e['kind'] in ('jump','call') and e['static'])
        seen=set(); downstream=[]
        while queue and len(seen)<limit:
            name=queue.popleft()
            if name in seen:continue
            seen.add(name)
            for edge in by_label.get(name,[]):
                if edge['kind'] not in ('jump','call'):continue
                downstream.append(edge)
                if edge['static'] and edge['target'] not in seen:queue.append(edge['target'])
        result.append(dict(choice,direct=direct,dependent_conditions=list(dependencies.values()),
                           downstream=downstream,truncated=bool(queue)))
    return result


def where(e): return '%s:%s [%s]'%(e['file'],e['line'],e.get('label') or 'global')


def describe(e):
    return e['code']+'  ('+where(e)+')'+(' / 조건: '+' AND '.join(e['guards']) if e['guards'] else '')


def render_guide(kind,items,warnings):
    lines=['정답 안내' if kind=='answers' else '루트 분석', '',
           '스크립트 정적 분석 결과. 게임·모델 실행 없음.',
           '비교값은 정답 확정이 아닙니다. !=, not, 실패 분기를 포함하므로 아래 조건과 이동을 함께 확인하세요.' if kind=='answers' else
           '변수 변화와 코드상 연결을 정리합니다. 모든 조건을 동시에 만족하는 경로인지 계산한 결과는 아닙니다.',
           '동적 Python, 화면 입력, 암호화, 반복·call/return 상태 및 암묵적 label 이동은 일부 누락될 수 있습니다.', '']
    if not items: lines.append('지원되는 코드 패턴을 찾지 못했습니다. 해당 기능이 없다는 뜻은 아닙니다.')
    for index,item in enumerate(items,1):
        lines += ['%d. %s'%(index,(item.get('prompt') or item['code']) if kind=='answers' else item['title']),where(item)]
        if item['guards']: lines.append('진입 조건: '+' AND '.join(item['guards']))
        if kind=='answers':
            lines.append('입력 처리: '+item['expression'])
            for e in item['transforms']:lines.append('후처리 / 별칭: '+describe(e))
            if not item['checks']:lines.append('관련 비교 조건을 확인하지 못했습니다.')
            for c in item['checks']:
                lines.append(('비교 조건: ' if c['file']==item['file'] and c['label']==item['label'] else '다른 장면의 동명 변수 조건 (연결 미확정): ')+describe(c))
                for v in c['candidates']:lines.append('  입력 후보: '+v['value']+' ('+v['basis']+')')
                for edge in c.get('branches',[]):lines.append('  분기 이동: '+describe(edge))
        else:
            for e in item['direct']:lines.append('선택지 내부: '+describe(e))
            if not item['direct']:lines.append('직접 변수 변경 / 명시적 이동을 찾지 못했습니다.')
            for c in item['dependent_conditions']:
                lines.append('같은 변수를 검사하는 조건 (도달 여부 미확정): '+describe(c))
                for edge in c.get('branches',[]):lines.append('  조건 아래 이동: '+describe(edge))
            for e in item['downstream']:lines.append('이동 대상 이후 연결 (조건부 후보): '+describe(e))
            if item['truncated']:lines.append('연결이 많아 80개 label까지만 추적했습니다.')
        lines.append('')
    if warnings:
        lines += ['미지원 / 부분 분석:']+[where(w)+': '+w['reason'] for w in warnings]
    return '\n'.join(lines)+'\n'


def safe_files(root):
    if root.is_symlink() or root.is_junction():raise ValueError('분석 입력 폴더가 외부 경로로 연결되어 있습니다: '+str(root))
    for directory,dirs,files in os.walk(root,followlinks=False):
        dirs[:]=[d for d in dirs if d!='tl' and not (Path(directory)/d).is_symlink() and not (Path(directory)/d).is_junction()]
        for name in files:
            p=Path(directory)/name
            if not p.is_symlink() and not name.startswith('zz_rpt_') and p.suffix in ('.rpy','.rpym','.rpyc','.rpymc','.rpa'):yield p


def read_scripts(project,cfg):
    """Reuse prepared scripts; otherwise extract scripts only into disposable tool storage."""
    recovered=project/'data/recovered-scripts';stage=project/'staging/game'
    if stage.exists() or recovered.exists():
        scripts={}
        for base in (recovered,stage):
            for p in safe_files(base):
                if p.suffix in ('.rpy','.rpym'):scripts[p.relative_to(base).as_posix()]=p.read_text(encoding='utf-8-sig')
        if scripts:return scripts
        raise ValueError('기존 프로젝트에 읽을 수 있는 원문 스크립트가 없습니다. 원본 게임으로 안내를 실행하세요.')
    source=Path(cfg['source'])/'game'
    with tempfile.TemporaryDirectory(prefix='guide-',dir=project/'data') as temp:
        dest=Path(temp).resolve()
        if not dest.is_relative_to((project/'data').resolve()):raise ValueError('Invalid guide workspace')
        archives=[]
        for p in safe_files(source):
            if p.suffix=='.rpa':archives.append(p);continue
            target=dest/p.relative_to(source);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
        for archive in sorted(archives,reverse=True):extract_scripts(archive,dest)
        compiled=[p for p in safe_files(dest) if p.suffix in ('.rpyc','.rpymc') and not p.with_suffix(p.suffix[:-1]).exists()]
        for offset in range(0,len(compiled),64):
            with (project/'data/guide-decompile.log').open('a',encoding='utf-8') as log:
                owned_run(cli_command()+['--unrpyc','-p','1',*map(str,compiled[offset:offset+64])],stdout=log,stderr=subprocess.STDOUT,
                          check=True,timeout=180,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if any(not p.with_suffix(p.suffix[:-1]).exists() for p in compiled):raise ValueError('일부 스크립트 복구 실패: data/guide-decompile.log')
        scripts={p.relative_to(dest).as_posix():p.read_text(encoding='utf-8-sig') for p in safe_files(dest) if p.suffix in ('.rpy','.rpym')}
        if not scripts:raise ValueError('분석할 스크립트를 찾지 못했습니다.')
        return scripts


def run(project,cfg,kinds):
    from story_hints import install
    print('In-game hints: no game execution; only new/outdated scene summaries use the local model',flush=True)
    scripts=read_scripts(project,cfg)
    events,warnings=parse(scripts)
    if 'routes' in kinds:
        from story_media import asset_index,enrich,navigation,navigation_destinations
        root=project/'staging/game'
        if not root.exists():root=Path(cfg['source'])/'game'
        assets,media_warnings=asset_index(root)
        from workspace_files import media_names
        extra_assets,extra_warnings=media_names(project)
        assets=sorted(set(assets)|set(extra_assets));media_warnings+=extra_warnings
        enrich(scripts,events,assets)
        report=navigation(scripts,events)
        navigation_destinations(report,events)
        report['media']=[e for e in events if e['kind']=='media']
        report['warnings']=media_warnings
        save_json(project/'data/navigation-media.json',report)
    install(project,cfg,kinds,events,warnings)
