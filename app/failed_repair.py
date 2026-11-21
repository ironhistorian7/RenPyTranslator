"""Recover indexed failures, with nearby prepared source context; no game launch."""
import json
from contextlib import ExitStack
from urllib.parse import urlparse
from engine import save_json
from script_literals import literal_eval
from translation import (read_catalog,cache,protect,restore,validate_text,PLACEHOLDER,HANGUL,
                         translate_batch,BatchTranslationError,retry_instruction,display_metadata)


def residual_candidate(entry, cfg, result):
    """Honor explicit exclusions before selecting a previously accepted draft."""
    from japanese_output_policy import enabled
    return bool(enabled(cfg) and entry and not result.get('excluded') and
                entry.get('status') not in ('manual_translation','name_review','ui_original','ui_fixed','literal') and
                entry.get('model') != 'reviewed override' and
                (entry.get('_japanese_residual_language') or entry.get('_rpg_residual_language')))


def saved_output(source,error,cfg,usage=None,*,input_format=None):
    _,sep,value=error.partition('; output=')
    if not sep:return None
    try:text=literal_eval(value)
    except (ValueError,SyntaxError,TypeError):return None
    if not isinstance(text,str):return None
    from japanese_output_policy import enabled as japanese_enabled
    if japanese_enabled(cfg):
        from japanese_cache_repair import numeric_candidate
        numeric=numeric_candidate(source,dict(cfg,_usage=usage))
        if numeric is not None:text=numeric
    from token_recovery import (restore_saved,split_outer_styles,restore_output,restore_renpy_output,
                                prepare_renpy_source,restore_prepared_renpy_output)
    try:
        if cfg.get('engine')=='kirikiri':
            from kirikiri_tokens import protect as native_protect,restore as native_restore
            from token_recovery import _normalize_tool_markers
            # A failure record is either the raw protected response or already
            # assembled native text. Never parse it as Ren'Py markup.
            if japanese_enabled(cfg):
                from japanese_cache_repair import restore_kirikiri_layout
                text=restore_kirikiri_layout(source,text,cfg,input_format=input_format)
            else:
                text=_normalize_tool_markers(text)[0]
                if PLACEHOLDER.search(text):text=native_restore(text,native_protect(source,cfg)[1])
        elif cfg.get('engine')=='rpgmaker':
            from rpgmaker_tokens import split_outer_controls,protect as rpg_protect
            from rpgmaker_protocol import normalize_markers
            prefix,core,suffix=split_outer_controls(source)
            text=normalize_markers(text)
            if PLACEHOLDER.search(text):text=restore_output(text,rpg_protect(core)[1])
            # A saved response may already contain restored codes. Only the
            # excluded leading coordinates can be supplied without inference.
            if prefix and not text.startswith(prefix):text=prefix+text
            text+=suffix
        elif cfg.get('engine')=='unity':
            from unity_output import INPUT_FORMAT
            from unity_text_formats import restore_outer_parentheses
            if PLACEHOLDER.search(text):
                _,tokens=protect(source,cfg,unity_visible_digits=input_format==INPUT_FORMAT)
                text=restore_output(text,tokens)
            if usage=='choice':text=restore_outer_parentheses(source,text)
        elif input_format=='renpy_visual_stripped_v1' or error.startswith('Visual-stripped:'):
            core,plan=prepare_renpy_source(source)
            text=restore_prepared_renpy_output(core,text,plan)[0]
        elif error.startswith('Wrapper-stripped:'):
            prefix,core,suffix=split_outer_styles(source)
            restored=restore_renpy_output(core,text)[0]
            text=prefix+restored+suffix
        else:text=restore_saved(source,text)
    except ValueError:return None
    from label_policy import expansion_errors
    from failure_candidates import visible
    from rpgmaker_names import name_only
    from rpgmaker_output_policy import enabled
    policy=enabled(cfg)
    from japanese_output_policy import enabled as japanese_enabled, prepare_output,small_tsu_punctuation_result
    details=None
    if japanese_enabled(cfg):
        cleaned,details=prepare_output(source,text,cfg)
        if cleaned!=text:details=dict(details,kind='clean',japanese_count=0,spans=[])
        text=cleaned
        if details['kind']!='clean':return None
    label_errors=(expansion_errors({'source':source,'usage':usage},text,cfg) if policy or cfg.get('engine')=='unity' else
                  expansion_errors({'source':source,'usage':usage},text))
    from japanese_numbers import compare as compare_numbers
    numeric_ok=japanese_enabled(cfg) and compare_numbers(source,text)=='match'
    return text if (HANGUL.search(visible(text)) or name_only(source,cfg) or numeric_ok or small_tsu_punctuation_result(source,text,cfg)) and not (validate_text(source,text,cfg,check_link_content=True,language_details=details)+label_errors) else None


