import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import translation


class NumberedPreprocessingFlowTests(unittest.TestCase):
    def cfg(self, model, language='japanese'):
        return dict(engine='rpgmaker',source_language=language,model=model,endpoint='http://unused.invalid',
                    num_ctx=16384,parallel=2,batch_size=8,context_txt_enabled=False)

    def row(self, source):
        return dict(id='case',source=source,kind='dialogue',usage='dialogue')

    def test_both_backends_strip_styles_and_restore_functional_values(self):
        for model in ('rpt-hymt2-7b:q6_k','synthetic-general'):
            with self.subTest(model=model):
                source=r'\C[2]こんにちは、\N[1]さん。\C[0]'
                def reply(endpoint,route,body,**kwargs):
                    if model.startswith('rpt-hymt'):
                        self.assertNotIn('format',body)
                        user=body['messages'][-1]['content'].split('[Source Text]\n',1)[1]
                    else:
                        self.assertIn('format',body)
                        user=json.loads(body['messages'][-1]['content'])['items'][0]['text']
                    self.assertNotIn(r'\C[',user)
                    marker='⟦RPT000⟧' if model.startswith('rpt-hymt') else '<rpt000/>'
                    self.assertIn(marker,user)
                    text='안녕하세요, '+marker+'님.'
                    return {'message':{'content':'[0] '+text if model.startswith('rpt-hymt') else json.dumps({'0':text},ensure_ascii=False)}}
                with patch('translation.request',side_effect=reply):
                    result,_=translation.translate_batch([self.row(source)],{},self.cfg(model))
                self.assertEqual(result[0]['text'],r'\C[2]안녕하세요, \N[1]님.\C[0]')
                self.assertEqual(translation.validate_entry(source,result[0],self.cfg(model)),[])

    def test_inline_decoration_omission_keeps_translation(self):
        source=r'今日は\C[2]良い\C[0]天気です。'
        with patch('translation.request',return_value={'message':{'content':'{"0":"오늘은 좋은 날씨입니다."}'}}):
            result,_=translation.translate_batch([self.row(source)],{},self.cfg('synthetic-general'))
        self.assertEqual(result[0]['text'],'오늘은 좋은 날씨입니다.')
        self.assertEqual(translation.validate_entry(source,result[0],self.cfg('synthetic-general')),[])

    def test_nonlexical_small_tsu_and_style_only_never_use_model(self):
        for source,expected in [('「――っ！」','「――！」'),(r'\C[2]\FS[24]',r'\C[2]\FS[24]')]:
            with self.subTest(source=source),patch('translation.request') as request:
                result,_=translation.translate_batch([self.row(source)],{},self.cfg('synthetic-general'))
                request.assert_not_called()
                self.assertEqual(result[0]['text'],expected)

    def test_manual_values_bypass_automatic_language_and_tag_rules(self):
        cfg=self.cfg('synthetic-general')
        for value in ('','未翻訳','<anything>\n  unchanged  ',r'\UNKNOWN[x]'):
            self.assertEqual(translation.validate_entry('こんにちは',dict(text=value,status='manual_translation'),cfg),[])

    def test_numeric_runaway_returns_to_item_retry_without_output_growth(self):
        body='[0] '+''.join(str(n)+'.' for n in range(100))
        with patch('translation.request',return_value={'message':{'content':body},'done_reason':'length'}) as request,redirect_stdout(io.StringIO()):
            with self.assertRaises(translation.BatchTranslationError) as error:
                translation.translate_batch([self.row('こんにちは')],{},self.cfg('synthetic-general'))
        self.assertEqual(request.call_count,1)
        self.assertEqual(error.exception.failures[0]['error_code'],'generation_repetition')

    def test_truncated_short_item_preserves_outer_retry_budget(self):
        with patch('translation.request',return_value={'message':{'content':'[0] incomplete'},'done_reason':'length'}) as request,redirect_stdout(io.StringIO()):
            with self.assertRaises(translation.BatchTranslationError) as error:
                translation.translate_batch([self.row('こんにちは')],{},self.cfg('synthetic-general'))
        self.assertEqual(request.call_count,1)
        self.assertEqual(error.exception.failures[0]['error_code'],'output_limit')
        self.assertEqual(error.exception.failures[0]['retry_attempt'],0)
        self.assertFalse(error.exception.failures[0].get('terminal',False))

    def test_unchanged_english_choice_is_not_success(self):
        row=dict(self.row('Open the door'),usage='choice',kind='string')
        with patch('translation.request',return_value={'message':{'content':'{"0":"Open the door"}'}}):
            with self.assertRaises(translation.BatchTranslationError) as error:
                translation.translate_batch([row],{},self.cfg('synthetic-general','english'))
        self.assertIn('no Korean translation',error.exception.failures[0]['error'])

    def test_known_name_choice_and_technical_code_remain_exempt(self):
        from label_policy import unchanged_choice_errors
        cfg=self.cfg('synthetic-general','english')
        self.assertEqual(unchanged_choice_errors(dict(source='John Smith',usage='choice'),'John Smith',dict(cfg,_person_names={'John Smith':'존 스미스'})),[])
        self.assertEqual(unchanged_choice_errors(dict(source='F1',usage='choice'),'F1',cfg),[])


if __name__=='__main__':unittest.main()
