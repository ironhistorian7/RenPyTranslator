"""Optional, local TXT translation guidance. No inference or game execution.

Only prompt-facing sections are read into requests. Catalog text supplies scene
anchors; uncertain ranges fall back to global guidance and matching terms.
"""
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from source_context import LABEL_HASH, relative_source, token_estimate


SCENE = re.compile(r'^\s*#{0,6}\s*(?:장면 ID|Scene ID)\s*:\s*(.+)$', re.I | re.M)
INJECT = re.compile(r'^\s*#{0,6}\s*(?:주입용 (?:공통 지침|문맥)|Injection context|Global guidance)\s*:?\s*$', re.I)
MANAGEMENT = re.compile(r'^\s*#{0,6}\s*(?:관리용|Management|Evidence)', re.I)
QUOTES = re.compile(r'"([^"\n]+)"|“([^”\n]+)”')
STATE = re.compile(r'^\s*#{0,6}\s*(?:State ID|상태 ID|상태 [A-Z0-9]+)\s*:\s*(.+)$',re.I|re.M)
FIELDS = re.compile(r'^(?:화자(?:·청자|와 청자)?|청자|현재 관계|관계 상태|말투(?:\s+[^:\n]+)?|현재 말투|Speaker|Listener|Relationship|Register(?:\s+[^:\n]+)?|Identity)\s*:',re.I)


def current_state(note):
    """Extract explicit author-provided fields; never infer a relationship."""
    result=[]
    for line in note.splitlines():
        clean=line.strip().lstrip('- ').lstrip('#').strip()
        if FIELDS.match(clean): result.append(clean)
    return '\n'.join(result)


def prompt_section(text, plain=False):
    """Accept inline or multiline sections; never include evidence/headers."""
    lines = text.splitlines(); active = False; result = []
    for line in lines:
        stripped = line.strip()
        if MANAGEMENT.match(stripped):
            break
        if INJECT.match(stripped):
            active = True
            continue
        inline = re.match(r'^(?:주입용 문맥|Injection context)\s*:\s*(.+)', stripped, re.I)
        if inline:
            active = True; result.append(inline[1]); continue
        if active and stripped.startswith('#'):
            break
        if active and stripped:
            result.append(stripped)
    if not active and plain:
        # A plain global.txt is useful too. Comments are never instructions.
        for line in lines:
            if MANAGEMENT.match(line): break
            if line.strip() and not line.lstrip().startswith('#'):
                result.append(line.strip())
    return '\n'.join(result)


def normalized(text):
    text = re.sub(r'\{[^{}]*\}', '', text)
    return re.sub(r'\s+', ' ', text.translate(str.maketrans({'’':"'", '‘':"'", '“':'"', '”':'"'}))).strip().casefold()


def anchor_match(anchor, source):
    # Short complete dialogue is valid evidence too. Only short *partial*
    # matches are rejected ("I" must not match every sentence containing I).
    if normalized(anchor) and normalized(anchor)==normalized(source):return True
    parts = [normalized(p) for p in re.split(r'…|\.{3}', anchor) if normalized(p)]
    if sum(map(len, parts)) < 8: return False
    return bool(re.search('.*'.join(re.escape(p) for p in parts), normalized(source)))


def scope_labels(scope, block):
    """Read code labels, including backticks and metadata; never scene titles."""
    identifier=r'`?([\w.]+)`?'
    pattern=r'\blabels?(?:\s*:\s*|\s+)'+identifier+r'((?:\s*(?:,|…|\.\.\.|→|~|\bto\b)\s*`?[\w.]+`?)*)'
    def extract(text):
        result=[]
        for match in re.finditer(pattern,text,re.I):
            result.append(match[1])
            tail=re.sub(r'\bto\b',' ',match[2],flags=re.I)
            result.extend(re.findall(r'[\w]+(?:\.[\w]+)*',tail))
        return list(dict.fromkeys(result))
    explicit=extract(scope)
    if explicit:return explicit
    # Management prose never goes in the prompt. Only its explicit labels field
    # can constrain a scene; do not interpret arbitrary evidence as instructions.
    metadata='\n'.join(line.strip().lstrip('- ') for line in block.splitlines()
                       if re.match(r'^\s*(?:-\s*)?labels?\s*:',line,re.I))
    return extract(metadata)


