"""RPG-only marker-loss retry: readable input, local control reconstruction."""
import json
from pathlib import Path
import re

DISPLAY = re.compile(r'\\(?:(?:FS|C)\[-?\d+\]|[.!|^><${}])', re.I)
VALUE = re.compile(r'\\([NPV])\[(\d+)\]', re.I)
CLAUSE = re.compile(r'[.!?。！？…\n]+')


def representatives(project, cfg, rows, known):
    """Read only prepared actor/party metadata; never inspect the original game."""
    actors = {}
    for row in rows:
        if Path(row.get('file', '')).name != 'Actors.json':
            continue
        match = re.fullmatch(r'\[(\d+)\]\.name', row.get('location', {}).get('ptr', ''))
        if not match:
            continue
        saved = known.get(row['id'], {})
        source = row['source'].strip()
        target = saved.get('text', source).strip()
        if saved.get('status') == 'source_fallback' or not re.fullmatch(r'[\w .\-·]+', target):
            target = source
        actors[int(match[1])] = {'source': source, 'text': target}
    # IDs in Actors.json are normally array indices; use the actual IDs when available.
    snapshot = project / 'data/rpg-source' / cfg.get('rpg_base', '.') / 'data'
    actor_file = snapshot / 'Actors.json'
    if actor_file.is_file():
        for index, actor in enumerate(json.loads(actor_file.read_text(encoding='utf-8-sig'))):
            if not isinstance(actor, dict):
                continue
            actors[int(actor.get('id', index))] = actors.get(index, {
                'source': actor.get('name', ''), 'text': actor.get('name', '')})
    system_file = snapshot / 'System.json'
    system = json.loads(system_file.read_text(encoding='utf-8-sig')) if system_file.is_file() else {}
    values = {'\\N[%d]' % key: value for key, value in actors.items() if value['text']}
    for slot, actor_id in enumerate(system.get('partyMembers', []), 1):
        if actor_id in actors and actors[actor_id]['text']:
            values['\\P[%d]' % slot] = actors[actor_id]
    if system.get('currencyUnit'):
        values['\\G'] = {'source': system['currencyUnit'], 'text': system['currencyUnit']}
    return values


def plan(source, error, cfg):
    """Use one plain retry for a marker-loss failure, not for every translation."""
    if 'RPG plain retry failed:' in error or not any(
            reason in error for reason in ('Protected token mismatch', 'RPG Maker control code mismatch')):
        return None
    from rpgmaker_layout import segments
    parts=segments(source)
    if any(part['prefix'] for part in parts):
        plans=[]
        for part in parts:
            body=_plan(part['source'],cfg)
            if body is None:
                body={'text':part['source'],'controls':[],'variables':[],'glossary':{}}
            plans.append(dict(part,plan=body))
        return {'segments':plans,'text':'\n'.join(part['plan']['text'] for part in plans),
                'variables':[value for part in plans for value in part['plan']['variables']],
                'glossary':{key:value for part in plans for key,value in part['plan']['glossary'].items()}}
    return _plan(source,cfg)


def _plan(source,cfg):
    from rpgmaker_tokens import protect
    from gameloc_vendor import load
    load()
    from gameloc.protect import PATTERNS
    from source_language import normalize
    separator='' if normalize(cfg.get('source_language'))=='japanese' else ' '
    source=separator.join(source.splitlines())
    atom = re.compile('|'.join(PATTERNS))
    masked, groups = protect(source)
    text = ''; controls = []; variables = []; glossary = {}
    cursor = 0
    from rpgmaker_protocol import ANCHOR
    defaults = cfg.get('_rpg_representatives', {})
    numbers = set(re.findall(r'\d+', ANCHOR.sub('', masked)))
    number_values = {}
    for marker in ANCHOR.finditer(masked):
        text += masked[cursor:marker.start()]
        group = groups[int(marker[1])]
        pieces = [match.group() for match in atom.finditer(group)]
        if ''.join(pieces) != group:
            raise ValueError('unsupported protected literal')
        for code in pieces:
            if DISPLAY.fullmatch(code):
                controls.append({'code': code, 'offset': len(text)})
                continue
            match = VALUE.fullmatch(code)
            if not match and code.upper() != '\\G':
                raise ValueError('unsupported code: ' + code)
            data = defaults.get(code.upper(), {})
            representative = data.get('text', '')
            if not representative and match and match[1].upper() in ('N', 'P'):
                representative = '존'
            if not representative and match and match[1].upper() == 'V':
                if code.upper() not in number_values:
                    number = 1
                    while str(number) in numbers:
                        number += 1
                    number_values[code.upper()] = str(number); numbers.add(str(number))
                representative = number_values[code.upper()]
            if not representative:
                raise ValueError('no readable representative: ' + code)
            if data.get('source') and data['source'] != representative:
                glossary[data['source']] = representative
            variables.append({'code': code, 'text': representative, 'start': len(text),
                              'end': len(text) + len(representative)})
            text += representative
        cursor = marker.end()
    text += masked[cursor:]
    if not controls and not variables:
        return None
    return {'text': text, 'controls': controls, 'variables': variables, 'glossary': glossary}


