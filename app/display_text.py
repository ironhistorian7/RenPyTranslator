"""Offline display assembly. The translation cache remains Korean-only."""
import re
from name_hints import INTERPOLATION

TAG = re.compile(r'\{\{|\{([^{}]*)\}')
PAIRS = {'은':'은/는', '는':'은/는', '이':'이/가', '가':'이/가',
         '을':'을/를', '를':'을/를', '과':'과/와', '와':'과/와', '으로':'으로/로', '로':'으로/로'}
# Allow common attached particles but avoid rewriting noun/verb beginnings such as 은행 or 이름.
PARTICLE = re.compile(r'(으로|은|는|이|가|을|를|과|와|로)(?=$|[^가-힣]|(?:는|도|만|부터|까지|조차|마저)(?=$|[^가-힣]))')
PAIRED = {'a','b','i','u','s','color','alpha','font','size','cps','k','outlinecolor','plain','rb','rt','art','alt','noalt'}


def attach_josa(text, names):
    result = []; pos = 0
    for match in INTERPOLATION.finditer(text):
        if match.start() < pos or match[1] not in names: continue
        # Closing emphasis tags may separate the name and its particle.
        close = re.match(r'(?:\{/(?:b|i|u|s|color|font|size|a)\})*', text[match.end():]).group()
        particle = PARTICLE.match(text, match.end() + len(close))
        if not particle: continue
        result.append(text[pos:match.end()] + close)
        context=", u'%s'" % match[1] if names[match[1]].get('kind') in ('address','text') else ''
        result.append("[rpt_josa(%s, u'%s'%s)]" % (match[1], PAIRS[particle[1]],context))
        pos = particle.end()
    return ''.join(result) + text[pos:]


def reference_text(source,names=None):
    """Drop control/visual tags in the reference only; preserve words and interpolation."""
    def strip(match):
        if match.group() == '{{': return '{{'
        tag = match[1].lstrip('/')
        name = tag.split('=', 1)[0]
        if name in ('p', 'br'): return '\n'
        if name == 'space': return ' '
        return ''
    text=TAG.sub(strip, source)
    # Include indexed expressions as well as simple player_name variables.
    result=[];i=0
    while i<len(text):
        if text[i]!='[':result.append(text[i]);i+=1;continue
        if text[i:i+2]=='[[':result.append('[[');i+=2;continue
        j=i+1;depth=1;quoted=None
        while j<len(text) and depth:
            c=text[j]
            if quoted:
                if c=='\\':j+=2;continue
                if c==quoted:quoted=None
            elif c in ('\x22','\x27'):quoted=c
            elif c=='[':depth+=1
            elif c==']':depth-=1
            j+=1
        if depth:result.append(text[i:]);break
        inside=text[i+1:j-1]
        conversion=re.search(r'!([a-z]+)(?=(:.*)?$)',inside)
        expression=inside[:conversion.start()] if conversion else inside
        flags=conversion[1].replace('t','') if conversion else ''
        formatting=inside[conversion.end():] if conversion else ''
        if expression in (names or {}):
            expression='rpt_reference_name('+expression+')';flags='q'+flags.replace('q','')
        result.append('['+expression+('!'+flags if flags else '')+formatting+']');i=j
    return ''.join(result)


def close_styles(text):
    stack = []
    for match in TAG.finditer(text):
        if match[1] is None: continue
        tag = match[1]; name = tag.lstrip('/').split('=', 1)[0]
        if name not in PAIRED: continue
        if tag.startswith('/'):
            if stack and stack[-1] == name: stack.pop()
        else: stack.append(name)
    return text + ''.join('{/'+name+'}' for name in reversed(stack))


def display_name_variables(text, names):
    def replace(match):
        if match[1] not in names:
            return match.group()
        flags = re.search(r'!([a-z]+)', match.group())
        flags = flags[1].replace('t', '').replace('q', '') if flags else ''
        context=", u'%s'" % match[1] if names[match[1]].get('kind') in ('address','text') else ''
        return '[rpt_display_name(' + match[1] + context + ')!q' + flags + ']'
    return INTERPOLATION.sub(replace, text)


def compose(row, entry, metadata):
    source = row['source']; target = entry['text']
    if entry.get('status') == 'source_fallback': return target
    from fixed_names import apply as fixed_names
    target=fixed_names(target,metadata.get('fixed_names',{}))
    if target == source:return target
    names=metadata.get('display_variables',metadata.get('names',{}))
    target = attach_josa(target, names)
    target = display_name_variables(target, names)
    choice_source=re.sub(r'\{#[^{}]*\}', '', source)
    if row['kind'] != 'dialogue' and (metadata.get('runtime_choices') or
            (source not in metadata.get('choices', []) and choice_source not in metadata.get('choices', []))): return target
    # A terminal {nw}/{done} before the reference would hide it or advance too early.
    terminal = []
    def controls(match):
        if match[1] and match[1].split('=', 1)[0] in ('nw','done'):
            terminal.append(match.group()); return ''
        return match.group()
    target = TAG.sub(controls, target)
    target = close_styles(target)
    reference=reference_text(source,metadata.get('names',{}) if metadata.get('reference_guard') else {})
    if metadata.get('reference_guard'):
        return target + '\n{rpt_ref=0.55}{cps=0}' + reference + '{/cps}{/rpt_ref}' + ''.join(terminal)
    return target + '\n{size=*0.55}{cps=0}' + reference + '{/cps}{/size}' + ''.join(terminal)
