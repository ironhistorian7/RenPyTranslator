import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from label_policy import expansion_errors
from unity_output import parse_single_hy, saved_attempts


class UnityOutputTests(unittest.TestCase):
    def test_single_unlabelled_response_preserves_text(self):
        self.assertEqual(parse_single_hy('번역된 문장입니다.', {'3': 'A sentence'}),
                         {'3': '번역된 문장입니다.'})
        self.assertEqual(parse_single_hy('<rpt000/>안녕\n세상', {'0': 'Hi\nworld'}),
                         {'0': '<rpt000/>안녕\n세상'})

    def test_ambiguous_and_truncated_responses_are_not_assigned(self):
        for content in ('[0] 하나', '[wrong] 하나', '번역: 하나', '하나\n둘',
                        '1. 하나', '```\n하나\n```', '{"0":"하나"}',
                        '<target_id=0>하나</target_id>'):
            with self.subTest(content=content):
                self.assertEqual(parse_single_hy(content, {'0': 'one'}), {})
        self.assertEqual(parse_single_hy('하나', {'0': 'one', '1': 'two'}), {})
        self.assertEqual(parse_single_hy('하나', {'0': 'one'}, truncated=True), {})

    def test_unity_ui_single_sentence_is_not_treated_as_expansion(self):
        row = {'source': 'Look', 'usage': 'ui_label'}
        cfg = {'engine': 'unity'}
        self.assertEqual(expansion_errors(row, '준비되었습니다.', cfg), [])
        for text in ('준비되었습니다. 다시 준비되었습니다.', '“준비되었습니다.”', '준비되었습니다.\n다시 갑니다.'):
            self.assertTrue(expansion_errors(row, text, cfg))
        self.assertTrue(expansion_errors(row, '준비되었습니다.', {'engine': 'renpy'}))
        self.assertTrue(expansion_errors(dict(row, usage='speaker_label'), '준비되었습니다.', cfg))

    def test_saved_trace_requires_matching_numbered_single_and_finished_reply(self):
        def attempt(request_id, item_id, *, finish='stop', input_count=1):
            failure = {'id': item_id, 'error_code': 'missing_response_item', 'expected_id': '0'}
            return [dict(event='request', items=[{'id': item_id}],
                         request_profile={'response_contract': 'hy_numbered', 'input_count': input_count}),
                    dict(event='response', response={'done_reason': finish, 'message': {'content': '준비됐습니다.'}}),
                    dict(event='validation', failures=[failure])]

        traces = {1: attempt(1, 'valid'), 2: attempt(2, 'cut', finish='length'),
                  3: attempt(3, 'batch', input_count=2)}
        rows = {uid: {'source': 'Ready'} for uid in ('valid', 'cut', 'batch')}
        with TemporaryDirectory() as temp:
            project = Path(temp)
            (project/'data').mkdir()
            events = [dict(record, source_language='english', request_id=str(key))
                      for key, records in traces.items() for record in records]
            (project/'data/translation-requests.jsonl').write_text(
                '\n'.join(json.dumps(record) for record in events), encoding='utf-8')
            found = saved_attempts(project, rows, {'source_language': 'english'})
        self.assertEqual(found, {'valid': (("Unity single response; output='준비됐습니다.'",None),)})


if __name__ == '__main__':
    unittest.main()
