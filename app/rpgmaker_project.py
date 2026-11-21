"""RPG Maker MV/MZ adapter: pinned Gameloc formats, existing local AI and cache.

Original games are read only during preparation. Rendering uses a text-only
snapshot and exports changed files for copying into the same game version.
"""
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shutil
import zipfile

from app_paths import ROOT, resource
from engine import save_json
from gameloc_vendor import load
from job_events import set_phase,progress,emit


def prepare(project, cfg):
    set_phase('prepare.inspect','작업 자료 확인 중')
    load()
    from gameloc.engines import rpgmaker
    from gameloc.extract import extract
    from gameloc.treepath import parse_path, get_by_path, format_path
    source=Path(cfg['source']); base=rpgmaker.is_rpgmaker(source)
    if base is None: raise ValueError('RPG Maker 데이터 폴더를 찾지 못했습니다.')
    variant=rpgmaker.variant(base)
    if variant not in ('MV','MZ'):raise ValueError('RPG Maker MV/MZ만 지원합니다.')
    snapshot=project/'data/rpg-source'; snapshot.mkdir(parents=True,exist_ok=True)
    paths=set(rpgmaker.data_files(base)) | set(rpgmaker.custom_data_files(base))
    paths.update((base/'js').rglob('*.js'))
    notes=base/'context'
    if notes.is_dir() and not notes.is_symlink() and not notes.is_junction():
        paths.update(p for p in notes.rglob('*.txt') if p.resolve().is_relative_to(notes.resolve()))
    manifest={}
    paths=sorted(p for p in paths if p.suffix.lower() in ('.json','.csv','.tsv','.txt','.js'))
    set_phase('prepare.copy','작업 파일 복사 중',len(paths),'files')
    for index,sourcefile in enumerate(paths,1):
        rel=sourcefile.relative_to(source); target=snapshot/rel
        target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(sourcefile,target)
        raw=target.read_bytes()
        if raw.startswith(b'\xef\xbb\xbf'):target.write_bytes(raw[3:])
        if target.suffix.lower()=='.json':
            try:json.loads(target.read_text(encoding='utf-8'))
            except (ValueError,UnicodeError) as exc:raise ValueError(f'RPG Maker JSON 읽기 실패: {rel}: {exc}') from exc
        manifest[rel.as_posix()]=hashlib.sha256(target.read_bytes()).hexdigest()
        progress('prepare.copy',index,len(paths))
    set_phase('prepare.extract','번역할 문구 추출 중')
    from rpgmaker_sources import copy_declared_tables
    copy_declared_tables(source,snapshot,base.relative_to(source),manifest)
    save_json(project/'data/source-manifest.json',manifest)
    print(f'Copying RPG Maker text and settings: {len(manifest)} files; no images/audio/runtime',flush=True)
    from rpgmaker_notices import Notices
    notices=Notices()
    try:
        upstream=extract(snapshot,dedup=False,languages=['ko'],source_lang='en' if cfg['source_language']=='english' else 'ja',
                         progress=notices.progress,on_notice=notices.notice)
    finally:notices.save(project)
    # Gameloc does not currently extract MZ's dedicated speaker-name field.
    if variant=='MZ':
        from gameloc.model import Entry,Location,make_id_unique
        def speakers(node,parts,rel):
            if isinstance(node,dict):
                params=node.get('parameters',[])
                if node.get('code')==101 and len(params)>4 and isinstance(params[4],str) and params[4].strip():
                    loc=Location(file=rel,kind='loose',fmt='json',ptr=format_path(parts+['parameters',4]))
                    upstream.entries.append(Entry(id=make_id_unique(params[4],loc),text=params[4],locations=[loc]))
                for key,value in node.items():speakers(value,parts+[key],rel)
            elif isinstance(node,list):
                for index,value in enumerate(node):speakers(value,parts+[index],rel)
        for path in rpgmaker.data_files(rpgmaker.is_rpgmaker(snapshot)):
            speakers(json.loads(path.read_text(encoding='utf-8-sig')),[],path.relative_to(snapshot).as_posix())
    rows=[]; docs={}
    for entry in upstream.entries:
        loc=entry.locations[0]
        parts=parse_path(loc.ptr) if loc.fmt=='json' else []
        usage='text'; kind='string'; block='labels'; speaker='unknown';command_code=None
        if parts and parts[-1] in ('description','profile','message1','message2','message3','message4'):usage='text'
        if loc.span:
            usage='dialogue';kind='dialogue'
        if 'parameters' in parts:
            position=parts.index('parameters'); head=parts[:position]
            if loc.file not in docs:docs[loc.file]=json.loads((snapshot/loc.file).read_text(encoding='utf-8-sig'))
            command=get_by_path(docs[loc.file],format_path(head))
            if isinstance(command,dict) and isinstance(command.get('code'),int) and head and isinstance(head[-1],int):
                code=command['code'];command_code=code;listptr=format_path(head[:-1]); commands=get_by_path(docs[loc.file],listptr)
                index=head[-1]
                markers=[n for n,c in enumerate(commands[:index+1]) if c.get('code') in (101,102,105,111,117,118,119,402,403,411,412)]
                # Start-message headers are not story boundaries. Branch/control flow is.
                boundaries=[n for n in markers if commands[n].get('code') not in (101,105)]
                block=listptr+':'+str(boundaries[-1] if boundaries else 0)+':'+str(command.get('indent',0))
                headers=[n for n in markers if commands[n].get('code')==101]
                if headers:
                    parameters=commands[headers[-1]].get('parameters',[])
                    if len(parameters)>4:speaker=parameters[4] or 'unknown'
                if code==102:usage='choice'
                elif code in (320,324) or (code==101 and parts[-1]==4):usage='speaker_label'
        elif Path(loc.file).name=='Actors.json' and parts and parts[-1]=='name':usage='speaker_label'
        uid=hashlib.sha256((loc.file+'\0'+loc.ptr+'\0'+entry.text).encode()).hexdigest()[:16]
        entry.id=uid
        rows.append({'id':uid,'source':entry.text,'kind':kind,'usage':usage,'file':loc.file,
                     'block':block,'speaker':speaker,'source_file':loc.file,'source_line':len(rows)+1,
                     'location':loc.to_dict(),**({'rpg_command_code':command_code} if command_code is not None else {})})
    if not rows:raise ValueError('RPG Maker에서 번역할 텍스트를 찾지 못했습니다.')
    from rpgmaker_paths import PATH_FORMAT
    upstream.stats['rpt_path_format']=PATH_FORMAT
    upstream.game_root=str(snapshot); upstream.save(project/'data/rpgmaker-project.json')
    from rpgmaker_language import prepare_sources
    from rpgmaker_sources import bind_rows,refresh_usages
    mapping=prepare_sources(snapshot,cfg['source_language'],base=base.relative_to(source),files=manifest.keys())
    bound=bind_rows(rows,mapping)
    usage_updates=refresh_usages(rows)
    save_json(project/'data/rpg-source-language.json',dict(mapping.report(),**bound,entry_ids_preserved=True,translation_cache_modified=False,usage_updates=usage_updates))
    save_json(project/'data/catalog.json',rows)
    save_json(project/'data/catalog-format.json',{'engine':'rpgmaker','version':1,'display_identifier_policy':1})
    save_json(project/'data/inspection.json',{'engine':'rpgmaker','variant':variant,'entries':len(rows),'files':len(manifest),'original_modified':False})
    cfg.update(engine='rpgmaker',engine_label='RPG Maker '+variant,engine_variant=variant,rpg_base=base.relative_to(source).as_posix())
    save_json(project/'project.json',cfg)
    from rpgmaker_scope import refresh
    refresh(project,cfg)
    print(f'Catalog: {sum(r["kind"]=="dialogue" for r in rows)} dialogue + {sum(r["kind"]!="dialogue" for r in rows)} labels/choices',flush=True)


