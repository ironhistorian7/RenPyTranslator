import unittest
import numbered_protocol as protocol


class NumberedProtocolTests(unittest.TestCase):
    def test_single_known_numeric_prefix_alias_only(self):
        self.assertEqual(protocol.parse('[nr_0] 안녕',{'0':'hello'}),{'0':'안녕'})
        for value in ('[nr_1] 안녕','[nr_0] 하나\n[nr_0] 둘'):
            self.assertEqual(protocol.parse(value,{'0':'hello'}),{})
        self.assertEqual(protocol.parse('[nr_0] 안녕',{'0':'[nr_0] literal'}),{})
    def test_multiline_partial_and_exact_ids(self):
        self.assertEqual(protocol.parse('[0] 하나\n둘\n[2] 셋', {'0': 'a', '1': 'b', '2': 'c'}),
                         {'0': '하나\n둘', '2': '셋'})

    def test_duplicates_and_unexpected_are_not_guessed(self):
        self.assertEqual(protocol.parse('[0] a\n[0] b\n[1] c\n[9] d\n[2] e',
                                        {'0': 'a', '1': 'b', '2': 'c'}), {'2': 'e'})

    def test_source_heading_collision_does_not_misassign(self):
        self.assertEqual(protocol.parse('[0] 앞\n[1] 제목\n[2] 끝',
                                        {'0': 'a\n[1] heading', '1': 'b', '2': 'c'}), {'2': '끝'})
        self.assertEqual(protocol.parse('[0] 앞\n[9] 제목\n[1] 뒤',
                                        {'0': 'a\n[9] heading', '1': 'b'}), {'1': '뒤'})

    def test_plain_fallback_is_explicit_and_conservative(self):
        self.assertEqual(protocol.parse('안녕 <custom>', {'4': 'hello'}, allow_headerless=True), {'4': '안녕 <custom>'})
        for value in ('[5] wrong', 'one\ntwo', '```x```', '[target_id=4]x[/target_id]'):
            self.assertEqual(protocol.parse(value, {'4': 'hello'}, allow_headerless=True), {})
        self.assertEqual(protocol.parse('hello', {'4': 'hello'}), {})

    def test_json_is_strict_and_keeps_response_order(self):
        sources = {'0': 'a', '1': 'b', '2': 'c'}
        parsed = protocol.parse('{"2":"둘", "0":"영"}', sources)
        self.assertEqual(list(parsed.items()), [('2', '둘'), ('0', '영')])
        for value in ('{"0":"a","0":"b"}', '{"0":"a","9":"b"}',
                      '{"0":5}', '{"0":"a"} trailing', '{"0":NaN}'):
            self.assertEqual(protocol.parse(value, sources), {})

    def test_source_provenance_wrapper_cleanup(self):
        source = {'0': 'hello', '1': 'bye', '2': 'literal [target_id="2"]'}
        text = ('[0] [target_id="0"]안녕[/target_id]\n'
                '[1] <target_id="9">잘못</target_id>\n'
                '[2] [target_id="2"]유지[/target_id]')
        self.assertEqual(protocol.parse(text, source),
                         {'0': '안녕', '2': '[target_id="2"]유지[/target_id]'})
        self.assertEqual(protocol.parse('{"0":"<target_id=0>안녕</target_id>"}', source), {'0':'안녕'})
        self.assertEqual(protocol.parse('[0] <custom>유지</custom>', source), {'0':'<custom>유지</custom>'})

    def test_truncated_discards_actual_last_header(self):
        sources = {'0': 'a', '1': 'b', '2': 'c'}
        self.assertEqual(protocol.parse('[2] 둘\n[0] 영\n[1] 하나', sources, truncated=True), {'2':'둘','0':'영'})
        self.assertEqual(protocol.parse('[0] 영\n[9] 잘림', sources, truncated=True), {})
        self.assertEqual(protocol.parse('{"0":"영"}', sources, truncated=True), {})
        self.assertEqual(protocol.parse('영', {'0':'a'}, allow_headerless=True, truncated=True), {})

    def test_unexpected_preamble_and_empty_item(self):
        self.assertEqual(protocol.parse('Output:\n[0] a\n[1] \n[2] c', {'0':'a','1':'b','2':'c'}), {'2':'c'})


if __name__ == '__main__':
    unittest.main()
