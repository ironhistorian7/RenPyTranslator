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
    print('Repair mode: '+', '.join(fixes)+'; keeping translation cache; no original-game access',flush=True)
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
    stage('validate',project,cfg);validate(project,cfg)
    stage('package',project,cfg);package(project,cfg)

def selected_tasks(args,parser):
    from diagnostics import stage
    from task_plan import FIXES,plan
    from output_paths import apply_options
    jobs=plan(args.tasks)
    if args.source and any(job in FIXES | {'language'} for job in jobs):
        parser.error('Standalone fixes require --project; select run to translate a source game')
    if args.project:
        project=args.project.resolve(strict=True).parent
        base=json.loads(args.project.read_text(encoding='utf-8-sig'))
        if args.source and Path(base['source']).resolve()!=args.source.resolve():parser.error('Source and project disagree')
        if args.model:base['model']=args.model
        elif 'routes' in jobs:
            from model_store import selected
            base['model']=selected()
    else:
        project,base=resolve_project(args.source,None,args.model)
    stage('selected tasks',project,base,tasks=jobs)
    if any(job in FIXES for job in jobs) and not (project/'data/translations.jsonl').exists():
        parser.error('This project has no translation cache. Run translation first, or select guides only.')
    options=[]
    for key,value in (('--output',args.output),('--suffix',args.suffix),('--textbox-scale',args.textbox_scale),('--model',args.model),
                      ('--language-corner',args.language_corner),('--language-margin',args.language_margin)):
        if value is not None:options.append(key+'='+str(value))
    if 'retranslate' in jobs:
        from retranslation import comparison
        with project_lock(project):
            project=comparison(project,base)
    if set(jobs) & {'run','retranslate'}:
        main(['run','--project',str(project/'project.json'),*options])
        base=json.loads((project/'project.json').read_text(encoding='utf-8-sig'))
    with project_lock(project):
        cfg=apply_options(project,base,args.output,args.suffix,args.textbox_scale)
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
        if 'retranslate' in jobs:
            from retranslation import complete
            complete(project)


def main(argv=None):
    parser=argparse.ArgumentParser(description='Local Korean patches for RenPy; no default action',
        formatter_class=argparse.RawDescriptionHelpFormatter,epilog='''Commands:
  run       Prepare, translate, and package an explicitly selected game
  retranslate Translate a prepared project afresh into a separate comparison result
  prepare   Prepare a project without translation
  repair    Repair an existing project; --fix is REQUIRED
  answers   Add answers to in-game input prompts; no translation or model
  routes    Choice effects + scenes; local model only for missing summaries
  language  Add/update language panel only; no model/game execution
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

No command, or repair without --fix: help only, no project access.''')
    parser.add_argument('command',nargs='?',choices=['run','retranslate','analyze','rebuild','repair','status','review','verify-source','answers','routes','language','tasks',
        'prepare','catalog','sample','translate','render','preview','support','seal-stage','validate','package'])
    parser.add_argument('--source',type=Path,help='The one game folder you authorize reading')
    parser.add_argument('--project',type=Path,help='Optional existing project path; not required for new games')
    parser.add_argument('--model',help='Installed tool-local model; otherwise use the AI Model tab selection')
    parser.add_argument('--fix',choices=['all','font','names','failed','display','layout'],help='required repair scope; omission prints help')
    from task_plan import TASKS
    parser.add_argument('--tasks',nargs='+',choices=list(TASKS),help='Explicit tasks; required with tasks command')
    parser.add_argument('--output',help='Output root (default: project beside the tool); relative to the tool')
    parser.add_argument('--suffix',help='Output folder suffix (default: -kr)')
    parser.add_argument('--textbox-scale',help='default: original game height, or a multiplier such as 1.2')
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
    from model_store import needs_model,require_selected
    jobs=args.tasks if args.command=='tasks' else (['names','failed'] if args.command=='repair' and args.fix=='all' else [args.fix] if args.command=='repair' else [args.command])
    if needs_model(jobs):args.model=require_selected(args.model)
    if args.project and args.project.is_dir():
        link=args.project/'project-link.json'
        if link.exists():args.project=(args.project/json.loads(link.read_text(encoding='utf-8'))['project']).resolve()
        args.project=args.project/'project.json'
    if args.language_margin is not None and not 0<=args.language_margin<=300:parser.error('--language-margin must be 0..300')
    if args.command=='language' and not args.project:parser.error('language requires --project (existing project or output folder)')
    if args.command in ('tasks','retranslate','answers','routes','language'):
        if args.command!='tasks':args.tasks=[args.command]
        selected_tasks(args,parser);return
    from output_paths import apply_options
    if args.command=='repair':
        if not args.project or args.source:parser.error('repair requires --project and does not read --source')
        project=args.project.resolve(strict=True).parent
        base=json.loads(args.project.read_text(encoding='utf-8-sig'))
        stage('repair',project,base)
        if args.model:base['model']=args.model
        with project_lock(project):
            base=apply_options(project,base,args.output,args.suffix,args.textbox_scale)
            from language_panel import options as panel_options
            base=panel_options(project,base,args.language_corner,args.language_margin)
            repair(project,base,args.fix)
        return
    project,base=resolve_project(args.source,args.project,args.model)
    stage(args.command,project,base)
    print('Project: '+str(project),flush=True)
    with model_session(), project_lock(project):
        base=apply_options(project,base,args.output,args.suffix,args.textbox_scale)
        from language_panel import options as panel_options
        base=panel_options(project,base,args.language_corner,args.language_margin)
        if args.command in ('run','prepare','rebuild','validate','package') and (project/'data/catalog.json').exists():
            from engine import verify_prepared_project
            verify_prepared_project(project)
            base=dict(base,_prepared_snapshot=True)
            print('Resuming saved project: keeping staging and cached translations; current source changes are not imported.',flush=True)
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
                if not (project/'data/static-screen-literals.json').exists():
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
        if args.command=='run':
            print('Direct translation: skipping full pre-analysis',flush=True)
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
