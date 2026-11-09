"""Real Python 2 importer + invented site.pyo only; no Ren'Py/game execution."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'app'))
from engine import interpreter_args

python2 = ROOT / 'build/renpy-validation/renpy-7.8.7-sdk/lib/py2-windows-x86_64/python.exe'
env = {k: v for k, v in os.environ.items()
       if k.upper() not in ('PYTHONHOME', 'PYTHONPATH', 'PYTHONOPTIMIZE')}

def run(args):
    return subprocess.run([str(python2), *args], cwd=ROOT / 'build', env=env,
                          capture_output=True, timeout=15,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

with tempfile.TemporaryDirectory(prefix='legacy-import-', dir=ROOT / 'build') as temp:
    runtime = Path(temp) / 'lib/windows-i686'
    stdlib = runtime / 'Lib'
    stdlib.mkdir(parents=True)
    # Generate real Python 2 bytecode without executing it or importing Ren'Py.
    make = ('import imp, marshal, struct, sys; '
            'code = compile("RPT_SYNTHETIC = 2718\\n", "synthetic_site", "exec"); '
            'open(sys.argv[1], "wb").write(imp.get_magic()+struct.pack("<I",0)+marshal.dumps(code))')
    result = run(['-S', '-B', '-c', make, str(stdlib / 'site.pyo')])
    assert result.returncode == 0, result.stderr
    probe = ('import sys; print("OPTIMIZE=%d" % sys.flags.optimize); '
             'sys.path[:] = [sys.argv[1]]; import site; '
             'assert site.RPT_SYNTHETIC == 2718; print("PYO_IMPORT_OK")')
    before = run(['-B', '-S', '-c', probe, str(stdlib)])
    if b'OPTIMIZE=0' in before.stdout:
        assert before.returncode != 0 and b'No module named site' in before.stderr, before.stderr
        print('Reproduced missing site without optimization.')
    else:
        assert before.returncode == 0 and b'PYO_IMPORT_OK' in before.stdout, before.stderr
        print('This SDK enables optimization by default; baseline failure not reproduced here.')
    flags = interpreter_args(runtime / 'python.exe')[1:]
    after = run([*flags, '-S', '-c', probe, str(stdlib)])
    assert after.returncode == 0 and b'PYO_IMPORT_OK' in after.stdout, after.stderr
    print('Actual Python 2: generated flags successfully load the synthetic site.pyo.')
    print('No game, RenPy bootstrap, project data or model was executed/read.')
