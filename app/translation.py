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
from script_literals import literal_eval
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextvars import copy_context
from engine import engine_command, save_json, verify_source
from source_language import letters, normalize, instruction as language_instruction, source_units

QUOTED = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')
TOKENS = re.compile(r'\{\{|\[\[|\{[^{}]*\}|\[[^\[\]\n]*\]|%\([^)]+\)[#0 +\-]*\d*(?:\.\d+)?[a-zA-Z]')
HANGUL = re.compile(r'[가-힣]')
# Actual percent-format conversion types, not arbitrary letters in prose (% to).
FORMATS = re.compile(r'(?!(?<=\d)%[ \t]+[A-Za-z])%(?:\([^)]+\))?[#0 +\-]*(?:\d+|\*)?(?:\.(?:\d+|\*))?[hlL]?[diouxXeEfFgGcrsa%](?![A-Za-z])')
# Accept delimiter-only damage, including the old Markdown-like spelling.
PLACEHOLDER = re.compile(r'<\s*rpt\s*[_-]?\s*(\d+)\s*/?\s*>|(?:\\?[_*])*RPT(?:\\?[_*])+(\d+)(?:\\?[_*])*', re.I)
TRACE_LOCK=threading.Lock()


def trace_event(cfg, request_id, event, **details):
    """Append exact local requests/responses; retries and concurrent calls keep IDs."""
    if not cfg.get('_trace_dir'): return
    record = dict(request_id=request_id, event=event,
                  source_language=normalize(cfg.get('source_language')),target_language='korean',
                  phase=cfg.get('_trace_phase','translation'),
                  recorded_at=datetime.now(timezone.utc).isoformat(), **details)
    if cfg.get('_runtime_pid'):
        record['server_pid']=cfg['_runtime_pid']
        record['server_log']=cfg.get('_runtime_log')
    path = Path(cfg['_trace_dir'])/'translation-requests.jsonl'
    with TRACE_LOCK:
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(record,ensure_ascii=False)+'\n')


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

def protect(source):
    tokens = []
    def substitute(m):
        tokens.append(m.group())
        return f'<rpt{len(tokens)-1:03d}/>'
    return re.sub(TOKENS.pattern+'|'+FORMATS.pattern,substitute,source), tokens

def movable_token(token):
    """Named interpolation can move with grammar; formatting/control tags cannot."""
    return ((token.startswith('[') and not token.startswith('[[') and token.endswith(']'))
            or token.startswith('%('))


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
    errors = []
    if not source.strip() and target == source:return errors
    if not target.strip(): errors.append('empty')
    if token_signature(TOKENS.findall(source)) != token_signature(TOKENS.findall(target)):
        errors.append('markup mismatch')
    if check_link_content and populated_links(source) - populated_links(target):
        errors.append('empty hyperlink text')
    if Counter(FORMATS.findall(source)) != Counter(FORMATS.findall(target)): errors.append('format field mismatch')
    if PLACEHOLDER.search(target): errors.append('unrestored placeholder')
    if re.search(r'<think>|</think>|```', target): errors.append('model commentary')
    visible_source=FORMATS.sub('',TOKENS.sub('',source))
    visible_target=FORMATS.sub('',TOKENS.sub('',target))
    if any(c.isalpha() for c in visible_source) and not any(c.isalnum() for c in visible_target):
        errors.append('punctuation-only translation')
    if len(re.findall(r'[A-Za-z]+', FORMATS.sub('',TOKENS.sub('', source)))) >= 5 and not HANGUL.search(target):
        # Numeric keys, URLs, and short acronyms may legitimately remain unchanged.
        if not re.fullmatch(r'(?:https?://\S+|[A-Z0-9 .:/_+\-]+)', TOKENS.sub('', source)):
            errors.append('no Korean translation')
    if normalize(cfg.get('source_language'))=='japanese' and letters(visible_source) and not HANGUL.search(visible_target):
        # Tiny labels, symbols and deliberate originals must not abort a game.
        if len([c for c in visible_source if c.isalpha()])>=5 and re.search(r'[\u3040-\u30ff\u3400-\u9fff]',visible_source):
            if 'no Korean translation' not in errors:errors.append('no Korean translation')
    return errors

