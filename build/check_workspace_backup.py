"""Exercise the actual Backup class against invented state, without loading a game."""
import ast
import importlib.util
import json
from pathlib import Path
import pickle
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / 'data/projects/onsengame-1.55-pc/staging/renpy/__init__.py'


def original_transfn(name):
    raise IOError(name)


def original_listdirfiles(*args, **kwargs):
    return []


def original_index_archives():
    return None


class EnginePickle:
    PROTOCOL = pickle.HIGHEST_PROTOCOL

    @staticmethod
    def dumps(value, highest=False):
        return pickle.dumps(value, pickle.HIGHEST_PROTOCOL if highest else 2)

    loads = staticmethod(pickle.loads)


def main():
    tree = ast.parse(ENGINE.read_text(encoding='utf-8'))
    selected = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == 'Backup':
            selected.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in
                ('backup_blacklist', 'type_blacklist', 'name_blacklist') for t in node.targets):
            selected.append(node)
    assert len(selected) == 4
    loader = types.ModuleType('renpy.loader')
    loader.archives = []
    loader.lower_map = {}
    loader.loadable_cache = {}
    loader.transfn = original_transfn
    loader.listdirfiles = original_listdirfiles
    loader.index_archives = original_index_archives
    helper_path = ROOT / 'app/workspace_runtime.py'
    spec = importlib.util.spec_from_file_location('rpt_workspace_assets', helper_path)
    helper = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = helper
    spec.loader.exec_module(helper)
    env = {'_object': object, 'types': types, 'pickle': EnginePickle,
           'sys': types.SimpleNamespace(modules={'renpy.loader': loader, spec.name: helper})}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(ENGINE), 'exec'), env)
    backup_class = env['Backup']

    def old_install():
        def index_archives():
            return None
        return index_archives

    loader.index_archives = old_install()
    try:
        backup_class()
    except (TypeError, AttributeError, pickle.PicklingError):
        baseline_failed = True
    else:
        raise AssertionError('The old closure must fail the engine snapshot')
    loader.index_archives = original_index_archives
    results = []
    with tempfile.TemporaryDirectory() as temp:
        assets = Path(temp)
        for source_language, asset_name in [('english', 'example.png'), ('japanese', '\u4f8b.png')]:
            (assets / asset_name).write_bytes(b'invented media')
            engine = types.SimpleNamespace(loader=loader, config=types.SimpleNamespace(archives=[]))
            settings = {'source_game': str(assets), 'source_root': str(assets),
                        'loose_assets': [asset_name], 'archives': [], 'original_archive_scripts': []}
            helper._state = None
            loader.transfn = original_transfn
            loader.listdirfiles = original_listdirfiles
            loader.index_archives = original_index_archives
            helper.install(engine, settings)
            snapshot = backup_class()
            loader.transfn = original_transfn
            loader.extra_after_backup = True
            snapshot.restore()
            assert not hasattr(loader, 'extra_after_backup')
            assert loader.transfn is helper.transfn
            assert loader.transfn(asset_name) == str(assets / asset_name)
            assert (str(assets), asset_name) in loader.listdirfiles()
            loader.index_archives()
            results.append({'source_language': source_language, 'backup_restore': 'passed'})
    print(json.dumps({'python': sys.version, 'original_closure_reproduced': baseline_failed,
                      'actual_engine_Backup': str(ENGINE), 'results': results}, indent=2))


if __name__ == '__main__':
    main()
