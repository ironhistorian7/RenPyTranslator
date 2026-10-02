"""Synthetic UI/CLI contract checks; never run a game, model or real worker."""
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import gui


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root_patch = patch.object(gui, 'ROOT', Path(self.temp.name))
        self.root_patch.start()
        self.window = tk.Tk()
        self.window.withdraw()
        self.app = gui.App(self.window, theme='dark')

    def tearDown(self):
        self.app.proc = None
        self.app.close()
        self.root_patch.stop()
        self.temp.cleanup()

    def test_navigation_keeps_inputs_and_requires_deliberate_jobs(self):
        a = self.app
        with patch('gui.subprocess.Popen', side_effect=AssertionError('Unexpected worker')):
            a.path.set('D:/Games/Example')
            a.output.set('custom-output')
            a.navigate(True)
            a.tasks['routes'].set(True)
            a.navigate(True)  # Clicking the already-open page must not discard work.
            self.assertEqual(a.selected(), ['routes'])
            a.navigate(False)
            self.assertEqual(a.selected(), ['run'])
            a.navigate(True)
            self.assertEqual(a.selected(), [])
            self.assertEqual(a.path.get(), 'D:/Games/Example')
            self.assertEqual(a.output.get(), 'custom-output')

    def test_project_worker_cancel_and_result_contract(self):
        a = self.app
        a.navigate(True)
        a.kind_buttons[1].invoke()
        a.path.set('D:/Example-existing-kr')
        a.tasks['names'].set(True)
        a.tasks['font'].set(True)
        fake = Mock()
        with patch('gui.subprocess.Popen', return_value=fake) as launch, \
             patch('gui.threading.Thread') as worker, \
             patch('gui.cli_command', return_value=['portable-cli.exe']):
            a.run()
            argv = launch.call_args.args[0]
            self.assertEqual(argv[:5], ['portable-cli.exe', 'tasks', '--tasks', 'names', 'font'])
            self.assertIn('--project', argv)
            self.assertNotIn('--source', argv)
            self.assertEqual(launch.call_args.kwargs['cwd'], Path(self.temp.name))
            self.assertIn('disabled', a.start.state())
            a.cancel()
            marker = a.cancel_path
            self.assertTrue(marker.is_file())
            self.assertEqual(a.status.get(), '취소 요청 중')
            a.events.put(('log', 'Output folder: D:/Example-existing-kr\n'))
            a.events.put(('done', 130))
            a.poll()
            self.assertEqual(a.result, Path('D:/Example-existing-kr'))
            self.assertIsNone(a.proc)
            self.assertFalse(marker.exists())
            self.assertEqual(a.status.get(), '취소됨')

    def test_empty_selection_cannot_launch(self):
        a = self.app
        a.navigate(True)
        a.path.set('D:/Example')
        with patch('gui.subprocess.Popen') as launch, patch('gui.messagebox.showerror') as error:
            a.run()
            launch.assert_not_called()
            error.assert_called_once()

    def test_switching_appearance_preserves_jobs_and_log(self):
        a = self.app
        a.navigate(True)
        a.tasks['failed'].set(True)
        a.toggle_repairs()
        a.write('한글 로그 / saved output\n')
        for mode in ('light', 'dark', 'light'):
            a.theme.apply(mode)
            a._paint()
            self.window.update_idletasks()
            self.assertEqual(a.selected(), ['failed'])
            self.assertTrue(a.repairs_open)
            self.assertIn('한글 로그', a.log.get('1.0', 'end'))
            self.assertEqual(a.log.cget('background'), a.theme.colors['log'])


if __name__ == '__main__':
    unittest.main()
