"""Synthetic request metadata only; no games, production caches or GPU calls."""
import io
import json
import tempfile
import unittest
import urllib.error
from contextlib import ExitStack, redirect_stderr
from pathlib import Path
from unittest.mock import Mock, patch

import translation
from failed_repair import saved_output


CFG={'model':'fake','language':'korean','source_language':'english',
     'endpoint':'http://127.0.0.1:1','num_ctx':4096}


class RequestDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for target,value in [('hy_backend.is_hy',True),('translation.runtime_status',{}),
                             ('diagnostics.stage',None),('diagnostics.report',None)]:
            self.stack.enter_context(patch(target,return_value=value))

    def rows(self,n=1):
        return [dict(id='invented-'+str(i),source='An invented sentence '+str(i)+'.',
                     kind='dialogue',usage='dialogue') for i in range(n)]

    def test_missing_batch_rows_have_specific_errors_and_keep_valid_sibling(self):
        response={'message':{'content':'[0] 새 문장입니다.'},'done_reason':'stop'}
        with patch('translation.request',return_value=response),self.assertRaises(translation.BatchTranslationError) as caught:
            translation.translate_batch(self.rows(3),{},CFG)
        error=caught.exception
        self.assertEqual([r['id'] for r in error.output],['invented-0'])
        self.assertEqual([f['expected_id'] for f in error.failures],['1','2'])
        for f in error.failures:
            self.assertEqual(f['error_code'],'missing_response_item')
            self.assertEqual(f['received_ids'],['0'])
            self.assertNotIn('Visual-stripped',f['error'])
            self.assertNotIn('input_format',f)

    def test_restore_error_has_separate_format_metadata_and_successful_transport(self):
        rows=self.rows();rows[0]['source']='Ask {i}[reader]{/i} tomorrow.'
        success=Mock();events=Mock()
        cfg=dict(CFG,_recovery_request_succeeded=success,_recovery_event=events,_runtime_generation=2)
        with patch('translation.request',return_value={'message':{'content':'[0] 내일 물어봐.'}}),self.assertRaises(translation.BatchTranslationError) as caught:
            translation.translate_batch(rows,{},cfg)
        f=caught.exception.failures[0]
        self.assertEqual(f['input_format'],'renpy_visual_stripped_v1')
        self.assertNotIn('Visual-stripped',f['error'])
        success.assert_called_once()
        self.assertEqual(events.call_args_list[0].args,('request_started',))
        self.assertEqual(events.call_args_list[1].kwargs['status'],'http_success')
        self.assertEqual(events.call_args_list[0].kwargs['generation'],2)
        self.assertNotIn('source',events.call_args_list[0].kwargs)

    def test_http_error_does_not_reset_recovery_budget(self):
        success=Mock();events=Mock()
        cfg=dict(CFG,_recovery_request_succeeded=success,_recovery_event=events)
        exc=urllib.error.HTTPError('http://localhost/api/chat',500,'runner failed',{},io.BytesIO())
        with patch('translation.request',side_effect=exc),self.assertRaises(urllib.error.HTTPError):
            translation.translate_batch(self.rows(),{},cfg)
        success.assert_not_called()
        self.assertEqual(events.call_args_list[-1].kwargs['status'],'transport_error')
        self.assertEqual(events.call_args_list[-1].kwargs['http_status'],500)

    def test_blocking_diagnostic_has_separate_request_timeout(self):
        with patch('translation.request',return_value={'message':{'content':'[0] 새 문장입니다.'}}) as request:
            translation.translate_batch(self.rows(),{},dict(CFG,_cuda_diagnostic_mode='blocking'))
        self.assertEqual(request.call_args.kwargs,{'timeout':1800})

    def test_log_failure_does_not_repeat_or_reject_successful_translation(self):
        cfg=dict(CFG,_recovery_request_succeeded=Mock(side_effect=OSError('log blocked')),
                 _recovery_event=Mock(side_effect=OSError('log blocked')))
        with patch('translation.request',return_value={'message':{'content':'[0] 새 문장입니다.'}}) as request,redirect_stderr(io.StringIO()):
            result,_=translation.translate_batch(self.rows(),{},cfg)
        self.assertEqual(len(result),1)
        request.assert_called_once()

    def test_trace_links_run_generation_server_request_and_monotonic_time(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=dict(CFG,_trace_dir=directory,_recovery_run_id='run-test',_runtime_generation=3,
                     _runtime_pid=42,_runtime_log='invented-server.log')
            translation.trace_event(cfg,'request-test','request_started')
            record=json.loads((Path(directory)/'translation-requests.jsonl').read_text(encoding='utf8'))
        self.assertEqual(record['run_id'],'run-test')
        self.assertEqual(record['generation'],3)
        self.assertEqual(record['server_pid'],42)
        self.assertEqual(record['request_id'],'request-test')
        self.assertGreater(record['monotonic_ns'],0)
        self.assertGreater(record['thread_id'],0)

    def test_separate_input_format_recovers_saved_output_and_legacy_still_works(self):
        source='Ask {i}[reader]{/i} about {b}tomorrow{/b}.'
        error='markup mismatch; output='+repr('내일 <rpt000/>에게 물어봐.')
        expected='내일 {i}[reader]{/i}에게 물어봐.'
        self.assertEqual(saved_output(source,error,CFG,input_format='renpy_visual_stripped_v1'),expected)
        self.assertEqual(saved_output(source,'Visual-stripped: '+error,CFG),expected)


if __name__=='__main__':unittest.main()
