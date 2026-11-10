"""Cheap structural routing. Unsupported engines never create work or load AI."""
import json
from pathlib import Path
from gameloc_vendor import load

RPG_TASKS = ('run', 'retranslate', 'update', 'font', 'names', 'failed', 'display', 'layout', 'check')


def detect(folder):
    root = Path(folder).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('게임 폴더를 선택하세요.')
    load()
    from gameloc.engines import rpgmaker, tyrano, wolf
    # Keep the existing Ren'Py distribution check and processing path intact.
    if (root/'game').is_dir() and (root/'renpy').is_dir():
        return {'engine': 'renpy', 'engine_label': "Ren’Py", 'supported': True}
    base = rpgmaker.is_rpgmaker(root)
    if base is not None:
        variant = rpgmaker.variant(base)
        return {'engine': 'rpgmaker', 'engine_label': 'RPG Maker '+variant,
                'variant': variant, 'base': str(base.relative_to(root)), 'supported': variant in ('MV','MZ')}
    if tyrano.is_tyrano(root) is not None:
        engine, label = 'tyrano', 'TyranoScript'
    elif wolf.is_wolf(root) is not None:
        engine, label = 'wolf', 'WOLF RPG'
    elif next(root.glob('*_Data'), None) is not None or (root/'globalgamemanagers').is_file():
        engine, label = 'unity', 'Unity'
    elif (root/'resources/app.asar').is_file() or (root/'resources/app/package.json').is_file():
        engine, label = 'electron', 'Electron'
    elif any((root/name).is_file() for name in ('Game.rgss3a','Game.rgss2a','Game.rgssad')):
        engine, label = 'rpgmaker-legacy', 'RPG Maker 구형 버전'
    else:
        engine, label = 'unknown', '알 수 없는 형식'
    return {'engine': engine, 'engine_label': label, 'supported': False}


def project_config(path):
    path = Path(path)
    if path.is_dir():
        link = path/'project-link.json'
        if link.is_file():
            path = (path/json.loads(link.read_text(encoding='utf-8-sig'))['project']).resolve()
        path = path/'project.json'
    return path, json.loads(path.read_text(encoding='utf-8-sig'))


def inspect(path, source=True, tasks=()):
    if source:
        result = detect(path)
    else:
        _, cfg = project_config(path)
        engine = cfg.get('engine','renpy')
        result = {'engine': engine, 'engine_label': cfg.get('engine_label', "Ren’Py"),
                  'supported': engine in ('renpy','rpgmaker')}
    stop = None
    if not result['supported']:
        stop = ('지원되는 게임 형식을 확인하지 못했습니다.' if result['engine']=='unknown'
                else result['engine_label']+' 게임입니다. 이 형식은 추후 지원 예정입니다.')
    if not stop and result['engine']=='rpgmaker':
        unsupported = set(tasks)-set(RPG_TASKS)-{'prepare','status','rebuild','translate','sample','render','validate','package','catalog','verify-source','repair'}
        if unsupported:
            from task_plan import TASKS
            stop = 'RPG Maker에서 아직 지원하지 않는 작업입니다: '+', '.join(TASKS.get(t,t) for t in sorted(unsupported))
    return dict(result, stop_message=stop, supported_tasks=list(RPG_TASKS) if result['engine']=='rpgmaker' else None)


def require(path, source=True, tasks=()):
    result = inspect(path, source, tasks)
    if result['stop_message']:
        raise ValueError(result['stop_message'])
    return result


def dispatch(args, parser):
    if not args.source and not args.project:
        return False
    jobs = args.tasks if args.command=='tasks' else [args.command]
    info = require(args.source or args.project, bool(args.source), jobs)
    print('Game engine: '+info['engine_label'], flush=True)
    if info['engine']=='renpy':
        return False
    if args.project:
        args.project, _ = project_config(args.project)
    from rpgmaker_project import main
    main(args, parser, info)
    return True
