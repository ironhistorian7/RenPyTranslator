"""CPU-only tokenizer transport and restart regressions; no real server is started."""
from contextlib import contextmanager, ExitStack
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import request_budget
import runtime_recovery
from test_runtime_recovery import EvidenceStub


class Response(io.BytesIO):
    def __init__(self, value):
        super().__init__(json.dumps(value).encode())


class TokenizerTransportTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('model_runtime.track_request', side_effect=lambda endpoint, route, body: body))
        self.stack.enter_context(patch('pathlib.Path.read_text', return_value='runner --port 45678'))
        self.stack.enter_context(patch('runtime_recovery.Evidence', EvidenceStub))
        self.stack.enter_context(patch('runtime_recovery.cancelled', lambda: None))
        self.stack.enter_context(patch('runtime_recovery.resources', lambda: {}))
        self.pauses = []
        self.stack.enter_context(patch('runtime_recovery.pause', self.pauses.append))
        self.cfg = {'endpoint': 'http://127.0.0.1:12345', '_runtime_log': 'synthetic-runtime.log',
                    'model': 'invented-model', 'num_ctx': 16384}
        self.addCleanup(request_budget.forget, self.cfg)
        EvidenceStub.events = []
        EvidenceStub.finalizations = []

    def opener(self, callback):
        class Opener:
            def open(self, request, timeout):
                return callback(request.full_url, timeout)
        self.stack.enter_context(patch('urllib.request.build_opener', return_value=Opener()))

    @staticmethod
    def fit(cfg):
        return request_budget.fit({}, cfg, lambda _: {'messages': [{'content': 'synthetic input'}],
                                   'options': {'num_predict': 10, 'num_ctx': 16384}})

    @staticmethod
    def http(code):
        return HTTPError('http://127.0.0.1:45678/tokenize', code, 'synthetic', {}, io.BytesIO(b'error'))

    def test_probe_missing_tokenizer_falls_back_to_estimate(self):
        calls = []
        def respond(url, timeout):
            calls.append(url)
            if url.endswith('/tokenize'): raise self.http(404)
            return Response({})
        self.opener(respond)
        counter = request_budget.Counter(self.cfg)
        self.assertEqual(counter.method, 'conservative_estimate')
        self.assertGreater(counter.count('another input'), 0)
        self.assertEqual(len(calls), 2)

    def test_probe_reset_and_http_500_propagate_as_recoverable(self):
        for failure in (ConnectionResetError('synthetic reset'), self.http(500)):
            with self.subTest(error=type(failure).__name__):
                def respond(url, timeout):
                    if url.endswith('/tokenize'): raise failure
                    return Response({})
                with patch('urllib.request.build_opener') as build:
                    build.return_value.open.side_effect = lambda req, timeout: respond(req.full_url, timeout)
                    with self.assertRaises(type(failure)) as raised:
                        request_budget.Counter(self.cfg)
                self.assertIs(raised.exception, failure)
                self.assertTrue(runtime_recovery.recoverable(raised.exception))

    def test_fit_reset_restarts_after_teardown_and_retains_saved_results(self):
        events = []; known = {'prior': 'already saved'}; counts = []
        state = {'fail': True}
        def respond(url, timeout):
            if url.endswith('/api/generate'): return Response({})
            self.assertTrue(url.startswith('http://127.0.0.1:45678/'))
            counts.append(url)
            if state['fail'] and len(counts) == 2:
                state['fail'] = False
                raise ConnectionResetError('synthetic reset')
            return Response({'tokens': [1, 2, 3]})
        self.opener(respond)
        @contextmanager
        def owned_runtime(cfg):
            events.append('start')
            try:
                yield dict(cfg, **self.cfg)
            finally:
                request_budget.forget(self.cfg)
                events.append('stop')
        def work(cfg):
            self.assertEqual(known, {'prior': 'already saved'})
            self.fit(cfg)
            return 'completed'
        with patch('hy_backend.runtime', owned_runtime):
            self.assertEqual(runtime_recovery.run(Path('.'), self.cfg, work, lambda: len(known)), 'completed')
        self.assertEqual(events, ['start', 'stop', 'start', 'stop'])
        self.assertEqual(self.pauses, [10])
        self.assertEqual(known, {'prior': 'already saved'})
        self.assertFalse(any(key[0] == self.cfg['_runtime_log'] for key in request_budget._COUNTERS))
        self.assertGreaterEqual(len(counts), 4)  # probe + fit in both generations

    def test_repeated_fit_reset_obeys_existing_restart_limit(self):
        tokens_per_generation = [0]
        def respond(url, timeout):
            if url.endswith('/api/generate'):
                tokens_per_generation[0] = 0
                return Response({})
            tokens_per_generation[0] += 1
            if tokens_per_generation[0] > 1: raise ConnectionResetError('synthetic reset')
            return Response({'tokens': [1]})
        self.opener(respond)
        starts = []
        @contextmanager
        def owned_runtime(cfg):
            starts.append('start')
            try: yield dict(cfg, **self.cfg)
            finally: request_budget.forget(self.cfg)
        with patch('hy_backend.runtime', owned_runtime), self.assertRaises(ConnectionResetError):
            runtime_recovery.run(Path('.'), self.cfg, self.fit, lambda: 1)
        self.assertEqual(len(starts), 4)
        self.assertEqual(self.pauses, [10, 30, 180, 2])
        self.assertFalse(any(key[0] == self.cfg['_runtime_log'] for key in request_budget._COUNTERS))

    def test_malformed_json_and_budget_error_are_not_transport(self):
        class BadResponse(io.BytesIO):
            def __init__(self): super().__init__(b'{invalid')
        self.opener(lambda url, timeout: BadResponse())
        with self.assertRaises(ValueError) as raised:
            request_budget.post('http://127.0.0.1:12345', '/tokenize', {})
        self.assertFalse(runtime_recovery.recoverable(raised.exception))
        cfg = dict(self.cfg, num_ctx=10)
        cfg.pop('_runtime_log')
        with self.assertRaises(request_budget.InputBudgetError) as raised:
            request_budget.fit({}, cfg, lambda _: {'messages': [{'content': 'oversized prompt'}],
                'options': {'num_predict': 10, 'num_ctx': 10}})
        self.assertFalse(runtime_recovery.recoverable(raised.exception))

    def test_non_owned_endpoint_does_not_discover_or_contact_tokenizer(self):
        calls = []
        self.opener(lambda url, timeout: calls.append(url) or Response({}))
        cfg = dict(self.cfg, endpoint='http://localhost:12345')
        cfg.pop('_runtime_log')
        counter = request_budget.Counter(cfg)
        self.assertEqual(counter.method, 'conservative_estimate')
        self.assertGreater(counter.count('synthetic'), 0)
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
