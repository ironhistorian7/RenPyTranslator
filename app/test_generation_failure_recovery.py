"""Synthetic output failures only; never accesses games, servers or production caches."""
from contextlib import ExitStack,redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import Mock,patch

from engine import save_json
import translation


CFG={'engine':'rpgmaker','model':'invented','language':'korean','source_language':'japanese',
     'num_ctx':16384,'parallel':1,'batch_size':8,'endpoint':'http://unused.invalid',
     'context_txt_enabled':False}
ROW={'id':'invented-item','source':'こんにちは。','kind':'dialogue','usage':'dialogue',
     'file':'data/Map001.json','block':'invented'}


def repeat_error():
    error=urllib.error.HTTPError(CFG['endpoint']+'/api/chat',500,'Internal Server Error',{},io.BytesIO())
    error.http_details={'status':500,'body':'{"error":"prediction aborted, token repeat limit reached"}'}
    error._rpt_transport=True
    return error


class GenerationRecoveryTests(unittest.TestCase):
    def setUp(self):
        stack=ExitStack();self.addCleanup(stack.close)
        for target,value in [('hy_backend.is_hy',False),('translation.runtime_status',{}),
                             ('diagnostics.report',None)]:
            stack.enter_context(patch(target,return_value=value))

    def test_repeat_abort_becomes_item_failure_with_actual_wire_diagnostics(self):
        snapshot=Mock(return_value='invented-generation.json')
        with patch('translation.request',side_effect=repeat_error()),self.assertRaises(translation.BatchTranslationError) as caught:
            translation.translate_batch([ROW],{},dict(CFG,_recovery_generation_failed=snapshot))
        error=caught.exception;failure=error.failures[0]
        self.assertFalse(getattr(error,'_rpt_transport',False))
        self.assertEqual(failure['error_code'],'token_repeat_limit')
        self.assertEqual(failure['request_diagnostics']['request']['options']['num_ctx'],16384)
        self.assertEqual(failure['request_diagnostics']['request']['format']['kind'],'schema')
        self.assertIs(failure['request_diagnostics']['request']['think'],False)
        self.assertEqual(failure['request_diagnostics']['request']['roles'],['system','user'])
        self.assertFalse(failure['request_diagnostics']['partial_response_available'])
        snapshot.assert_called_once()
        self.assertEqual(failure['request_diagnostics']['generation_evidence'],'invented-generation.json')

    def test_repeat_abort_exhausts_only_item_budget_and_retains_existing_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            project=Path(directory)
            earlier=dict(ROW,id='already-done',source='別の文。')
            save_json(project/'data/catalog.json',[earlier,ROW])
            saved=dict(id=earlier['id'],source=earlier['source'],text='이전 문장.',
                       model=CFG['model'],fingerprint=translation.fingerprint(CFG))
            raw=(json.dumps(saved,ensure_ascii=False)+'\n').encode('utf-8')
            path=project/'data/translations.jsonl';path.write_bytes(raw)
            with (patch('translation.request',side_effect=lambda *args,**kwargs:(_ for _ in ()).throw(repeat_error())) as request,
                  patch('job_events.warning'),redirect_stdout(io.StringIO())):
                translation._translate(project,CFG)
            self.assertEqual(request.call_count,3)
            self.assertTrue(path.read_bytes().startswith(raw))
            records=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
            self.assertEqual(records[-1]['status'],'source_fallback')
            self.assertEqual(records[-1]['id'],ROW['id'])

    def test_committed_fallback_does_not_claim_runtime_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            project=Path(directory);save_json(project/'data/catalog.json',[ROW])
            committed=Mock()
            def fail(*args,**kwargs):
                raise translation.BatchTranslationError([],[
                    dict(row=ROW,error='synthetic bad output',terminal=True)],dict(seconds=0,items=1))
            with patch('translation.translate_batch',side_effect=fail),patch('job_events.warning'),redirect_stdout(io.StringIO()):
                translation._translate(project,dict(CFG,_recovery_progress_committed=committed))
            committed.assert_not_called()

    def test_only_successful_commits_signal_runtime_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            project=Path(directory);save_json(project/'data/catalog.json',[ROW])
            committed=Mock()
            output=dict(id=ROW['id'],source=ROW['source'],text='안녕하세요.',
                        model=CFG['model'],fingerprint=translation.fingerprint(CFG),request_id='invented-request')
            with patch('translation.translate_batch',return_value=([output],dict(seconds=0,items=1))),redirect_stdout(io.StringIO()):
                translation._translate(project,dict(CFG,_recovery_progress_committed=committed))
            committed.assert_called_once_with(item_ids=[ROW['id']],request_ids=['invented-request'])

    def test_generic_model_uses_same_targeted_japanese_correction(self):
        previous="['untranslated Japanese text']; output='안녕ッ'"
        cfg=dict(CFG,_rpg_retry_errors={ROW['id']:previous})
        response={'message':{'content':'{"0":"안녕"}'},'done_reason':'stop'}
        with patch('translation.request',return_value=response) as request:
            result,_=translation.translate_batch([ROW],{},cfg)
        body=request.call_args.args[2]
        user=body['messages'][1]['content']
        payload=json.loads(user)
        self.assertEqual(payload['items'][0]['text'],'안녕ッ')
        self.assertEqual(payload['context_do_not_translate']['original_source'],ROW['source'])
        self.assertEqual(body['format']['required'],['0'])
        self.assertEqual(result[0]['text'],'안녕')

    def test_repetitive_length_stop_does_not_grow_output_limit(self):
        response={'message':{'content':'{"0":"'+'.,'*150},'done_reason':'length','eval_count':284}
        with patch('translation.request',return_value=response) as request,self.assertRaises(translation.BatchTranslationError) as caught:
            translation.translate_batch([ROW],{},CFG)
        request.assert_called_once()
        self.assertEqual(caught.exception.failures[0]['error_code'],'generation_repetition')

    def test_repetitive_response_uses_only_existing_retry_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            project=Path(directory)
            rows=[dict(ROW,id='first')]
            save_json(project/'data/catalog.json',rows)
            formats=[]
            def generate(endpoint,route,body):
                formats.append(body.get('format'))
                return {'message':{'content':'{"0":"'+'.,'*150},'done_reason':'length','eval_count':284}
            with patch('translation.request',side_effect=generate),redirect_stdout(io.StringIO()):
                translation._translate(project,dict(CFG,batch_size=1))
            self.assertEqual(len(formats),3)
            self.assertEqual(formats[0]['required'],['0'])
            self.assertEqual(formats[1:],[None,None])
            saved=translation.cache(project,CFG)
            self.assertEqual(set(saved),{'first'})
            self.assertEqual(saved['first']['status'],'source_fallback')


if __name__=='__main__':unittest.main()
