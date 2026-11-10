"""Synthetic requests only: no model, real game, cache or runtime execution."""
import re
import unittest
from unittest.mock import patch

from request_budget import InputBudgetError
from translation_segments import execute, split_text
from translation import translate_batch, BatchTranslationError, validate_text


CFG=dict(engine='rpgmaker',model='rpt-hymt2-7b:q6_k',source_language='japanese',
         endpoint='http://synthetic.invalid',num_ctx=16384,parallel=2)


def reply(values, *, truncated=False, limit=1800):
    return dict(values=values,truncated=truncated,hy=True,limit=limit,request_id='synthetic',
                budget={},seconds=0,tokens=1,eval_seconds=0)


class SegmentTests(unittest.TestCase):
    def test_sentence_boundaries_preserve_tokens_names_and_soft_line_breaks(self):
        for text in ('I love you\nbaby', 'Mr. Smith <rpt000/>', 'https://example.test/a?b=2', 'A <rpt000/> B'):
            self.assertIsNone(split_text(text),text)
        text='A complete first sentence. <rpt000/> A complete second sentence.'
        left,joiner,right=split_text(text)
        self.assertEqual(left+joiner+right,text)
        self.assertEqual((left+right).count('<rpt000/>'),1)
        self.assertEqual(split_text('第一の文。第二の文。'),('第一の文。',' ','第二の文。'))
        self.assertEqual(split_text('First paragraph\n\nSecond paragraph'),('First paragraph','\n\n','Second paragraph'))

    def test_preflight_splits_a_single_item_without_spending_a_model_retry(self):
        inputs=[dict(id='0',text='First complete sentence. Second complete sentence.')]
        sent=[]
        def send(items,minimum):
            if len(items[0]['text'])>30:raise InputBudgetError('synthetic budget')
            sent.append(items[0]['text']);return reply({'0':items[0]['text']})
        values,errors,summary=execute(inputs,CFG,send)
        self.assertFalse(errors);self.assertEqual(values['0'],inputs[0]['text'])
        self.assertEqual(len(sent),2);self.assertEqual(summary['attempts'],{})

    def test_length_stop_retranslates_only_the_unfinished_tail(self):
        seen=[]
        def send(items,minimum):
            ids=[item['id'] for item in items];seen.append(ids)
            if len(seen)==1:return reply({'0':'완료.','1':'잘린'},truncated=True)
            return reply({item['id']:'복구.' for item in items})
        values,errors,_=execute([dict(id=str(i),text='Some sentence.') for i in range(3)],CFG,send)
        self.assertEqual(values,{'0':'완료.'})
        self.assertEqual(set(errors),{'1','2'})
        self.assertEqual({value['error_code'] for value in errors.values()},{'output_limit'})
        self.assertEqual(seen,[['0','1','2']])  # Commit siblings before retrying the tail.

    def test_one_complete_sentence_gets_a_larger_limit_not_the_same_request(self):
        limits=[]
        def send(items,minimum):
            limit=max(1800,minimum);limits.append(limit)
            return reply({'0':'온전한 문장.'},truncated=len(limits)==1,limit=limit)
        values,errors,_=execute([dict(id='0',text='One complete sentence.')],CFG,send)
        self.assertFalse(errors);self.assertEqual(values['0'],'온전한 문장.')
        self.assertEqual(limits,[1800,2700])

    def test_output_limit_is_terminal_after_the_shared_retry_allowance(self):
        limits=[]
        def send(items,minimum):
            limit=max(1800,minimum);limits.append(limit)
            return reply({'0':'잘린'},truncated=True,limit=limit)
        values,errors,_=execute([dict(id='0',text='One sentence.')],dict(CFG,_item_retry_attempt=1),send)
        self.assertFalse(values);self.assertEqual(errors['0']['error_code'],'output_limit')
        self.assertTrue(errors['0']['terminal']);self.assertEqual(limits,[1800,2700])

    def test_unusable_segment_cancels_remaining_fragments_of_the_same_entry(self):
        calls=[]
        def send(items,minimum):
            text=items[0]['text']
            if len(text)>30:raise InputBudgetError('synthetic budget')
            calls.append(text)
            return reply({'0':None})
        _,errors,_=execute([dict(id='0',text='First complete sentence. Second complete sentence.')],CFG,send)
        self.assertEqual(len(calls),1)
        self.assertEqual(errors['0']['error_code'],'response_format')

    def test_length_stop_splits_at_whole_sentences_and_restores_original_parent(self):
        seen=[]
        def send(items,minimum):
            text=items[0]['text'];seen.append(text)
            if len(seen)==1:return reply({'0':'잘린'},truncated=True)
            return reply({'0':'첫 문장.' if text.startswith('First') else '둘째 문장.'})
        values,errors,_=execute([dict(id='0',text='First complete sentence. Second complete sentence.')],CFG,send)
        self.assertFalse(errors);self.assertEqual(values['0'],'첫 문장. 둘째 문장.')
        self.assertEqual(len(seen),3)


