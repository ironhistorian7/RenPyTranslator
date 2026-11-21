"""Invented registration metadata only; no model, server or game use."""
import json
import unittest
from unittest.mock import Mock,patch

from model_diagnostics import collect,summarize


class ModelDiagnosticsTests(unittest.TestCase):
    def test_real_quantization_and_template_identity_without_template_or_vocab_text(self):
        result=summarize({'details':{'quantization_level':'IQ2_XS','family':'invented'},
            'model_info':{'general.architecture':'invented','invented.context_length':12345,
                          'tokenizer.ggml.tokens':['PRIVATE WORD'],'invented.attention.head_count':8,
                          'tokenizer.chat_template':'PRIVATE EMBEDDED TEMPLATE'},
            'template':'PRIVATE TEMPLATE thinking tools','system':'PRIVATE SYSTEM',
            'parameters':'stop "<turn>"\ntemperature 0.2\nnum_ctx 8192\nunknown PRIVATE',
            'capabilities':['completion']},
            {'details':{'quantization_level':'Q4_0'}})
        self.assertEqual(result['details']['quantization_level'],'IQ2_XS')
        self.assertEqual(result['model_info']['invented.context_length'],12345)
        self.assertEqual(result['registered_parameters']['temperature'],0.2)
        self.assertTrue(result['template']['has_thinking_switch'])
        self.assertEqual(len(result['template']['sha256']),64)
        self.assertGreater(result['embedded_template']['characters'],0)
        self.assertNotIn('PRIVATE',json.dumps(result))
        self.assertNotIn('tokenizer.ggml.tokens',result['model_info'])

    def test_metadata_failure_is_nonfatal_and_does_not_retry(self):
        opener=Mock();opener.open.side_effect=TimeoutError('invented private endpoint')
        with patch('model_diagnostics.urllib.request.build_opener',return_value=opener):
            result=collect('http://unused.invalid','invented',{'details':{'family':'test'}})
        self.assertFalse(result['available'])
        self.assertEqual(result['details']['family'],'test')
        self.assertNotIn('unused.invalid',json.dumps(result))
        opener.open.assert_called_once()


if __name__=='__main__':unittest.main()
