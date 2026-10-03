"""Packaging fixtures only; no game, project cache or model execution."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from release_archives import publish_archives, sha256, write_full, write_light


class ReleaseArchiveTests(unittest.TestCase):
    def test_large_dll_is_deflated_and_unicode_roundtrips(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle = root / 'RenPyTranslator'
            bundle.mkdir()
            dll = bundle / 'large.dll'
            with dll.open('wb') as stream:
                stream.truncate(100_000_001)
            (bundle / '한글 안내.txt').write_text('압축 해제', encoding='utf-8')
            archive = root / 'light.zip'
            write_light(bundle, archive)
            with zipfile.ZipFile(archive) as z:
                info = z.getinfo('RenPyTranslator/large.dll')
                self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED)
                self.assertLess(info.compress_size, 200_000)
                self.assertEqual(z.read('RenPyTranslator/한글 안내.txt').decode('utf-8'), '압축 해제')

    def test_light_refuses_model_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            models = root / 'RenPyTranslator/models/ollama/blobs'
            models.mkdir(parents=True)
            (models / 'sha256-example').write_bytes(b'model')
            with self.assertRaisesRegex(ValueError, 'unexpectedly contains'):
                write_light(root / 'RenPyTranslator', root / 'light.zip')

    def test_same_version_backups_keep_other_edition(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            release = root / 'portable/V0.1.0'
            release.mkdir(parents=True)
            (release / 'RenPyTranslator-light.zip').write_bytes(b'old')
            (release / 'RenPyTranslator-full.zip').write_bytes(b'keep')
            (root / 'dist').mkdir()
            (root / 'dist/RenPyTranslator-light.zip').write_bytes(b'legacy')
            stage = root / 'stage'
            stage.mkdir()
            (stage / 'RenPyTranslator-light.zip').write_bytes(b'new')
            result = publish_archives(root, stage, '0.1.0', ['light'])
            self.assertEqual(result, release)
            self.assertEqual((release / 'RenPyTranslator-light.zip').read_bytes(), b'new')
            self.assertEqual((release / 'RenPyTranslator-full.zip').read_bytes(), b'keep')
            backup = next((root / 'portable/backup').iterdir())
            self.assertEqual((backup / 'release/RenPyTranslator-light.zip').read_bytes(), b'old')
            self.assertEqual((backup / 'dist/RenPyTranslator-light.zip').read_bytes(), b'legacy')
            self.assertFalse((root / 'portable/V0.1.1').exists())

    def test_failed_publish_restores_previous_release(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            release = root / 'portable/V0.1.0'
            release.mkdir(parents=True)
            old = release / 'RenPyTranslator-light.zip'
            old.write_bytes(b'old')
            stage = root / 'stage'
            stage.mkdir()
            new = stage / old.name
            new.write_bytes(b'new')
            original = Path.rename

            def rename(path, target):
                if path == new:
                    raise OSError('simulated publication failure')
                return original(path, target)

            with patch.object(Path, 'rename', rename), self.assertRaises(OSError):
                publish_archives(root, stage, '0.1.0', ['light'])
            self.assertEqual(old.read_bytes(), b'old')
            self.assertEqual(new.read_bytes(), b'new')

    def test_bandizip_split_boundary_and_korean_filename(self):
        root = Path(__file__).resolve().parents[1]
        cli = root / 'build/tools/bandizip-7.46/bz.x64.exe'
        if not cli.exists():
            self.skipTest('Run a FULL build to prepare the pinned Bandizip tool')
        from release_archives import run_archiver
        with tempfile.TemporaryDirectory(dir=root / 'build') as temp:
            fixture = Path(temp)
            source = fixture / 'RenPyTranslator'
            source.mkdir()
            (source / 'boundary.bin').write_bytes(os.urandom(200_000))
            (source / '한글 파일.txt').write_text('테스트', encoding='utf-8')
            parts = write_full(source, fixture / 'full.zip', cli, fixture / 'log.txt', 65_536)
            self.assertEqual([p.suffix for p in parts], ['.z01', '.z02', '.z03', '.zip'])
            output = fixture / 'out'
            run_archiver([cli, 'x', '-y', '-o:' + str(output), fixture / 'full.zip'], fixture / 'log.txt')
            for path in source.iterdir():
                self.assertEqual(sha256(path), sha256(output / source.name / path.name))


if __name__ == '__main__':
    unittest.main()