def terms_from(text):
    """Pipe-separated TXT, plus the verbose fields from the analysis prompt."""
    result = []; entry = {}
    def add(source, target, meaning=''):
        source = re.sub(r'\s*\([^)]*\)\s*$', '', source).strip()
        if not source or not target or target in ('—', '-', '선호 KO', '후보(KO)'): return
        # Keep rejected spellings out of the model request.
        meaning = re.split(r'기각\s*:|검토 메모|Rejected\s*:', meaning, flags=re.I)[0].strip(' ;—')
        pattern = re.compile(r'(?<!\w)' + re.escape(source) + r'(?!\w)', re.I)
        result.append((pattern, {'source':source, 'target':target, 'meaning':meaning}))
    def flush():
        if entry.get('source') and entry.get('target'):
            add(entry['source'], entry['target'], entry.get('meaning', ''))
        entry.clear()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'): continue
        if '|' in line or '\t' in line:
            flush()
            cells = [c.strip() for c in re.split(r'\||\t',line.strip('|'))]
            if len(cells) < 2 or cells[0].casefold() in ('en','원문','source'): continue
            if len(cells) > 2 and ('코드' in cells[2] or 'Character id' in cells[2]): continue
            sources = re.split(r'\s*/\s*', cells[0]); targets = re.split(r'\s*/\s*', cells[1])
            meaning = ' | '.join(cells[2:])
            if len(targets) == 1:
                for source in sources: add(source, targets[0], meaning)
            elif len(sources) == len(targets):
                for source, target in zip(sources, targets): add(source, target, meaning)
            # Ambiguous multi-target records do not impose a random spelling.
            continue
        match = re.match(r'^(원문|권장 한국어 표기|분류|의미와 적용 조건|원문상의 별칭|근거)\s*:\s*(.*)', line)
        if match:
            key, value = match.groups()
            if key == '원문': flush(); entry['source'] = value
            elif key == '권장 한국어 표기': entry['target'] = value
            elif key in ('분류','의미와 적용 조건'): entry['meaning'] = (entry.get('meaning','')+' '+value).strip()
            elif key == '원문상의 별칭' and value and entry.get('target'):
                for alias in re.split(r'[,/]', value): add(alias.strip(), entry['target'], entry.get('meaning',''))
    flush()
    return result