def validate_entry(source, entry, cfg):
    errors=validate_text(source,entry['text'],cfg)
    if entry.get('status') in ('source_fallback','ui_original') and entry['text']==source:
        errors=[e for e in errors if e!='no Korean translation']
    return errors

def request(endpoint, route, body):
    from model_runtime import track_request
    body=track_request(endpoint,route,body)
    req = urllib.request.Request(endpoint.rstrip('/')+route,
        data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type':'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=600) as response:
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
                    (cfg.get('_reuse_completed') and (row.get('model')==cfg['model'] or cfg.get('_reuse_previous_models')) and sources.get(row['id'])==row.get('source'))):
                    # Old format-only entries may contain visible Japanese prose.
                    if row.get('model')=='literal format' and letters(FORMATS.sub('',TOKENS.sub('',row.get('source','')))):
                        continue
                    out[row['id']] = row
    recovered=project/'data/recovered-translations.json'
    if recovered.exists():
        for uid,item in json.loads(recovered.read_text(encoding='utf-8')).items():
            prior=out.get(uid)
            if (prior and prior.get('status')=='source_fallback' and
                    item.get('source')==sources.get(uid) and item.get('before')==prior['text']):
                out[uid]=dict(prior,text=item['text'],status='failure_recovered',error=None)
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
    return out