def recover_display_catalog(project,cfg,*,only_values=None):
    """One migration of prepared display locations; never import game media or clear cache."""
    formatfile=project/'data/catalog-format.json'
    metadata=json.loads(formatfile.read_text(encoding='utf-8-sig')) if formatfile.exists() else {'engine':'rpgmaker','version':1}
    if only_values is None and metadata.get('display_identifier_policy')==1:
        return {'added':0,'added_ids':[],'already_current':True}
    load()
    from gameloc.model import Project
    from rpgmaker_catalog_repair import recover_display_entries
    rows=json.loads((project/'data/catalog.json').read_text(encoding='utf-8-sig'))
    upstream=Project.load(project/'data/rpgmaker-project.json')
    report=recover_display_entries(project,cfg,rows,upstream,only_values=only_values)
    if report['added']:
        save_json(project/'data/catalog.json',rows)
        upstream.save(project/'data/rpgmaker-project.json')
        print(f'Display entries recovered: {report["added"]}',flush=True)
    if only_values is None:
        metadata['display_identifier_policy']=1;save_json(formatfile,metadata)
    save_json(project/'data/rpg-display-recovery.json',report)
    return report


class IncompleteTranslationError(ValueError):
    error_code='incomplete_translation'

    def __init__(self,missing,total):
        self.remaining=len(missing);self.total=total
        super().__init__(f'아직 번역하지 않은 항목이 {self.remaining}개 있습니다. 번역을 선택하면 저장된 항목을 유지하고 이어서 진행합니다.')