class ContextNotes:
    def __init__(self, project, cfg, rows):
        self.global_text = ''; self.terms = []; self.scenes = {}; self.root = None
        self.file_guidance={}
        self.note_files={}; self.entry_files={}
        self.phase = 'failed-repair' if cfg.get('_trace_phase') == 'failed-repair' else 'translation'
        self.report = {'loaded_files':[], 'issues':[], 'mapped_entries':0, 'scope_details':[],
                       'phase':self.phase, 'file_sha256':{},
                       'budget':'Adaptive within the request window; 4500 TXT tokens is writing guidance, not a cap.',
                       'num_ctx_requested':cfg.get('num_ctx',16384)}
        project = Path(project)
        source = Path(cfg['source']) if cfg.get('source') and not cfg.get('_repair') else None
        # Source TXT is authoritative, even on resume. No source game scripts are
        # read. If the original game was moved, the prepared copy remains portable.
        if source and source.is_dir():
            root = source / 'game/context'
        else:
            root = project / 'staging/game/context'
        if not root.is_dir(): return
        if root.is_symlink() or root.is_junction():
            self.report['issues'].append('Linked context folder skipped')
            self._report(project); return
        self.root = root
        self.global_text = prompt_section(self._read(root/'global.txt'), plain=True)
        self.terms = terms_from(self._read(root/'terms.txt'))
        grouped = defaultdict(list)
        for row in rows:
            name = relative_source(row.get('source_file') or row.get('file', ''))
            if name: grouped[name].append(row)
        for name, items in grouped.items():
            items.sort(key=lambda r:(r.get('source_line', r.get('line', 0)), r['id']))
            path = root/'files'/(name+'.txt')
            if not path.exists(): path = (root/'files'/name).with_suffix('.txt')
            if not path.exists(): path = root/(name+'.txt')
            if not path.exists(): path = (root/name).with_suffix('.txt')
            content = self._read(path)
            if not content: continue
            self.note_files[name]=path.relative_to(root).as_posix()
            for row in items:self.entry_files[row['id']]=self.note_files[name]
            self._map(name, content, items)
        self.report['mapped_entries'] = len(self.scenes)
        self._report(project)

    def _read(self, path):
        if not path.is_file(): return ''
        try:
            if not path.resolve().is_relative_to(self.root.resolve()):
                raise ValueError('Linked file outside context folder')
            if path.stat().st_size > 256_000: raise ValueError('TXT exceeds 256 KB')
            raw = path.read_bytes()
            text = raw.decode('utf-8-sig')
            name = path.relative_to(self.root).as_posix()
            self.report['loaded_files'].append(name)
            self.report['file_sha256'][name] = hashlib.sha256(raw).hexdigest()
            return text
        except (OSError, UnicodeError, ValueError) as exc:
            self.report['issues'].append(str(path.name)+': '+str(exc))
            return ''

    def _map(self, name, text, rows):
        markers = list(SCENE.finditer(text))
        if markers:
            overview=prompt_section(text[:markers[0].start()])
            if overview:
                for row in rows:self.file_guidance[row['id']]=overview
        parts = [(m[1].strip(), text[m.end():markers[i+1].start() if i+1<len(markers) else len(text)])
                 for i,m in enumerate(markers)]
        if not parts:
            # Plain per-file notes apply to the file; no guessed scene semantics.
            if re.search(r'(?mi)^\s*(?:Applies to|적용 구간|State ID|상태 ID)\s*:',text):
                self.report['issues'].append(name+': scoped notes need Scene ID; global/terms only');return
            note = prompt_section(text, plain=True)
            if note:
                for row in rows: self.scenes[row['id']] = (name, note)
            return
        expanded=[]
        for ident,block in parts:
            block=re.sub(r'(?m)^[ \t]*#{1,6}[ \t]*','',block)
            states=list(STATE.finditer(block))
            if not states: expanded.append((ident,block));continue
            # Parent facts are shared. Parent Applies-to must not override child scopes.
            parent=prompt_section(block[:states[0].start()],plain=True)
            parent='\n'.join(line for line in parent.splitlines() if not re.match(r'^(?:Applies to|적용 구간)\s*:',line,re.I))
            for i,m in enumerate(states):
                child=block[m.end():states[i+1].start() if i+1<len(states) else len(block)]
                scope=re.search(r'(?mi)^\s*(?:적용 구간|Applies to)\s*:\s*(.*)',child)
                child_note=prompt_section(child,plain=True)
                metadata='\n'.join(line for line in child.splitlines() if re.match(
                    r'^\s*(?:-\s*)?(?:Start ID|End ID|시작 ID|끝 ID|Branch|Condition|분기 조건|labels?)\s*:',line,re.I))
                if not scope_labels(scope[1] if scope else '',child):
                    parent_labels=scope_labels('',block[:states[0].start()])
                    if parent_labels:metadata+='\nlabels: '+', '.join(parent_labels)
                expanded.append((ident+'/'+m[1].strip(),
                    ('Applies to: '+scope[1]+'\n' if scope else '')+'Injection context:\n'+parent+'\n'+child_note+'\nManagement:\n'+metadata))
        parts=expanded
        assigned = defaultdict(list)
        for ident, block in parts:
            note = prompt_section(block,plain=True)
            note = '\n'.join(line for line in note.splitlines() if not re.match(r'^(?:Applies to|적용 구간|Start ID|End ID|Entry IDs|시작 ID|끝 ID)\s*:',line,re.I))
            scope = re.search(r'(?m)^\s*(?:적용 구간|Applies to)\s*:\s*(.*)', block, re.I)
            ids_scope=re.search(r'(?mi)^\s*(?:Start ID|시작 ID)\s*:',block) and re.search(r'(?mi)^\s*(?:End ID|끝 ID)\s*:',block)
            if not note or (not scope and not ids_scope):
                self.report['issues'].append(name+' / '+ident+': missing injection text or scope'); continue
            scope = scope[1] if scope else ''
            anchors = [m[1] or m[2] for m in QUOTES.finditer(scope)]
            labels = scope_labels(scope,block)
            candidates = [i for i,r in enumerate(rows) if not labels or LABEL_HASH.sub('',r.get('block','')) in labels]
            detail={'file':name,'scene':ident,'scope':scope,'labels':labels,'candidate_entries':len(candidates)}
            self.report.setdefault('scope_details',[]).append(detail)
            if labels and not candidates:
                # Setup/UI-only labels can legitimately have no translatable
                # catalog entries. Keep the fact visible without a false failure.
                detail.update(status='no_catalog_entries',reason='No catalog entries for the stated labels')
                continue
            selected = []
            reason='No supported scope or matching entries'
            if re.search(r'(?mi)^\s*(?:Branch|Condition|분기 조건)\s*:',block):
                detail.update(status='unmatched',reason='Runtime branch condition is not evaluated')
                self.report['issues'].append(name+' / '+ident+': branch condition not evaluated; use explicit branch label/entry IDs');continue
            start_id=re.search(r'(?mi)^\s*(?:Start ID|시작 ID)\s*:\s*(\S+)',block)
            end_id=re.search(r'(?mi)^\s*(?:End ID|끝 ID)\s*:\s*(\S+)',block)
            if start_id and end_id:
                starts=[i for i in candidates if start_id[1] in (rows[i]['id'],rows[i].get('block'))]
                ends=[i for i in candidates if end_id[1] in (rows[i]['id'],rows[i].get('block'))]
                detail.update(start_matches=len(starts),end_matches=len(ends))
                reason='Explicit start/end IDs are missing, repeated or reversed'
                if len(starts)==len(ends)==1 and starts[0]<=ends[0]:selected=[i for i in candidates if starts[0]<=i<=ends[0]]
            elif anchors:
                starts = [i for i in candidates if anchor_match(anchors[0], rows[i]['source'])]
                detail.update(start_anchor=anchors[0],start_matches=len(starts))
                reason='Start anchor not found' if not starts else 'Start anchor is repeated'
                if len(starts) == 1:
                    start = starts[0]
                    if len(anchors) > 1:
                        ends = [i for i in candidates if i>=start and anchor_match(anchors[-1],rows[i]['source'])]
                        # A repeated ending in another label must not invalidate
                        # the one uniquely belonging to this starting label.
                        if len(ends)>1 and not labels:
                            start_label=LABEL_HASH.sub('',rows[start].get('block',''))
                            local=[i for i in ends if LABEL_HASH.sub('',rows[i].get('block',''))==start_label]
                            if len(local)==1:ends=local;detail['end_resolution']='unique within starting label'
                        detail.update(end_anchor=anchors[-1],end_matches=len(ends))
                        reason='End anchor not found' if not ends else 'End anchor is repeated within scope'
                        if len(ends) == 1: selected = [i for i in candidates if start<=i<=ends[0]]
                    elif re.search(r'\b(?:jump|return)\b|전체|\bend\b', scope, re.I):
                        selected = [i for i in candidates if i>=start]
                    else:reason='Missing end anchor or explicit end-of-scope marker'
            elif labels:
                selected = candidates
            elif re.search(r'전체 파일|whole file|entire file|^screen\s',scope,re.I):
                selected = list(range(len(rows)))
            if not selected:
                detail.update(status='unmatched',reason=reason)
                self.report['issues'].append(name+' / '+ident+': scope unmatched or ambiguous; '+reason+'; common guidance/terms only')
            else:
                detail.update(status='matched',matched_entries=len(selected),
                              start_id=rows[selected[0]]['id'],end_id=rows[selected[-1]]['id'],
                              start_line=rows[selected[0]].get('source_line'),end_line=rows[selected[-1]].get('source_line'))
            for index in selected: assigned[rows[index]['id']].append((ident,note))
        for uid, matches in assigned.items():
            if len(matches) == 1: self.scenes[uid] = (name+'/'+matches[0][0],matches[0][1])
        for detail in self.report.get('scope_details',[]):
            if detail['file']!=name or detail.get('status')!='matched':continue
            overlapping=sum(len(matches)>1 and any(ident==detail['scene'] for ident,_ in matches) for matches in assigned.values())
            detail['overlapping_entries']=overlapping
        ambiguous = sum(len(v)>1 for v in assigned.values())
        unmapped = len(rows)-sum(r['id'] in self.scenes for r in rows)
        if unmapped:
            fallback='file/global guidance and terms' if any(r['id'] in self.file_guidance for r in rows) else 'global/terms'
            self.report['issues'].append(f'{name}: {unmapped} entries use {fallback} only ({ambiguous} overlapping scopes)')

    def _report(self, project):
        from engine import save_json
        report = dict(self.report, folder=str(self.root) if self.root else None,
                      recorded_at=datetime.now(timezone.utc).isoformat())
        name = 'context-report.json' if self.phase == 'translation' else 'context-report.failed-repair.json'
        save_json(project/'data'/name,report)
        with (project/'data/context-history.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(report,ensure_ascii=False)+'\n')
        print('TXT context: %d files loaded; %d entries matched; %d notes. See data/%s' %
              (len(self.report['loaded_files']),len(self.scenes),len(self.report['issues']),name),flush=True)
        for issue in self.report['issues']: print('TXT context warning: '+issue,flush=True)

    def same_scope(self, left, right):
        return self.scenes.get(left['id'], ('', ''))[0] == self.scenes.get(right['id'], ('', ''))[0]

    def context(self, batch, cfg):
        if not self.root or not batch: return {}
        # Selection is semantic here. Only the final, complete request decides
        # what fits; old context_txt_tokens settings must not silently cut TXT.
        result = {}
        file_notes={self.file_guidance.get(r['id'],'') for r in batch}
        if len(file_notes)==1:
            overview=next(iter(file_notes))
            if overview:result['file']=overview
        scopes = {self.scenes.get(r['id'], ('','')) for r in batch}
        state='';scope=''
        if len(scopes) == 1:
            scope,note=next(iter(scopes));state=current_state(note)
            if note:result['scene']=note
        terms = []
        for pattern, entry in self.terms:
            if any(pattern.search(r['source']) for r in batch):
                terms.append(entry)
        if terms:result['terms']=terms
        if self.global_text:result['global']=self.global_text
        if not result and not state:return {}
        files={name:self.report['file_sha256'][name] for name in ('global.txt','terms.txt') if name in self.report['file_sha256']}
        for row in batch:
            note_file=self.entry_files.get(row['id'])
            if note_file:files[note_file]=self.report['file_sha256'][note_file]
        data={'translation_guidance':result,'_context_audit':{'scope':scope,'files':files,'preselection_dropped':[]}}
        if state:data['current_state']=state
        return data
