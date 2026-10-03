# -*- coding: utf-8 -*-
"""Python 2.7/3 compatible asset bridge, active only in the owned engine child.

External scripts are never discovered or compiled. Original assets are opened
for reading by Ren'Py; generated code/cache/logs remain in staging/data.
"""
import json
import os
import sys

SCRIPT_SUFFIXES = ('.rpy', '.rpyc', '.rpym', '.rpymc', '.py', '.pyc', '.pyo', '.rpe')
_state = None


def transfn(name):
    state = _state
    try:
        return state['original_transfn'](name)
    except Exception:
        normalized = name.replace('\\', '/').lstrip('/')
        relative = state['assets'].get(normalized.lower())
        if relative is not None:
            path = os.path.join(state['game'], relative.replace('/', os.sep))
            if os.path.isfile(path):
                return path
        if os.path.normcase(os.path.abspath(name)) in state['archive_paths'] and os.path.isfile(name):
            return name
        raise


def listdirfiles(*args, **kwargs):
    state = _state
    loader = state['loader']
    rows = list(state['original_listdirfiles'](*args, **kwargs))
    # Old indexers build filename caches before returning their indexes.
    rows = [(directory, name) for directory, name in rows
            if not (directory is None and name.lower().endswith(SCRIPT_SUFFIXES)
                    and name not in state['original_scripts']
                    and not (state['owned_game'] and os.path.isfile(os.path.join(state['owned_game'], name))))]
    include_game = kwargs.get('game', args[1] if len(args) > 1 else True)
    if not include_game:
        return rows
    seen = set(name for _, name in rows)
    for name in state['settings']['loose_assets']:
        if name in seen or name.lower().endswith(SCRIPT_SUFFIXES):
            continue
        rows.append((state['game'], name)); seen.add(name)
        loader.loadable_cache[name.lower()] = True
        loader.lower_map[name.lower()] = name
    return rows


def index_archives():
    state = _state
    loader = state['loader']
    if hasattr(loader, 'arc_files'):
        known = set(os.path.normcase(os.path.abspath(row[2])) for row in loader.arc_files)
        for path in state['archives']:
            if os.path.normcase(os.path.abspath(path)) not in known:
                loader.arc_files.append((os.path.basename(path)[:-4], '.rpa', path))
    else:
        for path in state['archives']:
            if path[:-4] not in state['renpy'].config.archives:
                state['renpy'].config.archives.append(path[:-4])
    result = state['original_index']()
    for path, index in loader.archives:
        physical = path if path.lower().endswith('.rpa') else path + '.rpa'
        if os.path.normcase(os.path.abspath(physical)) in state['archive_paths']:
            for name in list(index):
                text = name.decode('utf-8') if isinstance(name, bytes) else name
                if text.lower().endswith(SCRIPT_SUFFIXES):
                    del index[name]
    return result


def install(renpy, settings):
    global _state
    loader = renpy.loader
    if _state is not None and _state['loader'] is loader and loader.index_archives is index_archives:
        return
    original_transfn = loader.transfn
    original_listdirfiles = loader.listdirfiles
    original_index = loader.index_archives
    game = settings['source_game']
    assets = dict((name.lower(), name) for name in settings['loose_assets'])
    archives = [os.path.join(settings['source_root'], name.replace('/', os.sep))
                for name in sorted(settings['archives'],reverse=True) if os.path.basename(name) != '_rpt_original_scripts.rpa']
    archive_paths = set(os.path.normcase(os.path.abspath(p)) for p in archives)
    owned_game = os.path.join(os.path.dirname(settings['_record']), '..', 'staging', 'game') if '_record' in settings else None
    original_scripts = set(settings.get('original_archive_scripts', []))

    # This state is deliberately outside renpy.* modules, which Ren'Py pickles.
    _state = dict(loader=loader, renpy=renpy, settings=settings,
                  original_transfn=original_transfn, original_listdirfiles=original_listdirfiles,
                  original_index=original_index, game=game, assets=assets, archives=archives,
                  archive_paths=archive_paths, owned_game=owned_game, original_scripts=original_scripts)
    loader.transfn = transfn
    loader.listdirfiles = listdirfiles
    loader.index_archives = index_archives


def main():
    import runpy
    import types
    # run_path temporarily replaces __main__. Register the callbacks under a
    # stable non-renpy module before the launcher's engine backup runs.
    module_name = 'rpt_workspace_assets'
    bridge = sys.modules.get(module_name)
    if bridge is None:
        bridge = types.ModuleType(module_name)
        bridge.__file__ = os.path.abspath(__file__)
        sys.modules[module_name] = bridge
        with open(__file__, 'rb') as stream:
            exec(compile(stream.read(), __file__, 'exec'), bridge.__dict__)
    try:
        import builtins
    except ImportError:
        import __builtin__ as builtins
    with open(sys.argv[1], 'rb') as stream:
        settings = json.loads(stream.read().decode('utf-8'))
    settings['_record'] = os.path.abspath(sys.argv[1])
    launcher = sys.argv[2]
    sys.argv = [launcher] + sys.argv[3:]
    sys.path.insert(0, os.path.dirname(launcher))
    original_import = builtins.__import__
    installed = [False]

    def import_with_assets(*args, **kwargs):
        module = original_import(*args, **kwargs)
        loader = sys.modules.get('renpy.loader')
        if not installed[0] and loader is not None and all(hasattr(loader, name) for name in
                      ('transfn', 'listdirfiles', 'index_archives', 'archives', 'lower_map', 'loadable_cache')):
            installed[0] = True
            bridge.install(sys.modules['renpy'], settings)
            builtins.__import__ = original_import
        return module

    builtins.__import__ = import_with_assets
    try:
        runpy.run_path(launcher, run_name='__main__')
    finally:
        builtins.__import__ = original_import


if __name__ == '__main__':
    main()
