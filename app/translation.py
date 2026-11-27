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
from http.client import IncompleteRead, BadStatusLine
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

def protect(source,cfg=None,*,unity_visible_digits=False):
    if cfg and cfg.get('engine')=='kirikiri':
        from kirikiri_tokens import protect as kirikiri_protect
        return kirikiri_protect(source,cfg)
    if cfg and cfg.get('engine')=='rpgmaker':
        from rpgmaker_tokens import protect as rpg_protect
        return rpg_protect(source)
    if cfg and cfg.get('engine')=='unity':
        from unity_text_formats import protected_tokens
        matches=protected_tokens(source,include_numbers=not unity_visible_digits)
    else:
        matches=TOKENS.finditer(source,formats=FORMATS)
    tokens=[];pieces=[];cursor=0
    for match in matches:
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

def validate_text(source, target, cfg, *, check_link_content=False, language_details=None):
    if cfg.get('engine')=='kirikiri':
        from kirikiri_translation import validate
        return validate(source,target,cfg,language_details=language_details)
    if cfg.get('engine')=='rpgmaker':
        from rpgmaker_tokens import validate as rpg_validate
        return rpg_validate(source,target,cfg)
    if cfg.get('engine')=='unity':
        from unity_text_formats import validate_replacement
        from japanese_output_policy import enabled, language_errors
        if not validate_replacement(source,target,kind=cfg.get('_unity_text_kind')):
            return ['Unity text structure mismatch']
        if re.search(r'<think>|</think>|```',target):
            return ['model commentary']
        if enabled(cfg):
            return language_errors(source,target,cfg,language_details)
        source_text=PLACEHOLDER.sub('',protect(source,cfg)[0])
        target_text=PLACEHOLDER.sub('',protect(target,cfg)[0])
        if len(re.findall(r'[A-Za-z]+',source_text))>=5 and not HANGUL.search(target_text):
            return ['no Korean translation']
        return []
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
    # Escaped brackets and complete interpolations were removed by token_view.
    # A new bare '[' still reaches Ren'Py's interpolation parser at runtime.
    if target_visible.count('[') > source_visible.count('['):
        errors.append('unclosed interpolation')
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
    from japanese_output_policy import enabled as japanese_enabled, language_errors, small_tsu_punctuation_result
    if japanese_enabled(cfg):
        if small_tsu_punctuation_result(source,target,cfg):
            errors=[error for error in errors if error!='punctuation-only translation']
        for error in language_errors(source,target,cfg,language_details):
            if error not in errors:errors.append(error)
    return errors

def validate_entry(source, entry, cfg, *, allow_partial=False):
    if entry.get('status')=='manual_translation':return []
    if cfg.get('engine')=='kirikiri':
        from kirikiri_translation import row_config
        cfg=row_config(cfg,entry)
    if cfg.get('engine')=='unity' and entry.get('unity_format'):
        cfg=dict(cfg,_unity_text_kind=entry['unity_format'])
    errors=validate_text(source,entry['text'],cfg)
    if entry.get('status') in ('source_fallback','ui_original') and entry['text']==source:
        errors=[e for e in errors if e not in ('no Korean translation','untranslated Japanese text')]
    if allow_partial and entry.get('status')=='partial_translation':
        from japanese_output_policy import enabled,classify
        if enabled(cfg) and classify(source,entry['text'],cfg)['kind'] in ('minor','mixed'):
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
    except (urllib.error.URLError, TimeoutError, ConnectionError, IncompleteRead, BadStatusLine) as exc:
        from request_diagnostics import mark_transport_failure
        mark_transport_failure(exc,endpoint,route)
        raise

