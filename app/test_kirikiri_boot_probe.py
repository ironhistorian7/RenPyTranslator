import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
import app.kirikiri_boot_probe as probe

class BootSensorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.exe=self.root/'ゲーム.exe'
        b=bytearray(128); b[:2]=b'MZ'; struct.pack_into('<I',b,0x3c,64); b[64:68]=b'PE\0\0'; struct.pack_into('<H',b,68,0x14c); self.exe.write_bytes(b)
        self.process=MagicMock(pid=321, returncode=None); self.process.poll.return_value=None
        kernel=MagicMock(); kernel.GetACP.return_value=949
        self.windll=SimpleNamespace(kernel32=kernel)
    def test_text_policy(self):
        self.assertEqual(probe._candidate('Invalid multibyte sequence')['code'],'invalid_multibyte')
        self.assertIsNone(probe._candidate('SyntaxError: invalid multibyte sequence'))
        self.assertIsNone(probe._candidate('CUDA failed'))
        self.assertIsNone(probe._candidate('File not found'))
        japanese='文字列の変換に失敗しました'
        result=probe._candidate(japanese)
        self.assertEqual(result['code'],'encoding_error')
        mojibake=japanese.encode('cp932').decode('cp949')
        result=probe._candidate(mojibake)
        self.assertEqual(result['proof'],japanese)
        self.assertEqual(result['raw_proof'],mojibake)
        self.assertEqual(result['decoded_from'],'cp949_bytes_as_cp932')
        self.assertIsNone(probe._candidate('cannot convert string to integer'))
    def test_native_ready_requires_callback_and_cleanup_is_opt_in(self):
        with patch.object(probe.ctypes,'windll',self.windll), patch.object(probe.subprocess,'Popen',return_value=self.process), patch.object(probe,'_windows',return_value=[{'handle':1,'class':'Main','title':'window','dialog_text':[]}]), patch.object(probe.time,'sleep'):
            r=probe.probe_game(self.exe,'native',timeout=1,readiness=lambda s: {'ready':True,'evidence':'caller verified title control'})
        self.assertEqual(r['outcome'],'initialization_ready'); self.assertEqual(r['pid'],321)
        self.assertEqual(r['system_acp'],949); self.assertIsNone(r['code_page'])
        self.assertEqual(self.process._rpt_native_launch['native_library_directory'],str(self.exe.parent))
        self.process.terminate.assert_not_called()
    def test_dialog_candidate_and_owned_termination(self):
        windows=[{'handle':1,'class':'#32770','title':'Error','dialog_text':['Invalid multibyte sequence']}]
        with patch.object(probe.ctypes,'windll',self.windll), patch.object(probe.subprocess,'Popen',return_value=self.process), patch.object(probe,'_windows',return_value=windows):
            r=probe.probe_game(self.exe,'native',terminate_owned=True)
        self.assertEqual(r['outcome'],'initialization_failed'); self.assertEqual(r['error']['source_pid'],321)
        self.process.terminate.assert_called_once()
    def test_accessibility_helper_returns_only_valid_utf8_and_times_out(self):
        import base64
        encoded=base64.b64encode('文字列の変換に失敗しました'.encode('utf-8'))
        completed=SimpleNamespace(returncode=0, stdout=encoded+b'\nnot-base64!\n')
        with patch.object(probe,'native_run',return_value=completed):
            values,status=probe._msaa_dialog_text(123,321)
        self.assertEqual(status,'ok'); self.assertEqual(values,['文字列の変換に失敗しました'])
        with patch.object(probe,'native_run',side_effect=probe.subprocess.TimeoutExpired('probe',1)):
            values,status=probe._msaa_dialog_text(123,321)
        self.assertEqual(values,[]); self.assertEqual(status,'timeout')
    def test_engine_error_dialog(self):
        windows=[{'handle':1,'class':'TMessageForm','title':'error','dialog_text':['文字列の変換に失敗しました']}]
        with patch.object(probe.ctypes,'windll',self.windll), patch.object(probe.subprocess,'Popen',return_value=self.process), patch.object(probe,'_windows',return_value=windows):
            r=probe.probe_game(self.exe,'native')
        self.assertEqual(r['outcome'],'initialization_failed'); self.assertEqual(r['error']['code'],'encoding_error')
    def test_window_only_is_unknown_on_exit(self):
        self.process.poll.return_value=0
        with patch.object(probe.ctypes,'windll',self.windll), patch.object(probe.subprocess,'Popen',return_value=self.process), patch.object(probe,'_windows',return_value=[{'handle':1,'class':'Main','title':'window','dialog_text':[]}]):
            r=probe.probe_game(self.exe,'native')
        self.assertEqual(r['outcome'],'unknown')
    def test_japanese_pid_comes_from_probe_helper(self):
        rt=self.root/'runtime'; rt.mkdir()
        for name in ('Play-Korean.exe','LoaderDll.dll','LocaleEmulator.dll'): (rt/name).write_bytes(b'x')
        def start(command, **kwargs):
            Path(command[-1]).write_text('654',encoding='ascii'); return self.process
        with patch.object(probe.ctypes,'windll',self.windll), patch.object(probe.subprocess,'Popen',side_effect=start), patch.object(probe,'_open_process',return_value=9), patch.object(probe,'_windows',return_value=[]):
            r=probe.probe_game(self.exe,'japanese',runtime=rt,readiness=lambda s: {'ready':True,'evidence':'fixture ready'})
        self.assertEqual(r['pid'],654); self.assertEqual(r['launcher_pid'],321); self.assertEqual(r['configured_code_page'],932); self.assertIsNone(r['code_page'])
        self.assertEqual(self.process._rpt_native_launch['native_library_directory'],r['log_directory'])

