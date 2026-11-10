"""Invented protocol strings only; no game, cache or model execution."""
import unittest

from rpgmaker_protocol import (align_display_anchors, apply_corrections,
                               correction_ending, correction_parts, parse)


class ProtocolBoundaryTests(unittest.TestCase):
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
