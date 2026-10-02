"""Static character defaults shared by name collection and Korean rendering.

Only inspect literals, references and simple input wrappers. Never execute game code.
"""
import ast
import re
import textwrap
import tokenize
from script_literals import parse_expression,parse_script


def reference(expression, formatter=False):
    """Canonicalize a store reference; formatter brackets use literal keys."""
    try:node=parse_expression(expression.strip()).body
    except (SyntaxError,ValueError,tokenize.TokenError):return None
    def clean(node):
        if isinstance(node,ast.Name):return node
        if isinstance(node,ast.Attribute) and clean(node.value) is not None:return node
        if isinstance(node,ast.Subscript) and clean(node.value) is not None:
            if formatter and isinstance(node.slice,ast.Name):
                node.slice=ast.Constant(node.slice.id)
            if isinstance(node.slice,ast.Constant) and isinstance(node.slice.value,(str,int)):
                return node
        return None
    return ast.unparse(node) if clean(node) is not None else None


def fields(text):
    """Yield balanced interpolation spans, excluding escaped opening brackets."""
    i=0
    while i<len(text):
        if text[i:i+2]=='[[':i+=2;continue
        if text[i]!='[':i+=1;continue
        start=i;i+=1;depth=1;quote=None
        while i<len(text) and depth:
            ch=text[i]
            if quote:
                if ch=='\\':i+=2;continue
                if ch==quote:quote=None
            elif ch in ('"',"'"):quote=ch
            elif ch=='[':depth+=1
            elif ch==']':depth-=1
            i+=1
        if depth:break
        content=text[start+1:i-1]
        # Conversions/format specifications live outside indexed keys.
        level=0;quote=None;end=len(content)
        for j,ch in enumerate(content):
            if quote:
                if ch==quote and (not j or content[j-1]!='\\'):quote=None
            elif ch in ('"',"'"):quote=ch
            elif ch=='[':level+=1
            elif ch==']':level-=1
            elif level==0 and ch in '!:':end=j;break
        key=reference(content[:end],formatter=True)
        if key:yield start,i,key


def wrappers(scripts):
    """Recognize a single-return input helper, with no side effects evaluated."""
    result={}
    for text in scripts.values():
        lines=text.splitlines();i=0
        while i<len(lines):
            line=lines[i];i+=1
            is_function=bool(re.match(r'\s*def\s+\w+\s*\(',line))
            if not is_function and not re.match(r'\s*(?:class\s+|screen\s+|transform\s+|(?:(?:init(?:\s+-?\d+)?)\s+)?python\s+(?:hide|in)\b)',line):continue
            indent=len(line)-len(line.lstrip());block=[line]
            header='(' in line and not re.search(r'\)\s*(?:->[^:]+)?\s*:',line)
            while i<len(lines):
                following=lines[i]
                if not header and following.strip() and len(following)-len(following.lstrip())<=indent:break
                block.append(following);i+=1
                if header and re.search(r'\)\s*(?:->[^:]+)?\s*:',following):header=False
            if not is_function:continue
            try:fn=parse_script(textwrap.dedent('\n'.join(block))).body[0]
            except (SyntaxError,ValueError):continue
            body=[n for n in fn.body if not (isinstance(n,ast.Expr) and isinstance(n.value,ast.Constant) and isinstance(n.value.value,str))]
            if len(body)==1 and isinstance(body[0],ast.Return):result[fn.name]=(fn.args,body[0].value)
    return result


