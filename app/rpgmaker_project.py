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


def prepare(project, cfg):
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
    for sourcefile in sorted(paths):
        if sourcefile.suffix.lower() not in ('.json','.csv','.tsv','.txt','.js'):continue
        rel=sourcefile.relative_to(source); target=snapshot/rel
        target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(sourcefile,target)
        raw=target.read_bytes()
        if raw.startswith(b'\xef\xbb\xbf'):target.write_bytes(raw[3:])
        if target.suffix.lower()=='.json':
            try:json.loads(target.read_text(encoding='utf-8'))
            except (ValueError,UnicodeError) as exc:raise ValueError(f'RPG Maker JSON 읽기 실패: {rel}: {exc}') from exc
        manifest[rel.as_posix()]=hashlib.sha256(target.read_bytes()).hexdigest()
    save_json(project/'data/source-manifest.json',manifest)
    print(f'Copying RPG Maker text and settings: {len(manifest)} files; no images/audio/runtime',flush=True)
    upstream=extract(snapshot,dedup=False,languages=['ko'],source_lang='en' if cfg['source_language']=='english' else 'ja',progress=lambda s:print(s,flush=True))
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
        parts=parse_path(loc.ptr); usage='ui_label'; kind='string'; block='labels'; speaker='unknown'
        if parts and parts[-1] in ('description','profile','message1','message2','message3','message4'):usage='text'
        if loc.span:
            usage='dialogue';kind='dialogue'
        if 'parameters' in parts:
            position=parts.index('parameters'); head=parts[:position]
            if loc.file not in docs:docs[loc.file]=json.loads((snapshot/loc.file).read_text(encoding='utf-8-sig'))
            command=get_by_path(docs[loc.file],format_path(head))
            code=command.get('code'); listptr=format_path(head[:-1]); commands=get_by_path(docs[loc.file],listptr)
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
                     'location':loc.to_dict()})
    if not rows:raise ValueError('RPG Maker에서 번역할 텍스트를 찾지 못했습니다.')
    upstream.game_root=str(snapshot); upstream.save(project/'data/rpgmaker-project.json')
    save_json(project/'data/catalog.json',rows)
    save_json(project/'data/catalog-format.json',{'engine':'rpgmaker','version':1})
    save_json(project/'data/inspection.json',{'engine':'rpgmaker','variant':variant,'entries':len(rows),'files':len(manifest),'original_modified':False})
    cfg.update(engine='rpgmaker',engine_label='RPG Maker '+variant,engine_variant=variant,rpg_base=base.relative_to(source).as_posix())
    save_json(project/'project.json',cfg)
    from rpgmaker_scope import refresh
    refresh(project,cfg)
    print(f'Catalog: {sum(r["kind"]=="dialogue" for r in rows)} dialogue + {sum(r["kind"]!="dialogue" for r in rows)} labels/choices',flush=True)


