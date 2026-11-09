"""Bounded original-text context. No model analysis or game execution."""
from bisect import bisect_right
from collections import defaultdict
import json
from pathlib import PurePosixPath
import re


LOCATION = re.compile(r'^\s*#\s+(.+\.rpym?):(\d+)\s*$')
LABEL_HASH = re.compile(r'_[0-9a-f]{8}(?:_\d+)?$')
# `scene` replaces an image; it does not by itself end a conversation.
# Labels, control flow and explicit TXT state scopes still separate context.
BOUNDARY = re.compile(r'^(?:label|jump|call|return|menu|if|elif|else|while|for|python|screen|init)\b')


def token_estimate(text):
    # Budget estimate only, not an exact model tokenizer. Non-ASCII text costs more.
    return sum(1 if ord(c) < 128 else 3 for c in text) // 3 + 1


def boundaries(text):
    """Split at control-flow changes and on leaving an indented branch.

    This is a lexical boundary index, not an interpreter or a route predictor.
    """
    result = []; stack = []
    for number, line in enumerate(text.splitlines(), 1):
        clean = line.strip()
        if not clean or clean.startswith('#'):
            continue
        indent = len(line.expandtabs(8)) - len(line.expandtabs(8).lstrip())
        leaving = False
        while stack and indent <= stack[-1]:
            stack.pop(); leaving = True
        choice = clean.startswith(('"', "'")) and clean.endswith(':')
        barrier = bool(BOUNDARY.match(clean)) or choice
        if leaving or barrier:
            result.append(number)
        if barrier and clean.endswith(':'):
            stack.append(indent)
    return result


def relative_source(name):
    path = PurePosixPath(str(name).replace('\\', '/'))
    if path.is_absolute() or '..' in path.parts or ':' in str(path):
        return None
    if path.parts and path.parts[0] == 'game':
        path = PurePosixPath(*path.parts[1:])
    return path.as_posix()


class SourceContext:
    def __init__(self, rows, project=None, cfg=None):
        self.groups = defaultdict(list); self.positions = {}; self.keys = {}
        template_locations = {}; script_bounds = {}; note_rows = []
        for row in rows:
            file = row.get('file', '')
            location = (row.get('source_file'), row.get('source_line'))
            # Existing catalogs need no regeneration or cache migration.
            if row.get('kind') == 'dialogue' and not all(location) and project is not None:
                if file not in template_locations:
                    headers = []; locations = []
                    relative = relative_source(file)
                    path = project / 'data/templates' / relative if relative else None
                    if path and path.is_file():
                        for i, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines()):
                            match = LOCATION.match(line)
                            if match:
                                headers.append(i); locations.append((match[1], int(match[2])))
                    template_locations[file] = headers, locations
                headers, locations = template_locations[file]
                index = bisect_right(headers, row.get('line', -1)) - 1
                if index >= 0:
                    location = locations[index]
            note_rows.append(dict(row,source_file=location[0],source_line=location[1]) if all(location) else row)
            block = LABEL_HASH.sub('', row.get('block', ''))
            key = (file, row.get('kind'), row.get('usage',row.get('kind')), block)
            if row.get('kind') == 'dialogue' and all(location) and project is not None:
                relative = relative_source(location[0])
                if relative not in script_bounds:
                    script_bounds[relative] = None
                    if relative:
                        for base in (project / 'data/recovered-scripts', project / 'staging/game'):
                            path = base / relative
                            if path.is_file():
                                script_bounds[relative] = boundaries(path.read_text(encoding='utf-8-sig'))
                                break
                if script_bounds[relative] is not None:
                    key += (relative, bisect_right(script_bounds[relative], location[1]))
            # A repeated label after another group must not join noncontiguous spans.
            if not hasattr(self, '_last') or key != self._last:
                self._serial = getattr(self, '_serial', 0) + 1
            self._last = key
            key += (self._serial,)
            self.keys[row['id']] = key
            self.positions[row['id']] = len(self.groups[key])
            self.groups[key].append(row)
        self.notes = None
        if project is not None:
            from context_notes import ContextNotes
            self.notes = ContextNotes(project, cfg or {}, note_rows)

    def same_scope(self, left, right):
        return (self.keys[left['id']] == self.keys[right['id']]
                and (self.notes is None or self.notes.same_scope(left,right)))

    def context(self, batch, cfg):
        context = self.notes.context(batch,cfg) if self.notes is not None else {}
        context.update(self._passage(batch,cfg,context))
        return context

    def _passage(self, batch, cfg, extra):
        if not batch or batch[0].get('kind') != 'dialogue':
            return {}
        group = self.groups[self.keys[batch[0]['id']]]
        positions = [self.positions[r['id']] for r in batch if self.same_scope(batch[0], r)]
        start, end = min(positions), max(positions)
        # This is only a candidate allocation. The final request tokenizer also
        # accounts for prompts, protected variables and the output reserve.
        available = int(cfg.get('num_ctx', 16384)) - 1800 - 700 - sum(token_estimate(r['source']) for r in batch)
        used = token_estimate(json.dumps({k:v for k,v in extra.items() if not k.startswith('_')},ensure_ascii=False)) if extra else 0
        budget = max(0, available-used)
        selected = {}; spent = 0
        target_ids = {r['id'] for r in batch}
        def add(pos):
            nonlocal spent
            row = group[pos]
            if row['id'] in target_ids:
                return
            item = {'speaker': row.get('speaker', 'unknown'), 'text': row['source']}
            selected[pos] = item
            spent += token_estimate(json.dumps(item, ensure_ascii=False))
        # Never omit cached dialogue between pending targets. If it cannot fit,
        # the caller must split the batch, not make distant targets look adjacent.
        for pos in range(start,end+1):add(pos)
        audit=dict(extra.get('_context_audit',{}))
        dropped=list(audit.get('preselection_dropped',[]))
        active={-1:True,1:True};distance=1
        while any(active.values()):
            for direction in (-1,1):
                if not active[direction]:continue
                pos=start-distance if direction<0 else end+distance
                if not 0<=pos<len(group) or not self.same_scope(batch[0],group[pos]):
                    active[direction]=False;continue
                row=group[pos]
                cost=token_estimate(json.dumps({'speaker':row.get('speaker','unknown'),'text':row['source']},ensure_ascii=False))
                # Keep the nearest complete entry on each side as a candidate
                # even when notes are large. Final fitting can reduce TXT first.
                if distance>1 and spent+cost>budget:
                    active[direction]=False
                    dropped.append({'part':'source_passage','reason':'candidate_budget',
                                    'direction':'before' if direction<0 else 'after',
                                    'first_excluded_id':row['id'],'estimated_tokens':cost})
                    continue
                add(pos)
            distance+=1
        if not selected and not dropped:
            return {}
        # Mark where the requested items occur without duplicating their text.
        for index, row in enumerate(batch):
            if self.same_scope(batch[0], row):
                selected[self.positions[row['id']]] = {'target_id': str(index)}
        audit['preselection_dropped']=dropped
        return {'source_passage': [selected[i] for i in sorted(selected)],
                '_context_audit':audit,
                'context_file': batch[0].get('file', ''),
                'context_label': LABEL_HASH.sub('', batch[0].get('block', ''))}
