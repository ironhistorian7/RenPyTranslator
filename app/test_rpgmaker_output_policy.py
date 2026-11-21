"""Synthetic output policy checks, independent of game data and models."""
import unittest

from rpgmaker_output_policy import classify, enabled, prepare_output


CFG = dict(engine='rpgmaker', source_language='ja', _rpg_retranslation_policy=True)


class OutputPolicyTests(unittest.TestCase):
    def test_scope_requires_rpgmaker_and_japanese(self):
        for cfg in ({}, dict(CFG, engine='renpy'), dict(CFG, source_language='english')):
            with self.subTest(cfg=cfg):
                self.assertFalse(enabled(cfg))
                text, details = prepare_output('例文', '안녕っ', cfg)
                self.assertEqual(text, '안녕っ')
                self.assertEqual(details['kind'], 'not_applicable')
        self.assertTrue(enabled(dict(CFG, source_language='japanese')))
        self.assertTrue(enabled(dict(engine='rpgmaker', source_language='ja')))
        self.assertTrue(enabled(dict(CFG, _rpg_retranslation_policy=False)))

    def test_small_tsu_are_removed_only_with_visible_korean(self):
        text, details = prepare_output('例文', '한국어っ 문장ッ 끝ｯ', CFG)
        self.assertEqual(text, '한국어 문장 끝')
        self.assertEqual(details['kind'], 'minor')
        self.assertEqual(details['japanese_count'], 3)
        for target in ('っ', '例文っ', ''):
            with self.subTest(target=target):
                text, details = prepare_output('例文', target, CFG)
                self.assertEqual(text, target)
                self.assertEqual(details['kind'], 'no_korean')

    def test_other_kana_or_kanji_prevents_any_removal(self):
        for target in ('한국어っ 그리고 カナ', '한국어 단어っ日本語', '한국어っあ'):
            with self.subTest(target=target):
                self.assertEqual(prepare_output('例文', target, CFG)[0], target)

    def test_reviewed_name_and_control_arguments_are_preserved(self):
        cfg = dict(CFG, _rpg_name_groups=(('ミッカ', '미카'),))
        source = r'ミッカ、例文 \N[1] \V[3]'
        target = r'ミッカ, 한국어っ \N[1] \V[3]'
        text, details = prepare_output(source, target, cfg)
        self.assertEqual(text, r'ミッカ, 한국어 \N[1] \V[3]')
        self.assertEqual(details['japanese_count'], 1)
        self.assertEqual(classify('ミッカ', 'ミッカ', cfg)['kind'], 'clean')
        self.assertEqual(prepare_output('ミッカ、例', '미카 っ', cfg)[0], '미카 ')
        code_target = r'한국어 \Plugin[っカ] 와 <tag=ッ> 내용っ'
        self.assertEqual(prepare_output(r'例 \Plugin[っカ] <tag=ッ>', code_target, CFG)[0],
                         r'한국어 \Plugin[っカ] 와 <tag=ッ> 내용')

    def test_count_run_and_proportion_limit_minor_residue(self):
        self.assertEqual(classify('例', '이것은충분히긴한국어문장あ입니다', CFG)['kind'], 'minor')
        self.assertEqual(classify('例', '짧아あ', CFG)['kind'], 'mixed')
        self.assertEqual(classify('例', '이것은충분히긴한국어문장かな입니다', CFG)['kind'], 'mixed')
        self.assertEqual(classify('例', '이것은충분히긴한국어문장あ가나い다라う입니다', CFG)['kind'], 'mixed')
        self.assertEqual(classify('例', '완전한 한국어', CFG)['kind'], 'clean')


if __name__ == '__main__':
    unittest.main()
