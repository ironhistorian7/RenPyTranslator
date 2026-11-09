"""Only synthetic TXT, cached rows and mocked local endpoints. No inference."""
import io
import json
from contextlib import redirect_stdout,redirect_stderr
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from context_notes import ContextNotes
from hy_backend import body
from request_budget import fit,InputBudgetError,BatchNeedsSplit,Counter
from translation import translate_batch,cache,fingerprint,BatchTranslationError
from diagnostics import report,stage


class ExactCounter:
    method='test_tokenizer';reason=None
    def count(self,text):return len(text)


class ContextPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.project=Path(self.temp.name);self.root=self.project/'staging/game/context'
        self.root.mkdir(parents=True)
        self.rows=[dict(id=str(i),source=s,source_file='game/example.rpy',file='example.rpy',source_line=i+1,kind='dialogue',speaker='a',block='intro_%08x'%i) for i,s in enumerate(['First morning begins.','A request is made.','They agree to work together.','They return to work.'])]
        self.cfg=dict(model='fake',num_ctx=16384,context_txt_tokens=4500,_repair=True,_trace_dir=str(self.project/'data'),endpoint='http://127.0.0.1:1')

    def test_state_change_uses_only_current_state_and_boundary(self):
        text='''Scene ID: introduction
Applies to: whole file
Injection context:
An introduction at work.
State ID: strangers
Applies to: "First morning begins." ~ "A request is made."
Injection context:
화자: a
청자: b
현재 관계: 처음 만난 동료.
말투: 격식 있는 존댓말.
State ID: colleagues
Applies to: "They agree to work together." ~ "They return to work."
Injection context:
화자: a
청자: b
현재 관계: 협력하기로 한 동료.
말투: 존댓말 유지.
Management:
Do not inject this evidence.
'''
        (self.root/'example.txt').write_text(text,encoding='utf-8')
        notes=ContextNotes(self.project,self.cfg,self.rows)
        first=notes.context(self.rows[:1],self.cfg);last=notes.context(self.rows[-1:],self.cfg)
        self.assertIn('처음 만난',first['current_state']);self.assertNotIn('협력하기로',json.dumps(first,ensure_ascii=False))
        self.assertIn('협력하기로',last['current_state']);self.assertNotIn('처음 만난',last['current_state'])
        self.assertNotIn('Do not inject',json.dumps(last))
        self.assertTrue(notes.same_scope(self.rows[0],self.rows[1]));self.assertFalse(notes.same_scope(self.rows[1],self.rows[2]))
        self.assertIn('example.txt',last['_context_audit']['files'])
        prompt=body([{'id':'0','text':'Test.'}],first,self.cfg,{},'')['messages'][0]['content']
        self.assertLess(prompt.index('[Current State'),prompt.index('[Source Text]'))
        self.assertNotIn('_context_audit',prompt)

    def test_unresolved_scope_and_branch_do_not_become_global_facts(self):
        (self.root/'global.txt').write_text('공통 배경.',encoding='utf-8')
        for scope in ['unknown place','whole file\nBranch: flag == True']:
            (self.root/'example.txt').write_text('Scene ID: x\nApplies to: '+scope+'\nInjection context:\nUnsupported relationship.',encoding='utf-8')
            notes=ContextNotes(self.project,self.cfg,self.rows)
            self.assertNotIn('scene',notes.context(self.rows[:1],self.cfg)['translation_guidance'])
            self.assertTrue(notes.report['issues'])

    def test_explicit_ids(self):
        (self.root/'example.txt').write_text('Scene ID: x\nStart ID: 1\nEnd ID: 2\nInjection context:\n말투: 존댓말.',encoding='utf-8')
        notes=ContextNotes(self.project,self.cfg,self.rows)
        self.assertEqual(set(notes.scenes),{'1','2'})

    def test_final_combined_txt_budget_and_original_source_are_preserved(self):
        ctx={'translation_guidance':{'global':'\n'.join(['Full instruction.']*400),'scene':'A scene.'},'current_state':'말투: 존댓말.'}
        original=json.dumps(ctx)
        build=lambda c:{'messages':[{'role':'user','content':json.dumps(c,ensure_ascii=False)+'\n[0] Source stays intact.'}],'options':{'num_ctx':16384,'num_predict':1800}}
        with patch('request_budget.counter',return_value=ExactCounter()):
            result,meta=fit(ctx,self.cfg,build)
        self.assertGreater(meta['txt_tokens'],4500);self.assertFalse(meta['dropped'])
        self.assertEqual(meta['txt_budget'],'adaptive')
        self.assertIn('[0] Source stays intact.',result['messages'][0]['content'])
        self.assertEqual(json.dumps(ctx),original)

    def test_overflow_splits_before_removing_complete_dialogue(self):
        ctx={'source_passage':[{'target_id':'0'},{'text':'I love you baby.'},{'target_id':'1'}]}
        build=lambda c:{'messages':[{'role':'user','content':json.dumps(c)+'x'*200}],
                        'options':{'num_ctx':400,'num_predict':100}}
        with patch('request_budget.counter',return_value=ExactCounter()),self.assertRaises(BatchNeedsSplit):
            fit(ctx,dict(self.cfg,_allow_batch_split=True),build)
        # A caller without batch splitting must not delete the internal bridge.
        with patch('request_budget.counter',return_value=ExactCounter()),self.assertRaises(InputBudgetError):
            fit(ctx,self.cfg,build)

    def test_nearest_sentence_survives_large_txt_and_baby_is_never_a_fragment(self):
        text='I love you baby.'
        ctx={'translation_guidance':{'global':'\n'.join(['Background information.']*300)},
             'source_passage':[{'text':'far '*200},{'text':text},{'target_id':'0'}],
             '_context_audit':{'files':{'global.txt':'test-hash'}}}
        build=lambda c:{'messages':[{'role':'user','content':json.dumps(c)+'\nSOURCE'}],
                        'options':{'num_ctx':700,'num_predict':100}}
        with patch('request_budget.counter',return_value=ExactCounter()):result,meta=fit(ctx,self.cfg,build)
        selected=json.loads(result['messages'][0]['content'].removesuffix('\nSOURCE'))
        self.assertEqual(selected['source_passage'],[{'text':text},{'target_id':'0'}])
        self.assertTrue(meta['dropped']);self.assertEqual(meta['files'],{'global.txt':'test-hash'})
        self.assertLessEqual(meta['prompt_content_tokens']+meta['output_reserved']+meta['framing_reserved'],700)

    def test_all_notes_excluded_still_have_audit_and_target_is_whole(self):
        text='I love you baby.'
        ctx={'translation_guidance':{'global':'one long instruction '*200},
             '_context_audit':{'scope':'a','files':{'global.txt':'hash'}}}
        build=lambda c:{'messages':[{'role':'user','content':json.dumps(c)+'\n'+text}],
                        'options':{'num_ctx':500,'num_predict':100}}
        with patch('request_budget.counter',return_value=ExactCounter()):result,meta=fit(ctx,self.cfg,build)
        self.assertEqual(result['messages'][0]['content'],'{}\n'+text)
        self.assertEqual(meta['txt_tokens'],0);self.assertEqual(meta['scope'],'a')
        self.assertEqual(meta['dropped'][0]['part'],'TXT:global')
        self.assertEqual(meta['dropped'][0]['removed_units'],1)

    def test_budget_split_is_logged_before_any_translation_call(self):
        cfg=dict(self.cfg,_allow_batch_split=True,num_ctx=300)
        with patch('request_budget.counter',return_value=ExactCounter()),patch('translation.request') as api:
            with self.assertRaises(BatchNeedsSplit):translate_batch(self.rows[:2],{},cfg)
        api.assert_not_called()
        events=[json.loads(line) for line in (self.project/'data/translation-requests.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(events[0]['event'],'budget_split')
        self.assertEqual(events[0]['items'],['0','1'])
        self.assertEqual(events[0]['context_budget']['num_ctx_requested'],300)

    def test_single_oversize_item_logs_budget_without_sending_or_truncating(self):
        item=dict(self.rows[0],source='I love you baby. '*100)
        stage('translation',self.project,self.cfg)
        with patch('request_budget.counter',return_value=ExactCounter()),patch('translation.request') as api,redirect_stderr(io.StringIO()):
            with self.assertRaises(InputBudgetError) as caught:translate_batch([item],{},dict(self.cfg,num_ctx=300))
        api.assert_not_called()
        error=json.loads(Path(caught.exception._rpt_error_file).read_text(encoding='utf-8'))
        self.assertEqual(error['items'][0]['id'],'0')
        self.assertGreater(error['context_budget']['prompt_content_tokens'],300)
        self.assertIn('not truncated',error['error'])

    def test_total_window_keeps_source_and_crops_context_edges(self):
        ctx={'source_passage':[{'speaker':'a','text':'x'*4000},{'target_id':'0'},{'speaker':'b','text':'y'*4000}]}
        build=lambda c:{'messages':[{'role':'user','content':json.dumps(c)+'\nSOURCE'}],'options':{'num_ctx':4096,'num_predict':1800}}
        with patch('request_budget.counter',return_value=ExactCounter()):result,meta=fit(ctx,self.cfg,build)
        self.assertLessEqual(meta['prompt_content_tokens']+1800+128,4096)
        self.assertTrue(result['messages'][0]['content'].endswith('SOURCE'))
        build=lambda c:{'messages':[{'role':'user','content':'z'*5000}],'options':{'num_ctx':4096,'num_predict':1800}}
        with patch('request_budget.counter',return_value=ExactCounter()),self.assertRaises(InputBudgetError):fit({},self.cfg,build)

    def test_tokenizer_only_uses_owned_log_and_loads_without_generation(self):
        path=self.project/'owned.log';path.write_text('runner --port 12345 --offline')
        def fake(endpoint,route,payload):
            if route=='/api/generate':
                self.assertEqual(payload['prompt'],'');self.assertEqual(payload['options']['num_ctx'],16384);return {}
            self.assertEqual(endpoint,'http://127.0.0.1:12345');return {'tokens':[1,2,3]}
        with patch('request_budget.post',side_effect=fake) as api:
            measure=Counter(dict(self.cfg,_runtime_log=str(path)))
            self.assertEqual(measure.count('Hello.'),3);self.assertEqual(measure.method,'owned_model_tokenizer')
            self.assertEqual(api.call_args.args[1],'/tokenize')
        with patch('request_budget.post') as api:Counter(self.cfg);api.assert_not_called()

    def test_existing_8k_cache_survives_16k_and_changed_context(self):
        data=self.project/'data';data.mkdir()
        (data/'catalog.json').write_text(json.dumps(self.rows),encoding='utf-8')
        old=dict(self.cfg,num_ctx=8192)
        saved=[dict(id=r['id'],source=r['source'],text='기존 번역.',model='fake',fingerprint=fingerprint(old)) for r in self.rows]
        path=data/'translations.jsonl';path.write_text('\n'.join(json.dumps(r,ensure_ascii=False) for r in saved),encoding='utf-8');before=path.read_bytes()
        cfg=dict(self.cfg,_reuse_completed=True,_reuse_previous_models=True)
        self.assertEqual(len(cache(self.project,cfg)),4);self.assertEqual(path.read_bytes(),before)

    def test_detailed_validation_error_and_trace_include_reason(self):
        stage('translation',self.project,self.cfg)
        with patch('translation.request',return_value={'message':{'content':'{}'}}),redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            with self.assertRaises(BatchTranslationError) as raised:translate_batch(self.rows[:1],{},self.cfg)
        error=json.loads(Path(raised.exception._rpt_error_file).read_text(encoding='utf-8'))
        self.assertEqual(error['items'][0]['id'],'0');self.assertIn('traceback',error)
        self.assertTrue(error['validation_failures']);self.assertEqual(error['settings']['num_ctx'],16384)
        events=[json.loads(line) for line in (self.project/'data/translation-requests.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertIn('context_budget',events[0]);self.assertEqual(error['request_id'],events[0]['request_id'])


if __name__=='__main__':unittest.main()