class WorkflowTests(unittest.TestCase):
    def native(self, outcome='initialization_failed', active=False, acp=1252):
        return {'mode':'native','pid':123,'outcome':outcome,'system_acp':acp,'code_page':None,
                'target_active':active,'readiness_evidence':'title verified' if outcome=='initialization_ready' else None,
                'error':{'code':'invalid_multibyte','source':'owned_dialog','source_pid':123,'proof':'Invalid multibyte sequence'}}
    def japanese(self):
        return {'mode':'japanese','pid':456,'outcome':'initialization_ready','configured_code_page':932,
                'code_page':None,'readiness_evidence':'title verified'}
    def test_native_ready_never_retries_even_with_non_korean_system(self):
        native=self.native('initialization_ready',acp=1252)
        with patch.object(probe,'probe_game',return_value=native) as run:
            result=probe.probe_locale_requirement('game.exe')
        self.assertEqual(result['status'],'not_required'); self.assertEqual(run.call_count,1)
        self.assertIsNone(result['native']['code_page'])
    def test_candidate_retries_with_no_fabricated_acp(self):
        with patch.object(probe,'probe_game',side_effect=[self.native(),self.japanese()]) as run:
            result=probe.probe_locale_requirement('game.exe',terminate_owned=True)
        self.assertEqual(result['status'],'required'); self.assertEqual(run.call_count,2)
        self.assertIsNone(result['japanese']['code_page']); self.assertEqual(result['japanese']['configured_code_page'],932)
    def test_no_retry_for_general_error_or_live_native(self):
        native=self.native(); native['error']={'code':'cuda_error'}
        with patch.object(probe,'probe_game',return_value=native) as run:
            result=probe.probe_locale_requirement('game.exe')
        self.assertEqual(result['status'],'unknown'); self.assertEqual(run.call_count,1)
        with patch.object(probe,'probe_game',return_value=self.native(active=True)) as run:
            result=probe.probe_locale_requirement('game.exe')
        self.assertEqual(result['retry_skipped_reason'],'native_target_not_confirmed_stopped'); self.assertEqual(run.call_count,1)
    def test_window_only_or_unattributed_error_is_unknown(self):
        native=self.native('window_visible')
        with patch.object(probe,'probe_game',return_value=native) as run:
            result=probe.probe_locale_requirement('game.exe')
        self.assertEqual(result['status'],'unknown'); self.assertEqual(run.call_count,1)
        native=self.native(); native['error']['source_pid']=999
        with patch.object(probe,'probe_game',return_value=native) as run:
            result=probe.probe_locale_requirement('game.exe')
        self.assertEqual(result['status'],'unknown'); self.assertEqual(run.call_count,1)
    def test_unsupported_japanese_is_unknown(self):
        with patch.object(probe,'probe_game',side_effect=[self.native(),ValueError('Japanese locale requires x86')]):
            result=probe.probe_locale_requirement('game.exe')
        self.assertEqual(result['status'],'unknown'); self.assertEqual(result['retry_skipped_reason'],'japanese_probe_unavailable')

if __name__ == '__main__': unittest.main()
