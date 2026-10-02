import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock
import desktop_bridge as bridge
import hy_backend


class DesktopBridgeTests(unittest.TestCase):
    def test_auto_preserves_environment_and_all_requests_spread(self):
        self.assertEqual(bridge.gpu_environment({'gpu_mode':'auto'}),{})
        env=bridge.gpu_environment({'gpu_mode':'all'})
        self.assertEqual(env['OLLAMA_SCHED_SPREAD'],'1')
        self.assertIsNone(env['CUDA_VISIBLE_DEVICES'])

    def test_two_gpu_selection_uses_stable_ids_and_single_model_spread(self):
        ids=['GPU-0000-0001','GPU-0000-0002']
        with patch.object(bridge,'nvidia_devices',return_value=[{'id':i} for i in ids]):
            env=bridge.gpu_environment({'gpu_mode':'selected','gpu_ids':ids})
            self.assertEqual(env['CUDA_VISIBLE_DEVICES'],','.join(ids))
            self.assertEqual(env['OLLAMA_SCHED_SPREAD'],'1')
            self.assertEqual(env['OLLAMA_VULKAN'],'0')
            env=bridge.gpu_environment({'gpu_mode':'selected','gpu_ids':ids[:1]})
            self.assertEqual(env['OLLAMA_SCHED_SPREAD'],'0')

    def test_disconnected_or_empty_selection_does_not_silently_choose_another(self):
        with self.assertRaises(ValueError): bridge.validate_settings({'gpu_mode':'selected','gpu_ids':[]})
        with patch.object(bridge,'nvidia_devices',return_value=[]):
            with self.assertRaises(ValueError): bridge.gpu_environment({'gpu_mode':'selected','gpu_ids':['GPU-0001']})

    def test_preferences_and_run_preserve_existing_cli_contract(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(bridge,'ROOT',Path(temp)),patch('translate_game.main') as run,patch('sys.argv',['cli']),patch('model_store.require_selected',return_value='synthetic:latest'):
            data={'path':'X:/synthetic-project','source':False,'tasks':['font','names'],
                  'settings':dict(bridge.DEFAULTS,output='result',theme='dark',gpu_mode='all')}
            with contextlib.redirect_stdout(io.StringIO()):bridge.run_request(data)
            import sys
            self.assertEqual(sys.argv[:5],['cli','tasks','--tasks','names','font'])
            self.assertIn('--project',sys.argv)
            self.assertEqual(bridge.preferences()['theme'],'dark')
            self.assertEqual(bridge.preferences()['gpu_mode'],'all')
            run.assert_called_once()

    def test_rejects_no_jobs_before_writing_settings_or_starting_work(self):
        with patch.object(bridge,'save_settings') as save,patch('translate_game.main') as run:
            with self.assertRaises(ValueError):bridge.run_request({'path':'X:/fake','tasks':[]})
            save.assert_not_called();run.assert_not_called()

    def test_runtime_uses_policy_and_removes_descriptor_on_error(self):
        import engine
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);exe=root/'runtimes/ollama-0.34.2/ollama.exe';exe.parent.mkdir(parents=True);exe.touch()
            proc=Mock(pid=123456);proc.poll.return_value=None
            def api(endpoint,route):return {'models':[{'name':hy_backend.DEFAULT_MODEL}]} if route=='/api/tags' else {'version':'test'}
            with patch.object(engine,'ROOT',root),patch.object(bridge,'preferences',return_value=dict(bridge.DEFAULTS,gpu_mode='all')),\
                 patch.object(hy_backend.subprocess,'Popen',return_value=proc) as launch,patch('native_process.run') as stop,\
                 patch.object(hy_backend,'api',side_effect=api),patch.dict('os.environ',{'CUDA_VISIBLE_DEVICES':'0'}):
                with self.assertRaisesRegex(RuntimeError,'synthetic failure'):
                    with hy_backend.runtime({'model':hy_backend.DEFAULT_MODEL}) as cfg:
                        self.assertTrue(list((root/'data/runtime').glob('*.json')))
                        self.assertEqual(launch.call_args.kwargs['env']['OLLAMA_SCHED_SPREAD'],'1')
                        self.assertNotIn('CUDA_VISIBLE_DEVICES',launch.call_args.kwargs['env'])
                        raise RuntimeError('synthetic failure')
                self.assertFalse(list((root/'data/runtime').glob('*.json')))
                stop.assert_called_once();proc.wait.assert_called_once()

    def test_nvidia_csv_unknown_values_are_not_reported_as_zero(self):
        text='GPU-0001, Example GPU, 12288, [N/A], 15, 600.00\n'
        with patch.object(bridge,'command',return_value=text):
            gpu=bridge.nvidia_devices('fake-smi')[0]
            self.assertIsNone(gpu['used']);self.assertEqual(gpu['total'],12288)


if __name__=='__main__':unittest.main()
