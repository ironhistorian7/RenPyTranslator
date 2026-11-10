import ast
from collections import Counter, defaultdict, deque
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import threading
from datetime import datetime, timezone
import uuid
import urllib.request
import urllib.error
import zipfile
from renpy_text_syntax import TOKENS
from script_literals import literal_eval
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextvars import copy_context
from engine import engine_command, save_json, verify_source
from source_language import letters, normalize, instruction as language_instruction, source_units

QUOTED = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')
HANGUL = re.compile(r'[가-힣]')
# Actual percent-format conversion types, not arbitrary letters in prose (% to).
FORMATS = re.compile(r'(?!(?<=\d)%[ \t]+[A-Za-z])%(?:\([^)]+\))?[#0 +\-]*(?:\d+|\*)?(?:\.(?:\d+|\*))?[hlL]?[diouxXeEfFgGcrsa%](?![A-Za-z])')
# Accept delimiter-only damage, including the old Markdown-like spelling.
PLACEHOLDER = re.compile(r'[<\[]\s*rpt\s*[_-]?\s*(\d+)\s*(?:/\s*[>\]]|>\]|\]>|[>\]])|(?:\\?[_*])*RPT(?:\\?[_*])+(\d+)(?:\\?[_*])*', re.I)
TRACE_LOCK=threading.Lock()


def trace_event(cfg, request_id, event, **details):
    """Append exact local requests/responses; retries and concurrent calls keep IDs."""
    if not cfg.get('_trace_dir'): return
    record = dict(request_id=request_id, event=event,
                  source_language=normalize(cfg.get('source_language')),target_language='korean',
                  phase=cfg.get('_trace_phase','translation'),
                  recorded_at=datetime.now(timezone.utc).isoformat(),
                  monotonic_ns=time.monotonic_ns(),thread_id=threading.get_ident(),**details)
    for key,label in (('_recovery_run_id','run_id'),('_runtime_generation','generation')):
        if key in cfg:record[label]=cfg[key]
    if cfg.get('_hardware_policy'):record['hardware_policy']=cfg['_hardware_policy']
    if cfg.get('_runtime_pid'):
        record['server_pid']=cfg['_runtime_pid']
        record['server_log']=cfg.get('_runtime_log')
    path = Path(cfg['_trace_dir'])/'translation-requests.jsonl'
    with TRACE_LOCK:
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(record,ensure_ascii=False)+'\n')


def runtime_event(cfg,name,**details):
    """Technical request timeline only; a diagnostic failure never retries work."""
    callback=cfg.get('_recovery_event')
    if callback:
        try:callback(name,generation=cfg.get('_runtime_generation'),**details)
        except Exception as exc:
            import sys
            print('Runtime request logging unavailable: '+str(exc),file=sys.stderr,flush=True)


def populated_links(text):
    """Count visible links by destination, allowing intentionally empty originals."""
    result = Counter()
    for match in re.finditer(r'(?<!\{)\{a=([^{}]+)\}(.*?)\{/a\}',text,re.S):
        content = match[2].replace('{{','LITERAL_BRACE')
        content = re.sub(r'\{image=[^{}]+\}','IMAGE',content)
        content = re.sub(r'\{[^{}]*\}','',content)
        if content.strip(): result[match[1]] += 1
    return result

class BatchTranslationError(ValueError):
    def __init__(self, output, failures, metrics):
        super().__init__('Invalid translation items: '+', '.join(r['row']['id'] for r in failures))
        self.output=output;self.failures=failures;self.metrics=metrics

def quote(text):
    # Ren'Py treats braces/brackets as text markup, not Python formatting.
    return json.dumps(text, ensure_ascii=False).replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')

def dialogue_literal(original, translated):
    """Find the text slot the engine emptied, leaving speaker/arguments intact.

    RenPy's template generator changes Say.what only. Comparing that pair
    supports quoted speakers, character expressions, attributes and suffixes
    without reimplementing the engine's entire say-expression grammar.
    """
    before=list(QUOTED.finditer(original));after=list(QUOTED.finditer(translated))
    if len(before)==len(after):
        changed=[i for i,(a,b) in enumerate(zip(before,after)) if literal_eval(a.group())!=literal_eval(b.group())]
        if len(changed)==1:
            i=changed[0]
            return before[i],after[i],i
    # Match by surrounding code as well, for different literal spellings.
    for i,target in enumerate(after):
        prefix=translated[:target.start()];suffix=translated[target.end():]
        if original.startswith(prefix) and original.endswith(suffix):
            end=len(original)-len(suffix) if suffix else len(original)
            source=QUOTED.fullmatch(original,len(prefix),end)
            if source:return source,target,i
    raise ValueError('Cannot locate dialogue text in generated template: '+translated)

def rendered_text(line,row):
    """Read the same literal selected during extraction, not the first string."""
    matches=list(QUOTED.finditer(line))
    return literal_eval(matches[row.get('literal_index',0)].group())

def catalog(project, cfg):
    from source_context import LOCATION
    target = project / 'staging/game/tl' / cfg['language']
    templates = project / 'data/templates'
    if not templates.exists():
        shutil.copytree(target, templates, ignore=shutil.ignore_patterns('*.rpyc'))
    records = []
    for path in sorted(templates.rglob('*.rpy')):
        lines = path.read_text(encoding='utf-8-sig').splitlines(keepends=True)
        block = None; sources = []; old = None; ordinal = 0; location = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            origin = LOCATION.match(line)
            if origin:
                location = (origin[1], int(origin[2]))
                continue
            match = re.match(r'translate\s+\S+\s+(\S+):', stripped)
            if match:
                block = match.group(1); sources = []; ordinal = 0
                continue
            if not block:
                continue
            if block == 'strings':
                if stripped.startswith('old '):
                    old = literal_eval(stripped[4:])
                elif stripped.startswith('new ') and old is not None:
                    m = QUOTED.search(line)
                    if old:
                        item=record(path, templates, i, m, old, 'string', block, ordinal)
                        if location:item.update(source_file=location[0],source_line=location[1])
                        records.append(item)
                        ordinal += 1
                    old = None
                continue
            if stripped.startswith('# '):
                sources.append(stripped[2:])
            elif stripped and not stripped.startswith('#'):
                if not sources:
                    raise ValueError(f'Unexpected statement in translation template {path}:{i+1}')
                original = sources.pop(0)
                if original == stripped:
                    continue  # voice, blank dialogue, other unchanged statements
                source_match,target_match,literal_index=dialogue_literal(original,stripped)
                text = literal_eval(source_match.group())
                if text:
                    # Catalog offsets address the indented line used by render.
                    target_match=list(QUOTED.finditer(line))[literal_index]
                    item=record(path, templates, i, target_match, text, 'dialogue', block, ordinal)
                    if location:item.update(source_file=location[0],source_line=location[1])
                    if literal_index:item['literal_index']=literal_index
                    speaker=original[:source_match.start()].strip()
                    item['speaker']=speaker or 'narrator'
                    if QUOTED.fullmatch(speaker):
                        item['speaker_name']=literal_eval(speaker)
                        item['speaker']=item['speaker_name']
                    records.append(item)
                    ordinal += 1
    n = sum(r['kind'] == 'dialogue' for r in records)
    if cfg.get('expected_dialogue') and n != cfg['expected_dialogue']:
        raise ValueError(f'Expected {cfg["expected_dialogue"]} dialogue entries, found {n}')
    from label_policy import for_project
    records=for_project(project,records)
    save_json(project / 'data/catalog.json', records)
    save_json(project / 'data/catalog-format.json', {'version':2})
    print(f'Catalog: {n} dialogue + {len(records)-n} UI strings', flush=True)
    return records

