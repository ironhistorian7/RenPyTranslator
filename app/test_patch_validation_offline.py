import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine import save_json
from patch_validation import _baseline, _snapshot, _source_authenticity
from workspace_files import command


class OfflinePatchCheckTests(unittest.TestCase):
    def test_snapshot_baseline_and_proof_never_read_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / 'project'
            (project / 'data').mkdir(parents=True)
            stage = project / 'staging'
            (stage / 'game').mkdir(parents=True)
            (stage / 'renpy').mkdir()
            (stage / 'renpy/config.py').write_text('# engine', encoding='utf-8')
            (stage / 'game/script.rpy').write_text('label start:\n    return\n', encoding='utf-8')
            (stage / 'game/tl/korean').mkdir(parents=True)
            (stage / 'game/tl/korean/native.rpy').write_text('# translated', encoding='utf-8')
            source = root / 'forbidden-source'
            manifest = {'renpy/config.py': {}, 'game/script.rpy': {}, 'game/tl/korean/native.rpy': {}}
            save_json(project / 'data/source-manifest.json', manifest)
            save_json(project / 'data/workspace-files.json', dict(status='ready', source_root=str(source),
                      source_game=str(source/'game'), archives=[], loose_assets=['missing.png'], original_archive_scripts=[]))
            original_open = Path.open
            original_stat = Path.stat
            def checked_open(path, *args, **kwargs):
                if path == source or source in path.parents:
                    raise AssertionError('source accessed')
                return original_open(path, *args, **kwargs)
            def checked_stat(path, *args, **kwargs):
                if path == source or source in path.parents:
                    raise AssertionError('source accessed')
                return original_stat(path, *args, **kwargs)
            cfg = {'source': str(source), 'language': 'korean'}
            with patch.object(Path, 'open', checked_open), patch.object(Path, 'stat', checked_stat):
                snapshot = _snapshot(project, cfg, root/'snapshot')
                for file in (snapshot/'project.json', snapshot/'data/workspace-files.json'):
                    data = json.loads(file.read_text(encoding='utf-8'))
                    self.assertNotIn('source', data)
                    self.assertNotIn('source_root', data)
                    self.assertNotIn('source_game', data)
                self.assertEqual(command(snapshot, Path('renpy.py'), ['lint']),
                                 ['renpy.py', str(snapshot/'staging'), 'lint'])
                evidence = _baseline(project, snapshot, cfg)
                proof = _source_authenticity(project, cfg)
            self.assertFalse(evidence['confident'])
            self.assertEqual(evidence['issues'][0]['kind'], 'original_input_not_preserved')
            self.assertTrue(proof['verified'])
            self.assertEqual(proof['warnings'][0]['kind'], 'external_assets_not_available_offline')
            self.assertFalse((snapshot/'staging/game/tl/korean/native.rpy').exists())


if __name__ == '__main__':
    unittest.main()
