"""Separate only RPG Maker's documented colour and font-size display escapes."""
import re
from collections import Counter

VISUAL = re.compile(r'\\(?:C|FS)\[-?\d+\]|\\[{}]', re.I)


def _atoms(text):
    from gameloc_vendor import load
    load()
    from gameloc.protect import PATTERNS
    return list(re.finditer('|'.join(PATTERNS), text))


def prepare(source):
    """Return clean text and an immutable-by-convention reconstruction plan.

    Functional atoms are parsed first, so apparent escapes inside plugin arguments
    or literal escaped backslashes are never mistaken for standalone styling.
    """
    atoms = _atoms(source)
    styles = [m for m in atoms if VISUAL.fullmatch(m.group())]
    chunks=[]; cursor=0; positions=[]; clean_length=0
    for m in styles:
        chunk=source[cursor:m.start()]
        chunks.append(chunk); clean_length+=len(chunk)
        positions.append((clean_length,m.group()))
        cursor=m.end()
    chunks.append(source[cursor:]); clean=''.join(chunks)
    prefix=[]; suffix=[]; anchors=[]; used=set()
    # Whole-text styles are exact only when no style transition occurs inside.
    if all(pos in (0,len(clean)) for pos,_ in positions):
        for i,(pos,code) in enumerate(positions):
            (prefix if pos == 0 else suffix).append(code); used.add(i)
    else:
        # A pair surrounding precisely one uniquely identifiable functional atom
        # can move with it. Never infer a word position or synthesize a reset.
        clean_atoms=_atoms(clean)
        atom_counts=Counter(m.group() for m in clean_atoms)
        atom_at={(m.start(),m.end()):m for m in clean_atoms}
        for i in range(len(positions)-1):
            if i in used or i+1 in used: continue
            start,opening=positions[i]; end,closing=positions[i+1]
            if start>=end: continue
            # Only explicit reset pairs delimit an isolated decorated value.
            # FS changes set an absolute size and have no known default here.
            color_pair = (re.fullmatch(r'\\C\[-?\d+\]', opening, re.I) and
                          re.fullmatch(r'\\C\[0\]', closing, re.I))
            size_pair = opening == r'\{' and closing == r'\}'
            if not (color_pair or size_pair): continue
            candidate=atom_at.get((start,end))
            if candidate is not None and atom_counts[candidate.group()]==1:
                anchors.append((candidate.group(),opening,closing))
                used.update((i,i+1))
    return clean, {'source':source, 'clean':clean, 'prefix':prefix, 'suffix':suffix,
                   'anchors':anchors, 'omitted':len(positions)-len(used),
                   'source_style_codes':len(positions)}


def restore(translated_clean, plan):
    """Restore only exact whole-text or unique functional-token boundaries."""
    if not isinstance(translated_clean,str): raise ValueError('Translation is not a string')
    pieces=[]; cursor=0; removed=0
    for match in _atoms(translated_clean):
        if VISUAL.fullmatch(match.group()):
            pieces.append(translated_clean[cursor:match.start()]); cursor=match.end(); removed+=1
    pieces.append(translated_clean[cursor:])
    result=''.join(pieces); omitted=plan['omitted']; applied=list(plan['prefix'])+list(plan['suffix'])
    for token,opening,closing in plan['anchors']:
        matches=[m for m in _atoms(result) if m.group()==token]
        if len(matches)!=1:
            omitted+=2
            continue
        m=matches[0]
        result=result[:m.start()]+opening+token+closing+result[m.end():]
        applied.extend((opening,closing))
    result=''.join(plan['prefix'])+result+''.join(plan['suffix'])
    return result, {'visual_codes_omitted':omitted, 'visual_codes_restored':len(applied),
                    'applied_visual_codes':applied, 'model_visual_codes_removed':removed}


def validation_pair(plan, target, audit):
    """Compare functional codes after removing exactly the restored display codes.

    Unexpected extra display escapes remain in the target and fail validation.
    """
    expected=Counter(audit['applied_visual_codes']); found=Counter()
    pieces=[];cursor=0
    for m in _atoms(target):
        code=m.group()
        if VISUAL.fullmatch(code) and found[code]<expected[code]:
            pieces.append(target[cursor:m.start()]);cursor=m.end();found[code]+=1
    pieces.append(target[cursor:])
    if found != expected: raise ValueError('restored visual code mismatch')
    return plan['clean'], ''.join(pieces)


def canonicalize_validation(source, target):
    """Ignore omitted source-owned visual codes, never newly invented codes."""
    clean, plan = prepare(source)
    available = Counter(m.group() for m in _atoms(source) if VISUAL.fullmatch(m.group()))
    used = Counter(); pieces = []; cursor = 0
    for match in _atoms(target):
        code = match.group()
        if VISUAL.fullmatch(code) and used[code] < available[code]:
            pieces.append(target[cursor:match.start()]); cursor = match.end(); used[code] += 1
    pieces.append(target[cursor:])
    return clean, ''.join(pieces)