def render_inputs(project,cfg,*,rows=None,known=None):
    """Read and validate saved work once, before any staging/output writes."""
    from translation import cache,read_catalog
    if rows is None:rows=read_catalog(project)
    if known is None:known=cache(project,cfg)
    return {'rows':rows,'known':known,'missing':[row['id'] for row in rows if row['id'] not in known]}



def review_render_inputs(project,cfg,prepared,jobs):
    """Review failed rows just before output, then reload accepted cache rows."""
    if set(jobs)&{'run','translate','rebuild','failed'} and not prepared['missing']:
        from translation_review import run as review_translations
        review_translations(project,cfg,rows=prepared['rows'],known=prepared['known'])
        return render_inputs(project,cfg)
    return prepared


def incomplete_output(project,jobs,prepared):
    """A repair can finish without turning an unfinished game into a patch."""
    remaining=len(prepared['missing'])
    result={'status':'incomplete','reason':'incomplete_translation',
            'tasks':list(jobs),'total':len(prepared['rows']),
            'cached':len(prepared['rows'])-remaining,'remaining':remaining,
            'output_status':'skipped','check_status':'skipped' if 'check' in jobs else 'not_requested',
            'cache_preserved':True,'missing_ids':prepared['missing'][:24]}
    save_json(project/'data/rpg-task-status.json',result)
    if 'check' in jobs:
        save_json(project/'data/patch-validation.json',
                  dict(status='skipped',reason='incomplete_translation',remaining=remaining,
                       total=result['total'],report=str(project/'data/rpg-task-status.json')))
    from job_events import warning
    warning('incomplete_translation','아직 번역하지 않은 항목',item_ids=prepared['missing'],count=1,
            log_path=project/'data/rpg-task-status.json',
            detail=f'{remaining}개가 남아 결과 파일과 검사를 만들지 않았습니다. 번역을 선택해 이어서 진행하세요.')
    print(f'RPG Maker repair saved: {remaining} entries not yet translated; output and patch check skipped. Resume translation to continue.',flush=True)
    return result


def repair_failed(project,cfg):
    """One requested repair pass; unattempted rows stay pending for translation."""
    from translation import cache,read_catalog,_translate
    rows=read_catalog(project);known=cache(project,cfg)
    from rpgmaker_names import validation_config
    cfg=validation_config(cfg,rows,known)
    failures=[row for row in rows if known.get(row['id'],{}).get('status')=='source_fallback'
              or known.get(row['id'],{}).get('_rpg_residual_language')]
    if not failures:
        print('Failed repair: no failed entries',flush=True)
        return render_inputs(project,cfg,rows=rows,known=known)
    from runtime_recovery import run
    active=dict(cfg,_rpg_previous_failures={row['id']:known[row['id']].get('error') or 'untranslated Japanese text' for row in failures})
    from rpgmaker_output_policy import enabled
    if enabled(cfg):active['_rpg_previous_drafts']={row['id']:dict(known[row['id']]) for row in failures
                                                  if known[row['id']].get('status')=='partial_translation'}
    for row in failures:known.pop(row['id'],None)
    run(project,active,lambda launched:_translate(project,launched,rows=failures,known=known),lambda:len(known))
    return render_inputs(project,cfg,rows=rows,known=known)


