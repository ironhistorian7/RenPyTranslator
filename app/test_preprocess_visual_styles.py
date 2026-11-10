"""Invented pure text; no files, game, model, network or inference calls."""
import ast
import unittest

from token_recovery import (prepare_renpy_source,restore_prepared_renpy_output,
                            restore_renpy_output,restore_saved,strip_visual_styles,retry_details)
from translation import protect,validate_text


CFG={'source_language':'english','language':'korean'}


class VisualPreprocessingTests(unittest.TestCase):
    def restore(self,source,raw):
        clean,plan=prepare_renpy_source(source)
        return restore_prepared_renpy_output(clean,raw,plan)

    def test_all_supported_styles_leave_ai_input_before_protection(self):
        source=('A {i}word{/i} {b}word{/b} {u}word{/u} {s}word{/s} '
                '{color=#123456}word{/color} {size=24}word{/size} '
                '{font=Invented.ttf}word{/font} {outlinecolor=#abc}word{/outlinecolor} '
                '[name] %03d {w=0.2} {a=invented}link{/a} {image=invented_icon} {custom_effect}')
        clean,plan=prepare_renpy_source(source)
        self.assertEqual(plan['source_style_tags'],16)
        self.assertEqual(plan['functional_tokens'],['[name]','%03d','{w=0.2}',
            '{a=invented}','{/a}','{image=invented_icon}','{custom_effect}'])
        self.assertNotIn('{i}',clean);self.assertNotIn('{color=',clean)
        self.assertEqual(plan['protected_text'],protect(clean)[0])
        self.assertEqual(plan['functional_tokens'],protect(clean)[1])

    def test_context_cleanup_preserves_escaped_and_unsupported_tags(self):
        text='Literal {{i}, {{/i}, [[name]; {i}word{/i} {shake} {w=1}.'
        self.assertEqual(strip_visual_styles(text),'Literal {{i}, {{/i}, [[name]; word {shake} {w=1}.')
        self.assertEqual(prepare_renpy_source(text)[0],strip_visual_styles(text))

    def test_official_pure_display_tags_include_feature_colon_syntax(self):
        examples={'alpha':'{alpha=*0.5}','k':'{k=-.5}',
                  'plain':'{plain}','feature':'{feature:liga=0}'}
        for name,opening in examples.items():
            with self.subTest(tag=name):
                source=opening+'Please greet [name].{/'+name+'}'
                clean,plan=prepare_renpy_source(source)
                self.assertEqual(clean,'Please greet [name].')
                self.assertEqual(plan['functional_tokens'],['[name]'])
                self.assertEqual(plan['source_style_tags'],2)
                self.assertEqual(plan['protected_text'],protect(clean)[0])
                text,_=restore_prepared_renpy_output(clean,'<rpt000/>에게 인사해.',plan)
                self.assertEqual(text,opening+'[name]에게 인사해.{/'+name+'}')
                self.assertEqual(validate_text(source,text,CFG),[])

    def test_feature_argument_is_not_a_new_custom_tag_family(self):
        source='{custom:liga=0}Please greet [name].{/custom}'
        clean,plan=prepare_renpy_source(source)
        self.assertEqual(clean,source)
        self.assertEqual(plan['functional_tokens'],
                         ['{custom:liga=0}','[name]','{/custom}'])
        text,_=restore_prepared_renpy_output(clean,
                    '<rpt000/><rpt001/>에게 인사해.<rpt002/>',plan)
        self.assertEqual(text,'{custom:liga=0}[name]에게 인사해.{/custom}')

    def test_referenced_styles_and_layout_tags_remain_opaque_functional_codes(self):
        cases=[('{=invented_style}','{/}'),
               ('{shader=invented:u_value=1.0}','{/shader}'),
               ('{horiz}','{/horiz}'),('{vert}','{/vert}')]
        for opening,closing in cases:
            with self.subTest(opening=opening):
                source=opening+'Please greet [name].'+closing
                clean,plan=prepare_renpy_source(source)
                self.assertEqual(clean,source)
                self.assertEqual(plan['functional_tokens'],[opening,'[name]',closing])
                text,_=restore_prepared_renpy_output(clean,
                            '<rpt000/><rpt001/>에게 인사해.<rpt002/>',plan)
                self.assertEqual(text,opening+'[name]에게 인사해.'+closing)

    def test_accessibility_ruby_and_pacing_are_preserved_as_functional_units(self):
        tags=['{alt}','{/alt}','{noalt}','{/noalt}','{art}','{/art}',
              '{rb}','{/rb}','{rt}','{/rt}','{cps=*2}','{/cps}',
              '{space=20}','{vspace=8}','{w=0.2}','{p=0.2}',
              '{nw}','{fast}','{done}','{clear}','{#invented}']
        source='Please greet [name]. '+' '.join(tags)
        clean,plan=prepare_renpy_source(source)
        self.assertEqual(clean,source)
        self.assertEqual(plan['functional_tokens'],['[name]']+tags)
        self.assertEqual(strip_visual_styles(source),source)
        raw=plan['protected_text'].replace('Please greet ','인사해 ')
        text,_=restore_prepared_renpy_output(clean,raw,plan)
        self.assertEqual(text,source.replace('Please greet ','인사해 '))

    def test_display_like_syntax_inside_quoted_interpolation_is_not_stripped(self):
        variable='[mapping["{i}literal{/i}"]]'
        source='{alpha=.5}Please greet '+variable+'.{/alpha}'
        clean,plan=prepare_renpy_source(source)
        self.assertEqual(clean,'Please greet '+variable+'.')
        self.assertEqual(plan['functional_tokens'],[variable])
        text,_=restore_prepared_renpy_output(clean,'<rpt000/>에게 인사해.',plan)
        self.assertEqual(text,'{alpha=.5}'+variable+'에게 인사해.{/alpha}')

    def test_lenticular_ruby_keeps_structure_while_both_text_parts_are_translatable(self):
        source='{alpha=.5}Look 〖river｜reading〗 here.{/alpha}'
        clean,plan=prepare_renpy_source(source)
        self.assertEqual(clean,'Look 〖river｜reading〗 here.')
        self.assertEqual(plan['functional_tokens'],['〖','｜','〗'])
        self.assertIn('river',plan['protected_text'])
        self.assertIn('reading',plan['protected_text'])
        text,_=restore_prepared_renpy_output(clean,
                    '이곳의 <rpt000/>강<rpt001/>발음<rpt002/>을 봐.',plan)
        self.assertEqual(text,'{alpha=.5}이곳의 〖강｜발음〗을 봐.{/alpha}')
        self.assertEqual(validate_text(source,text,CFG),[])

    def test_generated_new_visual_family_is_removed_before_source_reassembly(self):
        text,changes=self.restore('{alpha=.5}Hello [name].{/alpha}',
                    '{feature:liga=0}안녕 <rpt000/>.{/feature}')
        self.assertEqual(text,'{alpha=.5}안녕 [name].{/alpha}')
        self.assertEqual(changes['raw_style_tags_removed'],2)

    def test_whole_text_styling_is_reassembled_without_ai_style_markers(self):
        source='{font=Invented.ttf}{color=#123456}Please greet [name].{/color}{/font}'
        text,_=self.restore(source,'<rpt000/>에게 인사해.')
        self.assertEqual(text,'{font=Invented.ttf}{color=#123456}[name]에게 인사해.{/color}{/font}')
        self.assertEqual(validate_text(source,text,CFG),[])

    def test_original_unclosed_whole_text_style_is_preserved(self):
        text,_=self.restore('{color=#123456}{i}Please greet [name].{/i}', '<rpt000/>에게 인사해.')
        self.assertEqual(text,'{color=#123456}{i}[name]에게 인사해.{/i}')

    def test_variable_styling_moves_with_its_exact_functional_id(self):
        source='Tell {i}[name]{/i} about {b}[place]{/b}.'
        text,_=self.restore(source,'<rpt001/>에 관해 <rpt000/>에게 알려줘.')
        self.assertEqual(text,'{b}[place]{/b}에 관해 {i}[name]{/i}에게 알려줘.')

    def test_token_only_span_decorates_each_protected_unit_without_word_alignment(self):
        source='Show {b}[first] [second]{/b} now.'
        text,_=self.restore(source,'지금 <rpt001/>와 <rpt000/>를 보여줘.')
        self.assertEqual(text,'지금 {b}[second]{/b}와 {b}[first]{/b}를 보여줘.')

    def test_unclosed_anchor_is_closed_after_the_reordered_variable(self):
        source='Hello {i}[name]'
        text,_=self.restore(source,'<rpt000/> 안녕, 오늘은 어때?')
        self.assertEqual(text,'{i}[name]{/i} 안녕, 오늘은 어때?')

    def test_ambiguous_inline_prose_loses_only_decoration(self):
        source='Please keep {i}this important phrase{/i} here.'
        clean,plan=prepare_renpy_source(source)
        self.assertEqual(clean,'Please keep this important phrase here.')
        text,changes=restore_prepared_renpy_output(clean,'중요한 문구를 여기에 남겨 주세요.',plan)
        self.assertEqual(text,'중요한 문구를 여기에 남겨 주세요.')
        self.assertEqual(changes['inline_styles_omitted'],1)
        self.assertEqual(validate_text(source,text,CFG),[])

    def test_generated_functional_tag_absent_from_source_is_removed(self):
        source='Hello [name].'
        text,_=self.restore(source,'{b}안녕 <rpt000/>.{/b}{unsupported_effect}')
        self.assertEqual(text,'안녕 [name].')
        self.assertEqual(validate_text(source,text,CFG),[])

    def test_functional_markers_are_not_optional(self):
        source='Hello {i}[name]{/i} {w=0.2}there %03d.'
        for raw in ('안녕 <rpt000/> <rpt002/>.',
                    '<rpt000/><rpt000/> <rpt001/><rpt002/> 안녕.',
                    '<rpt000/><rpt001/> 안녕.'):
            with self.subTest(raw=raw),self.assertRaises(ValueError):self.restore(source,raw)

    def test_one_rtpt_typo_restores_same_missing_functional_id(self):
        text,changes=self.restore('Hello [name].','안녕 <rtpt000/>.')
        self.assertEqual(text,'안녕 [name].')
        self.assertEqual(changes['marker_spelling_repaired'],1)

    def test_multiple_rtpt_spellings_restore_their_unchanged_functional_ids(self):
        text,changes=self.restore('Ask [name] about [place].',
                    '<rtpt000/>에게 <rtpt001/>에 관해 물어봐.')
        self.assertEqual(text,'[name]에게 [place]에 관해 물어봐.')
        self.assertEqual(changes['marker_spelling_repaired'],2)

    def test_rtpt_does_not_hide_unknown_or_duplicate_ids(self):
        cases=[('Hello [name].','안녕 <rtpt999/>.'),
               ('Hello [name].','안녕 <rpt000/><rtpt000/>.'),
               ('Hello [name].','안녕 <rtpt000/><rtpt000/>.')]
        for source,raw in cases:
            with self.subTest(raw=raw),self.assertRaises(ValueError):self.restore(source,raw)

    def test_marker_free_input_discards_invented_rpt_and_rtpt_only(self):
        text,_=self.restore('Please {i}keep this phrase{/i}.','<rpt000/>문구를 남겨 주세요.<rtpt002/>')
        self.assertEqual(text,'문구를 남겨 주세요.')
        text,_=self.restore('Please keep this phrase.','문구를 남겨 주세요.{unknown_effect}')
        self.assertEqual(text,'문구를 남겨 주세요.')

    def test_generated_engine_variables_and_angle_wrappers_keep_inner_prose(self):
        source='Please keep this phrase.'
        text,changes=self.restore(source,'{w=0.3}<b>문구를</b><span class="invented"> 남겨 주세요.</span>{image=invented}[invented_name]')
        self.assertEqual(text,'문구를 남겨 주세요.')
        self.assertEqual(changes['generated_engine_tags_removed'],3)
        self.assertEqual(changes['generated_angle_tags_removed'],4)

    def test_source_owned_custom_controls_and_variables_remain_required(self):
        source='{custom_effect}Hello [name].{/custom_effect}'
        text,_=self.restore(source,'<rpt000/>안녕 <rpt001/>.<rpt002/>{invented_effect}')
        self.assertEqual(text,'{custom_effect}안녕 [name].{/custom_effect}')
        self.assertEqual(validate_text(source,text,CFG),[])
        for raw in ('{custom_effect}안녕 <rpt001/>.{/custom_effect}',
                    '<rpt000/>안녕 [name].<rpt002/>',
                    '{custom_effect}안녕 [name].{/custom_effect}'):
            with self.subTest(raw=raw):
                text,_=self.restore(source,raw)
                self.assertEqual(text,'{custom_effect}안녕 [name].{/custom_effect}')
        for raw in ('안녕 <rpt001/>.', '{custom_effect}안녕.{/custom_effect}'):
            with self.subTest(raw=raw),self.assertRaises(ValueError):self.restore(source,raw)

    def test_literal_angle_tags_from_source_are_not_generated_cleanup_targets(self):
        source='The literal <code>example</code> stays here.'
        text,_=self.restore(source,'리터럴 <code>예시</code>는 <b>여기에</b> 남아.')
        self.assertEqual(text,'리터럴 <code>예시</code>는 여기에 남아.')

    def test_spaced_comparison_is_not_treated_as_a_generated_html_tag(self):
        source='The comparison is x < y > z.'
        text,_=self.restore(source,'비교는 x < y > z야.')
        self.assertEqual(text,'비교는 x < y > z야.')

    def test_target_id_protocol_fragments_are_never_hidden_by_generic_cleanup(self):
        for raw in ('[target_id="9"]문구를 남겨 주세요.[/target_id]',
                    '<target_id="9">문구를 남겨 주세요.</target_id>'):
            with self.subTest(raw=raw):
                text,_=self.restore('Please keep this phrase.',raw)
                self.assertEqual(text,raw)

    def test_rtpt_repair_is_not_intercepted_by_generated_angle_cleanup(self):
        text,changes=self.restore('Hello [name].','<b>안녕 <rtpt000/>.</b>')
        self.assertEqual(text,'안녕 [name].')
        self.assertEqual(changes['marker_spelling_repaired'],1)
        self.assertEqual(changes['generated_angle_tags_removed'],2)

    def test_saved_old_numbering_discards_visual_ids_and_retains_variables(self):
        source='Tell {i}[name]{/i} about [place].'
        text=restore_saved(source,'<rpt003/>에 관해 <rpt001/>에게 알려줘.')
        self.assertEqual(text,'[place]에 관해 {i}[name]{/i}에게 알려줘.')

    def test_saved_old_rtpt_id_maps_to_the_correct_clean_functional_id(self):
        source='Tell {i}[name]{/i} about [place].'
        text=restore_saved(source,'<rtpt003/>에 관해 <rpt001/>에게 알려줘.')
        self.assertEqual(text,'[place]에 관해 {i}[name]{/i}에게 알려줘.')

    def test_saved_inline_prose_marker_loss_no_longer_requires_retranslation(self):
        text,_=restore_renpy_output('Please {i}keep this phrase{/i}.','문구를 남겨 주세요.')
        self.assertEqual(text,'문구를 남겨 주세요.')

    def test_saved_missing_name_still_fails_with_unmodified_raw_response(self):
        source='Hello {i}[name]{/i}.'
        raw='<rpt000/>{b}안녕.{/b}<rpt002/>'
        with self.assertRaises(ValueError) as error:restore_saved(source,raw)
        self.assertEqual(ast.literal_eval(str(error.exception).partition('; output=')[2]),raw)

    def test_retry_details_use_clean_ids_and_never_reintroduce_source_styles(self):
        notes=retry_details([{'id':'synthetic','source':'Hello {i}[name]{/i} {w=1}.'}],{})
        self.assertIn('<rpt000/> = [name]',notes)
        self.assertIn('<rpt001/> = {w=1}',notes)
        self.assertNotIn('= {i}',notes);self.assertNotIn('= {/i}',notes)


if __name__=='__main__':unittest.main()
