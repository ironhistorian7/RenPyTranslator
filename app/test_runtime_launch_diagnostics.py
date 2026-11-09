"""Invented native files and mocked children only; no actual server or GPU use."""
from contextlib import ExitStack,nullcontext,contextmanager
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import hy_backend
from runtime_assets import runtime_executable


class OwnedProcess:
    pid=765432

    def __init__(self):
        self.returncode=None
        self.wait_calls=[]
        self._rpt_native_launch={'parent_directory_restored':True,
                                 'gui_bundle_directory':None}

    def poll(self):return self.returncode

    def wait(self,timeout=None):
        self.wait_calls.append(timeout)
        if self.returncode is None:self.returncode=-9
        return self.returncode


class RuntimeLaunchDiagnosticsTests(unittest.TestCase):
    def setUp(self):hy_backend._cached_binary_identity.cache_clear()

    @contextmanager
    def fixture(self,*,spawn_error=None,kill_error=None,diagnostic=''):
        with tempfile.TemporaryDirectory() as directory,ExitStack() as stack:
            root=Path(directory);executable=runtime_executable('ollama',root)
            executable.parent.mkdir(parents=True);executable.write_bytes(b'MZ-invented-executable')
            proc=OwnedProcess()
            probe=Mock();probe.getsockname.return_value=('127.0.0.1',43210)
            socket=Mock();socket.__enter__=Mock(return_value=probe);socket.__exit__=Mock(return_value=False)
            def kill(*args,**kwargs):
                if kill_error is not None:raise kill_error
                proc.returncode=-9
                return subprocess.CompletedProcess(args[0],0,b'owned child stopped',b'')
            patches={
                'engine.ROOT':root,
                'runtime_assets.runtime_executable':Mock(return_value=executable),
                'native_process.popen':Mock(return_value=proc,side_effect=spawn_error),
                'native_process.run':Mock(side_effect=kill),
                'native_process.native_runtime_directory':Mock(return_value=None),
                'hy_backend.socket.socket':Mock(return_value=socket),
                'hy_backend._windows_file_version':Mock(return_value={'file_version':'1.2.3.4'}),
                'desktop_bridge.preferences':Mock(return_value={'gpu_mode':'auto','gpu_ids':[]}),
                'desktop_bridge.gpu_environment':Mock(return_value={}),
                'hy_backend.api':Mock(side_effect=[{'version':'invented-api-version'},
                    {'models':[{'name':'invented-model','digest':'sha256:invented-digest','size':42}]}]),
                'model_runtime.model_session':Mock(return_value=nullcontext()),
                'cancel_runtime.register':Mock(),
                'cancel_runtime.unregister':Mock(),
                'diagnostics.stage':Mock(),
                'request_budget.forget':Mock(),
            }
            for target,value in patches.items():stack.enter_context(patch(target,value))
            stack.enter_context(patch.dict('os.environ',{'RPT_CUDA_DIAGNOSTICS':diagnostic},clear=False))
            # Keep the test independent of any diagnostic setting in the host.
            stack.enter_context(patch.dict('os.environ',{'CUDA_LAUNCH_BLOCKING':''},clear=False))
            cfg={'model':'invented-model','num_ctx':16384,'parallel':2,
                 '_recovery_run_id':'invented-run','_runtime_generation':3}
            yield SimpleNamespace(root=root,executable=executable,proc=proc,cfg=cfg,mocks=patches)

    def test_shared_exit_state_records_forced_cleanup_and_flushed_identity(self):
        with self.fixture() as f:
            with hy_backend.runtime(f.cfg,require_model=False) as active:
                state=active['_runtime_exit']
                self.assertIs(state,f.cfg['_runtime_exit'])
                self.assertIs(state,dict(active)['_runtime_exit'])
                launch=active['_runtime_launch']
                self.assertEqual(launch['run_id'],'invented-run')
                self.assertEqual(launch['runtime_generation'],3)
                self.assertEqual(launch['api_version'],'invented-api-version')
                self.assertEqual(launch['model_identity']['digest'],'sha256:invented-digest')
                self.assertEqual(launch['executable_identity']['file_version'],'1.2.3.4')
                self.assertEqual(launch['executable_identity']['sha256'],
                                 hashlib.sha256(b'MZ-invented-executable').hexdigest())
                self.assertEqual(active['_cuda_diagnostic_mode'],'off')
                log_path=Path(active['_runtime_log'])
                self.assertNotIn('RPT runtime exit:',log_path.read_text(encoding='utf-8'))
            self.assertEqual(state['cleanup_action'],'taskkill_owned_tree')
            self.assertTrue(state['alive_before_cleanup'])
            self.assertEqual(state['termination'],'application_cleanup')
            self.assertEqual(state['kill_returncode'],0)
            self.assertEqual(state['process_returncode'],-9)
            self.assertTrue(state['cleanup_finished_utc'].endswith('+00:00'))
            final=json.loads(log_path.read_text(encoding='utf-8').split('RPT runtime exit: ')[-1])
            self.assertEqual(final,state)
            self.assertEqual(f.mocks['hy_backend.api'].call_count,2)
            f.mocks['native_process.run'].assert_called_once()

    def test_natural_exit_is_not_reported_as_application_kill(self):
        with self.fixture() as f:
            with hy_backend.runtime(f.cfg,require_model=False) as active:
                state=active['_runtime_exit'];f.proc.returncode=17
            self.assertEqual(state['termination'],'natural_exit')
            self.assertFalse(state['alive_before_cleanup'])
            self.assertEqual(state['process_returncode'],17)
            self.assertEqual(state['cleanup_action'],'none')
            self.assertIsNone(state['kill_returncode'])
            f.mocks['native_process.run'].assert_not_called()

    def test_operation_exception_keeps_same_state_until_cleanup_finishes(self):
        error=RuntimeError('invented operation error')
        with self.fixture() as f:
            with self.assertRaises(RuntimeError) as caught:
                with hy_backend.runtime(f.cfg,require_model=False) as active:
                    state=active['_runtime_exit'];raise error
            self.assertIs(caught.exception,error)
            self.assertIs(error.runtime_exit,state)
            self.assertTrue(state['cleanup_finished_utc'])
            self.assertEqual(state['termination'],'application_cleanup')

    def test_launch_failure_exposes_exit_state_without_an_owned_child(self):
        error=OSError('invented launch failure')
        with self.fixture(spawn_error=error) as f:
            with self.assertRaises(OSError) as caught:
                with hy_backend.runtime(f.cfg,require_model=False):self.fail('must not yield')
            state=f.cfg['_runtime_exit']
            self.assertIs(caught.exception.runtime_exit,state)
            self.assertEqual(state['termination'],'not_started')
            self.assertIsNone(state['server_pid'])
            self.assertTrue(state['cleanup_finished_utc'])
            f.mocks['native_process.run'].assert_not_called()

    def test_cleanup_failure_is_preserved_and_logged_before_propagation(self):
        error=OSError('invented taskkill failure')
        with self.fixture(kill_error=error) as f:
            with self.assertRaises(OSError) as caught:
                with hy_backend.runtime(f.cfg,require_model=False) as active:
                    state=active['_runtime_exit'];log_path=Path(active['_runtime_log'])
            self.assertIs(caught.exception,error)
            self.assertIs(error.runtime_exit,state)
            self.assertEqual(state['termination'],'cleanup_failed')
            self.assertIn('invented taskkill failure',state['kill_error'])
            self.assertIsNone(state['process_returncode'])
            self.assertTrue(state['cleanup_finished_utc'])
            self.assertIn('RPT runtime exit:',log_path.read_text(encoding='utf-8'))
            f.mocks['cancel_runtime.unregister'].assert_called_once_with(f.proc)

    def test_blocking_diagnostic_is_opt_in_and_only_changes_child_environment(self):
        with self.fixture(diagnostic='blocking') as f:
            with hy_backend.runtime(f.cfg,require_model=False) as active:
                child=f.mocks['native_process.popen'].call_args.kwargs['env']
                self.assertEqual(child['CUDA_LAUNCH_BLOCKING'],'1')
                self.assertEqual(active['_cuda_diagnostic_mode'],'blocking')
                self.assertEqual(active['_runtime_launch']['startup_timeout_seconds'],60)
                import os
                self.assertEqual(os.environ['CUDA_LAUNCH_BLOCKING'],'')

    def test_normal_mode_does_not_enable_blocking_or_change_defaults(self):
        with self.fixture() as f:
            with hy_backend.runtime(f.cfg,require_model=False) as active:
                child=f.mocks['native_process.popen'].call_args.kwargs['env']
                self.assertEqual(child['CUDA_LAUNCH_BLOCKING'],'')
                self.assertEqual(child['OLLAMA_NUM_PARALLEL'],'2')
                self.assertEqual(child['OLLAMA_KV_CACHE_TYPE'],'q8_0')
                self.assertIsNone(active['_runtime_launch']['startup_timeout_seconds'])

    def test_binary_identity_is_cached_and_does_not_hash_models(self):
        with tempfile.TemporaryDirectory() as directory:
            executable=Path(directory)/'invented.exe';executable.write_bytes(b'MZ-invented')
            with patch('hy_backend._windows_file_version',return_value={'file_version':'4.3.2.1'}) as version:
                first=hy_backend.binary_identity(executable)
                second=hy_backend.binary_identity(executable)
                self.assertEqual(first,second);self.assertIsNot(first,second)
                version.assert_called_once()
            with patch.object(Path,'open') as opened,patch.object(Path,'stat') as stated:
                model=hy_backend.binary_identity(Path(directory)/'invented.gguf')
                self.assertEqual(model['hash_skipped'],'not_native_binary')
                opened.assert_not_called();stated.assert_not_called()

    def test_large_native_binary_reports_skipped_hash_without_reading_content(self):
        path=Path('invented-large.dll')
        with patch.object(Path,'resolve',return_value=path),patch.object(Path,'stat',return_value=
                SimpleNamespace(st_size=hy_backend._MAX_BINARY_HASH_BYTES+1,st_mtime_ns=1)), \
                patch.object(Path,'open') as opened, \
                patch('hy_backend._windows_file_version',return_value={'file_version':'1.0.0.0'}):
            identity=hy_backend.binary_identity(path)
            self.assertEqual(identity['hash_skipped'],'size_limit')
            self.assertEqual(identity['file_version'],'1.0.0.0')
            opened.assert_not_called()


if __name__=='__main__':unittest.main()
