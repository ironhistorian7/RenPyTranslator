"""Deterministic repairs only; missing names and inline tag positions are not guessed."""
import re
from collections import Counter

STYLE = re.compile(r'\{(/?)(b|i|u|s|color|size|font|outlinecolor)(?:=[^{}]*)?\}')


def split_outer_styles(source):
    prefix='';suffix='';core=source
    while True:
        lead=STYLE.match(core)
        if not lead or lead.group(1):break
        close='{/'+lead.group(2)+'}'
        if not core.endswith(close):break
        # Only remove a wrapper whose matching close is the final token.
        depth=0;end=None
        for tag in STYLE.finditer(core):
            if tag.group(2)!=lead.group(2):continue
            depth += -1 if tag.group(1) else 1
            if depth==0:end=tag.end();break
        if end!=len(core):break
        prefix+=lead.group();suffix=close+suffix
        core=core[lead.end():-len(close)]
    return prefix,core,suffix


def repair_id_typo(text,tokens):
    from translation import PLACEHOLDER,movable_token
    matches=list(PLACEHOLDER.finditer(text))
    found=[int(m.group(1) or m.group(2)) for m in matches]
    missing=list((Counter(range(len(tokens)))-Counter(found)).elements())
    extra=list((Counter(found)-Counter(range(len(tokens)))).elements())
    if len(missing)!=1 or len(extra)!=1 or extra[0]<len(tokens):return text
    wanted=missing[0]
    if movable_token(tokens[wanted]):return text
    # Common observed repeated-digit typo: 001 -> 011. Do not renumber
    # arbitrary IDs, or use a tag marker to fabricate a missing name.
    old='%03d'%extra[0];new='%03d'%wanted
    if len(old)!=3 or old[0]!='0' or old[1]!=old[2] or new!='00'+old[2]:return text
    return PLACEHOLDER.sub(lambda m:'<rpt%03d/>'%wanted
        if int(m.group(1) or m.group(2))==extra[0] else m.group(),text)


def restore_output(text,tokens):
    from translation import restore
    if not isinstance(text,str):return restore(text,tokens)
    return restore(repair_id_typo(text,tokens),tokens)


def restore_saved(source,text):
    """Saved outputs used the original numbering, before wrapper extraction."""
    from translation import protect,restore,PLACEHOLDER
    tokens=protect(source)[1]
    text=repair_id_typo(text,tokens)
    try:return restore(text,tokens)
    except ValueError:pass
    prefix,core,suffix=split_outer_styles(source)
    if not prefix:raise ValueError('No deterministic wrapper recovery')
    nleft=len(protect(prefix)[1]);nright=len(protect(suffix)[1])
    inner=tokens[nleft:len(tokens)-nright]
    present=[int(m.group(1) or m.group(2)) for m in PLACEHOLDER.finditer(text)]
    if inner and any(i<nleft or i>=len(tokens)-nright for i in present):
        # Partially retained wrappers can mean IDs shifted onto a name. Do not
        # relocate that name by interpreting the remaining markers differently.
        raise ValueError('Ambiguous partial wrapper; retry the inner text')
    def convert(m):
        index=int(m.group(1) or m.group(2))
        if index>=len(tokens):raise ValueError('Unknown placeholder')
        if index<nleft or index>=len(tokens)-nright:return ''
        return '<rpt%03d/>'%(index-nleft)
    # Inner variable counts are still checked by restore.
    return prefix+restore(PLACEHOLDER.sub(convert,text),inner)+suffix


def retry_details(rows,errors):
    from translation import protect,movable_token
    notes=[]
    for row in rows:
        _,core,_=split_outer_styles(row['source'])
        tokens=protect(core)[1]
        if tokens:
            notes.append('Required markers for this item: '+', '.join(
                '<rpt%03d/> = %s (%s)'%(i,t,'variable; once only' if movable_token(t) else 'tag; keep order')
                for i,t in enumerate(tokens)))
        error=errors.get(row.get('id'))
        if error:notes.append('Previous error: '+error.partition('; output=')[0][:400])
    if any('format field mismatch' in e for e in errors.values()):
        notes.append('Preserve actual percent-format fields; ordinary percentages are numbers, not variables.')
    return '\n'+'\n'.join(notes) if notes else ''
