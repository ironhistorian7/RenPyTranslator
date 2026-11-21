"""Engine routing tests with synthetic paths and mocked KiriKiri inspection."""
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import game_engine


class KiriRoutingTests(unittest.TestCase):
    def test_source_routes_and_preserves_existing_engine_priority(self):
        info = {'engine': 'kirikiri', 'engine_label': 'KiriKiri', 'supported': True, 'game_name': 'Example'}
        called = []
        mock = types.SimpleNamespace(inspect_source=lambda p: called.append(p) or info)
        with tempfile.TemporaryDirectory() as d, patch.dict(sys.modules, {'kirikiri_project': mock}):
            root = Path(d)
            (root / 'data.xp3').touch()
            self.assertEqual(game_engine.detect(root), info)
            iso = root / 'disc.iso'; iso.touch()
            self.assertEqual(game_engine.detect(iso), info)
            (root / 'renpy').mkdir(); (root / 'game').mkdir()
            self.assertEqual(game_engine.detect(root)['engine'], 'renpy')
            self.assertEqual(len(called), 2)

    def test_projects_and_task_filter(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'project.json').write_text(json.dumps({'engine': 'kirikiri', 'engine_label': 'KiriKiri'}), encoding='utf-8')
            result = game_engine.require(root, False, ['run', 'check', 'failed'])
            self.assertEqual(result['engine'], 'kirikiri')
            self.assertNotIn('answers', result['supported_tasks'])
            for task in ('answers', 'routes', 'language'):
                with self.assertRaises(ValueError): game_engine.require(root, False, [task])

    def test_dispatch_only_kirikiri(self):
        calls = []
        fake = types.SimpleNamespace(main=lambda *args: calls.append(args))
        args = types.SimpleNamespace(source='synthetic', project=None, command='tasks', tasks=['run'])
        info = {'engine': 'kirikiri', 'engine_label': 'KiriKiri', 'supported': True, 'stop_message': None}
        with patch.object(game_engine, 'require', return_value=info), patch.dict(sys.modules, {'kirikiri_project': fake}):
            self.assertTrue(game_engine.dispatch(args, 'parser'))
        self.assertEqual(calls, [(args, 'parser', info)])
        with patch.object(game_engine, 'require', return_value={'engine': 'renpy', 'engine_label': 'RenPy'}):
            self.assertFalse(game_engine.dispatch(args, 'parser'))


if __name__ == '__main__': unittest.main()
