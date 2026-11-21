"""Synthetic name, surrounding-language, cache and request-boundary regressions."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine import save_json
from rpgmaker_names import validation_config,mask_names
from rpgmaker_residue import needs_repair
from translation import validate_text,translate_batch,cache,fingerprint


CFG=dict(engine='rpgmaker',source_language='japanese',language='korean',model='synthetic',
         endpoint='http://unused.invalid',num_ctx=16384,parallel=2,batch_size=8)


def actor(name='銀崎玲奈'):
    return dict(id='actor',source=name,file='data/Actors.json',kind='string',usage='speaker_label',
                location={'fmt':'json','ptr':'[1].name'})


class NameLanguagePolicyTests(unittest.TestCase):
    def test_original_latin_and_mixed_names_pass_without_relaxing_controls(self):
        cfg=validation_config(CFG,[actor()])
        for value in ('銀崎玲奈','レナ','Rena','레나 Rena'):
            with self.subTest(value=value):self.assertEqual(validate_text('銀崎玲奈',value,cfg),[])
        self.assertIn('empty',validate_text('銀崎玲奈','',cfg))
        self.assertIn('punctuation-only translation',validate_text('銀崎玲奈','.',cfg))
        self.assertIn('RPG Maker control code mismatch',validate_text(r'\V[2]銀崎玲奈',r'銀崎玲奈',cfg))

    def test_only_names_present_in_this_source_are_excluded(self):
        cfg=validation_config(CFG,[actor()])
        source='銀崎玲奈が来た。'
        target='銀崎玲奈가 왔어요.'
        self.assertEqual(validate_text(source,target,cfg),[])
        self.assertFalse(needs_repair({'source':source},{'text':target},cfg))
        self.assertIn('untranslated Japanese text',validate_text(source,'銀崎玲奈가 森林에 왔어요.',cfg))
        self.assertIn('untranslated Japanese text',validate_text(source,'銀崎玲奈가 왔어요ね.',cfg))
        self.assertIn('untranslated Japanese text',validate_text('誰が来た。',target,cfg))

    def test_name_substrings_do_not_hide_common_nouns(self):
        cfg=validation_config(CFG,[actor('花'),dict(actor('ミラ'),id='second')])
        for source,target in [('花瓶を見た。','花瓶을 봤다.'),('ミラクルだ。','ミラクル이에요.')]:
            with self.subTest(source=source):
                self.assertIn('untranslated Japanese text',validate_text(source,target,cfg))
        self.assertEqual(mask_names('ミラさんが来た。','ミラ 씨가 왔다.',cfg),'   씨가 왔다.')

    def test_non_name_fields_do_not_create_a_language_whitelist(self):
        row=dict(actor('森林'),file='data/Map001.json',usage='choice',
                 location={'fmt':'json','ptr':'events[1].pages[0].list[0].parameters[0][0]'})
        cfg=validation_config(CFG,[row])
        self.assertIn('untranslated Japanese text',validate_text('森林へ行く。','森林으로 가자.',cfg))

    def test_han_name_does_not_whitelist_a_verb_stem(self):
        cfg=validation_config(CFG,[actor('愛')])
        self.assertIn('untranslated Japanese text',validate_text('愛している。','愛하고 있어.',cfg))
        self.assertEqual(validate_text('愛が来た。','愛가 왔어.',cfg),[])
        self.assertEqual(validate_text('愛さんが来た。','愛 씨가 왔어.',cfg),[])

    def test_direct_actor_batch_does_not_require_korean(self):
        with patch('hy_backend.is_hy',return_value=True),patch('translation.request',return_value={
                'message':{'content':'[0] 銀崎玲奈'},'done_reason':'stop'}):
            output,_=translate_batch([actor()],{},CFG)
        self.assertEqual(output[0]['text'],'銀崎玲奈')

    def test_completed_name_only_residue_is_not_requeued_and_cache_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp)
            rows=[actor(),dict(id='line',source='銀崎玲奈が来た。',kind='dialogue',file='data/Map001.json')]
            save_json(project/'data/catalog.json',rows)
            records=[dict(id=row['id'],source=row['source'],text=text,model=CFG['model'],fingerprint=fingerprint(CFG))
                     for row,text in zip(rows,('銀崎玲奈','銀崎玲奈가 왔다.'))]
            data=''.join(json.dumps(record,ensure_ascii=False)+'\n' for record in records)
            path=project/'data/translations.jsonl';path.write_text(data,encoding='utf-8')
            known=cache(project,CFG)
            self.assertEqual(len(known),2)
            self.assertTrue(all(not item.get('_rpg_residual_language') for item in known.values()))
            self.assertEqual(path.read_text(encoding='utf-8'),data)

    def test_partial_draft_stays_available_after_cache_language_check(self):
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp)
            row=dict(id='line',source='森林へ行く。',kind='dialogue',file='data/Map001.json')
            save_json(project/'data/catalog.json',[row])
            item=dict(id=row['id'],source=row['source'],text='森林으로 가자.',model=CFG['model'],fingerprint=fingerprint(CFG))
            path=project/'data/translations.jsonl';path.write_text(json.dumps(item,ensure_ascii=False)+'\n',encoding='utf-8')
            known=cache(project,CFG)
            self.assertEqual(known['line']['text'],item['text'])
            self.assertTrue(known['line']['_rpg_residual_language'])

    def test_correction_keeps_clean_original_as_reference(self):
        source=r'\C[2]森林へ行く。\C[0]'
        row=dict(id='line',source=source,kind='dialogue',usage='dialogue',file='data/Map001.json')
        cfg=dict(CFG,_rpg_retry_errors={'line':"untranslated Japanese text; output='<rpt000/>森林으로 가자.<rpt001/>'"})
        with patch('hy_backend.is_hy',return_value=True),patch('translation.request',return_value={
                'message':{'content':'[0] 숲으로'},'done_reason':'stop'}) as request:
            output,_=translate_batch([row],{},cfg)
        prompt=request.call_args.args[2]['messages'][0]['content']
        self.assertIn('森林へ行く。',prompt)
        self.assertNotIn(r'\C[2]',prompt)
        self.assertIn('숲으로',output[0]['text'])


if __name__=='__main__':unittest.main()
