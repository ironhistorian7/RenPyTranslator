"""Synthetic validation routing and serious-issue reporting; no game execution."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import diagnostics
import patch_validation
import support_report


class KiriValidationTests(unittest.TestCase):
    def test_static_result_is_saved_and_failure_preserved(self):
        for status in ('passed', 'failed'):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp)
                cfg = {'engine': 'kirikiri'}
                checker = unittest.mock.Mock(return_value={'status': status, 'failures': []})
                with patch.dict(sys.modules, {'kirikiri_project': SimpleNamespace(static_check=checker)}), \
                     patch('patch_validation._rpg_check', side_effect=AssertionError('wrong engine')), \
                     patch('patch_validation._execute', side_effect=AssertionError('game executed')):
                    result = patch_validation.run(project, cfg, mode='render')
                checker.assert_called_once_with(project.resolve(), cfg)
                self.assertEqual(result['status'], status)
                self.assertEqual(result['mode'], 'static')
                self.assertEqual(json.loads(Path(result['report']).read_text(encoding='utf-8')), result)
                self.assertEqual(json.loads((project/'data/patch-validation.json').read_text(encoding='utf-8')), result)

    def test_failed_check_uses_existing_failure_flow(self):
        import translate_game
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            cfg = {'engine': 'kirikiri'}
            checker = unittest.mock.Mock(return_value={'status': 'failed', 'failures': []})
            with patch.dict(sys.modules, {'kirikiri_project': SimpleNamespace(static_check=checker)}), \
                 patch('translate_game.package') as package:
                with self.assertRaises(RuntimeError):
                    translate_game.check_and_export(project, cfg)
                package.assert_not_called()

    def test_kiri_error_emits_failed_issue_and_persists_code(self):
        class KiriError(Exception):
            pass
        with tempfile.TemporaryDirectory() as tmp:
            token = diagnostics._STATE.set({'stage': 'KiriKiri patch output', 'project': tmp})
            try:
                for code in ('kirikiri_read_failure', 'kirikiri_format', 'kirikiri_patch_conflict'):
                    error = KiriError('PRIVATE INPUT')
                    error.error_code = code
                    with patch.dict(os.environ, {'RPT_DESKTOP_EVENTS': '1'}), patch('job_events.emit') as emit:
                        saved = diagnostics.report(error)
                    self.assertEqual(emit.call_args.args, ('issue',))
                    self.assertEqual(emit.call_args.kwargs['state'], 'failed')
                    self.assertEqual(emit.call_args.kwargs['code'], code)
                    self.assertNotIn('PRIVATE INPUT', str(emit.call_args))
                    record = json.loads(Path(saved).read_text(encoding='utf-8'))
                    self.assertEqual(record['error_code'], code)
                    self.assertEqual(support_report.build_report(tmp, saved)['issue']['code'], code)
            finally:
                diagnostics._STATE.reset(token)

if __name__ == '__main__': unittest.main()
