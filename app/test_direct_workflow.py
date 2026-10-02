import io
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from automatic import settings
from engine import save_json
from model_runtime import model_session, track_request, unload_model
from translation import translate, fingerprint, cache, request
import translate_game

CFG={'language':'korean','model':'fake-model','endpoint':'http://127.0.0.1:11434','num_ctx':4096,'batch_size':1}

class ModelLifecycleTests(unittest.TestCase):
    def test_success_error_and_cancel_all_unload_only_used_model(self):
        for problem in (None,RuntimeError('failed'),KeyboardInterrupt()):
            with self.subTest(problem=type(problem).__name__),patch('model_runtime.unload_model') as stop:
                try:
                    with model_session():
                        for _ in range(2):
                            body=track_request(CFG['endpoint'],'/api/chat',{'model':CFG['model'],'keep_alive':'10m'})
                            self.assertEqual(body['keep_alive'],'30s')
                        if problem:raise problem
                except (RuntimeError,KeyboardInterrupt) as exc:self.assertIs(exc,problem)
                stop.assert_called_once_with(CFG['endpoint'],CFG['model'])

    def test_offline_command_does_not_contact_ollama(self):
        with patch('model_runtime.unload_model') as stop:
            with model_session():pass
            stop.assert_not_called()

    def test_unload_failure_preserves_original_exception(self):
        with patch('model_runtime.unload_model',side_effect=TimeoutError('offline')):
            with self.assertRaisesRegex(RuntimeError,'original error'):
                with model_session():
                    track_request(CFG['endpoint'],'/api/chat',{'model':CFG['model']})
                    raise RuntimeError('original error')

    def test_unload_uses_zero_keepalive_and_short_timeout(self):
        opener=Mock();opener.open.return_value=io.BytesIO(b'{"done":true,"done_reason":"unload"}')
        with patch('model_runtime.urllib.request.build_opener',return_value=opener):
            unload_model(CFG['endpoint'],CFG['model'])
        req=opener.open.call_args.args[0]
        self.assertEqual(req.full_url,CFG['endpoint']+'/api/generate')
        self.assertEqual(json.loads(req.data),{'model':CFG['model'],'keep_alive':0,'stream':False})
        self.assertEqual(opener.open.call_args.kwargs['timeout'],15)

    def test_request_is_tracked_even_if_transport_is_interrupted(self):
        opener=Mock();opener.open.side_effect=KeyboardInterrupt()
        with patch('translation.urllib.request.build_opener',return_value=opener),patch('model_runtime.unload_model') as stop:
            with self.assertRaises(KeyboardInterrupt):
                with model_session():request(CFG['endpoint'],'/api/chat',{'model':CFG['model']})
            stop.assert_called_once_with(CFG['endpoint'],CFG['model'])

class DirectWorkflowTests(unittest.TestCase):
    def test_default_settings_dont_read_analysis_files(self):
        with patch.object(Path,'read_text',side_effect=AssertionError('No analysis reads')):
            cfg=settings(Path('unused'),dict(CFG,_scenes={'bad':'large'},analysis_digest='old'))
        self.assertNotIn('_scenes',cfg)
        self.assertNotIn('analysis_digest',cfg)
        self.assertTrue(cfg['_reuse_completed'])

    def test_run_bypasses_analysis_and_model_review(self):
        for problem in (None,RuntimeError('failed'),KeyboardInterrupt()):
            with tempfile.TemporaryDirectory() as temp:
                p=Path(temp)
                (p/'staging/game').mkdir(parents=True)
                (p/'data/templates').mkdir(parents=True)
                for name in ('catalog.json','catalog-format.json','static-screen-literals.json'):
                    save_json(p/'data'/name,{})
                def fake_translate(project,cfg):
                    track_request(cfg['endpoint'],'/api/chat',{'model':cfg['model']})
                    if problem:raise problem
                with patch('sys.argv',['translate_game.py','run','--source','unused']), \
                     patch('model_store.require_selected',return_value=CFG['model']), \
                     patch('translate_game.resolve_project',return_value=(p,CFG)), \
                     patch('translate_game.verify_source'), \
                     patch('translate_game.analyze',side_effect=AssertionError('No pre-analysis')) as analyze, \
                     patch('translate_game.review',side_effect=AssertionError('No second model pass')) as review, \
                     patch('translate_game.translate',side_effect=fake_translate) as translator, \
                     patch('translate_game.rebuild') as build, \
                     patch('model_runtime.unload_model') as stop:
                    try:translate_game.main()
                    except (RuntimeError,KeyboardInterrupt) as exc:self.assertIs(exc,problem)
                    analyze.assert_not_called();review.assert_not_called()
                    translator.assert_called_once()
                    self.assertEqual(build.call_count,0 if problem else 1)
                    stop.assert_called_once_with(CFG['endpoint'],CFG['model'])

    def test_translation_does_not_promote_ui_labels_to_dialogue_terms(self):
        rows=[dict(id='1',source='Nova',kind='string',file='story.rpy',block='strings'),
              dict(id='2',source='Nova returns.',kind='dialogue',file='story.rpy',block='start')]
        calls=[]
        def fake(batch,context,cfg,retry_note):
            calls.append((batch,context,cfg))
            r=batch[0];text='노바' if r['id']=='1' else '노바가 돌아온다.'
            return [dict(id=r['id'],source=r['source'],text=text,model=cfg['model'],fingerprint=fingerprint(cfg))],{'seconds':0}
        with tempfile.TemporaryDirectory() as temp,patch('hy_backend.runtime',side_effect=lambda cfg:nullcontext(cfg)):
            p=Path(temp);save_json(p/'data/catalog.json',rows)
            cfg=settings(p,CFG)
            with patch('translation.translate_batch',side_effect=fake),patch('translation.read_catalog',wraps=lambda _:rows) as read:
                translate(p,cfg)
                self.assertEqual(read.call_count,2,'Catalog must not be reread for every batch')
            self.assertEqual(len(calls),2)
            self.assertNotIn('scene_analysis',calls[1][1])
            self.assertEqual(calls[1][1],{},'UI strings are not dialogue context')
            self.assertEqual(calls[1][2]['_batch_glossary'],{})
            with patch('translation.translate_batch',side_effect=AssertionError('Must reuse translations')):
                translate(p,cfg)

    def test_old_same_model_translations_reused_without_old_analysis(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);rows=[{'id':'1','source':'Hello.','kind':'dialogue'}]
            save_json(p/'data/catalog.json',rows)
            stored={'id':'1','source':'Hello.','text':'안녕.','model':CFG['model'],'fingerprint':'old-analysis-fingerprint'}
            (p/'data/translations.jsonl').write_text(json.dumps(stored)+'\n',encoding='utf-8')
            self.assertEqual(cache(p,settings(p,CFG))['1']['text'],'안녕.')
            self.assertNotIn('1',cache(p,settings(p,dict(CFG,model='different-model'))))

if __name__=='__main__':unittest.main()
