"""Small deployment fixtures; no real game, project, download or model execution."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import build_portable
import development_setup as setup
import runtime_assets


class DeploymentTests(unittest.TestCase):
    def test_paths_cannot_leave_tool_root(self):
        with tempfile.TemporaryDirectory() as temp:
            for path in ('../outside', temp, 'nested/../../outside'):
                with self.assertRaises(ValueError):
                    runtime_assets.inside(Path(temp), path)

    def test_verified_runtime_reuses_files_and_repairs_changed_member(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); cache=root/'cache';cache.mkdir()
            archive=cache/'node-1.zip'
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('node/node.exe',b'known executable')
                z.writestr('node/LICENSE',b'license')
            asset={'node':{'version':'1','directory':'runtimes/node','prefix':'node/','executable':'node.exe','sha256':setup.sha256(archive)}}
            with patch('development_setup.load_lock',return_value=asset):
                directory=setup.ensure_runtime('node',root,cache)
                before=(directory/'node.exe').stat().st_mtime_ns
                setup.ensure_runtime('node',root,cache)
                self.assertEqual(before,(directory/'node.exe').stat().st_mtime_ns)
                (directory/'node.exe').write_bytes(b'changed')
                # Verification detects corruption, rather than accepting a stale receipt.
                with self.assertRaisesRegex(ValueError,'SHA-256 mismatch'):
                    setup.ensure_runtime('node',root,cache)

    def test_zip_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cache=root/'cache';cache.mkdir();archive=cache/'node-1.zip'
            with zipfile.ZipFile(archive,'w') as z:z.writestr('../outside',b'bad')
            asset={'node':{'version':'1','directory':'runtimes/node','prefix':'','executable':'node.exe','sha256':setup.sha256(archive)}}
            with patch('development_setup.load_lock',return_value=asset):
                with self.assertRaises(ValueError):setup.ensure_runtime('node',root,cache)
            self.assertFalse((root/'runtimes/outside').exists())

    def test_full_only_prepares_fixed_model_preserves_existing_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);blobs=root/'models/ollama/blobs';blobs.mkdir(parents=True)
            body=b'fixture weights';digest=hashlib.sha256(body).hexdigest()
            (blobs/('sha256-'+digest)).write_bytes(body)
            manifest=root/'models/ollama/manifests/fixed';manifest.parent.mkdir(parents=True);manifest.write_text('existing user manifest')
            asset={'sha256':digest,'size':len(body),'text_blobs':{},'manifest_path':'models/ollama/manifests/fixed','manifest':{'fixture':True}}
            with patch('development_setup.load_lock',return_value={'full_model':asset}):
                setup.prepare_full_model(root)
            self.assertEqual(manifest.read_text(),'existing user manifest')

    def test_light_build_never_requests_model_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for area in ('desktop/node_modules/electron/dist','desktop/dist','vendor/fonts','vendor/unrpyc','runtimes/ollama-fixture','licenses'):
                (root/area).mkdir(parents=True,exist_ok=True)
            (root/'desktop/node_modules/electron/dist/electron.exe').write_bytes(b'fixture')
            for name in ('translate.ps1','gui.ps1','README.md','runtime-lock.json','THIRD_PARTY_NOTICES.md','licenses/Hy-MT2-LICENSE.txt','docs/grok-context-preparation.txt','docs/context-runtime.md','vendor/asset-lock.json','vendor/versions.json'):
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('fixture')
            (root/'runtime-lock.json').write_text(json.dumps({'release':{'version':'0.1.0'}}),encoding='utf-8')
            def build(*args,**kwargs):
                (root/'dist/RenPyTranslator').mkdir(parents=True,exist_ok=True)
            with patch('build_portable.ROOT',root),patch('build_portable.verify_vendor'),patch('build_portable.runtime_executable',return_value=root/'node.exe'),patch('build_portable.load_lock',return_value={'release':{'version':'0.1.0'},'ollama':{'directory':'runtimes/ollama-fixture'}}),patch('build_portable.subprocess.run',side_effect=build),patch('build_portable.export_model',side_effect=AssertionError('LIGHT must not access models')),patch('build_portable.publish_local'):
                build_portable.main([])
            with zipfile.ZipFile(root/'portable/V0.1.0/RenPyTranslator-light.zip') as archive:
                self.assertFalse(any('/models/ollama/' in name for name in archive.namelist()))
                package=json.loads(archive.read('RenPyTranslator/_desktop/resources/app/package.json'))
                self.assertEqual(package['version'],'0.1.0')
            self.assertFalse((root/'models').exists())


if __name__=='__main__':unittest.main()
