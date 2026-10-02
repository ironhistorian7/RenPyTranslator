"""Static name hints: inspect source data, never execute game code."""
import ast
import hashlib
import json
import re
import tokenize
from script_literals import literal_eval,parse_expression

EXPR = r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*'
INTERPOLATION = re.compile(r'(?<!\[)\[(' + EXPR + r')(?:![a-z]+)?\]')
VERSION = 6
NAME_WORD = re.compile(r'(?:^|_)(?:name|firstname|lastname|surname|nickname|petname|alias)(?:$|_)|^(?:playername|mcname|firstname|lastname)$', re.I)
NAME_PROMPT = re.compile(r'\b(?:name|nickname|pet\s*name)\b|\b(?:call|address)\s+(?:me|you|him|her|them|each other)\b|이름|별명|애칭|호칭', re.I)


def input_kind(expr, prompt):
    text=(expr or '')+' '+(prompt or '')
    if re.search(r'password|passcode|\bcode\b|answer|solution|암호|정답|비밀번호',text,re.I):return 'answer'
    if re.search(r'nick.?name|pet.?name|alias|\bcall\b|\baddress\b|별명|애칭|호칭',text,re.I):return 'address'
    if is_name(expr or '') or re.search(r'\bname\b|이름',prompt or '',re.I):return 'name'
    return 'text'


def input_key(entry):
    return hashlib.sha256(json.dumps([entry.get(k) for k in ('file','line','variable','prompt','default','kind')],ensure_ascii=False).encode()).hexdigest()[:20]


def is_name(expr):
    word=expr.rsplit('.', 1)[-1]
    return bool(NAME_WORD.search(word) or NAME_WORD.search(re.sub(r'([a-z])([A-Z])',r'\1_\2',word)))


def statements(text):
    """Join multiline assignment expressions as data; never execute scripts."""
    lines=text.splitlines();i=0
    while i<len(lines):
        first=i;line=lines[i];i+=1
        assignment=re.match(r'\s*(?:(?:default|define)(?:\s+-?\d+)?\s+|\$\s*)?(.+?)\s*=\s*(?!=)(.+)',line)
        direct=re.match(r'\s*\$?\s*((?:renpy\.)?input\s*\(.*)',line)
        if assignment or direct:
            rhs=assignment[2] if assignment else direct[1]
            while True:
                try:parse_expression(rhs);break
                except (SyntaxError,tokenize.TokenError) as exc:
                    if i>=len(lines) or i-first>=128 or not any(t in str(exc) for t in ('EOF','never closed','unterminated')):break
                    rhs+='\n'+lines[i];line+='\n'+lines[i];i+=1
        yield first+1,i,line


def global_statements(text):
    """Ignore function/class locals when collecting game-store assignments."""
    local_indent=None;header=False
    for first,last,line in statements(text):
        stripped=line.strip()
        if not stripped or stripped.startswith('#'):continue
        indent=len(line)-len(line.lstrip())
        if local_indent is not None:
            if header:
                if re.search(r'\)\s*(?:->[^:]+)?\s*:',stripped):header=False
                continue
            if indent>local_indent:continue
            local_indent=None
        if re.match(r'(?:async\s+)?def\s+|class\s+|screen\s+|transform\s+|(?:(?:init(?:\s+-?\d+)?)\s+)?python\s+(?:hide|in)\b',stripped):
            local_indent=indent
            header='(' in stripped and not re.search(r'\)\s*(?:->[^:]+)?\s*:',stripped)
            continue
        yield first,last,line


def literal(node):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ('_', '__') and node.args:
        node = node.args[0]
    try:
        value = ast.literal_eval(node)
        return value.strip() if isinstance(value, str) else None
    except (ValueError, TypeError):
        return None


