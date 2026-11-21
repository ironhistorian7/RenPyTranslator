"""Offline checks for context audit propagation and the existing warning."""
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from request_budget import fit, should_report_adjustment
from translation import translate_batch


class FakeCounter:
    method = 'test_tokenizer'
    reason = None
    def count(self, text):
        return len(text)


class ContextBudgetAuditTests(unittest.TestCase):
    def test_adjustment_reporting_keeps_real_and_unknown_drops(self):
        for reason in ('policy_cap', 'clean_retry', 'candidate_budget'):
            with self.subTest(reason=reason):
                self.assertFalse(should_report_adjustment({'preselection_dropped': [{'reason': reason}]}))
        for budget in ({'dropped': [{'reason': 'request_window'}]},
                       {'dropped': [{'reason': 'request_window'}],
                        'preselection_dropped': [{'reason': 'candidate_budget'}]},
                       {'preselection_dropped': [{}]}):
            with self.subTest(budget=budget):
                self.assertTrue(should_report_adjustment(budget))
        self.assertFalse(should_report_adjustment({}))

    def setUp(self):
        from source_context import SourceContext
        rows=[{'id':str(i),'source':'A complete synthetic sentence.','kind':'dialogue',
               'file':'a.ks','block':'scene','speaker':'Narrator'} for i in range(100)]
        chosen=SourceContext(rows).context([rows[50]],{'engine':'kirikiri','num_ctx':16384})
        self.audit=chosen['_context_audit']
        self.selection=self.audit['surrounding_selection']

    def test_fit_preserves_selection_and_reports_window_drops_separately(self):
        context = {'_context_audit': self.audit,
                   'source_passage': [{'text': 'x' * 400}, {'text': 'near'}, {'target_id': '0'}]}
        def build(candidate):
            return {'messages': [{'role': 'user', 'content': json.dumps(candidate) + ' SOURCE'}],
                    'options': {'num_ctx': 340, 'num_predict': 80}}
        with patch('request_budget.counter', return_value=FakeCounter()):
            body, meta = fit(context, {}, build)
        self.assertEqual(meta['surrounding_selection'], self.selection)
        self.assertEqual(meta['preselection_dropped'], self.audit['preselection_dropped'])
        self.assertEqual(meta['dropped'][0]['reason'], 'request_window')
        self.assertLessEqual(meta['prompt_content_tokens'] + meta['output_reserved'] + meta['framing_reserved'], 340)
        self.assertTrue(body['messages'][0]['content'].endswith(' SOURCE'))
        self.assertNotIn('_context_audit', body['messages'][0]['content'])

    def test_policy_drop_is_silent_and_retained_in_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {'engine':'kirikiri','model': 'fake', 'endpoint': 'http://localhost:1', '_trace_dir': tmp}
            row = {'id': 'x', 'kind': 'dialogue', 'file': 'a.ks', 'source': 'Hello.'}
            context = {'_context_audit': self.audit}
            reply = {'message': {'content': '{"0":"안녕."}'}, 'done_reason': 'stop'}
            output = io.StringIO()
            with patch('request_budget.counter', return_value=FakeCounter()), \
                 patch('translation.request', return_value=reply), redirect_stdout(output):
                translate_batch([row], context, cfg)
            events = [json.loads(line) for line in (Path(tmp) / 'translation-requests.jsonl').read_text(encoding='utf-8').splitlines()]
            event = next(e for e in events if e['event'] == 'request')
            self.assertEqual(event['context_budget']['surrounding_selection'], self.selection)
            self.assertEqual(event['context_budget']['preselection_dropped'][0]['reason'], 'policy_cap')
            self.assertEqual(event['context_budget']['dropped'], [])
            self.assertNotIn('Context budget adjusted:', output.getvalue())

    def test_real_and_unknown_adjustments_still_show_message(self):
        cases = [
            ('clean_retry', {'_context_audit': {'preselection_dropped': [{'reason': 'clean_retry'}]}}, False),
            ('candidate_budget', {'_context_audit': {'preselection_dropped': [{'reason': 'candidate_budget'}]}}, False),
            ('unknown', {'_context_audit': {'preselection_dropped': [{}]}}, True),
            ('request_window', {'source_passage': [{'text': 'x' * 12000}, {'target_id': '0'}]}, True),
            ('empty', {}, False),
        ]
        for name, context, shows_message in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                cfg = {'model': 'fake', 'endpoint': 'http://localhost:1', '_trace_dir': tmp, 'num_ctx': 4096}
                row = {'id': 'x', 'kind': 'dialogue', 'file': 'a.ks', 'source': 'Hello.'}
                reply = {'message': {'content': '{"0":"안녕."}'}, 'done_reason': 'stop'}
                output = io.StringIO()
                with patch('request_budget.counter', return_value=FakeCounter()), \
                     patch('translation.request', return_value=reply), redirect_stdout(output):
                    translate_batch([row], context, cfg)
                events = [json.loads(line) for line in (Path(tmp) / 'translation-requests.jsonl').read_text(encoding='utf-8').splitlines()]
                event = next(e for e in events if e['event'] == 'request')
                message = 'Context budget adjusted: request ' + event['request_id'] + '; see data/translation-requests.jsonl\n'
                self.assertEqual(message in output.getvalue(), shows_message)
                if name == 'request_window':
                    self.assertEqual(event['context_budget']['dropped'][0]['reason'], 'request_window')
                if name == 'candidate_budget':
                    self.assertEqual(event['context_budget']['preselection_dropped'], [{'reason': 'candidate_budget'}])


if __name__ == '__main__':
    unittest.main()
