import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import build_portable
import development_setup
from release_archives import audit_bundle, runtime_file_allowed
from test_release_privacy import dump, fixture, put


class RocmAssetsTests(unittest.TestCase):
    def test_official_archive_layout_installs_and_reuses(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); cache = root/'cache'; cache.mkdir()
            archive = cache/'ollama_rocm-1.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('README_ROCm.txt', 'readme')
                z.writestr('lib/ollama/rocm_v7_1/ggml-hip.dll', b'dll')
                z.writestr('lib/ollama/rocm_v7_1/amdhip64_7.dll', b'hip-runtime')
                z.writestr('lib/ollama/rocm_v7_1/rocblas/library/a.hsaco', b'data')
            asset = {'version':'1', 'directory':'runtimes/ollama-0.40.15/lib/ollama/rocm_v7_1',
                     'prefix':'lib/ollama/rocm_v7_1/', 'ignored_members':['README_ROCm.txt'],
                     'executable':'ggml-hip.dll', 'sha256':development_setup.sha256(archive)}
            with patch.object(development_setup, 'load_lock', return_value={'ollama_rocm':asset}):
                path = development_setup.ensure_runtime('ollama_rocm', root, cache)
                build_portable.verify_rocm_addon(root, asset)
                self.assertFalse(build_portable.runtime_asset_selected(root, path/'ggml-hip.dll', asset, False))
                self.assertTrue(build_portable.runtime_asset_selected(root, path/'ggml-hip.dll', asset, True))
                (path/'rocblas/library/a.hsaco').write_bytes(b'changed')
                with self.assertRaises(ValueError): build_portable.verify_rocm_addon(root, asset)

    def test_release_packaging_requires_exact_official_rocm_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle, lock = fixture(root)
            runtime = 'runtimes/ollama-0.40.15'
            addon = runtime + '/lib/ollama/rocm_v7_1'
            lock['ollama_compatibility'] = [{'directory': runtime, 'executable': 'ollama.exe'}]
            lock['ollama_rocm'] = {'directory': addon, 'executable': 'ggml-hip.dll',
                                   'version': '0.40.15', 'sha256': 'a' * 64}
            dump(root, 'runtime-lock.json', lock)
            put(bundle, runtime + '/ollama.exe')
            members = {'ggml-hip.dll': b'dll', 'amdhip64_7.dll': b'hip-runtime',
                       'rocblas/library/Kernels.so-000-gfx906-xnack-.hsaco': b'official kernel'}
            dump(root, addon + '/asset-verification.json', {
                'archive_sha256': lock['ollama_rocm']['sha256'],
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in members.items()}})
            for name, data in members.items():
                put(bundle, addon + '/' + name, data)
            audit = audit_bundle(bundle, 'light', root=root, strict=True)
            self.assertEqual(audit['rocm']['files'], len(members))
            extra = put(bundle, addon + '/unapproved.dll')
            with self.assertRaisesRegex(ValueError, 'Unlisted official ROCm'):
                audit_bundle(bundle, 'light', root=root, strict=True)
            extra.unlink()
            hip = put(bundle, addon + '/amdhip64_7.dll', b'modified')
            with self.assertRaisesRegex(ValueError, 'Official ROCm release SHA-256 mismatch'):
                audit_bundle(bundle, 'light', root=root, strict=True)
            hip.unlink()
            with self.assertRaisesRegex(ValueError, 'Required official ROCm assets are missing'):
                audit_bundle(bundle, 'light', root=root, strict=True)

    def test_release_prepares_addon_by_default_but_local_and_stage_retains_opt_in(self):
        class StopBeforeBuild(Exception):
            pass
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            asset = {'directory': 'runtimes/official-rocm'}
            lock = {'ollama_rocm': asset}
            with patch.object(build_portable, 'ROOT', root), patch.object(build_portable, 'verify_vendor'), \
                 patch.object(build_portable, 'load_lock', return_value=lock), \
                 patch.object(build_portable, 'ensure_runtime') as prepare, \
                 patch.object(build_portable, 'verify_rocm_addon') as verify, \
                 patch.object(build_portable, 'runtime_executable', side_effect=StopBeforeBuild):
                with self.assertRaises(StopBeforeBuild):
                    build_portable.main([])
                prepare.assert_called_once_with('ollama_rocm', root, asset=asset)
                verify.assert_called_once_with(root, asset, verify_hashes=False)
                prepare.reset_mock()
                verify.reset_mock()
                put(root, asset['directory'] + '/asset-verification.json')
                for flags in (['--local-only'], ['--stage-only']):
                    with self.assertRaises(StopBeforeBuild):
                        build_portable.main(flags)
                verify.assert_not_called()
                with self.assertRaises(StopBeforeBuild):
                    build_portable.main(['--stage-only', '--include-rocm'])
                verify.assert_called_once_with(root, asset, verify_hashes=True)
                prepare.assert_not_called()

    def test_rocm_data_allowlist_is_version_and_subtree_scoped(self):
        base = 'runtimes/ollama-0.40.15/lib/ollama/rocm_v7_1/'
        for suffix in ('a.co', 'a.dat', 'a.hsaco', 'TensileManifest.txt'):
            self.assertTrue(runtime_file_allowed(base+'rocblas/library/'+suffix))
        for name in ('a.dat', 'rocblas/a.dat', 'other/library/a.co', 'rocblas/library/a.json'):
            self.assertFalse(runtime_file_allowed(base+name))
        self.assertFalse(runtime_file_allowed('runtimes/ollama-0.40.15/lib/ollama/other/a.dat'))
        self.assertFalse(runtime_file_allowed('runtimes/ollama-0.40.15/elsewhere/rocm_v7_1/rocblas/library/a.dat'))
        self.assertFalse(runtime_file_allowed('runtimes/ollama-0.34.2/lib/ollama/rocm_v7_1/ggml-hip.dll'))
        self.assertFalse(runtime_file_allowed('runtimes/ollama-0.40.15/lib/ollama/rocm_v8/ggml-hip.dll'))


if __name__ == '__main__': unittest.main()