def render(project,cfg,*,prepared=None):
    prepared=render_inputs(project,cfg) if prepared is None else prepared
    rows,known=prepared['rows'],prepared['known']
    if prepared['missing']:raise IncompleteTranslationError(prepared['missing'],len(rows))
    set_phase('render.prepare','번역 파일 준비 중')
    load()
    from gameloc.model import Project
    from gameloc.apply import apply, _reserved_names
    from gameloc.treepath import parse_path,get_by_path,format_path
    from gameloc.overlay import rm_register
    from translation import validate_entry
    from replacements import load_rules,replace_visible
    from output_paths import destination
    snapshot=project/'data/rpg-source'; stage=project/'staging'
    if not snapshot.is_dir():raise ValueError('RPG Maker 원문 작업 자료가 없습니다.')
    from rpgmaker_names import apply_names,validation_config
    manual={uid:dict(item) for uid,item in known.items() if item.get('status')=='manual_translation'}
    known,name_report=apply_names(project,cfg,rows,known)
    known.update(manual)
    cfg.update(validation_config(cfg,rows,known))
    save_json(project/'data/rpg-name-report.json',name_report)
    if name_report['updated_ids']:print(f'Name labels aligned: {len(name_report["updated_ids"])}',flush=True)
    manifest=json.loads((project/'data/source-manifest.json').read_text(encoding='utf-8'))
    for rel,digest in manifest.items():
        p=snapshot/rel
        if hashlib.sha256(p.read_bytes()).hexdigest()!=digest:raise ValueError('작업 원문이 변경되었습니다: '+rel)
        target=stage/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
    upstream=Project.load(project/'data/rpgmaker-project.json'); upstream.game_root=str(stage)
    from rpgmaker_paths import normalize_locations
    normalize_locations(upstream,snapshot)
    rules=load_rules(project/'replacements.json'); translated=0; failed=0; partial=0;validated_pairs={}
    entry_map={e.id:e for e in upstream.entries}
    for row in rows:
        item=known.get(row['id'])
        text=item['text'] if item.get('status')=='manual_translation' else replace_visible(item['text'],rules)[0]
        # cache() has validated unchanged text already. Only an actual user
        # replacement creates a new value requiring another check.
        errors=validate_entry(row['source'],dict(item,text=text),cfg,allow_partial=True) if text!=item['text'] else []
        if errors:raise ValueError(row['id']+': '+str(errors))
        validated_pairs[row['id']]=(row['source'],text)
        entry_map[row['id']].translations['ko']=text
        incomplete=item.get('status')=='partial_translation'
        translated+=text!=row['source'] and not incomplete;failed+=item.get('status')=='source_fallback'
        partial+=incomplete
    cfg['_rpg_render_known']=known;cfg['_rpg_render_validated']=validated_pairs
    # Generated plugins in staging are output, never source identifier evidence.
    reserved=set(upstream.identifiers)|_reserved_names(snapshot,upstream)
    cfg['_rpg_render_reserved_names']=sorted(reserved)
    manual_ids=set(manual) & set(entry_map)
    report=apply(upstream,'ko',game_root=stage,reserved_names=reserved,allow_empty_ids=manual_ids,manual_ids=manual_ids)
    save_json(project/'data/rpg-apply-report.json',report.__dict__)
    save_json(project/'data/rpg-translation-status.json',dict(total=len(rows),translated=translated,
        original_kept=failed,partial=partial,remaining_ids=[r['id'] for r in rows
            if known[r['id']].get('status') in ('source_fallback','partial_translation')]))
    if report.failures:raise ValueError('RPG Maker 적용 실패: '+'; '.join(report.failures))
    if report.risky:
        emit('notice',code='rpg_identifiers_preserved',count=len(report.risky),
             text=f'게임 동작을 위해 원문으로 유지한 항목: {len(report.risky)}개')
    # Final-display metadata belongs to these command locations, not a global
    # replacement dictionary. Native language hooks may otherwise translate it again.
    docs={}; messages=0;choices=0;choice_commands=[]
    for row in rows:
        if row['kind']!='dialogue' and row['usage']!='choice':continue
        rel=row['file']
        if rel not in docs:docs[rel]=json.loads((stage/rel).read_text(encoding='utf-8-sig'))
        location=entry_map[row['id']].locations[0]
        parts=parse_path(location.ptr);pos=parts.index('parameters')
        command=get_by_path(docs[rel],format_path(parts[:pos]))
        if row['usage']=='choice':
            if '_rpt_choice_translations' not in command:
                choice_commands.append(command)
                original_doc=json.loads((snapshot/rel).read_text(encoding='utf-8-sig'))
                command['_rpt_choice_translations']=list(command['parameters'][0])
                if cfg.get('show_reference',True):
                    command['_rpt_choice_originals']=list(get_by_path(original_doc,format_path(parts[:pos]))['parameters'][0])
                    choices+=1
            if cfg.get('show_reference',True):command['_rpt_choice_originals'][parts[-1]]=row['source']
            continue
        final=(known[row['id']]['text'] if row['id'] in manual else
               '\n'.join(get_by_path(docs[rel],ptr) for ptr in location.span) if location.span else command['parameters'][parts[-1]])
        if row['id'] in manual:command['_rpt_manual']=True
        command['_rpt_translation']=final
        if cfg.get('show_reference',True) and final!=row['source']:
            command['_rpt_original']=row['source'];messages+=1
    for command in choice_commands:
        if command.get('_rpt_choice_originals')==command['_rpt_choice_translations']:
            command.pop('_rpt_choice_originals');choices-=1
    changed=set(report.written)
    base=Path(cfg.get('rpg_base','.'))
    if cfg.get('rpg_actor_class_overrides'):
        from rpgmaker_class_repair import apply_overrides
        actor_rel=(base/'data/Actors.json').as_posix()
        class_rel=(base/'data/Classes.json').as_posix()
        actor_doc=docs.get(actor_rel)
        if actor_doc is None:actor_doc=json.loads((stage/actor_rel).read_text(encoding='utf-8-sig'))
        classes=json.loads((stage/class_rel).read_text(encoding='utf-8-sig'))
        actor_doc,manual_changes=apply_overrides(actor_doc,cfg,classes)
        if manual_changes:docs[actor_rel]=actor_doc
        save_json(project/'data/rpg-manual-data-report.json',{'actor_classes':manual_changes,'source_modified':False})
    for rel,doc in docs.items():
        (stage/rel).write_text(json.dumps(doc,ensure_ascii=False,separators=(',',':')),encoding='utf-8');changed.add(rel)
    base=Path(cfg.get('rpg_base','.')); pluginrel=base/'js/plugins/RPT_Bilingual.js'; registry=base/'js/plugins.js'
    if not (stage/registry).is_file():raise ValueError('js/plugins.js가 없습니다. 플러그인을 등록할 수 없습니다.')
    original_actors={}
    actors=snapshot/base/'data/Actors.json'
    if actors.exists():
        selected_names={}
        for row in rows:
            if Path(row['file']).name!='Actors.json':continue
            parts=parse_path(entry_map[row['id']].locations[0].ptr)
            if len(parts)==2 and isinstance(parts[0],int) and parts[1]=='name':selected_names[parts[0]]=row['source']
        for index,actor in enumerate(json.loads(actors.read_text(encoding='utf-8-sig'))):
            if isinstance(actor,dict):original_actors[str(actor['id'])]=selected_names.get(index,actor.get('name',''))
    params={'originalActors':original_actors,'textboxScale':cfg.get('textbox_scale') or 1,'referenceRatio':0.55}
    plugin='/* RenPyTranslator: local Korean translation, original reference below. */\nvar RPT_BilingualOptions = '+json.dumps(params,ensure_ascii=False)+';\n'+resource('rpgmaker_bilingual.js').read_text(encoding='utf-8')
    (stage/pluginrel).parent.mkdir(parents=True,exist_ok=True);(stage/pluginrel).write_text(plugin,encoding='utf-8')
    # Reuse Gameloc's plugin-list parser, registering our own presentation plugin.
    text=rm_register((stage/registry).read_text(encoding='utf-8-sig'),name='RPT_Bilingual')
    (stage/registry).write_text(text,encoding='utf-8');changed.update((pluginrel.as_posix(),registry.as_posix()))
    fontrel=base/'fonts/RPT-NanumSquareNeo.ttf';(stage/fontrel).parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(ROOT/'vendor/fonts/NanumSquareNeo-Regular.ttf',stage/fontrel);changed.add(fontrel.as_posix())
    license_rel=base/'fonts/RPT-NanumSquareNeo-OFL.txt';shutil.copy2(ROOT/'vendor/fonts/NanumSquareNeo-OFL.txt',stage/license_rel);changed.add(license_rel.as_posix())
    output,label=destination(project,cfg);output.mkdir(parents=True,exist_ok=True)
    set_phase('render.write','번역 파일 저장 중',len(changed),'files')
    for index,rel in enumerate(sorted(changed),1):
        target=output/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(stage/rel,target)
        progress('render.write',index,len(changed))
    import os
    save_json(output/'project-link.json',{'project':os.path.relpath(project,output),'engine':'rpgmaker'})
    (output/'APPLY.txt').write_text('이 폴더의 '+('www 폴더를 포함한 ' if base.as_posix()!='.' else '')+'data/js/fonts 내용을 같은 버전의 원본 게임 폴더에 복사하고 같은 이름의 파일을 덮어쓰세요.\n원본을 보관해 두세요. 게임 실행파일은 원래 것을 사용합니다.\nKorean dialogue + original reference (55%). Custom message plugins may require compatibility adjustments.\n',encoding='utf-8')
    save_json(project/'data/rpg-output.json',{'files':sorted(changed),'translated':translated,'failed_original_kept':failed,'partial':partial,'identifier_skips':len(report.risky),'bilingual_messages':messages,'bilingual_choice_groups':choices,'output':str(output)})
    archive=output/(label+'.zip')
    set_phase('package.write','결과 압축 중',len(changed),'files')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,allowZip64=True) as z:
        for index,rel in enumerate(sorted(changed),1):
            z.write(output/rel,rel)
            progress('package.write',index,len(changed))
        z.write(output/'APPLY.txt','APPLY.txt')
    progress('package.write',len(changed),len(changed),successful=translated,original_kept=failed,partial=partial)
    print(f'Rendered {len(rows)} entries; translated: {translated}; failed/original kept: {failed}; partial: {partial}; bilingual messages: {messages}; bilingual choice groups: {choices}',flush=True)
    print('Output folder: '+str(output),flush=True)


