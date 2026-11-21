"""Synthetic request and parser contracts for Japanese RPG translations."""
import unittest

from hy_backend import body
from label_policy import expansion_errors
from rpgmaker_protocol import parse, japanese_instruction


CFG = {'engine':'rpgmaker', 'source_language':'japanese', 'model':'synthetic'}


class RetranslationContractTests(unittest.TestCase):
    def test_prompt_gating_and_clean_override(self):
        inputs=[{'id':'0','text':'あっ<rpt000/>','usage':'dialogue'}]
        def prompt(cfg):return body(inputs,{},cfg,[], '')['messages'][0]['content']
        text=prompt(dict(CFG,_rpg_clean_retry=True,_rpg_retry_kind='mixed',_rpg_retry_attempt=1))
        self.assertIn('의성어',text)
        self.assertIn('섞여 있는 일본어',text)
        self.assertIn('⟦RPT000⟧',text)
        self.assertIn('의성어',prompt(CFG))
        self.assertNotIn('의성어',prompt(dict(CFG,source_language='english')))
        self.assertIn('의성어',prompt(dict(CFG,engine='renpy')))
        self.assertNotIn('이전 문맥의 한국어',prompt(dict(CFG,engine='renpy')))

    def test_retry_modes_and_attempts_are_distinct(self):
        values={japanese_instruction(dict(CFG,_rpg_retry_kind=kind,_rpg_retry_attempt=attempt))
                for kind in ('minor','no_korean','mixed') for attempt in (1,2)}
        self.assertEqual(len(values),6)
        self.assertEqual(japanese_instruction(dict(CFG,source_language='english')),'')

    def test_headerless_is_narrow_and_opt_in(self):
        self.assertEqual(parse('안녕하세요 ⟦RPT000⟧',1,expected_ids={'7'},allow_headerless=True),
                         ({'7':'안녕하세요 ⟦RPT000⟧'},True))
        for text in ('[1] 하나\n[2] 둘','[1] 하나\n[1] 둘','[bad] 하나',
                     '{"0":"하나"}','번역: 하나','하나\n둘','<target_id=7>하나</target_id>'):
            self.assertEqual(parse(text,1,expected_ids={'7'},allow_headerless=True)[0],{})
        self.assertEqual(parse('안녕하세요',1,expected_ids={'7'})[0],{})
        self.assertEqual(parse('안녕하세요',2,expected_ids={'7','8'},allow_headerless=True)[0],{})

    def test_ui_sentence_does_not_relax_speaker_or_other_engines(self):
        row={'source':'設定','usage':'ui_label'}
        self.assertEqual(expansion_errors(row,'설정입니다.',CFG),[])
        self.assertTrue(expansion_errors(row,'설정입니다.'))
        self.assertTrue(expansion_errors(dict(row,usage='speaker_label'),'설정입니다.',CFG))
        self.assertTrue(expansion_errors(row,'설정입니다.',dict(CFG,engine='renpy')))
        self.assertTrue(expansion_errors(row,'설정\n설명',CFG))


if __name__=='__main__':unittest.main()
