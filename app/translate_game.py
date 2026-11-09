"""CLI for producing copy-and-paste patches. Playing needs only the game exe."""
import argparse
from contextlib import contextmanager
import json
import sys
from pathlib import Path
from engine import prepare, verify_source, seal_stage
from translation import catalog, translate, render, validate, read_catalog, cache
from packaging import install_support, package
from automatic import resolve_project, settings, analyze, inspection, add_literal_templates, presentation, review
from model_runtime import model_session
from hy_backend import is_hy

def repair(project,cfg,fix):
    """User-triggered repair; preserve raw translations and never access the source game."""
    import hashlib
    import zipfile
    from datetime import datetime
    from engine import save_json,engine_command
    from task_plan import FIXES,plan
    fixes=plan(FIXES if fix=='all' else ([fix] if isinstance(fix,str) else fix))
    scope=next(k for k in ('font','names','display','failed','layout') if k in fixes)
    cfg=dict(cfg,_repair=True,_reuse_completed=True,_reuse_previous_models=True,_fix=scope)
    if is_hy(cfg):cfg.update(num_ctx=16384)
    cachefile=project/'data/translations.jsonl'
    before=hashlib.sha256(cachefile.read_bytes()).hexdigest()
    output=project/'output';output.mkdir(exist_ok=True)
    backups=output/'backups';backups.mkdir(exist_ok=True)
    backup=backups/('before-repair-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.zip')
    stage=project/'staging'
    with zipfile.ZipFile(backup,'w',zipfile.ZIP_DEFLATED) as z:
        paths=[p for p in (stage/'game/tl'/cfg['language']).rglob('*') if p.is_file()
               and p.suffix in ('.rpy','.json','.ttf','.otf','.txt')]+[stage/'game'/n for n in ('zz_rpt_korean.rpy','zz_rpt_names.rpy','zz_rpt_layout.rpy','zz_rpt_reference.rpy','zz_rpt_hints.rpy','zz_rpt_language.rpy')]
        for p in paths:
            if p.exists():z.write(p,p.relative_to(stage))
    print('Repair: '+', '.join(fixes),flush=True)
    names_report={'model_calls':0};failed_report={'model_calls':0}
    changed_ids=set()
    if 'failed' in fixes:
        from failed_repair import run as fix_failed
        changed,failed_report=fix_failed(project,cfg)
        changed_ids.update(changed)
    if 'names' in fixes:
        from name_translation import run as fix_names
        changed,names_report=fix_names(project,cfg)
        changed_ids.update(changed)
    if not {'font','display'}.intersection(fixes):cfg['_render_ids']=changed_ids
    if fixes==['layout']:
        cfg['_render_ids']=set()
        from layout_policy import install as install_layout
        install_layout(project,cfg)
    else:
        cfg['_reference_guard']=True
        from display_policy import install as install_reference
        install_reference(project,cfg,refresh_choices=False)
        render(project,cfg)
    if 'font' in fixes:install_support(project,cfg)
    else:
        if 'names' in fixes:
            from name_translation import install_support as install_names
            from replacements import effective_cache
            install_names(project,cfg,read_catalog(project),effective_cache(project,cfg,apply_ui=False))
        if 'failed' in fixes:
            from failed_repair import refresh_choices
            refresh_choices(project,cfg,changed_ids)
        if 'display' in fixes:
            from display_policy import install as install_reference
            install_reference(project,cfg)
        if {'display','layout'}.intersection(fixes) and fixes!=['layout']:
            from layout_policy import install as install_layout
            install_layout(project,cfg)
        if {'display','layout','names'}.intersection(fixes):
            from story_hints import restore as restore_hints
            restore_hints(project,cfg)
    # Compile only. No lint, story traversal or retranslating the catalog.
    engine_command(project,['compile'],'repair-compile.log')
    after=hashlib.sha256(cachefile.read_bytes()).hexdigest()
    if after!=before:raise RuntimeError('Translation cache changed during offline repair')
    save_json(project/'data/validation.json',{'errors':[],'mode':'repair',
        'checks':['engine compile','cache hash unchanged','rendered output matches preserved cache plus UI policy'],
        'full_translation_review':False})
    package(project,cfg)
    save_json(output/'repair-report.json',{'cache_sha256_before':before,'cache_sha256_after':after,
        'cache_unchanged':True,'fix':fix,'model_calls':names_report['model_calls']+failed_report['model_calls'],
        'failed_repair':failed_report,
        'original_game_accessed':False,'backup':str(backup.relative_to(output))})