def run(project,cfg):
    from job_events import set_phase,progress
    set_phase('repair.load','미번역 확인')
    cfg=dict(cfg,_trace_dir=str(project/'data'),_trace_phase='failed-repair')
    index=project/'data/failed-items.json'
    report={'indexed':0,'already_resolved':0,'recovered_saved':0,'retranslated':0,'remaining':0,
            'model_calls':0,'issues':[],'applied_items':0,'index_entries':0,
            'candidates':0,'discovered':0,'discovery':{},'reapplied_saved':0,
            'intentional_original_excluded':0,'exclusions':{}}
    if not index.exists() and not (project/'data/catalog.json').is_file():
        print('No prepared catalog or failure index: nothing to repair.',flush=True)
        save_json(project/'output/failed-repair-report.json',report)
        progress('repair.load',0,0)
        return set(),report
    failures=json.loads(index.read_text(encoding='utf-8')) if index.exists() else []
    indexed={f['row']['id']:f for f in failures if f.get('row',{}).get('id')}
    report['indexed']=report['index_entries']=len(indexed)
    catalog_rows=read_catalog(project)
    rows={r['id']:r for r in catalog_rows}
    known=cache(project,cfg)
    unity=cfg.get('engine')=='unity'
    if unity:
        from unity_text_formats import quantity_mismatch
    if cfg.get('engine')=='rpgmaker':
        from name_review import configuration as review_configuration
        cfg=review_configuration(project,cfg,catalog_rows)
        from rpgmaker_names import validation_config
        cfg=validation_config(cfg,catalog_rows,known)
        from rpgmaker_input import configuration as input_configuration
        cfg=input_configuration(cfg,catalog_rows,known)
    elif cfg.get('engine')=='kirikiri':
        from name_review import configuration as review_configuration
        cfg=review_configuration(project,cfg,catalog_rows)
    from ui_policy import cached_policy
    ui=cached_policy(project)
    from failure_candidates import classify,names_from,visible
    from name_translation import mapping
    person_names=cfg.get('_person_names',{}) if cfg.get('engine') in ('rpgmaker','kirikiri') else mapping(project)
    known_names=names_from(catalog_rows,cfg,person_names)
    path=project/'data/recovered-translations.json'
    recovered=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    changed=set();pending=[];unresolved={};stale=set()
    def keep(row,entry,text,method,*,readings_updated=False,numeric_review=None):
        before=entry.get('_japanese_cache_before',entry['text']) if entry is not None else None
        previous=recovered.get(row['id'],{})
        if (entry is not None and entry.get('status')=='failure_recovered' and
                previous.get('source')==row['source'] and previous.get('text')==entry['text'] and
                'before' in previous):
            before=previous['before']
        recovered[row['id']]={'source':row['source'],'before':before,
                              'text':text,'method':method}
        if unity:
            if numeric_review is None:numeric_review=quantity_mismatch(row['source'],text)
            if numeric_review:recovered[row['id']]['_unity_numeric_review']=True
        if (method=='retranslated' or readings_updated) and cfg.get('engine')=='kirikiri':
            from kirikiri_ruby import prepare,REVISION
            from kirikiri_translation import row_config
            if prepare(row['source'],row_config(cfg,row))[2]:
                recovered[row['id']]['ruby_revision']=REVISION
        save_json(path,recovered)
        changed.add(row['id']);report[method]+=1
    def exclude(reason):
        report['exclusions'][reason]=report['exclusions'].get(reason,0)+1
        if reason!='cache_source_mismatch':report['intentional_original_excluded']+=1
    set_phase('repair.index','실패 기록 확인',len(indexed))
    for position,(uid,failure) in enumerate(indexed.items()):
        progress('repair.index',position,len(indexed))
        row=rows.get(uid)
        if not row or failure['row'].get('source')!=row['source']:
            report['remaining']+=1
            report['issues'].append({'id':uid,'reason':'Stale failure index; left unchanged'})
            unresolved[uid]=failure;stale.add(uid)
    progress('repair.index',len(indexed),len(indexed))
    set_phase('repair.saved','저장된 번역 복구',len(rows))
    for position,(uid,row) in enumerate(rows.items()):
        progress('repair.saved',position,len(rows))
        if uid in stale:continue
        failure=indexed.get(uid);entry=known.get(uid)
        row_cfg=dict(cfg,_unity_text_kind=row.get('unity_format')) if unity else cfg
        result=classify(row,entry,row_cfg,ui,known_names,indexed=failure is not None)
        from japanese_output_policy import enabled as japanese_enabled
        if japanese_enabled(cfg) and entry and entry.get('status')=='name_review':
            result={'excluded':'name_review'}
        residual=residual_candidate(entry,cfg,result)
        excluded=result.get('excluded')
        if excluded:
            exclude(excluded)
            if excluded=='cache_source_mismatch' and failure:
                report['remaining']+=1;unresolved[uid]=failure
                report['issues'].append({'id':uid,'reason':'Cached source does not match the current catalog; left unchanged'})
            elif failure:report['already_resolved']+=1
            continue
        origin='japanese_residue' if residual else result.get('origin')
        if not origin:
            if failure:report['already_resolved']+=1
            continue
        report['candidates']+=1
        report['discovery'][origin]=report['discovery'].get(origin,0)+1
        if failure is None:report['discovered']+=1
        if origin=='saved_recovery':
            report['already_resolved']+=1;report['reapplied_saved']+=1;changed.add(uid)
            continue
        if residual:
            # A safe deterministic cleanup may resolve small-tsu remnants. Keep
            # the original cache record and save only a fully validated overlay.
            from japanese_output_policy import prepare_output, classify as classify_japanese
            check_cfg=cfg
            if cfg.get('engine')=='kirikiri':
                from kirikiri_translation import row_config
                check_cfg=row_config(cfg,row)
            cleaned,details=prepare_output(row['source'],entry['text'],check_cfg)
            if cleaned != entry['text'] and classify_japanese(row['source'],cleaned,check_cfg)['kind']=='clean':
                from label_policy import expansion_errors
                if not (validate_text(row['source'],cleaned,check_cfg,check_link_content=True) +
                        expansion_errors(row,cleaned,check_cfg)):
                    keep(row,entry,cleaned,'recovered_saved');continue
        error=('Unchanged English dialogue or choice' if origin=='unchanged_english' else
               'No matching cached translation' if origin=='missing_cache' else
               f'{uid}: {result.get("errors",[])}')
        if residual:
            error='untranslated Japanese text; output='+repr(entry['text'])
        # A newer indexed response and the original cache response can each be
        # recoverable. Neither should hide the other merely by taking priority.
        errors=[]
        for previous in (({'error':error},failure or {},entry or {}) if residual else
                         (failure or {},entry or {},{'error':error})):
            value=(previous.get('error'),previous.get('input_format'))
            if value[0] and value not in errors:errors.append(value)
        for previous_error,input_format in errors:
            if entry and entry.get('_japanese_ruby_pending') and input_format!='kirikiri_reading_v2':
                continue
            saved_cfg=row_cfg
            if cfg.get('engine')=='kirikiri':
                from kirikiri_translation import row_config
                saved_cfg=row_config(cfg,row)
            text=saved_output(row['source'],previous_error,saved_cfg,row.get('usage'),input_format=input_format)
            if text is not None:
                keep(row,entry,text,'recovered_saved',readings_updated=input_format=='kirikiri_reading_v2');break
        else:pending.append((row,entry,errors[0][0]))
    progress('repair.saved',len(rows),len(rows))
    if unity and pending:
        from unity_output import saved_attempts
        attempts=saved_attempts(project,{row['id']:row for row,_,_ in pending},cfg)
        unrecovered=[]
        for row,entry,error in pending:
            row_cfg=dict(cfg,_unity_text_kind=row.get('unity_format'))
            for attempt,input_format in attempts.get(row['id'],()):
                text=saved_output(row['source'],attempt,row_cfg,row.get('usage'),input_format=input_format)
                if text is not None:
                    keep(row,entry,text,'recovered_saved')
                    break
            else:unrecovered.append((row,entry,error))
        pending=unrecovered
    print('Failed repair candidates: index %d; discovered %d; total %d; original/policy exclusions %d; sources %s'%
          (report['index_entries'],report['discovered'],report['candidates'],
           report['intentional_original_excluded'],json.dumps(report['discovery'],sort_keys=True)),flush=True)
    print('Failed repair: %d saved outputs recovered; %d existing recoveries reapplied; %d entries require local translation.'%
          (report['recovered_saved'],report['reapplied_saved'],len(pending)),flush=True)
    set_phase('repair.translate','미번역 재번역',len(pending))
    progress('repair.translate',0,len(pending))
    remaining_before=report['remaining']
    if pending:
        from source_context import SourceContext
        files={row.get('file','') for row,entry,error in pending}
        from hy_backend import runtime,is_hy
        from model_runtime import model_session
        if not is_hy(cfg) and urlparse(cfg.get('endpoint','')).hostname not in ('127.0.0.1','localhost','::1'):
            raise ValueError('Failed repair requires a local model endpoint')
        active_cfg=dict(cfg,parallel=1,_serial_model_request=True,_names=display_metadata(project,dict(cfg,_fix='failed')).get('names',{}),
                        _person_names=person_names)
        with ExitStack() as stack:
            stack.enter_context(model_session())
            active=stack.enter_context(runtime(active_cfg))
            context_index=SourceContext([r for r in catalog_rows if r.get('file','') in files],project,active)
            for position,(row,entry,error) in enumerate(pending):
                failure_format=None
                clean=False
                for attempt in range(2):
                    report['model_calls']+=1
                    try:
                        from hy_backend import is_hy
                        request_cfg=active if is_hy(active) else dict(active,_item_retry_attempt=attempt+1,_item_retry_limit=2)
                        from japanese_output_policy import enabled as japanese_enabled
                        if japanese_enabled(active) and active.get('engine')!='rpgmaker':
                            request_cfg=dict(request_cfg,_japanese_retry_attempt=attempt+1,
                                             _japanese_retry_kind='mixed' if entry and entry.get('_japanese_ruby_pending') else 'structure')
                            clean=True
                        generic_residual=(active.get('engine')!='rpgmaker' and
                                          residual_candidate(entry,active,{}))
                        if generic_residual:
                            from japanese_output_policy import classify as classify_japanese
                            draft_cfg=active
                            if active.get('engine')=='kirikiri':
                                from kirikiri_translation import row_config
                                draft_cfg=row_config(active,row)
                            kind=classify_japanese(row['source'],entry['text'],draft_cfg)['kind']
                            draft_errors=validate_text(row['source'],entry['text'],draft_cfg,check_link_content=True)
                            structural=[e for e in draft_errors if e not in ('untranslated Japanese text','no Korean translation')]
                            from label_policy import expansion_errors
                            structural+=expansion_errors(row,entry['text'],draft_cfg)
                            request_cfg=dict(request_cfg,_japanese_retry_kind=kind,
                                             _japanese_retry_attempt=attempt+1)
                            # Generic retries receive either an explicitly validated
                            # minor draft or a clean source, never the old error text.
                            clean=True
                        clean=clean or (active.get('engine')=='rpgmaker' and
                                        any(code in error for code in ('untranslated Japanese text','no Korean translation')))
                        if active.get('engine')=='rpgmaker':
                            request_cfg=dict(request_cfg,_rpg_clean_retry=clean,_rpg_full_source_retry=clean,
                                _rpg_retry_errors={} if clean else {row['id']:error},_rpg_correction_enabled=False)
                            from rpgmaker_output_policy import enabled,classify as classify_output
                            if enabled(active):
                                from rpgmaker_protocol import previous_output
                                draft=previous_output(error)
                                kind=classify_output(row['source'],draft,active)['kind'] if draft is not None else 'no_korean'
                                clean=kind!='minor' or attempt>0
                                request_cfg.update(_rpg_retry_kind=kind,_rpg_retry_attempt=attempt+1,
                                    _rpg_clean_retry=clean,_rpg_full_source_retry=clean,
                                    _rpg_retry_errors={} if clean else {row['id']:error},
                                    _rpg_correction_enabled=kind=='minor' and not clean,_rpg_correction_attempt=attempt)
                                if kind=='minor' and not clean and residual_candidate(entry,active,{}):
                                    from rpgmaker_tokens import split_outer_controls
                                    from rpgmaker_preprocessing import prepare as prepare_rpg_styles
                                    from label_policy import expansion_errors
                                    draft_errors=validate_text(row['source'],entry['text'],active,check_link_content=True)
                                    structural=[e for e in draft_errors if e not in ('untranslated Japanese text','no Korean translation')]
                                    structural+=expansion_errors(row,entry['text'],active)
                                    if not structural:
                                        _,core,_=split_outer_controls(entry['text'])
                                        core=prepare_rpg_styles(core)[0]
                                        draft,_=protect(core,active)
                                        request_cfg['_rpg_validated_draft']={
                                            'source':prepare_rpg_styles(row['source'])[0], 'protected':draft}
                                        request_cfg['_rpg_retry_errors']={row['id']:
                                            'untranslated Japanese text; output='+repr(draft)}
                        note='' if clean else retry_instruction([row],{row['id']:error},active)
                        result,_=translate_batch([row],context_index.context([row],active),request_cfg,note)
                        text=result[0]['text']
                        if residual_candidate(entry,active,{}):
                            from japanese_output_policy import classify as classify_japanese
                            check_cfg=request_cfg
                            if active.get('engine')=='kirikiri':
                                from kirikiri_translation import row_config
                                check_cfg=row_config(request_cfg,row)
                            if classify_japanese(row['source'],text,check_cfg)['kind']!='clean':
                                raise ValueError('untranslated Japanese text; output='+repr(text))
                        from rpgmaker_names import name_only
                        from japanese_numbers import compare as compare_numbers
                        numeric_ok=japanese_enabled(active) and compare_numbers(row['source'],text)=='match'
                        if not HANGUL.search(visible(text)) and not name_only(row['source'],active) and not numeric_ok:
                            raise ValueError('No Korean in recovered output; output='+repr(text))
                        keep(row,entry,text,'retranslated',numeric_review=result[0].get('_unity_numeric_review',False));break
                    except BatchTranslationError as exc:
                        error=exc.failures[0]['error'];failure_format=exc.failures[0].get('input_format')
                    except (ValueError,KeyError,TypeError,IndexError) as exc:error=str(exc)
                else:
                    report['remaining']+=1;report['issues'].append({'id':row['id'],'reason':error})
                    unresolved[row['id']]={'row':row,'error':error,
                        **({'input_format':failure_format} if failure_format else {})}
                progress('repair.translate',position+1,len(pending),successful=report['retranslated'],
                         original_kept=report['remaining']-remaining_before)
    # The index is a projection of unresolved work, not the only source of truth.
    # Saved overlays continue to be discovered and reapplied after it becomes [].
    set_phase('repair.finish','복구 결과 저장')
    save_json(index,list(unresolved.values()))
    report['applied_items']=len(changed)
    save_json(project/'output/failed-repair-report.json',report)
    progress('repair.finish',1,1)
    print('Failed repair: saved recovery %d; reapplied %d; retranslated %d; still failed %d; model calls %d'%
          (report['recovered_saved'],report['reapplied_saved'],report['retranslated'],report['remaining'],report['model_calls']),flush=True)
    return changed,report


def refresh_choices(project,cfg,selected):
    """Update only repaired story-choice payloads, retaining all font/menu data."""
    path=project/'staging/game/tl'/cfg['language']/'_rpt_presentation.json'
    if not path.exists() or not selected:return
    from replacements import effective_cache
    from display_text import compose
    data=json.loads(path.read_text(encoding='utf-8'));known=effective_cache(project,cfg,apply_ui=False)
    metadata=display_metadata(project,cfg);changed=False
    for row in read_catalog(project):
        if row['id'] in selected and row['kind']=='string' and row['source'] in data.get('choices',{}) and row['id'] in known:
            data['choices'][row['source']]=compose(dict(row,kind='dialogue'),known[row['id']],metadata);changed=True
    if changed:save_json(path,data)
