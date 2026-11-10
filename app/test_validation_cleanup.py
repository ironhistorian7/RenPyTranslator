"""Invented temporary workspaces and mocked engine calls only."""
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from patch_validation import (cleanup_workspaces, _cleanup_path, _blocking_failures,
                              _execute, _failures, _install_probe, _mark_known_causes, _repair_candidates, classify, run)


class ValidationCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)/'project'
        self.project.mkdir()
        self.artifact = self.project/'data/validation-runs/example'
        self.artifact.mkdir(parents=True)

    def write(self, path, text='fixture'):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def workspace(self, name):
        workspace = self.artifact/name
        self.write(workspace/'staging/game/script.rpy', 'invented script')
        self.write(workspace/'data/compile.log', 'technical log')
        self.write(workspace/'data/probe-events.jsonl', '{"check":"complete"}\n')
        self.write(workspace/'data/probe-input.json', 'full text must not be retained')
        return workspace

    def test_cleanup_keeps_only_diagnostics_and_remaps_log(self):
        baseline = self.workspace('baseline')
        self.workspace('patched')
        outside = self.write(self.project/'data/translations.jsonl', 'saved translations')
        report = {'baseline':[{'log':str(baseline/'data/compile.log')}]}
        records = cleanup_workspaces(self.artifact, report)
        self.assertEqual([row['status'] for row in records], ['removed','removed'])
        self.assertFalse(baseline.exists())
        self.assertFalse((self.artifact/'patched').exists())
        diagnostic = self.artifact/'diagnostics/baseline/data/compile.log'
        self.assertEqual(diagnostic.read_text(), 'technical log')
        self.assertEqual(report['baseline'][0]['log'], str(diagnostic))
        self.assertFalse(list((self.artifact/'diagnostics').rglob('probe-input.json')))
        self.assertEqual(outside.read_text(), 'saved translations')

    def test_modal_progress_has_its_own_interaction_return_timer(self):
        workspace=self.workspace('patched')
        _install_probe(workspace,[],[],'korean')
        script=(workspace/'staging/game/zzzz_rpt_validation_probe.rpy').read_text(encoding='utf-8')
        progress=script.split('screen _rpt_validation_progress',1)[1].split('\nlabel ',1)[0]
        self.assertIn('    modal True\n',progress)
        self.assertIn('    timer .001 action Return(0)\n',progress)

    def test_cleanup_refuses_wrong_root_and_escaping_target(self):
        wrong = self.project/'staging'
        self.write(wrong/'baseline/keep.txt')
        records = cleanup_workspaces(wrong)
        self.assertTrue(all(row['status']=='failed' for row in records))
        self.assertTrue((wrong/'baseline/keep.txt').is_file())
        for path in (self.artifact, self.project, self.artifact/'baseline/../../../other'):
            with self.assertRaises(ValueError):
                _cleanup_path(self.artifact, path)

    def test_cleanup_refuses_linked_descendant_before_deleting(self):
        baseline = self.workspace('baseline')
        linked = self.write(baseline/'staging/game/link')
        original = Path.is_junction
        with patch.object(Path, 'is_junction', lambda path: path==linked or original(path)):
            records = cleanup_workspaces(self.artifact)
        self.assertEqual(records[0]['status'], 'failed')
        self.assertTrue((baseline/'staging/game/script.rpy').is_file())

    def _mock_pair(self, execute):
        def snapshot(project, cfg, target):
            self.latest_artifact = target.parent
            (target/'staging/game').mkdir(parents=True)
            self.write(target/'data/compile.log', 'diagnostic survives')
            return target
        stack = ExitStack()
        stack.enter_context(patch('patch_validation._render_rows', return_value=[]))
        stack.enter_context(patch('patch_validation._snapshot', side_effect=snapshot))
        stack.enter_context(patch('patch_validation._baseline', return_value={'confident':True}))
        stack.enter_context(patch('patch_validation._source_authenticity', return_value={'verified':True}))
        stack.enter_context(patch('patch_validation._original_code_files', return_value={'game/original.rpyc'}))
        stack.enter_context(patch('patch_validation._menu_targets', return_value=[]))
        stack.enter_context(patch('patch_validation._execute', side_effect=execute))
        return stack

    def _last_report(self):
        report = self.latest_artifact/'report.json'
        return report, json.loads(report.read_text(encoding='utf-8'))

    def test_exception_and_cancellation_both_clean_and_keep_report(self):
        for exception, expected in ((RuntimeError('fixture failure'),'failed'),
                                    (KeyboardInterrupt(),'cancelled')):
            with self.subTest(expected=expected):
                with self._mock_pair(exception):
                    if expected=='cancelled':
                        with self.assertRaises(KeyboardInterrupt):
                            run(self.project, {'language':'korean'}, mode='compile')
                    else:
                        self.assertEqual(run(self.project, {'language':'korean'}, mode='compile')['status'], expected)
                report, payload = self._last_report()
                self.assertEqual(payload['status'], expected)
                self.assertTrue(all(row['status']=='removed' for row in payload['cleanup']))
                self.assertFalse((report.parent/'baseline').exists())
                self.assertFalse((report.parent/'patched').exists())
                self.assertTrue((report.parent/'diagnostics/baseline/data/compile.log').is_file())

    def test_shared_lint_and_priority_advisory_pass_but_new_lint_fails(self):
        common = dict(check='lint_issue', status='failed', error_type='LintIssue',
                      error='Original missing label', file='game/original.rpy', line=5)
        priority = dict(check='lint_issue', status='failed', error_type='LintIssue',
                        error='The init priority (1050) is not in the -999 to 999 range.',
                        file='game/zz_rpt_names.rpy', line=1)
        capture = dict(check='lint_capture', status='passed')
        added = dict(check='lint_issue', status='failed', error_type='LintIssue',
                     error='Unknown string format code: %f',
                     file='game/tl/korean/scene.rpy', line=10)
        for new_failure in (False, True):
            with self.subTest(new_failure=new_failure):
                def execute(workspace, operation, timeout=180):
                    events = [capture, common]
                    if workspace.name=='patched':
                        events += [priority]+([added] if new_failure else [])
                    return dict(check=operation, status='failed' if operation=='lint' else 'passed',
                                returncode=0, events=events if operation=='lint' else [])
                with self._mock_pair(execute):
                    result = run(self.project, {'language':'korean'}, mode='compile', auto_fix=False)
                self.assertEqual(result['status'], 'failed' if new_failure else 'passed')
                self.assertEqual(len(result['failures']), 1 if new_failure else 0)
                self.assertEqual(len(result['diagnostics']), 2)
                if new_failure:
                    self.assertEqual(result['failures'][0]['attribution'], 'ours')

    def test_timeout_cannot_be_hidden_by_previous_advisory(self):
        advisory = dict(check='lint_issue', status='failed', error_type='LintIssue',
                        error='The init priority (1000) is not in the -999 to 999 range.')
        timed_out = dict(check='lint', status='failed', error_type='Timeout',
                         error='Validation timed out', events=[advisory])
        self.assertEqual(len(_failures([timed_out])), 2)
        evidence = dict(confident=True, code_inventory_equal=True, probe_equal=True,
                        source_authenticity={'verified':True})
        failures = classify([], [timed_out], evidence)['failures']
        self.assertEqual([item['error_type'] for item in _blocking_failures(failures)], ['Timeout'])

    def test_timeout_result_cleans_workspaces_and_preserves_failure(self):
        def execute(workspace, operation, timeout=180):
            return dict(check=operation, status='failed', error_type='Timeout',
                        error='Validation timed out', returncode=None, events=[])
        with self._mock_pair(execute):
            result=run(self.project, {'language':'korean'}, mode='compile')
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['failures'][0]['error_type'],'Timeout')
        self.assertTrue(all(item['status']=='removed' for item in result['cleanup']))

    def test_exit_zero_traceback_or_parse_error_is_not_hidden_by_lint_advisory(self):
        workspace=self.workspace('baseline')
        advisory=dict(check='lint_issue',status='failed',error_type='LintIssue',
                      error='The init priority (1000) is not in the -999 to 999 range.')
        evidence=dict(confident=True,code_inventory_equal=True,probe_equal=True,
                      source_authenticity={'verified':True})
        for filename,error_type in (('traceback.txt','EngineTraceback'),('errors.txt','EngineParseError')):
            with self.subTest(filename=filename):
                def engine_stub(args,**kwargs):
                    self.write(Path(kwargs['env']['RPT_VALIDATION_EVENTS']),
                               json.dumps(advisory)+'\n'+json.dumps(dict(check='lint_capture',status='passed'))+'\n')
                    self.write(workspace/'staging'/filename,
                               'File "game/invented.rpy", line 9\nException: fixture engine failure')
                    return SimpleNamespace(returncode=0)
                with patch('engine.find_launcher',return_value=workspace/'launcher.py'), \
                     patch('engine.select_windows_runtime',return_value=workspace/'python.exe'), \
                     patch('engine.interpreter_args',return_value=['fake-python']), \
                     patch('workspace_files.command',return_value=['fake-launcher']), \
                     patch('engine.owned_run',side_effect=engine_stub):
                    check=_execute(workspace,'lint')
                self.assertEqual(check['returncode'],0)
                self.assertEqual(check['error_type'],error_type)
                # Even a matching baseline engine crash is not a harmless lint message.
                failures=classify([check],[check],evidence)['failures']
                self.assertEqual([item['error_type'] for item in _blocking_failures(failures)],[error_type])
                with self._mock_pair(lambda workspace,operation,timeout=180:
                        check if operation=='lint' else dict(check=operation,status='passed',events=[])):
                    self.assertEqual(run(self.project,{'language':'korean'},mode='compile')['status'],'failed')

    def test_percent_repair_changes_only_exact_generated_literal(self):
        workspace=self.workspace('patched')
        target=self.write(workspace/'staging/game/tl/korean/scene.rpy', '"10% of the work, %s"\n')
        row=dict(id='percent',file='scene.rpy',line=1,literal_index=0,
                 source='10% of the work, %s',text='10% of the work, %s')
        event=dict(check='lint_issue',status='failed',file='game/tl/korean/scene.rpy',line=1,
                   error_type='LintIssue',error='Unknown string format code: %f')
        checks=[dict(check='lint',status='failed',events=[event])]
        _mark_known_causes(workspace,{'language':'korean'},checks,[row])
        self.assertEqual(event['known_generated_rule'],'literal_percent_display')
        event['attribution']='ours'
        proposals=_repair_candidates(workspace,{'language':'korean'},[event],[row])
        self.assertEqual(len(proposals),1)
        self.assertEqual(target.read_text(encoding='utf-8'), '"10％ of the work, %s"\n')
        self.assertNotIn('before_sha256',proposals[0])
        original_event=dict(event,file='game/scene.rpy')
        original_event.pop('known_generated_rule')
        original_event.pop('id')
        _mark_known_causes(workspace,{'language':'korean'},
                           [dict(check='lint',events=[original_event])],[row])
        self.assertNotIn('known_generated_rule',original_event)


if __name__=='__main__':
    unittest.main()
