"""Script-only media/navigation evidence. No image decoding or vision model.

Asset names describe provenance, never the visual contents of an image.
"""
import ast
from collections import defaultdict
from pathlib import PurePosixPath
import re

IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.avif', '.gif'}
VIDEO_SUFFIXES = {'.webm', '.mp4', '.ogv', '.avi', '.mpeg', '.mkv'}


def asset_index(root):
    """Read filenames/indexes, never image/video payloads. Stay within one root."""
    import io
    import os
    import zlib
    from pathlib import Path
    from engine import IndexUnpickler
    root = Path(root)
    result = set(); warnings = []
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in ('tl', 'saves', 'cache', '_mods')
                   and not (Path(folder)/d).is_symlink() and not (Path(folder)/d).is_junction()]
        for name in files:
            path = Path(folder)/name
            if path.is_symlink():continue
            if path.suffix.lower() in IMAGE_SUFFIXES | VIDEO_SUFFIXES:
                result.add(path.relative_to(root).as_posix())
            elif path.suffix.lower() == '.rpa' and not name.startswith('0x52_'):
                try:
                    with path.open('rb') as stream:
                        header = stream.readline(80).split()
                        if header[0] not in (b'RPA-2.0', b'RPA-3.0'):raise ValueError('unsupported archive')
                        stream.seek(int(header[1], 16))
                        index = IndexUnpickler(io.BytesIO(zlib.decompress(stream.read())), encoding='bytes').load()
                    for raw in index:
                        value = raw.decode('utf-8') if isinstance(raw, bytes) else raw
                        if PurePosixPath(value).suffix.lower() in IMAGE_SUFFIXES | VIDEO_SUFFIXES:result.add(value)
                except (OSError, ValueError, EOFError, zlib.error) as exc:
                    warnings.append({'file': path.relative_to(root).as_posix(), 'reason': 'media_index: ' + str(exc)})
    return sorted(result), warnings


def strings(code):
    from story_guides import QUOTED, expr, literal
    return [value for match in re.finditer(QUOTED, code)
            if isinstance(value := literal(expr(match[0])), str)]


def definitions(scripts, asset_names=()):
    from story_guides import logical_lines
    images = {}
    for path in asset_names:
        path = str(path).replace('\\', '/')
        if path.startswith('images/') and PurePosixPath(path).suffix.lower() in IMAGE_SUFFIXES:
            name = PurePosixPath(path).stem.lower()
            images.setdefault(name, {'assets': [path], 'animation': False, 'video': False, 'automatic': True})
    for filename, text in sorted(scripts.items()):
        lines = list(logical_lines(text))
        for index, (line, code, indent) in enumerate(lines):
            match = re.match(r'^image\s+(.+?)\s*(?:=\s*(.*)|:)\s*$', code)
            if not match:
                continue
            parts = [match[2] or '']; end = index + 1
            while end < len(lines) and lines[end][2] > indent:
                parts.append(lines[end][1]); end += 1
            content = '\n'.join(parts)
            assets = list(dict.fromkeys(s for s in strings(content) if PurePosixPath(s).suffix.lower() in IMAGE_SUFFIXES | VIDEO_SUFFIXES))
            images[match[1].strip()] = {'assets': assets,
                'references': [s for s in strings(content) if s not in assets],
                'animation': bool(re.search(r'\brepeat\b|\b(?:Animation|Movie)\s*\(', content)) or (match[2] is None and len(assets) > 1),
                'video': any(PurePosixPath(s).suffix.lower() in VIDEO_SUFFIXES for s in assets),
                'automatic': False, 'file': filename, 'line': line}
    for _ in range(8):
        changed=False
        for name,definition in images.items():
            for reference in definition.get('references',[]):
                other=images.get(reference)
                if not other or reference==name:continue
                additional=[p for p in other['assets'] if p not in definition['assets']]
                if additional:definition['assets']+=additional;changed=True
                definition['animation'] |= other['animation']
                definition['video'] |= other['video']
        if not changed:break
    return images


def enrich(scripts, events, asset_names=()):
    """Attach paths/frame membership, preserving source conditions and locations."""
    images = definitions(scripts, asset_names)
    known = set(asset_names)
    for event in events:
        if event['kind'] != 'media':
            continue
        name = event['media_name']; definition = images.get(name, {})
        assets = list(dict.fromkeys(definition.get('assets', []) + event.get('assets', [])))
        event.update(assets=assets, animation=bool(definition.get('animation') or event.get('animation')),
                     video=bool(definition.get('video') or event.get('video')),
                     media_resolved=bool(assets), automatic=definition.get('automatic', False))
        if known:
            event['missing_assets'] = [p for p in assets if p not in known]
    return events


def media_event(code):
    if re.match(r'^play\s+movie\s+',code):
        return {'kind':'media','media_name':'movie','assets':strings(code),'animation':True,'video':True}
    match = re.match(r'^(scene|show)\s+(?!screen\b|text\b|expression\b)(.+?)(?:\s+(?:at|with|onlayer|zorder|as|behind)\b.*|:)?$', code)
    if match:
        return {'kind': 'media', 'media_name': match[2].strip(), 'assets': [], 'animation': False, 'video': False}
    if re.match(r'^(?:scene|show)\s+expression\b|^\$\s*renpy\.movie_cutscene\(', code):
        assets = [s for s in strings(code) if PurePosixPath(s).suffix.lower() in IMAGE_SUFFIXES | VIDEO_SUFFIXES]
        return {'kind': 'media', 'media_name': '', 'assets': assets,
                'animation': bool(re.search(r'\bMovie\s*\(', code)),
                'video': any(PurePosixPath(s).suffix.lower() in VIDEO_SUFFIXES for s in assets)}
    return None