def main(args,parser,info):
    from automatic import resolve_project,settings
    from translate_game import project_lock
    from task_plan import FIXES,plan
    from output_paths import apply_options
    from model_runtime import model_session
    from translation import translate,cache,read_catalog
    from model_store import require_job_model
    from diagnostics import stage
    jobs=plan(args.tasks) if args.command=='tasks' else [args.command]
    if args.command=='repair':
        if not args.fix:parser.error('repair requires --fix')
        jobs=['font','names','failed'] if args.fix=='all' else [args.fix]
    if set(jobs)&{'run','retranslate','translate','sample','failed'}:
        args.model=require_job_model(jobs,args.model,runtime_id=getattr(args,'runtime_id',None))
    project,base=resolve_project(args.source,args.project,args.model,args.source_language,engine='rpgmaker')
    if getattr(args,'runtime_id',None) is not None:base['runtime_id']=args.runtime_id
    if set(jobs)&FIXES and not set(jobs)&{'run','retranslate','translate','sample'}:
        if not ((project/'data/catalog.json').is_file() and
                (project/'data/rpgmaker-project.json').is_file() and
                (project/'data/rpg-source').is_dir()):
            raise ValueError('기존 RPG Maker 작업 자료를 찾지 못했습니다. 먼저 번역을 진행하거나 기존 프로젝트·결과 폴더를 선택해 주세요.')
        if not (project/'data/translations.jsonl').is_file():
            raise ValueError('기존 번역 기록을 찾지 못했습니다. 먼저 번역을 진행하거나 번역이 저장된 프로젝트·결과 폴더를 선택해 주세요.')
    base=dict(base,engine='rpgmaker',engine_label=info['engine_label'])
    base.pop('_rpg_retranslation_policy',None)
    save_json(project/'project.json',base)
    print('Project: '+str(project),flush=True)
    from source_language import LANGUAGES
    print('Source language: '+LANGUAGES[base['source_language']]+'; output: 한국어',flush=True)
    with model_session(),project_lock(project):
        if 'retranslate' in jobs:
            project=comparison(project,base);jobs=['run' if j=='retranslate' else j for j in jobs]
            base=json.loads((project/'project.json').read_text(encoding='utf-8'))
        cfg=apply_options(project,settings(project,base),args.output,args.suffix,args.textbox_scale,args.show_reference)
        if jobs==['check']:
            from patch_validation import run as check_patch
            result=check_patch(project,cfg,output_root=getattr(args,'rpg_selected_output',None))
            print('Output folder: '+str(Path(result.get('report',project/'data/patch-validation.json')).parent),flush=True)
            if result.get('status')!='passed':raise RuntimeError('Patch check '+result.get('status','failed')+': '+str(result.get('report','data/patch-validation.json')))
            return
        stage('RPG Maker prepare',project,cfg)
        prepared=False
        if not (project/'data/catalog.json').exists():
            if not set(jobs)&{'run','prepare'}:raise ValueError('먼저 번역 또는 prepare를 실행하세요.')
            prepare(project,cfg);prepared=True
            base=json.loads((project/'project.json').read_text(encoding='utf-8'));cfg.update(base)
        if not prepared:
            display_recovery=(recover_display_catalog(project,cfg) if set(jobs)&{'run','translate','failed','rebuild','font','display','layout'}
                              else {'added_ids':[]})
            from rpgmaker_sources import refresh as refresh_sources
            refresh_sources(project,cfg,allow_original=args.source is not None and bool(set(jobs)&{'run','translate','sample','prepare'}))
        else:display_recovery={'added_ids':[]}
        if (project/'data/rpgmaker-project.json').exists() and jobs!=['names']:
            seed_defaults(project,cfg,recover_saved=bool(set(jobs)&{'run','translate','failed'}),
                          recovered_display_ids=display_recovery['added_ids'])
        prepared_output=None
        if set(jobs)&{'run','translate','sample','failed'}:
            from rpgmaker_scope import refresh
            refresh(project,cfg)
        if jobs==['prepare']:return
        if jobs==['status']:
            rows=read_catalog(project);known=cache(project,cfg)
            print(json.dumps({'engine':'rpgmaker','total':len(rows),'cached':sum(r['id'] in known for r in rows),'remaining':sum(r['id'] not in known for r in rows)}));return
        if jobs==['catalog']:
            print(project/'data/catalog.json');return
        if jobs==['verify-source']:
            manifest=json.loads((project/'data/source-manifest.json').read_text(encoding='utf-8'))
            changed=[rel for rel,digest in manifest.items() if not (project/'data/rpg-source'/rel).is_file() or hashlib.sha256((project/'data/rpg-source'/rel).read_bytes()).hexdigest()!=digest]
            if changed:raise ValueError('작업 원문이 변경되었습니다: '+', '.join(changed))
            print('Prepared RPG Maker source hashes match');return
        if jobs==['validate']:
            from translation import validate_entry
            rows=read_catalog(project);known=cache(project,cfg)
            from rpgmaker_names import validation_config
            cfg=validation_config(cfg,rows,known)
            for row in rows:
                if row['id'] not in known:raise ValueError('번역 캐시가 없는 항목: '+row['id'])
                errors=validate_entry(row['source'],known[row['id']],cfg,allow_partial=True)
                if errors:raise ValueError(row['id']+': '+str(errors))
            print(f'Validated {len(rows)} RPG Maker entries');return
        if set(jobs)&{'run','translate','sample','failed'}:
            from gpu_policy import configure
            cfg=configure(cfg)
            if set(jobs)&{'run','translate'}:
                from name_review_control import prepare_rpg,run as review_names
                stage('name preparation',project,cfg);prepare_rpg(project,cfg)
                stage('name confirmation',project,cfg);review_names(project,cfg)
            stage('RPG Maker translation',project,cfg)
            if set(jobs)&{'run','translate','sample'}:translate(project,cfg,'sample' in jobs)
            if 'failed' in jobs:prepared_output=repair_failed(project,cfg)
        if jobs==['sample']:return
        prepared=render_inputs(project,cfg) if prepared_output is None else prepared_output
        prepared=review_render_inputs(project,cfg,prepared,jobs)
        if prepared['missing'] and not set(jobs)&{'run','translate'} and set(jobs)&FIXES:
            incomplete_output(project,jobs,prepared)
            return
        stage('RPG Maker patch output',project,cfg);render(project,cfg,prepared=prepared)
        if 'check' in jobs or set(jobs)&{'run','translate','rebuild','names'}:
            from patch_validation import run as check_patch
            result=check_patch(project,cfg)
            if result.get('status')!='passed':raise RuntimeError('Patch check '+result.get('status','failed')+': '+str(result.get('report','data/patch-validation.json')))
        save_json(project/'data/rpg-task-status.json',
                  {'status':'completed','tasks':list(jobs),'total':len(prepared['rows']),
                   'cached':len(prepared['rows']),'remaining':0,'output_status':'written',
                   'check_status':'passed' if 'check' in jobs or set(jobs)&{'run','translate','rebuild','names'} else 'not_requested',
                    'cache_preserved':True,'partial':sum(prepared['known'][row['id']].get('status')=='partial_translation'
                                                      for row in prepared['rows'])})
        if (project/'data/retranslation.json').exists():
            from retranslation import complete
            complete(project)