@contextmanager
def project_lock(project):
    import msvcrt
    with (project/'data/pipeline.lock').open('a+b') as f:
        f.seek(0)
        try:msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:raise SystemExit('This project is already being processed')
        try:yield
        finally:f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)

def rebuild(project,cfg):
    from diagnostics import stage
    stage('presentation',project,cfg)
    # Entirely offline: no model, analysis, translation or source extraction.
    presentation(project,cfg)
    cfg=dict(cfg,_reference_guard=True)
    from display_policy import install as install_reference
    install_reference(project,cfg,refresh_choices=False)
    stage('render',project,cfg);render(project,cfg)
    stage('install support',project,cfg);install_support(project,cfg)
    stage('validate',project,cfg)
    try:validate(project,cfg)
    except RuntimeError as exc:
        if not str(exc).startswith('Engine exit '):raise
        from patch_validation import run as check_patch
        checked=check_patch(project,cfg,mode='compile')
        if checked.get('status')!='passed' or not any(item.get('status')=='applied' for item in checked.get('repairs',[])):
            raise
        from engine import save_json
        save_json(project/'data/validation.json',{'entries':len(read_catalog(project)),'errors':[],
            'mode':'paired_patch_check','report':checked.get('report')})
    stage('package',project,cfg);package(project,cfg)


def check_and_export(project,cfg):
    from patch_validation import run as check_patch
    result=check_patch(project,cfg)
    if result.get('status')=='passed' and any(item.get('status')=='applied' for item in result.get('repairs',[])):
        # The committed bytes have already passed the paired recheck. Publish
        # those bytes without rerendering or making translation/model requests.
        from engine import save_json
        save_json(project/'data/validation.json',{'entries':len(read_catalog(project)),'errors':[],
            'mode':'paired_patch_check','report':result.get('report')})
        package(project,dict(cfg,_prepared_snapshot=True))
    print('Output folder: '+str(Path(result.get('report',project/'data/patch-validation.json')).parent),flush=True)
    if result.get('status')!='passed':
        raise RuntimeError('Patch check '+result.get('status','failed')+': '+str(result.get('report','data/patch-validation.json')))
    return result

