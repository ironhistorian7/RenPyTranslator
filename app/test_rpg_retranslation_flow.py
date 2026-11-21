"""Synthetic Japanese RPG translation checks; no model or game is started."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine import save_json
import translation


CFG={'engine':'rpgmaker','model':'rpt-hymt2-7b:q6_k','source_language':'japanese',
     'language':'korean','num_ctx':16384,'parallel':1,'batch_size':8,
     'endpoint':'http://unused.invalid','context_txt_enabled':False}


class RetranslationFlowTests(unittest.TestCase):
    def run_job(self, outputs, source='こんにちは'):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        project=Path(temporary.name)
        row=dict(id='pending',source=source,kind='dialogue',usage='dialogue',file='test',block='one')
        save_json(project/'data/catalog.json',[row]);requests=[]
        def request(endpoint,route,body,**kwargs):
            requests.append(body)
            return {'message':{'content':'[0] '+outputs[len(requests)-1]}}
        with redirect_stdout(io.StringIO()), patch('translation.request',side_effect=request), \
             patch('source_context.SourceContext.context',return_value={'source_passage':[{'text':'CONTEXT_SENTINEL'}]}):
            translation._translate(project,dict(CFG))
        return project,row,requests

    def test_small_tsu_only_cleaned_before_single_validation(self):
        row=dict(id='x',source='こんにちは',kind='dialogue',usage='dialogue')
        with patch('translation.request',return_value={'message':{'content':'[0] 안녕ッ'}}), \
             patch('translation.validate_text',wraps=translation.validate_text) as validate:
            result,_=translation.translate_batch([row],{},dict(CFG))
        self.assertEqual(result[0]['text'],'안녕')
        self.assertEqual(validate.call_count,1)
        with patch('translation.request',return_value={'message':{'content':'[0] 안녕ッ'}}):
            renpy,_=translation.translate_batch([row],{},dict(CFG,engine='renpy'))
        self.assertEqual(renpy[0]['text'],'안녕')

    def test_mixed_retries_use_clean_context_and_distinct_prompts(self):
        project,row,requests=self.run_job(['안녕日本語','안녕日本語','안녕하세요'])
        self.assertEqual(len(requests),3)
        prompts=[request['messages'][0]['content'] for request in requests]
        self.assertIn('CONTEXT_SENTINEL',prompts[0])
        for prompt in prompts[1:]:
            self.assertNotIn('CONTEXT_SENTINEL',prompt)
            self.assertIn(row['source'],prompt)
            self.assertNotIn('안녕日本語',prompt)
            self.assertIn('Translate the following japanese text into Korean',prompt)
        self.assertIn('[0] '+row['source'],prompts[1])
        self.assertIn('Return only [ID]',prompts[1])
        self.assertIn('without an item header',prompts[2])
        self.assertTrue(prompts[2].endswith('[Source Text]\n'+row['source']))
        self.assertNotEqual(prompts[1],prompts[2])
        self.assertEqual(json.loads((project/'data/catalog.json').read_text(encoding='utf-8'))[0]['source'],row['source'])

    def test_minor_uses_whole_phrase_correction_without_revalidating_the_draft(self):
        with patch('translation.validate_text',wraps=translation.validate_text) as validate:
            project,row,requests=self.run_job(['오늘은 날씨가 정말 좋습니다日.','좋습니다.'])
        self.assertEqual(len(requests),2)
        self.assertEqual(validate.call_count,2)
        second=requests[1]['messages'][0]['content']
        self.assertIn('[0] 좋습니다日.',second)
        self.assertNotIn('CONTEXT_SENTINEL',second)
        saved=json.loads((project/'data/translations.jsonl').read_text(encoding='utf-8').splitlines()[-1])
        self.assertEqual(saved['text'],'오늘은 날씨가 정말 좋습니다.')

    def test_minor_correction_restores_visual_style_and_variable(self):
        source=r'\C[2]こんにちは\V[1]\C[0]'
        project,row,requests=self.run_job([r'오늘은 날씨가 정말 좋습니다日. ⟦RPT000⟧','좋습니다.'],source=source)
        self.assertEqual(len(requests),2)
        self.assertIn('[0] 좋습니다日.',requests[1]['messages'][0]['content'])
        saved=json.loads((project/'data/translations.jsonl').read_text(encoding='utf-8').splitlines()[-1])
        self.assertEqual(saved['text'],r'\C[2]오늘은 날씨가 정말 좋습니다. \V[1]\C[0]')
        self.assertEqual(translation.validate_entry(source,saved,CFG),[])
        self.assertEqual(translation.cache(project,dict(CFG))['pending']['text'],saved['text'])

    def test_generic_json_model_uses_the_same_cleanup_policy(self):
        row=dict(id='x',source='こんにちは',kind='dialogue',usage='dialogue')
        with patch('translation.request',return_value={'message':{'content':'{"0":"안녕ッ"}'}}) as request:
            result,_=translation.translate_batch([row],{},dict(CFG,model='synthetic'))
        self.assertEqual(result[0]['text'],'안녕')
        self.assertIn('의성어',request.call_args.args[2]['messages'][0]['content'])
        body=request.call_args.args[2]
        self.assertEqual(body['format']['required'],['0'])
        self.assertIn('JSON object',body['messages'][0]['content'])
        self.assertNotIn('[ID]',body['messages'][0]['content'])
        self.assertEqual(json.loads(body['messages'][-1]['content'])['items'][0]['text'],'こんにちは')

    def test_saved_small_tsu_output_recovers_without_model_request(self):
        from failed_repair import saved_output
        with patch('translation.request',side_effect=AssertionError('No model request')):
            text=saved_output('こんにちは',"untranslated Japanese text; output='안녕ッ'",CFG,'dialogue')
        self.assertEqual(text,'안녕')

    def test_kanji_only_body_uses_the_same_language_rule_in_worker_and_cache(self):
        row=dict(id='x',source='攻撃',kind='string',usage='ui_label')
        with patch('translation.request',return_value={'message':{'content':'[0] Attack'}}):
            with self.assertRaises(translation.BatchTranslationError) as failure:
                translation.translate_batch([row],{},dict(CFG))
        self.assertIn('no Korean translation',failure.exception.failures[0]['error'])
        self.assertIn('no Korean translation',translation.validate_entry('攻撃',{'text':'Attack'},CFG))

    def test_exhausted_language_errors_keep_partial_and_report_it(self):
        project,row,_=self.run_job(['안녕日本語','안녕日本語','안녕日本語'])
        record=json.loads((project/'data/translations.jsonl').read_text(encoding='utf-8').splitlines()[-1])
        self.assertEqual((record['status'],record['text']),('partial_translation','안녕日本語'))
        self.assertTrue(record['_rpg_residual_language'])
        progress=json.loads((project/'data/progress.json').read_text(encoding='utf-8'))
        self.assertEqual((progress['successful'],progress['partial']),(0,1))
        failures=json.loads((project/'data/failed-items.json').read_text(encoding='utf-8'))
        self.assertTrue(failures[0]['kept_partial'])

    def test_japanese_only_draft_is_not_reinjected(self):
        _,row,requests=self.run_job(['悪い下書き','悪い下書き','안녕하세요'])
        self.assertEqual(len(requests),3)
        for request in requests[1:]:
            prompt=request['messages'][0]['content']
            self.assertNotIn('悪い下書き',prompt)
            self.assertNotIn('CONTEXT_SENTINEL',prompt)
            self.assertIn(row['source'],prompt)

    def test_invalid_control_never_becomes_partial(self):
        project,row,requests=self.run_job(['안녕日本語','안녕日本語','안녕日本語'],source=r'こんにちは\V[1]')
        record=json.loads((project/'data/translations.jsonl').read_text(encoding='utf-8').splitlines()[-1])
        self.assertEqual((record['status'],record['text']),('source_fallback',row['source']))
        self.assertFalse(record.get('_rpg_residual_language',False))
        self.assertNotEqual(requests[1]['messages'],requests[2]['messages'])

    def test_incomplete_cache_is_displayable_but_remains_a_repair_candidate(self):
        project,row,_=self.run_job(['안녕日本語','안녕日本語','안녕日本語'])
        known=translation.cache(project,dict(CFG))
        self.assertEqual(known['pending']['status'],'partial_translation')
        self.assertTrue(known['pending']['_rpg_residual_language'])
        self.assertEqual(translation.validate_entry(row['source'],known['pending'],CFG,allow_partial=True),[])
        self.assertIn('untranslated Japanese text',translation.validate_entry(row['source'],known['pending'],CFG))

    def test_comparison_does_not_touch_original_cache(self):
        from rpgmaker_project import comparison
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);project=root/'original';(project/'data/rpg-source').mkdir(parents=True)
            save_json(project/'data/catalog.json',[]);save_json(project/'data/rpgmaker-project.json',{})
            cache=project/'data/translations.jsonl';cache.write_bytes(b'original cache bytes\n')
            cfg=dict(CFG)
            other=comparison(project,cfg)
            saved=json.loads((other/'project.json').read_text(encoding='utf-8'))
            self.assertNotIn('_rpg_retranslation_policy',saved)
            self.assertNotIn('_rpg_retranslation_policy',cfg)
            self.assertEqual(cache.read_bytes(),b'original cache bytes\n')
            self.assertFalse((other/'data/translations.jsonl').exists())


if __name__=='__main__':unittest.main()
