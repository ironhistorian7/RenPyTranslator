"""Synthetic end-to-end minor repair; no game or model access."""
from contextlib import nullcontext, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine import save_json
import failed_repair
import translation


class RpgMinorRepairTests(unittest.TestCase):
    def test_second_attempt_uses_clean_full_source(self):
        row=dict(id='x',source='こんにちは 世界。',kind='dialogue',usage='dialogue',
                 file='data/Map001.json',block='synthetic')
        draft='충분히 긴 한국어 문장에 일본어表現이 남아 있습니다.'
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp)
            cfg=dict(engine='rpgmaker',language='korean',source_language='japanese',
                     model='invented-hy',endpoint='http://127.0.0.1:1',num_ctx=16384)
            entry=dict(row,text=draft,model=cfg['model'],_rpg_residual_language=True)
            save_json(project/'data/catalog.json',[row])
            seen=[]
            def fake(batch,context,settings,note):
                seen.append((settings,note))
                raise ValueError('synthetic failure')
            with (patch('failed_repair.read_catalog',return_value=[row]),
                  patch('failed_repair.cache',return_value={'x':entry}),
                  patch('failed_repair.display_metadata',return_value={'names':{}}),
                  patch('source_context.SourceContext') as context,
                  patch('hy_backend.is_hy',return_value=True),
                  patch('hy_backend.runtime',side_effect=lambda c:nullcontext(c)),
                  patch('model_runtime.model_session',return_value=nullcontext()),
                  patch('failed_repair.translate_batch',side_effect=fake),
                  redirect_stdout(io.StringIO())):
                context.return_value.context.return_value={}
                changed,report=failed_repair.run(project,cfg)
            self.assertFalse(changed)
            self.assertEqual(report['model_calls'],2)
            self.assertEqual(len(seen),2)
            self.assertTrue(seen[1][0]['_rpg_clean_retry'])
            self.assertFalse(seen[1][0]['_rpg_correction_enabled'])
            self.assertEqual(seen[1][0]['_rpg_retry_errors'],{})
            self.assertNotIn('_rpg_validated_draft',seen[1][0])
            self.assertEqual(seen[1][1],'')

    def test_lone_residual_retries_full_source_for_both_protocols(self):
        row=dict(id='x',source=r'こんにちは \V[1] 世界。',kind='dialogue',
                 usage='dialogue',file='data/Map001.json',block='synthetic')
        draft=r'이 문장은 충분히 긴 한국어 문장입니다 \V[1] カ 남았습니다.'
        for model in ('invented-hy','invented-generic'):
            with self.subTest(model=model), tempfile.TemporaryDirectory() as temp:
                project=Path(temp)
                cfg=dict(engine='rpgmaker',language='korean',source_language='japanese',
                         model=model,endpoint='http://127.0.0.1:1',num_ctx=16384)
                entry=dict(row,text=draft,model=model,_rpg_residual_language=True)
                save_json(project/'data/catalog.json',[row])
                calls=[]
                def response(endpoint,route,body,**kwargs):
                    calls.append(body)
                    text='안녕하세요 ⟦RPT000⟧ 세계.' if model=='invented-hy' else '안녕하세요 <rpt000/> 세계.'
                    return {'message':{'content':'[0] '+text if model=='invented-hy' else text},'done_reason':'stop'}
                with (patch('failed_repair.read_catalog',return_value=[row]),
                      patch('failed_repair.cache',return_value={'x':entry}),
                      patch('failed_repair.display_metadata',return_value={'names':{}}),
                      patch('source_context.SourceContext') as context,
                      patch('hy_backend.is_hy',side_effect=lambda c:c['model']=='invented-hy'),
                      patch('hy_backend.runtime',side_effect=lambda c:nullcontext(c)),
                      patch('model_runtime.model_session',return_value=nullcontext()),
                      patch('translation.runtime_status',return_value={}),
                      patch('translation.trace_event'),patch('diagnostics.stage'),
                      patch('diagnostics.report'),patch('translation.request',side_effect=response),
                      patch('request_budget.post',side_effect=AssertionError('network prohibited')),
                      redirect_stdout(io.StringIO())):
                    context.return_value.context.return_value={}
                    changed,report=failed_repair.run(project,cfg)
                self.assertEqual(changed,{'x'},report)
                self.assertEqual(report['model_calls'],1)
                self.assertEqual(len(calls),1)
                user=calls[0]['messages'][-1]['content']
                source=user.split('[Source Text]\n',1)[1]
                self.assertNotIn('カ',source)
                self.assertIn('こんにちは',source)
                saved=json.loads((project/'data/recovered-translations.json').read_text(encoding='utf-8'))
                self.assertEqual(saved['x']['before'],draft)
                self.assertEqual(saved['x']['text'],r'안녕하세요 \V[1] 세계.')


if __name__=='__main__':unittest.main()
