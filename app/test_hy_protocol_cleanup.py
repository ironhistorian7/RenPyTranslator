"""Invented protocol strings only; no model, network, game or project access."""
import unittest

from hy_backend import body, parse


class HyProtocolCleanupTests(unittest.TestCase):
    def test_matching_whole_envelopes_are_removed_per_numeric_item(self):
        source={'0':'An invented greeting.','1':'An invented farewell.','2':'An invented label.'}
        content=('[0] [target_id="0"]안녕.[/target_id]\n'
                 "[1] [target_id='1']잘 가.[/target_id]\n"
                 '[2] [target_id=2]안내[/target_id]')
        self.assertEqual(parse(content,source),{'0':'안녕.','1':'잘 가.','2':'안내'})

    def test_spaces_and_multiline_body_do_not_lose_label_text(self):
        self.assertEqual(parse('[0] [target_id = "0"]\n  첫째 줄\n둘째 줄  \n[/target_id]',
                               {'0':'Two invented lines.'}),{'0':'첫째 줄\n둘째 줄'})
        self.assertEqual(parse('[0] [target_id="0"]미라[/target_id]',{'0':'Mira'}),{'0':'미라'})

    def test_plain_output_and_legacy_calls_are_unchanged(self):
        wrapped='[0] [target_id="0"]안녕[/target_id]'
        self.assertEqual(parse(wrapped),{'0':'[target_id="0"]안녕[/target_id]'})
        angle='[0] <target_id="0">안녕</target_id>'
        self.assertEqual(parse(angle),{'0':'<target_id="0">안녕</target_id>'})
        self.assertEqual(parse('[0] 안녕\n[1] 잘 가',{'0':'Hello.','1':'Goodbye.'}),
                         {'0':'안녕','1':'잘 가'})

    def test_duplicate_headers_keep_the_existing_discard_behavior(self):
        text=('[0] [target_id="0"]첫 출력[/target_id]\n'
              '[0] [target_id="0"]중복 출력[/target_id]\n'
              '[1] [target_id="1"]다른 항목[/target_id]')
        self.assertEqual(parse(text,{'0':'A greeting.','1':'A label.'}),{'1':'다른 항목'})

    def test_wrong_unknown_and_nonexact_ids_are_discarded(self):
        for key,wrapper,source in (('0','1',{'0':'A greeting.'}),
                                   ('9','9',{'0':'A greeting.'}),
                                   ('0','00',{'0':'A greeting.'})):
            value=f'[target_id="{wrapper}"]안녕[/target_id]'
            with self.subTest(key=key,wrapper=wrapper):
                self.assertEqual(parse(f'[{key}] '+value,source),{})

    def test_partial_extra_attribute_and_nested_wrappers_are_discarded(self):
        values=('[target_id="0"]안녕',
                '앞말 [target_id="0"]안녕[/target_id]',
                '[target_id="0"]안녕[/target_id] 뒷말',
                '[target_id="0" other="x"]안녕[/target_id]',
                '[target_id="0"][target_id="0"]안녕[/target_id][/target_id]',
                '[target_id="0"]안녕[/target_id][target_id="0"]다시[/target_id]')
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(parse('[0] '+value,{'0':'An invented greeting.'}),{})

    def test_angle_envelopes_use_the_same_numeric_id_proof(self):
        source={'0':'An invented greeting.','1':'An invented farewell.','2':'An invented label.'}
        content=('[0] <target_id="0">안녕.</target_id>\n'
                 "[1] <target_id='1'>잘 가.</target_id>\n"
                 '[2] <target_id=2>안내</target_id>')
        self.assertEqual(parse(content,source),{'0':'안녕.','1':'잘 가.','2':'안내'})
        self.assertEqual(parse('[0] <target_id="1">안녕</target_id>',source),{})

    def test_angle_mixed_nested_and_incomplete_protocol_rows_are_discarded(self):
        values=('<target_id="0">안녕', '<target_id', '[target_id',
                '<target_id="0">안녕[/target_id]',
                '[target_id="0"]안녕</target_id>',
                '<target_id="0">[target_id="0"]안녕[/target_id]</target_id>',
                '[target_id="0"]<target_id="0">안녕</target_id>[/target_id]',
                '<target_id="0">안녕</target_id><target_id="0">다시</target_id>',
                '<target_id>안녕</target_id>')
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(parse('[0] '+value,{'0':'An invented greeting.'}),{})

    def test_one_bad_protocol_row_does_not_discard_good_siblings(self):
        content=('[0] <target_id="9">오류</target_id>\n'
                 '[1] [target_id="1"]정상[/target_id]\n[2] 일반 출력')
        source={'0':'An invented bad row.','1':'An invented good row.','2':'A plain row.'}
        self.assertEqual(parse(content,source),{'1':'정상','2':'일반 출력'})
        duplicate='[0] <target_id="9">오류</target_id>\n[0] 정상인 척'
        self.assertEqual(parse(duplicate,{'0':'An invented row.'}),{})

    def test_original_target_id_markup_and_escaped_examples_are_never_removed(self):
        sources=('[target_id="0"]An invented literal[/target_id]',
                 'Show [[target_id="0"] as code.',
                 'An example [/target_id] closing tag.',
                 '<target_id="0">An invented literal</target_id>',
                 'An example <target_id> literal tag.')
        value='[target_id="0"]번역문[/target_id]'
        for source in sources:
            with self.subTest(source=source):
                self.assertEqual(parse('[0] '+value,{'0':source}),{'0':value})
        literal='<target_id="0">번역문</target_id>'
        self.assertEqual(parse('[0] '+literal,{'0':'Show <target_id> as literal code.'}),{'0':literal})

    def test_normal_renpy_variables_and_inline_tags_survive(self):
        value='[target_id="0"]{i}안녕 [name], [target_id]{/i} <rpt000/>[/target_id]'
        self.assertEqual(parse('[0] '+value,{'0':'Hello [name], [target_id].'}),
                         {'0':'{i}안녕 [name], [target_id]{/i} <rpt000/>'})
        for conditional in ('[target_id == "0"]', '[target_id != "0"]', '[target_id + offset]'):
            with self.subTest(expression=conditional):
                self.assertEqual(parse('[0] [target_id="0"]'+conditional+'[/target_id]',{'0':conditional}),
                                 {'0':conditional})

    def test_empty_cleaned_body_is_left_for_the_existing_empty_output_check(self):
        self.assertEqual(parse('[0] [target_id="0"]  [/target_id]',{'0':'An invented instruction.'}),
                         {'0':''})


