from contextlib import contextmanager
import io
import json
import unittest
from unittest.mock import Mock, patch

import gpu_runtime_pool
import hy_backend
import test_runtime_launch_diagnostics as launch_tests


class RocmRuntimeTests(unittest.TestCase):
    @contextmanager
    def fixture(self, *, model_vram, vulkan_index):
        # Exercise runtime routing, launch environment, warmup and /api/ps
        # validation with simulated native and service responses.
        with launch_tests.RuntimeLaunchDiagnosticsTests.fixture(self) as f:
            f.cfg['_runtime_device'] = dict(id='DXGI-1234', name='AMD card',
                vendor='amd', backend='rocm', selector=7, vulkan_index=vulkan_index)
            f.cfg['_hardware_policy'] = {'vendor': 'amd'}
            f.cfg['runtime_id'] = 'ollama-0.40.15'
            fallback_process = launch_tests.OwnedProcess()
            fallback_process.pid += 1
            f.mocks['native_process.popen'].side_effect = [f.proc, fallback_process]
            version = {'version': '0.40.15'}
            models = {'models': [{'name': f.cfg['model']}]}
            status = {'models': [{'name': f.cfg['model'], 'size_vram': model_vram}]}
            f.mocks['hy_backend.api'].side_effect = [version, models, status, version, models]
            resolver = Mock(return_value={'index': 4, 'variant': 'rocm_v7_1'})
            vulkan = Mock(return_value={'index': 6})
            opener = Mock()
            opener.open.return_value = io.BytesIO(b'{"done": true}')
            with patch('gpu_policy.prepare_model', side_effect=dict), \
                 patch('amd_rocm.resolve_rocm', resolver), \
                 patch('vulkan_inventory.resolve_selector', vulkan), \
                 patch('model_store.ensure_registered_template', return_value={}), \
                 patch('model_diagnostics.collect', return_value={}), \
                 patch('hy_backend.urllib.request.build_opener', return_value=opener), \
                 patch.dict('os.environ', {
                     'CUDA_VISIBLE_DEVICES': 'GPU-other',
                     'GGML_VK_VISIBLE_DEVICES': '99',
                     'HSA_OVERRIDE_GFX_VERSION': '99.0.0',
                     'OLLAMA_LIBRARY_PATH': 'unrelated-runtime',
                 }):
                yield f, resolver, vulkan, opener

    def test_official_rocm_masks_other_backends_and_validates_model_offload(self):
        with self.fixture(model_vram=8192, vulkan_index=3) as (f, resolver, vulkan, opener):
            with hy_backend.runtime(f.cfg) as active:
                self.assertEqual(active['_runtime_launch']['rocm_model_vram'], 8192)
                self.assertEqual(active['_runtime_device']['backend'], 'rocm')
                self.assertEqual(active['_runtime_device']['selector'], 4)
            child = f.mocks['native_process.popen'].call_args.kwargs['env']
            self.assertEqual(child['CUDA_VISIBLE_DEVICES'], '-1')
            self.assertEqual(child['HIP_VISIBLE_DEVICES'], '4')
            self.assertEqual(child['OLLAMA_LLM_LIBRARY'], 'rocm_v7_1')
            self.assertEqual(child['OLLAMA_VULKAN'], '0')
            self.assertTrue({'GGML_VK_VISIBLE_DEVICES', 'HSA_OVERRIDE_GFX_VERSION',
                             'OLLAMA_LIBRARY_PATH'}.isdisjoint(child))
            resolver.assert_called_once()
            self.assertEqual(resolver.call_args.args[0], f.executable)
            vulkan.assert_not_called()
            self.assertEqual(json.loads(opener.open.call_args.args[0].data)['model'], f.cfg['model'])

    def test_rocm_without_model_vram_switches_once_to_verified_vulkan(self):
        with self.fixture(model_vram=0, vulkan_index=3) as (f, resolver, vulkan, _):
            with hy_backend.runtime(f.cfg) as active:
                self.assertIn('did not offload', active['_amd_fallback_reason'])
                self.assertEqual(active['_runtime_device']['backend'], 'vulkan')
                self.assertEqual(active['_runtime_device']['planned_selector'], 3)
                self.assertEqual(active['_runtime_device']['selector'], 6)
            first, second = f.mocks['native_process.popen'].call_args_list
            self.assertEqual(first.kwargs['env']['OLLAMA_LLM_LIBRARY'], 'rocm_v7_1')
            self.assertEqual(second.kwargs['env']['OLLAMA_VULKAN'], '1')
            self.assertEqual(second.kwargs['env']['HIP_VISIBLE_DEVICES'], '-1')
            self.assertEqual(second.kwargs['env']['GGML_VK_VISIBLE_DEVICES'], '6')
            self.assertNotIn('OLLAMA_LLM_LIBRARY', second.kwargs['env'])
            resolver.assert_called_once()
            vulkan.assert_called_once()

    def test_rocm_without_model_vram_and_without_verified_vulkan_raises(self):
        with self.fixture(model_vram=0, vulkan_index=None) as (f, resolver, vulkan, _):
            with self.assertRaisesRegex(hy_backend.RocmStartupError, 'did not offload'):
                with hy_backend.runtime(f.cfg):
                    self.fail('A CPU model is not a successful ROCm start')
            f.mocks['native_process.popen'].assert_called_once()
            resolver.assert_called_once()
            vulkan.assert_not_called()

    def test_unmatched_official_rocm_device_skips_warmup_and_uses_vulkan(self):
        with self.fixture(model_vram=0, vulkan_index=3) as (f, resolver, vulkan, opener):
            resolver.return_value = None
            with hy_backend.runtime(f.cfg) as active:
                self.assertEqual(active['_runtime_device']['backend'], 'vulkan')
                self.assertEqual(active['_runtime_device']['selector'], 6)
                self.assertNotIn('rocm_model_vram', active['_runtime_launch'])
            f.mocks['native_process.popen'].assert_called_once()
            resolver.assert_called_once()
            vulkan.assert_called_once()
            opener.open.assert_not_called()

    def test_application_failure_does_not_repeat_the_started_job(self):
        started = []

        @contextmanager
        def start(cfg, require_model):
            started.append(cfg)
            yield {}

        config = {'_runtime_device': {'name': 'AMD card', 'backend': 'vulkan'}}
        with patch.object(hy_backend, '_runtime', start):
            with self.assertRaises(hy_backend.RocmStartupError):
                with hy_backend.runtime(config):
                    raise hy_backend.RocmStartupError('request failed after startup')
        self.assertEqual(len(started), 1)

    def test_during_job_fallback_is_only_for_failed_amd_device(self):
        starts = []

        @contextmanager
        def start(cfg):
            starts.append(cfg)
            device = dict(cfg['_runtime_device'])
            if cfg.get('_amd_force_vulkan'):
                device['backend'] = 'vulkan'
            yield dict(cfg, _runtime_device=device)

        devices = [
            dict(id='nvidia', name='NVIDIA', vendor='nvidia', backend='cuda', selector='GPU-1'),
            dict(id='amd', name='AMD', vendor='amd', backend='rocm', selector=1, vulkan_index=2),
        ]
        with patch.object(gpu_runtime_pool, 'runtime', start):
            with gpu_runtime_pool.runtime_pool({}, devices) as pool:
                self.assertEqual([w['device_backend'] for w in pool.workers], ['cuda', 'rocm'])
                pool.fallback_after_failure('amd', OSError('connection reset'))
                pool.stop('amd')
                self.assertEqual(pool.start('amd')['device_backend'], 'vulkan')
                self.assertIs(pool.workers[0], pool._active['nvidia'])
        self.assertEqual([x.get('_amd_force_vulkan') for x in starts], [None, None, True])


if __name__ == '__main__':
    unittest.main()
