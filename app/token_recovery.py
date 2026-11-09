"""Deterministic repairs only; missing names and inline tag positions are not guessed."""
import re
from collections import Counter
from renpy_text_syntax import PURE_VISUAL_TAG_NAMES

# Official text.html separates pure display styling from links, ruby content,
# accessibility, pacing and referenced styles/shaders. Only this shared visual
# registry is removable; every other source tag remains an opaque protected unit.
# feature's OpenType name uses a colon, but its close is still {/feature}.
STYLE = re.compile(r'\{(/?)('+ '|'.join(sorted(PURE_VISUAL_TAG_NAMES))
                   +r')(?:(?<=feature):[^{}=]+)?(?:=[^{}]*)?\}')
MARKER_FRAGMENT = re.compile(r'[<\[]\s*/?\s*[a-z_]*r(?:t)?pt\s*[_-]?\s*\d+',re.I)
MARKER_TAG = re.compile(r'[<\[]\s*/?\s*[a-z_]*r(?:t)?pt\s*[_-]?\s*(\d+)\s*/?\s*[>\]]',re.I)
RTPT_TAG = re.compile(r'[<\[]\s*/?\s*rtpt\s*[_-]?\s*(\d+)\s*/?\s*[>\]]',re.I)
ANGLE_TAG = re.compile(r'</?[A-Za-z_][A-Za-z0-9_:.-]*(?:[ \t]+[^<>\n]*)?\s*/?>')
TARGET_ID_TAG = re.compile(r'[<\[]\s*/?\s*target_id\b',re.I)


def strip_visual_styles(source):
    """One pass; escaped braces/brackets and all functional tags stay intact."""
    from translation import TOKENS
    return TOKENS.sub(lambda match:'' if STYLE.fullmatch(match.group()) else match.group(),source)


def prepare_renpy_source(source):
    """Remove display styling without splitting prose or relocating functional text.

    Whole-text wrappers and formatting around only protected units have exact
    anchors. Other inline prose styling is omitted rather than word-aligned.
    The old-ID map also repairs saved responses without another model call.
    """
    from translation import TOKENS,FORMATS
    pieces=[];protected_pieces=[];position=0;offset=0;tokens=[];original=[];old_to_clean=[]
    units=[];spans=[];opened={};style_count=0;unmatched_closes=0;literal_angles=set()
    for match in TOKENS.finditer(source,formats=FORMATS):
        before=source[position:match.start()];pieces.append(before);protected_pieces.append(before);offset+=len(before)
        literal_angles.update(ANGLE_TAG.findall(before))
        token=match.group();tag=STYLE.fullmatch(token)
        original.append(token)
        if tag:
            style_count+=1;old_to_clean.append(None)
            if not tag[1]:
                span={'start':offset,'end':None,'prefix':token,'suffix':'','name':tag[2],'order':len(spans)}
                spans.append(span);opened.setdefault(tag[2],[]).append(span)
            elif opened.get(tag[2]):
                span=opened[tag[2]].pop();span['end']=offset;span['suffix']=token
            else:unmatched_closes+=1
        else:
            index=len(tokens);tokens.append(token);old_to_clean.append(index)
            units.append({'start':offset,'end':offset+len(token),'index':index})
            pieces.append(token);offset+=len(token)
            protected_pieces.append('<rpt%03d/>'%index)
        position=match.end()
    tail=source[position:];pieces.append(tail);protected_pieces.append(tail);literal_angles.update(ANGLE_TAG.findall(tail));clean=''.join(pieces)
    for span in spans:
        if span['end'] is None:span['end']=len(clean)
    left=len(clean)-len(clean.lstrip());right=len(clean.rstrip())
    whole=[];anchored={};omitted=unmatched_closes
    for span in spans:
        if span['start']<=left and span['end']>=right:
            whole.append(span);continue
        enclosed=[unit for unit in units if unit['start']>=span['start'] and unit['end']<=span['end']]
        cursor=span['start'];prose=[]
        for unit in enclosed:
            prose.append(clean[cursor:unit['start']]);cursor=unit['end']
        prose.append(clean[cursor:span['end']])
        if enclosed and not ''.join(prose).strip():
            for unit in enclosed:anchored.setdefault(unit['index'],[]).append(span)
        else:omitted+=1
    anchors={index:{'prefix':''.join(s['prefix'] for s in styles),
                    # Close an anchored open-to-end style locally: a reordered
                    # variable must not start styling the rest of the sentence.
                    'suffix':''.join(s['suffix'] or '{/'+s['name']+'}' for s in reversed(styles))}
             for index,styles in anchored.items()}
    plan={'version':1,'functional_tokens':tokens,'original_tokens':original,
          'protected_text':''.join(protected_pieces),
          'source_raw_tokens':frozenset(tokens),'literal_angle_tags':frozenset(literal_angles),
          'old_to_clean':old_to_clean,'anchors':anchors,
          'whole_prefix':''.join(s['prefix'] for s in whole),
          'whole_suffix':''.join(s['suffix'] for s in reversed(whole)),
          'source_style_tags':style_count,'omitted_inline_styles':omitted}
    return clean,plan