def navigation(scripts, events):
    """Resolve literal actions and bounded constant loops; custom handlers stay unknown."""
    from story_guides import logical_lines, expr, literal
    labels = {e['name'] for e in events if e['kind'] == 'label'}
    constants = {}; screens = defaultdict(list); links = []
    custom_links = any('hyperlink_functions' in text or 'hyperlink_handlers' in text for text in scripts.values())
    for text in scripts.values():
        for _, code, indent in logical_lines(text):
            match = re.match(r'^(?:(?:default|define|\$)\s+)?(\w+)\s*=\s*(.+)$', code)
            if match:
                value = literal(expr(match[2]))
                if isinstance(value, (str, list, tuple)):
                    constants[match[1]] = value
    for filename, text in scripts.items():
        screen = None; scope = []; guards = []
        for line, code, indent in logical_lines(text):
            while scope and scope[-1][0] >= indent:scope.pop()
            while guards and guards[-1][0] >= indent:guards.pop()
            match = re.match(r'^screen\s+(\w+)', code)
            if match:screen = match[1]
            elif indent == 0:screen = None
            condition = re.match(r'^if\s+(.+):$', code)
            if condition:guards.append((indent, condition[1]))
            loop = re.match(r'^for\s+(\w+)\s+in\s+(.+):$', code)
            if loop:
                values = constants.get(loop[2], literal(expr(loop[2])))
                if isinstance(values, (list, tuple)) and len(values) <= 100:
                    scope.append((indent, loop[1], values))
            if screen:
                for action in re.finditer(r'\b(Jump|Call|Start)\s*\(([^()]*)\)', code):
                    argument = expr(action[2]); value = literal(argument)
                    values = [value] if isinstance(value, str) else []
                    if isinstance(argument, ast.Name):
                        values = next((s[2] for s in reversed(scope) if s[1] == argument.id), [])
                        if not values and isinstance(constants.get(argument.id), str):values = [constants[argument.id]]
                    for target in values:
                        if not isinstance(target, str):continue
                        screens[screen].append({'file': filename, 'line': line, 'target': target,
                            'action': action[1], 'resolved': target in labels, 'guards': [g[1] for g in guards]})
    for event in events:
        if event['kind'] == 'dialogue':
            for target, caption in re.findall(r'\{a=([^}]+)\}(.*?)\{/a\}', event.get('text') or ''):
                if re.match(r'^(?:https?|mailto):', target):continue
                protocol, sep, destination = target.partition(':')
                destination = destination if sep else target
                supported = protocol in ('jump', 'call') if sep else not custom_links
                links.append({'file': event['file'], 'line': event['line'], 'label': event['label'],
                    'caption': caption, 'target': destination, 'guards': event['guards'],
                    'resolved': supported and destination in labels,
                    'reason': 'custom_handler' if not supported else 'literal_target' if destination in labels else 'missing_label'})
        if event['kind'] == 'screen_call':
            event['destinations'] = screens.get(event['target'], [])
    return {'links': links, 'screens': dict(screens)}


def navigation_destinations(report, events):
    """Resolve each linked/chapter destination once, with the same bounded walker."""
    from route_flow import branches
    entries=report['links']+[row for rows in report['screens'].values() for row in rows]
    targets=sorted({row['target'] for row in entries if row['resolved']})
    result={};roots=[];edges=[]
    for index,target in enumerate(targets):
        identifier='@rpt-navigation:'+str(index)
        base=dict(file='@rpt-navigation',line=index,label=identifier,code='',guards=[],
                  choices=[],indent=4,menu_line=index,branch_ids=[])
        choice=dict(base,kind='choice',id=identifier,title=target)
        edge=dict(base,kind='jump',target=target,static=True,choices=[identifier],indent=8)
        roots.append(choice);edges.append(edge)
    stops={}
    for plan in branches(events+edges,stops,roots=roots):
        target=plan[0]['title'];identifier=plan[0]['id']
        walked=plan[2]
        result[target]={'media':[{k:e.get(k) for k in ('file','line','assets','media_name','animation','video','guards','media_resolved')}
                                  for e in walked if e['kind']=='media'],
                        'dialogue_count':sum(e['kind']=='dialogue' for e in walked),
                        'stops':stops.get(identifier,[]),'limited':plan[3]}
    report['destinations']=result
    return report


def hints(unique, people):
    from story_hints import GREEN, condition_text, evidence
    groups = defaultdict(list)
    for event in unique:
        if event['kind'] == 'media':groups[tuple(event['guards'])].append(event)
    result = []
    for guards, media in groups.items():
        assets = list(dict.fromkeys(p for event in media for p in event.get('assets', [])))
        resolved = [event for event in media if event.get('media_resolved')]
        # An unresolvable image expression is recorded in diagnostics, not guessed.
        if not resolved:continue
        videos = sum(bool(e.get('video')) for e in resolved)
        animations = sum(bool(e.get('animation')) and not e.get('video') for e in resolved)
        parts = []
        if videos:parts.append('영상 %d개' % videos)
        if animations:parts.append('애니메이션 %d개' % animations)
        stills = sum(not e.get('animation') and not e.get('video') for e in resolved)
        if stills:parts.append('이미지 %d회 표시' % stills)
        text = '장면: ' + ', '.join(parts)
        base = text
        if guards:text += ' (' + condition_text(guards, people) + '일 때)'
        result.append({'text': text, 'base_text': base, 'guards': list(guards), 'kind': 'media',
                       'color': GREEN, 'bold': True, 'evidence': evidence(resolved[0]), 'assets': assets})
    return result
