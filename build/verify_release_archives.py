"""Audit actual release ZIPs and relocated executables; no game or inference."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import struct
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'app'))
from release_archives import audit_bundle, prepare_archiver, release_version, run_archiver, sha256


def relative(value):
    path = PurePosixPath(value)
    if not value or '\\' in value or path.is_absolute() or '..' in path.parts or ':' in value or '\x00' in value:
        raise ValueError('Unsafe archive/manifest path: '+repr(value))
    return path.as_posix()


def manifest_files(document, edition):
    assert document['schema'] == 1 and document['edition'] == edition
    assert document['privacy_audit'] == 'passed'
    records = {}
    folded = set()
    for row in document['files']:
        name = relative(row['path'])
        assert name != 'bundle-manifest.json'
        assert name not in records and name.casefold() not in folded
        assert isinstance(row['bytes'], int) and row['bytes'] >= 0
        assert len(row['sha256']) == 64
        folded.add(name.casefold())
        records[name] = row
    assert document['totals']['files'] == len(records)
    assert document['totals']['bytes'] == sum(row['bytes'] for row in records.values())
    return records


def verify_tree(actual, edition):
    manifest = json.loads((actual/'bundle-manifest.json').read_text(encoding='utf-8'))
    records = manifest_files(manifest, edition)
    files = {path.relative_to(actual).as_posix(): path for path in actual.rglob('*') if path.is_file()}
    assert set(files) == set(records) | {'bundle-manifest.json'}
    audit = audit_bundle(actual, edition, root=ROOT, strict=True, write_manifest=False)
    assert audit['privacy_audit'] == 'passed'
    # audit_bundle hashes each file once and also checks vendor/model policy.
    # Reuse those records instead of rereading the multi-GB model a second time.
    assert manifest_files(audit, edition) == records
    return records, audit


def verify_light_metadata(archive):
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        assert len(names) == len(set(names))
        for name in names:
            normalized = relative(name.rstrip('/'))
            assert normalized == 'RenPyTranslator' or normalized.startswith('RenPyTranslator/')
        manifest = json.loads(bundle.read('RenPyTranslator/bundle-manifest.json'))
        records = manifest_files(manifest, 'light')
        actual = {name.removeprefix('RenPyTranslator/') for name in names if not name.endswith('/')}
        assert actual == set(records) | {'bundle-manifest.json'}
        assert bundle.testzip() is None, 'LIGHT CRC failure'
    return records


def split_parts(release, edition):
    assert edition in ('light', 'full')
    return sorted(release.glob('RenPyTranslator-'+edition+'.z[0-9]*'), key=lambda path:int(path.suffix[2:]))


def verify_split_metadata(release, edition='full'):
    archive = release/('RenPyTranslator-'+edition+'.zip')
    parts = split_parts(release, edition)
    assert parts, edition.upper()+' is not split into volumes'
    assert [part.name for part in parts] == ['RenPyTranslator-'+edition+'.z%02d' % (i+1) for i in range(len(parts))]
    with archive.open('rb') as stream:
        stream.seek(max(0, archive.stat().st_size-65557))
        tail = stream.read()
        position = tail.rfind(b'PK\x05\x06')
        assert position >= 0 and len(tail)-position >= 22, 'Missing ZIP end record'
        _, disk, central_disk, _, _, _, _, comment = struct.unpack_from('<4s4H2LH', tail, position)
        assert position+22+comment == len(tail), 'Unexpected trailing ZIP data'
        locator = tail.rfind(b'PK\x06\x07', 0, position)
        if locator >= 0 and position-locator == 20:
            _, record_disk, record_offset, disks = struct.unpack_from('<4sLQL', tail, locator)
            assert disks == len(parts)+1
            if record_disk == len(parts):
                stream.seek(record_offset)
                record = stream.read(56)
                assert record[:4] == b'PK\x06\x06'
                disk, central_disk = struct.unpack_from('<LL', record, 16)
        assert disk == len(parts), (disk, len(parts))
        assert central_disk <= disk
    return {'volumes':len(parts)+1, 'last_disk':disk, 'central_directory_disk':central_disk,
            'real_multivolume_zip':True}


def executable_check(actual, env, arguments):
    result = subprocess.run([str(actual/arguments[0]), *arguments[1:]], cwd=actual, env=env,
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=45,
                            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    assert result.returncode == 0, result.stdout+result.stderr
    print('Relocated executable OK: '+' '.join(arguments), flush=True)


def frozen_rpg_parser_check(actual, env):
    fixture = actual/'build/portable-rpg-parser-fixture'
    (fixture/'data/rpg-source').mkdir(parents=True)
    script = fixture/'staging/js/plugins/RPT_Bilingual.js'
    script.parent.mkdir(parents=True)
    script.write_text('var RPT_ReleaseValidation = { ok: true };\n', encoding='utf-8')
    config = dict(source=str(fixture/'unused-original'), language='korean', source_language='english',
                  engine='rpgmaker', engine_label='RPG Maker MV', automatic_version=1,
                  model=None, endpoint='http://127.0.0.1:11434', num_ctx=16384)
    (fixture/'project.json').write_text(json.dumps(config), encoding='utf-8')
    (fixture/'data/rpg-output.json').write_text(json.dumps({'files':['js/plugins/RPT_Bilingual.js']}), encoding='utf-8')
    executable_check(actual, env, ['RenPyTranslator-cli.exe','tasks','--tasks','check','--project',str(fixture/'project.json')])
    report = json.loads((fixture/'data/patch-validation.json').read_text(encoding='utf-8'))
    assert report['status'] == 'passed'
    assert report['coverage']['runtime'] == 'not_run'
    assert report['coverage']['javascript_checker'] == 'electron_node'
    assert report['coverage']['javascript_files'] == 1
    return {'frozen_rpg_javascript_parser':'electron_node','game_executed':False,'inference_executed':False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--alzip', type=Path, help='Optional explicit ALZipCon.exe')
    parser.add_argument('--release', type=Path, help='Explicit release folder; defaults to locked version')
    args = parser.parse_args()
    release = args.release.resolve(strict=True) if args.release else ROOT/('portable/V'+release_version(ROOT))
    manifest = json.loads((release/'release-manifest.json').read_text(encoding='utf-8'))
    for row in manifest['files']:
        archive = release/relative(row['name'])
        assert archive.parent == release
        assert archive.stat().st_size == row['bytes'] < 2**31
        assert sha256(archive) == row['sha256'], archive
    light_split = bool(split_parts(release, 'light'))
    light_records = None if light_split else verify_light_metadata(release/'RenPyTranslator-light.zip')
    split = {'light':verify_split_metadata(release,'light') if light_split else
                     {'volumes':1,'real_multivolume_zip':False},
             'full':verify_split_metadata(release,'full')}
    manifest_names = {row['name'] for row in manifest['files']}
    for edition in ('light','full'):
        expected = {part.name for part in split_parts(release,edition)} | {'RenPyTranslator-'+edition+'.zip'}
        declared = {name for name in manifest_names if name.startswith('RenPyTranslator-'+edition+'.')}
        assert expected == declared, (edition, expected, declared)
    archiver = prepare_archiver(ROOT)
    for edition in ('light','full'):
        if edition == 'full' or light_split:
            archive = release/('RenPyTranslator-'+edition+'.zip')
            run_archiver([archiver,'t','-y',archive],ROOT/('build/verify-'+edition+'-crc.log'))
            run_archiver([archiver,'l',archive],ROOT/('build/verify-'+edition+'-list.log'))
    results = []
    inventories = {}
    with tempfile.TemporaryDirectory(prefix='release-verify-', dir=ROOT/'build') as temporary:
        temp = Path(temporary)
        for edition in ('light','full'):
            archive = release/('RenPyTranslator-'+edition+'.zip')
            tools = [('Bandizip',archiver)] + ([('ALZip',args.alzip.resolve(strict=True))] if args.alzip else [])
            for name, tool in tools:
                output = temp/(edition+'-'+name)
                output.mkdir()
                command = ([tool,'x','-y','-o:'+str(output),archive] if name=='Bandizip' else [tool,'-x',archive,output])
                run_archiver(command,ROOT/('build/verify-'+edition+'-'+name.lower()+'.log'),cwd=output)
                actual = next((p for p in (output/'RenPyTranslator',output/archive.stem/'RenPyTranslator') if p.is_dir()),None)
                assert actual is not None, output
                records, audit = verify_tree(actual,edition)
                if edition=='light' and light_records is not None:
                    assert records == light_records
                if edition in inventories:
                    assert inventories[edition] == records
                inventories[edition] = records
                results.append({'edition':edition,'archiver':name,'files':len(records),'sha256_match':True,
                                'privacy_audit':audit['privacy_audit']})
                print(edition.upper()+' '+name+': manifest hashes and privacy audit passed',flush=True)
                if edition=='light' and name=='Bandizip':
                    env = dict(os.environ,PATH=str(Path(os.environ['SystemRoot'])/'System32'),
                               PYTHONHOME=str(actual/'no-system-python'),PYTHONPATH=str(actual/'no-system-python'))
                    env.pop('RPT_TOOL_ROOT',None)
                    env.pop('ELECTRON_RUN_AS_NODE',None)
                    for executable, options in (('RenPyTranslator-cli.exe',['--self-check']),
                                                ('RenPyTranslator-cli.exe',['--help']),('RenPyTranslator.exe',['--self-check'])):
                        executable_check(actual,env,[executable,*options])
                    results.append(frozen_rpg_parser_check(actual,env))
                    node = ROOT/json.loads((ROOT/'runtime-lock.json').read_text(encoding='utf-8'))['node']['directory']/'node.exe'
                    ui = subprocess.run([str(node),str(ROOT/'desktop/test-portable.mjs'),str(actual)],cwd=ROOT/'desktop',env=env,
                                        capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90,
                                        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    assert ui.returncode == 0,ui.stdout+ui.stderr
                    print(ui.stdout.strip(),flush=True)
                    results.append({'relocated_gui_cli':True,'system_python_node_ollama_required':False})
        common = set(inventories['light']) & set(inventories['full'])
        assert all(inventories['light'][path] == inventories['full'][path] for path in common)
        assert not (set(inventories['light'])-set(inventories['full']))
        assert all(path.startswith('models/ollama/') for path in set(inventories['full'])-set(inventories['light']))
    report = {'version':manifest['version'],'archives':manifest['files'],'checks':results,'split_zip':split,
              'game_executed':False,'model_inference_executed':False,'real_projects_read':False,
              'alzip_tested':args.alzip is not None,'limitation':'Relocated on this Windows PC, not a separate clean VM.'}
    (ROOT/'build/release-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('RELEASE_VERIFICATION '+str(ROOT/'build/release-verification.json'),flush=True)


if __name__ == '__main__':
    main()
