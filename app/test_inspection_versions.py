"""Preparation admission checks using empty, invented game folders only."""
import json
from pathlib import Path
import tempfile
import unittest

from automatic import inspection


class InspectionVersionTests(unittest.TestCase):
    def test_supported_versions_are_admitted_and_other_six_versions_rejected(self):
        for version in ((6,99,0),(6,99,14,3),(7,3,5),(7,8,7),(8,0,3),(8,5,3),
                        (6,16,0),(6,98,0),(6,100,0),(5,6,0),(9,0,0)):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temp:
                root=Path(temp); source=root/'invented';(source/'game').mkdir(parents=True)
                (source/'game/script_version.txt').write_text(repr(version),encoding='utf-8')
                project=root/'workspace'
                cfg={'source':str(source),'language':'korean'}
                if version[0] in (7,8) or version[:2]==(6,99):
                    report=inspection(project,cfg)
                    self.assertEqual(report['engine_version'],list(version))
                    self.assertIn('6.99/7/8',report['adapter'])
                    saved=json.loads((project/'data/inspection.json').read_text(encoding='utf-8'))
                    self.assertEqual(saved['engine_version'],list(version))
                else:
                    with self.assertRaisesRegex(ValueError,'supports RenPy 6.99/7/8'):
                        inspection(project,cfg)

    def test_missing_version_file_keeps_existing_admission_behavior(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'invented';(source/'game').mkdir(parents=True)
            report=inspection(root/'workspace',{'source':str(source),'language':'korean'})
            self.assertIsNone(report['engine_version'])


if __name__=='__main__':unittest.main()
