"""Version management with invented bundles and mock HTTP; never run a runtime."""
from contextlib import contextmanager,nullcontext,redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import runtime_assets
import runtime_versions as versions


def archive_bytes(extra=None,prefix='',complete=True):
    stream=io.BytesIO()
    entries={'ollama.exe':b'invented exe','LICENSE':b'invented license'}
    if complete:
        entries.update({'lib/ollama/llama-server.exe':b'invented runner',
                        'lib/ollama/cuda_v13/ggml-cuda.dll':b'invented CUDA library'})
    entries.update(extra or {})
    with zipfile.ZipFile(stream,'w') as archive:
        for name,data in entries.items():archive.writestr(prefix+name,data)
    return stream.getvalue()


def release(version,payload,*,digest=True):
    return {'tag_name':'v'+version,'name':'Ollama '+version,'draft':False,
            'prerelease':False,'published_at':'2026-11-09T00:00:00Z',
            'assets':[{'name':'ollama-windows-amd64.zip','size':len(payload),
                'browser_download_url':'https://github.com/ollama/ollama/releases/download/v'
                    +version+'/ollama-windows-amd64.zip',
                'digest':'sha256:'+hashlib.sha256(payload).hexdigest() if digest else None}]}


def create_bundle(root,version):
    directory=root/'runtimes'/('ollama-'+version)
    (directory/'lib/ollama').mkdir(parents=True)
    (directory/'ollama.exe').write_bytes(b'invented exe '+version.encode())
    (directory/'lib/ollama/llama-server.exe').write_bytes(b'invented runner '+version.encode())
    return directory


@contextmanager
def fixture(with_default=True,*,default_version='0.34.2',compatibility=()):
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory)
        def pin(version):
            return {'version':version,'directory':'runtimes/ollama-'+version,
                    'executable':'ollama.exe','sha256':hashlib.sha256(archive_bytes()).hexdigest()}
        lock={'ollama':pin(default_version),
              'ollama_compatibility':[pin(version) for version in compatibility],
              'node':{'directory':'runtimes/invented-node','executable':'node.exe'}}
        (root/'runtime-lock.json').write_text(json.dumps(lock),encoding='utf-8')
        if with_default:create_bundle(root,default_version)
        yield root