def _repair_rtpt(text,tokens):
    """Normalize spelling only; restore checks missing/duplicate IDs once."""
    if not RTPT_TAG.search(text):return text,0
    count=0
    def canonical(match):
        nonlocal count
        index=int(match[1])
        if index>=len(tokens):return match.group()
        count+=1
        return '<rpt%03d/>'%index
    return RTPT_TAG.sub(canonical,text),count


def _strip_generated_visual(text):
    from translation import TOKENS
    count=0
    def strip(match):
        nonlocal count
        if STYLE.fullmatch(match.group()):count+=1;return ''
        return match.group()
    return TOKENS.sub(strip,text),count


def _cleanup_generated_tags(text,plan):
    """Strip generated tag syntax, retaining its prose and source-owned codes.

    target_id is deliberately reserved for the protocol parser. A mismatched
    envelope must not turn into an accepted translation by generic cleanup.
    """
    from translation import TOKENS,PLACEHOLDER
    counts={}
    source_tokens=plan['source_raw_tokens'];literal_angles=plan['literal_angle_tags']
    def clean(match):
        fragment=match.group()
        if fragment in ('{{','[['):return fragment
        if (TARGET_ID_TAG.match(fragment) or PLACEHOLDER.fullmatch(fragment)
                or MARKER_TAG.fullmatch(fragment)):
            return fragment
        if STYLE.fullmatch(fragment):key='raw_style_tags_removed'
        elif fragment.startswith(('{','[')):
            if fragment in source_tokens:return fragment
            key='generated_engine_tags_removed'
        else:
            if fragment in literal_angles:return fragment
            key='generated_angle_tags_removed'
        counts[key]=counts.get(key,0)+1
        return ''
    pieces=[];position=0
    for match in TOKENS.finditer(text,angle_tags=True):
        pieces.append(text[position:match.start()]);pieces.append(clean(match))
        position=match.end()
    pieces.append(text[position:])
    return ''.join(pieces),counts


def restore_prepared_renpy_output(source,text,plan):
    """Restore clean functional IDs once; no language validation or extra inference."""
    from translation import PLACEHOLDER,restore
    original=text;tokens=plan['functional_tokens'];changes={}
    try:
        if not isinstance(text,str):return restore(text,tokens),changes
        text,cleaned=_cleanup_generated_tags(text,plan)
        changes.update(cleaned)
        if not tokens:
            count=0
            def discard(match):
                nonlocal count
                count+=1;return ''
            text=MARKER_TAG.sub(discard,text)
            if count:changes['unused_tool_markers_removed']=count
        else:
            text,repaired=_repair_rtpt(text,tokens)
            if repaired:changes['marker_spelling_repaired']=repaired
            text=repair_id_typo(text,tokens)
        anchors=plan['anchors']
        def decorate(match):
            index=int(match[1] or match[2]);style=anchors.get(index)
            return style['prefix']+match.group()+style['suffix'] if style else match.group()
        if anchors:text=PLACEHOLDER.sub(decorate,text)
        restored=restore(text,tokens)
        if MARKER_FRAGMENT.search(restored):raise ValueError('unrestored placeholder')
        restored=plan['whole_prefix']+restored+plan['whole_suffix']
    except ValueError as exc:
        reason=str(exc).partition('; output=')[0]
        raise ValueError(reason+'; output='+repr(original)) from exc
    if plan['omitted_inline_styles']:changes['inline_styles_omitted']=plan['omitted_inline_styles']
    return restored,changes


def split_outer_styles(source, *, allow_unclosed=False):
    from translation import TOKENS
    prefix='';suffix='';core=source
    while True:
        lead=STYLE.match(core)
        if not lead or lead.group(1):break
        close='{/'+lead.group(2)+'}'
        if not core.endswith(close) and not allow_unclosed:break
        # Only remove a wrapper whose matching close is the final token.
        depth=0;end=None
        for token in TOKENS.finditer(core):
            tag=STYLE.fullmatch(token.group())
            if not tag:continue
            if tag.group(2)!=lead.group(2):continue
            depth += -1 if tag.group(1) else 1
            if depth==0:end=token.end();break
        # Ren'Py can leave a visual style open until the text ends. Only the
        # output repair uses this; prompt extraction/marker numbering stays put.
        if end is None and allow_unclosed:
            prefix+=lead.group();core=core[lead.end():];continue
        if end!=len(core):break
        prefix+=lead.group();suffix=close+suffix
        core=core[lead.end():-len(close)]
    return prefix,core,suffix


def repair_id_typo(text,tokens):
    from translation import PLACEHOLDER,movable_token
    found=[int(m.group(1) or m.group(2)) for m in PLACEHOLDER.finditer(text)]
    if not any(index>=len(tokens) for index in found):return text
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