def reference(source, cfg):
    """Keep readable variable values in nearby dialogue without exposing codes."""
    try:
        result=plan(source,'Protected token mismatch',cfg)
        if result is not None:
            return result['text']
    except ValueError:
        pass
    from rpgmaker_tokens import protect
    from rpgmaker_protocol import ANCHOR
    return ANCHOR.sub('',protect(source)[0])


def _boundary(source, target, offset):
    """Coarse clause/word placement, never split a Korean word or syllable."""
    if offset <= 0:
        return 0
    if offset >= len(source):
        return len(target)
    a = [0] + [match.end() for match in CLAUSE.finditer(source)] + [len(source)]
    b = [0] + [match.end() for match in CLAUSE.finditer(target)] + [len(target)]
    if len(a) == len(b):
        part = next(i for i in range(len(a)-1) if offset <= a[i+1])
        start, end = a[part:part+2]; left, right = b[part:part+2]
    else:
        start, end, left, right = 0, len(source), 0, len(target)
    position = left + (offset-start) / max(1, end-start) * (right-left)
    boundaries = {left, right}
    boundaries.update(left + match.start() for match in re.finditer(r'\s+|[,.!?;:。！？、…]', target[left:right]))
    return min(boundaries, key=lambda point: (abs(point-position), point))


def restore(result, value):
    """Check content/codes, not literary quality; log approximate display positions."""
    if 'segments' in result:
        output=[];details=[]
        for index,part in enumerate(result['segments']):
            if not part['plan']['text'].strip():
                restored=part['source'];audit={}
            else:
                key=part.get('model_key',str(index))
                restored,audit=restore(part['plan'],value[key])
            output.append(part['prefix']+restored)
            details.append(dict(segment=index,prefix=part['prefix'],**audit))
        return ''.join(output),{'segments':details,'policy':'exact_position_boundaries; local_display_restoration'}
    from rpgmaker_protocol import ANCHOR, WIRE
    from rpgmaker_tokens import protect
    if not isinstance(value, str) or not value.strip():
        raise ValueError('empty plain translation')
    value = value.strip()
    if ANCHOR.search(value) or WIRE.search(value) or protect(value)[1]:
        raise ValueError('plain translation added a code or marker')
    by_text = {}
    for variable in result['variables']:
        by_text.setdefault(variable['text'], []).append(variable)
    spans = []; placements = []
    for text, variables in by_text.items():
        pattern = re.escape(text)
        if re.fullmatch(r'[A-Za-z0-9 _.-]+', text):
            pattern = r'(?<![A-Za-z0-9])' + pattern + r'(?![A-Za-z0-9])'
        elif re.fullmatch(r'[가-힣]+',text):
            boundary=r'(?=$|[\s,.!?;:。！？、…\"\'()])'
            particle=r'(?:에게|한테|으로|부터|까지|처럼|보다|하고|은|는|이|가|을|를|의|께|와|과|도|만|로|랑|야|아|씨|님)'
            pattern=r'(?<![가-힣A-Za-z0-9])'+pattern+r'(?='+boundary+'|'+particle+'+'+boundary+')'
        matches = list(re.finditer(pattern, value, re.I))
        if len(matches) != len(variables):
            raise ValueError('representative missing or duplicated: ' + text)
        for variable, match in zip(variables, matches):
            if any(match.start() < end and start < match.end() for start, end, _ in spans):
                raise ValueError('overlapping representatives')
            spans.append((match.start(), match.end(), variable['code']))
            placements.append(dict(variable, target_start=match.start(), target_end=match.end()))
    controls = []; last = 0
    for control in result['controls']:
        position = None
        for variable in placements:
            if control['offset'] == variable['start']:
                position = variable['target_start']; break
            if control['offset'] == variable['end']:
                position = variable['target_end']; break
        exact = position is not None or control['offset'] in (0, len(result['text']))
        if position is None:
            position = _boundary(result['text'], value, control['offset'])
        position = max(last, position)
        for start, end, _ in spans:
            if start < position < end:
                position = end
        last = position
        controls.append(dict(control, target_offset=position, approximate=not exact))
    insertions = {}
    for control in controls:
        insertions.setdefault(control['target_offset'], []).append(control['code'])
    replacements = {start: (end, code) for start, end, code in spans}
    output = []; cursor = 0
    while cursor <= len(value):
        output.extend(insertions.get(cursor, []))
        if cursor == len(value):
            break
        if cursor in replacements:
            cursor, code = replacements[cursor]; output.append(code)
        else:
            output.append(value[cursor]); cursor += 1
    return ''.join(output), {'controls': controls, 'variables': placements,
                             'policy': 'clause_word_boundaries; exact_variable_values'}
