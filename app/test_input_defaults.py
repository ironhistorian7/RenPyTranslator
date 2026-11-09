"""Regressions for question echoes; small fixtures, no game/model execution."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from contextlib import nullcontext
from engine import save_json
from input_defaults import usable,validated,translate
from name_hints import discover
from name_translation import install_runtime


class InputDefaultsTests(unittest.TestCase):
    def fixture(self):
        script='''default relation = "tenant"
default owner = "landlord"
$ relation = renpy.input("They are my ... (singular)", default=relation)
$ owner = renpy.input("I'm their ... (singular)", default=owner)
if relation == "daughter":
    pass
$ relation = renpy.input("They are my ... (singular)", default=relation)
$ owner = renpy.input("I'm their ... (singular)", default=owner)
'''
        metadata=discover({'fixture.rpy':script},[])
        prompts={'They are my ... (singular)':'그들은 제 ...입니다 (단수).',"I'm their ... (singular)":'저는 그들의 ...입니다.'}
        return metadata,prompts

    def test_relation_branch_is_not_a_puzzle_and_questions_are_not_values(self):
        metadata,prompts=self.fixture()
        self.assertEqual([e['kind'] for e in metadata['inputs']],['text']*4)
        item=metadata['inputs'][0]
        for bad in ('그들은 제 ...입니다.','그들은 제 것입니다.','그들은 제 …입니다 (단수).'):
            self.assertFalse(usable(item,bad,prompts))
        self.assertTrue(usable(item,'세입자',prompts))
        self.assertTrue(usable(dict(default='hello',prompt='Greeting?'),'안녕하세요',{}))
        self.assertTrue(usable(dict(default='I am a guest.',prompt='Message?'),'저는 손님입니다.',{}))
        self.assertEqual(discover({'s.rpy':'$ answer = renpy.input("Password?", default="secret")'},[])['inputs'][0]['kind'],'answer')

    def test_migrate_bad_cache_group_duplicates_retry_only_bad_and_reuse(self):
        metadata,prompts=self.fixture();entries=metadata['inputs']
        stale={e['key']:dict(source=e['default'],text='그들은 제 ...입니다.' if e['variable']=='relation' else '저는 그들의 ...입니다.',kind=e['kind'],variable=e['variable']) for e in entries}
        # One location already has the correct owner translation.
        stale[entries[-1]['key']]['text']='집주인'
        requests=[]
        def ask(prompt,schema):
            payload=json.loads(prompt.rsplit('\n',1)[1]);requests.append(payload)
            self.assertEqual([v['text'] for v in payload.values()],['tenant'])
            if len(requests)==1:return {'0':'그들은 제 ...입니다.'}
            self.assertNotIn('context',payload['0'])
            return {'0':'세입자'}
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);report={'issues':[]}
            save_json(p/'data/name-hints.json',metadata)
            save_json(p/'data/name-input-prompts.json',prompts)
            result=translate(p,entries,stale,prompts,{},ask,report)
            self.assertEqual(len(requests),2)
            self.assertEqual([result[e['key']]['text'] for e in entries],['세입자','집주인','세입자','집주인'])
            install_runtime(p,{'language':'korean'})
            payload=json.loads((p/'staging/game/tl/korean/_rpt_name_inputs.json').read_text(encoding='utf-8'))
            self.assertEqual(payload['variables']['relation']['values'],{'tenant':'세입자'})
            self.assertEqual(payload['variables']['owner']['values'],{'landlord':'집주인'})
            translate(p,entries,result,prompts,{},Mock(side_effect=AssertionError('No repeat calls')),report)

    def test_failed_retry_never_reinstalls_question_or_orphan_record(self):
        metadata,prompts=self.fixture();entries=metadata['inputs']
        stale={e['key']:dict(source=e['default'],text=prompts[e['prompt']],kind=e['kind'],variable=e['variable']) for e in entries}
        stale['old-key']=dict(source='tenant',text='다른 질문입니다.',kind='text',variable='relation')
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);report={'issues':[]}
            save_json(p/'data/name-hints.json',metadata);save_json(p/'data/name-input-prompts.json',prompts)
            save_json(p/'data/input-defaults.json',stale)
            install_runtime(p,{'language':'korean'})
            payload=json.loads((p/'staging/game/tl/korean/_rpt_name_inputs.json').read_text(encoding='utf-8'))
            self.assertEqual(payload['variables']['relation']['values'],{})
            ask=Mock(return_value={'0':'그들은 제 ...입니다.','1':'저는 그들의 ...입니다.'})
            result=translate(p,entries,stale,prompts,{},ask,report)
            self.assertEqual(result,{})
            self.assertEqual(ask.call_count,2)
            self.assertEqual(len(report['issues']),2)

    def test_conflicting_valid_values_do_not_depend_on_entry_order(self):
        metadata,prompts=self.fixture();entries=metadata['inputs'][::2]
        records={e['key']:dict(source='tenant',text=t) for e,t in zip(entries,['세입자','임차인'])}
        self.assertEqual(validated(entries,records,prompts),{})
        self.assertEqual(validated(list(reversed(entries)),records,prompts),{})

    def test_existing_names_fix_migrates_only_bad_value_preserving_dialogue_cache(self):
        from name_translation import run
        from translation import fingerprint
        script='''default relation = "tenant"
$ relation = renpy.input("They are my ... (singular)", default=relation)
if relation == "daughter":
    pass
'''
        metadata=discover({'s.rpy':script},[]);entry=metadata['inputs'][0]
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);game=p/'staging/game';game.mkdir(parents=True)
            (game/'s.rpy').write_text(script,encoding='utf-8')
            cfg=dict(language='korean',model='fake',endpoint='http://localhost')
            row=dict(id='scene',source='My two [relation]s are there.',kind='dialogue',file='s.rpy')
            save_json(p/'data/catalog.json',[row])
            cache=p/'data/translations.jsonl'
            cache.write_text(json.dumps(dict(row,text='제 두 [relation]가 거기 있어요.',model='fake',fingerprint=fingerprint(cfg)))+'\n',encoding='utf-8')
            before=cache.read_bytes()
            save_json(p/'data/name-input-prompts.json',{entry['prompt']:'그들은 제 ...입니다 (단수).'})
            save_json(p/'data/input-defaults.json',{entry['key']:dict(source='tenant',text='그들은 제 ...입니다.',kind='text',variable='relation')})
            def request(endpoint,route,body):
                payload=json.loads(body['messages'][0]['content'].rsplit('\n',1)[1])
                self.assertEqual([v['text'] for v in payload.values()],['tenant'])
                return {'message':{'content':'{"0":"세입자"}'}}
            with patch('hy_backend.runtime',return_value=nullcontext(cfg)),patch('name_translation.request',side_effect=request):
                changed,report=run(p,cfg)
            self.assertEqual(changed,{'scene'});self.assertEqual(report['model_calls'],1)
            install_runtime(p,cfg)
            payload=json.loads((game/'tl/korean/_rpt_name_inputs.json').read_text(encoding='utf-8'))
            self.assertEqual(payload['variables']['relation']['values'],{'tenant':'세입자'})
            self.assertEqual(cache.read_bytes(),before)
            with patch('hy_backend.runtime',side_effect=AssertionError('No repeat model calls')):
                changed,report=run(p,cfg)
            self.assertEqual(report['model_calls'],0);self.assertEqual(cache.read_bytes(),before)


if __name__=='__main__':unittest.main()
