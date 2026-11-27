"""Verify the completed patch-check path with invented text and a synthetic renderer."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from engine import save_json
from patch_validation import run
from test_validation_probe_runtime import probe_functions
from translation import quote


class OfflineInterpolationTests(unittest.TestCase):
    def test_original_and_patch_use_same_safe_values_and_classify_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / 'prepared'
            data = project / 'data'
            data.mkdir(parents=True)
            stage = project / 'staging'
            target = stage / 'game/tl/korean/example.rpy'
            target.parent.mkdir(parents=True)
            (stage / 'renpy').mkdir()
            (stage / 'renpy/engine.py').write_text('# synthetic engine', encoding='utf-8')
            (stage / 'game/settings.rpy').write_text(
                'define effect = (\n    "{shimmer}fixture{/shimmer}"\n)\n'
                'init python:\n    unknown_effect = "{missing}"\n    value = lookup()\n', encoding='utf-8')
            originals = ['A [effect].', 'B [unknown_effect].', 'C [value] [editor.name].', 'D.']
            translations = ['가.\n{rpt_ref=0.55}A [effect].{/rpt_ref}', '나 [unknown_effect].',
                            '다 [value] [editor.name].', '라 [4>']
            rows = [dict(id=str(i), file='example.rpy', line=i, kind='dialogue', source=text)
                    for i, text in enumerate(originals)]
            save_json(data / 'catalog.json', rows)
            save_json(data / 'source-manifest.json', {'game/settings.rpy': {}, 'renpy/engine.py': {}})
            unavailable = root / 'original-folder'
            save_json(data / 'workspace-files.json', dict(status='ready', archives=[], loose_assets=[],
                      source_root=str(unavailable), source_game=str(unavailable/'game')))
            target.write_text(''.join(quote(text) + '\n' for text in translations), encoding='utf-8')
            consumed = {}

            def execute(workspace, operation, timeout):
                if operation != 'render':
                    events = [dict(check='lint_capture', status='passed')] if operation == 'lint' else []
                    return dict(check=operation, status='passed', events=events)
                document = json.loads((workspace / 'data/probe-input.json').read_text(encoding='utf-8'))
                consumed[workspace.name] = [row['interpolation']['values'] for row in document['rows']]
                probe = probe_functions()
                events = []

                def render(text, *args):
                    if '{missing}' in text:
                        raise ValueError("Unknown text tag 'missing'")
                    if '{shimmer}' in text and '{rpt_ref=0.55}' in text:
                        raise ValueError("Unknown text tag 'shimmer'")

                probe.update(_rpt_probe_event=lambda event: events.append(dict(event)),
                             _rpt_probe_time=SimpleNamespace(time=lambda: 0),
                             _rpt_probe_engine=SimpleNamespace(text=SimpleNamespace(text=SimpleNamespace(Text=lambda text, **kw: text))),
                             renpy=SimpleNamespace(render=render), config=SimpleNamespace(screen_width=1280, screen_height=720))
                for row in document['rows']:
                    probe['_rpt_probe_attempt']('text', lambda: probe['_rpt_probe_text'](row), row)
                return dict(check='render', status='failed', events=events)

            with patch('patch_validation._execute', side_effect=execute):
                report = run(project, dict(source=str(unavailable), language='korean', model='invented'), auto_fix=False)
            self.assertFalse(unavailable.exists())
            self.assertEqual(consumed['baseline'], consumed['patched'])
            self.assertEqual(consumed['patched'][0], {'effect': '{shimmer}fixture{/shimmer}'})
            self.assertEqual(consumed['patched'][2], {})
            self.assertEqual(report['status'], 'failed')
            self.assertEqual([(item['id'], item['attribution'], item['diagnostic']) for item in report['failures']],
                             [('0', 'ours', 'unknown_text_tag'), ('3', 'ours', 'unclosed_interpolation')])
            self.assertEqual([(item['id'], item['attribution']) for item in report['diagnostics']], [('1', 'original')])
            self.assertEqual(report['coverage']['interpolation'], dict(candidate_items=4, attempted_items=4,
                            expanded_items=2, unresolved_items=1, not_run_items=0))
            self.assertNotIn('fixture', json.dumps(report))

    def test_check_only_source_uses_prepared_registry(self):
        from argparse import Namespace
        from automatic import source_key
        from game_engine import dispatch
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root/'unavailable'
            parent = root/'data/projects'
            saved = parent/'registered'/ 'project.json'
            saved.parent.mkdir(parents=True)
            save_json(saved, {'source': str(game), 'engine': 'renpy'})
            save_json(parent/'index.json', {source_key(game): 'registered'})
            args = Namespace(source=game, project=None, command='tasks', tasks=['check'])
            with patch('app_paths.ROOT', root), patch('game_engine.detect', side_effect=AssertionError('original detection')):
                self.assertFalse(dispatch(args, parser=None))
            self.assertEqual(args.project, saved)
            self.assertIsNone(args.source)


if __name__ == '__main__':
    unittest.main()