def _restore_numbered_output(source,text, *, allow_unclosed=False, legacy_numbering=False):
    """Restore original IDs; only whole-text visual wrappers may be rebuilt."""
    from translation import protect,restore,PLACEHOLDER
    tokens=protect(source)[1]
    if not isinstance(text,str):return restore(text,tokens),False
    text=repair_id_typo(text,tokens)
    try:return restore(text,tokens),False
    except ValueError as exc:original_error=exc
    prefix,core,suffix=split_outer_styles(source,allow_unclosed=allow_unclosed)
    if not prefix:raise original_error
    nleft=len(protect(prefix)[1]);nright=len(protect(suffix)[1])
    inner=tokens[nleft:len(tokens)-nright]
    present=[int(m.group(1) or m.group(2)) for m in PLACEHOLDER.finditer(text)]
    if legacy_numbering and inner and any(i<nleft or i>=len(tokens)-nright for i in present):
        # Partially retained wrappers can mean IDs shifted onto a name. Do not
        # relocate that name by interpreting the remaining markers differently.
        raise ValueError('Ambiguous partial wrapper; retry the inner text')
    def convert(m):
        index=int(m.group(1) or m.group(2))
        if index>=len(tokens):raise ValueError('Unknown placeholder')
        if index<nleft or index>=len(tokens)-nright:return ''
        return '<rpt%03d/>'%(index-nleft)
    # Inner variable counts are still checked by restore.
    return prefix+restore(PLACEHOLDER.sub(convert,text),inner)+suffix,True


def restore_renpy_output(source,text, *, legacy_numbering=False):
    """Read legacy source-numbered markers, dropping only known visual IDs.

    ``source`` is the exact original numbering domain: the full source for old
    saves, or the old stripped core when its error says Wrapper-stripped.
    """
    from translation import PLACEHOLDER
    clean,plan=prepare_renpy_source(source);original=text;changes={}
    if not isinstance(text,str):return restore_prepared_renpy_output(clean,text,plan)
    text,removed=_strip_generated_visual(text)
    if removed:changes['raw_style_tags_removed']=removed
    text=repair_id_typo(text,plan['original_tokens'])
    found=Counter(int(m[1] or m[2]) for m in PLACEHOLDER.finditer(text))
    visual_ids=[i for i,value in enumerate(plan['old_to_clean']) if value is None]
    damaged_visual=any(found[i]!=1 for i in visual_ids)
    repaired_visual_spellings=0
    wire=re.compile('(?:'+PLACEHOLDER.pattern+')|(?:'+MARKER_TAG.pattern+')',re.I)
    def reindex(match):
        nonlocal repaired_visual_spellings
        fragment=match.group();valid=PLACEHOLDER.fullmatch(fragment)
        malformed=MARKER_TAG.fullmatch(fragment)
        index=int(valid[1] or valid[2]) if valid else int(malformed[1])
        if not plan['functional_tokens']:return ''
        if index>=len(plan['old_to_clean']):return fragment
        target=plan['old_to_clean'][index]
        if target is None:
            if not valid:repaired_visual_spellings+=1
            return ''
        if valid:return '<rpt%03d/>'%target
        if RTPT_TAG.fullmatch(fragment):return '<rtpt%03d/>'%target
        return fragment
    try:
        text=wire.sub(reindex,text)
        restored,more=restore_prepared_renpy_output(clean,text,plan)
    except ValueError as exc:
        reason=str(exc).partition('; output=')[0]
        raise ValueError(reason+'; output='+repr(original)) from exc
    changes.update(more)
    if damaged_visual and plan['whole_prefix']:changes['outer_style_markers_rebuilt']=1
    if repaired_visual_spellings:changes['visual_marker_spellings_omitted']=repaired_visual_spellings
    return restored,changes


def restore_saved(source,text, *, visual_styles=True):
    """Saved outputs used the original numbering, before wrapper extraction."""
    if visual_styles:return restore_renpy_output(source,text,legacy_numbering=True)[0]
    return _restore_numbered_output(source,text,legacy_numbering=True)[0]


def retry_details(rows,errors,cfg=None):
    from translation import protect,movable_token
    notes=[]
    for row in rows:
        if cfg and cfg.get('engine')=='rpgmaker':
            from rpgmaker_tokens import split_outer_controls
            _,core,_=split_outer_controls(row['source'])
        else:core,_=prepare_renpy_source(row['source'])
        tokens=protect(core,cfg)[1]
        if tokens:
            notes.append('Required markers for this item: '+', '.join(
                '<rpt%03d/> = %s (%s)'%(i,t,'variable; once only' if movable_token(t) else 'tag; keep order')
                for i,t in enumerate(tokens)))
        error=errors.get(row.get('id'))
        if error:notes.append('Previous error: '+error.partition('; output=')[0][:400])
    if any('format field mismatch' in e for e in errors.values()):
        notes.append('Preserve actual percent-format fields; ordinary percentages are numbers, not variables.')
    return '\n'+'\n'.join(notes) if notes else ''
