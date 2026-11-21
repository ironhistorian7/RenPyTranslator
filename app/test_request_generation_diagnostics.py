"""Synthetic protocol failures and bounded summaries, with no model execution."""
import io
import json
import unittest
import urllib.error

from request_diagnostics import classify_request_failure,is_token_repeat_limit,request_metadata,response_metadata


class GenerationDiagnosticsTests(unittest.TestCase):
    def failure(self,body,status=500):
        error=urllib.error.HTTPError('http://127.0.0.1/api/chat',status,'failure',{},io.BytesIO(b'untouched'))
        error._rpt_transport=True;error.http_details={'status':status,'body':body}
        return error

    def test_repeat_prediction_is_local_generation_failure_and_does_not_consume_stream(self):
        error=self.failure('{"error":"prediction aborted: token repeat limit reached"}')
        self.assertTrue(is_token_repeat_limit(error))
        self.assertEqual(classify_request_failure(error),dict(code='token_repeat_limit',category='generation',restart_runtime=False))
        self.assertEqual(error.read(),b'untouched')

    def test_runner_and_cuda_failures_remain_runtime_failures(self):
        for body in ('runner process has terminated','CUDA error: illegal memory access',
                     'prediction aborted: token repeat limit reached; CUDA error: device-side assert'):
            with self.subTest(body=body):
                error=self.failure(body)
                self.assertFalse(is_token_repeat_limit(error))
                self.assertTrue(classify_request_failure(error)['restart_runtime'])

    def test_empty_body_and_non_500_are_not_guessed_as_repeat_limit(self):
        for body,status in (('',500),('token repeat',500),('prediction aborted: token repeat limit reached',400)):
            self.assertFalse(is_token_repeat_limit(self.failure(body,status)))
        self.assertFalse(is_token_repeat_limit(ValueError('prediction aborted: token repeat limit reached')))

    def test_request_summary_keeps_actual_options_protocol_schema_and_no_text_or_key(self):
        text='An invented private dialogue.'
        body={'model':'invented-model','messages':[{'role':'user','content':text}],
              'options':{'num_ctx':16384,'repeat_penalty':1.1,'api_key':'private-key'},
              'format':{'type':'object','properties':{'0':{'type':'string'}}}}
        result=request_metadata(body,protocol='indexed_text',input_count=1)
        self.assertEqual(result['options'],{'num_ctx':16384,'repeat_penalty':1.1})
        self.assertEqual(result['protocol'],'indexed_text');self.assertEqual(result['input_count'],1)
        self.assertEqual(result['prompt_characters'],len(text))
        self.assertEqual(result['format']['property_keys'],['0'])
        self.assertEqual(len(result['format']['sha256']),64)
        dumped=json.dumps(result)
        self.assertNotIn(text,dumped);self.assertNotIn('private-key',dumped)
        self.assertNotEqual(result['prompt_sha256'],request_metadata(dict(body,messages=[{'content':text+'!'}]))['prompt_sha256'])

    def test_response_summary_keeps_repetition_finish_length_and_scripts_without_output(self):
        text='가나다'*80
        result=response_metadata({'message':{'content':text,'thinking':'invented reasoning'},
                                  'done_reason':'length','eval_count':180,'eval_duration':42})
        self.assertEqual(result['done_reason'],'length')
        self.assertEqual(result['content']['characters'],240)
        self.assertEqual(result['scripts']['hangul'],240)
        self.assertEqual(result['repetition']['suffix_unit_characters'],3)
        self.assertEqual(result['repetition']['suffix_repeat_count'],80)
        self.assertNotIn(text,json.dumps(result));self.assertNotIn('invented reasoning',json.dumps(result))
        self.assertEqual(result['thinking']['characters'],len('invented reasoning'))

    def test_hashes_and_message_boundaries_are_unambiguous(self):
        first=request_metadata({'messages':[{'content':'a'},{'content':'bc'}]})
        second=request_metadata({'messages':[{'content':'ab'},{'content':'c'}]})
        self.assertNotEqual(first['prompt_sha256'],second['prompt_sha256'])
        self.assertEqual(response_metadata({})['content']['characters'],0)


if __name__=='__main__':unittest.main()
