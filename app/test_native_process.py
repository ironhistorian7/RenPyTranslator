"""Native launch isolation and pre-crash diagnostics; no model or game runs."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import native_process as native
import runtime_recovery as recovery
from diagnostics import runtime_settings


class NativeLaunchTests(unittest.TestCase):
    def fake_kernel(self, directory):
        kernel=Mock();kernel.directory=directory
        return kernel

    def windows_patch(self,kernel):
        return (patch('native_process._kernel32',return_value=kernel),
                patch('native_process._get_directory',side_effect=lambda k:k.directory),
                patch('native_process._set_directory',side_effect=lambda k,p:setattr(k,'directory',p)))

    def test_only_gui_path_entries_removed_without_mutating_environment(self):
        bundle=str(Path(tempfile.gettempdir())/'rpt-bundle')
        paths=[bundle,str(Path(bundle)/'tools'),bundle+'-other',str(Path(tempfile.gettempdir())/'models'),'']
        env={'PATH':os.pathsep.join(paths),'OLLAMA_MODELS':'portable-models','UNCHANGED':'value'}
        result,removed=native.clean_environment(env,bundle)
        self.assertEqual(removed,paths[:2])
        self.assertEqual(result['PATH'],os.pathsep.join(paths[2:]))
        self.assertEqual(env['PATH'],os.pathsep.join(paths))
        self.assertEqual(result['OLLAMA_MODELS'],'portable-models')

    @unittest.skipUnless(os.name=='nt','Windows DLL search path')
    def test_child_is_clean_and_parent_restored(self):
        bundle=str(Path(tempfile.gettempdir())/'rpt-bundle')
        kernel=self.fake_kernel(bundle);proc=Mock()
        def spawn(*args,**kwargs):
            self.assertIsNone(kernel.directory)
            self.assertNotIn(bundle,kwargs['env']['PATH'])
            return proc
        a,b,c=self.windows_patch(kernel)
        with a,b,c,patch.object(sys,'_MEIPASS',bundle,create=True),patch.object(sys,'frozen',True,create=True),patch('native_process.subprocess.Popen',side_effect=spawn):
            result=native.popen(['native-helper'],env={'PATH':bundle+os.pathsep+'system'})
        self.assertEqual(kernel.directory,bundle)
        self.assertTrue(result._rpt_native_launch['parent_directory_restored'])
        self.assertTrue(result._rpt_native_launch['frozen'])

    @unittest.skipUnless(os.name=='nt','Windows DLL search path')
    def test_spawn_error_still_restores_parent_and_has_diagnostics(self):
        kernel=self.fake_kernel('gui-directory');a,b,c=self.windows_patch(kernel)
        with a,b,c,patch('native_process.subprocess.Popen',side_effect=OSError('spawn failed')):
            with self.assertRaises(OSError) as raised:native.popen(['native-helper'])
        self.assertEqual(kernel.directory,'gui-directory')
        self.assertTrue(raised.exception.native_launch['parent_directory_restored'])

    @unittest.skipUnless(os.name=='nt','Windows DLL search path')
    def test_cleanup_failure_stops_the_created_child(self):
        kernel=self.fake_kernel('gui-directory');proc=Mock()
        def set_directory(k,path):
            if path is not None:raise OSError('restore failed')
            k.directory=path
        a,b,_=self.windows_patch(kernel)
        with a,b,patch('native_process._set_directory',side_effect=set_directory),patch('native_process.subprocess.Popen',return_value=proc):
            with self.assertRaisesRegex(OSError,'restore failed'):native.popen(['native-helper'])
        proc.kill.assert_called_once();proc.wait.assert_called_once()

    @unittest.skipUnless(os.name=='nt','Windows DLL search path')
    def test_concurrent_launches_do_not_restore_another_launch_directory(self):
        kernel=self.fake_kernel('gui-directory');seen=[]
        def spawn(*args,**kwargs):
            self.assertIsNone(kernel.directory)
            time.sleep(.005)
            self.assertIsNone(kernel.directory)
            seen.append(1);return Mock()
        a,b,c=self.windows_patch(kernel)
        with a,b,c,patch('native_process.subprocess.Popen',side_effect=spawn),ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _:native.popen(['native-helper']),range(12)))
        self.assertEqual(len(seen),12)
        self.assertEqual(kernel.directory,'gui-directory')

    def test_native_run_keeps_output_and_failure_status(self):
        result=native.run([sys.executable,'-c','print("native helper")'],capture_output=True,text=True,check=True)
        self.assertEqual(result.stdout.strip(),'native helper')
        with self.assertRaises(subprocess.CalledProcessError) as raised:
            native.run([sys.executable,'-c','import sys;print("failed");sys.exit(3)'],capture_output=True,text=True,check=True)
        self.assertEqual(raised.exception.returncode,3)
        self.assertEqual(raised.exception.stdout.strip(),'failed')

    def test_timed_out_helper_is_stopped(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            native.run([sys.executable,'-c','import time;time.sleep(30)'],capture_output=True,timeout=.1)

    @unittest.skipUnless(os.name=='nt','Windows DLL search path')
    def test_real_windows_child_avoids_bundle_and_parent_is_restored(self):
        kernel=native._kernel32();original=native._get_directory(kernel)
        bundle=str(Path(__file__).resolve().parents[1]/'_internal')
        try:
            native._set_directory(kernel,bundle)
            with patch.object(sys,'_MEIPASS',bundle,create=True),patch.object(sys,'frozen',True,create=True):
                result=native.self_check()
            self.assertFalse(native._inside(result['dll_path'],bundle))
            self.assertEqual(native._get_directory(kernel),bundle)
            self.assertTrue(result['native_launch']['parent_directory_restored'])
        finally:native._set_directory(kernel,original)

    @unittest.skipUnless(os.name=='nt','Windows native redistributables')
    def test_native_runtime_redistributable_path_available_in_the_child(self):
        root=Path(__file__).resolve().parents[1]
        directory=native.native_runtime_directory(root/'runtimes/ollama-0.34.2/ollama.exe')
        self.assertIsNotNone(directory)
        result=native.self_check(directory)
        self.assertEqual(os.path.normcase(result['first_path_entry']),os.path.normcase(directory))
        self.assertEqual(result['native_launch']['native_library_directory'],directory)
        self.assertIsNone(result['native_launch']['dll_directory_for_child'])


class ModuleEvidenceTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='nt','Windows module inspection')
    def test_real_module_reader_on_the_test_process_only(self):
        original=recovery.powershell
        def own_python_probe(script,timeout=6):
            script=script.replace("$_.Name -in @('ollama.exe','llama-server.exe')","$_.ProcessId -eq "+str(os.getpid()))
            return original(script,timeout)
        with patch('runtime_recovery.powershell',side_effect=own_python_probe):
            result=recovery.module_snapshot(os.getpid())
        self.assertNotIn('collection_error',result)
        own=[p for p in result['processes'] if p['pid']==os.getpid()]
        self.assertEqual(len(own),1)
        self.assertIsNone(own[0]['collection_error'])
        self.assertTrue(any(m['name'].lower()=='ucrtbase.dll' and m['path'] and m['version'] for m in own[0]['modules']))

    def test_module_paths_and_versions_and_gui_overlap_retained(self):
        bundle=str(Path(tempfile.gettempdir())/'rpt-bundle')
        rows=[{'pid':20,'executable':'llama-server.exe','modules':[{'name':'VCRUNTIME140.dll','path':str(Path(bundle)/'VCRUNTIME140.dll'),'version':'14.42'}],'collection_error':None}]
        with patch('runtime_recovery.powershell',return_value={'returncode':0,'stdout':json.dumps(rows)}):
            result=recovery.module_snapshot(10,bundle)
        self.assertEqual(result['processes'],rows)
        self.assertEqual(result['gui_bundle_modules'][0]['version'],'14.42')
        self.assertTrue(result['runner_present'])
        self.assertIn('not established',result['warning'])

    def test_module_probe_error_is_a_record_not_a_translation_failure(self):
        with patch('runtime_recovery.powershell',return_value={'collection_error':'access denied'}):
            result=recovery.module_snapshot(10)
        self.assertIn('collection_error',result)
        self.assertEqual(result['runtime_pid'],10)

    @unittest.skipUnless(os.name=='nt','Windows process sampling')
    def test_each_new_runner_logged_once_before_a_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            collector=recovery.Evidence(Path(directory))
            collector.configure({'_runtime_pid':10,'_runtime_launch':{'gui_bundle_directory':'gui'}})
            calls=[0]
            def sample():
                calls[0]+=1
                if calls[0]==3:collector.stop.set()
                return {'time':'sample'}
            def processes(pid):
                runner=20 if calls[0]<3 else 30
                return {'stdout':json.dumps([{'ProcessId':runner,'ExecutablePath':'llama-server.exe'}])}
            with patch('runtime_recovery.resources',side_effect=sample),patch('runtime_recovery.process_snapshot',side_effect=processes),patch('runtime_recovery.time.monotonic',side_effect=lambda:calls[0]*20),patch.object(collector.stop,'wait',return_value=False),patch('runtime_recovery.module_snapshot',return_value={'time':'before','runtime_pid':10,'processes':[]}) as probe:
                collector._sample()
            self.assertEqual(probe.call_count,2)
            self.assertEqual(len(collector.module_history),2)
            events=[json.loads(l) for l in (Path(directory)/'data/errors/runtime-recovery.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual([e['event'] for e in events],['runtime_modules','runtime_modules'])
            self.assertEqual(events[0]['snapshot']['time'],'before')

    def test_request_error_settings_include_launch_and_actual_environment(self):
        cfg={'_runtime_launch':{'frozen':True},'_runtime_env':{'OLLAMA_KV_CACHE_TYPE':'q8_0'},'_runtime_pid':10}
        result=runtime_settings(cfg)
        self.assertEqual(result['_runtime_launch'],cfg['_runtime_launch'])
        self.assertEqual(result['_runtime_env'],cfg['_runtime_env'])


class RuntimeIntegrationTests(unittest.TestCase):
    def test_owned_server_launch_metadata_and_cleanup_without_running_a_model(self):
        import hy_backend
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            executable=root/'runtimes/ollama-0.34.2/ollama.exe'
            executable.parent.mkdir(parents=True);executable.touch()
            proc=Mock(pid=123,poll=Mock(return_value=None))
            proc._rpt_native_launch={'parent_directory_restored':True,'gui_bundle_directory':'gui'}
            with patch('engine.ROOT',root),patch('native_process.popen',return_value=proc) as spawn,patch('native_process.run') as stop,patch('native_process.native_runtime_directory',return_value=None),patch('desktop_bridge.preferences',return_value={'gpu_mode':'auto','gpu_ids':[]}),patch('desktop_bridge.gpu_environment',return_value={}),patch('hy_backend.api',side_effect=[{'version':'test'},{'models':[{'name':'fake'}]}]),patch('model_runtime.model_session',return_value=nullcontext()),patch('cancel_runtime.register') as register,patch('cancel_runtime.unregister') as unregister,patch('diagnostics.stage'),patch('request_budget.forget'):
                with hy_backend.runtime({'model':'fake','num_ctx':16384}) as active:
                    self.assertEqual(active['_runtime_pid'],123)
                    self.assertTrue(active['_runtime_launch']['parent_directory_restored'])
                    self.assertEqual(active['_runtime_env']['OLLAMA_NUM_PARALLEL'],'2')
                    descriptor=json.loads((root/'data/runtime'/(str(os.getpid())+'.json')).read_text(encoding='utf-8'))
                    self.assertEqual(descriptor['native_launch'],active['_runtime_launch'])
                    self.assertIn('RPT native launch:',Path(active['_runtime_log']).read_text(encoding='utf-8'))
                self.assertFalse((root/'data/runtime'/(str(os.getpid())+'.json')).exists())
            self.assertEqual(spawn.call_args.kwargs['cwd'],executable.parent)
            self.assertEqual(stop.call_args.args[0],['taskkill','/PID','123','/T','/F'])
            register.assert_called_once_with(proc);unregister.assert_called_once_with(proc)
            proc.wait.assert_called_once()

    def test_request_trace_correlates_with_owned_server(self):
        from translation import trace_event
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'translation-requests.jsonl'
            cfg={'_trace_dir':directory,'_runtime_pid':123,'_runtime_log':'owned-server.log'}
            trace_event(cfg,'request-1','request')
            record=json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(record['server_pid'],123)
            self.assertEqual(record['server_log'],'owned-server.log')


if __name__=='__main__':unittest.main()