def comparison(project,cfg):
    if not ((project/'data/catalog.json').is_file() and
            (project/'data/rpgmaker-project.json').is_file() and
            (project/'data/rpg-source').is_dir()):
        raise ValueError('재번역할 RPG Maker 작업 자료가 없습니다. 먼저 번역을 진행하거나 기존 프로젝트·결과 폴더를 선택해 주세요.')
    from datetime import datetime
    import os
    indexfile=project/'data/rpg-retranslation-runs.json'
    index=json.loads(indexfile.read_text(encoding='utf-8')) if indexfile.exists() else {}
    previous=index.get(cfg['model'])
    if previous:
        candidate=(project/previous).resolve()
        if candidate.parent!=project.parent:raise ValueError('Invalid comparison path')
        state=json.loads((candidate/'data/retranslation.json').read_text(encoding='utf-8'))
        if state['status']=='pending':return candidate
    stamp=datetime.now().strftime('%Y%m%d-%H%M%S-%f');target=project.with_name(project.name+'-retranslate-'+stamp)
    (target/'data').mkdir(parents=True)
    for name in ('rpg-source','catalog.json','catalog-format.json','rpgmaker-project.json','source-manifest.json','inspection.json','tool-options.json','name-review.json','name-proposals.json'):
        src=project/'data'/name;dst=target/'data'/name
        if src.is_dir():shutil.copytree(src,dst)
        elif src.is_file():shutil.copy2(src,dst)
    if (project/'replacements.json').exists():shutil.copy2(project/'replacements.json',target/'replacements.json')
    if (project/'names.json').exists():shutil.copy2(project/'names.json',target/'names.json')
    newcfg=dict(cfg,comparison_label='재번역-'+stamp);newcfg.pop('imported_cache_fingerprint',None)
    newcfg.pop('_rpg_retranslation_policy',None)
    save_json(target/'project.json',newcfg);save_json(target/'data/retranslation.json',{'status':'pending','model':cfg['model'],'created':stamp})
    index[cfg['model']]=os.path.relpath(target,project);save_json(indexfile,index)
    print('Comparison project: '+str(target),flush=True)
    return target


