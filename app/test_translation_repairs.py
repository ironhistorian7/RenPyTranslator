import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from engine import save_json
from translation import validate_text,validate_entry,protect,cache,translate,translate_batch,BatchTranslationError,retry_instruction
from token_recovery import split_outer_styles,restore_saved,restore_output
from failed_repair import run,saved_output

CFG={'language':'korean','model':'fake','endpoint':'http://localhost','_reuse_completed':True}


class TranslationRepairTests(unittest.TestCase):
    def test_whitespace_resume_never_calls_model_and_preserves_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);row=dict(id='blank',source='\n\n\n',kind='string',file='custom.rpy')
            save_json(p/'data/catalog.json',[row])
            save_json(p/'data/failed-items.json',[{'row':row,'error':'empty'}])
            path=p/'data/translations.jsonl'
            original=json.dumps(dict(row,text=row['source'],status='source_fallback',model='fake',fingerprint='old'))+'\n'
            path.write_text(original,encoding='utf8')
            with patch('translation.request',side_effect=AssertionError('No inference')):
                translate(p,CFG)
                _,report=run(p,CFG)
            self.assertEqual(report['remaining'],0)
            self.assertEqual(report['model_calls'],0)
            self.assertEqual(path.read_text(encoding='utf8'),original)
            self.assertEqual(validate_entry(row['source'],cache(p,CFG)['blank'],CFG),[])
            self.assertIn('empty',validate_text('A sentence.','',CFG))

    def test_percent_prose_and_real_fields(self):
        self.assertEqual(validate_text('A 50% chance of victory.','승리 확률은 50%입니다.',CFG),[])
        self.assertEqual(validate_text('100% of it.','전부입니다.',CFG),[])
        for field in ('%s','%03d','% d','%(name)s','%%'):
            self.assertEqual(protect(field)[1],[field])
            self.assertIn('format field mismatch',validate_text('Value '+field,'값',CFG))

    def test_wrappers_are_restored_without_sending_them_to_model(self):
        row=dict(id='x',source='{color=#ffff00}{i}Hello [person].{/i}{/color}',kind='dialogue')
        answer={'message':{'content':json.dumps({'0':'안녕 <rpt000/>.'})}}
        with patch('translation.request',return_value=answer) as request:
            result,_=translate_batch([row],{},CFG)
        payload=json.loads(request.call_args.args[2]['messages'][1]['content'])
        self.assertEqual(payload['items'][0]['text'],'Hello <rpt000/>.')
        self.assertEqual(result[0]['text'],'{color=#ffff00}{i}안녕 [person].{/i}{/color}')
        source='{i}First{/i} and {i}second{/i}'
        self.assertEqual(split_outer_styles(source),('',source,''))

    def test_saved_style_recovery_never_fabricates_names(self):
        self.assertEqual(restore_output('<rpt000/> 안녕 <rpt011/>',['[name]','{w}']),'[name] 안녕 {w}')
        self.assertEqual(restore_saved('{color=red}Scream!{/color}','<rpt000/>아악!<rpt011/>'),'{color=red}아악!{/color}')
        self.assertEqual(restore_saved('{i}[person] arrives.{/i}','<rpt001/>가 왔어요.'),'{i}[person]가 왔어요.{/i}')
        for source,text in [('{i}[person] arrives.{/i}','<rpt000/>가 왔어요.<rpt002/>'),
                            ('{i}[person] arrives.{/i}','<rpt000/>님이 왔어요.<rpt001/>'),
                            ('Hello [person] [other].','<rpt000/> 안녕 <rpt011/>'),
                            ('[person] is here.','<rpt000/> <rpt000/> 왔어요.')]:
            with self.subTest(source=source),self.assertRaises(ValueError):restore_saved(source,text)

    def test_retry_feedback_and_new_saved_numbering(self):
        row=dict(id='x',source='{i}Hello [person] and [friend].{/i}',kind='dialogue')
        note=retry_instruction([row],{'x':'Protected token mismatch; output=irrelevant'})
        self.assertIn('<rpt000/> = [person]',note)
        self.assertIn('Previous error:',note)
        self.assertNotIn('irrelevant',note)
        error="Wrapper-stripped: Protected token mismatch; output='<rpt001/>와 <rpt000/> 안녕.'"
        self.assertEqual(saved_output(row['source'],error,CFG),'{i}[friend]와 [person] 안녕.{/i}')


if __name__=='__main__':unittest.main()