class HyPreparedInputInstructionTests(unittest.TestCase):
    def prompt(self, text, **cfg):
        return body([{'id':'0','text':text,'kind':'dialogue','usage':'dialogue'}],{},
                    dict(model='invented-hy',**cfg),{},'')['messages'][0]['content']

    def test_clean_renpy_input_does_not_request_absent_markers(self):
        prompt=self.prompt('An invented plain sentence.')
        self.assertIn('시각 서식은 프로그램이 처리합니다',prompt)
        self.assertIn('서식 태그를 추가하지 마세요',prompt)
        self.assertIn('target_id 같은 문맥 메타데이터는 출력하지 마세요',prompt)
        self.assertNotIn('표시만 정확히 한 번씩 보존',prompt)
        self.assertNotIn('<rpt000/>',prompt)

    def test_remaining_renpy_functional_marker_keeps_one_preservation_instruction(self):
        prompt=self.prompt('An invented <rpt000/> value.')
        self.assertEqual(prompt.count('표시만 정확히 한 번씩 보존'),1)
        self.assertIn('그 외 기능·제어 표시끼리의 순서는 유지',prompt)
        self.assertIn('시각 서식은 프로그램이 처리합니다',prompt)
        self.assertNotIn('서식·제어 표시끼리',prompt)

    def test_rpg_marker_protocol_does_not_receive_renpy_visual_policy(self):
        prompt=self.prompt('An invented <rpt000/> value.',engine='rpgmaker')
        self.assertNotIn('시각 서식은 프로그램이 처리합니다',prompt)
        self.assertIn('⟦RPT숫자⟧는 번역하지 않는 위치 표시',prompt)
        self.assertIn('⟦RPT000⟧',prompt)
        self.assertNotIn('<rpt000/>',prompt)


if __name__=='__main__':
    unittest.main()
