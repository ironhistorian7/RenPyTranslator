"""Cheap structural routing. Unsupported engines never create work or load AI."""
import json
import os
from pathlib import Path
import re
from gameloc_vendor import load

RPG_TASKS = ('run', 'retranslate', 'update', 'font', 'names', 'failed', 'display', 'layout', 'check')
KIRIKIRI_TASKS = ('run', 'retranslate', 'names', 'failed', 'display', 'check')
UNITY_TASKS = ('run', 'retranslate', 'failed', 'check')
_GAME_TASKS = {'rpgmaker': RPG_TASKS, 'kirikiri': KIRIKIRI_TASKS, 'unity': UNITY_TASKS}
_EXTRA_COMMANDS = {'prepare','status','rebuild','translate','sample','render','validate','package','catalog','verify-source','repair'}
_UNITY_COMMANDS = {'prepare','status','translate','sample','catalog','verify-source','repair'}


def detect(folder):
    root = Path(folder).resolve(strict=True)
    if root.is_file():
        if root.suffix.lower() != '.iso':
            raise ValueError('게임 폴더 또는 ISO 파일을 선택하세요.')
        from kirikiri_project import inspect_source
        return inspect_source(root) or {'engine': 'unknown', 'engine_label': '알 수 없는 형식', 'supported': False}
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
    if (root/'UnityPlayer.dll').is_file():
        from unity_assets import inspect_source
        info = inspect_source(root)
        if info is not None:
            return info
    if any(p.is_file() and p.suffix.lower() in ('.xp3', '.iso') for p in root.iterdir()):
        from kirikiri_project import inspect_source
        info = inspect_source(root)
        if info is not None:
            return info
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


def saved_check_project(source):
    """Find a check-only project using its registry, without probing the game."""
    from app_paths import ROOT
    from automatic import source_key

    source = Path(os.path.abspath(source))
    key = source_key(source)
    fallback = re.sub(r'[^\w. -]+', '_', source.name)
    for parent in (ROOT/'projects', ROOT/'data/projects'):
        registry = parent/'index.json'
        entries = json.loads(registry.read_text(encoding='utf-8-sig')) if registry.is_file() else {}
        relative = Path(entries.get(key, fallback))
        if relative.is_absolute() or '..' in relative.parts or not relative.parts:
            continue
        directory = (parent/relative).resolve()
        if not directory.is_relative_to(parent.resolve()):
            continue
        config = directory/'project.json'
        if not config.is_symlink() and config.is_file():
            _, saved = project_config(config)
            if saved.get('source') and Path(os.path.abspath(saved['source'])) == source:
                return config
    raise ValueError('패치 검사를 할 기존 프로젝트나 번역 결과 폴더를 선택하세요.')


def inspect(path, source=True, tasks=()):
    if source:
        result = detect(path)
    else:
        _, cfg = project_config(path)
        engine = cfg.get('engine','renpy')
        labels = {'kirikiri': 'KiriKiri', 'rpgmaker': 'RPG Maker', 'unity': 'Unity'}
        result = {'engine': engine, 'engine_label': cfg.get('engine_label', labels.get(engine, "Ren’Py")),
                  'supported': engine in _GAME_TASKS or engine == 'renpy'}
        if engine in ('kirikiri', 'unity') and cfg.get('game_name'):
            result['game_name'] = cfg['game_name']
    engine = result['engine']
    visible_tasks = _GAME_TASKS.get(engine)
    stop = result.get('stop_message')
    if not result['supported'] and not stop:
        stop = ('지원되는 게임 형식을 확인하지 못했습니다.' if engine == 'unknown'
                else result['engine_label']+' 게임입니다. 이 형식은 추후 지원 예정입니다.')
    if not stop and visible_tasks:
        extra = _UNITY_COMMANDS if engine == 'unity' else _EXTRA_COMMANDS
        unsupported = set(tasks)-set(visible_tasks)-extra
        if unsupported:
            from task_plan import TASKS
            stop = result['engine_label']+'에서 아직 지원하지 않는 작업입니다: '+', '.join(TASKS.get(t,t) for t in sorted(unsupported))
    return dict(result, stop_message=stop, supported_tasks=list(visible_tasks) if visible_tasks else None)


def require(path, source=True, tasks=()):
    result = inspect(path, source, tasks)
    if result['stop_message']:
        raise ValueError(result['stop_message'])
    return result


def dispatch(args, parser):
    if not args.source and not args.project:
        return False
    jobs = args.tasks if args.command=='tasks' else [args.command]
    if set(jobs or ()) == {'check'} and args.source and not args.project:
        args.project, args.source = saved_check_project(args.source), None
    info = require(args.source or args.project, bool(args.source), jobs)
    print('Game engine: '+info['engine_label'], flush=True)
    if info['engine']=='renpy':
        return False
    if args.project:
        selected = Path(args.project)
        if info['engine'] == 'rpgmaker' and selected.is_dir() and (selected/'project-link.json').is_file():
            args.rpg_selected_output = selected.resolve(strict=True)
        args.project, _ = project_config(args.project)
    if info['engine'] == 'kirikiri':
        from kirikiri_project import main
    elif info['engine'] == 'unity':
        from unity_project import main
    else:
        from rpgmaker_project import main
    main(args, parser, info)
    return True
