"""Only small invented assets; no real models, games, processes or downloads."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from build_portable import copy_vendor_assets
from release_archives import audit_bundle, package_editions, write_light, write_full, MANIFEST_NAME, light_archive_plan


def put(root, name, body=b'invented asset'):
    path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(body if isinstance(body,bytes) else body.encode('utf-8'))
    return path


def dump(root, name, body):
    return put(root,name,json.dumps(body))


def fixture(root):
    bundle=root/'dist/RenPyTranslator';bundle.mkdir(parents=True)
    lock={'release':{'version':'0.3.0'},'ollama':{'directory':'runtimes/ollama-fixture','executable':'ollama.exe'}}
    dump(root,'runtime-lock.json',lock);dump(bundle,'runtime-lock.json',lock)
    for name in ('RenPyTranslator.exe','RenPyTranslator-cli.exe','_internal/python312.dll',
                 '_desktop/RenPyTranslator-UI.exe','_desktop/resources/app/main.js',
                 '_desktop/resources/app/preload.js','_desktop/resources/app/index.html',
                 'runtimes/ollama-fixture/ollama.exe','models/README.txt'):
        put(bundle,name)
    for name in ('data','project'): (bundle/name).mkdir()
    return bundle,lock


def fixed_model(root,bundle,lock):
    config=b'{"synthetic":true}';weights=b'approved synthetic HY fixture'
    def entry(body, media):
        return {'mediaType':media,'size':len(body),'digest':'sha256:'+hashlib.sha256(body).hexdigest()}
    cfg=entry(config,'application/vnd.docker.container.image.v1+json')
    layer=entry(weights,'application/vnd.ollama.image.model')
    manifest={'schemaVersion':2,'config':cfg,'layers':[layer]}
    asset={'sha256':layer['digest'][7:],'size':len(weights),
           'manifest_path':'models/ollama/manifests/registry.ollama.ai/library/invented-hy/q6_k',
           'manifest':manifest}
    lock=dict(lock,full_model=asset)
    dump(root,'runtime-lock.json',lock);dump(bundle,'runtime-lock.json',lock)
    for item,body in ((cfg,config),(layer,weights)):
        put(bundle,'models/ollama/blobs/'+item['digest'].replace(':','-'),body)
    dump(bundle,asset['manifest_path'],manifest)
    return asset


class ReleasePrivacyTests(unittest.TestCase):
    def test_vendor_copy_uses_pinned_runtime_subset_not_sample_trees(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);bundle,_=fixture(root)
            names=('vendor/unrpyc/unrpyc.py','vendor/unrpyc/deobfuscate.py',
                   'vendor/unrpyc/decompiler/__init__.py','vendor/unrpyc/decompiler/testcasedecompiler.py',
                   'vendor/unrpyc/LICENSE','vendor/fonts/Approved.ttf',
                   'vendor/unrpyc/testcases/test_un_rpyc.py','vendor/unrpyc/testcases/original/game/script.rpy',
                   'vendor/unrpyc/testcases/compiled/game/script.rpyc','vendor/unrpyc/setup.py',
                   'vendor/unrpyc/un.rpyc/compile.py','vendor/unrpyc/.github/workflows/test.yml')
            assets={name:hashlib.sha256(put(root,name).read_bytes()).hexdigest() for name in names}
            dump(root,'vendor/asset-lock.json',{'schema':1,'files':assets});dump(root,'vendor/versions.json',{})
            upstream_names=('gameloc/__init__.py','gameloc/engines/rpgmaker.py','LICENSE',
                            'THIRD-PARTY-NOTICES.txt','gameloc/web/index.html','tests/fixture.json','pyproject.toml')
            upstream={name:hashlib.sha256(put(root,'vendor/gameloc/'+name).read_bytes()).hexdigest() for name in upstream_names}
            dump(root,'vendor/gameloc/upstream.json',{'version':'invented','files':upstream})
            def copy(source,relative):
                target=bundle/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
            copy_vendor_assets(root,bundle,copy)
            self.assertTrue((bundle/'vendor/unrpyc/decompiler/testcasedecompiler.py').is_file())
            for name in names[6:]: self.assertFalse((bundle/name).exists(),name)
            self.assertFalse((bundle/'vendor/gameloc/gameloc/web/index.html').exists())
            selected=json.loads((bundle/'vendor/asset-lock.json').read_text())['files']
            self.assertEqual(set(selected),set(names[:6]))
            audit_bundle(bundle,'light',root=root,strict=True)

    def test_private_game_and_model_paths_fail_before_archive_creation(self):
        forbidden=('data/gui-settings.json','data/projects/Example/data/translations.jsonl',
                   'project/Invented/game/script.rpy','projects/Invented/save.save','config/preferences.json',
                   'vendor/unrpyc/testcases/original/game/script.rpy',
                   'vendor/unrpyc/testcases/compiled/script.rpyc','_internal/game/archive.rpa',
                   'models/gguf/other.gguf','models/ollama/blobs/sha256-unapproved',
                   '_desktop/resources/app/gui-settings.json','_internal/cache/events.jsonl',
                   '_internal/Actors.json','_internal/runtime.evtx','private-notes.txt')
        for name in forbidden:
            with self.subTest(name=name),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);bundle,_=fixture(root);put(bundle,name)
                archive=root/'release.zip'
                with self.assertRaises(ValueError):
                    audit=audit_bundle(bundle,'light',root=root,strict=True)
                    write_light(bundle,archive,audit=audit)
                self.assertFalse(archive.exists())

    def test_manifest_is_complete_relative_hashed_and_roundtrips_in_light(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);bundle,_=fixture(root)
            audit=audit_bundle(bundle,'light',root=root,strict=True)
            self.assertEqual(audit['privacy_audit'],'passed')
            self.assertEqual(audit['model_policy'],'no_models')
            self.assertNotIn(MANIFEST_NAME,{row['path'] for row in audit['files']})
            archive=root/'light.zip';write_light(bundle,archive,audit=audit)
            with zipfile.ZipFile(archive) as zipped:
                manifest=json.loads(zipped.read('RenPyTranslator/'+MANIFEST_NAME))
                self.assertEqual(manifest,audit)
                for row in manifest['files']:
                    self.assertFalse(Path(row['path']).is_absolute())
                    self.assertEqual(hashlib.sha256(zipped.read('RenPyTranslator/'+row['path'])).hexdigest(),row['sha256'])

    def test_full_accepts_only_exact_locked_manifest_and_blob_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);bundle,lock=fixture(root);asset=fixed_model(root,bundle,lock)
            report=audit_bundle(bundle,'full',root=root,strict=True)
            self.assertEqual(report['model_policy'],'locked_full_model')
            blob=bundle/'models/ollama/blobs'/('sha256-'+asset['sha256'])
            body=blob.read_bytes();blob.write_bytes(b'X'+body[1:])
            with self.assertRaisesRegex(ValueError,'blob verification'):audit_bundle(bundle,'full',root=root,strict=True)
            blob.write_bytes(body)
            path=bundle/asset['manifest_path'];manifest=json.loads(path.read_text());manifest['private']='unexpected'
            dump(bundle,asset['manifest_path'],manifest)
            with self.assertRaisesRegex(ValueError,'manifest differs'):audit_bundle(bundle,'full',root=root,strict=True)

    def test_full_rejects_extra_and_missing_models(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);bundle,lock=fixture(root);asset=fixed_model(root,bundle,lock)
            extra=put(bundle,'models/ollama/blobs/sha256-'+'0'*64)
            with self.assertRaisesRegex(ValueError,'unapproved model'):audit_bundle(bundle,'full',root=root,strict=True)
            extra.unlink();(bundle/asset['manifest_path']).unlink()
            with self.assertRaisesRegex(ValueError,'missing verified'):audit_bundle(bundle,'full',root=root,strict=True)

    def test_unlisted_vendor_runtime_and_missing_launchers_are_rejected(self):
        for name in ('vendor/unrpyc/decompiler/private.py','runtimes/ollama-fixture/userprefs.json',
                     'runtimes/other/ollama.exe','docs/private-notes.md','licenses/personal.txt'):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);bundle,_=fixture(root);put(bundle,name)
                with self.assertRaises(ValueError):audit_bundle(bundle,'light',root=root,strict=True)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);bundle,_=fixture(root);(bundle/'RenPyTranslator.exe').unlink()
            with self.assertRaisesRegex(ValueError,'Required portable'):audit_bundle(bundle,'light',root=root,strict=True)

    def test_pinned_default_and_compatibility_runtimes_are_both_required_and_listed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);bundle,lock=fixture(root)
            lock['ollama']['version']='new'
            lock['ollama_compatibility']=[{'version':'previous','directory':'runtimes/ollama-old','executable':'ollama.exe'}]
            dump(root,'runtime-lock.json',lock);dump(bundle,'runtime-lock.json',lock)
            previous=put(bundle,'runtimes/ollama-old/ollama.exe')
            report=audit_bundle(bundle,'light',root=root,strict=True)
            self.assertEqual([(r['version'],r['default']) for r in report['runtimes']],[('new',True),('previous',False)])
            previous.unlink()
            with self.assertRaisesRegex(ValueError,'Required portable'):audit_bundle(bundle,'light',root=root,strict=True)

    def test_light_uses_a_conservative_size_plan_before_compression(self):
        self.assertFalse(light_archive_plan({'totals':{'bytes':1000,'files':2}})['split'])
        plan=light_archive_plan({'totals':{'bytes':2_000_000_000,'files':20}})
        self.assertTrue(plan['split']);self.assertEqual(plan['volume_bytes'],1_900_000_000)
        self.assertEqual(plan['writer'],'Bandizip')

    def test_large_light_is_split_without_attempting_single_zip_first(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);bundle,_=fixture(root)
            def split(source,archive,archiver,log,**kwargs):
                self.assertEqual(kwargs['edition'],'light')
                self.assertEqual(kwargs['audit']['model_policy'],'no_models')
                first=archive.with_suffix('.z01');first.write_bytes(b'invented volume')
                archive.write_bytes(b'invented final volume');return [first,archive]
            forced={'split':True,'writer':'Bandizip','volume_bytes':1_900_000_000,
                    'uncompressed_upper_bound':3_000_000_000,'selection_basis':'synthetic size plan'}
            with patch('release_archives.light_archive_plan',return_value=forced), \
                 patch('release_archives.prepare_archiver',return_value='invented archiver') as tool, \
                 patch('release_archives.write_light',side_effect=AssertionError('Must not compress twice')) as single, \
                 patch('release_archives.write_split',side_effect=split) as writer:
                release=package_editions(root,bundle,['light'],lambda b:self.fail('LIGHT must not read models'))
            tool.assert_called_once();writer.assert_called_once();single.assert_not_called()
            report=json.loads((release/'release-manifest.json').read_text())
            self.assertTrue(report['light_split'])
            self.assertIn('Bandizip LIGHT ZIP CRC',report['validation'])
            self.assertEqual(len(report['files']),2)
            self.assertIn('.z01',(release/'EXTRACT.txt').read_text(encoding='utf-8-sig'))

    def test_packaging_refuses_before_tools_exports_or_existing_release_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);bundle,_=fixture(root)
            old=put(root,'portable/V0.3.0/RenPyTranslator-light.zip',b'old release')
            put(bundle,'data/private.json')
            with patch('release_archives.prepare_archiver',side_effect=AssertionError('No tool download')) as tools, \
                 patch('release_archives.write_light',side_effect=AssertionError('No new archive')) as light, \
                 patch('release_archives.write_full',side_effect=AssertionError('No new archive')) as full:
                with self.assertRaises(ValueError):package_editions(root,bundle,['light','full'],lambda bundle:self.fail('No model export'))
            tools.assert_not_called();light.assert_not_called();full.assert_not_called()
            self.assertEqual(old.read_bytes(),b'old release')

    def test_changed_bundle_cannot_use_an_earlier_successful_audit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);bundle,_=fixture(root)
            report=audit_bundle(bundle,'light',root=root,strict=True);put(bundle,'data/late.json')
            with self.assertRaisesRegex(ValueError,'changed after'):write_light(bundle,root/'light.zip',audit=report)
            with patch('release_archives.run_archiver') as launch:
                with self.assertRaisesRegex(ValueError,'changed after|edition differs'):write_full(bundle,root/'full.zip','invented.exe',root/'log.txt',audit=report)
                launch.assert_not_called()


if __name__=='__main__':unittest.main()
