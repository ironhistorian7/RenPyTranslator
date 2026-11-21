"""Synthetic XP3 export checks; no game, model, or external assets."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.test_kirikiri_project import psb
from kirikiri_containers import DirectorySource, XP3Archive, write_xp3
from kirikiri_project import prepare, render, static_check


class PatchExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.game = self.root / 'game'
        self.game.mkdir()
        (self.game / 'game.exe').touch()
        self.project = self.root / 'project'
        self.cfg = {'source': str(self.game), 'source_language': 'english',
                    'language': 'korean', 'show_reference': False}

    def prepare_scenes(self, paths):
        data = psb({'scenes': [{'texts': [['id', None, 'Hello', None, 0, {}]]}]},
                   ['scenes', 'texts'])
        write_xp3(self.game / 'data.xp3', {name: data for name in paths})
        prepare(self.project, self.cfg)
        return json.loads((self.project / 'data/catalog.json').read_text(encoding='utf-8-sig'))

    @staticmethod
    def known(rows):
        return {row['id']: {'text': '안녕'} for row in rows}

    @staticmethod
    def members(path):
        with DirectorySource(path.parent) as source:
            archive = XP3Archive(source, path.name)
            return {name: archive.read(name) for name in archive.entries}

    def report(self):
        return json.loads((self.project / 'data/kirikiri-output.json').read_text(encoding='utf-8-sig'))

    def test_render_flat_members_and_static_check(self):
        rows = self.prepare_scenes(['scenario/a.scn', 'other/b.scn'])
        path = render(self.project, self.cfg, rows=rows, known=self.known(rows))
        self.assertEqual(set(self.members(path)), {'a.scn', 'b.scn'})
        report = self.report()
        self.assertEqual(report['member_map'], {'a.scn': 'scenario/a.scn', 'b.scn': 'other/b.scn'})
        self.assertEqual(report['layout'], 'flat')
        self.assertEqual(static_check(self.project)['status'], 'passed')

    def test_collision_preserves_existing_patch_and_ledger(self):
        rows = self.prepare_scenes(['first/a.scn', 'second/A.scn'])
        first = [row for row in rows if row['file'] == 'first/a.scn']
        path = render(self.project, self.cfg, rows=first, known=self.known(first))
        before = path.read_bytes()
        ledger = (self.project / 'data/kirikiri-output.json').read_bytes()
        with self.assertRaises(ValueError) as error:
            render(self.project, self.cfg, rows=rows, known=self.known(rows))
        self.assertEqual(error.exception.error_code, 'kirikiri_patch_conflict')
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual((self.project / 'data/kirikiri-output.json').read_bytes(), ledger)

    def test_repack_legacy_patch_preserves_each_payload_without_cache(self):
        from kirikiri_project import repack_output
        rows = self.prepare_scenes(['scenario/a.scn', 'other/b.scn'])
        path = render(self.project, self.cfg, rows=rows, known=self.known(rows))
        flat = self.members(path)
        report = self.report()
        legacy = {original: flat[name] for name, original in report['member_map'].items()}
        staged = path.with_name('legacy.tmp')
        write_xp3(staged, legacy)
        staged.replace(path)
        report['files'] = sorted(legacy)
        report['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        report.pop('member_map')
        report.pop('layout')
        record = self.project / 'data/kirikiri-output.json'
        record.write_text(json.dumps(report), encoding='utf-8')
        self.assertEqual(static_check(self.project)['status'], 'passed')
        with patch('translation.cache', side_effect=AssertionError('cache must not be read')):
            result = repack_output(self.project, self.cfg)
        self.assertEqual(result['status'], 'repacked')
        actual, report = self.members(path), self.report()
        self.assertEqual(report['layout'], 'flat')
        self.assertEqual(set(actual), {'a.scn', 'b.scn'})
        self.assertEqual({original: actual[name] for name, original in report['member_map'].items()}, legacy)
        self.assertEqual(static_check(self.project)['status'], 'passed')
        self.assertTrue(Path(result['backup']).exists())

    def test_repack_rejects_stale_patch_without_overwriting_it(self):
        from kirikiri_project import repack_output
        rows = self.prepare_scenes(['scenario/a.scn'])
        path = render(self.project, self.cfg, rows=rows, known=self.known(rows))
        path.write_bytes(b'changed externally')
        record = self.project / 'data/kirikiri-output.json'
        ledger = record.read_bytes()
        with self.assertRaises(ValueError):
            repack_output(self.project, self.cfg)
        self.assertEqual(path.read_bytes(), b'changed externally')
        self.assertEqual(record.read_bytes(), ledger)

    def test_confirmed_root_loader_rejects_legacy_nested_patch(self):
        rows = self.prepare_scenes(['scenario/a.scn'])
        path = render(self.project,self.cfg,rows=rows,known=self.known(rows))
        report = self.report()
        contents = self.members(path)
        legacy = {original:contents[name] for name,original in report['member_map'].items()}
        temporary = path.with_name('legacy.tmp')
        write_xp3(temporary,legacy); temporary.replace(path)
        report.update(files=sorted(legacy),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        report.pop('member_map'); report.pop('layout')
        (self.project/'data/kirikiri-output.json').write_text(json.dumps(report),encoding='utf-8')
        (self.project/'data/kirikiri-patch-layout.json').write_text(json.dumps({
            'version':1,'layout':'flat','verified':True,'basis':'patch_auto_path'}),encoding='utf-8')
        self.assertEqual(static_check(self.project)['status'],'failed')


if __name__ == '__main__':
    unittest.main()
