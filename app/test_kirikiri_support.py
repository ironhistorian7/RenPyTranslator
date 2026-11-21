"""Synthetic tests for optional KiriKiri support; no game or model is used."""
import hashlib
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kirikiri_project
import kirikiri_support
import portable_locale
from kirikiri_containers import write_xp3


def png(w, h, tail):
    return (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR' + struct.pack('>II', w, h)
            + b'\x08\x06\x00\x00\x00\x00\x00\x00\x00' + tail)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def put_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


class KiriSupportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.data = self.project / 'data'
        self.data.mkdir(parents=True)
        self.cache = self.data / 'translation-cache.json'
        self.cache.write_bytes(b'cache must remain unchanged')
        self.game = self.root / 'fixture'
        self.game.mkdir()
        self.bank = self.root / 'bank'
        self.bank.mkdir()
        self.original = png(8, 4, b'original')
        self.replacement = png(8, 4, b'replacement')
        self.button = 'ui/title@%button;normal.png'
        (self.bank / 'replacement.png').write_bytes(self.replacement)
        put_json(self.bank / 'manifest.json', {'version': 1, 'assets': [{
            'source_sha256': sha(self.original), 'width': 8, 'height': 4,
            'file': 'replacement.png', 'source_names': [self.button]}]})
        write_xp3(self.game / 'data.xp3', {self.button: self.original})

    def overrides(self):
        manifest = {'source': {'container_path': str(self.game), 'game_root': ''}}
        with patch.object(kirikiri_support, 'ui_bank', return_value=self.bank), \
             patch.object(kirikiri_project, '_verified', return_value=manifest), \
             patch.object(kirikiri_project, '_archive_order', return_value=(['data.xp3'], [])):
            return kirikiri_support.ui_overrides(self.project)

    def test_snapshot_persists_and_digest_detects_tampering_without_cache(self):
        self.assertEqual(self.overrides(), {self.button: self.replacement})
        record = json.loads((self.data / 'kirikiri-ui-overrides.json').read_text(encoding='utf-8-sig'))
        saved = self.data / 'kirikiri-ui-overrides' / record['files'][self.button]['file']
        self.assertEqual(saved.read_bytes(), self.replacement)
        (self.bank / 'replacement.png').write_bytes(b'bank changed')
        self.assertEqual(self.overrides(), {self.button: self.replacement})
        saved.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.overrides()
        self.assertEqual(self.cache.read_bytes(), b'cache must remain unchanged')


    def test_snapshot_adds_new_bank_assets_without_replacing_old(self):
        second='ui/settings.png'; original2=png(8,4,b'original2'); replacement2=png(8,4,b'replacement2')
        write_xp3(self.game/'replacement.xp3',{self.button:self.original,second:original2})
        (self.game/'replacement.xp3').replace(self.game/'data.xp3')
        self.assertEqual(self.overrides(),{self.button:self.replacement})
        first=json.loads((self.data/'kirikiri-ui-overrides.json').read_text(encoding='utf-8-sig'))['files'][self.button]
        (self.bank/'replacement2.png').write_bytes(replacement2)
        manifest=json.loads((self.bank/'manifest.json').read_text(encoding='utf-8-sig'))
        manifest['assets'].append({'source_sha256':sha(original2),'width':8,'height':4,'file':'replacement2.png','source_names':[second]})
        put_json(self.bank/'manifest.json',manifest)
        self.assertEqual(self.overrides(),{self.button:self.replacement,second:replacement2})
        saved=json.loads((self.data/'kirikiri-ui-overrides.json').read_text(encoding='utf-8-sig'))['files']
        self.assertEqual(saved[self.button],first)
        self.assertIn(second,saved)
        self.assertEqual(self.cache.read_bytes(),b'cache must remain unchanged')

    def test_unknown_locale_writes_nothing(self):
        out = self.root / 'output'
        result = kirikiri_support.locale_output(self.project, out)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['files'], [])
        self.assertFalse(out.exists())
        self.assertFalse((self.data / 'kirikiri-locale-output.json').exists())
        self.assertEqual(self.cache.read_bytes(), b'cache must remain unchanged')

    def test_confirmed_native_success_does_not_start_locale_probe(self):
        profile={'status':'not_required','evidence':'user_observed_native_success'}
        put_json(self.data/'kirikiri-locale-profile.json',profile)
        with patch.object(kirikiri_support,'_game_executable') as find:
            self.assertEqual(kirikiri_support.ensure_locale_profile(self.project,{'source':str(self.game)}),profile)
        find.assert_not_called()

    def test_running_game_is_not_probed_or_closed(self):
        with patch.object(kirikiri_support,'_game_executable',return_value=self.game/'game.exe'), \
             patch.object(kirikiri_support,'_game_running',return_value=True), \
             patch('kirikiri_boot_probe.probe_locale_requirement') as probe:
            self.assertEqual(kirikiri_support.ensure_locale_profile(self.project,{'source':str(self.game)}),{})
        probe.assert_not_called()

    def test_check_wires_comparison_and_retains_unmeasured_target_acp(self):
        exe=self.game/'game.exe';exe.write_bytes(b'fixture')
        comparison={'status':'required','locale':'ja-JP',
                    'native':{'mode':'native','pid':10,'outcome':'initialization_failed','code_page':None,'system_acp':949,
                              'error':{'code':'encoding_error','source_pid':10,'source':'owned_dialog','proof':'fixture encoding failure'}},
                    'japanese':{'mode':'japanese','pid':20,'outcome':'initialization_ready','code_page':None,
                                'configured_code_page':932,'readiness_evidence':{'kind':'title_buttons','matched_buttons':2}}}
        with patch.object(kirikiri_support,'_game_executable',return_value=exe), \
             patch.object(kirikiri_support,'_game_running',return_value=False), \
             patch('kirikiri_boot_assets.prepare_menu_patterns',return_value={'original':{'fixture':True}}), \
             patch('kirikiri_menu_probe.make_menu_readiness',return_value=lambda r:{'ready':True,'evidence':'fixture'}), \
             patch('kirikiri_boot_probe.probe_locale_requirement',return_value=comparison) as probe:
            result=kirikiri_support.ensure_locale_profile(self.project,{'source':str(self.game)})
        self.assertEqual(portable_locale.detect_locale_need(result)['status'],'required')
        self.assertIsNone(result['launch_comparison']['japanese']['code_page'])
        self.assertTrue(probe.call_args.kwargs['terminate_owned'])
        self.assertEqual(self.cache.read_bytes(),b'cache must remain unchanged')

    def locale_fixture(self):
        exe = self.root / 'fixture.exe'
        buf = bytearray(128); buf[:2] = b'MZ'; struct.pack_into('<I', buf, 0x3c, 64)
        buf[64:68] = b'PE\0\0'; struct.pack_into('<H', buf, 68, 0x14c); exe.write_bytes(buf)
        runtime = self.root / 'runtime'; runtime.mkdir()
        for name in ('Play-Korean.exe', *portable_locale._RUNTIME_FILES):
            (runtime / name).write_bytes(('fixture:' + name).encode())
        (runtime / 'unlisted.txt').write_bytes(b'not permitted')
        put_json(self.data / 'kirikiri-locale-profile.json', {
            'status': 'required', 'locale': 'ja-JP', 'evidence': 'user_confirmed_required',
            'source_exe': str(exe), 'target_relative': 'fixture.exe'})
        return runtime

    def test_required_locale_copies_only_allowlisted_files(self):
        runtime = self.locale_fixture()
        out = self.root / 'output'
        with patch.object(portable_locale, 'runtime_dir', return_value=runtime):
            result = kirikiri_support.locale_output(self.project, out)
        expected = {'Play-Korean.exe', 'locale-target.txt', 'locale-mode.txt'} | {
            n if n.endswith('.dll') or n=='MenuBridge.exe' else 'locale-runtime/' + n for n in portable_locale._RUNTIME_FILES}
        actual = {p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file()}
        self.assertEqual(set(result['files']), expected)
        self.assertEqual(actual, expected)
        self.assertEqual(self.cache.read_bytes(), b'cache must remain unchanged')

    def test_ui_menu_without_locale_evidence_uses_native_launcher_without_dlls(self):
        self.locale_fixture()
        put_json(self.data/'kirikiri-locale-profile.json',{'source_exe':str(self.game/'game.exe'),
                 'target_relative':'game.exe'})
        out=self.root/'output';out.mkdir()
        put_json(out/'ui-native-menus.json',{'version':1,'captions':{'Settings':'설정'}})
        def install(staged,target,source,profile):
            self.assertEqual(target,'game.exe')
            (staged/'MenuBridge.exe').write_bytes(b'bridge')
            (staged/'locale-mode.txt').write_text('native')
            return {'status':'unknown','locale':None,'files':['MenuBridge.exe','locale-mode.txt']}
        with patch.object(portable_locale,'install_portable_ui_launcher',side_effect=install):
            result=kirikiri_support.locale_output(self.project,out)
        self.assertEqual(result['status'],'unknown')
        self.assertEqual((out/'locale-mode.txt').read_text(),'native')
        self.assertFalse((out/'LocaleEmulator.dll').exists())
        self.assertEqual(json.loads((out/'ui-native-menus.json').read_text(encoding='utf-8'))['version'],1)

    def test_conflicting_locale_destination_is_not_overwritten(self):
        runtime = self.locale_fixture()
        out = self.root / 'output'; out.mkdir()
        existing = out / 'LoaderDll.dll'; existing.write_bytes(b'user file')
        with patch.object(portable_locale, 'runtime_dir', return_value=runtime):
            with self.assertRaises(ValueError):
                kirikiri_support.locale_output(self.project, out)
        self.assertEqual(existing.read_bytes(), b'user file')
        self.assertEqual({p.name for p in out.iterdir()}, {'LoaderDll.dll'})
        self.assertFalse((self.data / 'kirikiri-locale-output.json').exists())
        self.assertEqual(self.cache.read_bytes(), b'cache must remain unchanged')

    def static_check_fixture(self, declared, actual):
        patch_path = self.root / 'patch.xp3'
        write_xp3(patch_path, actual)
        mapping = {name: 'ui/' + name for name in actual}
        put_json(self.data / 'catalog.json', [])
        put_json(self.data / 'kirikiri-output.json', {'status': 'written', 'patch': str(patch_path),
            'sha256': sha(patch_path.read_bytes()), 'files': sorted(actual), 'layout': 'flat',
            'member_map': mapping, 'ui_assets': declared})
        with patch.object(kirikiri_project, '_verified', return_value={}), \
             patch.object(kirikiri_project, 'ensure_patch_layout', return_value={'layout': 'flat', 'verified': True}):
            return kirikiri_project.static_check(self.project)

    def test_static_check_uses_canonical_paths_for_flattened_members(self):
        self.assertEqual(self.static_check_fixture({'ui/a.png': sha(self.replacement)},
                                                   {'a.png': self.replacement})['status'], 'passed')

    def test_static_check_rejects_changed_ui_bytes(self):
        self.assertEqual(self.static_check_fixture({'ui/a.png': sha(self.replacement)},
                                                   {'a.png': b'wrong bytes'})['status'], 'failed')

    def test_static_check_rejects_declared_but_missing_ui_asset(self):
        result = self.static_check_fixture({'ui/missing.png': sha(self.replacement)}, {'other.bin': b'x'})
        self.assertEqual(result['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
