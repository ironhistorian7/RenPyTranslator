"""Synthetic repair/translation scheduling; no real games, models or GPU calls."""
from contextlib import ExitStack,nullcontext,redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import rpgmaker_project as adapter
import translation
import translate_game  # Import aliases before per-test automatic.resolve_project patches.
from automatic import settings
from engine import save_json
from task_plan import plan


class TaskCompletionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.project=Path(self.temp.name)/'project';(self.project/'data/rpg-source').mkdir(parents=True)
        self.cfg=dict(source='DO-NOT-READ',engine='rpgmaker',source_language='english',language='korean',
                      model='synthetic',num_ctx=8192,batch_size=1,parallel=1,context_txt_enabled=False)
        self.rows=[dict(id=uid,source=source,kind='dialogue',usage='dialogue',file='synthetic',block='one')
                   for uid,source in [('kept','Kept.'),('failed','Failed.'),('pending','Pending.')]]
        save_json(self.project/'data/catalog.json',self.rows)
        save_json(self.project/'data/rpgmaker-project.json',{})
        self.cache=self.project/'data/translations.jsonl'

    def record(self,row,text=None,failed=False):
        item=dict(id=row['id'],source=row['source'],text=row['source'] if failed else (text or '보존된 대사.'),
                  model='synthetic',fingerprint=translation.fingerprint(self.cfg))
        if failed:item.update(status='source_fallback',error='no Korean translation')
        return item

    def populate(self,items):
        self.cache.write_text(''.join(json.dumps(item,ensure_ascii=False)+'\n' for item in items),encoding='utf-8')

    def run_main(self,jobs,*,batch,render=None,check=None):
        args=SimpleNamespace(command='tasks',tasks=jobs,source=None,project=self.project/'project.json',
            source_language=None,model='synthetic',runtime_id=None,output='',suffix='-kr',textbox_scale='default',show_reference=None)
        with ExitStack() as stack:
            stack.enter_context(redirect_stdout(io.StringIO()))
            stack.enter_context(patch('automatic.resolve_project',return_value=(self.project,dict(self.cfg))))
            stack.enter_context(patch('model_store.require_selected',return_value='synthetic'))
            stack.enter_context(patch('model_runtime.model_session',return_value=nullcontext()))
            stack.enter_context(patch('translate_game.project_lock',return_value=nullcontext()))
            stack.enter_context(patch('output_paths.apply_options',side_effect=lambda p,c,*a:c))
            stack.enter_context(patch('rpgmaker_project.recover_display_catalog',return_value={'added_ids':[]}))
            stack.enter_context(patch('rpgmaker_project.seed_defaults'))
            stack.enter_context(patch('rpgmaker_sources.refresh'))
            stack.enter_context(patch('rpgmaker_scope.refresh'))
            stack.enter_context(patch('gpu_policy.configure',side_effect=lambda cfg:cfg))
            stack.enter_context(patch('runtime_recovery.run',side_effect=lambda p,c,operation,count:operation(c)))
            stack.enter_context(patch('translation.translate_batch',side_effect=batch))
            renderer=stack.enter_context(patch('rpgmaker_project.render',side_effect=render))
            checker=stack.enter_context(patch('patch_validation.run',side_effect=check or (lambda *a,**k:{'status':'passed'})))
            adapter.main(args,SimpleNamespace(error=lambda message:(_ for _ in ()).throw(ValueError(message))),
                         {'engine':'rpgmaker','engine_label':'RPG Maker MZ'})
            return renderer.call_count,checker.call_count

    def test_run_failed_check_keeps_selection_and_executes_in_order(self):
        self.assertEqual(plan(['check','failed','run']),['run','failed','check'])
        # Only one row was ever translated before this job. Ordinary translation
        # processes missing rows; an explicit repair handles its remaining error.
        self.populate([self.record(self.rows[0])]);before=self.cache.read_bytes()
        calls=[];failed_calls=0
        def batch(rows,context,cfg,retry=''):
            nonlocal failed_calls
            self.assertEqual(len(rows),1);row=rows[0];calls.append(row['id'])
            if row['id']=='failed':
                failed_calls+=1
                if failed_calls<=3:
                    raise translation.BatchTranslationError([],[{'row':row,'error':'no Korean translation'}],{'seconds':0,'items':1})
            return [self.record(row,'새 번역.')],{'seconds':0,'items':1}
        def render(project,cfg,**kwargs):
            self.assertEqual(set(kwargs['prepared']['known']),{row['id'] for row in self.rows})
            self.assertEqual(kwargs['prepared']['missing'],[]);calls.append('render')
        def check(*args,**kwargs):calls.append('check');return {'status':'passed'}
        self.assertEqual(self.run_main(['run','failed','check'],batch=batch,render=render,check=check),(1,1))
        self.assertNotIn('kept',calls)
        self.assertEqual(calls.count('failed'),4);self.assertEqual(calls.count('pending'),1)
        self.assertEqual(calls[-2:],['render','check'])
        self.assertTrue(self.cache.read_bytes().startswith(before))
        result=json.loads((self.project/'data/rpg-task-status.json').read_text(encoding='utf-8'))
        self.assertEqual((result['status'],result['remaining'],result['check_status']),('completed',0,'passed'))

    def test_failed_only_saves_repair_and_skips_incomplete_output_and_check(self):
        self.populate([self.record(self.rows[0]),self.record(self.rows[1],failed=True)])
        before=self.cache.read_bytes();calls=[]
        stage=self.project/'staging/keep.json';stage.parent.mkdir();stage.write_bytes(b'existing translated staging')
        output=self.project/'output/keep.zip';output.parent.mkdir();output.write_bytes(b'existing output archive')
        def batch(rows,context,cfg,retry=''):
            calls.extend(row['id'] for row in rows)
            return [self.record(row,'복구된 대사.') for row in rows],{'seconds':0,'items':len(rows)}
        with patch('translation.cache',wraps=translation.cache) as cached:
            self.assertEqual(self.run_main(['check','failed'],batch=batch),(0,0))
        cached.assert_called_once()
        self.assertEqual(calls,['failed']);self.assertTrue(self.cache.read_bytes().startswith(before))
        self.assertEqual(stage.read_bytes(),b'existing translated staging')
        self.assertEqual(output.read_bytes(),b'existing output archive')
        result=json.loads((self.project/'data/rpg-task-status.json').read_text(encoding='utf-8'))
        self.assertEqual((result['status'],result['remaining'],result['cached']),('incomplete',1,2))
        self.assertEqual((result['output_status'],result['check_status']),('skipped','skipped'))
        check=json.loads((self.project/'data/patch-validation.json').read_text(encoding='utf-8'))
        self.assertEqual(check['status'],'skipped');self.assertEqual(check['reason'],'incomplete_translation')

    def test_run_failed_without_previous_cache_is_allowed(self):
        self.assertFalse(self.cache.exists())
        def batch(rows,context,cfg,retry=''):
            return [self.record(row,'새 번역.') for row in rows],{'seconds':0,'items':len(rows)}
        self.assertEqual(self.run_main(['run','failed','check'],batch=batch),(1,1))

    def test_render_rejects_incomplete_cache_before_staging_or_renderer_initialization(self):
        self.populate([self.record(self.rows[0])])
        stage=self.project/'staging/keep.json';stage.parent.mkdir();stage.write_bytes(b'keep existing staging')
        cfg=settings(self.project,self.cfg)
        with patch('rpgmaker_project.load',side_effect=AssertionError('No renderer initialization')),\
             patch('translation.cache',wraps=translation.cache) as cached:
            with self.assertRaises(adapter.IncompleteTranslationError) as caught:adapter.render(self.project,cfg)
        self.assertEqual(caught.exception.error_code,'incomplete_translation')
        self.assertEqual(caught.exception.remaining,2);cached.assert_called_once()
        self.assertEqual(stage.read_bytes(),b'keep existing staging')


if __name__=='__main__':unittest.main()
