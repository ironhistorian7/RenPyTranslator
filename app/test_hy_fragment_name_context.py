"""Synthetic request-body checks; no model or project access."""
import json
import unittest

from hy_backend import body


class HyFragmentNameContextTests(unittest.TestCase):
    def request(self, context, **flags):
        cfg={'model':'synthetic', 'engine':'rpgmaker', 'source_language':'japanese',
             '_rpg_correction_mode':True, '_rpg_fragment_correction':True, **flags}
        return body([{'id':'0', 'text':'안녕 ⟦RPT0⟧'}], context, cfg, {}, '')

    def test_fragment_request_retains_filtered_names_and_wire_controls(self):
        names={'Mira':'미라'}
        result=self.request({'original_source':'Mira', 'person_name_spellings':names,
                             'translation_guidance':{'large':'excluded'}})
        prompt=result['messages'][0]['content']
        background=prompt.split('[Background Information]\n',1)[1].split('\n\n[Source Text]',1)[0]
        self.assertEqual(json.loads(background), {'original_source':'Mira', 'person_name_spellings':names})
        self.assertIn('실제 인물을 가리킬 때만',prompt)
        self.assertIn('국가명이나 일반 명사에는 적용하지',prompt)
        self.assertIn('⟦RPT0⟧',prompt)
        self.assertEqual(result['options']['temperature'],0.7)
        self.assertEqual(result['options']['top_p'],0.6)
        self.assertEqual(result['options']['top_k'],20)
        self.assertEqual(result['options']['repeat_penalty'],1.05)
        self.assertEqual(result['options']['seed'],42)

    def test_missing_or_empty_names_do_not_invent_a_map_or_instruction(self):
        for context in ({}, {'person_name_spellings':{}}):
            with self.subTest(context=context):
                prompt=self.request(context)['messages'][0]['content']
                self.assertNotIn('person_name_spellings',prompt)

    def test_plain_correction_and_nonfragment_keep_normal_context(self):
        context={'person_name_spellings':{'Mira':'미라'}}
        for flags in ({'_rpg_plain_correction':True}, {'_rpg_fragment_correction':False}):
            with self.subTest(flags=flags):
                prompt=self.request(context,**flags)['messages'][0]['content']
                self.assertIn('person_name_spellings는 인명 음역표입니다',prompt)
                self.assertIn('"Mira":"미라"',prompt)
                self.assertNotIn('실제 인물을 가리킬 때만',prompt)


if __name__=='__main__':
    unittest.main()
