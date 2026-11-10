"""Only invented temporary log trees; never read or remove application data."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import log_retention


NOW = 2_000_000_000
DAY = 86400


class LogRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def file(self, name, age=8*DAY):
        path = self.root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'invented')
        os.utime(path, (NOW-age, NOW-age))
        return path

    def cleanup(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return log_retention.cleanup_logs(self.root, now=NOW)

    def test_globally_removes_old_logs_from_multiple_projects_and_tool_folders(self):
        names = ['logs/hy-server-1.log', 'logs/errors/20200101-error.json',
                 'data/projects/one/data/errors/runtime-1.json',
                 'data/projects/two/data/translation-requests.jsonl',
                 'data/projects/three/data/compile.log',
                 'build/diagnostic/server.log.1', 'custom/location/session.log',
                 'project/result/log.txt', 'project/result/traceback.txt',
                 'report.evtx', 'Report.wer',
                 'data/projects/two/data/last-invalid-model-response.json',
                 'data/projects/three/data/context-report.json',
                 'data/projects/three/data/validation-runs/old/report.json',
                 'data/projects/three/data/validation-runs/old/patched-data/probe-events.jsonl']
        paths = [self.file(name) for name in names]
        with patch.object(Path, 'read_text', side_effect=AssertionError('Do not read log contents')), \
                patch.object(Path, 'read_bytes', side_effect=AssertionError('Do not read log contents')):
            result = self.cleanup()
        self.assertEqual(result['deleted'], len(paths))
        self.assertFalse(result['errors'])
        self.assertTrue(all(not p.exists() for p in paths))

    def test_exact_seven_day_boundary_and_recent_file_preservation(self):
        boundary = self.file('logs/boundary.log', 7*DAY)
        recent = self.file('logs/recent.log', 7*DAY-1)
        future = self.file('logs/future.log', -DAY)
        self.assertEqual(self.cleanup()['deleted'], 1)
        self.assertFalse(boundary.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(future.exists())

    def test_preserves_caches_models_release_archives_games_and_project_metadata(self):
        names = ['data/projects/a/data/translations.jsonl',
                 'data/projects/a/data/translations.jsonl.tmp',
                 'data/projects/a/data/recovered-translations.json',
                 'data/projects/a/data/reviewed-translations.json',
                 'data/projects/a/data/failed-items.json',
                 'data/projects/a/data/catalog.json', 'data/settings.json',
                 'data/projects/a/project.json', 'data/runtime/123.json',
                 'data/projects/a/output/repair-report.json',
                 'data/projects/a/data/unknown.jsonl',
                 'models/example.gguf', 'models/notes.log',
                 'portable/V0.3.0/keep.zip', 'portable/V0.3.0/keep.log',
                 'dist/RenPyTranslator/logs/keep.log',
                 'project/game/logs/game.json', 'project/game/script.rpy',
                 'project/game/saves/state.log', 'project/saves/state.log',
                 'vendor/example.log', '.venv/example.log',
                 'desktop/node_modules/example.log', '_internal/example.log',
                 'app/tests/errors/fixture.json', 'app/fixtures/example.log',
                 'desktop/tests/logs/fixture.json', 'desktop/fixtures/example.log']
        paths = [self.file(name) for name in names]
        self.assertEqual(self.cleanup()['deleted'], 0)
        self.assertTrue(all(p.exists() for p in paths))

    def test_busy_log_is_reported_and_does_not_stop_other_deletions(self):
        busy = self.file('logs/busy.log')
        other = self.file('logs/other.log')
        original = Path.unlink
        def unlink(path, *args, **kwargs):
            if path == busy:
                raise PermissionError('invented file lock')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'unlink', unlink):
            result = self.cleanup()
        self.assertEqual(result['deleted'], 1)
        self.assertEqual(len(result['errors']), 1)
        self.assertTrue(busy.exists())
        self.assertFalse(other.exists())

    def test_default_root_is_portable_app_root_not_selected_project(self):
        first = self.file('data/projects/a/data/compile.log')
        second = self.file('logs/server.log')
        with patch('app_paths.ROOT', self.root), contextlib.redirect_stdout(io.StringIO()):
            result = log_retention.cleanup_logs(now=NOW)
        self.assertEqual(result['deleted'], 2)
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())

    def test_links_are_not_followed_or_deleted(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside)/'outside.log'
            target.write_bytes(b'keep')
            os.utime(target, (NOW-8*DAY, NOW-8*DAY))
            directory = self.root/'linked'
            file = self.root/'linked.log'
            try:
                directory.symlink_to(outside, target_is_directory=True)
                file.symlink_to(target)
            except OSError:
                self.skipTest('Windows symlink privilege is unavailable')
            self.assertEqual(self.cleanup()['deleted'], 0)
            self.assertTrue(target.exists())
            self.assertTrue(file.is_symlink())
            self.assertTrue(directory.is_symlink())

    def test_safety_rejects_outside_path_without_unlink(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside)/'outside.log'
            target.write_bytes(b'keep')
            with self.assertRaises(ValueError):
                log_retention._safe_file(self.root, target)
            self.assertTrue(target.exists())

    def test_new_write_between_discovery_and_delete_is_retained(self):
        active = self.file('logs/active.log')
        def discover(*args, **kwargs):
            os.utime(active, (NOW, NOW))
            return [active]
        with patch.object(log_retention, 'expired_logs', side_effect=discover):
            self.assertEqual(self.cleanup()['deleted'], 0)
        self.assertTrue(active.exists())

    def test_junction_like_directory_is_not_traversed(self):
        link = self.root/'linked'
        hidden = self.file('linked/logs/keep.log')
        other = self.file('logs/expired.log')
        original = log_retention._linked
        with patch.object(log_retention, '_linked', side_effect=lambda p: p == link or original(p)):
            result = self.cleanup()
        self.assertEqual(result['deleted'], 1)
        self.assertTrue(hidden.exists())
        self.assertFalse(other.exists())


class TranslationRetentionRoutingTests(unittest.TestCase):
    def test_translation_entrypoints_clean_before_engine_dispatch(self):
        from translate_game import main
        commands = [['run'], ['translate'], ['sample'], ['retranslate'],
                    ['tasks', '--tasks', 'run'],
                    ['tasks', '--tasks', 'retranslate']]
        for command in commands:
            with self.subTest(command=command), \
                    patch('log_retention.cleanup_logs') as cleanup, \
                    patch('model_store.needs_model', return_value=False), \
                    patch('game_engine.dispatch', return_value=True) as dispatch:
                main(command)
                cleanup.assert_called_once_with()
                dispatch.assert_called_once()

    def test_nontranslation_entrypoints_do_not_clean(self):
        from translate_game import main
        for command in (['check'], ['status'], ['prepare'], ['repair', '--fix', 'font'],
                        ['tasks', '--tasks', 'failed']):
            with self.subTest(command=command), \
                    patch('log_retention.cleanup_logs') as cleanup, \
                    patch('model_store.needs_model', return_value=False), \
                    patch('game_engine.dispatch', return_value=True):
                main(command)
                cleanup.assert_not_called()

    def test_update_translation_also_cleans_before_task_routing(self):
        from translate_game import main
        for command in (['update'], ['tasks', '--tasks', 'update']):
            with self.subTest(command=command), \
                    patch('log_retention.cleanup_logs') as cleanup, \
                    patch('translate_game.selected_tasks') as tasks:
                main(command)
                cleanup.assert_called_once_with()
                tasks.assert_called_once()

    def test_nested_tasks_translation_cleans_once_and_next_request_cleans_again(self):
        import translate_game
        from types import SimpleNamespace
        calls = []
        def fake_main(argv):
            translate_game._translation_log_cleanup(SimpleNamespace(command='run', tasks=None))
            calls.append(argv)
            if argv == ['outer']:
                translate_game.main(['inner'])
        with patch.object(translate_game, '_main', side_effect=fake_main), \
                patch('log_retention.cleanup_logs') as cleanup:
            translate_game.main(['outer'])
            self.assertEqual(cleanup.call_count, 1)
            translate_game.main(['next'])
            self.assertEqual(cleanup.call_count, 2)
        self.assertEqual(calls, [['outer'], ['inner'], ['next']])


if __name__ == '__main__':
    unittest.main()