def selected_tasks(args,parser):
    from diagnostics import stage
    from task_plan import FIXES,plan
    from output_paths import apply_options
    jobs=plan(args.tasks)
    updated=None
    if 'update' in jobs:
        if not args.project or not args.source:parser.error('update requires --project OLD_PROJECT --source UPDATED_GAME')
        from game_engine import project_config
        from game_engine import require
        from project_update import update_project
        original,base=project_config(args.project)
        require(args.source,True,args.tasks)
        from model_store import require_selected
        args.model=require_selected(args.model)
        base['model']=args.model
        if args.runtime_id is not None:base['runtime_id']=args.runtime_id
        if args.source_language is not None:base['source_language']=args.source_language
        with project_lock(original.parent):
            updated,base,_=update_project(original.parent,base,args.source)
        args.project=updated/'project.json';args.source=None
        jobs=['run' if job=='update' else job for job in jobs]
    if args.project:
        project=args.project.resolve(strict=True).parent
        base=json.loads(args.project.read_text(encoding='utf-8-sig'))
        if args.source and Path(base['source']).resolve()!=args.source.resolve():parser.error('Source and project disagree')
        if args.model:base['model']=args.model
        if args.runtime_id is not None:base['runtime_id']=args.runtime_id
        elif 'routes' in jobs:
            from model_store import selected
            base['model']=selected()
    else:
        project,base=resolve_project(args.source,None,args.model)
    fixes_selected=any(job in FIXES for job in jobs)
    if args.source and (fixes_selected or 'language' in jobs):
        prepared=((project/'data/catalog.json').is_file() and
                  (project/'data/templates').is_dir() and
                  (project/'staging/game').is_dir())
        if not prepared:
            parser.error('기존 번역 작업을 찾지 못했습니다. 먼저 번역을 진행하거나 기존 프로젝트·결과 폴더를 선택해 주세요.')
    if fixes_selected and not (project/'data/translations.jsonl').is_file():
        parser.error('기존 번역 기록을 찾지 못했습니다. 먼저 번역을 진행하거나 번역이 저장된 프로젝트·결과 폴더를 선택해 주세요.')
    from source_language import apply as apply_language
    base=apply_language(project,base,args.source_language)
    stage('selected tasks',project,base,tasks=jobs)
    options=[]
    if args.show_reference is not None:options.append('--show-reference' if args.show_reference else '--hide-reference')
    for key,value in (('--output',args.output),('--suffix',args.suffix),('--textbox-scale',args.textbox_scale),('--model',args.model),
                      ('--language-corner',args.language_corner),('--language-margin',args.language_margin),('--source-language',args.source_language)):
        if value is not None:options.append(key+'='+str(value))
    if args.runtime_id is not None:options.append('--runtime-id='+args.runtime_id)
    if 'retranslate' in jobs:
        from retranslation import comparison
        with project_lock(project):
            project=comparison(project,base)
    if set(jobs) & {'run','retranslate'}:
        main(['run','--project',str(project/'project.json'),*options])
        base=json.loads((project/'project.json').read_text(encoding='utf-8-sig'))
    with project_lock(project):
        cfg=apply_options(project,base,args.output,args.suffix,args.textbox_scale,args.show_reference)
        from language_panel import options as panel_options,export as export_panel
        cfg=panel_options(project,cfg,args.language_corner,args.language_margin)
        fixes=[j for j in jobs if j in FIXES]
        if fixes:
            stage('repair',project,cfg);repair(project,cfg,fixes)
        kinds=[j for j in jobs if j in ('answers','routes')]
        if kinds:
            stage('in-game hints',project,cfg)
            from story_guides import run as guides
            guides(project,cfg,kinds)
        if 'language' in jobs:export_panel(project,cfg)
        if 'check' in jobs:
            stage('patch check',project,cfg)
            check_and_export(project,cfg)
        if 'retranslate' in jobs:
            from retranslation import complete
            complete(project)
        if updated is not None:
            from project_update import mark_complete
            mark_complete(updated)


