"""Recover indexed failures, with nearby prepared source context; no game launch."""
import json
from contextlib import ExitStack
from urllib.parse import urlparse
from engine import save_json
from script_literals import literal_eval
from translation import (read_catalog,cache,protect,restore,validate_text,PLACEHOLDER,HANGUL,
                         translate_batch,BatchTranslationError,retry_instruction,display_metadata)


def saved_output(source,error,cfg,usage=None,*,input_format=None):
    _,sep,value=error.partition('; output=')
    if not sep:return None
    try:text=literal_eval(value)
    except (ValueError,SyntaxError,TypeError):return None
    if not isinstance(text,str):return None
    from token_recovery import (restore_saved,split_outer_styles,restore_output,restore_renpy_output,
                                prepare_renpy_source,restore_prepared_renpy_output)
    try:
        if (input_format=='renpy_visual_stripped_v1' or error.startswith('Visual-stripped:')) and cfg.get('engine')!='rpgmaker':
            core,plan=prepare_renpy_source(source)
            text=restore_prepared_renpy_output(core,text,plan)[0]
        elif error.startswith('Wrapper-stripped:'):
            prefix,core,suffix=split_outer_styles(source)
            restored=(restore_output(text,protect(core)[1]) if cfg.get('engine')=='rpgmaker'
                      else restore_renpy_output(core,text)[0])
            text=prefix+restored+suffix
        else:text=restore_saved(source,text,visual_styles=cfg.get('engine')!='rpgmaker')
    except ValueError:return None
    from label_policy import expansion_errors
    from failure_candidates import visible
    return text if HANGUL.search(visible(text)) and not (validate_text(source,text,cfg,check_link_content=True)+expansion_errors({'source':source,'usage':usage},text)) else None


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
    from ui_policy import cached_policy
    ui=cached_policy(project)
    from failure_candidates import classify,names_from,visible
    from name_translation import mapping
    person_names=mapping(project)
    known_names=names_from(catalog_rows,cfg,person_names)
    path=project/'data/recovered-translations.json'
    recovered=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    changed=set();pending=[];unresolved={};stale=set()
    def keep(row,entry,text,method):
        before=entry['text'] if entry is not None else None
        previous=recovered.get(row['id'],{})
        if (entry is not None and entry.get('status')=='failure_recovered' and
                previous.get('source')==row['source'] and previous.get('text')==entry['text'] and
                'before' in previous):
            before=previous['before']
        recovered[row['id']]={'source':row['source'],'before':before,
                              'text':text,'method':method}
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
        result=classify(row,entry,cfg,ui,known_names,indexed=failure is not None)
        excluded=result.get('excluded')
        if excluded:
            exclude(excluded)
            if excluded=='cache_source_mismatch' and failure:
                report['remaining']+=1;unresolved[uid]=failure
                report['issues'].append({'id':uid,'reason':'Cached source does not match the current catalog; left unchanged'})
            elif failure:report['already_resolved']+=1
            continue
        origin=result.get('origin')
        if not origin:
            if failure:report['already_resolved']+=1
            continue
        report['candidates']+=1
        report['discovery'][origin]=report['discovery'].get(origin,0)+1
        if failure is None:report['discovered']+=1
        if origin=='saved_recovery':
            report['already_resolved']+=1;report['reapplied_saved']+=1;changed.add(uid)
            continue
        error=('Unchanged English dialogue or choice' if origin=='unchanged_english' else
               'No matching cached translation' if origin=='missing_cache' else
               f'{uid}: {result.get("errors",[])}')
        # A newer indexed response and the original cache response can each be
        # recoverable. Neither should hide the other merely by taking priority.
        errors=[]
        for previous in (failure or {},entry or {},{'error':error}):
            value=(previous.get('error'),previous.get('input_format'))
            if value[0] and value not in errors:errors.append(value)
        for previous_error,input_format in errors:
            text=saved_output(row['source'],previous_error,cfg,row.get('usage'),input_format=input_format)
            if text is not None:
                keep(row,entry,text,'recovered_saved');break
        else:pending.append((row,entry,errors[0][0]))
    progress('repair.saved',len(rows),len(rows))
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
                for attempt in range(2):
                    report['model_calls']+=1
                    try:
                        result,_=translate_batch([row],context_index.context([row],active),active,retry_instruction([row],{row['id']:error}))
                        text=result[0]['text']
                        if not HANGUL.search(visible(text)):raise ValueError('No Korean in recovered output; output='+repr(text))
                        keep(row,entry,text,'retranslated');break
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
