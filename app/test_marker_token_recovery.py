"""Synthetic strings only; no game, project, cache, model or network access."""
import json
import unittest
from unittest.mock import patch

from token_recovery import (prepare_renpy_source,restore_prepared_renpy_output,
                            restore_saved,has_unrestored_tool_marker,retry_details)
from translation import restore,validate_text,translate_batch


CFG={'source_language':'english','language':'korean'}


class MarkerTokenRecoveryTests(unittest.TestCase):
    def recover(self,source,output):
        clean,plan=prepare_renpy_source(source)
        return restore_prepared_renpy_output(clean,output,plan)[0]

    def test_bare_and_known_damaged_marker_spellings(self):
        for marker in ('rpt0000','RPT_0000','rtpt0000','<rpt0000/>',
                       '</rpt0000>','<brpt0000/>','[/RPT0000]',
                       'rpt0000/>','RPT_0000/>','rtpt0000]'):
            with self.subTest(marker=marker):
                self.assertEqual(self.recover('Hello [name].','안녕 '+marker+'.'),'안녕 [name].')
        self.assertEqual(self.recover('Hello [name].','rpt0000에게 인사해.'),'[name]에게 인사해.')

    def test_bare_markers_with_no_source_units_are_discarded(self):
        self.assertEqual(self.recover('Hello there.','rpt0000안녕하세요.rtpt002.'),'안녕하세요..')

    def test_ordinary_identifiers_and_rpt_words_are_prose(self):
        text='corpt0000 rpt0000suffix my_rpt0000 rpt RPT report'
        self.assertEqual(self.recover('An invented phrase.',text),text)
        literal='my_rpt_0000 rpt_0000suffix'
        self.assertEqual(self.recover(literal,literal),literal)
        self.assertEqual(validate_text(literal,literal,CFG),[])

    def test_intact_raw_units_work_without_any_wire_markers(self):
        source='{custom_effect}Hello [name], %(count)d %03d.{/custom_effect}'
        output='{custom_effect}안녕 [name], %(count)d %03d.{/custom_effect}'
        self.assertEqual(self.recover(source,output),output)
        self.assertEqual(validate_text(source,output,CFG),[])

    def test_raw_ruby_and_escape_units_keep_the_shared_lexer_structure(self):
        source='Look 〖river｜reading〗 with [[name] and {{brace}.'
        output='〖강｜발음〗과 [[name], {{brace}를 봐.'
        self.assertEqual(self.recover(source,output),output)
        self.assertEqual(validate_text(source,output,CFG),[])

    def test_raw_unit_payload_is_not_scanned_as_a_wire_marker(self):
        token='[mapping["<rpt000/> rpt0000 {w}"]]'
        self.assertEqual(self.recover('Hello '+token+'.','안녕 '+token+'.'),'안녕 '+token+'.')

    def test_mixed_raw_and_explicit_ids_reserve_each_occurrence_once(self):
        source='Tell {i}[name]{/i} and {b}[name]{/b} to wait {w}.'
        for output in ('[name]와 <rpt001/>는 <rpt002/> 기다려.',
                       '<rpt001/>와 [name]는 {w} 기다려.'):
            with self.subTest(output=output):
                expected=('{i}[name]{/i}와 {b}[name]{/b}는 {w} 기다려.'
                          if output.startswith('[name]') else
                          '{b}[name]{/b}와 {i}[name]{/i}는 {w} 기다려.')
                self.assertEqual(self.recover(source,output),expected)

    def test_repeated_identical_raw_variables_fill_distinct_ids(self):
        self.assertEqual(self.recover('[name] and [name].','[name]와 [name].'),'[name]와 [name].')

    def test_adjacent_legacy_markdown_markers_keep_the_original_parser(self):
        self.assertEqual(self.recover('[name][place]','__RPT_000____RPT_001__'),'[name][place]')

    def test_duplicate_unknown_and_missing_units_still_fail(self):
        source='Hello [name] [name] {w}.'
        for output in ('[name] <rpt000/> <rpt001/> <rpt002/>',
                       '[name] [name] [name] {w}',
                       '<rpt000/> rpt9999 <rpt002/>',
                       '[name] {w}', '[name] [name]'):
            with self.subTest(output=output),self.assertRaises(ValueError):self.recover(source,output)

    def test_nonmovable_control_order_survives_raw_and_wire_mixes(self):
        source='Wait {w=1} then {p} [name].'
        for output in ('{p} <rpt000/> [name]', '<rpt001/> {w=1} [name]'):
            with self.subTest(output=output),self.assertRaises(ValueError):self.recover(source,output)
        self.assertEqual(self.recover(source,'[name] {w=1} <rpt001/>'),'[name] {w=1} {p}')

    def test_unknown_or_duplicate_marker_without_raw_units_is_rejected(self):
        for output in ('rpt9999', 'rpt0000 rpt0000', '<rtpt000/><rpt000/>'):
            with self.subTest(output=output),self.assertRaises(ValueError):self.recover('Hello [name].',output)

    def test_incomplete_non_source_marker_is_rejected(self):
        with self.assertRaises(ValueError):self.recover('Hello [name].','안녕 <rpt000')

    def test_exact_source_marker_examples_are_preserved_and_validate(self):
        for literal in ('rpt0000','rpt0000/>','<rpt000/>','<rpt_000/>','<rpt000','</rpt000>'):
            source='The literal '+literal+' stays.'
            output='리터럴 '+literal+'는 남아.'
            with self.subTest(literal=literal):
                self.assertEqual(self.recover(source,output),output)
                self.assertEqual(validate_text(source,output,CFG),[])

    def test_source_marker_literal_gets_a_distinct_active_wire_spelling(self):
        source='Literal <rpt000/> and <rpt0000/> belong to [name].'
        _,plan=prepare_renpy_source(source)
        self.assertEqual(plan['protected_text'],
                         'Literal <rpt000/> and <rpt0000/> belong to <rpt00000/>.')
        output='<rpt00000/>에게 <rpt0000/>와 <rpt000/>가 있어.'
        expected='[name]에게 <rpt0000/>와 <rpt000/>가 있어.'
        self.assertEqual(self.recover(source,output),expected)
        self.assertEqual(validate_text(source,expected,CFG),[])

    def test_distinct_wire_spelling_is_shared_by_prompt_metadata_and_retry(self):
        source='Literal <rpt000/> belongs to [name].'
        row={'id':'synthetic','kind':'dialogue','source':source}
        cfg=dict(CFG,model='synthetic',endpoint='http://127.0.0.1:1',num_ctx=4096,
                 _names={'name':{'representative':'Alex'}})
        response={'message':{'content':json.dumps({'0':'<rpt0000/>에게 <rpt000/>가 있어.'})}}
        with patch('translation.request',return_value=response) as request, \
                patch('translation.runtime_status',return_value={}), \
                patch('gpu_policy.translation_context',side_effect=lambda context,cfg:context), \
                patch('diagnostics.stage'),patch('diagnostics.report'):
            output,_=translate_batch([row],{},cfg)
        item=json.loads(request.call_args.args[2]['messages'][1]['content'])['items'][0]
        self.assertEqual(item['text'],'Literal <rpt000/> belongs to <rpt0000/>.')
        self.assertEqual(item['movable_placeholders'],['<rpt0000/>'])
        self.assertEqual(item['name_hints'],{'<rpt0000/>':'Alex'})
        self.assertEqual(output[0]['text'],'[name]에게 <rpt000/>가 있어.')
        note=retry_details([row],{},cfg)
        self.assertIn('<rpt0000/> = [name]',note)
        self.assertNotIn('<rpt000/> = [name]',note)

    def test_collapsed_literal_and_active_wire_spelling_is_ambiguous(self):
        for literal in ('<rpt000/>','rpt0000'):
            with self.subTest(literal=literal),self.assertRaises(ValueError):
                self.recover('Literal '+literal+' belongs to [name].',literal+'와 '+literal)

    def test_literal_wire_and_raw_functional_units_can_coexist(self):
        source='Literal <rpt000/> belongs to [name].'
        output='[name]에게 <rpt000/>가 있어.'
        self.assertEqual(self.recover(source,output),output)
        self.assertEqual(validate_text(source,output,CFG),[])
        self.assertEqual(self.recover('Read [rpt000].','[rpt000]를 읽어.'),'[rpt000]를 읽어.')
        self.assertEqual(validate_text('Read [rpt000].','[rpt000]를 읽어.',CFG),[])

    def test_four_digit_functional_id_is_not_truncated(self):
        source=' '.join('[name%d]'%index for index in range(1001))
        _,plan=prepare_renpy_source(source)
        output=plan['protected_text'].replace('<rpt1000/>','rpt1000')
        self.assertEqual(self.recover(source,output),source)

    def test_saved_numbering_normalizes_before_visual_id_remapping(self):
        source='Tell {i}[name]{/i} about [place] {w}.'
        self.assertEqual(restore_saved(source,'rpt0003에게 [name]를 말해 {w}.'),
                         '[place]에게 {i}[name]{/i}를 말해 {w}.')
        with self.assertRaises(ValueError):restore_saved(source,'rpt0000 말해 rpt0002.')

    def test_saved_literals_are_hidden_before_old_ids_are_remapped(self):
        source='Literal <rpt001/> belongs to {i}[name]{/i}.'
        output='[name]에게 <rpt001/>가 있어.'
        self.assertEqual(restore_saved(source,output),'{i}[name]{/i}에게 <rpt001/>가 있어.')
        with self.assertRaises(ValueError):restore_saved(source,'<rpt001/>와 <rpt001/>')

    def test_existing_restore_checks_run_once_for_raw_and_marker_mix(self):
        with patch('translation.restore',wraps=restore) as checked:
            self.assertEqual(self.recover('Hello [name] {w}.','[name] 안녕 rpt0001.'),'[name] 안녕 {w}.')
        self.assertEqual(checked.call_count,1)
        with patch('translation.restore',wraps=restore) as checked:
            self.assertEqual(restore_saved('Hello {i}[name]{/i} {w}.','[name] 안녕 rpt0003.'),
                             '{i}[name]{/i} 안녕 {w}.')
        self.assertEqual(checked.call_count,1)

    def test_marker_free_validation_does_not_build_another_source_plan(self):
        with patch('token_recovery.prepare_renpy_source',side_effect=AssertionError('No redundant plan')):
            self.assertFalse(has_unrestored_tool_marker('Hello [name].','안녕 [name].'))


if __name__=='__main__':unittest.main()
