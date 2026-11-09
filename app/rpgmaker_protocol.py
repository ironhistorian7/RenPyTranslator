"""RPG Maker-only protocol repairs and error-specific retry instructions."""
import ast
from difflib import SequenceMatcher
import re
import unicodedata

MARKER=re.compile(r'(?:\\+)?[<\[]\s*rpt\s*[_-]?\s*(\d+)\s*(?:/(?:\\+)?\s*[>\]]|(?:\\+)?(?:>\]|\]>|[>\]]))',re.I)
HEADER=re.compile(r'(?m)^\[(?:(?:[A-Za-z][A-Za-z0-9]*_)+)?(\d+)\][ \t]*')
WIRE=re.compile(r'⟦\s*(?:RPT\s*)?(\d+)\s*[⟧⟦⟩]',re.I)
WORD=re.compile(r'[가-힣\u3041-\u3096\u30a1-\u30fa\uff66-\uff9d\u3400-\u9fffA-Za-z0-9ー]+')
ANCHOR=re.compile(r'<rpt(\d+)/>')
DISPLAY_CONTROL=re.compile(r'(?:\\(?:[.|><{}]|(?:PX|PY|FS|C)\[-?\d+\]))+',re.I)
PUNCTUATION=re.compile(r'[.!?…！？。、，：；:;,〜～~]+')
SENTENCE_END=re.compile(r'[.!?…。！？]+')


def normalize_markers(text):
    text=WIRE.sub(lambda m:'<rpt%03d/>'%int(m[1]),text)
    return MARKER.sub(lambda m:'<rpt%03d/>'%int(m[1]),text)


def wire_markers(text):
    """The model sees numeric anchors, not HTML-shaped placeholders."""
    return re.sub(r'<rpt(\d+)/>',lambda m:'⟦RPT%03d⟧'%int(m[1]),text)


def wire_inputs(inputs):
    return [dict(item,text=wire_markers(item['text']),
                 movable_placeholders=[wire_markers(t) for t in item.get('movable_placeholders',[])],
                 name_hints={wire_markers(k):v for k,v in item.get('name_hints',{}).items()}) for item in inputs]


def parse(content,count):
    from hy_backend import parse as normal_parse
    parsed=normal_parse(content)
    # One requested item, one explicit numeric header with a harmless prefix.
    # Never merge records, accept duplicate headers or guess another number.
    headers=list(HEADER.finditer(content.strip()))
    alias=False
    if count==1 and not parsed and len(headers)==1 and headers[0].start()==0 and headers[0][1]=='0':
        parsed={'0':content.strip()[headers[0].end():].strip()};alias=True
    return parsed,alias


def previous_output(error):
    if '; output=' not in error:return None
    try:value=ast.literal_eval(error.split('; output=',1)[1])
    except (SyntaxError,ValueError):return None
    return value if isinstance(value,str) else None


def correction_draft(source,error,cfg):
    """Only isolate language residue in a complete, structurally valid draft."""
    if 'untranslated Japanese text' not in error:return None
    value=previous_output(error)
    if not value or not re.search(r'[가-힣]',value):return None
    from rpgmaker_tokens import split_outer_controls,protect,validate
    from translation import restore
    prefix,core,suffix=split_outer_controls(source);_,tokens=protect(core)
    value=normalize_markers(value)
    try:target=prefix+restore(value,tokens)+suffix
    except ValueError:return None
    errors=validate(source,target,cfg)
    return value if errors and set(errors)=={'untranslated Japanese text'} else None


