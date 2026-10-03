"""Extract release archives with real archivers; never run games or inference."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from release_archives import prepare_archiver, release_version, run_archiver, sha256


def verify_tree(actual, expected):
    files = {p.relative_to(actual).as_posix(): p for p in actual.rglob('*') if p.is_file()}
    assert files.keys() == expected.keys(), (files.keys() - expected.keys(), expected.keys() - files.keys())
    for relative, digest in expected.items():
        assert sha256(files[relative]) == digest, relative


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--alzip', type=Path, help='Optional unpacked/installed ALZipCon.exe')
    args = parser.parse_args()
    release = ROOT / ('portable/V' + release_version(ROOT))
    bundle = ROOT / 'dist/RenPyTranslator'
    full_expected = {p.relative_to(bundle).as_posix(): sha256(p)
                     for p in bundle.rglob('*') if p.is_file()}
    manifest = json.loads((release / 'release-manifest.json').read_text(encoding='utf-8'))
    for row in manifest['files']:
        archive = release / row['name']
        assert archive.stat().st_size == row['bytes'] < 2 ** 31
        assert sha256(archive) == row['sha256'], archive
    light_zip = release / 'RenPyTranslator-light.zip'
    with zipfile.ZipFile(light_zip) as z:
        names = z.namelist()
        assert all(n.startswith('RenPyTranslator/') for n in names)
        assert not any('/models/ollama/' in n for n in names)
        assert not any('/build/tools/' in n or '/project/' in n.rstrip('/')
                       or '/data/projects/' in n for n in names)
        light_expected = {n.removeprefix('RenPyTranslator/'): full_expected[n.removeprefix('RenPyTranslator/')]
                          for n in names if not n.endswith('/')}
        assert all(n.startswith('models/ollama/') for n in full_expected.keys() - light_expected.keys())
    archiver = prepare_archiver(ROOT)
    results = []
    with tempfile.TemporaryDirectory(prefix='release-verify-', dir=ROOT / 'build') as temp:
        temp = Path(temp)
        for edition, expected in (('light', light_expected), ('full', full_expected)):
            archive = release / ('RenPyTranslator-' + edition + '.zip')
            for name, tool in [('Bandizip', archiver)] + ([('ALZip', args.alzip.resolve())] if args.alzip else []):
                output = temp / (edition + '-' + name)
                output.mkdir()
                log = ROOT / ('build/verify-' + edition + '-' + name.lower() + '.log')
                command = ([tool, 'x', '-y', '-o:' + str(output), archive] if name == 'Bandizip'
                           else [tool, '-x', archive, output])
                run_archiver(command, log, cwd=output)
                choices = [output / 'RenPyTranslator', output / archive.stem / 'RenPyTranslator']
                actual = next((p for p in choices if p.is_dir()), None)
                assert actual is not None, output
                verify_tree(actual, expected)
                results.append({'edition': edition, 'archiver': name, 'files': len(expected), 'sha256_match': True})
                print(edition.upper() + ' ' + name + ': every extracted file SHA-256 matched', flush=True)
                if edition == 'light' and name == 'Bandizip':
                    env = dict(os.environ, PATH=str(Path(os.environ['SystemRoot']) / 'System32'),
                               PYTHONHOME=str(actual / 'no-system-python'),
                               PYTHONPATH=str(actual / 'no-system-python'))
                    env.pop('RPT_TOOL_ROOT', None)
                    env.pop('ELECTRON_RUN_AS_NODE', None)
                    for executable, options in (
                            ('RenPyTranslator-cli.exe', ['--self-check']),
                            ('RenPyTranslator-cli.exe', ['--help']),
                            ('RenPyTranslator.exe', ['--self-check'])):
                        result = subprocess.run([str(actual / executable)] + options, cwd=actual, env=env,
                                                capture_output=True, text=True, encoding='utf-8', errors='replace',
                                                timeout=40, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                        assert result.returncode == 0, result.stdout + result.stderr
                        print('Relocated executable OK: ' + executable + ' ' + ' '.join(options), flush=True)
                    node = ROOT / json.loads((ROOT / 'runtime-lock.json').read_text(encoding='utf-8'))['node']['directory'] / 'node.exe'
                    result = subprocess.run([str(node), str(ROOT / 'desktop/test-portable.mjs'), str(actual)],
                                            cwd=ROOT / 'desktop', env=env, capture_output=True, text=True,
                                            encoding='utf-8', errors='replace', timeout=90,
                                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    assert result.returncode == 0, result.stdout + result.stderr
                    print(result.stdout.strip(), flush=True)
                    # This UI test writes settings only into the relocated fixture.
                    results.append({'relocated_gui_cli': True, 'system_python_node_ollama_required': False})
    report = {'version': manifest['version'], 'archives': manifest['files'], 'checks': results,
              'game_executed': False, 'model_inference_executed': False,
              'real_projects_read': False, 'alzip_tested': args.alzip is not None,
              'limitation': 'Tested on this Windows PC at a relocated path, not on a clean VM.'}
    (ROOT / 'build/release-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('RELEASE_VERIFICATION ' + str(ROOT / 'build/release-verification.json'), flush=True)


if __name__ == '__main__':
    main()