def sources(scripts,metadata):
    from name_hints import global_statements,input_kind,literal
    from name_translation import ROLES
    records={};characters={};inputs={};calls=[];helpers=wrappers(scripts)
    for text in scripts.values():
        for _,_,line in global_statements(text):
            match=re.match(r'\s*(?:(default|define)(?:\s+-?\d+)?\s+|\$\s*)?(.+?)\s*=\s*(?!=)(.+)',line,re.S)
            if not match:continue
            declaration,lhs,rhs=match.groups();expr=reference(lhs)
            if not expr or expr.startswith(('config.','build.','gui.')):continue
            try:node=parse_expression(rhs).body
            except (SyntaxError,ValueError,tokenize.TokenError):continue
            calls.append((expr,node))
            priority=2 if declaration else 1
            if expr not in records or priority>records[expr][0]:records[expr]=(priority,node)
            if isinstance(node,ast.Call) and ast.unparse(node.func).rsplit('.',1)[-1] in ('Character','DynamicCharacter'):
                characters[expr]=node
    # Explicit non-person inputs override name-like identifiers and speakers.
    kinds={k:v.get('kind') for k,v in metadata.get('names',{}).items() if v.get('kind') in ('address','answer') and v.get('origin')=='source'}
    kinds.update({e['variable']:e['kind'] for e in metadata.get('inputs',[]) if e.get('variable')})
    used=set()
    def input_call(node,env=None,seen=()):
        env=env or {}
        if not isinstance(node,ast.Call):return None
        if isinstance(node.func,ast.Attribute) and node.func.attr=='strip' and not node.args and not node.keywords:
            return input_call(node.func.value,env,seen)
        fn=ast.unparse(node.func);kwargs={k.arg:k.value for k in node.keywords}
        if fn in ('renpy.input','input'):
            prompt=node.args[0] if node.args else kwargs.get('prompt')
            default=node.args[1] if len(node.args)>1 else kwargs.get('default')
            return evaluate(prompt,seen,env),evaluate(default,seen,env)
        if fn not in helpers or fn in seen:return None
        args,body=helpers[fn];params=args.posonlyargs+args.args
        if args.vararg or args.kwarg or any(k.arg is None for k in node.keywords):return None
        local={a.arg:evaluate(v,seen,env) for a,v in zip(params[-len(args.defaults):],args.defaults)} if args.defaults else {}
        local.update({a.arg:evaluate(v,seen,env) for a,v in zip(params,node.args)})
        local.update({k:evaluate(v,seen,env) for k,v in kwargs.items()})
        local.update({a.arg:evaluate(v,seen,env) for a,v in zip(args.kwonlyargs,args.kw_defaults) if a.arg not in local})
        return input_call(body,local,seen+(fn,))
    def resolve(expr,seen=()):
        if expr in seen:return None
        used.add(expr)
        if expr in records:
            value=evaluate(records[expr][1],seen+(expr,))
            if value is not None and value!='':return value
            if inputs.get(expr):return inputs[expr]
            info=metadata.get('names',{}).get(expr,{})
            if info.get('origin')=='source':return info.get('representative')
            return value
        if expr.endswith('.name') and expr[:-5] in characters:return resolve(expr[:-5],seen)
        try:node=parse_expression(expr).body
        except SyntaxError:return None
        if isinstance(node,ast.Subscript):return evaluate(node,seen+(expr,))
        info=metadata.get('names',{}).get(expr,{})
        if info.get('kind')=='name' and info.get('origin')=='source':return info.get('representative')
        return None
    def evaluate(node,seen=(),env=None):
        env=env or {}
        if node is None:return None
        if isinstance(node,ast.Name) and node.id in env:return env[node.id]
        if isinstance(node,ast.Constant):return node.value
        if isinstance(node,(ast.Name,ast.Attribute)):return resolve(ast.unparse(node),seen)
        if isinstance(node,ast.Subscript):
            key=ast.unparse(node)
            if key in records and key not in seen:return resolve(key,seen)
            value=evaluate(node.value,seen,env);index=evaluate(node.slice,seen,env)
            try:return value[index] if isinstance(value,(dict,list,tuple)) else None
            except (KeyError,IndexError,TypeError):return None
        if isinstance(node,ast.Dict):
            try:return {evaluate(k,seen,env):evaluate(v,seen,env) for k,v in zip(node.keys,node.values) if k is not None}
            except TypeError:return None
        if isinstance(node,(ast.List,ast.Tuple)):return [evaluate(n,seen,env) for n in node.elts]
        if not isinstance(node,ast.Call):return None
        fn=ast.unparse(node.func).rsplit('.',1)[-1]
        if fn in ('_','__') and node.args:return evaluate(node.args[0],seen,env)
        if fn in ('Character','DynamicCharacter'):
            kwargs={k.arg:k.value for k in node.keywords}
            arg=node.args[0] if node.args else kwargs.get('name')
            value=evaluate(arg,seen,env)
            if not isinstance(value,str):return None
            if fn=='DynamicCharacter' or evaluate(kwargs.get('dynamic'),seen,env) is True:
                key=reference(value)
                return resolve(key,seen) if key else None
            spans=list(fields(value));parts=[];pos=0
            for start,end,key in spans:
                name=resolve(key,seen)
                if not isinstance(name,str):return None
                parts.extend((value[pos:start],name));pos=end
            return ''.join(parts)+value[pos:]
        call=input_call(node,env,seen)
        return call[1] if call else None
    for expr,node in calls:
        call=input_call(node)
        if call:
            prompt,default=call
            kinds[expr]=input_kind(expr,prompt if isinstance(prompt,str) else '')
            inputs[expr]=default
    def eligible(expr,value):
        return (kinds.get(expr) not in ('address','text','answer')
                and isinstance(value,str) and bool(value.strip()) and len(value)<=80
                and not any(c in value for c in '[]{}\n') and value.casefold() not in ROLES)
    result={}
    for expr in characters:
        used.clear();value=resolve(expr)
        dependencies=set(used)
        if any(kinds.get(k) in ('address','text','answer') for k in dependencies):continue
        if eligible(expr,value):
            result[expr]=value;result[expr+'.name']=value
            for key in sorted(dependencies):
                resolved=resolve(key)
                if eligible(key,resolved):result[key]=resolved
    for expr,info in metadata.get('names',{}).items():
        if expr.startswith(('config.','build.','gui.')):continue
        if info.get('kind')=='name' and info.get('origin')=='source':
            value=resolve(expr)
            if eligible(expr,value):result.setdefault(expr,value)
    for entry in metadata.get('inputs',[]):
        if entry.get('kind')=='name' and entry.get('variable'):
            inputs.setdefault(entry['variable'],entry.get('default') or 'John')
    for expr,default in inputs.items():
        value=resolve(expr) or default or 'John'
        if kinds.get(expr)=='name' and eligible(expr,value):result.setdefault(expr,value)
    # Close alias chains irrespective of file/declaration order.
    pending={k:v for k,(_,v) in records.items() if reference(ast.unparse(v))}
    while pending:
        added=[]
        for expr,node in pending.items():
            if ast.unparse(node) in result:
                value=resolve(expr)
                if eligible(expr,value):result[expr]=value;added.append(expr)
        if not added:break
        for expr in added:pending.pop(expr)
    return result

def translations(metadata,names,types=None):
    # Use confirmed transliterations, already-Korean defaults and the established
    # fallback name. Do not turn arbitrary roles or config.name into people.
    result={}
    for expr,source in metadata.get('fixed_name_sources',{}).items():
        if (types or {}).get(source,{}).get('kind') in ('address','role','object'):continue
        target=names.get(source)
        if not target and source=='John':target='존'
        # Missing transliteration must not restore the user's renamed value.
        if not target:target=source
        if target:result[expr]=target
    return result


def apply(text,names):
    from display_text import PARTICLE,PAIRS
    from josa_runtime import rpt_josa
    output=[];pos=0
    for start,end,key in fields(text):
        value=names.get(key)
        if not value or start<pos:continue
        closing=re.match(r'(?:\{/(?:b|i|u|s|color|font|size|a)\})*',text[end:]).group()
        particle=PARTICLE.match(text,end+len(closing))
        replacement=value.replace('{','{{').replace('[','[[')
        if particle:
            replacement+=closing+rpt_josa(value,PAIRS[particle[1]])
            end=particle.end()
        output.append(text[pos:start]+replacement);pos=end
    return ''.join(output)+text[pos:]