def correction_parts(draft,limit=8,tokens=()):
    """Read through known display anchors; values and unknown codes stay boundaries."""
    from rpgmaker_tokens import KANA
    words=[];positions=[];indices={}
    offset=0
    for match in WORD.finditer(draft):
        if match.start()<offset:continue
        end=match.end()
        while True:
            cursor=end
            while True:
                anchor=ANCHOR.match(draft,cursor)
                if not anchor:break
                index=int(anchor[1])
                if index>=len(tokens) or not DISPLAY_CONTROL.fullmatch(tokens[index]):break
                cursor=anchor.end()
            following=WORD.match(draft,cursor)
            if cursor==end or not following:break
            end=following.end()
        offset=end
        word=draft[match.start():end]
        if not KANA.search(word):continue
        punctuation=PUNCTUATION.match(draft,end)
        if punctuation:end=punctuation.end();word=draft[match.start():end];offset=end
        if word not in indices:indices[word]=len(words);words.append(word)
        positions.append((match.start(),end,str(indices[word])))
    if not words or len(words)>limit:return None
    return words,positions


def correction_plan(source,error,cfg):
    draft=correction_draft(source,error,cfg)
    if draft is None:return None
    from rpgmaker_tokens import split_outer_controls,protect
    _,core,_=split_outer_controls(source);_,tokens=protect(core)
    parts=correction_parts(draft,cfg.get('batch_size',8),tokens)
    return (draft,parts) if parts is not None else None


def correction_ending(original,value):
    """Keep original punctuation when only glyph width/repetition changed."""
    previous=ANCHOR.sub('',original);visible=ANCHOR.sub('',value)
    ending=re.search(PUNCTUATION.pattern+'$',previous)
    emitted=re.search(PUNCTUATION.pattern+'$',visible)
    def key(text):return re.sub(r'(.)\1+',r'\1',unicodedata.normalize('NFKC',text))
    if ending:
        if emitted:
            if not key(ending[0]).startswith(key(emitted[0])):
                raise ValueError('RPG Japanese span correction changed punctuation')
            return value[:-len(emitted[0])]+ending[0]
        if PUNCTUATION.search(visible):raise ValueError('RPG Japanese span correction added text after punctuation')
        return value+ending[0]
    if PUNCTUATION.search(visible):raise ValueError('RPG Japanese span correction added punctuation')
    return value


def align_display_anchors(original,target):
    """Reinsert only uniquely aligned edit boundaries; never scale positions."""
    if ANCHOR.search(target):raise ValueError('Plain correction contains an unexpected anchor')
    target=correction_ending(original,target)
    text=ANCHOR.sub('',original);anchors=[];offset=0;cursor=0
    for marker in ANCHOR.finditer(original):
        offset+=marker.start()-cursor;anchors.append((offset,marker.group()));cursor=marker.end()
    if not anchors:return target,[]
    n,m=len(text),len(target)
    if n*m>65536:raise ValueError('Correction alignment too large; marked correction required')
    forward=[list(range(m+1))]+[[i]+[0]*m for i in range(1,n+1)]
    for i in range(1,n+1):
        for j in range(1,m+1):
            forward[i][j]=min(forward[i-1][j]+1,forward[i][j-1]+1,
                              forward[i-1][j-1]+(text[i-1]!=target[j-1]))
    backward=[[0]*(m+1) for _ in range(n+1)]
    backward[n]=list(range(m,-1,-1))
    for i in range(n-1,-1,-1):
        backward[i][m]=n-i
        for j in range(m-1,-1,-1):
            backward[i][j]=min(backward[i+1][j]+1,backward[i][j+1]+1,
                               backward[i+1][j+1]+(text[i]!=target[j]))
    edits=SequenceMatcher(None,text,target,autojunk=False).get_opcodes()
    placements=[]
    for index,anchor in anchors:
        possible={j for j in range(m+1) if forward[index][j]+backward[index][j]==forward[n][m]}
        justified=set()
        for kind,a,b,c,d in edits:
            if kind=='equal' and a<=index<=b:justified.add(c+index-a)
            elif kind=='delete' and a<=index<=b:justified.add(c)
            elif kind=='replace':
                if index==a:justified.add(c)
                if index==b:justified.add(d)
        if len(possible)!=1 or not possible.issubset(justified):
            raise ValueError('Correction anchor has no unique unchanged-text/edit boundary: '+anchor)
        placements.append({'anchor':anchor,'source_offset':index,'target_offset':next(iter(possible))})
    result=target
    for placement in reversed(placements):
        index=placement['target_offset'];result=result[:index]+placement['anchor']+result[index:]
    return result,placements


