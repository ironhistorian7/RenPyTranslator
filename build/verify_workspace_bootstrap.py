"""Run only invented launcher fixtures through existing official SDK Python binaries."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from engine import interpreter_args

OUT = ROOT / 'build' / 'workspace-bootstrap-compat'
HELPER = ROOT / 'app' / 'workspace_runtime.py'

DRIVER = r'''# -*- coding: utf-8 -*-
import json, os, pickle, sys, types
if sys.version_info[0] < 3:
    import __builtin__ as builtins
else:
    import builtins
root = os.path.abspath(os.path.dirname(__file__))
helper_file = sys.argv[1]
original_main = sys.modules['__main__']
original_import = builtins.__import__
blocked = []
guard_module = types.ModuleType('_fixture_reduced_import')
guard_module.blocked = blocked
guard_module.original_import = original_import
sys.modules[guard_module.__name__] = guard_module
exec(compile("def guard(name, *args, **kwargs):\n"
    "    if name.split('.')[0] in ('runpy', 'pkgutil'):\n"
    "        blocked.append(name)\n"
    "        raise ImportError('intentionally unavailable optional stdlib: ' + name)\n"
    "    return original_import(name, *args, **kwargs)\n", '<synthetic-import-guard>', 'exec'), guard_module.__dict__)
guard = guard_module.guard
builtins.__import__ = guard
helper = types.ModuleType('_workspace_runtime_probe')
helper.__file__ = helper_file
sys.modules[helper.__name__] = helper
with open(helper_file, 'rb') as stream:
    exec(compile(stream.read(), helper_file, 'exec'), helper.__dict__)
checks = []
try:
    for forbidden in ('runpy', 'pkgutil'):
        try:
            __import__(forbidden)
        except ImportError:
            pass
        else:
            raise AssertionError('import guard did not block ' + forbidden)
    launcher = os.path.join(root, 'plain_launcher.py')
    sys.argv = [launcher, 'synthetic-stage', 'translate', 'korean', '--empty']
    helper.run_launcher(launcher)
    assert sys.modules['__main__'] is original_main
    checks += ['fresh_main_namespace', 'normal_argv', 'main_function_pickling', 'latin1_encoding_cookie', 'optimization_preserved', 'normal_main_restored']
    try:
        helper.run_launcher(os.path.join(root, 'error_launcher.py'))
    except ValueError as error:
        assert str(error) == 'synthetic-bootstrap-error'
    else:
        raise AssertionError('launcher exception suppressed')
    assert sys.modules['__main__'] is original_main
    checks += ['exception_propagated', 'exception_main_restored']
    try:
        helper.run_launcher(os.path.join(root, 'exit_launcher.py'))
    except SystemExit as error:
        assert error.code == 23
    else:
        raise AssertionError('launcher SystemExit suppressed')
    assert sys.modules['__main__'] is original_main
    checks += ['systemexit_propagated', 'systemexit_main_restored']
    launcher = os.path.join(root, 'fixture_launcher.py')
    record = os.path.join(root, 'workspace-files.json')
    sys.argv = [helper_file, record, launcher, root, 'translate', 'korean', '--empty']
    helper.main()
    assert sys.modules['__main__'] is original_main
    assert builtins.__import__ is guard
    checks += ['bridge_main_namespace', 'bridge_normal_argv', 'stable_callback_module', 'callback_pickling_protocols_0_2', 'loader_state_pickling_without_builtins', 'external_asset_reference', 'bridge_import_restored', 'bridge_main_restored']
    assert blocked == ['runpy', 'pkgutil']
    result = dict(status='passed', python=sys.version, executable=sys.executable,
                  optimize=sys.flags.optimize, checks=checks,
                  optional_import_probe=blocked, optional_imports_during_launch=[])
finally:
    builtins.__import__ = original_import
with open(os.path.join(root, 'result.json'), 'wb') as stream:
    stream.write(json.dumps(result, indent=2).encode('utf-8'))
print('PASS ' + str(len(checks)) + ' checks; Python ' + sys.version.split()[0])
'''

PLAIN = '''# -*- coding: latin-1 -*-
import os, pickle, sys
assert __name__ == '__main__'
assert sys.modules['__main__'].__dict__ is globals()
assert os.path.abspath(__file__) == os.path.abspath(sys.argv[0])
assert sys.argv[1:] == ['synthetic-stage', 'translate', 'korean', '--empty']
assert __package__ is None
assert __spec__ is None and __loader__ is None and __cached__ is None
assert not any(name in globals() for name in ('_state', 'transfn', 'install', 'run_launcher', 'bridge', 'settings'))
assert __debug__ == (not bool(sys.flags.optimize))
encoded_word = u'caf\xe9'
assert encoded_word == u'caf\\u00e9'
def named_fixture():
    return 7
for protocol in (0, 2):
    assert pickle.loads(pickle.dumps(named_fixture, protocol)) is named_fixture
'''

BRIDGE_LAUNCHER = '''# -*- coding: utf-8 -*-
import renpy.loader
import os, pickle, sys
assert renpy.__file__.startswith(os.path.dirname(__file__))
assert __name__ == '__main__' and sys.modules['__main__'].__dict__ is globals()
assert not any(name in globals() for name in ('_state', 'transfn', 'install', 'run_launcher', 'bridge', 'settings'))
assert sys.argv == [__file__, os.path.dirname(__file__), 'translate', 'korean', '--empty']
assert sys.modules['rpt_workspace_assets'].__name__ == 'rpt_workspace_assets'
for callback in (renpy.loader.transfn, renpy.loader.listdirfiles, renpy.loader.index_archives):
    assert callback.__module__ == 'rpt_workspace_assets'
    for protocol in (0, 2):
        assert pickle.loads(pickle.dumps(callback, protocol)) is callback
loader_state = dict((key, value) for key, value in vars(renpy.loader).items() if key != '__builtins__')
assert pickle.loads(pickle.dumps(loader_state, 2))['index_archives'] is renpy.loader.index_archives
expected = os.path.join(os.path.dirname(__file__), 'assets', 'image.png')
assert renpy.loader.transfn('image.png') == expected
assert ('image.png' in [name for directory, name in renpy.loader.listdirfiles()])
'''


def explicit_checks(source):
    """Keep fixture checks active under SDK interpreters that force -O."""
    output = []
    for line in source.splitlines():
        matched = re.fullmatch(r'(\s*)assert (.+)', line)
        if matched:
            indent, expression = matched.groups()
            line = indent + 'if not (' + expression + '): raise AssertionError(' + repr('fixture condition failed: ' + expression) + ')'
        output.append(line)
    return '\n'.join(output) + '\n'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    binaries = sorted((ROOT / 'build' / 'renpy-validation').glob('renpy-*-sdk/lib/*windows*/python.exe'))
    reports = []
    for binary in binaries:
        label = binary.parents[2].name + '-' + binary.parent.name
        fixture = OUT / label
        fixture.mkdir(exist_ok=True)
        (fixture / 'driver.py').write_text(explicit_checks(DRIVER), encoding='utf-8')
        (fixture / 'plain_launcher.py').write_bytes(explicit_checks(PLAIN).encode('latin-1'))
        (fixture / 'fixture_launcher.py').write_text(explicit_checks(BRIDGE_LAUNCHER), encoding='utf-8')
        (fixture / 'error_launcher.py').write_text("raise ValueError('synthetic-bootstrap-error')\n", encoding='ascii')
        (fixture / 'exit_launcher.py').write_text('raise SystemExit(23)\n', encoding='ascii')
        package = fixture / 'renpy'; package.mkdir(exist_ok=True)
        (package / '__init__.py').write_text('class _Config(object):\n    archives=[]\nconfig=_Config()\n', encoding='ascii')
        (package / 'loader.py').write_text('archives=[]\nlower_map={}\nloadable_cache={}\n'
            'def transfn(name):\n    raise IOError(name)\n'
            'def listdirfiles(common=True, game=True):\n    return []\n'
            'def index_archives():\n    return None\n', encoding='ascii')
        assets = fixture / 'assets'; assets.mkdir(exist_ok=True)
        (assets / 'image.png').write_bytes(b'invented asset content; not a real image')
        (fixture / 'workspace-files.json').write_text(json.dumps({
            'source_root': str(fixture), 'source_game': str(assets),
            'loose_assets': ['image.png'], 'archives': [], 'original_archive_scripts': []
        }), encoding='utf-8')
        command = [*interpreter_args(binary), str(fixture / 'driver.py'), str(HELPER)]
        started = time.monotonic()
        try:
            process = subprocess.run(command, cwd=fixture, capture_output=True,
                timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            result_file = fixture / 'result.json'
            result = json.loads(result_file.read_text(encoding='utf-8')) if process.returncode == 0 and result_file.exists() else {'status': 'failed'}
            result.update(returncode=process.returncode,
                stdout=process.stdout.decode('utf-8', errors='replace'),
                stderr=process.stderr.decode('utf-8', errors='replace'))
        except subprocess.TimeoutExpired as error:
            result = {'status': 'timeout', 'error': str(error)}
        result.update(sdk=binary.parents[2].name, runtime=binary.parent.name,
                      command=command, elapsed_seconds=round(time.monotonic() - started, 3))
        reports.append(result)
        print(label + ': ' + result['status'] + ' ' + result.get('stdout', '').strip(), flush=True)
    summary = dict(scope='invented source launchers and renpy loader only; no engine/game/bootstrap/model invocation',
                   helper=str(HELPER), helper_sha256=hashlib.sha256(HELPER.read_bytes()).hexdigest(),
                   assertions='explicit if/raise conditions; not removable by -O',
                   total=len(reports), passed=sum(r['status']=='passed' for r in reports), results=reports)
    (OUT / 'report.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print('REPORT: ' + str(OUT / 'report.json'), flush=True)
    raise SystemExit(0 if summary['passed'] == len(reports) and reports else 1)


if __name__ == '__main__':
    main()