class RuntimeVersionTests(unittest.TestCase):
    def setUp(self):versions._CACHE.clear()

    def test_default_keeps_pinned_runtime_and_unrelated_assets(self):
        with fixture() as root:
            self.assertEqual(versions.resolve(root=root)['id'],'ollama-0.34.2')
            self.assertEqual(runtime_assets.runtime_executable('ollama',root),
                             root/'runtimes/ollama-0.34.2/ollama.exe')
            self.assertEqual(runtime_assets.runtime_executable('node',root),
                             root/'runtimes/invented-node/node.exe')

    def test_latest_default_and_legacy_bundles_remain_separately_selectable(self):
        with fixture(default_version='0.40.15',compatibility=('0.34.2',)) as root:
            legacy=create_bundle(root,'0.34.2')
            with patch.object(versions,'_open') as network:
                items=versions.installed(root)
                selected=versions.resolve(root=root)
                older=versions.resolve('ollama-0.34.2',root)
            self.assertEqual([item['id'] for item in items],['ollama-0.40.15','ollama-0.34.2'])
            self.assertEqual([item['default'] for item in items],[True,False])
            self.assertEqual(selected['id'],'ollama-0.40.15')
            self.assertEqual(runtime_assets.runtime_executable('ollama',root),
                             root/'runtimes/ollama-0.40.15/ollama.exe')
            self.assertEqual(older['directory'],str(legacy))
            self.assertEqual(runtime_assets.runtime_executable('ollama',root,runtime_id='0.34.2'),
                             legacy/'ollama.exe')
            network.assert_not_called()

    def test_complete_new_runtime_selects_its_own_executable_and_directory(self):
        with fixture() as root:
            newest=create_bundle(root,'0.40.6')
            self.assertEqual(runtime_assets.runtime_directory('ollama',root,runtime_id='ollama-0.40.6'),newest)
            self.assertEqual(runtime_assets.runtime_executable('ollama',root,runtime_id='0.40.6'),newest/'ollama.exe')
            self.assertEqual(versions.resolve('v0.40.6',root)['id'],'ollama-0.40.6')
            self.assertEqual(versions.resolve(root=root)['id'],'ollama-0.34.2')

    def test_installed_list_is_offline_excludes_partial_folders_and_keeps_default_first(self):
        with fixture() as root:
            create_bundle(root,'0.40.6');create_bundle(root,'0.40.5')
            partial=root/'runtimes/ollama-0.41.0';partial.mkdir();(partial/'ollama.exe').touch()
            with patch.object(versions,'_open') as network,patch.object(versions.hashlib,'sha256') as hashed:
                items=versions.installed(root)
            self.assertEqual([i['id'] for i in items],['ollama-0.34.2','ollama-0.40.6','ollama-0.40.5'])
            network.assert_not_called();hashed.assert_not_called()
            self.assertEqual(items[0]['source'],'bundled')

    def test_invalid_or_uninstalled_selection_never_falls_back(self):
        with fixture() as root:
            for value in ('../0.40.6','ollama-../../outside','0.40.6/ollama.exe','C:/external.exe','0.40.6'):
                with self.subTest(value=value),self.assertRaises(ValueError):versions.resolve(value,root)
            with self.assertRaises(ValueError):runtime_assets.runtime_executable('node',root,runtime_id='0.40.6')

    def test_official_release_list_exposes_verified_hash_and_installation_status(self):
        payload=archive_bytes()
        rows=[release('0.40.6',payload),release('0.34.2',payload),
              release('0.40.5',payload,digest=False)]
        rows.append(dict(release('0.41.0',payload),draft=True))
        with fixture() as root,patch.object(versions,'_json',return_value=rows) as query:
            result=versions.releases(root)
            again=versions.releases(root)
            self.assertEqual(query.call_count,1)
            self.assertEqual(result,again)
            self.assertTrue(result[0]['available']);self.assertFalse(result[0]['installed'])
            self.assertTrue(result[1]['installed']);self.assertTrue(result[1]['default'])
            self.assertFalse(result[2]['available'])
            self.assertEqual(result[0]['sha256'],hashlib.sha256(payload).hexdigest())
            versions.releases(root,refresh=True)
            self.assertEqual(query.call_count,2)

    def test_install_verifies_and_publishes_entire_bundle_without_changing_old_or_models(self):
        payload=archive_bytes();events=[]
        with fixture() as root:
            original=(root/'runtimes/ollama-0.34.2/ollama.exe').read_bytes()
            model=root/'models/ollama/blobs/invented';model.parent.mkdir(parents=True);model.write_bytes(b'unchanged model')
            with patch.object(versions,'_json',return_value=release('0.40.6',payload)), \
                    patch.object(versions,'_open',return_value=io.BytesIO(payload)):
                result=versions.install('ollama-0.40.6',root,events.append)
            directory=Path(result['directory'])
            self.assertTrue(result['archive_verified']);self.assertFalse(result['already_installed'])
            self.assertEqual((directory/'lib/ollama/cuda_v13/ggml-cuda.dll').read_bytes(),b'invented CUDA library')
            self.assertTrue((directory/'LICENSE').is_file())
            self.assertEqual((root/'runtimes/ollama-0.34.2/ollama.exe').read_bytes(),original)
            self.assertEqual(model.read_bytes(),b'unchanged model')
            self.assertEqual(versions.resolve(root=root)['id'],'ollama-0.34.2')
            self.assertEqual([e['phase'] for e in events if e['phase']!='download'],
                             ['metadata','verify','extract','complete'])
            self.assertEqual(list((root/'runtimes/.downloads').iterdir()),[])

    def test_duplicate_install_uses_existing_bundle_without_network(self):
        with fixture() as root,patch.object(versions,'_json') as query,patch.object(versions,'_open') as download:
            result=versions.install('0.34.2',root)
            self.assertTrue(result['already_installed'])
            query.assert_not_called();download.assert_not_called()

    def test_hash_mismatch_never_extracts_or_publishes(self):
        payload=archive_bytes();item=release('0.40.6',payload)
        item['assets'][0]['digest']='sha256:'+'0'*64
        with fixture() as root,patch.object(versions,'_json',return_value=item), \
                patch.object(versions,'_open',return_value=io.BytesIO(payload)), \
                patch.object(versions,'_extract') as extracted:
            with self.assertRaisesRegex(ValueError,'SHA256 mismatch'):versions.install('0.40.6',root)
            extracted.assert_not_called()
            self.assertFalse((root/'runtimes/ollama-0.40.6').exists())
            self.assertTrue((root/'runtimes/ollama-0.34.2/ollama.exe').is_file())

    def test_missing_official_hash_blocks_download_for_unpinned_version(self):
        payload=archive_bytes()
        with fixture() as root,patch.object(versions,'_json',return_value=release('0.40.6',payload,digest=False)), \
                patch.object(versions,'_open') as download:
            with self.assertRaisesRegex(ValueError,'SHA256'):versions.install('0.40.6',root)
            download.assert_not_called()

    def test_pinned_hash_can_verify_legacy_release_without_asset_digest(self):
        payload=archive_bytes()
        with fixture(with_default=False) as root, \
                patch.object(versions,'_json',return_value=release('0.34.2',payload,digest=False)), \
                patch.object(versions,'_open',return_value=io.BytesIO(payload)):
            result=versions.install('0.34.2',root)
            self.assertTrue(result['default']);self.assertTrue(result['archive_verified'])

    def test_compatibility_pin_verifies_old_release_when_new_version_is_default(self):
        payload=archive_bytes()
        rows=[release('0.40.15',payload,digest=False),release('0.34.2',payload,digest=False)]
        with fixture(default_version='0.40.15',compatibility=('0.34.2',)) as root, \
                patch.object(versions,'_json',return_value=rows):
            listed=versions.releases(root)
            self.assertEqual([item['available'] for item in listed],[True,True])
            self.assertEqual([item['default'] for item in listed],[True,False])
            original=runtime_assets.runtime_executable('ollama',root).read_bytes()
            with patch.object(versions,'_json',return_value=rows[1]), \
                    patch.object(versions,'_open',return_value=io.BytesIO(payload)):
                result=versions.install('0.34.2',root)
            self.assertTrue(result['archive_verified']);self.assertFalse(result['default'])
            self.assertEqual(result['sha256'],hashlib.sha256(payload).hexdigest())
            self.assertEqual(versions.resolve(root=root)['id'],'ollama-0.40.15')
            self.assertEqual(runtime_assets.runtime_executable('ollama',root).read_bytes(),original)

    def test_compatibility_pin_mismatch_still_blocks_install(self):
        payload=archive_bytes()
        with fixture(default_version='0.40.15',compatibility=('0.34.2',)) as root:
            lock=json.loads((root/'runtime-lock.json').read_text(encoding='utf-8'))
            lock['ollama_compatibility'][0]['sha256']='0'*64
            (root/'runtime-lock.json').write_text(json.dumps(lock),encoding='utf-8')
            with patch.object(versions,'_json',return_value=release('0.34.2',payload,digest=False)), \
                    patch.object(versions,'_open',return_value=io.BytesIO(payload)), \
                    patch.object(versions,'_extract') as extracted:
                with self.assertRaisesRegex(ValueError,'SHA256 mismatch'):versions.install('0.34.2',root)
            extracted.assert_not_called()
            self.assertFalse((root/'runtimes/ollama-0.34.2').exists())

    def test_wrong_official_asset_url_is_not_used(self):
        payload=archive_bytes();item=release('0.40.6',payload)
        item['assets'][0]['browser_download_url']='https://example.invalid/ollama.exe'
        with fixture() as root,patch.object(versions,'_json',return_value=item),patch.object(versions,'_open') as download:
            with self.assertRaisesRegex(ValueError,'not found'):versions.install('0.40.6',root)
            download.assert_not_called()

    def test_partial_download_is_not_installed(self):
        payload=archive_bytes()
        with fixture() as root,patch.object(versions,'_json',return_value=release('0.40.6',payload)), \
                patch.object(versions,'_open',return_value=io.BytesIO(payload[:-10])):
            with self.assertRaisesRegex(ValueError,'Incomplete'):versions.install('0.40.6',root)
            self.assertFalse((root/'runtimes/ollama-0.40.6').exists())

    def test_zip_traversal_and_executable_only_bundles_are_rejected(self):
        cases=[archive_bytes({'../escaped.txt':b'bad'}),archive_bytes(complete=False),
               archive_bytes({'OLLAMA.exe':b'duplicate case-insensitive executable'})]
        for payload in cases:
            with self.subTest(payload_size=len(payload)),fixture() as root, \
                    patch.object(versions,'_json',return_value=release('0.40.6',payload)), \
                    patch.object(versions,'_open',return_value=io.BytesIO(payload)):
                with self.assertRaises(ValueError):versions.install('0.40.6',root)
                self.assertFalse((root/'runtimes/ollama-0.40.6').exists())
                self.assertFalse((root/'runtimes/.downloads/escaped.txt').exists())

    def test_single_archive_root_folder_is_supported_without_mixing_versions(self):
        payload=archive_bytes(prefix='ollama-windows-amd64/')
        with fixture() as root,patch.object(versions,'_json',return_value=release('0.40.6',payload)), \
                patch.object(versions,'_open',return_value=io.BytesIO(payload)):
            result=versions.install('0.40.6',root)
            self.assertTrue(Path(result['executable']).is_file())
            self.assertFalse((Path(result['directory'])/'ollama-windows-amd64').exists())

    def test_existing_partial_folder_is_not_overwritten(self):
        with fixture() as root:
            partial=root/'runtimes/ollama-0.40.6';partial.mkdir();(partial/'keep.txt').write_bytes(b'keep')
            with patch.object(versions,'_json') as query:
                with self.assertRaisesRegex(ValueError,'incomplete runtime folder'):versions.install('0.40.6',root)
            self.assertEqual((partial/'keep.txt').read_bytes(),b'keep');query.assert_not_called()

    def test_hy_launch_uses_selected_bundle_for_executable_cwd_and_shared_models(self):
        import hy_backend
        import test_runtime_launch_diagnostics as lifecycle
        helper=lifecycle.RuntimeLaunchDiagnosticsTests()
        actual_executable=runtime_assets.runtime_executable
        with helper.fixture() as f:
            lock={'ollama':{'version':'0.34.2','directory':'runtimes/ollama-0.34.2','executable':'ollama.exe'}}
            (f.root/'runtime-lock.json').write_text(json.dumps(lock),encoding='utf-8')
            selected=create_bundle(f.root,'0.40.6');f.cfg['runtime_id']='ollama-0.40.6'
            with patch('runtime_assets.runtime_executable',side_effect=actual_executable), \
                    patch('native_process.native_runtime_directory',return_value=str(selected/'lib/ollama')):
                with hy_backend.runtime(f.cfg,require_model=False) as active:
                    launch=f.mocks['native_process.popen'].call_args
                    self.assertEqual(launch.args[0][0],str(selected/'ollama.exe'))
                    self.assertEqual(launch.kwargs['cwd'],selected)
                    self.assertEqual(launch.kwargs['library_directory'],str(selected/'lib/ollama'))
                    self.assertEqual(launch.kwargs['env']['OLLAMA_MODELS'],str(f.root/'models/ollama'))
                    self.assertEqual(active['_runtime_launch']['runtime_id'],'ollama-0.40.6')

    def test_hy_freezes_preference_selection_only_when_runtime_key_is_missing(self):
        import hy_backend
        import test_runtime_launch_diagnostics as lifecycle
        helper=lifecycle.RuntimeLaunchDiagnosticsTests()
        for explicit in ('missing','',None):
            with self.subTest(explicit_default=explicit),helper.fixture() as f:
                selected='ollama-0.40.6'
                f.mocks['desktop_bridge.preferences'].return_value.update(runtime_id=selected)
                is_default=explicit!='missing'
                if is_default:f.cfg['runtime_id']=explicit
                with hy_backend.runtime(f.cfg,require_model=False) as active:
                    self.assertEqual(active['runtime_id'],explicit if is_default else selected)
                    selection=f.mocks['runtime_assets.runtime_executable'].call_args
                    self.assertEqual(selection.kwargs,{} if is_default else {'runtime_id':selected})
                    # A later preference change cannot alter the active snapshot.
                    f.mocks['desktop_bridge.preferences'].return_value['runtime_id']='ollama-0.99.0'
                    self.assertEqual(active['runtime_id'],explicit if is_default else selected)
                    f.mocks['desktop_bridge.preferences'].assert_called_once()

    def test_model_registration_uses_the_selected_runtime_without_inference(self):
        import model_store as store
        from test_model_store import sample_gguf
        captures=[]
        with fixture() as root,patch.object(store,'ROOT',root):
            path=root/'models/invented.gguf';path.parent.mkdir();path.write_bytes(sample_gguf())
            def owned_runtime(cfg,**kwargs):
                captures.append((dict(cfg),dict(kwargs)))
                return nullcontext({'endpoint':'http://127.0.0.1:1'})
            def create(request,**kwargs):
                data=json.loads(request.data);digest=next(iter(data['files'].values()))
                manifest=root/'models/ollama/manifests/registry.ollama.ai/library'/data['model'].replace(':','/')
                store.save(manifest,{'config':{'digest':digest,'size':path.stat().st_size},
                    'layers':[{'mediaType':'application/vnd.ollama.image.model','digest':digest,'size':path.stat().st_size}]})
                return io.BytesIO(b'{"status":"success"}')
            opener=type('Opener',(),{'open':staticmethod(create)})()
            with patch('desktop_bridge.preferences',return_value={'runtime_id':'ollama-0.40.6'}), \
                    patch('hy_backend.runtime',side_effect=owned_runtime), \
                    patch.object(store.urllib.request,'build_opener',return_value=opener),redirect_stdout(io.StringIO()):
                store.register(path,manual=True)
            self.assertEqual(captures[0][0]['runtime_id'],'ollama-0.40.6')
            self.assertEqual(captures[0][1],{'require_model':False})

    def test_explicit_registration_default_does_not_follow_app_selection(self):
        import model_store as store
        with patch('desktop_bridge.preferences',return_value={'runtime_id':'ollama-0.40.6'}) as saved:
            self.assertEqual(store.registration_runtime('invented','')['runtime_id'],'')
            self.assertEqual(store.registration_runtime('invented',None)['runtime_id'],'')
            self.assertEqual(store.registration_runtime('invented','ollama-0.34.2')['runtime_id'],'ollama-0.34.2')
            saved.assert_not_called()

    def test_auto_variant_registration_keeps_the_job_runtime_snapshot(self):
        from gpu_policy import prepare_model
        cfg={'runtime_id':'ollama-0.40.6','_hardware_policy':{},
             '_hardware_pending_model':{'path':'invented-not-opened.gguf','digest':'invented',
                                        'info':{'filename':'invented.gguf'}}}
        with patch('model_store.register',return_value='invented:latest') as register:
            result=prepare_model(cfg)
        self.assertEqual(register.call_args.kwargs['runtime_id'],'ollama-0.40.6')
        self.assertEqual(result['runtime_id'],'ollama-0.40.6')
        self.assertEqual(result['model'],'invented:latest')


if __name__=='__main__':unittest.main()