def main(argv=None):
    parser=argparse.ArgumentParser(description='Local Korean patches for RenPy and RPG Maker MV/MZ',
        formatter_class=argparse.RawDescriptionHelpFormatter,epilog='''Commands:
  run       Prepare, translate, and package an explicitly selected game
  retranslate Translate a prepared project afresh into a separate comparison result
  update    Translate additions/changes: --project OLD_PROJECT --source UPDATED_GAME
  check     Check the installed patch in an isolated engine workspace
  prepare   Prepare a project without translation
  repair    Repair an existing project; --fix is REQUIRED
  answers   In-game input answers (beta)
  routes    Choice effects and scene hints (beta)
  language  Add/update the language panel
  tasks     Run only explicitly selected --tasks (checkbox equivalent)
  status    Show cached translation counts
  rebuild   Rebuild a patch from cached translations
  analyze / review / verify-source   Explicit analysis or checks

Repair scopes:
  --fix font    Fonts, standard menus, display; no model
  --fix names   Name transliteration and candidate corrections
  --fix failed  Indexed failures only; saved output first, local model if needed
  --fix display Reference text and default layout; no model
  --fix layout  Textbox height + cached choice-hint display; no model
  --fix all     All fixes (may load a local model)

Omit the command, or repair --fix, to display help.''')
    from app_version import version
    parser.add_argument('--version',action='version',version='RenPyTranslator '+version())
    parser.add_argument('command',nargs='?',choices=['run','retranslate','update','check','analyze','rebuild','repair','status','review','verify-source','answers','routes','language','tasks',
        'prepare','catalog','sample','translate','render','preview','support','seal-stage','validate','package'])
    parser.add_argument('--source',type=Path,help='Original or updated game folder')
    parser.add_argument('--project',type=Path,help='Optional existing project path; not required for new games')
    parser.add_argument('--model',help='Installed tool-local model; otherwise use the AI Model tab selection')
    parser.add_argument('--runtime-id',help='Installed Ollama runtime ID (see Settings)')
    from source_language import LANGUAGES,ALIASES
    parser.add_argument('--source-language',choices=list(LANGUAGES)+list(ALIASES),help='Original language: english/en or japanese/ja; existing projects retain their selection, new projects default to English')
    parser.add_argument('--fix',choices=['all','font','names','failed','display','layout'],help='required repair scope; omission prints help')
    from task_plan import TASKS
    parser.add_argument('--tasks',nargs='+',choices=list(TASKS),help='Explicit tasks; required with tasks command')
    parser.add_argument('--output',help='Output root (default: project beside the tool); relative to the tool')
    parser.add_argument('--suffix',help='Output folder suffix (default: -kr)')
    parser.add_argument('--textbox-scale',help='default: original game height, or a multiplier such as 1.2')
    reference=parser.add_mutually_exclusive_group()
    reference.add_argument('--show-reference',dest='show_reference',action='store_true',help='Show original dialogue below Korean (default)')
    reference.add_argument('--hide-reference',dest='show_reference',action='store_false',help='Show Korean only; untranslated fallbacks remain visible')
    parser.set_defaults(show_reference=None)
    parser.add_argument('--language-corner',choices=['left','right'],help='Language panel bottom corner (default: right)')
    parser.add_argument('--language-margin',type=int,help='Language panel edge margin in game pixels (0..300; default 12)')
    parser.add_argument('--language',choices=['korean','ko'],default='korean')
    parser.add_argument('--sample',type=int,default=0,help='analyze only: maximum dialogue entries; does not replace full analysis')
    args=parser.parse_args(argv)
    from diagnostics import stage
    stage(args.command or 'help',reset=True,source=str(args.source) if args.source else None)
    if args.command is None or (args.command=='repair' and not args.fix) or (args.command=='tasks' and not args.tasks):
        parser.print_help();return
    if args.fix and args.command!='repair':parser.error('--fix is only for repair')
    if args.tasks and args.command!='tasks':parser.error('--tasks is only for tasks')
    if args.sample<0 or (args.sample and args.command!='analyze'):parser.error('--sample is only for analyze and must be positive')
    # Update uses both paths intentionally. Prepare the revision before normal
    # engine routing so it cannot enter a fresh full translation by accident.
    if args.command=='update':args.command='tasks';args.tasks=['update']
    if args.command=='tasks' and args.tasks and 'update' in args.tasks:
        selected_tasks(args,parser);return
    from model_store import needs_model,require_selected
    jobs=args.tasks if args.command=='tasks' else (['names','failed'] if args.command=='repair' and args.fix=='all' else [args.fix] if args.command=='repair' else [args.command])
    if needs_model(jobs):args.model=require_selected(args.model)
    from game_engine import dispatch
    if dispatch(args,parser):return
    if args.project and args.project.is_dir():
        link=args.project/'project-link.json'
        if link.exists():args.project=(args.project/json.loads(link.read_text(encoding='utf-8'))['project']).resolve()
        args.project=args.project/'project.json'
    if args.language_margin is not None and not 0<=args.language_margin<=300:parser.error('--language-margin must be 0..300')
    if args.command=='language' and not (args.project or args.source):
        parser.error('language requires --project or --source for a previously prepared game')
    if args.command in ('tasks','retranslate','answers','routes','language','check'):
        if args.command!='tasks':args.tasks=[args.command]
        selected_tasks(args,parser);return
    from output_paths import apply_options
    if args.command=='repair':
        if not args.project or args.source:parser.error('repair requires --project and does not read --source')
        project=args.project.resolve(strict=True).parent
        base=json.loads(args.project.read_text(encoding='utf-8-sig'))
        from source_language import apply as apply_language
        base=apply_language(project,base,args.source_language)
        stage('repair',project,base)
        if args.model:base['model']=args.model
        if args.runtime_id is not None:base['runtime_id']=args.runtime_id
        with project_lock(project):
            base=apply_options(project,base,args.output,args.suffix,args.textbox_scale,args.show_reference)
            from language_panel import options as panel_options
            base=panel_options(project,base,args.language_corner,args.language_margin)
            repair(project,base,args.fix)
        return
    project,base=resolve_project(args.source,args.project,args.model,args.source_language)
    if args.runtime_id is not None:base['runtime_id']=args.runtime_id
    from source_language import normalize
    base=dict(base,source_language=normalize(base.get('source_language')))
    stage(args.command,project,base)
    print('Project: '+str(project),flush=True)
    from source_language import LANGUAGES
    print('Source language: '+LANGUAGES[base['source_language']]+'; output: 한국어',flush=True)
    with model_session(), project_lock(project):
        base=apply_options(project,base,args.output,args.suffix,args.textbox_scale,args.show_reference)
        from language_panel import options as panel_options
        base=panel_options(project,base,args.language_corner,args.language_margin)
        if args.command in ('run','prepare','rebuild','validate','package') and (project/'data/catalog.json').exists():
            from engine import verify_prepared_project
            verify_prepared_project(project)
            base=dict(base,_prepared_snapshot=True)
            print('Resuming saved project',flush=True)
        if args.command in ('run','prepare'):
            if not (project/'data/catalog.json').exists():
                if not (project/'data/templates').exists():
                    stage('inspect and prepare',project,base)
                    inspection(project,base)
                    prepare(project,base)
                catalog(project,base)
                add_literal_templates(project,base)
                seal_stage(project)
            else:
                if not (project/'data/catalog-format.json').exists():catalog(project,base)
                literal_policy=project/'data/literal-policy.json'
                if not (project/'data/static-screen-literals.json').exists() or (base['source_language']=='japanese' and
                        (not literal_policy.exists() or json.loads(literal_policy.read_text(encoding='utf-8')).get('version')!=2)):
                    add_literal_templates(project,base)
                    seal_stage(project)
            if args.command=='prepare':return
        if args.command == 'analyze':
            if is_hy(base):raise SystemExit('Hy is the translation-only default. Use run to translate directly; analyze requires an explicit general-purpose --model.')
            if not (project/'data/catalog.json').exists():raise ValueError('Run prepare or run first')
            if any('speaker' not in r for r in read_catalog(project) if r['kind']=='dialogue'):catalog(project,base)
            analyze(project,base,args.sample)
            if args.command=='analyze':return
        cfg=settings(project,base)
        if args.command in ('run','translate','sample','analyze'):
            from gpu_policy import configure
            cfg=configure(cfg)
        if args.command=='run':
            print('Translation: direct',flush=True)
            from name_translation import run as fix_names
            from failed_repair import run as fix_failed
            # Establish person spellings before new translation; correct existing candidates.
            stage('name preparation',project,cfg);fix_names(project,dict(cfg,_names_only=True))
            stage('translation',project,cfg);translate(project,cfg)
            stage('failed item repair',project,cfg);fix_failed(project,cfg)
            stage('name correction',project,cfg);fix_names(project,cfg)
            rebuild(project,cfg)
        elif args.command=='rebuild':rebuild(project,cfg)
        elif args.command=='review':review(project,cfg)
        elif args.command=='catalog':catalog(project,cfg)
        elif args.command in ('sample','translate'):translate(project,cfg,args.command=='sample')
        elif args.command=='render':render(project,cfg)
        elif args.command=='preview':render(project,cfg,True)
        elif args.command=='support':install_support(project,cfg)
        elif args.command=='seal-stage':seal_stage(project)
        elif args.command=='validate':validate(project,cfg)
        elif args.command=='package':package(project,cfg)
        elif args.command=='status':
            rows=read_catalog(project);known=cache(project,cfg)
            failed=sum(known.get(r['id'],{}).get('status')=='source_fallback' for r in rows)
            print(json.dumps({'model':cfg['model'],'cached':sum(r['id'] in known for r in rows),
                'failed_original_kept':failed,'recovered':sum(known.get(r['id'],{}).get('status')=='failure_recovered' for r in rows),
                'total':len(rows),'remaining':sum(r['id'] not in known for r in rows)},indent=2))
        elif args.command=='verify-source':print(json.dumps(verify_source(project,cfg),indent=2))

if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:
        print('Cancelled. Completed translations are saved.',file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        from diagnostics import report
        report(exc);sys.exit(1)
