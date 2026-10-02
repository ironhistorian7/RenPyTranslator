"""Parse script literals without changing valid escapes or warning on unknown ones."""
import ast
import io
import re
import tokenize


def normalize_literal(text):
    match=re.match(r'(?i)^([rubf]*)(\x22{3}|\x27{3}|\x22|\x27)',text)
    if not match or 'r' in match[1].lower():return text
    prefix=text[:match.end()];body=text[match.end():-len(match[2])]
    out=[];i=0
    while i<len(body):
        char=body[i]
        if char=='\\' and i+1<len(body):
            following=body[i+1]
            if following not in "\\'\"abfnrtv01234567xuUN\n\r":out.append('\\')
            out.append(body[i:i+2]);i+=2
        else:out.append(char);i+=1
    return prefix+''.join(out)+match[2]


def parse_script(text,mode='exec'):
    """Normalize string tokens for both Python statements and expressions."""
    try:tokens=list(tokenize.generate_tokens(io.StringIO(text).readline))
    except tokenize.TokenError as exc:raise SyntaxError(str(exc)) from exc
    tokens=[t._replace(string=normalize_literal(t.string)) if t.type==tokenize.STRING else t for t in tokens]
    return ast.parse(tokenize.untokenize(tokens),mode=mode)


def parse_expression(text):
    return parse_script(text,mode='eval')


def literal_eval(value):
    if isinstance(value,str):value=parse_expression(value.strip()).body
    return ast.literal_eval(value)