def record(path, root, line, match, source, kind, block, ordinal):
    rel = path.relative_to(root).as_posix()
    uid = hashlib.sha256(f'{rel}:{block}:{ordinal}:{source}'.encode()).hexdigest()[:16]
    return {'id': uid, 'file': rel, 'line': line, 'start': match.start(), 'end': match.end(),
            'source': source, 'kind': kind, 'block': block}

def read_catalog(project):
    from label_policy import for_project
    return for_project(project,json.loads((project / 'data/catalog.json').read_text(encoding='utf-8')))

def protect(source,cfg=None):
    if cfg and cfg.get('engine')=='rpgmaker':
        from rpgmaker_tokens import protect as rpg_protect
        return rpg_protect(source)
    tokens=[];pieces=[];cursor=0
    for match in TOKENS.finditer(source,formats=FORMATS):
        pieces.extend((source[cursor:match.start()],f'<rpt{len(tokens):03d}/>'))
        tokens.append(match.group());cursor=match.end()
    pieces.append(source[cursor:])
    return ''.join(pieces),tokens

def movable_token(token):
    """Named interpolation can move with grammar; formatting/control tags cannot."""
    return ((token.startswith('[') and not token.startswith('[[') and token.endswith(']'))
            or token.startswith('%(') or bool(re.fullmatch(r'\\[VNP]\[\d+\]',token,re.I)))


def token_signature(tokens):
    return (Counter(t for t in tokens if movable_token(t)),
            [t for t in tokens if not movable_token(t)])


def restore(text, tokens):
    if not isinstance(text, str):
        raise ValueError('Translation is not a string')
    expected = list(range(len(tokens)))
    matches=list(PLACEHOLDER.finditer(text))
    found = [int(m.group(1) or m.group(2)) for m in matches]
    if not tokens and found:
        # No original variable/tag exists to lose: remove only tool markers.
        return PLACEHOLDER.sub('',text).strip()
    # {#context} is invisible Ren'Py disambiguation metadata. Its position has
    # no rendered meaning, so restore an omitted context prefix deterministically.
    if not found and tokens and all(t.startswith('{#') for t in tokens):
        return ''.join(tokens)+text.strip()
    if (Counter(found) != Counter(expected) or
            [i for i in found if not movable_token(tokens[i])] !=
            [i for i in expected if not movable_token(tokens[i])]):
        raise ValueError(f'Protected token mismatch: {found} != {expected}; output={text!r}')
    # One substitution pass prevents restored source text from being rescanned.
    return PLACEHOLDER.sub(lambda m:tokens[int(m.group(1) or m.group(2))],text).strip()

def validate_text(source, target, cfg, *, check_link_content=False):
    if cfg.get('engine')=='rpgmaker':
        from rpgmaker_tokens import validate as rpg_validate
        return rpg_validate(source,target,cfg)
    errors = []
    if not source.strip() and target == source:return errors
    if not target.strip(): errors.append('empty')
    from token_recovery import STYLE
    def token_view(text):
        tokens=[];visible=[];named_formats=[];cursor=0
        for match in TOKENS.finditer(text):
            token=match.group()
            if token.startswith('%'):named_formats.append(token)
            elif not STYLE.fullmatch(token):tokens.append(token)
            visible.append(text[cursor:match.start()]);cursor=match.end()
        visible.append(text[cursor:])
        return tokens,''.join(visible),named_formats
    source_tokens,source_visible,source_named=token_view(source)
    target_tokens,target_visible,target_named=token_view(target)
    if token_signature(source_tokens) != token_signature(target_tokens):
        errors.append('markup mismatch')
    if check_link_content and populated_links(source) - populated_links(target):
        errors.append('empty hyperlink text')
    if Counter(source_named+FORMATS.findall(source_visible)) != Counter(target_named+FORMATS.findall(target_visible)):
        errors.append('format field mismatch')
    from token_recovery import has_unrestored_tool_marker
    if has_unrestored_tool_marker(source,target): errors.append('unrestored placeholder')
    if re.search(r'<think>|</think>|```', target): errors.append('model commentary')
    visible_source=FORMATS.sub('',source_visible)
    visible_target=FORMATS.sub('',target_visible)
    if any(c.isalpha() for c in visible_source) and not any(c.isalnum() for c in visible_target):
        errors.append('punctuation-only translation')
    if len(re.findall(r'[A-Za-z]+',visible_source)) >= 5 and not HANGUL.search(visible_target):
        # Numeric keys, URLs, and short acronyms may legitimately remain unchanged.
        if not re.fullmatch(r'(?:https?://\S+|[A-Z0-9 .:/_+\-]+)',source_visible):
            errors.append('no Korean translation')
    if normalize(cfg.get('source_language'))=='japanese' and letters(visible_source) and not HANGUL.search(visible_target):
        # Tiny labels, symbols and deliberate originals must not abort a game.
        if len([c for c in visible_source if c.isalpha()])>=5 and re.search(r'[\u3040-\u30ff\u3400-\u9fff]',visible_source):
            if 'no Korean translation' not in errors:errors.append('no Korean translation')
    return errors

def validate_entry(source, entry, cfg):
    errors=validate_text(source,entry['text'],cfg)
    if entry.get('status') in ('source_fallback','ui_original') and entry['text']==source:
        errors=[e for e in errors if e not in ('no Korean translation','untranslated Japanese text')]
    return errors