class BatchIntegrationTests(unittest.TestCase):
    def test_long_japanese_and_english_single_entries_join_then_validate_once(self):
        for engine,language,unit in (('rpgmaker','japanese','架空の最初の文です。架空の次の文です。'),
                                    ('rpgmaker','english','An invented first sentence. Another invented sentence. '),
                                    ('renpy','english','An invented first sentence. Another invented sentence. ')):
            with self.subTest(language=language,engine=engine):
                variable=r'\N[1]' if engine=='rpgmaker' else '[name]'
                source=unit*160+variable
                if engine=='renpy':source='{i}'+source+'{/i}'
                row=dict(id='original-id',source=source,kind='string',usage='text')
                calls=[]
                def respond(endpoint,route,body,**kwargs):
                    calls.append(body)
                    target=body['messages'][-1]['content'].split('[Source Text]\n',1)[1]
                    markers=re.findall(r'⟦RPT\d+⟧|<rpt\d+/>',target)
                    return {'message':{'content':'[0] 번역 문장.'+''.join(markers)},'done_reason':'stop'}
                with patch('translation.request',side_effect=respond), \
                     patch('translation.validate_text',wraps=validate_text) as validate, \
                     patch('diagnostics.report'),patch('translation.trace_event'):
                    result,_=translate_batch([row],{'source_passage':[{'target_id':'0','text':source}]},
                                             dict(CFG,engine=engine,source_language=language,num_ctx=4096))
                self.assertGreater(len(calls),1)
                self.assertEqual(validate.call_count,1)
                self.assertEqual(result[0]['id'],'original-id');self.assertEqual(result[0]['source'],source)
                self.assertEqual(result[0]['text'].count(variable),1)
                if engine=='renpy':
                    self.assertTrue(result[0]['text'].startswith('{i}'))
                    self.assertTrue(result[0]['text'].endswith('{/i}'))
                self.assertTrue(all(body['options']['num_ctx']==4096 for body in calls))

    def test_cut_response_never_becomes_a_missing_id_even_if_parser_cannot_parse_it(self):
        row=dict(id='x',source='一つの文です。',kind='string',usage='text')
        with patch('translation.request',return_value={'message':{'content':'[0'},'done_reason':'length'}), \
             patch('diagnostics.report'),patch('translation.trace_event'):
            with self.assertRaises(BatchTranslationError) as caught:translate_batch([row],{},CFG)
        self.assertEqual(caught.exception.failures[0]['error_code'],'output_limit')
        self.assertNotIn('Missing response item',caught.exception.failures[0]['error'])

    def test_genuinely_missing_header_still_uses_missing_response_error(self):
        row=dict(id='x',source='短い文。',kind='string',usage='text')
        with patch('translation.request',return_value={'message':{'content':'번호 없는 응답'},'done_reason':'stop'}), \
             patch('diagnostics.report'),patch('translation.trace_event'):
            with self.assertRaises(BatchTranslationError) as caught:translate_batch([row],{},CFG)
        self.assertEqual(caught.exception.failures[0]['error_code'],'missing_response_item')


if __name__=='__main__':unittest.main()
