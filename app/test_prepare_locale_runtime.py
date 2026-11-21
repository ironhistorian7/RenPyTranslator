import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from app import prepare_locale_runtime as prep

class LocalePreparationTests(unittest.TestCase):
    def test_existing_assets_skip_download_and_compile(self):
        digest=lambda b: hashlib.sha256(b).hexdigest()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); assets=root/'app/locale_assets'; locale=root/'runtimes/locale'; probe=root/'runtimes/probe'
            for folder in (assets,locale,probe): folder.mkdir(parents=True)
            for name in prep._NOTICES: (assets/name).write_bytes(b'notice')
            (locale/'sample.dll').write_bytes(b'dll')
            (locale/'source.zip').write_bytes(b'upstream')
            (assets/'helper.cs').write_bytes(b'source')
            (locale/'helper.exe').write_bytes(b'binary')
            helpers={'locale':('helper.cs','helper.exe','winexe','x86','x.dll',digest(b'source'),digest(b'binary'))}
            sources={'source.zip':('https://example.invalid/source.zip',digest(b'upstream'))}
            with patch.object(prep,'_DLLS',{'sample.dll':digest(b'dll')}), patch.object(prep,'_SOURCES',sources), patch.object(prep,'_HELPERS',helpers), patch.object(prep,'_fetch',side_effect=AssertionError('unexpected download')), patch.object(prep,'native_run',side_effect=AssertionError('unexpected compiler')):
                result=prep.ensure_runtime(root)
            self.assertEqual(result,{'downloaded':[],'compiled':[]})
    def test_extract_only_pinned_root_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp); archive=path/'release.zip'; dest=path/'out'; dest.mkdir()
            data={'LoaderDll.dll':b'loader','LocaleEmulator.dll':b'runtime'}
            hashes={name:hashlib.sha256(value).hexdigest() for name,value in data.items()}
            with zipfile.ZipFile(archive,'w') as z:
                for name,value in data.items(): z.writestr(name,value)
                z.writestr('../unwanted.txt','not copied')
            with patch.object(prep,'_DLLS',hashes): prep._extract_dlls(archive,dest)
            self.assertEqual({p.name for p in dest.iterdir()},set(data))
            self.assertFalse((path/'unwanted.txt').exists())
            with patch.object(prep,'_DLLS',{'LoaderDll.dll':'0'*64}):
                with self.assertRaisesRegex(ValueError,'SHA-256'): prep._extract_dlls(archive,dest)
    def test_helper_rebuild_when_source_or_binary_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp); output=path/'helper.exe'; output.write_bytes(b'known')
            binary=hashlib.sha256(b'known').hexdigest()
            compiled=prep._compile_helper(path,'locale',path/'a.cs',output,'winexe','x86','foo.dll','sourcehash','sourcehash',binary)
            self.assertFalse(compiled)
            with patch.object(prep.os,'name','nonwindows'):
                with self.assertRaisesRegex(OSError,'compiler|compilation'):
                    prep._compile_helper(path,'locale',path/'a.cs',output,'winexe','x86','foo.dll','newhash','sourcehash',binary)

if __name__ == '__main__': unittest.main()
