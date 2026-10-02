"""Diagnostic-only regressions with invented scripts and mocked local requests."""
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scene_hints import branches,scene_decision,update
from story_guides import parse
from test_scene_hints import detail


SCRIPT='''label start:
    menu:
        "Talk":
            "The guide reveals a secret."
        "Leave":
            "The guide leaves alone."
'''
CFG={'model':'fake','endpoint':'http://localhost:1'}


def response(value,**extra):
    return dict(message={'content':json.dumps(value,ensure_ascii=False)},done_reason='stop',**extra)


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        selected=patch('model_store.require_selected',side_effect=lambda model:model or 'fake')
        selected.start();self.addCleanup(selected.stop)
    def run_analysis(self,project,reply,script=SCRIPT):
        with patch('scene_hints.runtime',return_value=nullcontext(CFG)),patch('scene_hints.request',return_value=reply) as ask:
            state={'answers':[],'routes':[]}
            report=update(project,CFG,['routes'],parse({'invented.rpy':script})[0],{},state)
            return report,state,ask

    def records(self,project):
        return [json.loads(line) for line in (project/'data/scene-errors.jsonl').read_text(encoding='utf-8').splitlines()]

    def snapshot(self,project):
        return json.loads((project/'data/scene-diagnostics.json').read_text(encoding='utf-8'))

    def test_partial_batch_retains_success_and_logs_only_failed_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp);raw=response({'0':detail('가이드가 비밀을 밝힘')},eval_count=71)
            report,state,ask=self.run_analysis(project,raw)
            self.assertEqual(report['scene_failures'],1)
            record=self.records(project)[0]
            self.assertEqual(record['response'],raw)
            self.assertEqual(record['options'],{'num_ctx':8192,'num_predict':560,'temperature':0,'seed':42})
            self.assertEqual(record['options'],ask.call_args.args[2]['options'])
            self.assertEqual(len(record['failures']),1)
            failure=record['failures'][0]
            self.assertEqual(failure['reason'],'missing_batch_item')
            self.assertEqual(failure['input']['choice'],'Leave')
            self.assertEqual(failure['locations'][0]['file'],'invented.rpy')
            self.assertEqual(failure['locations'][0]['line'],5)
            self.assertEqual(failure['input']['dialogue'][0]['text'],'The guide leaves alone.')
            self.assertEqual(record['model'],'fake')
            self.assertIn('각 항목',record['instruction'])
            self.assertEqual(len(json.loads((project/'data/scene-summaries.json').read_text(encoding='utf-8'))),1)
            self.assertEqual(self.snapshot(project)['status'],'completed')

    def test_output_limit_records_actual_response_without_changing_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp);raw=response({'0':detail(),'1':detail('혼자 떠남')},eval_count=560)
            raw['done_reason']='length'
            report,_,_=self.run_analysis(project,raw)
            self.assertEqual(report['scene_failures'],2)
            record=self.records(project)[0]
            self.assertEqual([f['reason'] for f in record['failures']],['output_limit']*2)
            self.assertEqual(record['response']['eval_count'],560)
            self.assertEqual(record['response']['done_reason'],'length')
            self.assertEqual(json.loads((project/'data/scene-summaries.json').read_text(encoding='utf-8')), {})

    def test_invalid_json_and_missing_content_have_distinct_reasons(self):
        for raw,reason in [({'message':{'content':'{"0":'}},'invalid_json'),
                           ({'message':{}},'missing_response_content'),
                           (response([]),'invalid_response_object')]:
            with self.subTest(reason=reason),tempfile.TemporaryDirectory() as tmp:
                project=Path(tmp);self.run_analysis(project,raw)
                record=self.records(project)[0]
                self.assertEqual(record['response'],raw)
                self.assertEqual({f['reason'] for f in record['failures']},{reason})

    def test_validation_reason_does_not_change_decision(self):
        item={'payload':{'choice':'Talk','dialogue':[{'id':1,'text':'Evidence.'}]}}
        invalid=[(dict(detail(),summary='가'*65),'summary_length'),
                 (dict(detail(),summary='English only'),'summary_not_korean'),
                 (dict(detail(),summary='{b}대화{/b}'),'summary_forbidden_characters'),
                 (dict(detail(),evidence=[]),'missing_evidence'),
                 (dict(detail(),evidence=['1']),'evidence_type'),
                 (dict(detail(),evidence=[99]),'unknown_evidence_id'),
                 (dict(detail(),status='maybe'),'invalid_status')]
        for value,expected in invalid:
            with self.subTest(expected=expected):
                diagnostic={}
                self.assertIsNone(scene_decision(value,item,diagnostic))
                self.assertEqual(diagnostic['reason'],expected)
                self.assertEqual(scene_decision(value,item),scene_decision(value,item,{}))

    def test_omission_and_success_are_not_errors_and_cached_work_skips_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp)
            raw=response({'0':{'status':'no_detail','choice_ko':'대화','summary':'','evidence':[]},'1':detail('혼자 떠남')})
            report,_,_=self.run_analysis(project,raw)
            self.assertEqual(report['scene_failures'],0)
            self.assertFalse((project/'data/scene-errors.jsonl').exists())
            first=self.snapshot(project)
            self.assertEqual({s['reason'] for s in first['segments'].values()},{'model_no_detail','detail'})
            before=(project/'data/scene-summaries.json').read_bytes()
            with patch('scene_hints.runtime',side_effect=AssertionError('No model for cached summaries')):
                update(project,CFG,['routes'],parse({'invented.rpy':SCRIPT})[0],{}, {'answers':[],'routes':[]})
            self.assertEqual((project/'data/scene-summaries.json').read_bytes(),before)
            second=self.snapshot(project)
            self.assertNotEqual(first['run_id'],second['run_id'])
            self.assertTrue(all(s['cached'] for s in second['segments'].values()))

    def test_trace_reasons_are_recorded_without_changing_paths(self):
        script='''label start:
    menu:
        "A":
            jump shared
        "B":
            jump shared
        "Dynamic":
            jump expression destination
label shared:
    "A shared result."
    return
'''
        events=parse({'invented.rpy':script})[0];stops={}
        self.assertEqual(branches(events),branches(events,stops))
        with tempfile.TemporaryDirectory() as tmp,patch('scene_hints.runtime',side_effect=AssertionError('No scene inputs')):
            project=Path(tmp)
            update(project,CFG,['routes'],events,{}, {'answers':[],'routes':[]})
            snapshot=self.snapshot(project)
            choices={c['title']:c for c in snapshot['choices'].values()}
            self.assertEqual(choices['A']['reason'],'shared_dialogue_removed')
            self.assertEqual(choices['B']['reason'],'shared_dialogue_removed')
            self.assertEqual(choices['Dynamic']['reason'],'no_followed_dialogue')
            self.assertEqual(choices['Dynamic']['stops'][0]['reason'],'dynamic_target')
            self.assertFalse((project/'data/scene-errors.jsonl').exists())

    def test_deduplicated_failure_lists_all_affected_choice_locations(self):
        script='''label one:
    menu:
        "Talk":
            "The guide reveals a secret."
label two:
    menu:
        "Talk":
            "The guide reveals a secret."
'''
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp);report,_,ask=self.run_analysis(project,{'message':{'content':'cut'}},script)
            self.assertEqual(report['scene_failures'],1)
            self.assertEqual(ask.call_count,1)
            locations=self.records(project)[0]['failures'][0]['locations']
            self.assertEqual({loc['line'] for loc in locations},{3,7})

    def test_request_error_or_cancel_persists_log_and_propagates(self):
        for exc,reason,status in [(TimeoutError('timed out'),'request_exception','error'),
                                  (KeyboardInterrupt(),'cancelled','cancelled')]:
            with self.subTest(reason=reason),tempfile.TemporaryDirectory() as tmp:
                project=Path(tmp)
                with patch('scene_hints.runtime',return_value=nullcontext(CFG)),patch('scene_hints.request',side_effect=exc):
                    with self.assertRaises(type(exc)):
                        update(project,CFG,['routes'],parse({'invented.rpy':SCRIPT})[0],{}, {'answers':[],'routes':[]})
                self.assertEqual(self.snapshot(project)['status'],status)
                record=self.records(project)[0]
                self.assertEqual(record['exception']['type'],type(exc).__name__)
                self.assertEqual({f['reason'] for f in record['failures']},{reason})

    def test_runtime_start_error_and_append_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp)
            with patch('scene_hints.runtime',side_effect=RuntimeError('startup failed')):
                with self.assertRaises(RuntimeError):
                    update(project,CFG,['routes'],parse({'invented.rpy':SCRIPT})[0],{}, {'answers':[],'routes':[]})
            self.run_analysis(project,{'message':{'content':'cut'}})
            records=self.records(project)
            self.assertEqual(len(records),2)
            self.assertNotEqual(records[0]['run_id'],records[1]['run_id'])
            self.assertEqual(records[0]['failures'][0]['reason'],'runtime_startup')

    def test_diagnostic_write_failure_does_not_discard_valid_summaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp)
            with patch('scene_diagnostics.save_json',side_effect=PermissionError('readonly diagnostic')):
                report,_,_=self.run_analysis(project,response({'0':detail(),'1':detail('혼자 떠남')}))
            self.assertEqual(report['scene_failures'],0)
            self.assertEqual(len(json.loads((project/'data/scene-summaries.json').read_text(encoding='utf-8'))),2)


if __name__=='__main__':unittest.main()
