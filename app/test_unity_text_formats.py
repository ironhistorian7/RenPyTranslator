import unittest
from unity_text_formats import Span, extract, replace, protected_tokens, validate_replacement


class UnityTextFormatsTests(unittest.TestCase):
    def test_key_value_auto_and_placeholders(self):
        text = '# heading\nFIRST = Hello {0}, <b>{1}</b>!\\n\nSECOND = Continue\n'
        spans = extract(text)
        self.assertEqual([text[s.start:s.end] for s in spans], ['Hello {0}, <b>{1}</b>!\\n', 'Continue'])
        changed = replace(text, spans, {0: '<b>{1}</b>, 안녕 {0}!\\n', 1: '계속'})
        self.assertEqual(changed, '# heading\nFIRST = <b>{1}</b>, 안녕 {0}!\\n\nSECOND = 계속\n')

    def test_detection_and_unknown(self):
        self.assertEqual(extract('key = value\n'), [])
        self.assertEqual(extract('key = value\nunknown command\n'), [])
        self.assertEqual(extract('one,two\nabc,def\n'), [])
        self.assertEqual(extract('{"label": "hello"}'), [])
        self.assertEqual(len(extract('key = value\n', kind='key_value')), 1)

    def test_command_requires_and_checks_reference(self):
        text = ('@event\nN Hello {name}\nH happy,wave | Welcome\n'
                '  >[ready] (Wave.) Hello | heart 2\nname Alice\n'
                'role Teacher\nseen A person\nage 26\n'
                'gift_a | Flowers | 12\nwish.mode | inside\n')
        self.assertEqual(extract(text, kind='command'), [])
        spans = extract(text, reference=text, kind='command')
        self.assertEqual([s.kind for s in spans], ['dialogue', 'dialogue', 'choice', 'name', 'dialogue', 'dialogue', 'name'])
        self.assertEqual([text[s.start:s.end] for s in spans], ['Hello {name}', 'Welcome', '(Wave.) Hello', 'Alice', 'Teacher', 'A person', 'Flowers'])
        output = replace(text, spans, {2:'(손짓.) 안녕', 6:'꽃'}, reference=text, kind='command')
        self.assertIn('  >[ready] (손짓.) 안녕 | heart 2\n', output)
        self.assertIn('gift_a | 꽃 | 12\n', output)
        self.assertEqual(extract(text.replace('heart 2', 'heart 3'), reference=text, kind='command'), [])
        self.assertEqual(extract(text.replace('age 26', 'age 27'), reference=text, kind='command'), [])

    def test_numeric_only_dialogue_remains_in_its_verified_command(self):
        source='N Ten\nN 20\n'
        spans=extract(source,reference=source,kind='command')
        self.assertEqual(len(spans),1)
        self.assertEqual(replace(source,spans,{0:'10'},reference=source,kind='command'),'N 10\nN 20\n')

    def test_injection_and_token_changes_rejected(self):
        text = 'a = Hello {0} <b>world</b>\\n\nb = Next\n'
        spans = extract(text)
        for value in ['Hi {0} <b>world</b>\nnext', 'Hi {0} | x <b>world</b>\\n',
                      'Hi <b>world</b>\\n', 'Hi {0} <i>world</i>\\n',
                      'Hi {0} <b>world</b>', 'Hi {0} <b>world</b>\\n = bad',
                      'Hi {0} <b>world</b>\\n {']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                replace(text, spans, {0:value})
        with self.assertRaises(ValueError): replace(text, [Span(0, 1, 'label')], {0:'x'})
        with self.assertRaises(ValueError): replace(text, spans, {2:'x'})

    def test_rejected_rows_keep_source_and_other_rows_are_applied(self):
        text = 'a = Hello {0}\nb = Exit\n'
        spans = extract(text)
        rejected = []
        self.assertEqual(replace(text, spans, {0: '안녕', 1: '나가기'}, rejected=rejected),
                         'a = Hello {0}\nb = 나가기\n')
        self.assertEqual(rejected, [0])
        self.assertEqual([m.group() for m in protected_tokens('A {0} <size=20> 42', include_numbers=False)],
                         ['{0}', '<size=20>'])

    def test_numeric_values_and_numbers_are_preserved(self):
        text = 'speed = 12\nlabel = Level 12\n'
        spans = extract(text)
        self.assertEqual(len(spans), 1)
        self.assertEqual(replace(text, spans, {0: '레벨 13'}), 'speed = 12\nlabel = 레벨 13\n')
        self.assertEqual(replace(text, spans, {0: '레벨 12'}), 'speed = 12\nlabel = 레벨 12\n')

    def test_manual_display_changes_preserve_controls_and_boundaries(self):
        text = 'a = Level 12 (old) {0} <b>OK</b>\\n | Next 3\nb = Exit\n'
        spans = extract(text)
        value = '레벨 13 {0} <b>좋음</b>\\n | 다음 (4)'
        with self.assertRaises(ValueError):
            replace(text, spans, {0:value}, kind='key_value')
        self.assertEqual(replace(text, spans, {0:value}, kind='key_value', manual_indices={0}),
                         'a = '+value+'\nb = Exit\n')
        for bad in ('레벨 13 <b>좋음</b>\\n | 다음 (4)',
                    '레벨 13 {0} <b>좋음</b> | 다음 (4)',
                    '레벨 13 {0} <b>좋음</b>\\n | 다음 | 추가',
                    '레벨 13 {0} <b>좋음</b>\\n | ',
                    '레벨 13 {0} <b>좋음</b>\\n\n추가 | 다음',
                    '레벨 13 <b>좋음</b>\\n | {0} 다음'):
            with self.subTest(bad=bad):
                self.assertFalse(validate_replacement(text[spans[0].start:spans[0].end], bad,
                                                      kind='key_value', manual=True))
        self.assertFalse(validate_replacement('Hello', '안녕 | 추가', kind='key_value', manual=True))
        self.assertFalse(validate_replacement('(Go) 1', '가 2', kind='command', manual=True))
        self.assertTrue(validate_replacement('(Go) 1', '(가) 2', kind='command', manual=True))
        self.assertTrue(validate_replacement('Go 1', '가 2', kind='unknown', manual=True))

    def test_manual_flag_is_per_span(self):
        text = 'a = Level 1\nb = Level 2\n'
        spans = extract(text)
        self.assertEqual(replace(text, spans, {0:'레벨 3', 1:'레벨 4'}, manual_indices={0}),
                         'a = 레벨 3\nb = 레벨 4\n')
        self.assertEqual(replace(text, spans, {0:'레벨 3', 1:'레벨 4'}, manual_indices={0, 1}),
                         'a = 레벨 3\nb = 레벨 4\n')

    def test_manual_number_only_still_keeps_key_value_layout(self):
        text = 'Amount = Eight hundred\nOther = Continue\n'
        spans = extract(text)
        self.assertEqual(replace(text, spans, {0:'800'}, manual_indices={0}),
                         'Amount = 800\nOther = Continue\n')

    def test_parallel_text_tables_exclude_settings(self):
        ref = ('line.one | Hello there\nline.two | Good morning\nline.three | Come over\n'
               'config.mode | inside\n')
        local = ('line.one | 안녕\nline.two | 좋은 아침\nline.three | 이리 와\n'
                 'config.mode | inside\n')
        spans = extract(local, reference=ref, kind='command')
        self.assertEqual([local[s.start:s.end] for s in spans], ['안녕', '좋은 아침', '이리 와'])
        self.assertIn('config.mode | inside', replace(local, spans, {0:'반가워'}, reference=ref, kind='command'))
        self.assertEqual(extract('x | 하나\n', reference='x | One word\n', kind='command'), [])
        self.assertEqual(extract(local.replace('config.mode | inside', 'config.mode | outside'), reference=ref, kind='command'), [])

    def test_parallel_labels_can_match_reference_after_translation(self):
        source = 'a | First choice\nb | Second choice\nc | Third choice\n> Leave now\n'
        reference = 'a | 첫째\nb | 둘째\nc | 셋째\n> 지금 떠나\n'
        spans = extract(source, reference=reference, kind='command')
        self.assertEqual(replace(source, spans, {0:'첫째', 1:'둘째', 2:'셋째'},
                                 reference=reference, kind='command'),
                         'a | 첫째\nb | 둘째\nc | 셋째\n> Leave now\n')
        with self.assertRaisesRegex(ValueError, 'document structure'):
            replace(source, spans, {3:'[condition] 떠나'},
                    reference=reference, kind='command')

    def test_key_value_reference_skips_unknown_and_handles_equals_in_key(self):
        ref = '<size=60%>A</size> = A\nKnown = B\n'
        local = '<size=60%>A</size> = 가\nKnown = 나\nExtra = 다\n'
        spans = extract(local, reference=ref, kind='key_value')
        self.assertEqual([local[s.start:s.end] for s in spans], ['가', '나'])
        self.assertIn('Extra = 다', replace(local, spans, {0:'제목'}, reference=ref, kind='key_value'))

    def test_tokens_and_unchanged_reserved_value(self):
        self.assertEqual([m.group() for m in protected_tokens('A {0} <size=20> 42')], ['{0}', '<size=20>', '42'])
        text = 'a = A | B\nb = Continue\n'
        spans = extract(text)
        self.assertEqual(replace(text, spans, {1: '계속'}), 'a = A | B\nb = 계속\n')

    def test_companion_comments_and_display_punctuation(self):
        ref = '# ref\nN (Hello) 12!\n\nEND\n'
        local = '# local\n\nN 안녕 １２？\nEND\n# trailing\n'
        spans = extract(local, kind='command', reference=ref)
        self.assertEqual(len(spans), 1)
        self.assertEqual(replace(local, spans, {0:'잘 가 １２？'}, kind='command', reference=ref), '# local\n\nN 잘 가 １２？\nEND\n# trailing\n')

    def test_explicit_json(self):
        ref = '{"hello":"Hello", "bye":"Bye"}'
        data = '{"hello":"안녕", "bye":"잘 가"}'
        spans = extract(data, reference=ref, kind='json')
        self.assertEqual(len(spans), 2)
        self.assertEqual(replace(data, spans, {0:'반가워'}, reference=ref, kind='json'), '{"hello":"반가워", "bye":"잘 가"}')
        self.assertEqual(extract(data, kind='json'), [])
        self.assertEqual(extract(data, reference='{"wrong":"A", "bye":"B"}', kind='json'), [])
        self.assertEqual(extract('{"a": "hello\\nworld", "b": "yes"}', reference=ref, kind='json'), [])
        with self.assertRaises(ValueError): replace(data, spans, {0:'bad"value'}, reference=ref, kind='json')

    def test_explicit_csv(self):
        ref = 'key,source,translation\na,Hello,Hello\nb,Bye,Bye\n'
        data = 'key,source,translation\na,Hello,안녕\nb,Bye,잘 가\n'
        spans = extract(data, reference=ref, kind='csv')
        self.assertEqual(len(spans), 2)
        self.assertEqual(replace(data, spans, {1:'다음'}, reference=ref, kind='csv'), 'key,source,translation\na,Hello,안녕\nb,Bye,다음\n')
        self.assertEqual(extract(data.replace('a,Hello', 'a,Other'), reference=ref, kind='csv'), [])
        self.assertEqual(extract(data.replace('잘 가', '"잘 가"'), reference=ref, kind='csv'), [])
        with self.assertRaises(ValueError): replace(data, spans, {0:'안녕, 친구'}, reference=ref, kind='csv')

    def test_reference_mismatch_and_parentheses(self):
        reference = '> (Go.) Hi {0}\n'
        localized = '> (가.) 안녕 {0}\n'
        spans = extract(localized, reference=reference, kind='command')
        self.assertEqual(len(spans), 1)
        with self.assertRaises(ValueError):
            replace(localized, spans, {0:'가. 안녕 {0}'}, reference=reference, kind='command')
        self.assertEqual(extract('>[new] (가.) 안녕 {0}\n', reference=reference, kind='command'), [])


if __name__ == '__main__': unittest.main()
