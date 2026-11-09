"""CLI/desktop handoff with temporary metadata and mocked work only."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from engine import save_json
from task_plan import arguments, plan
import translate_game as cli


class UpdateRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old = self.root/'Old'; self.new = self.root/'NewRevision'; self.source = self.root/'NewGame'
        for project in (self.old, self.new):
            (project/'data/templates').mkdir(parents=True)
            (project/'staging/game').mkdir(parents=True)
            save_json(project/'data/catalog.json', [])
            save_json(project/'project.json', {'source': str(self.root/'OldGame' if project == self.old else self.source),
                                               'language': 'korean', 'source_language': 'english', 'model': 'synthetic',
                                               'automatic_version': 1})
        (self.source/'game').mkdir(parents=True); (self.source/'renpy').mkdir()
        self.entrypoint = cli.main

    def fake_update(self, old, cfg, source):
        self.assertEqual(old, self.old)
        self.assertEqual(source, self.source)
        saved = dict(cfg, source=str(source))
        save_json(self.new/'project.json', saved)
        return self.new, saved, {'reused': 0, 'remaining': 0, 'ambiguous': 0}

    @contextlib.contextmanager
    def mocks(self, *, failure=None):
        events = []; calls = []
        @contextlib.contextmanager
        def lock(project):
            events.append(('enter', project))
            try: yield
            finally: events.append(('exit', project))
        def inner(argv=None):
            self.assertEqual(argv[0], 'run')
            calls.append(argv)
            if failure: raise failure
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch('diagnostics.stage'))
            stack.enter_context(patch('translate_game.project_lock', side_effect=lock))
            stack.enter_context(patch('translate_game.main', side_effect=inner))
            updater = stack.enter_context(patch('project_update.update_project', side_effect=self.fake_update))
            completed = stack.enter_context(patch('project_update.mark_complete'))
            models = stack.enter_context(patch('model_store.require_selected', return_value='synthetic'))
            stack.enter_context(patch('model_store.selected', return_value='synthetic'))
            guides = stack.enter_context(patch('story_guides.run'))
            checked = stack.enter_context(patch('patch_validation.run', return_value={'status': 'passed'}))
            for name in ('translation.request', 'hy_backend.runtime', 'translate_game.translate',
                         'translate_game.prepare', 'translate_game.rebuild', 'retranslation.comparison'):
                stack.enter_context(patch(name, side_effect=AssertionError('Unexpected work: '+name)))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            yield updater, completed, models, guides, checked, events, calls

    def test_direct_update_and_task_update_route_only_new_revision(self):
        for prefix in (['update'], ['tasks', '--tasks', 'update', 'answers', 'routes', 'check']):
            with self.subTest(prefix=prefix), self.mocks() as state:
                updater, completed, _, guides, checked, events, calls = state
                self.entrypoint([*prefix, '--project', str(self.old), '--source', str(self.source),
                                 '--runtime-id', 'alternate', '--source-language', 'japanese', '--hide-reference'])
                updater.assert_called_once()
                self.assertEqual(updater.call_args.args[1]['runtime_id'], 'alternate')
                self.assertEqual(updater.call_args.args[1]['source_language'], 'japanese')
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0][:3], ['run', '--project', str(self.new/'project.json')])
                self.assertIn('--runtime-id=alternate', calls[0])
                self.assertIn('--source-language=japanese', calls[0])
                self.assertIn('--hide-reference', calls[0])
                self.assertNotIn('--source', calls[0])
                self.assertEqual(events, [('enter', self.old), ('exit', self.old), ('enter', self.new), ('exit', self.new)])
                completed.assert_called_once_with(self.new)
                if 'answers' in prefix:
                    self.assertEqual(guides.call_args.args[0], self.new)
                    self.assertEqual(guides.call_args.args[2], ['answers', 'routes'])
                    self.assertEqual(checked.call_args.args[0], self.new)

    def test_failed_translation_or_patch_check_does_not_complete_update(self):
        with self.mocks(failure=RuntimeError('translation interrupted')) as state:
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                self.entrypoint(['update', '--project', str(self.old), '--source', str(self.source)])
            state[1].assert_not_called()
        with self.mocks() as state:
            state[4].return_value = {'status': 'failed', 'report_path': 'invented-report.json'}
            with self.assertRaisesRegex(RuntimeError, 'Patch check failed'):
                self.entrypoint(['tasks', '--tasks', 'update', 'check', '--project', str(self.old), '--source', str(self.source)])
            state[1].assert_not_called()

    def test_unsupported_new_source_stops_before_model_gate_or_update(self):
        unsupported = self.root/'Unknown'; unsupported.mkdir()
        with self.mocks() as state:
            with self.assertRaisesRegex(ValueError, '지원되는 게임 형식'):
                self.entrypoint(['update', '--project', str(self.old), '--source', str(unsupported)])
            state[0].assert_not_called(); state[2].assert_not_called(); state[1].assert_not_called()

    def test_update_requires_both_paths_before_model_or_work(self):
        for options in (['--project', str(self.old)], ['--source', str(self.source)]):
            with self.subTest(options=options), self.mocks() as state, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit): self.entrypoint(['update', *options])
                state[0].assert_not_called(); state[2].assert_not_called()

    def test_ordinary_source_project_disagreement_still_rejected(self):
        with self.mocks() as state, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.entrypoint(['tasks', '--tasks', 'check', '--project', str(self.old), '--source', str(self.source)])
            state[0].assert_not_called(); state[4].assert_not_called(); state[2].assert_not_called()

    def test_check_only_never_requires_model_or_runs_translation(self):
        with self.mocks() as state:
            self.entrypoint(['check', '--project', str(self.old)])
            state[2].assert_not_called(); state[0].assert_not_called(); state[1].assert_not_called()
            self.assertEqual(state[4].call_args.args[0], self.old)
            self.assertEqual(state[6], [])

    def test_update_task_contract_preserves_optional_hints_and_checks(self):
        self.assertEqual(plan(['update', 'answers', 'routes', 'check']), ['update', 'answers', 'routes', 'check'])
        for other in ('run', 'retranslate'):
            with self.assertRaises(ValueError): plan(['update', other])
        with self.assertRaises(ValueError): arguments(['update'], str(self.old), source=True, update_source=str(self.source))
        with self.assertRaises(ValueError): arguments(['update'], str(self.old))
        args = arguments(['update', 'check'], str(self.old), update_source=str(self.source), source_language='ja')
        self.assertIn('--project', args); self.assertIn('--source', args)
        self.assertEqual(args[args.index('--source-language')+1], 'japanese')

    def test_rpg_update_resumes_through_rpg_dispatch(self):
        from test_rpgmaker import fixture
        source = self.root/'NewRPG'; fixture(source, 'MZ')
        cfg = {'source': str(source), 'language': 'korean', 'source_language': 'english',
               'engine': 'rpgmaker', 'engine_label': 'RPG Maker MZ', 'model': 'synthetic', 'automatic_version': 1}
        save_json(self.old/'project.json', cfg); save_json(self.new/'project.json', cfg)
        with patch('project_update.update_project', return_value=(self.new, cfg, {})) as updated, \
             patch('project_update.mark_complete') as completed, \
             patch('model_store.require_selected', return_value='synthetic'), \
             patch('translate_game.project_lock', side_effect=lambda p: contextlib.nullcontext()), \
             patch('rpgmaker_project.main') as rpg, patch('diagnostics.stage'), \
             contextlib.redirect_stdout(io.StringIO()):
            self.entrypoint(['update', '--project', str(self.old), '--source', str(source), '--runtime-id', 'alternate'])
        updated.assert_called_once(); rpg.assert_called_once(); completed.assert_called_once_with(self.new)
        args = rpg.call_args.args[0]
        self.assertEqual(args.command, 'run'); self.assertEqual(args.project, self.new/'project.json')
        self.assertIsNone(args.source); self.assertEqual(args.runtime_id, 'alternate')


class DesktopUpdateTests(unittest.TestCase):
    def test_desktop_validates_both_folders_before_model_and_preserves_options(self):
        import desktop_bridge as bridge
        events = []
        settings = dict(bridge.DEFAULTS, source_language='japanese', runtime_id='alternate', show_reference=False)
        with patch('desktop_bridge.validate_settings', return_value=settings), \
             patch('desktop_bridge.save_settings'), patch('game_engine.require', side_effect=lambda *a: events.append(('require', a[0], a[1]))), \
             patch('model_store.require_selected', side_effect=lambda: events.append(('model',)) or 'synthetic'), \
             patch('translate_game.main') as main, patch.object(sys, 'argv', ['synthetic']), \
             contextlib.redirect_stdout(io.StringIO()):
            bridge.run_request({'tasks': ['update', 'check'], 'path': 'invented-old', 'source': False,
                                'update_source': 'invented-new', 'settings': settings})
            argv = list(sys.argv)
        self.assertEqual(events, [('require', 'invented-old', False), ('require', 'invented-new', True), ('model',)])
        self.assertIn('--runtime-id', argv); self.assertIn('alternate', argv)
        self.assertIn('--hide-reference', argv); self.assertIn('japanese', argv)
        main.assert_called_once()


if __name__ == '__main__': unittest.main()
