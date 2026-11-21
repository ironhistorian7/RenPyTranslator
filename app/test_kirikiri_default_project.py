import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.test_kirikiri_project import psb
from kirikiri_containers import write_xp3, DirectorySource, XP3Archive
from kirikiri_project import prepare, render, static_check, ensure_name_catalog
from kirikiri_psb import loads, dumps
from name_review import build_review, apply_review, overlay


class DefaultProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.game = self.base / 'game'
        self.game.mkdir()
        (self.game / 'game.exe').touch()
        self.project = self.base / 'project'
        self.cfg = {'source': str(self.game), 'source_language': 'english',
                    'language': 'korean', 'show_reference': False}

    def setup_scn(self):
        self.original = {'scenes': [{'texts': [
            ['alpha_internal', None, 'Hello', None, 0, {}],
            ['beta_internal', None, 'World', None, 0, {}, None, 'speech', 'search'],
            ['third_internal', 'Alice', 'Explicit', None, 0, {}],
            ['blank_internal', '', 'Blank', None, 0, {}],
            [None, None, 'Narration', None, 0, {}],
        ]}]}
        write_xp3(self.game / 'data.xp3', {
            'main.scn': psb(self.original, ['scenes', 'texts']),
            'charvoice.csv': b'name,a,b,c,namealias\nalpha_internal,,,,Alice\nbeta_internal,,,,Bob\n',
        })
        prepare(self.project, self.cfg)

    def rows(self):
        return json.loads((self.project / 'data/catalog.json').read_text(encoding='utf-8-sig'))

    def make_old(self):
        old = [row for row in self.rows() if not row.get('derived_speaker')]
        (self.project / 'data/catalog.json').write_text(json.dumps(old, ensure_ascii=False), encoding='utf-8')
        (self.project / 'data/catalog-format.json').unlink()
        (self.project / 'data/kirikiri-name-aliases.json').unlink()
        return old

    def test_prepare_render_check_and_tampered_internal_id(self):
        self.setup_scn()
        rows = self.rows()
        defaults = [r for r in rows if r.get('derived_speaker')]
        self.assertEqual([(r['source'], r['speaker_internal']) for r in defaults],
                         [('Alice', 'alpha_internal'), ('Bob', 'beta_internal')])
        known = {r['id']: {'text': {'Alice': '앨리스', 'Bob': '밥'}.get(r['source'], r['source'])}
                 for r in rows}
        patch_path = render(self.project, self.cfg, rows=rows, known=known)
        self.assertEqual(static_check(self.project)['status'], 'passed')
        with DirectorySource(patch_path.parent) as source:
            doc = loads(XP3Archive(source, patch_path.name).read('main.scn'))
        expected = copy.deepcopy(self.original)
        expected['scenes'][0]['texts'][0][1] = '앨리스'
        expected['scenes'][0]['texts'][1][1] = '밥'
        expected['scenes'][0]['texts'][2][1] = '앨리스'
        self.assertEqual(doc.root, expected)
        doc.root['scenes'][0]['texts'][0][0] = 'other_internal'
        replacement = patch_path.parent / 'replacement.tmp'
        write_xp3(replacement, {'main.scn': dumps(doc)})
        replacement.replace(patch_path)
        report_path = self.project / 'data/kirikiri-output.json'
        report = json.loads(report_path.read_text(encoding='utf-8-sig'))
        report['sha256'] = hashlib.sha256(patch_path.read_bytes()).hexdigest()
        report_path.write_text(json.dumps(report), encoding='utf-8')
        self.assertEqual(static_check(self.project)['status'], 'failed')

    def test_migration_preserves_cache_and_review_and_is_idempotent(self):
        self.setup_scn()
        old = self.make_old()
        cache_path = self.project / 'data/translations.jsonl'
        cache_bytes = (json.dumps({'id': old[0]['id'], 'source': old[0]['source'], 'text': 'manual', 'status': 'manual_translation', 'fingerprint': 'old', 'model': 'manual'}) + '\n').encode()
        cache_path.write_bytes(cache_bytes)
        review = build_review(self.project, self.cfg)
        for entry in review['entries']:
            if entry['source'] == 'Alice': entry['value'] = '수동 이름'
        apply_review(self.project, self.cfg, review)
        self.assertEqual(ensure_name_catalog(self.project, self.cfg, dry_run=True)['added'], 2)
        self.assertEqual(self.rows(), old)
        result = ensure_name_catalog(self.project, self.cfg)
        self.assertEqual((result['added'], result['groups']), (2, 2))
        self.assertEqual(self.rows()[:len(old)], old)
        self.assertEqual(cache_path.read_bytes(), cache_bytes)
        self.assertEqual(json.loads((self.project / 'data/catalog.before-default-names.json').read_text(encoding='utf-8')), old)
        after_review = build_review(self.project, self.cfg)
        alice = next(e for e in after_review['entries'] if e['source'] == 'Alice')
        self.assertEqual(alice['value'], '수동 이름')
        self.assertEqual(len(alice['members']), 2)
        # Existing approval must apply immediately to the newly derived slot,
        # even before the expanded review snapshot is reconfirmed.
        rows = self.rows()
        known = {r['id']: {'text': r['source']} for r in rows}
        effective, _ = overlay(self.project, rows, known)
        new_alice = next(r for r in rows if r.get('derived_speaker') and r['source'] == 'Alice')
        self.assertEqual(effective[new_alice['id']]['text'], '수동 이름')
        patch_path = render(self.project, self.cfg, rows=rows, known=effective)
        with DirectorySource(patch_path.parent) as source:
            document = loads(XP3Archive(source, patch_path.name).read('main.scn'))
        self.assertEqual(document.root['scenes'][0]['texts'][0][1], '수동 이름')
        self.assertEqual(document.root['scenes'][0]['texts'][0][0], 'alpha_internal')
        self.assertTrue(ensure_name_catalog(self.project, self.cfg)['already_current'])
        self.assertEqual(cache_path.read_bytes(), cache_bytes)

    def test_missing_source_fallback_and_no_name_kag(self):
        self.setup_scn()
        old = self.make_old()
        shutil.rmtree(self.game)
        with patch('job_events.warning') as warning:
            result = ensure_name_catalog(self.project, self.cfg)
        self.assertEqual(result['added'], 2)
        self.assertEqual(self.rows()[:len(old)], old)
        names = [r['source'] for r in self.rows() if r.get('derived_speaker')]
        self.assertEqual(names, ['alpha_internal', 'beta_internal'])
        warning.assert_called_once()
        self.assertEqual(warning.call_args.args[0], 'kirikiri_name_aliases')

        game2 = self.base / 'game2'
        game2.mkdir(); (game2 / 'game.exe').touch()
        write_xp3(game2 / 'data.xp3', {'main.ks': b'plain text\n'})
        project2 = self.base / 'project2'
        cfg2 = dict(self.cfg, source=str(game2))
        prepare(project2, cfg2)
        catalog = project2 / 'data/catalog.json'
        before = catalog.read_bytes()
        (project2 / 'data/catalog-format.json').unlink()
        result = ensure_name_catalog(project2, cfg2)
        self.assertEqual(result['added'], 0)
        self.assertEqual(catalog.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
