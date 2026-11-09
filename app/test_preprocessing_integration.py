"""Invented text and mocked requests; no actual games, models or caches."""
import json
from unittest import TestCase
from unittest.mock import patch

import translation
from failed_repair import saved_output


CFG = {'model':'fake','language':'korean','source_language':'english',
       'endpoint':'http://127.0.0.1:1','num_ctx':4096}


class PreprocessingIntegrationTests(TestCase):
    def batch(self, source, raw, *, context=None, hy=False):
        row={'id':'invented','source':source,'kind':'dialogue','usage':'dialogue'}
        calls=[]
        def request(endpoint, route, body):
            calls.append(body)
            return {'message':{'content':'[0] '+raw if hy else json.dumps({'0':raw})},'done_reason':'stop'}
        with patch('translation.request',side_effect=request), \
             patch('translation.runtime_status',return_value={}), \
             patch('hy_backend.is_hy',return_value=hy), \
             patch('diagnostics.stage'), patch('diagnostics.report'), \
             patch('translation.validate_text',wraps=translation.validate_text) as validate:
            output,_=translation.translate_batch([row],context or {},CFG)
            self.assertEqual(validate.call_count,1)
        self.assertEqual(len(calls),1)
        return output[0]['text'],calls[0]

    def test_first_request_removes_inline_styles_without_splitting_the_sentence(self):
        text,body=self.batch('Let us {i}try{/i} the {color=#123456}new{/color} route.',
                             '새로운 경로를 시도해 보자.')
        item=json.loads(body['messages'][1]['content'])['items'][0]
        self.assertEqual(item['text'],'Let us try the new route.')
        self.assertNotIn('<rpt',item['text'])
        self.assertEqual(text,'새로운 경로를 시도해 보자.')

    def test_whole_sentence_style_is_reassembled_locally(self):
        text,body=self.batch('{b}An invented notice.{/b}','새 안내입니다.')
        self.assertEqual(text,'{b}새 안내입니다.{/b}')
        self.assertEqual(json.loads(body['messages'][1]['content'])['items'][0]['text'],
                         'An invented notice.')

    def test_variable_anchor_keeps_style_and_functional_marker_numbering(self):
        text,body=self.batch('Ask {i}[reader]{/i} tomorrow.','내일 <rpt000/>에게 물어봐.')
        self.assertEqual(text,'내일 {i}[reader]{/i}에게 물어봐.')
        item=json.loads(body['messages'][1]['content'])['items'][0]
        self.assertEqual(item['text'],'Ask <rpt000/> tomorrow.')

    def test_context_passage_is_also_sent_without_visual_tags(self):
        _,body=self.batch('An invented sentence.','새 문장입니다.',
                          context={'source_passage':[{'text':'A {i}nearby{/i} sentence.'},{'target_id':'0'}]})
        context=json.loads(body['messages'][1]['content'])['context_do_not_translate']
        self.assertEqual(context['source_passage'][0]['text'],'A nearby sentence.')

    def test_hy_target_id_wrapper_is_removed_before_the_single_final_check(self):
        text,_=self.batch('An invented sentence.','[target_id="0"]새 문장입니다.[/target_id]',hy=True)
        self.assertEqual(text,'새 문장입니다.')

    def test_new_error_numbering_can_be_restored_without_a_model(self):
        source='Ask {i}[reader]{/i} about {b}tomorrow{/b}.'
        error='Visual-stripped: markup mismatch; output='+repr('내일 <rpt000/>에게 물어봐.')
        self.assertEqual(saved_output(source,error,CFG),'내일 {i}[reader]{/i}에게 물어봐.')

    def test_retry_instructions_do_not_ask_for_removed_visual_markers(self):
        row={'id':'invented','source':'An {i}invented{/i} sentence.'}
        note=translation.retry_instruction([row],{'invented':'missing response id'},CFG)
        self.assertNotIn('<rpt',note)
        row['source']='Ask {i}[reader]{/i} tomorrow.'
        note=translation.retry_instruction([row],{'invented':'missing marker'},CFG)
        self.assertIn('<rpt000/>',note)
        self.assertNotIn('<rpt001/>',note)
