"""ZIP metadata/manifest fixtures only; no actual distribution or model reads."""
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile


SPEC = importlib.util.spec_from_file_location('release_verifier', Path(__file__).resolve().parents[1]/'build/verify_release_archives.py')
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


def manifest(rows=None):
    rows = rows or []
    return dict(schema=1, edition='light', privacy_audit='passed', files=rows,
                totals={'files':len(rows),'bytes':sum(row['bytes'] for row in rows)})


class ReleaseVerifierTests(unittest.TestCase):
    def test_manifest_rejects_duplicate_case_and_escaping_names(self):
        row = dict(path='Program.exe', bytes=1, sha256='a'*64)
        with self.assertRaises(AssertionError):
            VERIFIER.manifest_files(manifest([row, dict(row,path='program.exe')]), 'light')
        with self.assertRaises(ValueError):
            VERIFIER.manifest_files(manifest([dict(row,path='../private.json')]), 'light')

    def test_light_crc_and_manifest_names_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'light.zip'
            with zipfile.ZipFile(target,'w') as bundle:
                bundle.writestr('RenPyTranslator/bundle-manifest.json',json.dumps(manifest()))
                bundle.writestr('RenPyTranslator/data/',b'')
            self.assertEqual(VERIFIER.verify_light_metadata(target), {})

    def test_unmanifested_private_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'light.zip'
            with zipfile.ZipFile(target,'w') as bundle:
                bundle.writestr('RenPyTranslator/bundle-manifest.json',json.dumps(manifest()))
                bundle.writestr('RenPyTranslator/data/gui-settings.json','{}')
            with self.assertRaises(AssertionError):
                VERIFIER.verify_light_metadata(target)

    def test_real_split_disk_metadata_is_required(self):
        with tempfile.TemporaryDirectory() as directory:
            release = Path(directory)
            (release/'RenPyTranslator-full.z01').write_bytes(b'first disk')
            target = release/'RenPyTranslator-full.zip'
            target.write_bytes(struct.pack('<4s4H2LH', b'PK\x05\x06', 1, 1, 0, 0, 0, 0, 0))
            result = VERIFIER.verify_split_metadata(release)
            self.assertEqual(result['volumes'], 2)
            self.assertTrue(result['real_multivolume_zip'])
            target.write_bytes(struct.pack('<4s4H2LH', b'PK\x05\x06', 0, 0, 0, 0, 0, 0, 0))
            with self.assertRaises(AssertionError):
                VERIFIER.verify_split_metadata(release)

    def test_light_split_zip_uses_the_same_real_disk_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            release = Path(directory)
            (release/'RenPyTranslator-light.z01').write_bytes(b'first disk')
            (release/'RenPyTranslator-light.z02').write_bytes(b'second disk')
            target = release/'RenPyTranslator-light.zip'
            target.write_bytes(struct.pack('<4s4H2LH', b'PK\x05\x06', 2, 2, 0, 0, 0, 0, 0))
            result = VERIFIER.verify_split_metadata(release, 'light')
            self.assertEqual(result['volumes'], 3)
            self.assertTrue(result['real_multivolume_zip'])
            (release/'RenPyTranslator-light.z01').unlink()
            with self.assertRaises(AssertionError):
                VERIFIER.verify_split_metadata(release, 'light')


if __name__ == '__main__':
    unittest.main()
