"""Exercise the actual translation preflight failure boundary without inference."""
import unittest
import io
from http.client import IncompleteRead
from urllib.error import HTTPError
from unittest.mock import patch

import translation
from request_diagnostics import mark_transport_failure
from runtime_recovery import recoverable
from support_report import classify_issue


class TokenizerPreflightDiagnosticsTests(unittest.TestCase):
    def test_both_api_clients_preserve_network_metadata_and_http_body(self):
        import request_budget
        for client in (request_budget.post,translation.request):
            error=HTTPError('http://127.0.0.1:4321/tokenize',503,'synthetic',{},io.BytesIO(b'{"error":"runner stopped"}'))
            with self.subTest(client=client.__module__), \
                 patch('urllib.request.build_opener') as build, \
                 self.assertRaises(HTTPError) as raised:
                build.return_value.open.side_effect=error
                client('http://127.0.0.1:4321','/tokenize',{})
            self.assertIs(raised.exception,error)
            self.assertEqual(error.transport_endpoint,'http://127.0.0.1:4321')
            self.assertEqual(error.transport_route,'/tokenize')
            self.assertEqual(error.http_details['body'],'{"error":"runner stopped"}')
            self.assertTrue(recoverable(error))

    def test_incomplete_http_body_is_a_transport_error(self):
        import request_budget
        for client in (request_budget.post,translation.request):
            error=IncompleteRead(b'partial',10)
            class Response(io.BytesIO):
                def read(self,*args,**kwargs):raise error
            with self.subTest(client=client.__module__),patch('urllib.request.build_opener') as build, \
                 self.assertRaises(IncompleteRead) as raised:
                build.return_value.open.return_value=Response()
                client('http://127.0.0.1:4321','/tokenize',{})
            self.assertIs(raised.exception,error)
            self.assertTrue(recoverable(error))

    def test_model_load_transport_failure_is_not_hidden_by_estimation(self):
        import request_budget
        for error in (ConnectionResetError('synthetic load reset'),
                      HTTPError('http://127.0.0.1:4321/api/generate',500,'synthetic',{},io.BytesIO(b'runner stopped')),
                      HTTPError('http://127.0.0.1:4321/api/generate',400,'synthetic',{},io.BytesIO(b'bad model'))):
            cfg=dict(endpoint='http://127.0.0.1:4321',_runtime_log='unused-synthetic.log',model='synthetic')
            with self.subTest(error=repr(error)),patch('urllib.request.build_opener') as build, \
                 self.assertRaises(type(error)) as raised:
                build.return_value.open.side_effect=error
                request_budget.Counter(cfg)
            self.assertIs(raised.exception,error)
            self.assertEqual(error.transport_route,'/api/generate')
            self.assertEqual(recoverable(error),getattr(error,'code',None)!=400)

    def test_reset_is_identified_before_chat_and_has_route_evidence(self):
        cfg=dict(engine='renpy',source_language='english',language='korean',model='synthetic',
                 endpoint='http://127.0.0.1:1234',num_ctx=16384)
        rows=[dict(id='a',source='Hello there.',kind='dialogue',file='synthetic.rpy',block='one')]
        for log_error in (None,PermissionError('synthetic log lock')):
            error=ConnectionResetError('synthetic reset')
            mark_transport_failure(error,'http://127.0.0.1:4321','/tokenize')
            class Counter:
                method='owned_model_tokenizer';reason=None
                def count(self,text):raise error
            with self.subTest(log_error=bool(log_error)), \
                 patch('request_budget.counter',return_value=Counter()), \
                 patch('translation.request',side_effect=AssertionError('chat was reached')), \
                 patch('translation.trace_event',side_effect=log_error) as trace, \
                 patch('diagnostics.report') as report, \
                 self.assertRaises(ConnectionResetError) as raised:
                translation.translate_batch(rows,{},cfg)
            self.assertIs(raised.exception,error)
            self.assertTrue(recoverable(error))
            self.assertEqual(error.request_diagnostics['phase'],'token_count')
            self.assertEqual(error.request_diagnostics['route'],'/tokenize')
            self.assertEqual(error.request_diagnostics['endpoint'],'http://127.0.0.1:4321')
            self.assertTrue(error.request_diagnostics['failure']['restart_runtime'])
            self.assertEqual(report.call_args.args[0],error)
            self.assertEqual(trace.call_count,1)
            self.assertEqual(classify_issue(dict(error_type=type(error).__name__,
                error_code=error.error_code,error=str(error)))['code'],'server_connection')

    def test_input_budget_failure_is_not_reclassified_or_reported_as_connection(self):
        from request_budget import InputBudgetError
        error=InputBudgetError('synthetic oversized input')
        cfg=dict(engine='renpy',source_language='english',language='korean',model='synthetic')
        rows=[dict(id='a',source='Hello there.',kind='dialogue',file='synthetic.rpy',block='one')]
        with patch('request_budget.fit',side_effect=error),patch('diagnostics.report') as report, \
             self.assertRaises(translation.BatchTranslationError) as raised:
            translation.translate_batch(rows,{},cfg)
        self.assertFalse(recoverable(error))
        self.assertFalse(recoverable(raised.exception))
        self.assertEqual(raised.exception.failures[0]['error_code'],'input_budget')
        self.assertFalse(any(getattr(call.args[0],'_rpt_transport',False) for call in report.call_args_list))


if __name__=='__main__':unittest.main()