def apply_corrections(draft,positions,values):
    replacements={}
    for start,end,key in positions:
        value=values.get(key)
        original=draft[start:end]
        if isinstance(value,str):
            value=normalize_markers(value.strip())
            repeated=re.fullmatch(r'([가-힣A-Za-z0-9 \-]+)([.!?…！？。、，：；:;,]+)',value)
            # A fragment excludes surrounding punctuation. Accept an echoed suffix
            # only when those exact bytes already follow this occurrence in the draft.
            if repeated and draft[end:].startswith(repeated[2]):value=repeated[1].strip()
        if not isinstance(value,str):
            raise ValueError('RPG Japanese span correction invalid or missing item '+key)
        # Original marker IDs and order remain explicit; no position projection.
        if ANCHOR.findall(value)!=ANCHOR.findall(original):
            raise ValueError('RPG Japanese span correction marker mismatch item '+key)
        value=correction_ending(original,value)
        visible=ANCHOR.sub('',value);previous=ANCHOR.sub('',original)
        if (not re.fullmatch(r'[가-힣A-Za-z0-9 \-]+(?:[.!?…！？。、，：；:;,〜～~][가-힣A-Za-z0-9 \-]*)*',visible)
                or len(SENTENCE_END.findall(visible))>max(1,len(SENTENCE_END.findall(previous)))):
            raise ValueError('RPG Japanese fragment correction invalid or missing item '+key)
        replacements[start]=value.strip()
    for start,end,key in reversed(positions):draft=draft[:start]+replacements[start]+draft[end:]
    return draft


def retry_instruction(rows,errors,cfg):
    from rpgmaker_tokens import protect,split_outer_controls
    from translation import restore,movable_token
    notes=['이전 출력의 오류를 교정하세요. 입력의 [ID]를 그대로 유지하고 한국어 결과만 출력하세요. '
           '보호 표시는 해석하거나 엔진 코드로 풀어 쓰지 마세요. 새 태그를 만들지 마세요.']
    for index,row in enumerate(rows):
        _,core,_=split_outer_controls(row['source']);_,tokens=protect(core)
        markers=['<rpt%03d/>'%i for i in range(len(tokens))]
        if markers:
            movable=['<rpt%03d/>'%i for i,t in enumerate(tokens) if movable_token(t)]
            notes.append('항목 %d의 필요한 보호 표시: %s. 각각 한 번. 서식 표시의 상대 순서는 유지. 이동 가능한 변수: %s.'%
                         (index,', '.join(markers),', '.join(movable) or '없음'))
        error=errors.get(row['id'],'');candidate=previous_output(error)
        if 'untranslated Japanese text' in error or 'no Korean translation' in error:
            notes.append('항목 %d에 일본어가 남았습니다. 이미 번역된 한국어의 뜻과 말투는 유지하고, 남은 일본어 부분을 원문 문맥에 맞춰 한국어로 교정하세요. 감탄사와 웃음소리도 한글로 표현하세요. 내용을 추가하지 마세요.'%index)
            if candidate:
                candidate=normalize_markers(candidate)
                try:restore(candidate,tokens)
                except ValueError:pass
                else:
                    # No raw engine-code mapping in this prompt, even on retries.
                    if not re.search(r'\\(?:[A-Za-z]+|[.!|^><{}])',candidate):
                        notes.append('항목 %d의 교정할 이전 번역: %s'%(index,candidate))
        elif 'mismatch' in error or 'placeholder' in error:
            notes.append('항목 %d의 보호 표시 번호·개수·순서를 다시 확인하세요. 역슬래시로 감싸지 말고 입력 표기를 그대로 쓰세요.'%index)
        else:
            notes.append('항목 %d의 출력 머리글은 반드시 [%d]이며 설명이나 별도 표제를 붙이지 마세요.'%(index,index))
    return '\n'.join(notes)
