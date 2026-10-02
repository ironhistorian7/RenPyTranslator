"""Recover indexed failures, with nearby prepared source context; no game launch."""
import json
from contextlib import ExitStack
from urllib.parse import urlparse
from engine import save_json
from script_literals import literal_eval
from translation import (read_catalog,cache,protect,restore,validate_text,PLACEHOLDER,HANGUL,
                         translate_batch,BatchTranslationError,retry_instruction,display_metadata)


def saved_output(source,error,cfg,usage=None):
    _,sep,value=error.partition('; output=')
    if not sep:return None
    try:text=literal_eval(value)
    except (ValueError,SyntaxError,TypeError):return None
    if not isinstance(text,str):return None
    from token_recovery import restore_saved,split_outer_styles,restore_output
    try:
        if error.startswith('Wrapper-stripped:'):
            prefix,core,suffix=split_outer_styles(source)
            text=prefix+restore_output(text,protect(core)[1])+suffix
        else:text=restore_saved(source,text)
    except ValueError:return None
    from label_policy import expansion_errors
    return text if HANGUL.search(text) and not (validate_text(source,text,cfg,check_link_content=True)+expansion_errors({'source':source,'usage':usage},text)) else None


def run(project,cfg):
    cfg=dict(cfg,_trace_dir=str(project/'data'),_trace_phase='failed-repair')
    index=project/'data/failed-items.json'
    report={'indexed':0,'already_resolved':0,'recovered_saved':0,'retranslated':0,'remaining':0,
            'model_calls':0,'issues':[]}
    if not index.exists():
        print('No failed-items index: nothing scanned or translated.',flush=True)
        save_json(project/'output/failed-repair-report.json',report)
        return set(),report
    failures=json.loads(index.read_text(encoding='utf-8'))
    indexed={f['row']['id']:f for f in failures if f.get('row',{}).get('id')}
    report['indexed']=len(indexed)
    catalog_rows=read_catalog(project)
    rows={r['id']:r for r in catalog_rows if r['id'] in indexed}
    known=cache(project,cfg)
    from ui_policy import cached_policy,standard
    ui=cached_policy(project)
    path=project/'data/recovered-translations.json'
    recovered=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    changed=set();pending=[]
    def keep(row,entry,text,method):
        recovered[row['id']]={'source':row['source'],'before':entry['text'],'text':text,'method':method}
        save_json(path,recovered)
        changed.add(row['id']);report[method]+=1
    for uid,failure in indexed.items():
        row=rows.get(uid);entry=known.get(uid)
        if not row or failure['row'].get('source')!=row['source']:
            report['remaining']+=1
            report['issues'].append({'id':uid,'reason':'Stale failure index; left unchanged'});continue
        if (row['kind']=='string' and row['source'] not in ui.get('choices',[]) and
                (row['source'] in ui.get('menu',{}) or
                 (row.get('file','').endswith('common.rpy') and standard(row['source']) is not None))):
            report['already_resolved']+=1;continue
        if entry and entry.get('status')!='source_fallback':
            report['already_resolved']+=1
            if entry.get('status')=='failure_recovered':changed.add(uid)
            continue
        if not entry:
            report['remaining']+=1
            report['issues'].append({'id':uid,'reason':'No matching cached failure; left unchanged'});continue
        text=saved_output(row['source'],entry.get('error') or failure.get('error',''),cfg,row.get('usage'))
        if text is not None:keep(row,entry,text,'recovered_saved')
        else:pending.append((row,entry))
    print('Failed repair: %d saved outputs recovered; %d entries require local translation.'%
          (report['recovered_saved'],len(pending)),flush=True)
    if pending:
        from source_context import SourceContext
        files={row.get('file','') for row,entry in pending}
        context_index=SourceContext([r for r in catalog_rows if r.get('file','') in files],project,cfg)
        from hy_backend import runtime,is_hy
        from model_runtime import model_session
        from name_translation import mapping
        if not is_hy(cfg) and urlparse(cfg.get('endpoint','')).hostname not in ('127.0.0.1','localhost','::1'):
            raise ValueError('Failed repair requires a local model endpoint')
        active_cfg=dict(cfg,parallel=1,_names=display_metadata(project,dict(cfg,_fix='failed')).get('names',{}),
                        _person_names=mapping(project))
        with ExitStack() as stack:
            stack.enter_context(model_session())
            active=stack.enter_context(runtime(active_cfg))
            for row,entry in pending:
                error=entry.get('error') or 'No usable translation'
                for attempt in range(2):
                    report['model_calls']+=1
                    try:
                        result,_=translate_batch([row],context_index.context([row],active),active,retry_instruction([row],{row['id']:error}))
                        text=result[0]['text']
                        if not HANGUL.search(text):raise ValueError('No Korean in recovered output')
                        keep(row,entry,text,'retranslated');break
                    except BatchTranslationError as exc:error=exc.failures[0]['error']
                    except (ValueError,KeyError,TypeError,IndexError) as exc:error=str(exc)
                else:
                    report['remaining']+=1;report['issues'].append({'id':row['id'],'reason':error})
    report['applied_items']=len(changed)
    save_json(project/'output/failed-repair-report.json',report)
    print('Failed repair: saved recovery %d; retranslated %d; still failed %d; model calls %d'%
          (report['recovered_saved'],report['retranslated'],report['remaining'],report['model_calls']),flush=True)
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