def seed_defaults(project,cfg,*,recover_saved=False,recovered_display_ids=()):
    """Keep upstream's conventional System.json Korean terms out of model calls."""
    load()
    from gameloc.model import Project
    from translation import cache,fingerprint,read_catalog
    from source_language import JAPANESE_MENUS
    from ui_policy import STANDARD
    known=cache(project,cfg);items=[]
    rows={row['id']:row for row in read_catalog(project)}
    from rpgmaker_names import validation_config
    cfg=validation_config(cfg,rows.values(),known)
    if recovered_display_ids:
        from rpgmaker_catalog_repair import reusable_display_records
        reused=reusable_display_records(list(rows.values()),known,recovered_display_ids)
        items.extend(reused);known.update((item['id'],item) for item in reused)
    recovered=[];remaining=None
    if recover_saved:
        from rpgmaker_saved_repair import saved_records
        recovered,remaining=saved_records(project,cfg,rows,known)
        items.extend(recovered);known.update((item['id'],item) for item in recovered)
    for entry in Project.load(project/'data/rpgmaker-project.json').entries:
        row=rows.get(entry.id,{'source':entry.text})
        text=entry.translations.get('ko')
        if text and entry.id not in known:
            items.append(dict(id=entry.id,source=row['source'],text=text,model='RPG Maker standard menu',fingerprint=fingerprint(cfg)))
        elif row.get('usage') in ('ui_label','choice'):
            item=known.get(entry.id)
            original=item is None or item.get('status')=='source_fallback' or item.get('text') in row.get('source_aliases',[row['source']])
            standard=JAPANESE_MENUS.get(row['source']) or STANDARD.get(row['source'])
            if standard and original:
                items.append(dict(id=entry.id,source=row['source'],text=standard,model='RPG Maker standard menu',fingerprint=fingerprint(cfg)))
    resolved=set(known)|{item['id'] for item in items}
    for uid in recovered_display_ids:
        if uid in resolved:continue
        row=rows[uid]
        items.append(dict(id=uid,source=row['source'],text=row['source'],model=cfg['model'],
                          fingerprint=fingerprint(cfg),status='source_fallback',
                          error='Display text was excluded by legacy identifier protection'))
    if items:
        import os
        path=project/'data/translations.jsonl';temp=path.with_suffix('.jsonl.tmp')
        existing=path.read_bytes() if path.exists() else b''
        with temp.open('wb') as stream:
            stream.write(existing+''.join(json.dumps(item,ensure_ascii=False)+'\n' for item in items).encode('utf-8'))
            stream.flush();os.fsync(stream.fileno())
        temp.replace(path)
        standards=len(items)-len(recovered)
        if standards:print(f'RPG Maker standard terms: {standards} prefilled',flush=True)
    if remaining is not None:save_json(project/'data/failed-items.json',remaining)
    if recovered:print(f'RPG Maker saved outputs recovered: {len(recovered)}',flush=True)
