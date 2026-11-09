"""Tiny invented inputs only; network/model calls are mocked."""
import json
from contextlib import nullcontext
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from engine import save_json
from name_hints import discover
from name_translation import run,mapping,apply_names,install_runtime,candidate_spans,replace_selected
from translation import fingerprint
from term_memory import TermMemory
from test_input_names import name_runtime

class SemanticInputTests(unittest.TestCase):
    def test_input_forms_and_semantic_types(self):
        script='''default player_name = "Alex"
$ player_name = renpy.input(prompt="Your name?", default="Alex")
$ nickname = renpy.input(
    prompt="What should I call you?",
    default="dude"
)
$ greeting = renpy.input("Your greeting?", "hello")
$ answer = renpy.input("Password?", "secret")
$ renpy.input(prompt="Leave a note.", default="hello")
'''
        data=discover({'s.rpy':script},[])
        self.assertEqual([e['kind'] for e in data['inputs']],['name','address','text','answer','text'])
        self.assertEqual(data['inputs'][1]['prompt'],'What should I call you?')
        self.assertEqual(data['inputs'][1]['default'],'dude')
        self.assertEqual(data['inputs'][-1]['variable'],'')
        self.assertEqual(data['display_variables']['greeting']['kind'],'text')

    def test_ui_and_quoted_speakers_never_become_automatic_dialogue_terms(self):
        rows=[{'id':'a','source':'Car','kind':'string'}, {'id':'b','source':'Car','kind':'dialogue','speaker_name':'Car'}]
        memory=TermMemory(rows,{'a':{'source':'Car','text':'카'}})
        self.assertEqual(memory.relevant([{'source':'my car'}]),{})
        rows[0].update(term_kind='general',term_approved=True)
        self.assertEqual(TermMemory(rows,{'a':{'source':'Car','text':'차'}}).relevant([{'source':'my car'}]),{'Car':'차'})

    def test_runtime_address_map_and_particle_use_same_final_spelling(self):
        ns,_=name_runtime()
        exec(Path(__file__).with_name('josa_runtime.py').read_text(encoding='utf-8'),ns)
        ns['_rpt_name_map']['Kenzie']='켄지'
        ns['_rpt_pronunciations']={'kenzie':'켄'}
        ns['_rpt_name_inputs']['variables']={'nickname':{'kind':'address','values':{'dude':'친구'}}}
        self.assertEqual(ns['rpt_josa']('Kenzie','이/가'),'가')
        self.assertEqual(ns['rpt_display_name']('dude','nickname'),'친구')
        self.assertEqual(ns['rpt_josa']('dude','이/가','nickname'),'가')
        get=ns['_rpt_name_formatter'].get_field
        self.assertEqual(get("rpt_display_name(nickname, u'nickname')",(),{'nickname':'dude'})[0],'친구')
        self.assertEqual(get("rpt_josa(nickname, u'이/가', u'nickname')",(),{'nickname':'dude'})[0],'가')

    def test_literal_name_particle_only_at_selected_person_position(self):
        text='켄지이 더 자세히 설명한다.'
        spans=candidate_spans('Kenzie explains.',text,{'Kenzie':'켄지'},{})
        self.assertEqual(replace_selected(text,spans,[0]),'켄지가 더 자세히 설명한다.')
        self.assertEqual(replace_selected(text,spans,[]),text)
        self.assertEqual(candidate_spans('A city explains.',text,{'Kenzie':'켄지'},{}),[])
        spans=candidate_spans('Kenzie explains.','켄이 설명한다.',{'Kenzie':'켄지'},{'Kenzie':['켄']})
        self.assertEqual(replace_selected('켄이 설명한다.',spans,[0]),'켄지가 설명한다.')

    def test_fix_reuses_completed_work_preserves_cache_and_targets_poisoned_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);game=p/'staging/game';game.mkdir(parents=True)
            script='''default player_name = "Kenzie"
default nickname = "dude"
$ nickname = renpy.input(prompt="What should I call you?", default="dude")
$ answer = renpy.input("Password?", default="secret")
$ greeting = renpy.input("Your greeting?", default="hello")
'''
            (game/'s.rpy').write_text(script,encoding='utf-8')
            rows=[{'id':'ui','kind':'string','source':'Car','file':'s.rpy'},
                  {'id':'sound','kind':'dialogue','speaker_name':'Car','source':'Vroom.','file':'s.rpy'},
                  {'id':'line','kind':'dialogue','source':'I found it on my car.','file':'s.rpy'},
                  {'id':'person','kind':'dialogue','source':'Kenzie explains.','file':'s.rpy'},
                  {'id':'other','kind':'dialogue','source':'Nothing happens.','file':'s.rpy'},
                  {'id':'both','kind':'dialogue','source':'Kenzie found it on my car.','file':'s.rpy'},
                  {'id':'greet','kind':'dialogue','source':'[greeting], everybody.','file':'s.rpy'}]
            cfg={'model':'fake','language':'korean','endpoint':'http://localhost'}
            save_json(p/'data/catalog.json',rows)
            raw=[dict(r,text=t,model='fake',fingerprint=fingerprint(cfg)) for r,t in zip(rows,['카','부릉.','제 카에서 찾았어요.','켄지이 설명한다.','아무 일도 없다.','켄지이 제 카에서 찾았어요.','[greeting], 여러분.'])]
            cache=p/'data/translations.jsonl';cache.write_text(''.join(json.dumps(r)+'\n' for r in raw),encoding='utf-8');before=cache.read_bytes()
            save_json(p/'data/name-transliterations.json',{'names':{'Car':'카','dude':'듀드','Kenzie':'켄지'},'sources':{'Car':[{'kind':'quoted speaker'}],'dude':[{'kind':'name default','variable':'nickname'}],'Kenzie':[{'kind':'name default','variable':'player_name'}]}})
            requests=[]
            def request(endpoint,path,body):
                prompt=body['messages'][0]['content'];requests.append(prompt)
                data=json.loads(prompt.rsplit('\n',1)[1])
                if '표시 명칭을 분류' in prompt: result={k:'object' for k in data}
                elif '입력 안내문' in prompt: result={k:('암호를 입력하세요.' if v=='Password?' else '어떻게 불러드릴까요?') for k,v in data.items()}
                elif '입력란 기본값' in prompt:
                    self.assertTrue(all(v['kind'] in ('address','text') for v in data.values()))
                    result={k:'친구' if v['kind']=='address' else '안녕' for k,v in data.items()}
                elif '잘못 적용된 인명 음역' in prompt:
                    self.assertTrue(all(v['source'] in ('Car','I found it on my car.','Kenzie found it on my car.') for v in data.values()))
                    result={k:v['translation'].replace('카','차') for k,v in data.items()}
                else: result={k:[0] for k in data}
                return {'message':{'content':json.dumps(result,ensure_ascii=False)}}
            with patch('hy_backend.runtime',return_value=nullcontext(cfg)),patch('name_translation.request',side_effect=request):
                changed,report=run(p,cfg)
            self.assertEqual(mapping(p),{'Kenzie':'켄지'})
            out=apply_names(p,rows,{r['id']:r for r in raw})
            self.assertEqual(out['line']['text'],'제 차에서 찾았어요.')
            self.assertEqual(out['person']['text'],'켄지가 설명한다.')
            self.assertEqual(out['both']['text'],'켄지가 제 차에서 찾았어요.')
            self.assertIn('greet',changed)
            self.assertNotIn('other',changed)
            self.assertEqual(cache.read_bytes(),before)
            install_runtime(p,cfg)
            inputs=json.loads((game/'tl/korean/_rpt_name_inputs.json').read_text(encoding='utf-8'))
            self.assertEqual(inputs['variables']['nickname']['values'],{'dude':'친구'})
            self.assertNotIn('secret',str(inputs['variables']['answer']['values']))
            with patch('hy_backend.runtime',side_effect=AssertionError('No repeat inference')):
                _,report=run(p,cfg)
            self.assertEqual(report['model_calls'],0)
            self.assertEqual(cache.read_bytes(),before)

if __name__=='__main__':unittest.main()