def fingerprint(cfg):
    relevant = {k:cfg.get(k) for k in ('model','language','style','glossary','num_ctx')}
    if cfg.get('engine') in ('kirikiri','unity'):
        relevant.update(engine=cfg['engine'],protocol=1)
    if cfg.get('analysis_digest'):relevant['analysis_digest']=cfg['analysis_digest']
    if normalize(cfg.get('source_language'))!='english':relevant['source_language']=normalize(cfg.get('source_language'))
    return hashlib.sha256(json.dumps(relevant, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def cache(project, cfg):
    path = project / 'data/translations.jsonl'
    out = {}
    from japanese_output_policy import enabled as japanese_enabled
    histories={} if japanese_enabled(cfg) and not cfg.get('_skip_name_review') else None
    rows=read_catalog(project);sources={r['id']:r['source'] for r in rows}
    source_aliases={r['id']:set(r.get('source_aliases',[]))|{r.get('base_source',r['source']),r['source']}
                    for r in rows} if cfg.get('engine')=='rpgmaker' else {}
    active_fingerprint=fingerprint(cfg)
    accepted_fingerprints={active_fingerprint}
    for device in cfg.get('_gpu_device_plan',[]):
        accepted_fingerprints.add(fingerprint(dict(cfg,**device.get('config',{}))))
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
                if ((row.get('status')=='manual_translation' and sources.get(row['id'])==row.get('source')) or
                    row['fingerprint'] in accepted_fingerprints or row['fingerprint'] == cfg.get('imported_cache_fingerprint') or
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
                    if histories is not None and row['id'] in out:
                        histories.setdefault(row['id'],[out[row['id']]]).append(row)
                    out[row['id']] = row
    if japanese_enabled(cfg):
        out={uid:dict(item,_japanese_cache_before=item['text']) for uid,item in out.items()}
    manual={uid:dict(item) for uid,item in out.items() if item.get('status')=='manual_translation'}
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
            if cfg.get('engine')=='unity':
                if item.get('_unity_numeric_review'):out[uid]['_unity_numeric_review']=True
                else:out[uid].pop('_unity_numeric_review',None)
            if cfg.get('engine')=='kirikiri' and item.get('ruby_revision'):
                from kirikiri_ruby import REVISION
                if item['ruby_revision']==REVISION:
                    out[uid]['_japanese_ruby_revision']=REVISION
                    out[uid].pop('_japanese_ruby_pending',None)
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
    out.update(manual)
    if cfg.get('engine')=='kirikiri':
        if not cfg.get('_skip_name_review'):
            from name_review import overlay
            out,_=overlay(project,rows,out,engine='kirikiri')
            out.update(manual)
        for r in rows:
            if r['id'] not in out:
                from kirikiri_tokens import readable
                from kirikiri_translation import row_config
                if not any(c.isalpha() for c in readable(r['source'],row_config(cfg,r))):
                    out[r['id']]=dict(id=r['id'],source=r['source'],text=r['source'],status='literal',
                        model='literal controls',fingerprint=active_fingerprint,dialect=r.get('dialect','kag'),usage=r.get('usage'))
    if cfg.get('engine')=='rpgmaker':
        if not cfg.get('_skip_name_review'):
            from name_review import overlay,configuration
            out,_=overlay(project,rows,out,engine='rpgmaker')
            out.update(manual)
            cfg=configuration(project,cfg,rows)
        from rpgmaker_layout import repair as repair_layout
        from rpgmaker_residue import needs_repair
        from rpgmaker_names import validation_config
        cfg=validation_config(cfg,rows,out)
        from replacements import load_rules,replace_visible
        residue_rules=load_rules(project/'replacements.json')
        from japanese_cache_repair import repair as repair_japanese_cache
        out=repair_japanese_cache(project,rows,out,cfg)
        invalid=[]
        for uid,item in list(out.items()):
            if uid not in sources:continue
            if item.get('status')=='manual_translation':continue
            from rpgmaker_output_policy import enabled as policy_enabled,prepare_output
            if (policy_enabled(cfg) and item.get('model')!='reviewed override'
                    and item.get('status') not in ('source_fallback','ui_original','literal')):
                cleaned,_=prepare_output(sources[uid],item['text'],cfg)
                if cleaned!=item['text']:
                    item=dict(item,text=cleaned);out[uid]=item
                    if item.get('status')=='partial_translation':
                        item.update(status='failure_recovered',error=None)
            repaired,changes=repair_layout(sources[uid],item['text'])
            if changes:
                item=dict(item,text=repaired);out[uid]=item
                trace_event(dict(cfg,_trace_dir=str(project/'data')),uuid.uuid4().hex,
                            'rpg_cached_layout_repair',id=uid,placements=changes,cache_modified=False)
            # Residual source script stays available as a saved correction draft.
            # Structural errors still invalidate a cache entry immediately.
            checked=dict(item,text=replace_visible(item['text'],residue_rules)[0]) if residue_rules else item
            errors=validate_entry(sources[uid],checked,cfg)
            language_errors=[error for error in errors if error in ('untranslated Japanese text','no Korean translation')]
            errors=[error for error in errors if error not in language_errors]
            if errors:
                invalid.append({'id':uid,'errors':errors})
                del out[uid]
            elif needs_repair({'id':uid,'source':sources[uid]},checked,cfg,errors=language_errors):
                # Keep completed text available for offline rendering. Only a
                # translation/failed-repair task queues this incomplete draft.
                out[uid]=dict(item,_rpg_residual_language=True,_japanese_residual_language=True)
            elif (item.get('_rpg_residual_language') or policy_enabled(cfg) and
                  (item.get('_japanese_residual_language') or item.get('status')=='partial_translation')):
                out[uid]=dict(item);out[uid].pop('_rpg_residual_language',None)
                if policy_enabled(cfg):
                    out[uid].pop('_japanese_residual_language',None)
                    out[uid].pop('_japanese_residue_kind',None)
                    if item.get('status')=='partial_translation':
                        out[uid].update(status='failure_recovered',error=None)
        if invalid:
            # Keep the append-only cache intact; only invalid items re-enter the queue.
            save_json(project/'data/rpg-invalid-cache.json',{'entries':invalid,'cache_deleted':False})
    out.update(manual)
    if cfg.get('engine')=='kirikiri':
        for r in rows:
            if r['id'] in out:
                out[r['id']]=dict(out[r['id']],dialect=r.get('dialect','kag'),usage=r.get('usage'))
    if histories:
        from japanese_resume import clean_history_recoveries
        if cfg.get('engine')=='kirikiri':
            from name_review import configuration
            cfg=configuration(project,cfg,rows)
        elif cfg.get('engine') not in ('rpgmaker','unity'):
            from name_translation import mapping
            cfg=dict(cfg,_person_names=mapping(project))
        out,_=clean_history_recoveries(rows,out,histories,cfg)
    if cfg.get('engine')!='rpgmaker':
        from japanese_resume import annotate
        out=annotate(project,rows,out,cfg)
    if cfg.get('engine')=='kirikiri' and japanese_enabled(cfg) and not cfg.get('_skip_name_review'):
        from kirikiri_reviewed_terms import apply as apply_reviewed_terms
        out=apply_reviewed_terms(project,rows,out,cfg)
    return out

def translate_batch(rows, context, cfg, retry_note=''):
    from rpgmaker_output_policy import enabled as output_policy_enabled
    output_policy=output_policy_enabled(cfg)
    from japanese_output_policy import enabled as japanese_enabled
    japanese_policy=japanese_enabled(cfg)
    if cfg.get('engine')=='rpgmaker' and '_rpg_name_groups' not in cfg:
        from rpgmaker_names import validation_config
        cfg=validation_config(cfg,rows)
    rpg=cfg.get('engine')=='rpgmaker'
    unity=cfg.get('engine')=='unity'
    if unity:
        from unity_output import INPUT_FORMAT
        from unity_text_formats import quantity_mismatch
    if japanese_policy:
        from japanese_output_policy import small_tsu_only_source,prepare_output
        from japanese_cache_repair import numeric_candidate
        local=[];remaining=[]
        for row in rows:
            check_cfg=dict(cfg,_usage=row.get('usage',row['kind']))
            if cfg.get('engine')=='kirikiri':
                from kirikiri_translation import row_config
                check_cfg=row_config(check_cfg,row)
            numeric=numeric_candidate(row['source'],check_cfg)
            if numeric is not None and not validate_text(row['source'],numeric,check_cfg):
                local.append(dict(id=row['id'],source=row['source'],text=numeric,
                                  model=cfg['model'],fingerprint=fingerprint(cfg),
                                  _japanese_offline_repair='numeric_value'))
                continue
            if not rpg and small_tsu_only_source(row['source'],check_cfg):
                cleaned,_=prepare_output(row['source'],row['source'],check_cfg)
                if cleaned!=row['source'] and not validate_text(row['source'],cleaned,check_cfg):
                    local.append(dict(id=row['id'],source=row['source'],text=cleaned,
                                      model=cfg['model'],fingerprint=fingerprint(cfg)))
                    continue
            remaining.append(row)
        if local:
            if not remaining:return local,dict(seconds=0,items=len(rows),tokens=0,eval_seconds=0)
            try:translated,metrics=translate_batch(remaining,context,cfg,retry_note)
            except BatchTranslationError as exc:
                exc.output=local+exc.output;raise
            return local+translated,dict(metrics,items=len(rows))
    if japanese_policy and not rpg and cfg.get('_japanese_retry_kind') in ('minor','no_korean','mixed'):
        from rpgmaker_input import clean_context
        context,dropped=clean_context(context)
        audit=dict(context.get('_context_audit',{}))
        audit['preselection_dropped']=list(audit.get('preselection_dropped',[]))+dropped
        context=dict(context,_context_audit=audit)
        retry_note=''
    if rpg:
        from rpgmaker_input import configuration as input_configuration, clean_context
        if '_rpg_display_names' not in cfg:cfg=input_configuration(cfg,rows)
    if rpg:
        from rpgmaker_output_policy import small_tsu_only_source, prepare_output
        from rpgmaker_preprocessing import prepare as prepare_rpg_styles
        from rpgmaker_input import prepare as prepare_input, restore as restore_input
        local=[];remaining=[]
        for row in rows:
            visual_clean,visual_plan=prepare_rpg_styles(row['source'])
            if visual_plan['source_style_codes'] and not visual_clean.strip():
                local.append(dict(id=row['id'],source=row['source'],text=row['source'],model=cfg['model'],
                                  fingerprint=fingerprint(cfg),input_format='rpg_visual_stripped_v1'))
                continue
            if output_policy and small_tsu_only_source(row['source'],cfg):
                plan=prepare_input(row['source'],cfg,row.get('usage',row['kind']),row.get('speaker'))
                candidate=restore_input(plan['text'],plan)
                cleaned,_=prepare_output(row['source'],candidate,cfg)
                if cleaned!=candidate and not validate_text(row['source'],cleaned,cfg):
                    local.append(dict(id=row['id'],source=row['source'],text=cleaned,
                        model=cfg['model'],fingerprint=fingerprint(cfg),input_format='rpg_visual_stripped_v1'))
                    trace_event(cfg,uuid.uuid4().hex,'rpg_small_tsu_source_processed',id=row['id'],model_calls=0)
                    continue
            remaining.append(row)
        if local:
            if not remaining:return local,dict(seconds=0,items=len(rows),tokens=0,eval_seconds=0)
            try:translated,metrics=translate_batch(remaining,context,cfg,retry_note)
            except BatchTranslationError as exc:
                exc.output=local+exc.output
                raise
            return local+translated,dict(metrics,items=len(rows))
    clean_retry=rpg and bool(cfg.get('_rpg_clean_retry') or cfg.get('_rpg_full_source_retry'))
    if clean_retry:
        cfg=dict(cfg,_rpg_clean_retry=True,_rpg_retry_errors={},_rpg_correction_enabled=False,
                 _rpg_correction_mode=False,_rpg_fragment_correction=False,_rpg_plain_retry=False)
        context,dropped=clean_context(context)
        audit=dict(context.get('_context_audit',{}))
        audit['preselection_dropped']=list(audit.get('preselection_dropped',[]))+dropped
        context=dict(context,_context_audit=audit)
        retry_note=''
    elif output_policy and cfg.get('_rpg_retry_kind') in ('minor','no_korean','mixed'):
        # Retry the current utterance, not the surrounding Japanese that was
        # already present in the failed request. User context notes survive.
        context,dropped=clean_context(context)
        audit=dict(context.get('_context_audit',{}))
        audit['preselection_dropped']=list(audit.get('preselection_dropped',[]))+dropped
        context=dict(context,_context_audit=audit)
    from gpu_policy import translation_context
    context=translation_context(context,cfg)
    from diagnostics import stage, report
    stage('translation request',cfg=cfg)
    from name_hints import placeholder_hints
    from hy_backend import is_hy, body as hy_body, parse as hy_parse
    hy_model=is_hy(cfg)
    from token_recovery import prepare_renpy_source, strip_visual_styles, restore_output, restore_prepared_renpy_output
    from label_policy import context_for, label_batch, expansion_errors
    inputs = []; protected = {}; wrappers = {}; source_cores = {}; style_plans = {}; rpg_plans = {}; rpg_styles = {}
    kirikiri_source_spans = {}
    kirikiri = cfg.get('engine') == 'kirikiri'
    name_index = cfg.get('_kirikiri_name_index') if kirikiri and japanese_policy and not cfg.get('_name_preparation') else None
    for index, row in enumerate(rows):
        row_cfg = cfg
        names = []
        name_hints = {}
        if cfg.get('engine')=='rpgmaker':
            from rpgmaker_tokens import split_outer_controls
            prefix,core,suffix=split_outer_controls(row['source'])
            from rpgmaker_input import prepare as prepare_input
            rpg_plans[row['id']]=prepare_input(core,cfg,row.get('usage',row['kind']),speaker=row.get('speaker'))
            core=rpg_plans[row['id']]['text']
            from rpgmaker_preprocessing import prepare as prepare_rpg_styles
            core,rpg_styles[row['id']]=prepare_rpg_styles(core)
        elif kirikiri:
            from kirikiri_ruby import prepare as prepare_ruby, REVISION as RUBY_REVISION
            from kirikiri_translation import row_config
            row_cfg=row_config(cfg,row)
            core,ruby_readings,ruby_plan=prepare_ruby(row['source'],row_cfg)
            if name_index:
                from kirikiri_name_readings import evidence_and_names
                ruby_readings,names=evidence_and_names(row['source'],row_cfg,name_index,
                                                       prepared=(core,ruby_readings,ruby_plan))
            prefix=suffix=''
        elif unity:
            core=row['source']
            prefix=suffix=''
        else:
            core,style_plans[row['id']]=prepare_renpy_source(row['source'])
            prefix=suffix=''
        wrappers[row['id']]=(prefix,suffix)
        source_cores[row['id']]=core
        if rpg or kirikiri or unity:
            if names:
                from kirikiri_tokens import protect_names
                text,tokens,source_spans,name_hints=protect_names(core,row_cfg,names)
                kirikiri_source_spans[row['id']]=source_spans
            else:text,tokens=protect(core,row_cfg,unity_visible_digits=unity)
            markers=['<rpt%03d/>'%i for i in range(len(tokens))]
        else:
            plan=style_plans[row['id']]
            text,tokens=plan['protected_text'],plan['functional_tokens'];markers=plan['wire_markers']
        protected[row['id']] = tokens
        hint_markers={'<rpt%03d/>'%i:marker for i,marker in enumerate(markers)}
        variable_hints={hint_markers[key]:value for key,value in placeholder_hints(tokens,cfg.get('_names',{})).items()}
        inputs.append({'id': str(index), 'kind': row['kind'], 'usage':row.get('usage',row['kind']), 'speaker':row.get('speaker','unknown'), 'text':text,
                       'name_hints':{**variable_hints,**name_hints},
                       'movable_placeholders':[markers[i] for i,t in enumerate(tokens) if movable_token(t)]})
        if cfg.get('engine')=='kirikiri' or unity:inputs[-1]['movable_placeholders']=[]
        if cfg.get('engine')=='kirikiri' and ruby_readings:
            inputs[-1]['ruby_readings']=ruby_readings
        if cfg.get('engine')=='rpgmaker':
            from rpgmaker_input import reading_text
            plan=rpg_plans[row['id']]
            if plan['header']:inputs[-1]['speaker']=plan['header']
            reading=reading_text(core,cfg) if not clean_retry else None
            if reading is not None:inputs[-1]['reading_text']=reading
    if cfg.get('engine')=='rpgmaker' and context.get('source_passage'):
        from rpgmaker_tokens import READING_CONTROLS
        from rpgmaker_input import prepare as prepare_input
        passage=[]
        for item in context['source_passage']:
            if 'text' not in item:passage.append(item);continue
            plan=prepare_input(item['text'],cfg,speaker=item.get('speaker'))
            from rpgmaker_preprocessing import prepare as prepare_rpg_styles
            entry=dict(item,text=READING_CONTROLS.sub('',prepare_rpg_styles(plan['text'])[0]))
            if plan['header']:entry['speaker']=plan['header']
            passage.append(entry)
        context=dict(context,source_passage=passage)
    elif cfg.get('engine')=='kirikiri':
        from kirikiri_translation import prepare_context
        context=prepare_context(context,row_config(cfg,rows[0]))
    elif not unity and context.get('source_passage'):
        context=dict(context,source_passage=[dict(item,text=strip_visual_styles(item['text']))
            if 'text' in item else item for item in context['source_passage']])
    correction=None;plain_correction=False;plain_retries={};japanese_correction=None
    response_keys={row['id']:str(index) for index,row in enumerate(rows)}
    # Generic fallback translates the one complete source utterance as plain
    # text. It must not turn one failed entry into multiple protocol requests.
    generic_retry=not hy_model and max(int(cfg.get('_item_retry_attempt',0)),int(cfg.get('_rpg_retry_attempt',0)))>0
    if (cfg.get('engine')=='rpgmaker' and len(rows)==1 and cfg.get('_rpg_correction_enabled',True)
            and (not generic_retry or cfg.get('_rpg_validated_draft'))):
        from rpgmaker_tokens import readable
        from rpgmaker_protocol import correction_plan
        previous=cfg.get('_rpg_retry_errors',{}).get(rows[0]['id'],'')
        from rpgmaker_preprocessing import prepare as prepare_rpg_styles
        correction_source=prepare_rpg_styles(rows[0]['source'])[0]
        plan=correction_plan(correction_source,previous,cfg)
        if plan is not None and any(re.fullmatch(r'[\u3040-\u30ff\uff66-\uff9f\u3400-\u9fff]',word)
                                    for word in plan[1][0]):
            # A lone glyph has no reliable word or syllable boundary.
            plan=None
        if plan is not None:
            draft,parts=plan
            words,positions=parts;correction=(draft,positions)
            plain_correction=cfg.get('_rpg_correction_attempt',0)==0
            inputs=[{'id':str(i),'text':PLACEHOLDER.sub('',word) if plain_correction else word,'kind':'dialogue' if plain_correction else 'string',
                     'usage':'dialogue' if plain_correction else 'spelling_fragment','speaker':'unknown',
                     'name_hints':{},'movable_placeholders':[]} for i,word in enumerate(words)]
            cfg=dict(cfg,_rpg_correction_mode=True,_rpg_fragment_correction=True,_rpg_plain_correction=plain_correction)
            audit=dict(context.get('_context_audit',{}))
            from rpgmaker_plain_retry import reference as correction_reference
            context=dict(context,_context_audit=audit,
                         spelling_context=PLACEHOLDER.sub('',draft),
                         original_source=readable(correction_reference(source_cores[rows[0]['id']],cfg),cfg),
                         correction_reading={str(i):PLACEHOLDER.sub('',word) for i,word in enumerate(words)})
            if plain_correction:
                # Do not repeat the defective Korean draft as reference text:
                # HY otherwise echoes its residual Japanese unchanged.
                audit['preselection_dropped']=list(audit.get('preselection_dropped',[]))+[
                    {'part':part,'reason':'standalone complete-phrase correction avoids copying the defective draft'}
                    for part in context if part!='_context_audit']
                context={'_context_audit':audit,'original_source':context['original_source']}
            retry_note=''
    # A residual span can be half a Japanese syllable embedded in a Korean
    # word. Its boundaries do not prove a translatable word, so retry the
    # complete source instead of sending individual glyphs or the bad draft.
    # First-pass visual separation supersedes approximate representative/offset retries.
    # Functional codes retain their original opaque anchors on every attempt.
    context,context_policy=context_for(rows,context)
    readings={item['id']:item['ruby_readings'] for item in inputs if item.get('ruby_readings')}
    if readings:context=dict(context,ruby_readings=readings)
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
    person_names={k:v for k,v in cfg.get('_person_names',{}).items() if any(occurrences(r['source'],k) for r in rows)} if name_index is None else {}
    from generic_protocol import INSTRUCTION as json_instruction
    system = (language_instruction(cfg)+'You translate game text into Korean from its original language. Translate EVERY input item fully. '
        +('Return only numbered [ID] translations. IDs must be copied exactly. Never merge or omit items. ' if hy_model else json_instruction+' ')+
        'Do not add facts, quotation marks, commentary or English alternatives. '
        'For interface strings use concise conventional Korean UI labels. '
        +('Preserve the supplied label meaning.\n' if labels_only else
          'For dialogue use the style below. Preserve numbers and sentence meaning.\n'+cfg.get('style','Preserve the original tone and meaning.'))+
        '\nGlossary: '+json.dumps(relevant_glossary, ensure_ascii=False)+
        '\nUse these phonetic spellings only when the name refers to a person, not a country/common noun: '+json.dumps(person_names,ensure_ascii=False))
    if cfg.get('_name_preparation'):
        system += ('\nThese inputs are character name fields. Render proper personal names phonetically in Korean, '
                   'not as country names or common nouns. Keep digits and state qualifiers. '
                   'Explicit titles or relationship labels retain their meaning. Return names only, never dialogue.')
    if labels_only:
        system += ('\nThese items are speaker labels or interface controls, not dialogue. '
                   'Translate only the supplied name, role or label as a name or noun phrase. '
                   'Never add speech, speaker prefixes, events, quotations or explanations. '
                   'Term source/target pairs are spellings only.')
    if cfg.get('engine')=='rpgmaker' and normalize(cfg.get('source_language'))=='japanese':
        system+='\nTranslate visible ideographs in headings and choice captions into Korean too. Prefer transliterated personal names, but original-script and Latin names are acceptable. Translate all non-name phrases into Korean.'
    if rpg and any(item.get('reading_text') for item in inputs):
        system+='\nreading_text is reference with visual/timing codes removed. Translate only text and preserve its placeholders.'
    if context.get('translation_guidance') and not labels_only:
        system += ('\ntranslation_guidance contains reference-only global style, current-scene facts and applicable terms. '
                   'Resolve subjects, speakers and relationships from the source and current scene before general background. '
                   'Do not assume every subject is the global protagonist. The source overrides conflicting notes. '
                   'Apply terminology only in its stated sense; retain global narration style without imposing it on quoted dialogue. '
                   'Use it to interpret the input, never translate the notes or add their facts to the output. '
                   'Keep each input item separate even when the background explains a later event.')
    if any(protected[row['id']] for row in rows if row['id'] not in plain_retries):
        system+='\nPreserve each input placeholder exactly once. IDs in movable_placeholders represent named variables and may move for Korean word order. Keep all other placeholders in their original relative order. Never invent a placeholder or copy one from another item.'
    if kirikiri_source_spans:
        system+='\nname_hints gives the Korean reading of a placeholder for context and particles. Preserve the placeholder itself exactly; do not write its reading separately.'
    if plain_retries:
        system+='\nKeep these readable name/number values spelled exactly as supplied, allowing Korean word order and particles: '+json.dumps(cfg['_rpg_plain_values'],ensure_ascii=False)
    if correction and not plain_correction:
        system+=('\nThese inputs are incomplete Korean drafts. Correct only the remaining Japanese fragments into Korean. '
                  'Keep the existing Korean meaning and every supplied placeholder. '+
                  ('Return only numbered [ID] translations.' if hy_model else json_instruction))
    if japanese_correction:
        system+='\nTranslate only the supplied Japanese fragments into Korean using the reference sentence. Do not rewrite or output the reference sentence.'
    if clean_retry:
        system=(language_instruction(cfg)+'Translate only the items naturally into Korean, preserving meaning and tone. '
                +('Return only numbered [ID] translations with exactly the original IDs. ' if hy_model else json_instruction+' ')+
                'Background context is reference only. Do not add explanations or facts. '
                'Use the supplied spellings only for actual personal names: '+json.dumps(person_names,ensure_ascii=False)+
                '\nGlossary: '+json.dumps(relevant_glossary,ensure_ascii=False))
        if labels_only:system+='\nThese are names or UI labels. Return only the name, role or label, never dialogue.'
        elif cfg.get('style'):system+='\nStyle: '+cfg['style']
        if any(protected[row['id']] for row in rows):
            system+='\nPreserve every input placeholder exactly once; only movable variables may change relative position.'
    if output_policy:
        from rpgmaker_protocol import japanese_instruction
        system+='\n'+japanese_instruction(cfg)
        system+='\n원문의 독특한 말버릇도 인물의 말투에 맞게 자연스러운 한국어로 옮기세요.'
    if japanese_policy and not rpg:
        from hy_backend import japanese_general_instruction
        system+='\n'+japanese_general_instruction(cfg)
    if readings:
        system+='\nruby_readings supplies the original words and their readings, not text to append. Use it to interpret the source; do not output ruby annotation codes.'
    from kirikiri_translation import ui_instruction
    ui_note=ui_instruction(rows,cfg)
    if ui_note:system+='\n'+ui_note
    from numbered_protocol import response_instruction
    if hy_model:system+='\n'+response_instruction()
    body = {'model':cfg['model'],'stream':False,'think':False,'keep_alive':'10m',
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
        simple_reply=(not japanese_correction and len(active_inputs)==1 and bool(output_minimum or int(cfg.get('_item_retry_attempt',0)) or int(cfg.get('_rpg_retry_attempt',0))))
        single_plain=(simple_reply and (not hy_model or max(int(cfg.get('_item_retry_attempt',0)),int(cfg.get('_rpg_retry_attempt',0)))>=2))
        def build(selected):
            result=(hy_body(active_inputs,dict(selected,person_name_spellings=person_names),active_cfg,relevant_glossary,retry_note)
                    if is_hy(cfg) else dict(body))
            result.pop('format',None)
            result['options']=dict(result['options'])
            if int(cfg.get('_item_retry_attempt',0)) or int(cfg.get('_rpg_retry_attempt',0)):
                # Reserve once for the new single-item request shape. Further
                # retries do not keep multiplying the output allowance.
                result['options']['num_predict']=(result['options']['num_predict']*3+1)//2
            if output_minimum:result['options']['num_predict']=max(output_minimum,result['options']['num_predict'])
            if simple_reply:
                prompt=('Translate the following '+normalize(cfg.get('source_language'))+' text into Korean. '
                        'Preserve its meaning, tone, numbers and supplied placeholders. '
                        'Translate short sound expressions too. Do not add explanations or examples. '
                        +('Return only the translation text, without an item header.\n' if single_plain else
                          'Return only [ID] followed by the translation, using the exact input ID.\n'))
                if person_names:prompt+='Personal name spellings: '+json.dumps(person_names,ensure_ascii=False)+'\n'
                if relevant_glossary:prompt+='Applicable terms: '+json.dumps(relevant_glossary,ensure_ascii=False)+'\n'
                if kirikiri_source_spans and active_inputs[0].get('name_hints'):
                    prompt+='Name placeholder readings for context and particles (preserve each placeholder itself exactly; do not write the reading separately): '+json.dumps(active_inputs[0]['name_hints'],ensure_ascii=False)+'\n'
                if japanese_policy:
                    from hy_backend import japanese_general_instruction
                    prompt+=japanese_general_instruction(dict(cfg,_japanese_retry_attempt=max(
                        int(cfg.get('_japanese_retry_attempt',0)),int(cfg.get('_item_retry_attempt',0)),int(cfg.get('_rpg_retry_attempt',0)))))+'\n'
                    if selected.get('ruby_readings'):
                        prompt+='Original word readings (reference only, do not output ruby codes): '+json.dumps(selected['ruby_readings'],ensure_ascii=False)+'\n'
                if not hy_model:
                    if labels_only:prompt+='This is a name or interface label. Return only the translated name or label.\n'
                    elif cfg.get('style'):prompt+='Style: '+cfg['style']+'\n'
                    if max(int(cfg.get('_item_retry_attempt',0)),int(cfg.get('_rpg_retry_attempt',0)))>=2:
                        prompt+='Make a fresh complete Korean rendering of the source. Do not copy an untranslated answer.\n'
                if ui_note:prompt+=ui_note+'\n'
                prompt+='\n[Source Text]\n'+(active_inputs[0]['text'] if single_plain else
                    '\n'.join('['+item['id']+'] '+item['text'] for item in active_inputs))
                result['messages']=[{'role':'user','content':prompt}]
                return result
            if is_hy(cfg):return result
            from generic_protocol import schema
            result['format']=schema(active_inputs)
            result['messages']=[body['messages'][0],{'role':'user','content':json.dumps({
                'context_do_not_translate':{k:v for k,v in selected.items() if k not in ('current_state','_context_audit')},
                **({'current_state_reference_only':selected['current_state']} if selected.get('current_state') else {}),
                'items':active_inputs,'retry_note':retry_note},ensure_ascii=False)}]
            return result
        from request_budget import fit, InputBudgetError
        try:fitted,budget=fit(selected_context,active_cfg,build)
        except InputBudgetError:raise  # Replan before any model call or error report.
        except Exception as exc:
            if getattr(exc,'_rpt_transport',False):
                from request_diagnostics import classify_request_failure
                category=classify_request_failure(exc)
                exc.request_id=request_id
                exc.error_code=category['code']
                exc.request_diagnostics={'phase':'token_count','failure':category,
                    'endpoint':getattr(exc,'transport_endpoint',None),
                    'route':getattr(exc,'transport_route',None),
                    'server_log':cfg.get('_runtime_log'),'server_pid':cfg.get('_runtime_pid'),
                    'num_ctx':cfg.get('num_ctx'),'response_available':False}
                runtime_event(cfg,'request_preflight_failed',request_id=request_id,
                    error_type=type(exc).__name__,http_status=getattr(exc,'code',None),
                    endpoint=getattr(exc,'transport_endpoint',None),route=getattr(exc,'transport_route',None))
                try:
                    trace_event(cfg,request_id,'request_error',error=str(exc),error_type=type(exc).__name__,
                        http=getattr(exc,'http_details',None),request_diagnostics=exc.request_diagnostics)
                except OSError as log_error:
                    exc.request_diagnostics['trace_error_type']=type(log_error).__name__
            report(exc,cfg,rows,request_id,handled=True);raise
        from request_diagnostics import request_metadata,response_metadata,classify_request_failure
        profile=request_metadata(fitted,protocol='hy' if is_hy(cfg) else 'generic',
                                 input_count=len(active_inputs))
        profile['response_contract']='hy_numbered' if hy_model and not single_plain else 'single_plain' if single_plain else 'json_object'
        rpg_details={}
        if rpg:
            from rpgmaker_sources import source_binding_trace
            rpg_details=dict(source_bindings=source_binding_trace(rows,cfg),
                rpg_input_mode='clean_source' if clean_retry else 'normal',
                rpg_output_policy='japanese_v1' if output_policy else 'legacy',
                rpg_retry_kind=cfg.get('_rpg_retry_kind') if output_policy else None,
                rpg_retry_attempt=cfg.get('_rpg_retry_attempt',0) if output_policy else None,
                rpg_input_plans=[dict(id=row['id'],speaker_line_separated=bool(rpg_plans[row['id']]['header']),
                                      source_body_characters=len(source_cores[row['id']])) for row in rows])
        trace_event(cfg,request_id,'request',
            items=[{k:r.get(k) for k in ('id','file','block','source_file','source_line')} for r in rows],
            request=fitted,request_profile=profile,context_budget=budget,context_policy=context_policy,
            item_purposes=[{'id':r['id'],'usage':r.get('usage',r['kind']),'evidence':r.get('usage_evidence')} for r in rows],
            **({'input_format':INPUT_FORMAT} if unity else {}),
            **rpg_details,
            **({'repair_mode':'rpg_japanese_plain_spans' if plain_correction else 'rpg_japanese_marked_spans','model_items':len(active_inputs),
                'correction_spans':[{'start':a,'end':b,'item':k} for a,b,k in correction[1]]} if correction else {}))
        if plain_retries:
            trace_event(cfg,request_id,'rpg_plain_retry',plans=plain_retries,
                        policy='readable_representatives; local_clause_word_restoration')
        from request_budget import should_report_adjustment
        if should_report_adjustment(budget):
            print('Context budget adjusted: request '+request_id+'; see data/translation-requests.jsonl',flush=True)
        start=time.monotonic()
        runtime_event(cfg,'request_started',request_id=request_id,item_ids=[r['id'] for r in rows],
                      monotonic_ns=time.monotonic_ns(),prompt_tokens_estimated=budget.get('prompt_content_tokens'))
        try:
            response=(request(cfg['endpoint'],'/api/chat',fitted,timeout=1800)
                      if cfg.get('_cuda_diagnostic_mode')=='blocking' else request(cfg['endpoint'],'/api/chat',fitted))
        except Exception as exc:
            exc.request_id=request_id
            category=classify_request_failure(exc)
            elapsed=time.monotonic()-start
            exc.error_code=category['code']
            exc.request_diagnostics={'request':profile,'failure':category,
                'response_available':False,'partial_response_available':False,
                'server_log':cfg.get('_runtime_log'),'model_metadata':cfg.get('_runtime_model_metadata')}
            runtime_event(cfg,'request_finished',request_id=request_id,
                          status='generation_error' if category['category']=='generation' else 'transport_error',
                          duration_ms=round(elapsed*1000,3),error_code=category['code'],
                          error_type=type(exc).__name__,http_status=getattr(exc,'code',None))
            snapshot=cfg.get('_recovery_generation_failed')
            if category['category']=='generation' and snapshot:
                try:
                    evidence=snapshot(request_id=request_id,details=dict(exc.request_diagnostics,
                        http=getattr(exc,'http_details',None),
                        request_trace=str(Path(cfg['_trace_dir'])/'translation-requests.jsonl') if cfg.get('_trace_dir') else None))
                    if evidence:exc.request_diagnostics['generation_evidence']=evidence
                except Exception as log_error:
                    exc.request_diagnostics['snapshot_error_type']=type(log_error).__name__
            import traceback
            trace_event(cfg,request_id,'request_error',error=str(exc),error_type=type(exc).__name__,
                traceback=traceback.format_exc(),http=getattr(exc,'http_details',None),
                request_diagnostics=exc.request_diagnostics)
            if category['code']=='token_repeat_limit' and not category['restart_runtime']:
                # The runner rejected this generation; it did not lose the
                # connection. Isolate/retry these items within the usual budget.
                errors={item['id']:dict(error_code='token_repeat_limit',
                    error='Model generation stopped at the token repeat limit',
                    http=getattr(exc,'http_details',None),request_diagnostics=exc.request_diagnostics)
                    for item in active_inputs}
                return dict(values={},errors=errors,truncated=False,parse_error=None,hy=is_hy(cfg),
                    limit=fitted['options']['num_predict'],request_id=request_id,budget=budget,
                    seconds=elapsed,tokens=0,eval_seconds=0)
            report(exc,cfg,rows,request_id,handled=True);raise
        elapsed=time.monotonic()-start
        runtime_event(cfg,'request_finished',request_id=request_id,status='http_success',duration_ms=round(elapsed*1000,3))
        recovered=cfg.get('_recovery_request_succeeded')
        if recovered:
            try:recovered(request_id=request_id,metrics={k:response.get(k) for k in ('eval_count','prompt_eval_count','done_reason')})
            except Exception as exc:
                import sys
                print('Runtime recovery status unavailable: '+str(exc),file=sys.stderr,flush=True)
        actual=response.get('prompt_eval_count')
        response_profile=response_metadata(response)
        trace_event(cfg,request_id,'response',response=response,response_profile=response_profile,runtime=runtime_status(cfg),
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
        repetition=response_profile['repetition']
        # A long non-language cycle cannot become a translation by granting it
        # more output tokens. Return it to the existing item retry budget.
        content=response['message']['content']
        numbers=re.findall(r'\d+',content)
        source_numbers=sum(len(re.findall(r'\d+',item['text'])) for item in active_inputs)
        digit_runaway=(len(numbers)>=max(16,source_numbers*3+1)
                       and sum(map(len,numbers))>max(1,len(re.sub(r'\s','',content)))*.55)
        exact_cycle=(sum(response_profile['scripts'].values())==0
                     and repetition['analysed_characters']>=96 and repetition['suffix_repeat_count']>=24
                     and repetition['suffix_unit_characters']*repetition['suffix_repeat_count']>=repetition['analysed_characters']*.6)
        if truncated and any(letters(item['text']) for item in active_inputs) and (digit_runaway or exact_cycle):
            errors={item['id']:dict(error_code='generation_repetition',
                error='Model output generated a non-translation sequence instead of completing the translation',
                request_id=request_id,response_profile=response_profile) for item in active_inputs}
            trace_event(cfg,request_id,'generation_repetition',items=[r['id'] for r in rows],
                            response_profile=response_profile,output_limit_growth_skipped=True,
                            numeric_runaway=digit_runaway,output_number_terms=len(numbers),source_number_terms=source_numbers)
            return dict(values={},errors=errors,truncated=False,parse_error=None,hy=is_hy(cfg),
                limit=fitted['options']['num_predict'],request_id=request_id,budget=budget,
                seconds=elapsed,tokens=response.get('eval_count'),eval_seconds=response.get('eval_duration',0)/1e9)
        parse_error=None
        try:
            expected_inputs={item['id']:item['text'] for item in active_inputs}
            if hy_model or single_plain:
                from numbered_protocol import parse as parse_numbered
                values=parse_numbered(content,expected_inputs,
                                     allow_headerless=simple_reply,allow_multiline=single_plain,truncated=truncated)
                if unity and hy_model and not values and response.get('done_reason') == 'stop':
                    from unity_output import parse_single_hy
                    values=parse_single_hy(content,expected_inputs)
            else:
                from generic_protocol import parse as parse_json
                values=parse_json(content,expected_inputs,truncated=truncated)
        except (ValueError,KeyError,TypeError) as exc:
            values={}
            if not truncated:parse_error=str(exc)
            if cfg.get('_trace_dir'):
                with TRACE_LOCK:save_json(Path(cfg['_trace_dir'])/'last-invalid-model-response.json',response)
        return dict(values=values,truncated=truncated,parse_error=parse_error,hy=is_hy(cfg),truncation_checked=True,
                    limit=fitted['options']['num_predict'],request_id=request_id,budget=budget,
                    seconds=elapsed,tokens=response.get('eval_count'),eval_seconds=response.get('eval_duration',0)/1e9)

    from translation_segments import execute
    from request_budget import BatchNeedsSplit
    parents={}
    for row in rows:
        plan=plain_retries.get(row['id'],{})
        keys=([item['id'] for item in inputs] if correction or japanese_correction else
              [part['model_key'] for part in plan['segments'] if 'model_key' in part] if 'segments' in plan else
              [response_keys[row['id']]])
        parents.update({key:row['id'] for key in keys})
    try:
        parsed,model_errors,summary=execute(inputs,dict(cfg,_outer_output_retries=True),send,parents=parents,
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
        if not correction and not japanese_correction and not (row['id'] in plain_retries and 'segments' in plain_retries[row['id']]) and key not in parsed:
            failures.append({'row':row,'error':f'Missing response item [{key}]; received IDs: '+', '.join(sorted(parsed)),
                             'error_code':'missing_response_item','expected_id':key,'received_ids':list(parsed)})
            continue
        correction_failure=None;text=None;candidate=None;residue=None;value=None
        try:
            style_changes={}
            prefix,suffix=wrappers[row['id']]
            if japanese_correction:
                value=japanese_correction[0]
                for a,b,fragment_key in reversed(japanese_correction[1]):
                    value=value[:a]+parsed[fragment_key]+value[b:]
                text=value
            elif correction:
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
            if japanese_correction:
                pass  # The draft already has native controls; only visible spans changed.
            elif row['id'] in plain_retries:
                from rpgmaker_plain_retry import restore as restore_plain
                restored,placements=restore_plain(plain_retries[row['id']],value)
                text=prefix+restored+suffix
                trace_event(cfg,request_id,'rpg_plain_restoration',id=row['id'],**placements)
            elif cfg.get('engine')=='rpgmaker':
                text=restore_output(value,protected[row['id']])
                from rpgmaker_preprocessing import restore as restore_rpg_styles
                if correction and rpg_plans[row['id']]['header']:
                    header_plan=rpg_plans[row['id']]
                    header=header_plan['prefix']+header_plan['header']+header_plan['suffix']+header_plan['separator']
                    if not text.startswith(header):raise ValueError('Correction lost the proven speaker header')
                    body_text,style_changes=restore_rpg_styles(text[len(header):],rpg_styles[row['id']])
                    text=header+body_text
                else:text,style_changes=restore_rpg_styles(text,rpg_styles[row['id']])
                from rpgmaker_input import restore as restore_input
                text=prefix+(text if correction else restore_input(text,rpg_plans[row['id']]))+suffix
            elif cfg.get('engine')=='kirikiri':
                from kirikiri_tokens import restore as restore_kirikiri
                from token_recovery import _normalize_tool_markers, _marker_pattern
                # Source-owned marker-like text must never become a missing
                # control token. Otherwise reuse the shared delimiter repair.
                if not _marker_pattern().search(row['source']):
                    normalized,_=_normalize_tool_markers(value)
                    if normalized!=value:
                        trace_event(cfg,request_id,'kirikiri_protocol_repair',
                                    repair='marker_delimiters',ids=[row['id']])
                    value=normalized
                if row.get('dialect','kag') == 'kagex_psb':
                    from kirikiri_runtime import _kagex_escape
                    escaped=_kagex_escape(value)
                    if escaped != value:
                        trace_event(cfg,request_id,'kirikiri_literal_escape',ids=[row['id']])
                    value=escaped
                if japanese_policy and row.get('dialect')=='kagex_psb':
                    from kirikiri_layout import recover_linebreak_markers
                    core=source_cores[row['id']]
                    source_spans=kirikiri_source_spans.get(row['id'])
                    source_tokens=protected[row['id']] if source_spans is None else [core[a:b] for a,b in source_spans]
                    recovered=recover_linebreak_markers(core,value,source_tokens,source_spans=source_spans)
                    if recovered is not None:
                        value,inserted=recovered
                        if inserted:
                            trace_event(cfg,request_id,'kirikiri_linebreak_restored',id=row['id'],indices=inserted,model_calls=0)
                text=restore_kirikiri(value,protected[row['id']])
                if japanese_policy:
                    from kirikiri_ruby import strip_output_ruby
                    text=strip_output_ruby(row['source'],text,row_config(cfg,row))
            elif unity:
                from unity_text_formats import restore_outer_parentheses
                text=restore_output(value,protected[row['id']])
                if row.get('usage') == 'choice':
                    text=restore_outer_parentheses(row['source'],text)
            else:
                restored,style_changes=restore_prepared_renpy_output(source_cores[row['id']],value,style_plans[row['id']])
                text=prefix+restored+suffix
            if cfg.get('engine')=='rpgmaker':
                if row['id'] in plain_retries:
                    from rpgmaker_input import restore as restore_input
                    text=prefix+restore_input(restored,rpg_plans[row['id']])+suffix
                from rpgmaker_layout import repair as repair_layout
                text,changes=repair_layout(row['source'],text)
                if changes:trace_event(cfg,request_id,'rpg_layout_repair',id=row['id'],placements=changes)
            validation_cfg=dict(cfg,_usage=row.get('usage',row['kind'])) if japanese_policy else cfg
            if cfg.get('engine')=='kirikiri':
                from kirikiri_translation import row_config
                validation_cfg=row_config(validation_cfg,row)
            if unity:
                validation_cfg=dict(validation_cfg,_unity_text_kind=row.get('unity_format'))
            if '┻' in text and text != row['source']:
                from replacements import repair_heart_symbol
                text=repair_heart_symbol(text,validation_cfg)
            if japanese_policy:
                from japanese_output_policy import prepare_output
                cleaned,residue=prepare_output(row['source'],text,validation_cfg)
                if cleaned!=text:
                    trace_event(cfg,request_id,'rpg_small_tsu_removed' if rpg else 'japanese_small_tsu_removed',id=row['id'],
                                removed_characters=len(text)-len(cleaned))
                    text=cleaned
                    residue=dict(residue,kind='clean',japanese_count=0,spans=[])
            errors = validate_text(row['source'],text,validation_cfg,check_link_content=True,language_details=residue)
            if not errors:
                from label_policy import automatic_english_errors
                errors+=automatic_english_errors(row,text,cfg)
            errors += expansion_errors(row,text,cfg) if output_policy or unity else expansion_errors(row,text)
            if (japanese_policy and errors and residue['kind'] in ('minor','mixed') and
                    set(errors).issubset({'untranslated Japanese text','no Korean translation'})):
                # This text has already passed the structural checks above.
                # It remains incomplete; do not throw the Korean draft away.
                candidate=dict(text=text,residue_kind=residue['kind'],
                               japanese_count=residue['japanese_count'],hangul_count=residue['hangul_count'])
            if correction and errors:
                correction_failure=('language' if set(errors).issubset({'untranslated Japanese text','no Korean translation'}) else 'structure')
            if errors: raise ValueError(f'{row["id"]}: {errors}; output={value!r}')
            if style_changes:
                trace_event(cfg,request_id,'rpg_visual_restoration' if rpg else 'renpy_style_repair',id=row['id'],**style_changes)
        except (ValueError,KeyError,TypeError) as exc:
            error=str(exc)
            if correction:
                error=error.split('; output=',1)[0]+'; untranslated Japanese text; output='+repr(correction[0])
            elif rpg and row['id'] in plain_retries and isinstance(text,str):
                # The readable model response no longer contains display codes.
                # Keep the assembled text once restoration has succeeded.
                error=error.split('; output=',1)[0]+'; output='+repr(text)
            elif rpg and isinstance(text,str):
                # Saved repair outputs keep the original display structure even
                # though the model saw only the body. Functional tokens stay raw.
                from rpgmaker_input import restore as restore_input
                error=error.split('; output=',1)[0]+'; output='+repr(text)
            elif japanese_policy and isinstance(text,str):
                error=error.split('; output=',1)[0]+'; output='+repr(text)
            elif japanese_policy and cfg.get('engine')=='kirikiri' and isinstance(value,str):
                error=error.split('; output=',1)[0]+'; output='+repr(value)
            if row['id'] in plain_retries:
                error='RPG plain retry failed: '+error
                trace_event(cfg,request_id,'rpg_plain_restoration_failed',id=row['id'],error=error)
            failure={'row':row,'error':error}
            if japanese_policy and residue is not None:
                failure['residue_kind']=residue['kind']
                if candidate is not None:failure['candidate']=candidate
            if correction:failure['correction_failure']=correction_failure or 'structure'
            failure['retry_attempt']=summary['attempts'].get(row['id'],cfg.get('_item_retry_attempt',0))
            if kirikiri:
                if text is None and row['id'] in kirikiri_source_spans:
                    from kirikiri_tokens import NAME_ANCHOR_FORMAT
                    failure['input_format']=NAME_ANCHOR_FORMAT
                else:
                    failure['input_format']='kirikiri_reading_v2' if japanese_policy else 'kirikiri_protected_v1'
            elif unity:failure['input_format']=INPUT_FORMAT
            elif cfg.get('engine')!='rpgmaker':failure['input_format']='renpy_visual_stripped_v1'
            elif any(wrappers[row['id']]):failure['error']='Wrapper-stripped: '+error
            failures.append(failure);continue
        output.append({'id':row['id'],'source':row['source'],'text':text,
                       'model':cfg['model'],'fingerprint':fingerprint(cfg),
                       'context_digest':budget['context_sha256'],'context_files':budget.get('files',{}),
                       'context_scope':budget.get('scope',''),'request_id':request_id,
                        **({'_unity_numeric_review':True} if unity and quantity_mismatch(row['source'],text) else {}),
                        **({'input_format':'rpg_visual_stripped_v1'} if rpg else {}),
                         **({'input_format':'kirikiri_reading_v2' if japanese_policy else 'kirikiri_protected_v1','dialect':row.get('dialect','kag'),'usage':row.get('usage')} if cfg.get('engine')=='kirikiri' else {}),
                         **({'_japanese_ruby_revision':RUBY_REVISION} if japanese_policy and cfg.get('engine')=='kirikiri' and source_cores[row['id']]!=row['source'] else {}),
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

def translate_subset(project,cfg,item_ids,*,force=False):
    """Translate an explicit catalog selection without queuing other entries."""
    ids=list(dict.fromkeys(item_ids))
    available={row['id'] for row in read_catalog(project)}
    if not set(ids).issubset(available):raise ValueError('번역할 항목이 카탈로그에 없습니다.')
    if not ids:return None
    return translate(project,dict(cfg,_translation_selection=ids,_translation_force_selection=bool(force)))


def translate(project, cfg, sample=False):
    # Offline resumes and rebuilds never start a server or load a model.
    from job_events import translation_tracker
    cfg=dict(cfg);translation_tracker(cfg)
    rows=read_catalog(project); known=cache(project,cfg)
    selection=set(cfg['_translation_selection']) if '_translation_selection' in cfg else None
    if selection is not None and cfg.get('_translation_force_selection'):
        for uid in selection:known.pop(uid,None)
    from japanese_output_policy import enabled as japanese_enabled
    if japanese_enabled(cfg):
        from japanese_resume import pending,exempt
        by_id={row['id']:row for row in rows}
        previous={uid:(item.get('error') or 'untranslated Japanese text')
                  for uid,item in known.items() if uid in by_id and (selection is None or uid in selection)
                  and pending(item) and not exempt(by_id[uid],item)}
        if previous:
            drafts={uid:dict(known[uid]) for uid in previous
                    if known[uid].get('status')!='source_fallback'}
            cfg=dict(cfg,_japanese_previous_failures=previous,_japanese_previous_drafts=drafts)
            if cfg.get('engine')=='rpgmaker':
                cfg.update(_rpg_previous_failures=previous,_rpg_previous_drafts=drafts)
            for uid in previous:known.pop(uid,None)
            print(f'Japanese resume: {len(previous)} unfinished entries queued; saved translations retained',flush=True)
    elif cfg.get('engine')=='rpgmaker':
        previous={uid:(item.get('error') or 'untranslated Japanese text') for uid,item in known.items()
                  if (selection is None or uid in selection) and
                  (item.get('status')=='source_fallback' or item.get('_rpg_residual_language'))}
        if previous:
            from rpgmaker_output_policy import enabled as policy_enabled
            if policy_enabled(cfg):
                cfg['_rpg_previous_drafts']={uid:dict(known[uid]) for uid in previous
                    if known[uid].get('status')=='partial_translation'}
            cfg=dict(cfg,_rpg_previous_failures=previous)
            for uid in previous:known.pop(uid,None)
            print(f'RPG Maker resume: retrying {len(previous)} failed or incomplete entries; other translations retained',flush=True)
    elif cfg.get('engine')=='kirikiri':
        # A previous fallback is unfinished work. Keep the append-only record,
        # but queue this item again instead of silently treating it as complete.
        known={uid:item for uid,item in known.items() if item.get('status')!='source_fallback'
               or selection is not None and uid not in selection}
    if cfg.get('engine') not in ('rpgmaker','kirikiri','unity'):
        from ui_policy import apply_policy
        known=apply_policy(project,rows,known,preserve_choices=True)
    if all(row['id'] in known for row in rows if selection is None or row['id'] in selection):
        return _translate(project,cfg,sample,rows,known,all_rows=rows)
    from runtime_recovery import run
    return run(project,cfg,lambda active:_translate(project,active,sample,rows,known,all_rows=rows),
               lambda:len(known))


def _translate(project, cfg, sample=False, rows=None, known=None, *, all_rows=None):
    from term_memory import TermMemory
    from name_hints import ensure_metadata
    from job_events import translation_tracker
    tracker=translation_tracker(cfg)
    cfg=dict(cfg,_trace_dir=str(project/'data'),_trace_phase='translation')
    from diagnostics import stage
    stage('translation',project,cfg)
    from name_translation import mapping
    cfg['_person_names']=mapping(project) if cfg.get('engine') not in ('rpgmaker','kirikiri','unity') else {}
    if rows is None:
        rows=read_catalog(project)
        if all_rows is None:all_rows=rows
    if known is None:known=cache(project,cfg)
    if all_rows is None:
        all_rows=read_catalog(project) if cfg.get('engine') in ('rpgmaker','kirikiri') else rows
    selection=set(cfg['_translation_selection']) if '_translation_selection' in cfg else None
    if cfg.get('engine')=='rpgmaker':
        from name_review import configuration
        cfg=configuration(project,cfg,all_rows)
        from rpgmaker_names import validation_config
        cfg=validation_config(cfg,all_rows,known)
        from rpgmaker_input import configuration as input_configuration
        cfg=input_configuration(cfg,all_rows,known)
        from rpgmaker_plain_retry import representatives
        cfg['_rpg_representatives']=representatives(project,cfg,all_rows,known)
    elif cfg.get('engine')=='kirikiri':
        from name_review import configuration
        has_pending=any(row['id'] not in known and (selection is None or row['id'] in selection) for row in rows)
        cfg=configuration(project,cfg,all_rows,prepare_names=has_pending)
    actor_ids={r['id'] for r in rows if Path(r.get('file','')).name=='Actors.json'} if cfg.get('engine')=='rpgmaker' else set()
    cfg['_names']=ensure_metadata(project,rows)['names'] if cfg.get('engine') not in ('rpgmaker','kirikiri','unity') else {}
    memory=TermMemory(rows,known)
    from source_context import SourceContext
    if sample:
        dialogue=[r for r in rows if r['kind']=='dialogue']
        # Beginning, later scenes, hyperlinks and UI all represented.
        chosen=dialogue[:12]+dialogue[len(dialogue)//2:len(dialogue)//2+8]+[r for r in dialogue if '{a=' in r['source']][:4]+[r for r in rows if r['kind']=='string'][:12]
        ids={r['id'] for r in chosen}; rows=[r for r in rows if r['id'] in ids]
    pending=[r for r in rows if r['id'] not in known and (selection is None or r['id'] in selection)]
    units={row['id']:max(1,source_units(row['source'])) for row in pending} if tracker.units is None else None
    tracker.begin(rows,pending,known,units)
    pending_files={r.get('file','') for r in pending}
    context_cfg=cfg
    dispatch_for_context=cfg.get('_gpu_dispatch')
    if dispatch_for_context and any(state['active'].get('context_txt_enabled',cfg.get('context_txt_enabled',True))
                                    for state in dispatch_for_context.states.values()):
        context_cfg=dict(cfg,context_txt_enabled=True)
    context_index=SourceContext([r for r in all_rows if r.get('file','') in pending_files],project,context_cfg) if pending else None
    size=cfg.get('batch_size',12); completed=0
    print(f'Translating {len(pending)} remaining entries with {cfg["model"]}',flush=True)
    queue=[]
    for row in pending:
        if not queue or len(queue[-1])>=size or not context_index.same_scope(queue[-1][-1],row) or sum(source_units(r['source']) for r in queue[-1])+source_units(row['source'])>cfg.get('max_batch_source_words',240) or sum(len(r['source'].encode('utf-8')) for r in queue[-1])+len(row['source'].encode('utf-8'))>min(5000,cfg.get('num_ctx',8192)//2):
            queue.append([])
        queue[-1].append(row)
    from japanese_output_policy import enabled as japanese_enabled
    japanese_policy=japanese_enabled(cfg)
    previous=(cfg.get('_japanese_previous_failures',cfg.get('_rpg_previous_failures',{})) if japanese_policy else
              cfg.get('_rpg_previous_failures',{}) if cfg.get('engine')=='rpgmaker' else {})
    from hy_backend import is_hy
    rpg=cfg.get('engine')=='rpgmaker'
    from rpgmaker_output_policy import enabled as policy_enabled,classify as classify_output
    output_policy=policy_enabled(cfg)
    partials={}
    rows_by_id={row['id']:row for row in rows} if japanese_policy else {}
    if japanese_policy and not rpg:
        from japanese_output_policy import classify as classify_output
    draft_entries=cfg.get('_japanese_previous_drafts',cfg.get('_rpg_previous_drafts',{}))
    for uid,item in draft_entries.items() if japanese_policy else ():
        row=rows_by_id.get(uid)
        if row is not None and item.get('source')==row['source']:
            check_cfg=dict(cfg,_usage=row.get('usage',row['kind']))
            if cfg.get('engine')=='kirikiri':
                from kirikiri_translation import row_config
                check_cfg=row_config(check_cfg,row)
            details=classify_output(row['source'],item['text'],check_cfg)
            structure=validate_text(row['source'],item['text'],check_cfg,check_link_content=True,
                                    language_details=dict(details,kind='clean',japanese_count=0)) if not rpg else []
            if (details['kind'] in ('minor','mixed') or item.get('_japanese_ruby_pending')) and not structure:
                partials[uid]=dict(text=item['text'],residue_kind=details['kind'],
                                  japanese_count=details['japanese_count'],hangul_count=details['hangul_count'])
                if item.get('_japanese_ruby_pending'):
                    partials[uid].update(residue_kind='mixed',ruby_pending=True)
    latest_kinds={}
    def retry_kind(row):
        uid=row['id']
        if uid in latest_kinds:return latest_kinds[uid]
        if uid in partials:return partials[uid]['residue_kind']
        from rpgmaker_protocol import previous_output
        value=previous_output(previous.get(uid,''))
        return classify_output(row['source'],value,cfg)['kind'] if value is not None else 'no_korean'
    def language_failure(error):
        return any(code in error for code in ('untranslated Japanese text','no Korean translation'))
    initial=[]
    for batch in queue:
        if ((rpg or japanese_policy) and any(language_failure(previous.get(r['id'],'')) for r in batch)
                or japanese_policy and any(r['id'] in draft_entries for r in batch)):
            for row in batch:
                repair=language_failure(previous.get(row['id'],''))
                initial.append(([row],1 if row['id'] in previous else 0,repair,None))
        else:initial.append((batch,1 if batch and all(r['id'] in previous for r in batch) else 0,False,None))
    queue=deque(initial)
    failures=[dict(row=r,error=known[r['id']].get('error','Previous translation failed'),
                   **{k:known[r['id']][k] for k in ('input_format','error_code') if k in known[r['id']]})
              for r in rows if known.get(r['id'],{}).get('status')=='source_fallback']
    dispatch=cfg.get('_gpu_dispatch')
    workers=max(1,dispatch.capacity if dispatch else cfg.get('parallel',2))
    print(f'Translation workers: {workers}; context window: {cfg.get("num_ctx",8192)}',flush=True)
    if dispatch:
        for state in dispatch.states.values():
            active=state['active']
            print(f'GPU worker: {active["device_id"]}; {active["device_backend"]}; '
                  f'slots={active["worker_slots"]}; context={active["num_ctx"]}; model={active.get("model",cfg["model"])}',flush=True)
    pool=ThreadPoolExecutor(max_workers=workers)
    running={};leases={}
    retry_errors=dict(previous);retry_spent={}
    source_retry_errors={uid:error for uid,error in previous.items() if rpg and language_failure(error)}
    def persist(result,metrics,active=None):
        nonlocal completed
        if not result:return
        if active:
            metrics=dict(metrics,device_id=active.get('device_id'),backend=active.get('device_backend'),
                         runtime_generation=active.get('_runtime_generation'))
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
        accepted=[item for item in result if item.get('status') not in ('source_fallback','partial_translation')]
        if accepted and output_policy:
            from job_events import resolve_item_warnings
            resolve_item_warnings([item['id'] for item in accepted])
        committed=cfg.get('_recovery_progress_committed')
        if accepted and committed:
            try:
                committed(item_ids=[item['id'] for item in accepted],
                    request_ids=list(dict.fromkeys(request_id for item in accepted
                        for request_id in item.get('request_ids',[item.get('request_id')]) if request_id)))
            except Exception as exc:
                import sys
                print('Runtime commit logging unavailable: '+str(exc),file=sys.stderr,flush=True)
        if any(item['id'] in actor_ids for item in result):
            from rpgmaker_plain_retry import representatives
            cfg['_rpg_representatives']=representatives(project,cfg,all_rows,known)
            from rpgmaker_names import validation_config
            cfg.update(validation_config(cfg,all_rows,known))
            from rpgmaker_input import configuration as input_configuration
            cfg.update(input_configuration(cfg,all_rows,known))
        if cfg.get('_recovery_incident') and cfg.get('_recovery_event'):
            cfg['_recovery_event']('cache_committed',incident_id=cfg['_recovery_incident'],
                ids=[item['id'] for item in result],cached_total=len(known))
        if memory.observe(result):save_json(project/'data/term-memory.json',memory.translations)
        with (project/'data/metrics.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(metrics)+'\n')
        completed+=len(result)
        tracker.commit(result)
        if dispatch and active:
            dispatch.committed(active,[item['id'] for item in result
                if item.get('status') not in ('source_fallback','partial_translation')])
        save_json(project/'data/progress.json',dict(tracker.snapshot(),mode='sample' if sample else 'full',cached_total=len(known)))
        print(f'{completed}/{len(pending)} entries, batch {metrics["seconds"]}s',flush=True)
    def submit(batch,attempt,clean_phase,active=None,previous_model=None):
        request_cfg=dict(cfg)
        if active:
            request_cfg.update({key:value for key,value in active.items()
                if key in ('model','endpoint','num_ctx','parallel','kv_cache_type','context_txt_enabled','device_id',
                           'device_vendor','device_backend','worker_slots')
                or key.startswith(('_runtime_','_recovery_','_hardware_','_dispatch_'))})
        switched_model=previous_model is not None and request_cfg.get('model') != previous_model
        clean_phase=clean_phase or switched_model
        context=(context_index.context(batch,request_cfg,known=known) if output_policy else context_index.context(batch,request_cfg))
        spent=max([attempt]+[retry_spent.get(row['id'],0) for row in batch])
        full_source=rpg and (clean_phase or all(row['id'] in source_retry_errors for row in batch))
        kind=retry_kind(batch[0]) if japanese_policy and (spent or clean_phase) and len(batch)==1 else None
        if kind in ('minor','no_korean','mixed'):full_source=switched_model or kind!='minor' or spent>=2
        if switched_model:kind=None
        batch_cfg=dict(request_cfg,_batch_glossary=memory.relevant(batch),_allow_batch_split=len(batch)>1,
                       _item_retry_attempt=spent,_item_retry_limit=2)
        if cfg.get('engine')=='rpgmaker':
            batch_cfg['_rpg_retry_errors']={} if full_source else dict(retry_errors)
            batch_cfg['_rpg_correction_enabled']=output_policy and kind=='minor' and not full_source
            batch_cfg['_rpg_correction_attempt']=max(0,spent-1) if output_policy else 0
            batch_cfg['_rpg_clean_retry']=full_source
            batch_cfg['_rpg_full_source_retry']=full_source
            if output_policy:
                batch_cfg.update(_rpg_retry_kind=kind,_rpg_retry_attempt=spent)
                if kind=='minor' and not full_source and batch[0]['id'] in partials:
                    from rpgmaker_tokens import split_outer_controls
                    from rpgmaker_preprocessing import prepare as prepare_rpg_styles
                    _,core,_=split_outer_controls(partials[batch[0]['id']]['text'])
                    core=prepare_rpg_styles(core)[0]
                    draft,_=protect(core,cfg)
                    batch_cfg['_rpg_validated_draft']={'source':prepare_rpg_styles(batch[0]['source'])[0],'protected':draft}
                    batch_cfg['_rpg_retry_errors'][batch[0]['id']]='untranslated Japanese text; output='+repr(draft)
        elif japanese_policy and kind:
            batch_cfg['_japanese_retry_kind']=kind
            batch_cfg['_japanese_retry_attempt']=spent
        retry_note=retry_instruction(batch,retry_errors,request_cfg) if attempt and not full_source and not switched_model else ''
        if japanese_policy and not rpg and kind:retry_note=''
        if attempt or clean_phase:
            trace_event(batch_cfg,uuid.uuid4().hex,'item_retry',attempt=attempt,
                        **({'retry_phase':'clean_source' if full_source else 'translation','attempt_limit':3} if rpg else
                           {'retry_phase':'full_source','attempt_limit':2} if japanese_policy else {}),
                        items=[{'id':r['id'],'usage':r.get('usage',r['kind']),'error':retry_errors.get(r['id'])} for r in batch])
        future=pool.submit(copy_context().run,translate_batch,batch,context,batch_cfg,retry_note)
        running[future]=(batch,attempt,clean_phase,previous_model)
        if active:leases[future]=active
    from request_budget import BatchNeedsSplit
    try:
      while queue or running:
        if dispatch:dispatch.tick()
        while queue and len(running)<workers:
            batch,attempt,clean_phase,previous_model=queue[0]
            active=dispatch.acquire(preferred_model=previous_model) if dispatch else None
            if dispatch and active is None:break
            queue.popleft()
            try:submit(batch,attempt,clean_phase,active,previous_model)
            except BaseException:
                if dispatch:dispatch.release(active)
                raise
        if not running:
            if dispatch:
                from runtime_recovery import cancelled
                cancelled();time.sleep(.1);continue
            break
        ready,_=wait(running,timeout=.1 if dispatch else None,return_when=FIRST_COMPLETED)
        if not ready:continue
        future=next(iter(ready));batch,attempt,clean_phase,previous_model=running.pop(future)
        active=leases.pop(future,None)
        if dispatch:dispatch.release(active)
        invalid=[]
        try:
            result,metrics=future.result()
        except BatchNeedsSplit:
            middle=len(batch)//2
            queue.appendleft((batch[middle:],attempt,clean_phase,previous_model))
            queue.appendleft((batch[:middle],attempt,clean_phase,previous_model))
            print(f'Context window: splitting {len(batch)} entries into {middle} + {len(batch)-middle}; source text kept whole.',flush=True)
            continue
        except BatchTranslationError as exc:
            result,metrics,invalid=exc.output,exc.metrics,exc.failures
        except (ValueError,KeyError,TypeError) as exc:
            # Malformed model output can be isolated; transport errors must stop
            # the job instead of turning every remaining line into a fallback.
            result=[];metrics={'seconds':0,'items':len(batch)}
            invalid=[{'row':r,'error':str(exc)} for r in batch]
        except Exception as exc:
            if dispatch and getattr(exc,'_rpt_transport',False):
                exc.recovery_state={'failed_batch':[r['id'] for r in batch],
                    'device_id':active.get('device_id'),'cached_total':len(known)}
                dispatch.failed(active,exc)
                queue.appendleft((batch,attempt,clean_phase,active.get('model',cfg.get('model'))))
                continue
            raise
        for failure in reversed(invalid):
            row=failure['row']
            failure_attempt=max(attempt,failure.get('retry_attempt',attempt),retry_spent.get(row['id'],0))
            if japanese_policy:
                candidate=failure.get('candidate')
                if candidate is not None:
                    prior=partials.get(row['id'])
                    if prior is None or (candidate['japanese_count'],-candidate['hangul_count'])<(prior['japanese_count'],-prior['hangul_count']):
                        partials[row['id']]=candidate
                kind=failure.get('residue_kind')
                if kind is None and language_failure(failure['error']):
                    from rpgmaker_protocol import previous_output
                    value=previous_output(failure['error'])
                    kind=classify_output(row['source'],value,cfg)['kind'] if value is not None else 'no_korean'
                latest_kinds[row['id']]=kind or 'structure'
            if row['id'] in source_retry_errors and not japanese_policy:
                # Retain the usable partial draft if the one full-source retry
                # also fails. Its actual last failure stays in diagnostics.
                failure['source_retry_error']=failure['error']
                failure['error']=source_retry_errors[row['id']]
            retry_errors[row['id']]=failure['error']
            clean_next=(rpg or japanese_policy) and (clean_phase or language_failure(failure['error']))
            if clean_next and failure_attempt<2 and not failure.get('terminal'):
                source_retry_errors.setdefault(row['id'],failure['error'])
                retry_spent[row['id']]=failure_attempt+1
                queue.appendleft(([row],failure_attempt+1,True,active.get('model',cfg.get('model')) if active else None))
                print(f'Retrying item {row["id"]} with clean source ({failure_attempt+1}/2)',flush=True)
            elif failure_attempt<2 and not failure.get('terminal'):
                retry_spent[row['id']]=failure_attempt+1
                queue.appendleft(([row],failure_attempt+1,False,active.get('model',cfg.get('model')) if active else None))
                print(f'Retrying item {row["id"]} alone ({failure_attempt+1}/2)',flush=True)
            else:
                draft=partials.get(row['id']) if japanese_policy else None
                result.append({'id':row['id'],'source':row['source'],'text':draft['text'] if draft else row['source'],
                    'model':active.get('model',cfg['model']) if active else cfg['model'],'fingerprint':fingerprint(dict(cfg,**{k:active[k] for k in ('model','num_ctx') if k in active})) if active else fingerprint(cfg),
                    'status':'partial_translation' if draft else 'source_fallback','error':failure['error'],
                    **({'_japanese_residual_language':True,'residue_kind':draft['residue_kind'],
                        **({'_japanese_ruby_pending':True} if draft.get('ruby_pending') else {}),
                        **({'_rpg_residual_language':True} if rpg else {})} if draft else {}),
                    **{k:failure[k] for k in ('input_format','error_code','source_retry_error') if k in failure}})
                if draft:failure=dict(failure,kept_partial=True,residue_kind=draft['residue_kind'])
                failures.append(failure)
                from job_events import warning
                warning('partial_translation' if draft else 'original_kept',
                        '부분 번역을 유지한 항목' if draft else '원문으로 남은 항목',item_ids=[row['id']])
                trace_event(cfg,uuid.uuid4().hex,'partial_translation' if draft else 'source_fallback',
                            id=row['id'],usage=row.get('usage',row['kind']),error=failure['error'])
                save_json(project/'data/failed-items.json',failures)
                print(f'Kept {"partial translation" if draft else "original text"} for {row["id"]}; continuing',flush=True)
        persist(result,metrics,active)
    except Exception as exc:
        if getattr(exc,'_rpt_transport',False):
            # Save completed siblings before unwinding the failed runtime; never
            # wait here for a hung GPU request. Uncommitted entries remain pending.
            siblings=[]
            for task in running:
                if not task.done() or task.cancelled():continue
                try:output,measurement=task.result()
                except BatchTranslationError as partial:output,measurement=partial.output,partial.metrics
                except Exception as sibling:
                    siblings.append({'request_id':getattr(sibling,'request_id',None),
                        'error':str(sibling),'http':getattr(sibling,'http_details',None)});continue
                persist(output,measurement)
            exc.recovery_state={'failed_batch':[r['id'] for r in batch],
                'in_flight':[[r['id'] for r in other] for other,_,_,_ in running.values()],
                'queued_batches':len(queue),'cached_total':len(known),'sibling_errors':siblings}
            exc._rpt_workers=list(running)
        raise
    finally:
        for future in running:future.cancel()
        # On interruption the owned runtime is killed as this function unwinds.
        pool.shutdown(wait=not running,cancel_futures=True)
    save_json(project/'data/failed-items.json',failures)
    if failures:
        partial_count=sum(bool(f.get('kept_partial')) for f in failures)
        print(f'{len(failures)-partial_count} entries kept in original language; '+
              (f'{partial_count} partial translations retained; ' if partial_count else '')+
              'see data/failed-items.json',flush=True)
    if sample:
        save_json(project/'data/sample-review.json',[dict(r,translation=known[r['id']]['text']) for r in rows])

def retry_instruction(rows, errors=None, cfg=None):
    if cfg and cfg.get('engine')=='kirikiri':
        from kirikiri_translation import retry_instruction as kirikiri_retry
        return kirikiri_retry(rows,cfg)
    if cfg and cfg.get('engine')=='rpgmaker':
        from rpgmaker_protocol import retry_instruction as rpg_retry
        return rpg_retry(rows,errors or {},cfg)
    from token_recovery import retry_details
    detail=retry_details(rows,errors or {},cfg)
    from label_policy import label_batch
    if label_batch(rows):
        detail+=' These are names/roles or interface labels. Keep each translation value limited to the translated label as a name or noun phrase; do not create dialogue, a speaker prefix, an event or an explanation.'
    if any('empty hyperlink text' in error for error in (errors or {}).values()):
        detail+=' Keep the translated link label BETWEEN its opening and closing markers; never leave the link empty.'
    from token_recovery import prepare_renpy_source
    if any(protect(prepare_renpy_source(r['source'])[0],cfg)[1] for r in rows):
        return 'Previous output failed validation. Preserve each input placeholder exactly once. Only movable_placeholders may change order; preserve the relative order of the remaining tags. Never add new placeholders. Translate the visible text fully into Korean.'+detail
    return 'Previous output failed validation. Translate every input item fully into Korean. Do not add commentary, annotations, or control tokens.'+detail


def display_metadata(project,cfg,rows=None):
    if cfg.get('engine')=='kirikiri':return {'names':{},'show_reference':cfg.get('show_reference',True)}
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
    from display_text import rendered_value
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
        errors=validate_entry(row['source'],known[row['id']],cfg,allow_partial=True)
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
            entry=known[row['id']]
            manual=entry.get('status')=='manual_translation'
            composed=rendered_value(row,entry,metadata)
            from patch_validation import validate_final
            final_errors=[] if manual else validate_final(row,composed,cfg)
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
        e=validate_entry(r['source'],known[r['id']],cfg,allow_partial=True)
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
