"""Invented protocol strings only; no game, cache or model execution."""
import unittest

from rpgmaker_protocol import (align_display_anchors, apply_corrections,
                               correction_draft, correction_ending, correction_parts,
                               correction_plan, parse, retry_instruction)


class ProtocolBoundaryTests(unittest.TestCase):
    def test_confirmed_names_are_not_selected_as_correction_fragments(self):
        source='架空名は挨拶した。'
        draft='架空名 안녕ッ 끝'
        cfg={'engine':'rpgmaker','source_language':'japanese',
             '_rpg_name_groups':(('架空名','미라'),)}
        words,positions=correction_parts(draft,source=source,cfg=cfg)
        self.assertEqual(words,['안녕ッ'])
        self.assertEqual(draft[positions[0][0]:positions[0][1]],'안녕ッ')
        self.assertEqual(apply_corrections(draft,positions,{'0':'안녕'}),'架空名 안녕 끝')
        error="['untranslated Japanese text']; output="+repr(draft)
        self.assertEqual(correction_plan(source,error,cfg)[1][0],['안녕ッ'])

    def test_a_korean_name_is_not_evidence_that_the_body_was_translated(self):
        source='架空名は挨拶した。'
        candidate='미라 おはよう'
        cfg={'engine':'rpgmaker','source_language':'japanese',
             '_rpg_name_groups':(('架空名','미라'),)}
        error="['untranslated Japanese text']; output="+repr(candidate)
        self.assertIsNone(correction_draft(source,error,cfg))
        note=retry_instruction([{'id':'x','source':source}],{'x':error},cfg)
        self.assertNotIn(candidate,note)
        self.assertNotIn('교정할 이전 번역:',note)
        self.assertIn('원문 전체',note)

    def test_untranslated_responses_are_not_reinjected_as_korean_drafts(self):
        cfg={'engine':'rpgmaker','source_language':'japanese'}
        row={'id':'x','source':'おはよう'}
        for candidate in ('おはよう','挨拶'):
            with self.subTest(candidate=candidate):
                note=retry_instruction([row],{'x':"['untranslated Japanese text']; output="+repr(candidate)},cfg)
                self.assertNotIn(candidate,note)
                self.assertNotIn('교정할 이전 번역:',note)
                self.assertIn('원문 전체',note)
                self.assertNotIn('[ID]',note)

    def test_natural_korean_fragment_characters_are_not_an_ascii_whitelist(self):
        draft='앞 안녕ッ 뒤'
        _,positions=correction_parts(draft)
        for target in ('“안녕”','안녕(친구)','가나/다라','A+B 좋아요','50% 증가',
                       'ㅋㅋ 좋아요','가나, 다라: 좋아요'):
            with self.subTest(target=target):
                self.assertEqual(apply_corrections(draft,positions,{'0':target}),
                                 '앞 '+target+' 뒤')
        for target in ('','안녕\n새 줄',None):
            with self.subTest(target=target),self.assertRaises(ValueError):
                apply_corrections(draft,positions,{'0':target})

    def test_segment_header_alias_matches_the_actual_requested_id(self):
        self.assertEqual(parse('[번역_3] 합성 응답',1,expected_ids={'3'}),({'3':'합성 응답'},True))
        self.assertEqual(parse('[번역_0] 다른 번호',1,expected_ids={'3'}),({},False))

    def test_equivalent_wave_endings_restore_the_original_bytes(self):
        for original in ('\u301c', '\uff5e', '~'):
            for emitted in ('\u301c', '\uff5e', '~', '~~~'):
                with self.subTest(original=original, emitted=emitted):
                    self.assertEqual(correction_ending('안녕ッ'+original, '안녕'+emitted),
                                     '안녕'+original)

    def test_wave_normalization_splices_only_the_selected_fragment(self):
        draft='앞 안녕ッ\u301c<rpt000/> 뒤.'
        _,positions=correction_parts(draft)
        self.assertEqual(apply_corrections(draft,positions,{'0':'안녕~'}),
                         '앞 안녕\u301c<rpt000/> 뒤.')
        restored,placements=align_display_anchors('가ッ<rpt000/>나\u301c','가나~')
        self.assertEqual(restored,'가<rpt000/>나\u301c')
        self.assertEqual(placements[0]['target_offset'],1)

    def test_different_or_new_punctuation_is_still_rejected(self):
        for original,target in (('안녕ッ\u301c','안녕?'),('안녕ッ','안녕~')):
            with self.subTest(original=original),self.assertRaises(ValueError):
                correction_ending(original,target)
        draft='앞 안녕ッ\u301c 뒤.'
        _,positions=correction_parts(draft)
        for target in ('안녕 → 설명~','안녕~ 새 설명'):
            with self.subTest(target=target),self.assertRaises(ValueError):
                apply_corrections(draft,positions,{'0':target})

    def test_unicode_prefixes_need_the_same_explicit_zero_header(self):
        for prefix in ('nr','번역','翻訳','réponse','응답_항목'):
            with self.subTest(prefix=prefix):
                self.assertEqual(parse('['+prefix+'_0] 안녕',1),({'0':'안녕'},True))
        self.assertEqual(parse('[0] 안녕',1),({'0':'안녕'},False))

    def test_no_prefix_number_or_record_boundary_is_guessed(self):
        cases=[('[번역_3] 안녕',1),('[번역0] 안녕',1),
               ('설명\n[번역_0] 안녕',1),('[번역_0] 안녕',2),
               ('[번역_0] 안녕\n[번역_0] 다시',1),
               ('[번역_0] 안녕\n[0] 다시',1),
               ('[0] 안녕\n[번역_0] 다시',1),
               ('[번역_0] 안녕\n[번역_1] 다시',1),
               ('[번역→_0] 안녕',1)]
        for text,count in cases:
            with self.subTest(text=text,count=count):
                self.assertEqual(parse(text,count),({},False))


if __name__=='__main__':unittest.main()
