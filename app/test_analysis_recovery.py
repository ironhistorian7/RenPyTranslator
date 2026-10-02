"""Offline regression cases; uses only generated temporary files and fake responses."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from automatic import (analysis_body, analysis_input_size, analyze_chunk, resilient_analysis,
    merge_analysis, analyze, AnalysisResponseError, VERSION, digest)
from engine import save_json

STRUCTURE={'characters':{'n':'Narrator'},'labels':{}}
CONFIG={'model':'fake-local','endpoint':'http://unused.invalid','num_ctx':4096}

def item(uid,text='A short sentence.'):
    return {'id':str(uid),'source':text,'speaker':'n','kind':'dialogue','block':'scene_1234abcd','file':'scene.rpy'}

def result():
    return {'summary':'짧은 설명','register':'평서체','characters':[],'terms':[],'uncertainties':[]}

def response(value=None,reason='stop'):
    return {'message':{'content':json.dumps(value or result(),ensure_ascii=False)},
        'done':True,'done_reason':reason,'eval_count':50,'prompt_eval_count':500}

class AnalysisRecoveryTests(unittest.TestCase):
    def test_large_branch_metadata_cannot_overflow_prompt(self):
        structure={'characters':{'n':'N'*10000},'labels':{'scene':{
            'jumps':['target'+str(i) for i in range(1000)],'calls':[],
            'choices':['한글 선택지 '*100 for _ in range(500)]}}}
        body=analysis_body([item(1)],CONFIG,structure)
        self.assertLessEqual(analysis_input_size(body)+body['options']['num_predict'],4096)
        payload=json.loads(body['messages'][1]['content'])
        self.assertEqual(payload['passage'][0]['text'],'A short sentence.')
        self.assertLess(len(json.dumps(payload)),3000)

    def test_configured_context_is_respected(self):
        body=analysis_body([item(1)],dict(CONFIG,num_ctx=8192),STRUCTURE)
        self.assertEqual(body['options']['num_ctx'],8192)
        self.assertLessEqual(analysis_input_size(body)+body['options']['num_predict'],8192)

    def test_overlong_single_line_is_split_only_for_analysis(self):
        text=''.join(f'{i}번 한글로 된 아주 긴 대사입니다. ' for i in range(200))
        rows=[item(1,text)];seen=[]
        def fake(endpoint,route,body):
            self.assertLessEqual(analysis_input_size(body)+body['options']['num_predict'],4096)
            seen.extend(r['text'] for r in json.loads(body['messages'][1]['content'])['passage'])
            return response()
        with tempfile.TemporaryDirectory() as temp,patch('automatic.request',side_effect=fake):
            value=resilient_analysis(rows,CONFIG,STRUCTURE,Path(temp),[])
        self.assertGreater(len(seen),1)
        self.assertEqual(''.join(seen),text)
        self.assertEqual(rows[0]['source'],text)
        self.assertNotIn('_fallback_ids',value)

    def test_length_cut_is_logged_then_only_failed_batch_is_split(self):
        rows=[item(1),item(2)];bodies=[]
        def fake(endpoint,route,body):
            bodies.append(body)
            return response(reason='length') if len(bodies)==1 else response()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);cfg=dict(CONFIG,_analysis_log_dir=str(root/'logs'))
            with patch('automatic.request',side_effect=fake) as api:
                value=resilient_analysis(rows,cfg,STRUCTURE,root/'cache',[])
                self.assertEqual(api.call_count,3)
                again=resilient_analysis(rows,cfg,STRUCTURE,root/'cache',[])
                self.assertEqual(api.call_count,3)
                self.assertEqual(value,again)
            saved=json.loads(next((root/'logs/failures').glob('*.json')).read_text(encoding='utf-8'))
            self.assertEqual(saved['response']['done_reason'],'length')
            self.assertEqual(saved['response']['eval_count'],50)
            self.assertIn('output limit',saved['error'])
        self.assertEqual([len(json.loads(b['messages'][1]['content'])['passage']) for b in bodies],[2,1,1])

    def test_unterminated_json_singleton_gets_compact_retry_then_fallback(self):
        bodies=[]
        bad={'message':{'content':'{"summary":"unterminated'},'done_reason':'stop'}
        def fake(endpoint,route,body):bodies.append(body);return bad
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);failures=[];cfg=dict(CONFIG,_analysis_log_dir=str(root/'logs'))
            with patch('automatic.request',side_effect=fake):
                value=resilient_analysis([item(1)],cfg,STRUCTURE,root/'cache',failures)
            self.assertEqual(len(bodies),2)
            self.assertNotEqual(bodies[0],bodies[1])
            self.assertEqual(value['_fallback_ids'],['1'])
            saved=json.loads((root/'logs/last-response.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['response']['message']['content'],bad['message']['content'])
            self.assertIn('Unterminated string',saved['error'])
            self.assertEqual(len(failures),1)

    def test_existing_successful_cache_is_reused(self):
        rows=[item(1)]
        key=digest({'version':VERSION,'rows':rows,'model':CONFIG['model'],'outline':STRUCTURE})
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);save_json(root/(key+'.json'),result())
            with patch('automatic.request',side_effect=AssertionError('Must not reanalyze')):
                self.assertEqual(resilient_analysis(rows,CONFIG,STRUCTURE,root,[]),result())

    def test_analysis_continues_after_bad_chunk_and_reports_fallback(self):
        rows=[item(1),dict(item(2),file='other.rpy')]
        def fake(batch,cfg,structure):
            if batch[0]['id']=='1':raise AnalysisResponseError('invalid JSON')
            return result()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);save_json(root/'data/catalog.json',rows)
            with patch('automatic.outline',return_value=STRUCTURE),patch('automatic.analyze_chunk',side_effect=fake) as api:
                profile=analyze(root,CONFIG)
                self.assertEqual(api.call_count,3)
                self.assertEqual(profile['analysis_fallback_ids'],['1'])
                self.assertEqual(profile['entries_analyzed'],1)
                self.assertEqual(profile['entries_covered'],2)
                again=analyze(root,CONFIG)
                self.assertEqual(api.call_count,3)
                self.assertEqual(profile,again)

    def test_corrupt_success_cache_is_regenerated(self):
        rows=[item(1)]
        key=digest({'version':VERSION,'rows':rows,'model':CONFIG['model'],'outline':STRUCTURE})
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/(key+'.json')).write_text('{',encoding='utf-8')
            with patch('automatic.request',return_value=response()) as api:
                value=resilient_analysis(rows,CONFIG,STRUCTURE,root,[])
            self.assertEqual(api.call_count,1)
            self.assertEqual(value,result())

if __name__=='__main__':unittest.main()