def render(project,cfg):
    load()
    from gameloc.model import Project
    from gameloc.apply import apply
    from gameloc.treepath import parse_path,get_by_path,format_path
    from gameloc.overlay import rm_register
    from translation import cache,read_catalog,validate_entry
    from replacements import load_rules,replace_visible
    from output_paths import destination
    rows=read_catalog(project); known=cache(project,cfg); snapshot=project/'data/rpg-source'; stage=project/'staging'
    if not snapshot.is_dir():raise ValueError('RPG Maker 원문 작업 자료가 없습니다.')
    manifest=json.loads((project/'data/source-manifest.json').read_text(encoding='utf-8'))
    for rel,digest in manifest.items():
        p=snapshot/rel
        if hashlib.sha256(p.read_bytes()).hexdigest()!=digest:raise ValueError('작업 원문이 변경되었습니다: '+rel)
        target=stage/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
    upstream=Project.load(project/'data/rpgmaker-project.json'); upstream.game_root=str(stage)
    rules=load_rules(project/'replacements.json'); missing=[]; translated=0; failed=0
    entry_map={e.id:e for e in upstream.entries}
    for row in rows:
        item=known.get(row['id'])
        if item is None:missing.append(row['id']);continue
        text,_=replace_visible(item['text'],rules)
        errors=validate_entry(row['source'],dict(item,text=text),cfg)
        if errors:raise ValueError(row['id']+': '+str(errors))
        entry_map[row['id']].translations['ko']=text
        translated+=text!=row['source'];failed+=item.get('status')=='source_fallback'
    if missing:raise ValueError(f'번역 캐시가 없는 항목 {len(missing)}개입니다. 번역을 재개하세요.')
    report=apply(upstream,'ko',game_root=stage,progress=lambda s:print(s,flush=True))
    save_json(project/'data/rpg-apply-report.json',report.__dict__)
    if report.failures:raise ValueError('RPG Maker 적용 실패: '+'; '.join(report.failures))
    if report.risky:print(f'RPG Maker identifiers kept unchanged: {len(report.risky)}; see data/rpg-apply-report.json',flush=True)
    # Attach source references to message commands, never concatenate UI labels.
    docs={}; messages=0;choices=0
    for row in rows:
        if not cfg.get('show_reference',True):continue
        if row['kind']!='dialogue' and row['usage']!='choice':continue
        if entry_map[row['id']].translations['ko']==row['source']:continue
        rel=row['file']
        if rel not in docs:docs[rel]=json.loads((stage/rel).read_text(encoding='utf-8-sig'))
        parts=parse_path(row['location']['ptr']);pos=parts.index('parameters')
        command=get_by_path(docs[rel],format_path(parts[:pos]))
        if row['usage']=='choice':
            if '_rpt_choice_originals' not in command:
                original_doc=json.loads((snapshot/rel).read_text(encoding='utf-8-sig'))
                command['_rpt_choice_originals']=get_by_path(original_doc,format_path(parts[:pos]))['parameters'][0]
                choices+=1
            continue
        if command['parameters'][parts[-1]]==row['source'].split('\n')[0]:continue
        command['_rpt_original']=row['source']
        command['_rpt_translation']=entry_map[row['id']].translations['ko']
        messages+=1
    changed=set(report.written)
    for rel,doc in docs.items():
        (stage/rel).write_text(json.dumps(doc,ensure_ascii=False,separators=(',',':')),encoding='utf-8');changed.add(rel)
    base=Path(cfg.get('rpg_base','.')); pluginrel=base/'js/plugins/RPT_Bilingual.js'; registry=base/'js/plugins.js'
    if not (stage/registry).is_file():raise ValueError('js/plugins.js가 없습니다. 플러그인을 등록할 수 없습니다.')
    original_actors={}
    actors=snapshot/base/'data/Actors.json'
    if actors.exists():
        for actor in json.loads(actors.read_text(encoding='utf-8-sig')):
            if isinstance(actor,dict):original_actors[str(actor['id'])]=actor.get('name','')
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
    for rel in sorted(changed):
        target=output/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(stage/rel,target)
    import os
    save_json(output/'project-link.json',{'project':os.path.relpath(project,output),'engine':'rpgmaker'})
    (output/'APPLY.txt').write_text('이 폴더의 '+('www 폴더를 포함한 ' if base.as_posix()!='.' else '')+'data/js/fonts 내용을 같은 버전의 원본 게임 폴더에 복사하고 같은 이름의 파일을 덮어쓰세요.\n원본을 보관해 두세요. 게임 실행파일은 원래 것을 사용합니다.\nKorean dialogue + original reference (55%). Custom message plugins may require compatibility adjustments.\n',encoding='utf-8')
    save_json(project/'data/rpg-output.json',{'files':sorted(changed),'translated':translated,'failed_original_kept':failed,'identifier_skips':len(report.risky),'bilingual_messages':messages,'bilingual_choice_groups':choices,'output':str(output)})
    archive=output/(label+'.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,allowZip64=True) as z:
        for rel in sorted(changed):z.write(output/rel,rel)
        z.write(output/'APPLY.txt','APPLY.txt')
    print(f'Rendered {len(rows)} entries; translated: {translated}; failed/original kept: {failed}; bilingual messages: {messages}; bilingual choice groups: {choices}',flush=True)
    print('Output folder: '+str(output),flush=True)


def main(args,parser,info):
    from automatic import resolve_project,settings
    from translate_game import project_lock
    from task_plan import FIXES,plan
    from output_paths import apply_options
    from model_runtime import model_session
    from translation import translate,cache,read_catalog,_translate
    from model_store import require_selected,needs_model
    from diagnostics import stage
    jobs=plan(args.tasks) if args.command=='tasks' else [args.command]
    if args.command=='repair':
        if not args.fix:parser.error('repair requires --fix')
        if args.fix=='names':raise ValueError('RPG Maker 이름 보정은 아직 지원하지 않습니다.')
        jobs=['font','failed'] if args.fix=='all' else [args.fix]
    if set(jobs)&{'run','retranslate','translate','sample','failed'}:args.model=require_selected(args.model)
    project,base=resolve_project(args.source,args.project,args.model,args.source_language,engine='rpgmaker')
    if getattr(args,'runtime_id',None) is not None:base['runtime_id']=args.runtime_id
    if set(jobs)&FIXES:
        if not ((project/'data/catalog.json').is_file() and
                (project/'data/rpgmaker-project.json').is_file() and
                (project/'data/rpg-source').is_dir()):
            raise ValueError('기존 RPG Maker 작업 자료를 찾지 못했습니다. 먼저 번역을 진행하거나 기존 프로젝트·결과 폴더를 선택해 주세요.')
        if not (project/'data/translations.jsonl').is_file():
            raise ValueError('기존 번역 기록을 찾지 못했습니다. 먼저 번역을 진행하거나 번역이 저장된 프로젝트·결과 폴더를 선택해 주세요.')
    base=dict(base,engine='rpgmaker',engine_label=info['engine_label'])
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
            result=check_patch(project,cfg)
            print('Output folder: '+str(Path(result.get('report',project/'data/patch-validation.json')).parent),flush=True)
            if result.get('status')!='passed':raise RuntimeError('Patch check '+result.get('status','failed')+': '+str(result.get('report','data/patch-validation.json')))
            return
        stage('RPG Maker prepare',project,cfg)
        if not (project/'data/catalog.json').exists():
            if not set(jobs)&{'run','prepare'}:raise ValueError('먼저 번역 또는 prepare를 실행하세요.')
            prepare(project,cfg)
            base=json.loads((project/'project.json').read_text(encoding='utf-8'));cfg.update(base)
        if (project/'data/rpgmaker-project.json').exists():seed_defaults(project,cfg)
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
            for row in rows:
                if row['id'] not in known:raise ValueError('번역 캐시가 없는 항목: '+row['id'])
                errors=validate_entry(row['source'],known[row['id']],cfg)
                if errors:raise ValueError(row['id']+': '+str(errors))
            print(f'Validated {len(rows)} RPG Maker entries');return
        if set(jobs)&{'run','translate','sample','failed'}:
            from gpu_policy import configure
            cfg=configure(cfg)
            stage('RPG Maker translation',project,cfg)
            if 'failed' in jobs and not set(jobs)&{'run','translate','sample'}:
                rows=read_catalog(project);known=cache(project,cfg)
                failures=[r for r in rows if known.get(r['id'],{}).get('status')=='source_fallback']
                if failures:
                    from runtime_recovery import run
                    cfg=dict(cfg,_rpg_previous_failures={r['id']:known[r['id']].get('error','') for r in failures})
                    for row in failures:known.pop(row['id'],None)
                    run(project,cfg,lambda active:_translate(project,active,rows=failures,known=known),lambda:len(known))
                else:print('Failed repair: no failed entries',flush=True)
            else:translate(project,cfg,'sample' in jobs)
        if jobs==['sample']:return
        stage('RPG Maker patch output',project,cfg);render(project,cfg)
        if 'check' in jobs:
            from patch_validation import run as check_patch
            result=check_patch(project,cfg)
            if result.get('status')!='passed':raise RuntimeError('Patch check '+result.get('status','failed')+': '+str(result.get('report','data/patch-validation.json')))
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
    for name in ('rpg-source','catalog.json','catalog-format.json','rpgmaker-project.json','source-manifest.json','inspection.json','tool-options.json'):
        src=project/'data'/name;dst=target/'data'/name
        if src.is_dir():shutil.copytree(src,dst)
        elif src.is_file():shutil.copy2(src,dst)
    if (project/'replacements.json').exists():shutil.copy2(project/'replacements.json',target/'replacements.json')
    newcfg=dict(cfg,comparison_label='재번역-'+stamp);newcfg.pop('imported_cache_fingerprint',None)
    save_json(target/'project.json',newcfg);save_json(target/'data/retranslation.json',{'status':'pending','model':cfg['model'],'created':stamp})
    index[cfg['model']]=os.path.relpath(target,project);save_json(indexfile,index)
    print('Comparison project: '+str(target),flush=True)
    return target


def seed_defaults(project,cfg):
    """Keep upstream's conventional System.json Korean terms out of model calls."""
    load()
    from gameloc.model import Project
    from translation import cache,fingerprint
    known=cache(project,cfg);items=[]
    for entry in Project.load(project/'data/rpgmaker-project.json').entries:
        text=entry.translations.get('ko')
        if text and entry.id not in known:
            items.append(dict(id=entry.id,source=entry.text,text=text,model='RPG Maker standard menu',fingerprint=fingerprint(cfg)))
    if items:
        import os
        path=project/'data/translations.jsonl';temp=path.with_suffix('.jsonl.tmp')
        existing=path.read_bytes() if path.exists() else b''
        with temp.open('wb') as stream:
            stream.write(existing+''.join(json.dumps(item,ensure_ascii=False)+'\n' for item in items).encode('utf-8'))
            stream.flush();os.fsync(stream.fileno())
        temp.replace(path)
        print(f'RPG Maker standard terms: {len(items)} prefilled',flush=True)
