"""Invented in-memory text only; no games, model, cache or network access."""
import json
import ast
import unittest
from unittest.mock import patch

from failed_repair import saved_output
from token_recovery import restore_renpy_output, restore_saved, split_outer_styles
from translation import BatchTranslationError, translate_batch, validate_text


CFG = {'model': 'fake', 'language': 'korean', 'source_language': 'english',
       'endpoint': 'http://127.0.0.1:1', 'num_ctx': 4096}


class VisualStyleRecoveryTests(unittest.TestCase):
    def test_extra_raw_visual_tags_preserve_existing_inline_emphasis(self):
        source = 'He calls {i}[name]{/i} today.'
        raw = '그는 <rpt000/><rpt001/><rpt002/>를 {b}오늘{/b} 부른다.'
        text, changes = restore_renpy_output(source, raw)
        self.assertEqual(text, '그는 {i}[name]{/i}를 오늘 부른다.')
        self.assertGreater(changes.get('raw_style_tags_removed', 0), 0)
        self.assertEqual(validate_text(source, text, CFG), [])

    def test_valid_output_and_reordered_named_variables_are_unchanged(self):
        source = 'Tell {i}[name]{/i} about [place].'
        raw = '<rpt003/>에 관해 <rpt000/><rpt001/><rpt002/>에게 알려줘.'
        text, changes = restore_renpy_output(source, raw)
        self.assertEqual(text, '[place]에 관해 {i}[name]{/i}에게 알려줘.')
        self.assertFalse(changes)
        self.assertEqual(validate_text(source, text, CFG), [])

    def test_unclosed_wholeline_color_and_balanced_italic_markers_rebuild(self):
        source = '{color=#51a7c9}{i}An invented notification.{/i}'
        for raw in ('새로운 공지.', '<rpt000/><rpt000/>새로운 공지.<rpt002/>'):
            with self.subTest(raw=raw):
                text, changes = restore_renpy_output(source, raw)
                self.assertEqual(text, '{color=#51a7c9}{i}새로운 공지.{/i}')
                self.assertGreater(changes.get('outer_style_markers_rebuilt', 0), 0)
                self.assertEqual(validate_text(source, text, CFG), [])

    def test_unclosed_wholeline_styles_keep_the_name_marker(self):
        source = '{color=#a34e72}{i}Hello [name].{/i}'
        raw = '<rpt000/><rpt000/>안녕 <rpt002/>.<rpt003/>'
        text, changes = restore_renpy_output(source, raw)
        self.assertEqual(text, '{color=#a34e72}{i}안녕 [name].{/i}')
        self.assertGreater(changes.get('outer_style_markers_rebuilt', 0), 0)

    def test_different_nested_styles_and_parameters_use_source_spelling(self):
        source = '{font=InventedSans.ttf}{u}An invented reminder.{/u}{/font}'
        raw = '{color=#b49d20}{s}새로운 안내.{/s}{/color}'
        text, changes = restore_renpy_output(source, raw)
        self.assertEqual(text, '{font=InventedSans.ttf}{u}새로운 안내.{/u}{/font}')
        self.assertGreater(changes.get('raw_style_tags_removed', 0), 0)
        self.assertEqual(validate_text(source, text, CFG), [])

    def test_named_variable_omission_or_duplication_is_not_repaired(self):
        source = '{color=#a34e72}{i}Hello [name].{/i}'
        for raw in ('안녕.', '<rpt002/><rpt002/> 안녕.'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                restore_renpy_output(source, raw)

    def test_functional_variable_and_percent_marker_damage_is_rejected(self):
        cases = [
            ('Hello [name] {w=0.2}there %03d.', '안녕 <rpt000/> <rpt002/>.'),
            ('Hello [name] {w=0.2}there %03d.', '<rpt000/><rpt000/> <rpt001/> <rpt002/> 안녕.'),
            ('Hello [name] {w=0.2}there %03d.', '<rpt000/> <rpt001/> 안녕.'),
            ('Open {a=fictional_target}[name]{/a}.', '열기 <rpt001/><rpt002/>.'),
            ('Show {image=invented_icon} now.', '지금 표시해.'),
        ]
        for source, raw in cases:
            with self.subTest(source=source, raw=raw), self.assertRaises(ValueError):
                restore_renpy_output(source, raw)

    def test_unknown_marker_is_not_mistaken_for_a_visual_style(self):
        source = 'Please {i}[name]{/i}, come here.'
        raw = '<rpt000/><rpt001/><rpt002/> 여기로 와.<rpt999/>'
        with self.assertRaises(ValueError):
            restore_renpy_output(source, raw)

    def test_misspelled_known_wholeline_style_ids_are_removed_and_source_style_restored(self):
        source = '{color=#92a8cd}{i}An invented notice.{/i}'
        for fragment in ('</rpt002>', '<brpt002/>', '[/RPT002]'):
            with self.subTest(fragment=fragment):
                text,changes=restore_renpy_output(source, '<rpt000/><rpt001/>새 안내.' + fragment)
                self.assertEqual(text,'{color=#92a8cd}{i}새 안내.{/i}')
                self.assertGreater(changes.get('outer_style_markers_rebuilt',0),0)
                self.assertEqual(validate_text(source,text,CFG),[])

    def test_malformed_unknown_and_functional_ids_are_not_guessed(self):
        cases=[('{color=#92a8cd}{i}Notice [name].{/i}', '<rpt000/><rpt001/><rpt002/>새 안내.</rpt999>'),
               ('{color=#92a8cd}Hello [name].', '<rpt000/>안녕 </rpt001>.'),
               ('Please {i}[name]{/i}.', '<rpt000/>와요.</rpt001>')]
        for source,raw in cases:
            with self.subTest(source=source),self.assertRaises(ValueError):
                restore_renpy_output(source,raw)

    def test_missing_inline_markers_rebuild_only_the_exact_variable_anchor(self):
        source = 'Tell {i}[name]{/i} the news.'
        for raw in ('<rpt001/>에게 소식을 알려줘.',
                    '<rpt000/><rpt001/>에게 소식을 알려줘.'):
            with self.subTest(raw=raw):
                text,_=restore_renpy_output(source,raw)
                self.assertEqual(text,'{i}[name]{/i}에게 소식을 알려줘.')

    def test_escaped_braces_and_brackets_survive_raw_style_cleanup(self):
        source = 'Literal {{i}, {{/i}, [[name], then {b}a word{/b}.'
        raw = ('문자 <rpt000/>i}, <rpt001/>/i}, <rpt002/>name], '
               '그리고 <rpt003/>단어<rpt004/> {u}표시{/u}.')
        text, changes = restore_renpy_output(source, raw)
        self.assertEqual(text, '문자 {{i}, {{/i}, [[name], 그리고 단어 표시.')
        self.assertGreater(changes.get('raw_style_tags_removed', 0), 0)
        self.assertEqual(validate_text(source, text, CFG), [])

    def test_literal_raw_style_spelling_is_not_removed_as_a_real_tag(self):
        source = 'Literal {{i} and {{/i} with [[name].'
        raw = '문자 <rpt000/>i}와 <rpt001/>/i}, <rpt002/>name].'
        text, changes = restore_renpy_output(source, raw)
        self.assertEqual(text, '문자 {{i}와 {{/i}, [[name].')
        self.assertFalse(changes)
        self.assertEqual(validate_text(source, text, CFG), [])

    def test_escape_then_real_style_and_split_default_are_distinct(self):
        source = '{{{i}An invented phrase.{/i}'
        raw = '<rpt000/><rpt001/>새 문구.<rpt002/>'
        text, changes = restore_renpy_output(source, raw)
        self.assertEqual(text, '{{새 문구.')
        self.assertGreater(changes.get('inline_styles_omitted',0),0)
        self.assertEqual(validate_text(source, text, CFG), [])
        unclosed = '{color=#51a7c9}{i}An invented phrase.{/i}'
        self.assertEqual(split_outer_styles(unclosed), ('', unclosed, ''))
        self.assertEqual(split_outer_styles(unclosed, allow_unclosed=True),
                         ('{color=#51a7c9}{i}', 'An invented phrase.', '{/i}'))

    def test_saved_full_numbered_output_recovers_without_renumbering_names(self):
        source = '{i}Please greet [name].{/i}'
        raw = '<rpt000/>안녕 <rpt001/>.<rpt002/>'
        self.assertEqual(restore_saved(source, raw), '{i}안녕 [name].{/i}')
        stripped = '<rpt001/> 안녕.'
        self.assertEqual(restore_saved(source, stripped), '{i}[name] 안녕.{/i}')

    def test_saved_output_preserves_wrapper_stripped_numbering(self):
        source = '{b}Please greet [name].{/b}'
        raw = '<rpt000/>에게 {u}인사해{/u}.'
        error = 'Wrapper-stripped: markup mismatch; output=' + repr(raw)
        text = saved_output(source, error, CFG)
        self.assertEqual(text, '{b}[name]에게 인사해.{/b}')
        self.assertEqual(validate_text(source, text, CFG), [])


class LiveVisualStyleRoutingTests(unittest.TestCase):
    def run_batch(self, source, raw, cfg=None):
        row = {'id': 'synthetic', 'kind': 'dialogue', 'source': source}
        response = {'message': {'content': json.dumps({'0': raw}, ensure_ascii=False)}}
        with patch('translation.request', return_value=response), \
                patch('translation.runtime_status', return_value={}), \
                patch('diagnostics.stage'), patch('diagnostics.report'):
            return translate_batch([row], {}, cfg or CFG)

    def test_live_renpy_removes_extra_raw_style_and_keeps_the_variable(self):
        source = 'Hello [name].'
        output, _ = self.run_batch(source, '{i}안녕 <rpt000/>.{/i}')
        self.assertEqual(output[0]['text'], '안녕 [name].')

    def test_live_style_repair_records_counts_without_including_dialogue(self):
        source = '{color=#23a6bb}{i}An invented notice.{/i}'
        raw = '<rpt000/><rpt000/>{b}새 안내.{/b}<rpt002/>'
        with patch('translation.trace_event') as trace:
            output, _ = self.run_batch(source, raw)
        self.assertEqual(output[0]['text'], '{color=#23a6bb}{i}새 안내.{/i}')
        repairs = [call.kwargs for call in trace.call_args_list
                   if call.args[2] == 'renpy_style_repair']
        self.assertEqual(len(repairs),1)
        self.assertEqual(repairs[0]['id'],'synthetic')
        self.assertEqual(repairs[0]['raw_style_tags_removed'],2)

    def test_unrecoverable_output_keeps_raw_response_for_later_diagnostics(self):
        raw = '{i}안녕.{/i}'
        with self.assertRaises(BatchTranslationError) as caught:
            self.run_batch('Hello [name].', raw)
        error = caught.exception.failures[0]['error']
        self.assertIn('Protected token mismatch', error)
        self.assertEqual(ast.literal_eval(error.partition('; output=')[2]), raw)

    def test_live_generated_functional_tags_are_removed_without_losing_prose(self):
        for tag in ('{w=0.3}', '{image=invented_icon}', '{unknown_effect}',
                    '{a=fictional_target}안내{/a}'):
            with self.subTest(tag=tag):
                result,_=self.run_batch('An invented instruction.','새로운 안내.'+tag)
                expected='새로운 안내.안내' if tag.startswith('{a=') else '새로운 안내.'
                self.assertEqual(result[0]['text'],expected)

    def test_rpgmaker_does_not_enter_the_renpy_visual_repair_path(self):
        cfg = dict(CFG, engine='rpgmaker')
        with patch('token_recovery.restore_renpy_output',
                   side_effect=AssertionError('RenPy repair must not process RPG Maker')):
            output, _ = self.run_batch(r'Hello \N[1].', '안녕 <rpt000/>.', cfg)
        self.assertEqual(output[0]['text'], r'안녕 \N[1].')


if __name__ == '__main__':
    unittest.main()