def request(endpoint, route, body, *, timeout=600):
    from model_runtime import track_request
    body=track_request(endpoint,route,body)
    req = urllib.request.Request(endpoint.rstrip('/')+route,
        data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type':'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        exc._rpt_transport=True
        if isinstance(exc,urllib.error.HTTPError):
            try:
                raw=exc.read(1048577)
                exc.http_details={'status':exc.code,'url':exc.url,'headers':dict(exc.headers or {}),
                    'body':raw[:1048576].decode('utf-8','replace'),'body_truncated':len(raw)>1048576}
            except Exception as read_error:exc.http_details={'status':exc.code,'read_error':str(read_error)}
            finally:exc.close()
        raise

def fingerprint(cfg):
    relevant = {k:cfg.get(k) for k in ('model','language','style','glossary','num_ctx')}
    if cfg.get('analysis_digest'):relevant['analysis_digest']=cfg['analysis_digest']
    if normalize(cfg.get('source_language'))!='english':relevant['source_language']=normalize(cfg.get('source_language'))
    return hashlib.sha256(json.dumps(relevant, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def cache(project, cfg):
    path = project / 'data/translations.jsonl'
    out = {}
    rows=read_catalog(project);sources={r['id']:r['source'] for r in rows}
    source_aliases={r['id']:set(r.get('source_aliases',[]))|{r.get('base_source',r['source']),r['source']}
                    for r in rows} if cfg.get('engine')=='rpgmaker' else {}
    active_fingerprint=fingerprint(cfg)
    for r in rows:
        bare=TOKENS.sub('',r['source'])
        if FORMATS.search(bare) and not letters(FORMATS.sub('',bare)):
            out[r['id']]={'id':r['id'],'source':r['source'],'text':r['source'],
                          'model':'literal format','fingerprint':fingerprint(cfg)}
    if path.exists():
        lines=path.read_text(encoding='utf-8').splitlines()
        for number,line in enumerate(lines):
            if line.strip():
                try: row = json.loads(line)
                except json.JSONDecodeError:
                    if number==len(lines)-1:
                        raise ValueError('Incomplete final cache record. Preserve the file and repair its final line before resuming.')
                    raise
                if (row['fingerprint'] == active_fingerprint or row['fingerprint'] == cfg.get('imported_cache_fingerprint') or
                    (cfg.get('_reuse_completed') and (row.get('model')==cfg['model'] or cfg.get('_reuse_previous_models')) and
                     (sources.get(row['id'])==row.get('source') or row.get('source') in source_aliases.get(row['id'],set())))):
                    # Old format-only entries may contain visible Japanese prose.
                    if row.get('model')=='literal format' and letters(FORMATS.sub('',TOKENS.sub('',row.get('source','')))):
                        continue
                    selected=sources.get(row['id'])
                    if selected is not None and row.get('source')!=selected and row.get('source') in source_aliases.get(row['id'],set()):
                        previous_source=row['source'];row=dict(row,source=selected)
                        if row.get('text')==previous_source:
                            row=dict(row,text=selected,status='source_fallback',
                                     error='Unchanged base-language text; selected-language translation is pending')
                    out[row['id']] = row
    recovered=project/'data/recovered-translations.json'
    if recovered.exists():
        for uid,item in json.loads(recovered.read_text(encoding='utf-8')).items():
            prior=out.get(uid)
            if (uid not in sources or item.get('source')!=sources[uid] or
                    item.get('id',uid)!=uid or not isinstance(item.get('text'),str)):
                continue
            if prior is not None:
                if (prior.get('model')=='reviewed override' or prior.get('source')!=sources[uid] or
                        item.get('before')!=prior.get('text')):
                    continue
            elif 'before' not in item or item['before'] is not None:
                continue
            base=prior or {'id':uid,'source':sources[uid],'model':cfg.get('model','saved recovery'),
                           'fingerprint':active_fingerprint}
            out[uid]=dict(base,text=item['text'],status='failure_recovered',error=None)
    overrides=project/'data/reviewed-translations.json'
    if overrides.exists():
        reviewed=json.loads(overrides.read_text(encoding='utf-8'))
        for r in rows:
            if r['source'] in reviewed:
                out[r['id']]={'id':r['id'],'source':r['source'],'text':reviewed[r['source']],
                             'model':'reviewed override','fingerprint':fingerprint(cfg)}
    # Layout whitespace is data, never a model task. Override old false failures
    # in memory only; leave the user's append-only translation cache intact.
    for r in rows:
        if not r['source'].strip():
            out[r['id']]={'id':r['id'],'source':r['source'],'text':r['source'],
                          'model':'literal whitespace','status':'literal',
                          'fingerprint':active_fingerprint}
    if cfg.get('engine')=='rpgmaker':
        from rpgmaker_layout import repair as repair_layout
        invalid=[]
        for uid,item in list(out.items()):
            if uid not in sources:continue
            repaired,changes=repair_layout(sources[uid],item['text'])
            if changes:
                item=dict(item,text=repaired);out[uid]=item
                trace_event(dict(cfg,_trace_dir=str(project/'data')),uuid.uuid4().hex,
                            'rpg_cached_layout_repair',id=uid,placements=changes,cache_modified=False)
            errors=validate_entry(sources[uid],item,cfg)
            if errors:
                invalid.append({'id':uid,'errors':errors})
                del out[uid]
        if invalid:
            # Keep the append-only cache intact; only invalid items re-enter the queue.
            save_json(project/'data/rpg-invalid-cache.json',{'entries':invalid,'cache_deleted':False})
    return out

def translate_batch(rows, context, cfg, retry_note=''):
    from gpu_policy import translation_context
    context=translation_context(context,cfg)
    from diagnostics import stage, report
    stage('translation request',cfg=cfg)
    from name_hints import placeholder_hints
    from hy_backend import is_hy, body as hy_body, parse as hy_parse
    from token_recovery import prepare_renpy_source, strip_visual_styles, restore_output, restore_prepared_renpy_output
    from label_policy import context_for, label_batch, expansion_errors
    inputs = []; protected = {}; wrappers = {}; source_cores = {}; style_plans = {}
    for index, row in enumerate(rows):
        if cfg.get('engine')=='rpgmaker':
            from rpgmaker_tokens import split_outer_controls
            prefix,core,suffix=split_outer_controls(row['source'])
        else:
            core,style_plans[row['id']]=prepare_renpy_source(row['source'])
            prefix=suffix=''
        wrappers[row['id']]=(prefix,suffix)
        source_cores[row['id']]=core
        if cfg.get('engine')=='rpgmaker':
            text,tokens=protect(core,cfg);markers=['<rpt%03d/>'%i for i in range(len(tokens))]
        else:
            plan=style_plans[row['id']]
            text,tokens=plan['protected_text'],plan['functional_tokens'];markers=plan['wire_markers']
        protected[row['id']] = tokens
        hint_markers={'<rpt%03d/>'%i:marker for i,marker in enumerate(markers)}
        inputs.append({'id': str(index), 'kind': row['kind'], 'usage':row.get('usage',row['kind']), 'speaker':row.get('speaker','unknown'), 'text':text,
                       'name_hints':{hint_markers[key]:value for key,value in placeholder_hints(tokens,cfg.get('_names',{})).items()},
                       'movable_placeholders':[markers[i] for i,t in enumerate(tokens) if movable_token(t)]})
        if cfg.get('engine')=='rpgmaker':
            from rpgmaker_tokens import readable
            inputs[-1]['reading_text']=readable(core,cfg)
    if cfg.get('engine')=='rpgmaker' and context.get('source_passage'):
        from rpgmaker_tokens import readable
        context=dict(context,source_passage=[dict(item,text=readable(item['text'],cfg))
            if 'text' in item else item for item in context['source_passage']])
    elif context.get('source_passage'):
        context=dict(context,source_passage=[dict(item,text=strip_visual_styles(item['text']))
            if 'text' in item else item for item in context['source_passage']])
    correction=None;plain_correction=False;plain_retries={}
    response_keys={row['id']:str(index) for index,row in enumerate(rows)}
    if cfg.get('engine')=='rpgmaker' and len(rows)==1 and is_hy(cfg) and cfg.get('_rpg_correction_enabled',True):
        from rpgmaker_protocol import correction_plan
        previous=cfg.get('_rpg_retry_errors',{}).get(rows[0]['id'],'')
        plan=correction_plan(rows[0]['source'],previous,cfg)
        if plan is not None:
            draft,parts=plan
            words,positions=parts;correction=(draft,positions)
            plain_correction=cfg.get('_rpg_correction_attempt',0)==0
            inputs=[{'id':str(i),'text':PLACEHOLDER.sub('',word) if plain_correction else word,'kind':'dialogue' if plain_correction else 'string',
                     'usage':'dialogue' if plain_correction else 'spelling_fragment','speaker':'unknown',
                     'name_hints':{},'movable_placeholders':[]} for i,word in enumerate(words)]
            cfg=dict(cfg,_rpg_correction_mode=True,_rpg_fragment_correction=True,_rpg_plain_correction=plain_correction)
            audit=dict(context.get('_context_audit',{}))
            context=dict(context,_context_audit=audit,
                         spelling_context=PLACEHOLDER.sub('',draft),
                         correction_reading={str(i):PLACEHOLDER.sub('',word) for i,word in enumerate(words)})
            if plain_correction:
                # Do not repeat the defective Korean draft as reference text:
                # HY otherwise echoes its residual Japanese unchanged.
                audit['preselection_dropped']=list(audit.get('preselection_dropped',[]))+[
                    {'part':part,'reason':'standalone complete-phrase correction avoids copying the defective draft'}
                    for part in context if part!='_context_audit']
                context={'_context_audit':audit}
            retry_note=''
    if cfg.get('engine')=='rpgmaker' and not correction:
        from rpgmaker_plain_retry import plan as plain_retry_plan
        for index,row in enumerate(rows):
            previous=cfg.get('_rpg_retry_errors',{}).get(row['id'],'')
            try:
                _,core,_=split_outer_controls(row['source'])
                plain_plan=plain_retry_plan(core,previous,cfg)
            except ValueError as exc:
                trace_event(cfg,uuid.uuid4().hex,'rpg_plain_retry_skipped',id=row['id'],reason=str(exc))
                continue
            if plain_plan is not None:
                plain_retries[row['id']]=plain_plan
                inputs[index]=dict(inputs[index],text=plain_plan['text'],reading_text='',
                                   name_hints={},movable_placeholders=[])
        if plain_retries:
            expanded=[];readable_values={}
            for index,row in enumerate(rows):
                plain_plan=plain_retries.get(row['id'])
                if plain_plan and 'segments' in plain_plan:
                    for part in plain_plan['segments']:
                        if not part['plan']['text'].strip():continue
                        key=str(len(expanded));part['model_key']=key
                        expanded.append(dict(inputs[index],id=key,text=part['plan']['text']))
                        readable_values[key]=[v['text'] for v in part['plan']['variables']]
                else:
                    key=str(len(expanded));response_keys[row['id']]=key
                    expanded.append(dict(inputs[index],id=key))
                    if plain_plan:readable_values[key]=[v['text'] for v in plain_plan['variables']]
            inputs=expanded
            cfg=dict(cfg,_rpg_plain_retry=True,_rpg_plain_values=readable_values)
            retry_note='이전 응답에서 표시가 누락되어 표시 없는 원문을 번역합니다. 각 [ID]와 한국어 번역만 출력하세요.'
            if any('segments' in value for value in plain_retries.values()):
                context=dict(context,layout_reading={row['id']:value['text'] for row in rows
                    if (value:=plain_retries.get(row['id'])) and 'segments' in value})
            # No original engine escapes leak back into the reference passage.
            if context.get('source_passage'):
                from rpgmaker_plain_retry import reference as plain_reference
                context=dict(context,source_passage=[dict(item,text=plain_reference(item['text'],cfg))
                    if 'text' in item else item for item in context['source_passage']])
    context,context_policy=context_for(rows,context)
    labels_only=label_batch(rows)
    identities={}
    from name_hints import INTERPOLATION
    for row in rows:
        for token in protected[row['id']]:
            match=INTERPOLATION.fullmatch(token)
            if match and match[1] in cfg.get('_names',{}):
                data=cfg['_names'][match[1]]
                identities[match[1]]={k:data[k] for k in ('representative','kind','origin') if k in data}
    if identities:context['variable_identities']=identities
    terms=dict(cfg.get('glossary',{}),**cfg.get('_batch_glossary',{}))
    relevant_glossary={k:v for k,v in terms.items() if any(k.casefold() in r['source'].casefold() for r in rows)}
    for plain_plan in plain_retries.values():
        relevant_glossary.update(plain_plan['glossary'])
    from name_translation import occurrences
    person_names={k:v for k,v in cfg.get('_person_names',{}).items() if any(occurrences(r['source'],k) for r in rows)}
    system = (language_instruction(cfg)+'You translate game text into Korean from its original language. Translate EVERY input item fully. '
        'Return only the required JSON object. IDs must be copied exactly. Never merge or omit items. '
        'Do not add facts, quotation marks, commentary or English alternatives. '
        'For interface strings use concise conventional Korean UI labels. '
        +('Preserve the supplied label meaning.\n' if labels_only else
          'For dialogue use the style below. Preserve numbers and sentence meaning.\n'+cfg.get('style','Preserve the original tone and meaning.'))+
        '\nGlossary: '+json.dumps(relevant_glossary, ensure_ascii=False)+
        '\nUse these phonetic spellings only when the name refers to a person, not a country/common noun: '+json.dumps(person_names,ensure_ascii=False))
    if labels_only:
        system += ('\nThese items are speaker labels or interface controls, not dialogue. '
                   'Translate only the supplied name, role or label as a name or noun phrase. '
                   'Never add speech, speaker prefixes, events, quotations or explanations. '
                   'Term source/target pairs are spellings only.')
    if context.get('translation_guidance') and not labels_only:
        system += ('\ntranslation_guidance contains reference-only global style, current-scene facts and applicable terms. '
                   'Resolve subjects, speakers and relationships from the source and current scene before general background. '
                   'Do not assume every subject is the global protagonist. The source overrides conflicting notes. '
                   'Apply terminology only in its stated sense; retain global narration style without imposing it on quoted dialogue. '
                   'Use it to interpret the input, never translate the notes or add their facts to the output. '
                   'Keep each input item separate even when the background explains a later event.')
    if any(protected[row['id']] for row in rows if row['id'] not in plain_retries):
        system+='\nPreserve each input placeholder exactly once. IDs in movable_placeholders represent named variables and may move for Korean word order. Keep all other placeholders in their original relative order. Never invent a placeholder or copy one from another item.'
    if plain_retries:
        system+='\nKeep these readable name/number values spelled exactly as supplied, allowing Korean word order and particles: '+json.dumps(cfg['_rpg_plain_values'],ensure_ascii=False)
    properties={}
    for item in inputs:
        spec={'type':'string','minLength':1}
        properties[item['id']]=spec
    schema = {'type':'object','properties':properties,
        'required':[item['id'] for item in inputs],'additionalProperties':False}
    body = {'model':cfg['model'],'stream':False,'think':False,'format':schema,'keep_alive':'10m',
        'options':{'temperature':0.2,'num_ctx':cfg.get('num_ctx',8192),
            'num_predict':min(2048, 256+sum(source_units(r['source']) for r in rows)*7),
            'repeat_penalty':1.1,'seed':42},
        'messages':[{'role':'system','content':system},{'role':'user','content':json.dumps({
            'context_do_not_translate':context,'items':inputs,'retry_note':retry_note},ensure_ascii=False)}]}
    def send(active_inputs, output_minimum):
        active_cfg=dict(cfg,_allow_batch_split=len(active_inputs)>1)
        if output_minimum:active_cfg['_output_minimum']=output_minimum
        request_id=uuid.uuid4().hex
        selected_context=dict(context)
        active_ids={item['id'] for item in active_inputs}
        if selected_context.get('source_passage'):
            selected_context['source_passage']=[{key:value for key,value in item.items() if key!='text'}
                if 'target_id' in item else item
                for item in selected_context['source_passage']
                if 'target_id' not in item or item['target_id'] in active_ids]
        if any(item not in inputs for item in active_inputs):selected_context.pop('layout_reading',None)
        def build(selected):
            if is_hy(cfg):return hy_body(active_inputs,dict(selected,person_name_spellings=person_names),active_cfg,relevant_glossary,retry_note)
            result=dict(body)
            result['format']=dict(schema,properties={item['id']:properties[item['id']] for item in active_inputs},
                                  required=[item['id'] for item in active_inputs])
            result['options']=dict(body['options'])
            if output_minimum:result['options']['num_predict']=max(output_minimum,result['options']['num_predict'])
            result['messages']=[body['messages'][0],{'role':'user','content':json.dumps({
                'context_do_not_translate':{k:v for k,v in selected.items() if k!='current_state'},
                **({'current_state_reference_only':selected['current_state']} if selected.get('current_state') else {}),
                'items':active_inputs,'retry_note':retry_note},ensure_ascii=False)}]
            return result
        from request_budget import fit, InputBudgetError
        try:fitted,budget=fit(selected_context,active_cfg,build)
        except InputBudgetError:raise  # Replan before any model call or error report.
        except Exception as exc:
            report(exc,cfg,rows,request_id);raise
        trace_event(cfg,request_id,'request',
            items=[{k:r.get(k) for k in ('id','file','block','source_file','source_line')} for r in rows],
            request=fitted,context_budget=budget,context_policy=context_policy,
            item_purposes=[{'id':r['id'],'usage':r.get('usage',r['kind']),'evidence':r.get('usage_evidence')} for r in rows],
            **({'repair_mode':'rpg_japanese_plain_spans' if plain_correction else 'rpg_japanese_marked_spans','model_items':len(active_inputs),
                'correction_spans':[{'start':a,'end':b,'item':k} for a,b,k in correction[1]]} if correction else {}))
        if plain_retries:
            trace_event(cfg,request_id,'rpg_plain_retry',plans=plain_retries,
                        policy='readable_representatives; local_clause_word_restoration')
        if budget['dropped'] or budget.get('preselection_dropped'):
            print('Context budget adjusted: request '+request_id+'; see data/translation-requests.jsonl',flush=True)
        start=time.monotonic()
        runtime_event(cfg,'request_started',request_id=request_id,item_ids=[r['id'] for r in rows],
                      monotonic_ns=time.monotonic_ns(),prompt_tokens_estimated=budget.get('prompt_content_tokens'))
        try:
            response=(request(cfg['endpoint'],'/api/chat',fitted,timeout=1800)
                      if cfg.get('_cuda_diagnostic_mode')=='blocking' else request(cfg['endpoint'],'/api/chat',fitted))
        except Exception as exc:
            exc.request_id=request_id
            runtime_event(cfg,'request_finished',request_id=request_id,status='transport_error',
                          duration_ms=round((time.monotonic()-start)*1000,3),
                          error_type=type(exc).__name__,http_status=getattr(exc,'code',None))
            import traceback
            trace_event(cfg,request_id,'request_error',error=str(exc),error_type=type(exc).__name__,traceback=traceback.format_exc())
            report(exc,cfg,rows,request_id);raise
        elapsed=time.monotonic()-start
        runtime_event(cfg,'request_finished',request_id=request_id,status='http_success',duration_ms=round(elapsed*1000,3))
        recovered=cfg.get('_recovery_request_succeeded')
        if recovered:
            try:recovered(request_id=request_id,metrics={k:response.get(k) for k in ('eval_count','prompt_eval_count','done_reason')})
            except Exception as exc:
                import sys
                print('Runtime recovery status unavailable: '+str(exc),file=sys.stderr,flush=True)
        actual=response.get('prompt_eval_count')
        trace_event(cfg,request_id,'response',response=response,runtime=runtime_status(cfg),
                    actual_prompt_tokens=actual,num_ctx_requested=fitted['options']['num_ctx'])
        if actual and actual+fitted['options']['num_predict']>fitted['options']['num_ctx']:
            exc=InputBudgetError('Actual prompt consumed output reserve; request '+request_id)
            exc.context_budget=budget
            raise exc
        if cfg.get('_trace_dir'):
            with TRACE_LOCK:save_json(Path(cfg['_trace_dir'])/'last-model-response.json',response)
        # Check truncation before parsing, so a cut response never becomes a
        # missing-ID/JSON error or enters an identical fixed-limit retry.
        truncated=response.get('done_reason')=='length'
        parse_error=None
        try:
            if is_hy(cfg) and cfg.get('engine')=='rpgmaker':
                from rpgmaker_protocol import parse as rpg_parse
                values,alias=rpg_parse(response['message']['content'],len(active_inputs),expected_ids=active_ids)
                if alias:trace_event(cfg,request_id,'rpg_protocol_repair',repair='single_item_header_prefix',ids=[r['id'] for r in rows])
            else:
                values=(hy_parse(response['message']['content'],expected_inputs={item['id']:item['text'] for item in active_inputs})
                        if is_hy(cfg) else json.loads(response['message']['content']))
            if not isinstance(values,dict):raise ValueError('Response must be a JSON object')
        except (ValueError,KeyError,TypeError) as exc:
            values={}
            if not truncated:parse_error=str(exc)
            if cfg.get('_trace_dir'):
                with TRACE_LOCK:save_json(Path(cfg['_trace_dir'])/'last-invalid-model-response.json',response)
        return dict(values=values,truncated=truncated,parse_error=parse_error,hy=is_hy(cfg),
                    limit=fitted['options']['num_predict'],request_id=request_id,budget=budget,
                    seconds=elapsed,tokens=response.get('eval_count'),eval_seconds=response.get('eval_duration',0)/1e9)

    from translation_segments import execute
    from request_budget import BatchNeedsSplit
    parents={}
    for row in rows:
        plan=plain_retries.get(row['id'],{})
        keys=([item['id'] for item in inputs] if correction else
              [part['model_key'] for part in plan['segments'] if 'model_key' in part] if 'segments' in plan else
              [response_keys[row['id']]])
        parents.update({key:row['id'] for key in keys})
    try:
        parsed,model_errors,summary=execute(inputs,cfg,send,parents=parents,
            event=lambda kind,**details:trace_event(cfg,details.pop('request_id',uuid.uuid4().hex),kind,**details))
    except BatchNeedsSplit as exc:
        trace_event(cfg,uuid.uuid4().hex,'budget_split',reason=str(exc),
                    items=[row['id'] for row in rows],context_budget=exc.context_budget)
        raise
    budget=summary['budget'];request_id=summary['request_id'] or uuid.uuid4().hex
    output=[];failures=[]
    for index, row in enumerate(rows):
        key=response_keys[row['id']]
        required=[model_id for model_id,parent in parents.items() if parent==row['id']]
        rejected=next((model_errors[model_id] for model_id in required if model_id in model_errors),None)
        if rejected:
            failures.append(dict(row=row,**rejected));continue
        if not correction and not (row['id'] in plain_retries and 'segments' in plain_retries[row['id']]) and key not in parsed:
            failures.append({'row':row,'error':f'Missing response item [{key}]; received IDs: '+', '.join(sorted(parsed)),
                             'error_code':'missing_response_item','expected_id':key,'received_ids':list(parsed)})
            continue
        try:
            style_changes={}
            prefix,suffix=wrappers[row['id']]
            if correction:
                from rpgmaker_protocol import apply_corrections,align_display_anchors
                corrected=parsed
                if plain_correction:
                    corrected={};alignment=[]
                    for a,b,key in correction[1]:
                        if key in corrected:continue
                        corrected[key],placements=align_display_anchors(correction[0][a:b],parsed[key])
                        alignment.extend(dict(p,item=key) for p in placements)
                    trace_event(cfg,request_id,'rpg_anchor_alignment',policy='unique_unchanged_text_and_edit_boundaries',placements=alignment)
                value=apply_corrections(correction[0],correction[1],corrected)
            elif row['id'] in plain_retries and 'segments' in plain_retries[row['id']]:value=parsed
            else:value=parsed[response_keys[row['id']]]
            if cfg.get('engine')=='rpgmaker' and isinstance(value,str):
                from rpgmaker_protocol import normalize_markers
                normalized=normalize_markers(value)
                if normalized!=value:trace_event(cfg,request_id,'rpg_protocol_repair',repair='marker_delimiters',ids=[row['id']])
                value=normalized
            if row['id'] in plain_retries:
                from rpgmaker_plain_retry import restore as restore_plain
                restored,placements=restore_plain(plain_retries[row['id']],value)
                text=prefix+restored+suffix
                trace_event(cfg,request_id,'rpg_plain_restoration',id=row['id'],**placements)
            elif cfg.get('engine')=='rpgmaker':
                text=prefix+restore_output(value,protected[row['id']])+suffix
            else:
                restored,style_changes=restore_prepared_renpy_output(source_cores[row['id']],value,style_plans[row['id']])
                text=prefix+restored+suffix
            if cfg.get('engine')=='rpgmaker':
                from rpgmaker_layout import repair as repair_layout
                text,changes=repair_layout(row['source'],text)
                if changes:trace_event(cfg,request_id,'rpg_layout_repair',id=row['id'],placements=changes)
            errors = validate_text(row['source'],text,cfg,check_link_content=True)
            errors += expansion_errors(row,text)
            if errors: raise ValueError(f'{row["id"]}: {errors}; output={value!r}')
            if style_changes:
                trace_event(cfg,request_id,'renpy_style_repair',id=row['id'],**style_changes)
        except (ValueError,KeyError,TypeError) as exc:
            error=str(exc)
            if correction:
                error=error.split('; output=',1)[0]+'; untranslated Japanese text; output='+repr(correction[0])
            if row['id'] in plain_retries:
                error='RPG plain retry failed: '+error
                trace_event(cfg,request_id,'rpg_plain_restoration_failed',id=row['id'],error=error)
            failure={'row':row,'error':error}
            failure['retry_attempt']=summary['attempts'].get(row['id'],cfg.get('_item_retry_attempt',0))
            if cfg.get('engine')!='rpgmaker':failure['input_format']='renpy_visual_stripped_v1'
            elif any(wrappers[row['id']]):failure['error']='Wrapper-stripped: '+error
            failures.append(failure);continue
        output.append({'id':row['id'],'source':row['source'],'text':text,
                       'model':cfg['model'],'fingerprint':fingerprint(cfg),
                       'context_digest':budget['context_sha256'],'context_files':budget.get('files',{}),
                       'context_scope':budget.get('scope',''),'request_id':request_id,
                       **({'request_ids':summary['request_ids']} if len(summary['request_ids'])>1 else {})})
    metrics = {key:summary[key] for key in ('seconds','tokens','eval_seconds')}
    metrics.update(seconds=round(metrics['seconds'],2),items=len(rows))
    trace_event(cfg,request_id,'validation',accepted_ids=[r['id'] for r in output],
                failures=[dict(id=f['row']['id'],**{k:v for k,v in f.items() if k!='row'}) for f in failures])
    if failures:
        try:
            exc=BatchTranslationError(output,failures,metrics)
            budgets=[failure['context_budget'] for failure in failures if failure.get('context_budget')]
            if budgets:exc.context_budget=budgets[0]
            raise exc
        except BatchTranslationError as exc:
            report(exc,cfg,[f['row'] for f in failures],request_id,handled=True);raise
    return output, metrics


_RUNTIME_STATUS={}
def runtime_status(cfg):
    """Once per owned runtime; report engine values, not assumed allocation."""
    key=cfg.get('_runtime_log')
    if not key:return {'verified':False,'reason':'No owned runtime descriptor'}
    with TRACE_LOCK:
        if key not in _RUNTIME_STATUS:
            try:
                from hy_backend import api
                models=api(cfg['endpoint'],'/api/ps').get('models',[])
                current=next(m for m in models if m.get('name',m.get('model'))==cfg['model'])
                _RUNTIME_STATUS[key]={'verified':True,'model':current,'server_log':key}
                print('Model runtime: '+json.dumps({k:current.get(k) for k in ('name','context_length','size','size_vram')})+
                      '; context requested='+str(cfg.get('num_ctx',16384))+'; server log='+key,flush=True)
            except Exception as exc:
                return {'verified':False,'error_type':type(exc).__name__,'error':str(exc),'server_log':key}
        return _RUNTIME_STATUS[key]

def translate(project, cfg, sample=False):
    # Offline resumes and rebuilds never start a server or load a model.
    from job_events import translation_tracker
    cfg=dict(cfg);translation_tracker(cfg)
    rows=read_catalog(project); known=cache(project,cfg)
    if cfg.get('engine')=='rpgmaker':
        previous={uid:item.get('error','') for uid,item in known.items() if item.get('status')=='source_fallback'}
        if previous:
            cfg=dict(cfg,_rpg_previous_failures=previous)
            for uid in previous:known.pop(uid,None)
            print(f'RPG Maker resume: retrying {len(previous)} failed entries; successful translations retained',flush=True)
    if cfg.get('engine')!='rpgmaker':
        from ui_policy import apply_policy
        known=apply_policy(project,rows,known,preserve_choices=True)
    if all(row['id'] in known for row in rows):
        return _translate(project,cfg,sample,rows,known)
    from runtime_recovery import run
    return run(project,cfg,lambda active:_translate(project,active,sample,rows,known),
               lambda:len(known))


def _translate(project, cfg, sample=False, rows=None, known=None):
    from term_memory import TermMemory
    from name_hints import ensure_metadata
    from job_events import translation_tracker
    tracker=translation_tracker(cfg)
    cfg=dict(cfg,_trace_dir=str(project/'data'),_trace_phase='translation')
    from diagnostics import stage
    stage('translation',project,cfg)
    from name_translation import mapping
    cfg['_person_names']=mapping(project) if cfg.get('engine')!='rpgmaker' else {}
    if rows is None:rows=read_catalog(project)
    if known is None:known=cache(project,cfg)
    if cfg.get('engine')=='rpgmaker':
        from rpgmaker_plain_retry import representatives
        cfg['_rpg_representatives']=representatives(project,cfg,rows,known)
    actor_ids={r['id'] for r in rows if Path(r.get('file','')).name=='Actors.json'} if cfg.get('engine')=='rpgmaker' else set()
    cfg['_names']=ensure_metadata(project,rows)['names'] if cfg.get('engine')!='rpgmaker' else {}
    memory=TermMemory(rows,known)
    from source_context import SourceContext
    all_rows=read_catalog(project) if cfg.get('engine')=='rpgmaker' else rows
    if sample:
        dialogue=[r for r in rows if r['kind']=='dialogue']
        # Beginning, later scenes, hyperlinks and UI all represented.
        chosen=dialogue[:12]+dialogue[len(dialogue)//2:len(dialogue)//2+8]+[r for r in dialogue if '{a=' in r['source']][:4]+[r for r in rows if r['kind']=='string'][:12]
        ids={r['id'] for r in chosen}; rows=[r for r in rows if r['id'] in ids]
    pending=[r for r in rows if r['id'] not in known]
    units={row['id']:max(1,source_units(row['source'])) for row in pending} if tracker.units is None else None
    tracker.begin(rows,pending,known,units)
    pending_files={r.get('file','') for r in pending}
    context_index=SourceContext([r for r in all_rows if r.get('file','') in pending_files],project,cfg) if pending else None
    size=cfg.get('batch_size',12); completed=0
    print(f'Translating {len(pending)} remaining entries with {cfg["model"]}',flush=True)
    queue=[]
    for row in pending:
        if not queue or len(queue[-1])>=size or not context_index.same_scope(queue[-1][-1],row) or sum(source_units(r['source']) for r in queue[-1])+source_units(row['source'])>cfg.get('max_batch_source_words',240) or sum(len(r['source'].encode('utf-8')) for r in queue[-1])+len(row['source'].encode('utf-8'))>min(5000,cfg.get('num_ctx',8192)//2):
            queue.append([])
        queue[-1].append(row)
    previous=cfg.get('_rpg_previous_failures',{}) if cfg.get('engine')=='rpgmaker' else {}
    from hy_backend import is_hy
    rpg_hy=cfg.get('engine')=='rpgmaker' and is_hy(cfg)
    if rpg_hy:
        from rpgmaker_protocol import correction_plan
    initial=[]
    for batch in queue:
        if rpg_hy and any(correction_plan(r['source'],previous.get(r['id'],''),cfg) for r in batch):
            for row in batch:
                repair=correction_plan(row['source'],previous.get(row['id'],''),cfg) is not None
                initial.append(([row],0 if repair or row['id'] not in previous else 1,repair))
        else:initial.append((batch,1 if batch and all(r['id'] in previous for r in batch) else 0,False))
    queue=deque(initial)
    failures=[dict(row=r,error=known[r['id']].get('error','Previous translation failed'),
                   **{k:known[r['id']][k] for k in ('input_format','error_code') if k in known[r['id']]})
              for r in rows if known.get(r['id'],{}).get('status')=='source_fallback']
    workers=max(1,cfg.get('parallel',1))
    print(f'Translation workers: {workers}; context window: {cfg.get("num_ctx",8192)}',flush=True)
    pool=ThreadPoolExecutor(max_workers=workers)
    running={}
    retry_errors=dict(previous);retry_spent={}
    def persist(result,metrics):
        nonlocal completed
        if not result:return
        # The parent thread alone commits results. Recovery never races writers.
        cachefile=project/'data/translations.jsonl'
        existing=cachefile.read_bytes() if cachefile.exists() else b''
        chunk=''.join(json.dumps(item,ensure_ascii=False)+'\n' for item in result).encode('utf-8')
        temp=cachefile.with_suffix('.jsonl.tmp')
        with temp.open('wb') as f:
            f.write(existing+chunk); f.flush(); os.fsync(f.fileno())
        from atomic_files import replace_cache_file
        replace_cache_file(temp,cachefile)
        for item in result:known[item['id']]=item
        if any(item['id'] in actor_ids for item in result):
            from rpgmaker_plain_retry import representatives
            cfg['_rpg_representatives']=representatives(project,cfg,rows,known)
        if cfg.get('_recovery_incident') and cfg.get('_recovery_event'):
            cfg['_recovery_event']('cache_committed',incident_id=cfg['_recovery_incident'],
                ids=[item['id'] for item in result],cached_total=len(known))
        if memory.observe(result):save_json(project/'data/term-memory.json',memory.translations)
        with (project/'data/metrics.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(metrics)+'\n')
        completed+=len(result)
        tracker.commit(result)
        save_json(project/'data/progress.json',dict(tracker.snapshot(),mode='sample' if sample else 'full',cached_total=len(known)))
        print(f'{completed}/{len(pending)} entries, batch {metrics["seconds"]}s',flush=True)
    def submit(batch,attempt,correction_phase):
        context=context_index.context(batch,cfg)
        spent=max([attempt]+[retry_spent.get(row['id'],0) for row in batch])
        batch_cfg=dict(cfg,_batch_glossary=memory.relevant(batch),_allow_batch_split=len(batch)>1,
                       _item_retry_attempt=spent,_item_retry_limit=4 if correction_phase else 2)
        if cfg.get('engine')=='rpgmaker':
            batch_cfg['_rpg_retry_errors']=dict(retry_errors)
            batch_cfg['_rpg_correction_enabled']=correction_phase
            batch_cfg['_rpg_correction_attempt']=attempt if correction_phase else 0
        retry_note=retry_instruction(batch,retry_errors,cfg) if attempt else ''
        if attempt or correction_phase:
            trace_event(batch_cfg,uuid.uuid4().hex,'item_retry',attempt=attempt,
                        **({'retry_phase':'correction' if correction_phase else 'translation',
                            'attempt_limit':2 if correction_phase else 3} if cfg.get('engine')=='rpgmaker' else {}),
                        items=[{'id':r['id'],'usage':r.get('usage',r['kind']),'error':retry_errors.get(r['id'])} for r in batch])
        future=pool.submit(copy_context().run,translate_batch,batch,context,batch_cfg,retry_note)
        running[future]=(batch,attempt,correction_phase)
    from request_budget import BatchNeedsSplit
    try:
      while queue or running:
        while queue and len(running)<workers:
            batch,attempt,correction_phase=queue.popleft();submit(batch,attempt,correction_phase)
        ready,_=wait(running,return_when=FIRST_COMPLETED)
        future=next(iter(ready));batch,attempt,correction_phase=running.pop(future)
        invalid=[]
        try:
            result,metrics=future.result()
        except BatchNeedsSplit:
            middle=len(batch)//2
            queue.appendleft((batch[middle:],attempt,correction_phase))
            queue.appendleft((batch[:middle],attempt,correction_phase))
            print(f'Context window: splitting {len(batch)} entries into {middle} + {len(batch)-middle}; source text kept whole.',flush=True)
            continue
        except BatchTranslationError as exc:
            result,metrics,invalid=exc.output,exc.metrics,exc.failures
        except (ValueError,KeyError,TypeError) as exc:
            # Malformed model output can be isolated; transport errors must stop
            # the job instead of turning every remaining line into a fallback.
            result=[];metrics={'seconds':0,'items':len(batch)}
            invalid=[{'row':r,'error':str(exc)} for r in batch]
        for failure in reversed(invalid):
            row=failure['row']
            failure_attempt=max(attempt,failure.get('retry_attempt',attempt),retry_spent.get(row['id'],0))
            if correction_phase and failure.get('error_code') not in ('output_limit','input_budget'):
                from rpgmaker_protocol import correction_plan
                previous_plan=correction_plan(row['source'],retry_errors.get(row['id'],''),cfg)
                if previous_plan and correction_plan(row['source'],failure['error'],cfg) is None:
                    failure['error']=failure['error'].split('; output=',1)[0]+'; untranslated Japanese text; output='+repr(previous_plan[0])
            retry_errors[row['id']]=failure['error']
            can_correct=False
            if rpg_hy and not correction_phase:
                can_correct=correction_plan(row['source'],failure['error'],cfg) is not None
            if can_correct and failure_attempt<4 and not failure.get('terminal'):
                retry_spent[row['id']]=failure_attempt+1
                queue.appendleft(([row],0,True))
                print(f'Correcting residual Japanese in {row["id"]}; separate correction budget (1/2)',flush=True)
            elif correction_phase and attempt<1 and failure_attempt<4 and not failure.get('terminal'):
                retry_spent[row['id']]=failure_attempt+1
                queue.appendleft(([row],attempt+1,True))
                print(f'Retrying correction for {row["id"]} ({attempt+2}/2)',flush=True)
            elif not correction_phase and failure_attempt<2 and not failure.get('terminal'):
                retry_spent[row['id']]=failure_attempt+1
                queue.appendleft(([row],failure_attempt+1,False))
                print(f'Retrying item {row["id"]} alone ({failure_attempt+1}/2)',flush=True)
            else:
                result.append({'id':row['id'],'source':row['source'],'text':row['source'],
                    'model':cfg['model'],'fingerprint':fingerprint(cfg),
                    'status':'source_fallback','error':failure['error'],
                    **{k:failure[k] for k in ('input_format','error_code') if k in failure}})
                failures.append(failure)
                trace_event(cfg,uuid.uuid4().hex,'source_fallback',id=row['id'],usage=row.get('usage',row['kind']),error=failure['error'])
                save_json(project/'data/failed-items.json',failures)
                print(f'Kept original text for {row["id"]}; continuing',flush=True)
        persist(result,metrics)
    except Exception as exc:
        if getattr(exc,'_rpt_transport',False):
            # Save completed siblings before unwinding the failed runtime; never
            # wait here for a hung GPU request. Uncommitted entries remain pending.
            siblings=[]
            for task,(other,_,_) in running.items():
                if not task.done() or task.cancelled():continue
                try:output,measurement=task.result()
                except BatchTranslationError as partial:output,measurement=partial.output,partial.metrics
                except Exception as sibling:
                    siblings.append({'request_id':getattr(sibling,'request_id',None),
                        'error':str(sibling),'http':getattr(sibling,'http_details',None)});continue
                persist(output,measurement)
            exc.recovery_state={'failed_batch':[r['id'] for r in batch],
                'in_flight':[[r['id'] for r in b] for b,_,_ in running.values()],
                'queued_batches':len(queue),'cached_total':len(known),'sibling_errors':siblings}
            exc._rpt_workers=list(running)
        raise
    finally:
        for future in running:future.cancel()
        # On interruption the owned runtime is killed as this function unwinds.
        pool.shutdown(wait=not running,cancel_futures=True)
    save_json(project/'data/failed-items.json',failures)
    if failures:print(f'{len(failures)} entries kept in original language; see data/failed-items.json',flush=True)
    if sample:
        save_json(project/'data/sample-review.json',[dict(r,translation=known[r['id']]['text']) for r in rows])

def retry_instruction(rows, errors=None, cfg=None):
    if cfg and cfg.get('engine')=='rpgmaker':
        from rpgmaker_protocol import retry_instruction as rpg_retry
        return rpg_retry(rows,errors or {},cfg)
    from token_recovery import retry_details
    detail=retry_details(rows,errors or {},cfg)
    from label_policy import label_batch
    if label_batch(rows):
        detail+=' These are names/roles or interface labels. Return only the translated label as a name or noun phrase; do not create dialogue, a speaker prefix, an event or an explanation.'
    if any('empty hyperlink text' in error for error in (errors or {}).values()):
        detail+=' Keep the translated link label BETWEEN its opening and closing markers; never leave the link empty.'
    from token_recovery import prepare_renpy_source
    if any(protect(prepare_renpy_source(r['source'])[0],cfg)[1] for r in rows):
        return 'Previous output failed validation. Preserve each input placeholder exactly once. Only movable_placeholders may change order; preserve the relative order of the remaining tags. Never add new placeholders. Translate the visible text fully into Korean.'+detail
    return 'Previous output failed validation. Return only each ID and its complete Korean translation. Do not add prefixes, suffixes, annotations, or control tokens.'+detail


def display_metadata(project,cfg,rows=None):
    from name_hints import ensure_metadata
    if cfg.get('_fix') in ('failed','layout'):
        path=project/'data/name-hints.json'
        metadata=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    else:metadata=ensure_metadata(project,rows)
    from name_translation import mapping,read
    from fixed_names import translations as fixed_translations
    metadata=dict(metadata,fixed_names=fixed_translations(metadata,mapping(project),read(project/'data/identity-types.json',{})))
    if cfg.get('_fix') in ('names','failed','display','layout'):
        support=project/'staging/game/zz_rpt_korean.rpy'
        metadata['runtime_choices']=support.exists() and '_rpt_wrap_menu' in support.read_text(encoding='utf-8')
    guard=project/'staging/game/zz_rpt_reference.rpy'
    metadata['reference_guard']=cfg.get('_reference_guard',False) or guard.exists()
    metadata['show_reference']=cfg.get('show_reference',True)
    return metadata


def render(project,cfg,preview=False):
    from job_events import set_phase,progress
    set_phase('render.prepare','번역 파일 준비 중')
    from replacements import effective_cache
    from name_hints import ensure_metadata
    from display_text import compose
    # A standalone render must install the same size/reference adapter as run.
    from display_policy import install as install_reference
    install_reference(project,cfg,refresh_choices=False)
    rows=read_catalog(project); known=effective_cache(project,cfg,report=True)
    selected=cfg.get('_render_ids')
    if selected is not None:rows=[r for r in rows if r['id'] in selected]
    metadata=display_metadata(project,cfg)
    missing=[r['id'] for r in rows if r['id'] not in known]
    if missing and not preview: raise ValueError(f'{len(missing)} untranslated entries')
    files={}
    for row in rows:
        if row['id'] not in known:
            known[row['id']]={'text':row['source']}
            files.setdefault(row['file'],[]).append(row)
            continue
        errors=validate_entry(row['source'],known[row['id']],cfg)
        if errors and not cfg.get('_repair'): raise ValueError(f'{row["id"]}: {errors}')
        files.setdefault(row['file'],[]).append(row)
    set_phase('render.write','번역 파일 생성 중',len(files),'files')
    for file_index,(rel, entries) in enumerate(files.items(),1):
        existing=project/'staging/game/tl'/cfg['language']/rel
        base=existing if selected is not None and existing.exists() else project/'data/templates'/rel
        lines=base.read_text(encoding='utf-8-sig').splitlines(keepends=True)
        for row in entries:
            i=row['line']; line=lines[i]
            if selected is not None:
                slot=list(QUOTED.finditer(line))[row.get('literal_index',0)]
                start,end=slot.span()
            else:start,end=row['start'],row['end']
            composed=compose(row,known[row['id']],metadata)
            from patch_validation import validate_final
            final_errors=validate_final(row,composed,cfg)
            if final_errors:
                save_json(project/'data/final-text-validation.json',{'errors':[{'id':row['id'],'file':rel,'line':i+1,'errors':final_errors}]})
                raise ValueError('Invalid rendered text '+row['id']+': '+', '.join(final_errors))
            lines[i]=line[:start]+quote(composed)+line[end:]
        path=project/'staging/game/tl'/cfg['language']/rel
        path.parent.mkdir(parents=True,exist_ok=True)
        text=''.join(lines)
        if not path.exists() or path.read_text(encoding='utf-8')!=text:path.write_text(text,encoding='utf-8')
        progress('render.write',file_index,len(files))
    failed=sum(known.get(r['id'],{}).get('status')=='source_fallback' for r in rows)
    original=sum(known.get(r['id'],{}).get('status')!='source_fallback' and known.get(r['id'],{}).get('text')==r['source'] for r in rows if r['id'] not in missing)
    translated=len(rows)-failed-original-len(missing)
    progress('render.write',len(files),len(files),successful=translated,original_kept=failed)
    save_json(project/'data/final-text-validation.json',{'entries':len(rows),'errors':[]})
    print(f'Rendered {len(rows)} entries; translated: {translated}; failed/original kept: {failed}; intentional or unchanged original: {original}; cache missing: {len(missing)}',flush=True)

def validate(project,cfg):
    from job_events import set_phase,progress
    set_phase('validate.text','번역 자료 확인 중')
    from replacements import effective_cache
    rows=read_catalog(project); known=effective_cache(project,cfg);errors=[]
    for r in rows:
        if r['id'] not in known: errors.append({'id':r['id'],'errors':['missing']});continue
        e=validate_entry(r['source'],known[r['id']],cfg)
        if e: errors.append({'id':r['id'],'errors':e})
    save_json(project/'data/validation.json',{'entries':len(rows),'errors':errors})
    if errors: raise ValueError(f'{len(errors)} translation errors')
    set_phase('validate.compile','스크립트 컴파일 검사 중')
    engine_command(project,['compile'],'compile.log')
    set_phase('validate.lint','스크립트 연결 검사 중')
    engine_command(project,['lint'],'lint.log')
    source=verify_source(project,cfg)
    detail='saved project snapshot' if cfg.get('_prepared_snapshot') else f'source {source["files"]} files unchanged'
    print(f'Validation passed: {len(rows)} entries; {detail}',flush=True)
