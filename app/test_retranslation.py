"""Temporary synthetic projects only; no game, model or user project access."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine import save_json
from retranslation import comparison, complete, read


class RetranslationTests(unittest.TestCase):
    def fixture(self, root):
        p = root / 'projects/Example'
        (p / 'staging/game').mkdir(parents=True)
        (p / 'data/templates').mkdir(parents=True)
        (p / 'staging/game/script.rpy').write_text('original', encoding='utf-8')
        (p / 'data/templates/test.rpy').write_text('template', encoding='utf-8')
        cfg = {'source': str(root / 'DO-NOT-READ'), 'model': 'old:model', 'language': 'korean', 'automatic_version': 1}
        save_json(p / 'project.json', cfg)
        save_json(p / 'data/catalog.json', [{'id': 'a', 'source': 'Hello', 'kind': 'dialogue'}])
        save_json(p / 'data/catalog-format.json', {'version': 2})
        save_json(p / 'data/static-screen-literals.json', [])
        (p / 'data/translations.jsonl').write_text(json.dumps({'id':'a','source':'Hello','text':'old','model':'old:model','fingerprint':'old'})+'\n', encoding='utf-8')
        for name in ('recovered-translations.json','reviewed-translations.json','name-edits.json','term-memory.json','output-location.json'):
            save_json(p / 'data' / name, {'old': True})
        save_json(p / 'replacements.json', {'replacements': {}})
        save_json(p / 'names.json', {'overrides': {'India': '인디아'}})
        return p, cfg

    def test_fresh_independent_cache_outputs_and_resume(self):
        from translation import cache
        from output_paths import destination, apply_options
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);p,cfg=self.fixture(root)
            oldcache=(p/'data/translations.jsonl').read_bytes()
            with patch('output_paths.ROOT',root):
                original,_=destination(p,apply_options(p,cfg));original.mkdir(parents=True)
                (original/'keep.txt').write_text('old result')
                other=comparison(p,dict(cfg,model='new:model'))
                newcfg=read(other/'project.json',{})
                self.assertEqual(cache(other,newcfg),{})
                for name in ('recovered-translations.json','reviewed-translations.json','name-edits.json','term-memory.json','output-location.json'):
                    self.assertFalse((other/'data'/name).exists())
                out,_=destination(other,apply_options(other,newcfg))
                self.assertNotEqual(out,original)
                self.assertTrue(out.name.endswith('-kr'))
                self.assertIn('재번역',out.name)
                self.assertEqual(read(other/'names.json',{})['overrides']['India'],'인디아')
                # A completed item in this run must survive cancellation/retry.
                (other/'data/translations.jsonl').write_text(json.dumps({'id':'a','source':'Hello','text':'새 번역','model':'new:model','fingerprint':'new'})+'\n',encoding='utf-8')
                self.assertEqual(comparison(p,dict(cfg,model='new:model')),other)
                self.assertEqual(cache(other,dict(newcfg,_reuse_completed=True))['a']['text'],'새 번역')
                (other/'staging/game/script.rpy').write_text('changed')
                self.assertEqual((p/'staging/game/script.rpy').read_text(),'original')
                self.assertEqual((p/'data/translations.jsonl').read_bytes(),oldcache)
                self.assertEqual((original/'keep.txt').read_text(),'old result')
                different=comparison(p,dict(cfg,model='third:model'))
                self.assertNotEqual(different,other)
                complete(other)
                self.assertNotEqual(comparison(p,dict(cfg,model='new:model')),other)

    def test_interrupted_copy_retries_without_inheriting_old_cache(self):
        with tempfile.TemporaryDirectory() as t:
            p,cfg=self.fixture(Path(t))
            with patch('retranslation.clone',side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):comparison(p,cfg)
            path=(p/read(p/'data/retranslation-runs.json',{})[cfg['model']]).resolve()
            self.assertEqual(read(path/'data/retranslation.json',{})['status'],'copying')
            self.assertEqual(comparison(p,cfg),path)
            self.assertFalse((path/'data/translations.jsonl').exists())

    def test_main_routing_full_pipeline_and_hints_use_new_project(self):
        import translate_game as cli
        with tempfile.TemporaryDirectory() as t:
            p,cfg=self.fixture(Path(t));called=[]
            def translated(project, settings):
                self.assertNotEqual(project,p)
                self.assertEqual(settings['model'],'new:model')
                self.assertFalse((project/'data/translations.jsonl').exists())
                called.append(project)
            with patch('model_store.require_selected',return_value='new:model'), \
                 patch('translate_game.translate',side_effect=translated), \
                 patch('name_translation.run'),patch('failed_repair.run'), \
                 patch('translate_game.rebuild') as rebuild,patch('story_guides.run') as guides, \
                 contextlib.redirect_stdout(io.StringIO()):
                cli.main(['tasks','--tasks','retranslate','answers','routes','--project',str(p)])
            self.assertEqual(len(called),1)
            self.assertEqual(rebuild.call_args.args[0],called[0])
            self.assertEqual(guides.call_args.args[0],called[0])
            self.assertEqual(read(called[0]/'data/retranslation.json',{})['status'],'complete')

    def test_task_contract_and_no_model_gate_bypass(self):
        from task_plan import plan,arguments
        from model_store import needs_model
        self.assertEqual(plan(['retranslate','names','font','routes']),['retranslate','routes'])
        self.assertEqual(plan(['run','failed','answers']),['run','answers'])
        with self.assertRaises(ValueError):plan(['run','retranslate'])
        self.assertTrue(needs_model(['retranslate']))
        self.assertIn('retranslate',arguments(['retranslate'],'synthetic'))
        self.assertIn('--source',arguments(['retranslate'],'synthetic',source=True))
        self.assertEqual(plan(['run','language']),['run'])
        self.assertEqual(plan(['retranslate','language','routes']),['retranslate','routes'])

    def test_original_source_retranslation_only_runs_the_comparison_project(self):
        import translate_game as cli
        from automatic import source_key
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);project,cfg=self.fixture(root);source=root/'InventedGame'
            (source/'game').mkdir(parents=True);(source/'renpy').mkdir()
            cfg=dict(cfg,source=str(source));save_json(project/'project.json',cfg)
            save_json(root/'projects/index.json',{source_key(source.resolve()):project.name})
            original_cache=(project/'data/translations.jsonl').read_bytes();called=[]
            def translated(target,active):
                self.assertNotEqual(target,project)
                self.assertEqual(active['model'],'new:model')
                self.assertFalse((target/'data/translations.jsonl').exists())
                called.append(target)
            with patch('automatic.ROOT',root),patch('model_store.require_selected',return_value='new:model'), \
                 patch('gpu_policy.configure',side_effect=lambda active:active), \
                 patch('translate_game.translate',side_effect=translated), \
                 patch('translate_game.prepare',side_effect=AssertionError('Prepared source should not prepare again')), \
                 patch('name_translation.run'),patch('failed_repair.run'), \
                 patch('translate_game.rebuild') as rebuild,patch('story_guides.run') as guides, \
                 patch('language_panel.export',side_effect=AssertionError('Panel already included in translation')), \
                 contextlib.redirect_stdout(io.StringIO()):
                cli.main(['tasks','--tasks','retranslate','language','answers','routes','--source',str(source)])
            self.assertEqual(len(called),1)
            self.assertEqual(rebuild.call_args.args[0],called[0])
            self.assertEqual(guides.call_args.args[0],called[0])
            self.assertEqual((project/'data/translations.jsonl').read_bytes(),original_cache)
            self.assertEqual(read(called[0]/'data/retranslation.json',{})['status'],'complete')

    def test_new_source_retranslation_never_falls_back_to_an_original_run(self):
        import translate_game as cli
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'NewInventedGame'
            (source/'game').mkdir(parents=True);(source/'renpy').mkdir()
            with patch('automatic.ROOT',root),patch('model_store.require_selected',return_value='new:model'), \
                 patch('translate_game.translate',side_effect=AssertionError('No translation fallback')) as translate, \
                 patch('translate_game.prepare',side_effect=AssertionError('No preparation fallback')) as prepare, \
                 patch('name_translation.run',side_effect=AssertionError('No name model')) as names, \
                 patch('translate_game.rebuild',side_effect=AssertionError('No original rebuild')) as rebuild, \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(ValueError,'재번역할 준비 자료가 없습니다'):
                    cli.main(['tasks','--tasks','retranslate','--source',str(source)])
            translate.assert_not_called();prepare.assert_not_called();names.assert_not_called();rebuild.assert_not_called()

    def test_cli_rejects_run_and_retranslation_together_before_any_work(self):
        import translate_game as cli
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'NewInventedGame'
            (source/'game').mkdir(parents=True);(source/'renpy').mkdir()
            with patch('model_store.require_selected',return_value='new:model'), \
                 patch('translate_game.resolve_project',side_effect=AssertionError('No project work')), \
                 patch('translate_game.translate',side_effect=AssertionError('No translation')), \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(ValueError,'하나만 선택'):
                    cli.main(['tasks','--tasks','run','retranslate','--source',str(source)])

    def test_fresh_run_calls_translator_but_resume_does_not(self):
        from translation import translate, fingerprint, read_catalog
        with tempfile.TemporaryDirectory() as t:
            p,cfg=self.fixture(Path(t));other=comparison(p,cfg)
            cfg=dict(read(other/'project.json',{}),_reuse_completed=True,_reuse_previous_models=True)
            def reply(rows,context,active,note):
                return ([dict(r,text='새 번역',model=active['model'],fingerprint=fingerprint(active)) for r in rows],{'seconds':0,'items':len(rows)})
            with patch('hy_backend.runtime',side_effect=lambda active:contextlib.nullcontext(active)) as runtime, \
                 patch('name_hints.ensure_metadata',return_value={'names':{}}), \
                 patch('source_context.SourceContext') as context, \
                 patch('translation.translate_batch',side_effect=reply) as request, \
                 contextlib.redirect_stdout(io.StringIO()):
                context.return_value.context.return_value=[]
                translate(other,cfg)
                self.assertEqual(request.call_count,1)
                self.assertEqual(runtime.call_count,1)
                translate(other,cfg)
                self.assertEqual(request.call_count,1)
                self.assertEqual(runtime.call_count,1)

    def test_direct_command_failure_keeps_pending_comparison(self):
        import translate_game as cli
        with tempfile.TemporaryDirectory() as t:
            p,cfg=self.fixture(Path(t))
            with patch('model_store.require_selected',return_value='new:model'), \
                 patch('translate_game.translate',side_effect=RuntimeError('interrupted')), \
                 patch('name_translation.run'),contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError,'interrupted'):
                    cli.main(['retranslate','--project',str(p)])
            other=(p/read(p/'data/retranslation-runs.json',{})['new:model']).resolve()
            self.assertEqual(read(other/'data/retranslation.json',{})['status'],'pending')
            self.assertEqual(comparison(p,dict(cfg,model='new:model')),other)

    def test_legacy_registry_not_reassigned_to_comparison(self):
        from automatic import resolve_project
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);p,cfg=self.fixture(root)
            save_json(root/'projects/index.json',{'original':'Example'})
            other=comparison(p,cfg)
            with patch('automatic.ROOT',root):resolve_project(config=other/'project.json',model=cfg['model'])
            self.assertEqual(read(root/'projects/index.json',{}),{'original':'Example'})


if __name__=='__main__':unittest.main()