def translate_batch(rows, context, cfg, retry_note=''):
    from diagnostics import stage, report
    stage('translation request',cfg=cfg)
    from name_hints import placeholder_hints
    from hy_backend import is_hy, body as hy_body, parse as hy_parse
    from token_recovery import split_outer_styles, restore_output
    from label_policy import context_for, label_batch, expansion_errors
    inputs = []; protected = {}; wrappers = {}
    for index, row in enumerate(rows):
        prefix,core,suffix=split_outer_styles(row['source'])
        wrappers[row['id']]=(prefix,suffix)
        text, tokens = protect(core); protected[row['id']] = tokens
        inputs.append({'id': str(index), 'kind': row['kind'], 'usage':row.get('usage',row['kind']), 'speaker':row.get('speaker','unknown'), 'text':text,
                       'name_hints':placeholder_hints(tokens,cfg.get('_names',{})),
                       'movable_placeholders':['<rpt%03d/>'%i for i,t in enumerate(tokens) if movable_token(t)]})
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
    if any(protected.values()):
        system+='\nPreserve each input placeholder exactly once. IDs in movable_placeholders represent named variables and may move for Korean word order. Keep all other placeholders in their original relative order. Never invent a placeholder or copy one from another item.'
    properties={}
    for i,row in enumerate(rows):
        spec={'type':'string','minLength':1}
        properties[str(i)]=spec
    schema = {'type':'object','properties':properties,
        'required':[str(i) for i in range(len(rows))],'additionalProperties':False}
    body = {'model':cfg['model'],'stream':False,'think':False,'format':schema,'keep_alive':'10m',
        'options':{'temperature':0.2,'num_ctx':cfg.get('num_ctx',8192),
            'num_predict':min(2048, 256+sum(source_units(r['source']) for r in rows)*7),
            'repeat_penalty':1.1,'seed':42},
        'messages':[{'role':'system','content':system},{'role':'user','content':json.dumps({
            'context_do_not_translate':context,'items':inputs,'retry_note':retry_note},ensure_ascii=False)}]}
    request_id = uuid.uuid4().hex
    def build(selected):
        if is_hy(cfg):return hy_body(inputs,dict(selected,person_name_spellings=person_names),cfg,relevant_glossary,retry_note)
        result=dict(body)
        result['messages']=[body['messages'][0],{'role':'user','content':json.dumps({
            'context_do_not_translate':{k:v for k,v in selected.items() if k!='current_state'},
            **({'current_state_reference_only':selected['current_state']} if selected.get('current_state') else {}),
            'items':inputs,'retry_note':retry_note},ensure_ascii=False)}]
        return result
    from request_budget import fit, BatchNeedsSplit
    try:
        fitted,budget=fit(context,cfg,build)
    except BatchNeedsSplit as exc:
        trace_event(cfg,request_id,'budget_split',reason=str(exc),
                    items=[r['id'] for r in rows],context_budget=exc.context_budget)
        raise
    except Exception as exc:
        report(exc,cfg,rows,request_id);raise
    body=fitted
    trace_event(cfg,request_id,'request',
                items=[{k:r.get(k) for k in ('id','file','block','source_file','source_line')} for r in rows],
                request=body,context_budget=budget,context_policy=context_policy,
                item_purposes=[{'id':r['id'],'usage':r.get('usage',r['kind']),'evidence':r.get('usage_evidence')} for r in rows])
    if budget['dropped'] or budget.get('preselection_dropped'):
        print('Context budget adjusted: request '+request_id+'; see data/translation-requests.jsonl',flush=True)
    start = time.monotonic()
    try:
        response = request(cfg['endpoint'],'/api/chat',body)
    except Exception as exc:
        exc.request_id=request_id
        import traceback
        trace_event(cfg,request_id,'request_error',error=str(exc),error_type=type(exc).__name__,traceback=traceback.format_exc())
        report(exc,cfg,rows,request_id)
        raise
    actual=response.get('prompt_eval_count')
    runtime=runtime_status(cfg)
    trace_event(cfg,request_id,'response',response=response,runtime=runtime,
                actual_prompt_tokens=actual,num_ctx_requested=body['options']['num_ctx'])
    if actual and actual+body['options']['num_predict']>body['options']['num_ctx']:
        from request_budget import InputBudgetError
        exc=InputBudgetError('Actual prompt consumed output reserve; request '+request_id)
        report(exc,cfg,rows,request_id);raise exc
    if cfg.get('_trace_dir'):
        with TRACE_LOCK:save_json(Path(cfg['_trace_dir'])/'last-model-response.json',response)
    try:
        parsed = hy_parse(response['message']['content']) if is_hy(cfg) else json.loads(response['message']['content'])
    except (ValueError,KeyError,TypeError) as exc:
        if cfg.get('_trace_dir'):
            with TRACE_LOCK:save_json(Path(cfg['_trace_dir'])/'last-invalid-model-response.json',response)
        report(exc,cfg,rows,request_id,handled=True);raise
    if not isinstance(parsed,dict):
        try:raise ValueError('Response must be a JSON object')
        except ValueError as exc:
            report(exc,cfg,rows,request_id,handled=True);raise
    if response.get('done_reason')=='length':
        # Earlier complete Hy rows can still be retained; the final row may be cut mid-sentence.
        if is_hy(cfg) and parsed: parsed.pop(next(reversed(parsed)),None)
        else:
            try:raise ValueError('Translation response reached the output limit')
            except ValueError as exc:
                report(exc,cfg,rows,request_id,handled=True);raise
    output=[];failures=[]
    for index, row in enumerate(rows):
        try:
            prefix,suffix=wrappers[row['id']]
            text = prefix+restore_output(parsed[str(index)],protected[row['id']])+suffix
            errors = validate_text(row['source'],text,cfg,check_link_content=True)
            errors += expansion_errors(row,text)
            if errors: raise ValueError(f'{row["id"]}: {errors}; output={parsed[str(index)]!r}')
        except (ValueError,KeyError,TypeError) as exc:
            error=str(exc)
            if any(wrappers[row['id']]):error='Wrapper-stripped: '+error
            failures.append({'row':row,'error':error});continue
        output.append({'id':row['id'],'source':row['source'],'text':text,
                       'model':cfg['model'],'fingerprint':fingerprint(cfg),
                       'context_digest':budget['context_sha256'],'context_files':budget.get('files',{}),
                       'context_scope':budget.get('scope',''),'request_id':request_id})
    metrics = {'seconds':round(time.monotonic()-start,2),'tokens':response.get('eval_count'),
               'eval_seconds':response.get('eval_duration',0)/1e9,'items':len(rows)}
    trace_event(cfg,request_id,'validation',accepted_ids=[r['id'] for r in output],
                failures=[{'id':f['row']['id'],'error':f['error']} for f in failures])
    if failures:
        try:raise BatchTranslationError(output,failures,metrics)
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
    rows=read_catalog(project); known=cache(project,cfg)
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
    cfg=dict(cfg,_trace_dir=str(project/'data'),_trace_phase='translation')
    from diagnostics import stage
    stage('translation',project,cfg)
    from name_translation import mapping
    cfg['_person_names']=mapping(project)
    if rows is None:rows=read_catalog(project)
    if known is None:known=cache(project,cfg)
    cfg['_names']=ensure_metadata(project,rows)['names']
    memory=TermMemory(rows,known)
    from source_context import SourceContext
    all_rows=rows
    if sample:
        dialogue=[r for r in rows if r['kind']=='dialogue']
        # Beginning, later scenes, hyperlinks and UI all represented.
        chosen=dialogue[:12]+dialogue[len(dialogue)//2:len(dialogue)//2+8]+[r for r in dialogue if '{a=' in r['source']][:4]+[r for r in rows if r['kind']=='string'][:12]
        ids={r['id'] for r in chosen}; rows=[r for r in rows if r['id'] in ids]
    pending=[r for r in rows if r['id'] not in known]
    pending_files={r.get('file','') for r in pending}
    context_index=SourceContext([r for r in all_rows if r.get('file','') in pending_files],project,cfg) if pending else None
    started=time.monotonic(); size=cfg.get('batch_size',12); completed=0
    print(f'Translating {len(pending)} remaining entries with {cfg["model"]}',flush=True)
    queue=[]
    for row in pending:
        if not queue or len(queue[-1])>=size or not context_index.same_scope(queue[-1][-1],row) or sum(source_units(r['source']) for r in queue[-1])+source_units(row['source'])>cfg.get('max_batch_source_words',240) or sum(len(r['source'].encode('utf-8')) for r in queue[-1])+len(row['source'].encode('utf-8'))>min(5000,cfg.get('num_ctx',8192)//2):
            queue.append([])
        queue[-1].append(row)
    queue=deque((batch,0) for batch in queue)
    failures=[{'row':r,'error':known[r['id']].get('error','Previous translation failed')}
              for r in rows if known.get(r['id'],{}).get('status')=='source_fallback']
    workers=max(1,min(2,cfg.get('parallel',1)))
    pool=ThreadPoolExecutor(max_workers=workers)
    running={}
    retry_errors={}
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
        temp.replace(cachefile)
        for item in result:known[item['id']]=item
        if cfg.get('_recovery_incident') and cfg.get('_recovery_event'):
            cfg['_recovery_event']('cache_committed',incident_id=cfg['_recovery_incident'],
                ids=[item['id'] for item in result],cached_total=len(known))
        if memory.observe(result):save_json(project/'data/term-memory.json',memory.translations)
        with (project/'data/metrics.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(metrics)+'\n')
        completed+=len(result)
        save_json(project/'data/progress.json',{'mode':'sample' if sample else 'full','completed':completed,
            'pending_at_start':len(pending),'cached_total':len(known),'seconds':round(time.monotonic()-started)})
        print(f'{completed}/{len(pending)} entries, batch {metrics["seconds"]}s',flush=True)
    def submit(batch,attempt):
        context=context_index.context(batch,cfg)
        batch_cfg=dict(cfg,_batch_glossary=memory.relevant(batch),_allow_batch_split=len(batch)>1)
        retry_note=retry_instruction(batch,retry_errors) if attempt else ''
        if attempt:
            trace_event(batch_cfg,uuid.uuid4().hex,'item_retry',attempt=attempt,
                        items=[{'id':r['id'],'usage':r.get('usage',r['kind']),'error':retry_errors.get(r['id'])} for r in batch])
        future=pool.submit(copy_context().run,translate_batch,batch,context,batch_cfg,retry_note)
        running[future]=(batch,attempt)
    from request_budget import BatchNeedsSplit
    try:
      while queue or running:
        while queue and len(running)<workers:
            batch,attempt=queue.popleft();submit(batch,attempt)
        ready,_=wait(running,return_when=FIRST_COMPLETED)
        future=next(iter(ready));batch,attempt=running.pop(future)
        invalid=[]
        try:
            result,metrics=future.result()
        except BatchNeedsSplit:
            middle=len(batch)//2
            queue.appendleft((batch[middle:],attempt))
            queue.appendleft((batch[:middle],attempt))
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
            retry_errors[row['id']]=failure['error']
            if attempt<2:
                queue.appendleft(([row],attempt+1))
                print(f'Retrying item {row["id"]} alone ({attempt+1}/2)',flush=True)
            else:
                result.append({'id':row['id'],'source':row['source'],'text':row['source'],
                    'model':cfg['model'],'fingerprint':fingerprint(cfg),
                    'status':'source_fallback','error':failure['error']})
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
            for task,(other,_) in running.items():
                if not task.done() or task.cancelled():continue
                try:output,measurement=task.result()
                except BatchTranslationError as partial:output,measurement=partial.output,partial.metrics
                except Exception as sibling:
                    siblings.append({'request_id':getattr(sibling,'request_id',None),
                        'error':str(sibling),'http':getattr(sibling,'http_details',None)});continue
                persist(output,measurement)
            exc.recovery_state={'failed_batch':[r['id'] for r in batch],
                'in_flight':[[r['id'] for r in b] for b,_ in running.values()],
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

def retry_instruction(rows, errors=None):
    from token_recovery import retry_details
    detail=retry_details(rows,errors or {})
    from label_policy import label_batch
    if label_batch(rows):
        detail+=' These are names/roles or interface labels. Return only the translated label as a name or noun phrase; do not create dialogue, a speaker prefix, an event or an explanation.'
    if any('empty hyperlink text' in error for error in (errors or {}).values()):
        detail+=' Keep the translated link label BETWEEN its opening and closing markers; never leave the link empty.'
    if any(protect(r['source'])[1] for r in rows):
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
    return metadata


def render(project,cfg,preview=False):
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
    for rel, entries in files.items():
        existing=project/'staging/game/tl'/cfg['language']/rel
        base=existing if selected is not None and existing.exists() else project/'data/templates'/rel
        lines=base.read_text(encoding='utf-8-sig').splitlines(keepends=True)
        for row in entries:
            i=row['line']; line=lines[i]
            if selected is not None:
                slot=list(QUOTED.finditer(line))[row.get('literal_index',0)]
                start,end=slot.span()
            else:start,end=row['start'],row['end']
            lines[i]=line[:start]+quote(compose(row,known[row['id']],metadata))+line[end:]
        path=project/'staging/game/tl'/cfg['language']/rel
        path.parent.mkdir(parents=True,exist_ok=True)
        text=''.join(lines)
        if not path.exists() or path.read_text(encoding='utf-8')!=text:path.write_text(text,encoding='utf-8')
    failed=sum(known.get(r['id'],{}).get('status')=='source_fallback' for r in rows)
    original=sum(known.get(r['id'],{}).get('status')!='source_fallback' and known.get(r['id'],{}).get('text')==r['source'] for r in rows if r['id'] not in missing)
    translated=len(rows)-failed-original-len(missing)
    print(f'Rendered {len(rows)} entries; translated: {translated}; failed/original kept: {failed}; intentional or unchanged original: {original}; cache missing: {len(missing)}',flush=True)

def validate(project,cfg):
    from replacements import effective_cache
    rows=read_catalog(project); known=effective_cache(project,cfg);errors=[]
    for r in rows:
        if r['id'] not in known: errors.append({'id':r['id'],'errors':['missing']});continue
        e=validate_entry(r['source'],known[r['id']],cfg)
        if e: errors.append({'id':r['id'],'errors':e})
    save_json(project/'data/validation.json',{'entries':len(rows),'errors':errors})
    if errors: raise ValueError(f'{len(errors)} translation errors')
    engine_command(project,['compile'],'compile.log')
    engine_command(project,['lint'],'lint.log')
    source=verify_source(project,cfg)
    detail='saved project snapshot; original source not accessed' if cfg.get('_prepared_snapshot') else f'source {source["files"]} files unchanged'
    print(f'Validation passed: {len(rows)} entries; {detail}',flush=True)
