import ast
from contextvars import copy_context
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from display_text import attach_josa, compose, reference_text
from engine import save_json
from hy_backend import DEFAULT_MODEL, body, parse
from josa_runtime import rpt_josa
from model_runtime import model_session, track_request
from name_hints import discover, placeholder_hints
from translation import catalog, translate, translate_batch, cache, fingerprint, render, rendered_text


class NamesAndDisplayTests(unittest.TestCase):
    def test_static_defaults_input_and_fallback_without_execution(self):
        script = '''default player_name = "Alex"
default rival_name = ""
$ rival_name = renpy.input("Your name?", default="Robin").strip()
if not rival_name:
    $ rival_name = "Taylor"
define h = Character("[hero]")
$ hero = "Morgan"
default pet_name = dangerous_function()
default score = 10
menu:
    "Go home":
        jump home
screen prefs():
    textbutton "Save"
'''
        result=discover({'story.rpy':script},[{'source':'Hello [stranger_name], [score].'}])
        self.assertEqual(result['names']['player_name']['representative'],'Alex')
        self.assertEqual(result['names']['rival_name']['representative'],'Robin')
        self.assertEqual(result['names']['hero']['representative'],'Morgan')
        self.assertEqual(result['names']['pet_name']['representative'],'John')
        self.assertEqual(result['names']['stranger_name']['representative'],'John')
        self.assertNotIn('score',result['names'])
        self.assertEqual(result['choices'],['Go home'])

    def test_name_hint_is_attached_to_identity_marker_not_global_replacement(self):
        names={'player_name':{'representative':'Alex'}}
        self.assertEqual(placeholder_hints(['{i}','[player_name]','{/i}','[score]'],names),{'<rpt001/>':'Alex'})

    def test_josa_changes_with_runtime_value(self):
        for name,expected in [('민수','는'),('서연','은'),('John','은'),('Unknown','은(는)')]:
            self.assertEqual(rpt_josa(name,'은/는'),expected)
        self.assertEqual(rpt_josa('하늘','으로/로'),'로')
        self.assertEqual(rpt_josa('민수','으로/로'),'로')
        self.assertEqual(rpt_josa('서연','으로/로'),'으로')
        self.assertEqual(rpt_josa('{b}서연{/b}!','을/를'),'을')

    def test_particles_do_not_rewrite_nouns_or_unrelated_values(self):
        names={'name':{}}
        source='[name]은 [name] 씨는 [score]은 [name]은행 [name]이름 [name]으로는'
        result=attach_josa(source,names)
        self.assertEqual(result.count('rpt_josa('),2)
        self.assertIn('[name] 씨는',result)
        self.assertIn('[name]은행',result)
        self.assertEqual(attach_josa(result,names),result)
        self.assertIn("{/b}[rpt_josa(name, u'은/는')]",attach_josa('{b}[name]{/b}은',names))

    def test_bilingual_controls_reference_and_fallback(self):
        row={'kind':'dialogue','source':'{size=80}Hi [name].{/size}{w=1}{nw}'}
        out=compose(row,{'text':'{size=80}[name]은 안녕.{/size}{w=1}{nw}'},{'names':{'name':{}}})
        self.assertIn('\n{size=*0.55}{cps=0}Hi [name].{/cps}{/size}{nw}',out)
        self.assertEqual(out.count('{w=1}'),1)
        self.assertEqual(out.count('{nw}'),1)
        self.assertIn('rpt_josa',out)
        self.assertEqual(compose(row,{'text':row['source'],'status':'source_fallback'},{}),row['source'])

    def test_menu_bilingual_ui_single_and_unclosed_style(self):
        meta={'choices':['Go home'],'names':{}}
        self.assertIn('size=*0.55',compose({'kind':'string','source':'Go home'},{'text':'집에 가기'},meta))
        self.assertEqual(compose({'kind':'string','source':'Save'},{'text':'저장'},meta),'저장')
        result=compose({'kind':'dialogue','source':'{b}Hello'},{'text':'{b}안녕'},meta)
        self.assertIn('{/b}\n{size=*0.55}',result)
        self.assertEqual(reference_text('{{literal [[name] {p}next'),'{{literal [[name] \nnext')

    def test_render_rebuild_preserves_cache_and_package_verifies_final_display(self):
        from packaging import install_support, package
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp); game=p/'staging/game'; target=game/'tl/korean'; target.mkdir(parents=True)
            (game/'story.rpy').write_text('default player_name = "John"\n',encoding='utf-8')
            (target/'story.rpy').write_text('translate korean start_a:\n    # n "[player_name] left."\n    n ""\n',encoding='utf-8')
            # Match publish(): enable reference isolation before the first render.
            cfg={'language':'korean','model':'fake','_reference_guard':True}; rows=catalog(p,cfg)
            entry=dict(id=rows[0]['id'],source=rows[0]['source'],text='[player_name]은 떠났다.',model='fake',fingerprint=fingerprint(cfg))
            cache_path=p/'data/translations.jsonl'; cache_path.write_text(json.dumps(entry)+'\n',encoding='utf-8'); original=cache_path.read_bytes()
            save_json(p/'replacements.json',{'replacements':{'떠났다':'돌아왔다'}})
            render(p,cfg); install_support(p,cfg)
            displayed=rendered_text((target/'story.rpy').read_text(encoding='utf-8').splitlines()[2],rows[0])
            self.assertIn('존은 돌아왔다',displayed);self.assertIn('left.',displayed);self.assertNotIn('rpt_josa',displayed)
            before=(target/'story.rpy').read_bytes();render(p,cfg);self.assertEqual((target/'story.rpy').read_bytes(),before)
            self.assertEqual(cache_path.read_bytes(),original)
            support=(game/'zz_rpt_korean.rpy').read_text(encoding='utf-8')
            self.assertNotIn('textbutton',support);self.assertNotIn('K_F6',support)
            # Compile each generated Python block, including the exact embedded runtime helper.
            lines=support.splitlines(); block=[]
            for line in lines+['END']:
                if line and not line.startswith(' '):
                    if block:compile('\n'.join(block),'<support>','exec');block=[]
                    active=line.endswith('python:')
                elif locals().get('active') and line.startswith('    '):block.append(line[4:])
            save_json(p/'data/validation.json',{'errors':[]})
            with patch('packaging.verify_source',return_value={'files':0}):package(p,cfg)


