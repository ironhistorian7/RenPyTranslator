"""Local publishing uses invented app files only; never build or read user assets."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import build_portable


class LocalPublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.bundle = self.root/'dist/RenPyTranslator'
        self.bundle.mkdir(parents=True)
        for parent, version in ((self.root, 'old'), (self.bundle, 'new')):
            for name in build_portable.LOCAL_COMPONENTS:
                target = parent/name
                if name.startswith('_'):
                    target.mkdir()
                    (target/'runtime.bin').write_text(version, encoding='utf-8')
                else:
                    target.write_text(version, encoding='utf-8')
        # Stale runtime files must disappear on success, without merging sets.
        (self.root/'_internal/old-only.bin').write_bytes(b'old')
        for name in ('models/keep.gguf', 'project/keep.txt', 'data/settings.json',
                     'portable/old.zip', 'app/source.py'):
            path = self.root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'keep')
        self.original = self.snapshot()
        self.addCleanup(patch.stopall)
        patch.object(build_portable, 'ROOT', self.root).start()
        self.processes = patch.object(build_portable, '_active_local_processes', return_value=[]).start()
        self.probe = patch.object(build_portable, '_probe_local_path').start()

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob('*') if p.is_file()}

    def publish(self):
        with contextlib.redirect_stdout(io.StringIO()):
            build_portable.publish_local(self.bundle)

    def assert_unchanged(self):
        self.assertEqual(self.snapshot(), self.original)
        self.assertFalse(list(self.root.glob('.local-publish-*')))
        self.assertFalse(list((self.root/'build').glob('.local-publish-*')))

    def test_running_app_refuses_before_staging_or_copying(self):
        self.processes.return_value = ['4321']
        with patch.object(build_portable.shutil, 'copytree') as copy, \
                patch.object(build_portable.tempfile, 'mkdtemp') as stage:
            with self.assertRaisesRegex(RuntimeError, 'Close RenPyTranslator.*4321'):
                self.publish()
        copy.assert_not_called()
        stage.assert_not_called()
        self.assert_unchanged()

    def test_locked_runtime_refuses_before_any_change(self):
        def probe(path):
            if path.name == 'runtime.bin':
                raise PermissionError('simulated runtime lock')
        self.probe.side_effect = probe
        with patch.object(build_portable.tempfile, 'mkdtemp') as stage:
            with self.assertRaisesRegex(PermissionError, 'runtime lock'):
                self.publish()
        stage.assert_not_called()
        self.assert_unchanged()

    def test_newly_opened_app_refuses_after_staging_without_touching_targets(self):
        self.processes.side_effect = [[], ['4321']]
        with self.assertRaisesRegex(RuntimeError, 'Close RenPyTranslator'):
            self.publish()
        self.assert_unchanged()

    def test_copy_failure_keeps_complete_old_set(self):
        original = build_portable.shutil.copytree
        def copy(source, target, *args, **kwargs):
            if Path(source).name == '_desktop':
                raise OSError('simulated copy failure')
            return original(source, target, *args, **kwargs)
        with patch.object(build_portable.shutil, 'copytree', side_effect=copy):
            with self.assertRaisesRegex(OSError, 'copy failure'):
                self.publish()
        self.assert_unchanged()

    def test_backup_move_failure_restores_complete_old_set(self):
        original = build_portable._move_local
        def move(root, source, target):
            if source == self.root/'_desktop':
                raise PermissionError('simulated late backup lock')
            return original(root, source, target)
        with patch.object(build_portable, '_move_local', side_effect=move):
            with self.assertRaisesRegex(PermissionError, 'late backup lock'):
                self.publish()
        self.assert_unchanged()

    def test_install_move_failure_restores_complete_old_set(self):
        original = build_portable._move_local
        def move(root, source, target):
            if source.parent.name == 'pending' and source.name == 'RenPyTranslator.exe':
                raise OSError('simulated install failure')
            return original(root, source, target)
        with patch.object(build_portable, '_move_local', side_effect=move):
            with self.assertRaisesRegex(OSError, 'install failure'):
                self.publish()
        self.assert_unchanged()

    def test_success_replaces_complete_components_and_preserves_other_assets(self):
        self.publish()
        for name in build_portable.LOCAL_COMPONENTS:
            path = self.root/name
            if path.is_dir():
                self.assertEqual(sorted(p.name for p in path.iterdir()), ['runtime.bin'])
                path = path/'runtime.bin'
            self.assertEqual(path.read_text(encoding='utf-8'), 'new')
        for name, content in self.original.items():
            if name.split('/')[0] not in build_portable.LOCAL_COMPONENTS:
                self.assertEqual((self.root/name).read_bytes(), content)
        self.assertFalse(list(self.root.glob('.local-publish-*')))
        self.assertFalse(list((self.root/'build').glob('.local-publish-*')))
        self.assertEqual(self.processes.call_count, 2)

    def test_incomplete_bundle_refuses_before_target_changes(self):
        (self.bundle/'RenPyTranslator.exe').unlink()
        self.original = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'Incomplete executable bundle'):
            self.publish()
        self.assert_unchanged()

    def test_staging_stays_under_build_and_cleanup_refuses_other_tool_paths(self):
        original = build_portable.tempfile.mkdtemp
        with patch.object(build_portable.tempfile, 'mkdtemp', wraps=original) as stage:
            self.publish()
        self.assertEqual(stage.call_args.kwargs['dir'], self.root/'build')
        for path in (self.root, self.root/'build', self.root/'app', self.root/'.local-publish-other',
                     self.root/'build/other'):
            with self.assertRaises(ValueError):
                build_portable._remove_local_stage(self.root, path)
        self.assertEqual((self.root/'app/source.py').read_bytes(), b'keep')

    def test_moves_and_cleanup_refuse_paths_outside_tool_root(self):
        with tempfile.TemporaryDirectory() as outside:
            path = Path(outside)
            sentinel = path/'keep.txt'
            sentinel.write_bytes(b'keep')
            with self.assertRaisesRegex(ValueError, 'below tool root'):
                build_portable._move_local(self.root, self.root/'RenPyTranslator.exe', path/'app.exe')
            with self.assertRaisesRegex(ValueError, 'below tool root'):
                build_portable._remove_local_stage(self.root, path)
            self.assertEqual(sentinel.read_bytes(), b'keep')
        self.assert_unchanged()

    def test_normal_build_publishes_only_archives(self):
        with patch.object(build_portable, 'package_editions') as archives, \
                patch.object(build_portable, 'publish_local') as local:
            build_portable.publish_build_output(self.bundle, ['light'])
        archives.assert_called_once_with(self.root, self.bundle, ['light'], build_portable.export_model)
        local.assert_not_called()
        self.assert_unchanged()

    def test_local_only_publishes_no_archives(self):
        with patch.object(build_portable, 'package_editions') as archives, \
                patch.object(build_portable, 'publish_local') as local:
            build_portable.publish_build_output(self.bundle, ['light'], local_only=True)
        archives.assert_not_called()
        local.assert_called_once_with(self.bundle)
        self.assert_unchanged()


@unittest.skipUnless(os.name == 'nt', 'Windows process-query contract')
class LocalProcessProbeTests(unittest.TestCase):
    def test_filters_by_application_executable_path_without_querying_command_lines(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            rows = [
                {'ProcessId': 1, 'Name': 'RenPyTranslator.exe',
                 'ExecutablePath': str(root/'RenPyTranslator.exe')},
                {'ProcessId': 2, 'Name': 'RenPyTranslator-UI.exe',
                 'ExecutablePath': str(root/'_desktop/RenPyTranslator-UI.exe')},
                {'ProcessId': 3, 'Name': 'RenPyTranslator-cli.exe',
                 'ExecutablePath': str(root.parent/'other/RenPyTranslator-cli.exe')},
                {'ProcessId': 4, 'Name': 'python.exe',
                 'ExecutablePath': str(root/'.venv/Scripts/python.exe')},
                {'ProcessId': 5, 'Name': 'RenPyTranslator-cli.exe', 'ExecutablePath': None},
            ]
            result = subprocess.CompletedProcess([], 0, stdout=json.dumps(rows))
            with patch.object(build_portable.subprocess, 'run', return_value=result) as query:
                self.assertEqual(build_portable._active_local_processes(root), ['1', '2', '5'])
            self.assertNotIn('CommandLine', query.call_args.args[0][-1])
            self.assertEqual(query.call_args.kwargs['timeout'], 15)
            self.assertEqual(query.call_args.kwargs['creationflags'], subprocess.CREATE_NO_WINDOW)

    def test_process_query_failure_refuses_local_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(build_portable.subprocess, 'run', side_effect=OSError('simulated query failure')):
                with self.assertRaisesRegex(RuntimeError, 'Cannot check running applications'):
                    build_portable._active_local_processes(Path(temp).resolve())


if __name__ == '__main__':
    unittest.main()
