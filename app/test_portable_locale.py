import struct
import tempfile
import unittest
from pathlib import Path
from app.portable_locale import detect_locale_need, install_portable_locale, install_portable_ui_launcher

REQUIRED = {"status": "required", "locale": "ja-JP", "evidence": "user_observed_failure_then_ja_success"}

def pe(path, machine):
    data=bytearray(128); data[:2]=b'MZ'; struct.pack_into('<I',data,0x3c,64); data[64:68]=b'PE\0\0'; struct.pack_into('<H',data,68,machine); path.write_bytes(data)

class PortableLocaleTests(unittest.TestCase):
    def test_comparison_and_unknown(self):
        self.assertEqual(detect_locale_need({"language":"Japanese", "code_page":949})['status'],'unknown')
        native={"code_page":949,"outcome":"initialization_ready"}
        self.assertEqual(detect_locale_need({"launch_comparison":{"native":native}})['status'],'not_required')
        native['outcome']='initialization_failed'; native['error']={'code':'invalid_multibyte'}; ja={"code_page":932,"outcome":"initialization_ready"}
        self.assertEqual(detect_locale_need({"launch_comparison":{"native":native,"japanese":ja}})['status'],'required')
        ja['outcome']='window_shown'
        self.assertEqual(detect_locale_need({"launch_comparison":{"native":native,"japanese":ja}})['status'],'unknown')
    def test_explicit_ui_launcher_without_locale_dlls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'source.exe'; pe(source,0x8664); out=root/'한국어'
            result=install_portable_ui_launcher(out,'source.exe',source,{})
            self.assertEqual(result['status'],'unknown')
            self.assertEqual((out/'locale-mode.txt').read_text(encoding='utf-8'),'native')
            self.assertTrue((out/'MenuBridge.exe').is_file())
            self.assertFalse((out/'LoaderDll.dll').exists()); self.assertFalse((out/'LocaleEmulator.dll').exists())
            self.assertTrue((out/'locale-runtime'/'MenuBridge.cs').is_file())
    def test_confirmed_locale_ui_launcher_includes_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'source.exe'; pe(source,0x14c); out=root/'패치'
            result=install_portable_ui_launcher(out,'source.exe',source,REQUIRED)
            self.assertEqual(result['status'],'required')
            self.assertEqual((out/'locale-mode.txt').read_text(encoding='utf-8'),'ja-JP')
            self.assertTrue((out/'LoaderDll.dll').is_file() and (out/'MenuBridge.exe').is_file())
    def test_packaging_only_when_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'ゲーム.exe'; pe(source,0x14c); out=root/'한글'
            self.assertEqual(install_portable_locale(out,source.name,source,{})['files'],[])
            self.assertFalse(out.exists())
            self.assertEqual(install_portable_locale(out,source.name,source,REQUIRED)['status'],'required')
            self.assertEqual((out/'locale-target.txt').read_text(encoding='utf-8'),source.name)
            self.assertTrue((out/'LoaderDll.dll').is_file() and (out/'LocaleEmulator.dll').is_file())
            self.assertTrue((out/'locale-runtime'/'COPYING.LESSER').is_file())
            self.assertEqual(source.read_bytes()[:2],b'MZ')
            with self.assertRaises(FileExistsError): install_portable_locale(out,source.name,source,REQUIRED)
            other=root/'x64.exe'; pe(other,0x8664)
            with self.assertRaisesRegex(ValueError,'x86'): install_portable_locale(root/'x64out','x64.exe',other,REQUIRED)
            with self.assertRaisesRegex(ValueError,'relative'): install_portable_locale(root/'bad','../outside.exe',source,REQUIRED)

if __name__ == '__main__': unittest.main()