class HyBackendTests(unittest.TestCase):
    def test_owned_server_stops_on_success_error_and_cancel(self):
        from hy_backend import runtime
        from unittest.mock import Mock
        for failure in (None, RuntimeError('test error'), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);exe=root/'runtimes/ollama-0.34.2/ollama.exe';exe.parent.mkdir(parents=True);exe.touch()
                proc=Mock();proc.poll.return_value=None;proc.pid=87654
                def api(endpoint,route):return {'models':[{'name':DEFAULT_MODEL}]} if route=='/api/tags' else {'version':'test'}
                with patch('engine.ROOT',root),patch('hy_backend.subprocess.Popen',return_value=proc), \
                     patch('native_process.run') as kill,patch('hy_backend.api',side_effect=api), \
                     patch('model_runtime.unload_model') as unload:
                    try:
                        with runtime({'model':DEFAULT_MODEL}) as cfg:
                            track_request(cfg['endpoint'],'/api/chat',{'model':DEFAULT_MODEL})
                            if failure:raise failure
                    except (RuntimeError,KeyboardInterrupt) as caught:self.assertIs(caught,failure)
                    unload.assert_called_once()
                    self.assertEqual(kill.call_args.args[0],['taskkill','/PID','87654','/T','/F'])

    def test_new_and_existing_settings_default_to_hy_without_erasing_cache(self):
        from automatic import resolve_project
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);save_json(p/'project.json',{'source':'unused','model':'qwen3:14b','automatic_version':1})
            (p/'data').mkdir();(p/'data/cache-sentinel').write_text('keep',encoding='utf-8')
            project,cfg=resolve_project(config=p/'project.json')
            self.assertEqual(cfg['model'],'qwen3:14b')
            self.assertTrue(cfg['_reuse_previous_models'])
            self.assertEqual((p/'data/cache-sentinel').read_text(encoding='utf-8'),'keep')
            _,custom=resolve_project(config=p/'project.json',model='custom-model')
            self.assertEqual(custom['model'],'custom-model')
            self.assertTrue(custom['_reuse_previous_models'])
            _,hy=resolve_project(config=p/'project.json',model=DEFAULT_MODEL)
            self.assertEqual(hy['num_ctx'],16384)
            self.assertEqual(hy['parallel'],2)

    def test_request_uses_single_user_and_no_json_constraint(self):
        request=body([{'id':'0','text':'Hi <rpt000/>','name_hints':{'<rpt000/>':'Alex'}}],{}, {'model':DEFAULT_MODEL},{},'')
        self.assertEqual([m['role'] for m in request['messages']],['user'])
        self.assertNotIn('format',request);self.assertNotIn('think',request)
        self.assertIn('Alex',request['messages'][0]['content'])
        self.assertEqual(parse('[0] 안녕\n[1] 다음\n줄'),{'0':'안녕','1':'다음\n줄'})
        self.assertEqual(parse('[0] 첫째\n[0] 중복\n[1] 둘째'),{'1':'둘째'})

    def test_hy_restores_names_and_drops_truncated_last_entry(self):
        from translation import BatchTranslationError
        rows=[dict(id='a',kind='dialogue',source='Hi [name]'),dict(id='b',kind='dialogue',source='Good bye')]
        cfg={'model':DEFAULT_MODEL,'endpoint':'http://localhost','_names':{'name':{'representative':'Alex'}}}
        response={'message':{'content':'[0] 안녕 <rpt000/>\n[1] 잘 가'},'done_reason':'length'}
        with patch('translation.request',return_value=response):
            with self.assertRaises(BatchTranslationError) as caught:translate_batch(rows,{},cfg)
        self.assertEqual(caught.exception.output[0]['text'],'안녕 [name]')
        self.assertEqual(caught.exception.failures[0]['row']['id'],'b')

    def test_two_requests_in_parallel_preserve_tracking_and_cache(self):
        barrier=threading.Barrier(2)
        def fake(rows,context,cfg,retry):
            track_request(cfg['endpoint'],'/api/chat',{'model':cfg['model']})
            barrier.wait(timeout=3)
            return [dict(id=r['id'],source=r['source'],text='안녕',model=cfg['model'],fingerprint=fingerprint(cfg)) for r in rows],{'seconds':0}
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp); rows=[dict(id=str(i),source='Hello',kind='dialogue',file='a.rpy') for i in range(2)]
            save_json(p/'data/catalog.json',rows)
            cfg={'model':'fake','endpoint':'http://localhost','parallel':2,'batch_size':1}
            with patch('hy_backend.runtime',return_value=nullcontext(cfg)),patch('translation.translate_batch',side_effect=fake),patch('model_runtime.unload_model') as unload:
                with model_session():translate(p,cfg)
            self.assertEqual(len(cache(p,cfg)),2)
            unload.assert_called_once_with('http://localhost','fake')

    def test_old_model_cache_can_be_reused_when_migrating_to_hy(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);row=dict(id='a',source='Hello',kind='dialogue',file='a.rpy')
            save_json(p/'data/catalog.json',[row])
            (p/'data/translations.jsonl').write_text(json.dumps(dict(row,text='안녕',model='old',fingerprint='old'))+'\n',encoding='utf-8')
            cfg={'model':DEFAULT_MODEL,'_reuse_completed':True,'_reuse_previous_models':True}
            with patch('hy_backend.subprocess.Popen',side_effect=AssertionError('No inference')):translate(p,cfg)
            self.assertEqual(cache(p,cfg)['a']['text'],'안녕')


if __name__=='__main__': unittest.main()