def discover(scripts, rows):
    candidates = {m[1] for r in rows for m in INTERPOLATION.finditer(r['source']) if is_name(m[1])}
    defaults = {}
    choices = set()
    inputs = []
    # Collect constant defaults and input defaults, including common empty-name fallbacks.
    for filename,text in scripts.items():
        in_menu = None
        python_block = None
        for number,end,line in global_statements(text):
            indent = len(line) - len(line.lstrip())
            stripped = line.strip()
            if stripped and not stripped.startswith('#'):
                if python_block and indent <= python_block[0]:python_block=None
                if re.match(r'(?:(?:init(?:\s+-?\d+)?)\s+)?python\b.*:\s*$',stripped):
                    python_block=(indent,number)
                if in_menu is not None and indent <= in_menu:
                    in_menu = None
                if re.match(r'menu(?:\s+\w+)?(?:\s*\(.*\))?\s*:', stripped):
                    in_menu = indent
                elif in_menu is not None:
                    match = re.match(r'(?:_\(\s*)?(["\'](?:\\.|[^\\])*?["\'])\s*\)?\s*(?:if\s+.+)?\s*:', stripped)
                    if match:
                        try: choices.add(literal_eval(match[1]))
                        except (ValueError, SyntaxError): pass
            # Character("[hero]") establishes that hero is a name even without a name-like identifier.
            for match in re.finditer(r'Character\(\s*["\']\[(' + EXPR + r')\]["\']', line):
                candidates.add(match[1])
            if 'dynamic=True' in line.replace(' ', ''):
                match = re.search(r'Character\(\s*["\'](' + EXPR + r')["\']', line)
                if match: candidates.add(match[1])
            match = re.match(r'\s*(?:(?:default|define)(?:\s+-?\d+)?\s+|\$\s*)?(' + EXPR + r')\s*=\s*(.+)', line, re.S)
            direct=re.match(r'\s*\$?\s*((?:renpy\.)?input\s*\(.*)',line,re.S)
            if not match and not direct:continue
            expr,rhs=match.groups() if match else ('',direct[1])
            try: node = parse_expression(rhs).body
            except (SyntaxError,tokenize.TokenError): continue
            value = literal(node)
            priority = 2 if stripped.startswith(('default ', 'define ')) else 1
            # renpy.input(...).strip() and renpy.input(..., default="Alex").
            calls = [n for n in ast.walk(node) if isinstance(n, ast.Call) and
                     ((isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name) and
                     n.func.value.id == 'renpy' and n.func.attr == 'input') or
                     (isinstance(n.func,ast.Name) and n.func.id=='input'))]
            for call in calls:
                keywords={kw.arg:kw.value for kw in call.keywords}
                prompt = literal(call.args[0]) if call.args else literal(keywords.get('prompt'))
                kind=input_kind(expr,prompt)
                if expr and kind in ('name','address'):
                    candidates.add(expr)
                for kw in call.keywords:
                    if kw.arg == 'default': value = literal(kw.value); priority = 3
                if len(call.args) > 1: value = literal(call.args[1]); priority = 3
                inputs.append({'file':filename,'line':number,'end_line':end,
                               'statement_line':python_block[1] if python_block else number,
                               'variable':expr,'prompt':prompt or '', 'default':value or '', 'kind':kind})
            if is_name(expr): candidates.add(expr)
            if expr and value and len(value) <= 80 and not any(c in value for c in '[]{}\n'):
                old = defaults.get(expr)
                if old is None or priority > old[0]: defaults[expr] = (priority, value)
    kinds={e['variable']:e['kind'] for e in inputs if e['variable']}
    names = {expr: {'representative': defaults.get(expr, (0, 'John'))[1],
                    'origin': 'source' if expr in defaults else 'fallback',
                    'kind':kinds.get(expr,input_kind(expr,''))} for expr in sorted(candidates)
             if kinds.get(expr) != 'answer'}
    for entry in inputs:
        if not entry['default'] and entry['variable'] in defaults:entry['default']=defaults[entry['variable']][1]
        # Comparing a relationship/name to a string selects dialogue branches;
        # it does not establish that the input is a password or puzzle answer.
        entry['key']=input_key(entry)
    result={'version': VERSION, 'names': names, 'choices': sorted(choices),
            'inputs':inputs,'display_variables':dict(names,**{e['variable']:{'kind':e['kind']} for e in inputs if e['variable'] and e['kind']=='text'})}
    from fixed_names import sources
    result['fixed_name_sources']=sources(scripts,result)
    return result


def ensure_metadata(project, rows=None, scripts=None):
    from engine import save_json
    path = project / 'data/name-hints.json'
    if rows is None:
        from translation import read_catalog
        rows = read_catalog(project)
    digest=hashlib.sha256(json.dumps([(r.get('id'),r['source']) for r in rows],ensure_ascii=False).encode('utf-8')).hexdigest()
    if path.exists():
        old=json.loads(path.read_text(encoding='utf-8'))
        if old.get('version')==VERSION and old.get('catalog_digest')==digest:
            old['runtime_choices']=True
            return old
    from automatic import script_sources
    data = discover(script_sources(project) if scripts is None else scripts, rows)
    data['catalog_digest']=digest
    data['runtime_choices']=True
    save_json(path, data)
    return data


def placeholder_hints(tokens, names):
    hints = {}
    for i, token in enumerate(tokens):
        match = INTERPOLATION.fullmatch(token)
        if match and match[1] in names:
            hints['<rpt%03d/>' % i] = names[match[1]]['representative']
    return hints
