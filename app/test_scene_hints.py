"""Short invented scripts; model and runtime are always mocked."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scene_hints import branches,scene_payloads,summary,update,digest,scene_decision,SCENE_VERSION,MAX_CHARS
from story_guides import parse

SCRIPT='''define a = Character("Alice")
label start:
    menu:
        "Talk":
            $ alice_affection += 2
            a happy "Let's discuss our journey."
            call private_talk
            jump common
        "Leave":
            $ trust -= 1
            jump common
        "Wait":
            pass
label private_talk:
    a "We should leave tomorrow."
    return
label common:
    "Everyone returns to work."
    menu:
        "Later":
            return
'''


def detail(text='앨리스와 여행 일정 상의',choice='대화한다',evidence=None):
    return {'status':'detail','summary':text,'choice_ko':choice,'evidence':[1] if evidence is None else evidence}


def answer(text='앨리스와 여행 일정 상의'):
    return {'message':{'content':json.dumps({'0':detail(text)},ensure_ascii=False)},'done_reason':'stop'}


class SceneTests(unittest.TestCase):
    def setUp(self):
        # Model registration is tested separately; scene fixtures never use it.
        selected=patch('model_store.require_selected',side_effect=lambda model:model or 'rpt-hymt2-7b:q6_k')
        selected.start();self.addCleanup(selected.stop)
    def test_existing_json_fingerprints_unchanged(self):
        events=parse({'s.rpy':SCRIPT})[0]
        for value in (events,{'a':[None,True,3,1.5,'한글'], 'b':('x','y')},[1,'Talk',[]]):
            previous=hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode('utf-8')).hexdigest()
            self.assertEqual(digest(value),previous)

    def test_python_literals_stable_across_processes_and_distinct(self):
        value={'nested':[{1,'hall',('garden',2)},b'bytes',1+2j,Ellipsis], 'keys':{('x',1):False,2:'two','2':'text'}}
        copy={'keys':{'2':'text',2:'two',('x',1):False},'nested':[{('garden',2),'hall',1},b'bytes',1+2j,Ellipsis]}
        self.assertEqual(digest(value),digest(copy))
        self.assertNotEqual(digest({'values':{1,2}}),digest({'values':[1,2]}))
        self.assertNotEqual(digest({1:'x','1':'y'}),digest({'1':'y'}))
        self.assertNotEqual(digest({1,'hall'}),digest({1,'garden'}))
        for seed in ('1','2'):
            output=subprocess.check_output([sys.executable,'-B','-X','utf8','-c',
                'from scene_hints import digest; print(digest('+repr(value)+'))'],
                cwd=Path(__file__).parent,env=dict(os.environ,PYTHONHASHSEED=seed),text=True,encoding='utf-8')
            self.assertEqual(output.strip(),digest(value))

    def test_parsed_sets_survive_analysis_and_reuse_without_model(self):
        script='''label start:
    menu:
        "Help":
            $ visited = {"garden", "hall"}
            $ metadata = {"nested": [set(), {1, "two"}], (1, "key"): b"bytes"}
            $ trust += 1
'''
        events=parse({'s.rpy':script})[0]
        values=[e['value'] for e in events if e['kind']=='assignment']
        self.assertIsInstance(values[0],set)
        with tempfile.TemporaryDirectory() as temp,patch('scene_hints.runtime',side_effect=AssertionError('No model')):
            p=Path(temp);state={'answers':[],'routes':[]}
            first=update(p,{},['answers','routes'],events,{},state)
            self.assertEqual(first['effects_analyzed'],1)
            self.assertEqual(state['routes'][0]['hints'][0]['text'],'신뢰도 +1')
            with patch('story_hints.infer_routes',side_effect=AssertionError('Reuse effects')):
                repeated=update(p,{},['answers','routes'],events,{},state)
            self.assertEqual(repeated['effects_reused'],1)
            self.assertTrue(repeated['answers_reused'])
            self.assertEqual(repeated['model_calls'],0)
            self.assertEqual(values[0],{'garden','hall'})
            self.assertIsInstance(values[1]['nested'][0],set)
            changed=parse({'s.rpy':script.replace('"garden"','"beach"')})[0]
            report=update(p,{},['routes'],changed,{},state)
            self.assertEqual(report['effects_analyzed'],1)

    def test_inline_calls_common_join_and_next_menu(self):
        plans=branches(parse({'s.rpy':SCRIPT})[0]);talk=plans[0]
        text=[e['text'] for e in talk[1] if e['kind']=='dialogue']
        self.assertEqual(text,["Let's discuss our journey.",'We should leave tomorrow.'])
        self.assertFalse(plans[1][1] and any(e['kind']=='dialogue' for e in plans[1][1]))
        self.assertEqual(talk[1][1]['speaker'],'a')

    def test_conditional_jump_does_not_leak_into_other_branch(self):
        script='''label start:
    menu:
        "Visit":
            if trust > 2:
                jump party
            "You wait alone."
            return
        "Go":
            return
label party:
    "We celebrate together."
    return
'''
        c,unique,*_=branches(parse({'s.rpy':script})[0])[0]
        items=scene_payloads(c,unique,{})
        self.assertEqual(len(items),2)
        party=next(i for i in items if i['payload']['dialogue'][0]['text']=='We celebrate together.')
        alone=next(i for i in items if i['payload']['dialogue'][0]['text']=='You wait alone.')
        self.assertIn('(trust > 2)',party['guards'])
        self.assertIn('not ((trust > 2))',alone['guards'])

    def test_nested_menu_and_call_that_does_not_return(self):
        script='''label start:
    menu:
        "Visit":
            call next_decision
            "Must not summarize past next decision."
label next_decision:
    "Arrival."
    menu:
        "Choose":
            $ trust += 9
            "This belongs to the nested choice."
'''
        c,unique,*_=branches(parse({'s.rpy':script})[0])[0]
        self.assertEqual([e['text'] for e in unique if e['kind']=='dialogue'],['Arrival.'])

    def test_flag_unlock_scene_without_label_keyword(self):
        script='''label start:
    menu:
        "Agree":
            $ promised = True
        "Decline":
            pass
label later:
    if promised:
        call chapter_003
label chapter_003:
    "You meet by the river."
    return
'''
        c,unique,*_=branches(parse({'s.rpy':script})[0])[0]
        self.assertEqual(scene_payloads(c,unique,{})[0]['payload']['dialogue'][0]['text'],'You meet by the river.')

    def test_cache_effects_empty_results_and_model_only_missing(self):
        events=parse({'s.rpy':SCRIPT})[0]
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);state={'answers':[],'routes':[]};cfg={'model':'fake','endpoint':'http://127.0.0.1:1'}
            with patch('scene_hints.runtime',return_value=__import__('contextlib').nullcontext(cfg)),patch('scene_hints.request',return_value=answer()) as ask:
                first=update(p,cfg,['routes'],events,{'a':'앨리스'},state)
            self.assertEqual(first['model_calls'],1);ask.assert_called_once()
            talk=next(r for r in state['routes'] if r['source']=='Talk')
            self.assertTrue(any('호감도 +2' in h['text'] for h in talk['hints']))
            self.assertTrue(any(h['text']=='장면: 앨리스와 여행 일정 상의' for h in talk['hints']))
            with patch('scene_hints.runtime',side_effect=AssertionError('No model')),patch('story_hints.infer_routes',side_effect=AssertionError('No effects reanalysis')):
                again=update(p,cfg,['routes'],events,{'a':'앨리스'},state)
            self.assertEqual(again['model_calls'],0);self.assertEqual(again['effects_reused'],4)
            modified=parse({'s.rpy':SCRIPT.replace('trust -= 1','trust -= 2')})[0]
            with patch('scene_hints.runtime',side_effect=AssertionError('Summary unchanged')):
                later=update(p,cfg,['routes'],modified,{'a':'앨리스'},state)
            self.assertEqual(later['effects_analyzed'],1)
            self.assertTrue(any(h['text']=='신뢰도 -2' for r in state['routes'] for h in r['hints']))

    def test_failed_summary_retry_only_and_answers_reused(self):
        events=parse({'s.rpy':SCRIPT})[0]
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);state={'answers':[],'routes':[]};cfg={'model':'fake','endpoint':'http://localhost:1'}
            with patch('scene_hints.runtime',side_effect=lambda c:__import__('contextlib').nullcontext(cfg)),patch('scene_hints.request',side_effect=[{'message':{'content':'{"summary":"cut'}},answer()]):
                first=update(p,cfg,['answers','routes'],events,{},state)
                second=update(p,cfg,['answers','routes'],events,{},state)
            self.assertEqual(first['scene_failures'],1);self.assertEqual(second['model_calls'],1)
            self.assertEqual(second['effects_analyzed'],0);self.assertTrue(second['answers_reused'])

    def test_cancel_preserves_completed_summary_and_closes_runtime(self):
        script=SCRIPT.replace('$ trust -= 1','$ trust -= 1\n            "I leave without her."')
        events=parse({'s.rpy':script})[0];closed=[]
        @contextmanager
        def fake_runtime(cfg):
            try:yield dict(cfg,endpoint='http://localhost:1')
            finally:closed.append(True)
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);state={'answers':[],'routes':[]}
            with patch('scene_hints.BATCH_SIZE',1),patch('scene_hints.runtime',side_effect=fake_runtime),patch('scene_hints.request',side_effect=[answer(),KeyboardInterrupt()]):
                with self.assertRaises(KeyboardInterrupt):update(p,{},['routes'],events,{},state)
            self.assertEqual(closed,[True])
            self.assertEqual(len(json.loads((p/'data/scene-summaries.json').read_text(encoding='utf-8'))),1)
            with patch('scene_hints.runtime',side_effect=fake_runtime),patch('scene_hints.request',return_value=answer('홀로 떠남')) as ask:
                report=update(p,{},['routes'],events,{},state)
            ask.assert_called_once();self.assertEqual(report['effects_analyzed'],0)

    def test_payload_bound_and_full_text_cache_invalidation(self):
        script='label s:\n    menu:\n        "Talk":\n'+''.join('            "'+str(i)+' '+('long words '*50)+'"\n' for i in range(100))
        c,unique,*_=branches(parse({'s.rpy':script})[0])[0]
        item=scene_payloads(c,unique,{})[0]
        self.assertLessEqual(len(json.dumps(item['payload']['dialogue'],ensure_ascii=False)),MAX_CHARS)
        self.assertTrue(item['payload']['excerpt'])
        unique[1]=dict(unique[1],text='Changed unseen line.')
        self.assertNotEqual(item['key'],scene_payloads(c,unique,{})[0]['key'])

    def test_reject_truncated_invalid_or_non_korean_summary(self):
        def response(text):return {'message':{'content':json.dumps({'summary':text})}}
        self.assertIsNone(summary(dict(response('대화 장면'),done_reason='length')))
        self.assertIsNone(summary(response('A conversation')))
        self.assertIsNone(summary(response('{b}장면{/b}')))
        self.assertEqual(summary(response('대화 장면')),'대화 장면')

    def test_remote_endpoint_rejected(self):
        with tempfile.TemporaryDirectory() as temp,patch('scene_hints.runtime',side_effect=AssertionError('No runtime')):
            with self.assertRaisesRegex(ValueError,'local model'):
                update(Path(temp),{'model':'fake','endpoint':'https://example.com'},['routes'],parse({'s.rpy':SCRIPT})[0],{}, {'answers':[],'routes':[]})

    def test_small_batch_keeps_success_and_retries_only_failed_member(self):
        script=SCRIPT.replace('$ trust -= 1','$ trust -= 1\n            "I leave without her."')
        events=parse({'s.rpy':script})[0]
        cfg={'model':'fake','endpoint':'http://localhost:1'}
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);state={'answers':[],'routes':[]}
            with patch('scene_hints.runtime',side_effect=lambda c:__import__('contextlib').nullcontext(cfg)),patch('scene_hints.request',side_effect=[answer(),answer('혼자 떠남')]) as ask:
                first=update(p,cfg,['routes'],events,{},state)
                self.assertEqual(first['scene_failures'],1)
                self.assertEqual(len(ask.call_args.args[2]['format']['properties']),2)
                second=update(p,cfg,['routes'],events,{},state)
                self.assertEqual(second['scenes_reused'],1)
                self.assertEqual(len(ask.call_args.args[2]['format']['properties']),1)

    def test_changed_scene_requests_only_affected_summary(self):
        script=SCRIPT.replace('$ trust -= 1','$ trust -= 1\n            "I leave without her."')
        cfg={'model':'fake','endpoint':'http://localhost:1'}
        batch={'message':{'content':json.dumps({'0':detail('함께 여행 계획'),'1':detail('홀로 떠남')})}}
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);state={'answers':[],'routes':[]}
            with patch('scene_hints.runtime',side_effect=lambda c:__import__('contextlib').nullcontext(cfg)),patch('scene_hints.request',side_effect=[batch,answer('다음 달 출발 상의')]) as ask:
                update(p,cfg,['routes'],parse({'s.rpy':script})[0],{},state)
                changed=script.replace('We should leave tomorrow.','We should leave next month.')
                report=update(p,cfg,['routes'],parse({'s.rpy':changed})[0],{},state)
                self.assertEqual(report['scenes_reused'],1)
                self.assertEqual(report['effects_analyzed'],1)
                self.assertEqual(ask.call_count,2)
                payload=json.loads(ask.call_args.args[2]['messages'][0]['content'].split('\n',1)[1])
                self.assertEqual(len(payload),1)
                self.assertEqual(payload['0']['choice'],'Talk')

    def test_effects_stop_at_nested_menu(self):
        script='''label start:
    menu:
        "Enter":
            $ trust += 1
            menu:
                "Nested":
                    $ trust += 99
'''
        with tempfile.TemporaryDirectory() as temp,patch('scene_hints.runtime',side_effect=AssertionError('No scenes')):
            state={'answers':[],'routes':[]}
            update(Path(temp),{},['routes'],parse({'s.rpy':script})[0],{},state)
            outer=next(r for r in state['routes'] if r['source']=='Enter')
            self.assertEqual([h['text'] for h in outer['hints']],['신뢰도 +1'])

    def test_restatement_and_missing_evidence_not_shown_as_detail(self):
        c,unique,*_=branches(parse({'s.rpy':SCRIPT})[0])[0]
        item=scene_payloads(c,unique,{})[0]
        result=scene_decision(detail('그녀와 대화하는 장면','그녀와 대화한다'),item)
        self.assertEqual(result['status'],'no_detail')
        self.assertEqual(result['summary'],'')
        for ids in ([],[999],[True],['1']):
            self.assertIsNone(scene_decision(detail(evidence=ids),item))
        result=scene_decision(detail('내일 함께 출발하기로 약속함',evidence=[2]),item)
        self.assertEqual(result['status'],'detail');self.assertEqual(result['evidence'],[2])

    def test_no_new_information_cached_and_numeric_effect_retained(self):
        script='''label start:
    menu:
        "Say hello":
            $ trust += 1
            "Hello."
'''
        events=parse({'s.rpy':script})[0];cfg={'model':'fake','endpoint':'http://localhost:1'}
        response={'message':{'content':json.dumps({'0':{'status':'no_detail','choice_ko':'인사한다','summary':'','evidence':[]}})}}
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);state={'answers':[],'routes':[]}
            with patch('scene_hints.runtime',return_value=__import__('contextlib').nullcontext(cfg)),patch('scene_hints.request',return_value=response):
                first=update(p,cfg,['routes'],events,{},state)
            self.assertEqual(first['scenes_omitted'],1)
            self.assertEqual([h['text'] for h in state['routes'][0]['hints']],['신뢰도 +1'])
            with patch('scene_hints.runtime',side_effect=AssertionError('Completed omission')):
                again=update(p,cfg,['routes'],events,{},state)
            self.assertEqual(again['scenes_reused'],1);self.assertEqual(again['model_calls'],0)

    def test_legacy_summary_rechecked_in_existing_routes_without_stat_reanalysis(self):
        from engine import save_json
        events=parse({'s.rpy':SCRIPT})[0];cfg={'model':'fake','endpoint':'http://localhost:1'}
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);state={'answers':[],'routes':[]}
            with patch('scene_hints.runtime',side_effect=lambda c:__import__('contextlib').nullcontext(cfg)),patch('scene_hints.request',return_value=answer()):
                update(p,cfg,['routes'],events,{},state)
            path=p/'data/scene-summaries.json'
            saved=json.loads(path.read_text(encoding='utf-8'));key=next(iter(saved))
            saved[key]={'summary':'대화하는 장면','model':'old'};save_json(path,saved)
            effect_bytes=(p/'data/hint-analysis.json').read_bytes()
            with patch('story_hints.infer_routes',side_effect=AssertionError('Keep numeric analysis')),patch('scene_hints.runtime',return_value=__import__('contextlib').nullcontext(cfg)),patch('scene_hints.request',return_value=answer('내일 출발하기로 약속함')) as ask:
                report=update(p,cfg,['routes'],events,{},state)
            ask.assert_called_once();self.assertEqual(report['legacy_scenes_rechecked'],1)
            self.assertEqual((p/'data/hint-analysis.json').read_bytes(),effect_bytes)
            saved=json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(saved[key]['version'],SCENE_VERSION)
            self.assertTrue(any(h['text']=='장면: 내일 출발하기로 약속함' for r in state['routes'] for h in r['hints']))
            with patch('scene_hints.runtime',side_effect=AssertionError('Already updated')):
                self.assertEqual(update(p,cfg,['routes'],events,{},state)['model_calls'],0)

    def test_long_scene_preserves_end_and_transition_context_with_ids(self):
        script='label s:\n    menu:\n        "Talk":\n'
        for i in range(80):
            if i==40:script+='            $ trust += 1\n'
            script+='            "Line '+str(i)+': '+('A conversation. '*4)+'"\n'
        c,unique,*_=branches(parse({'s.rpy':script})[0])[0]
        payload=scene_payloads(c,unique,{})[0]['payload'];lines=payload['dialogue']
        ids=[line['id'] for line in lines]
        self.assertIn(80,ids);self.assertTrue({40,41,42}.issubset(ids))
        self.assertEqual(ids,sorted(ids));self.assertTrue(any(line.get('gap_before') for line in lines))
        self.assertLessEqual(len(json.dumps(lines,ensure_ascii=False)),MAX_CHARS)

    def test_long_individual_lines_still_keep_final_outcome(self):
        script='label s:\n    menu:\n        "Talk":\n'+''.join('            "'+str(i)+' '+('word '*400)+'ending'+str(i)+'"\n' for i in range(8))
        c,unique,*_=branches(parse({'s.rpy':script})[0])[0]
        lines=scene_payloads(c,unique,{})[0]['payload']['dialogue']
        self.assertEqual(lines[-1]['id'],8);self.assertTrue(lines[-1]['text'].endswith('ending7'))
        self.assertLessEqual(len(json.dumps(lines,ensure_ascii=False)),MAX_CHARS)


if __name__=='__main__':unittest.main()
