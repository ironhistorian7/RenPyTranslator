"""Synthetic requests only: no game, model or external connection."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from context_notes import ContextNotes
from hy_backend import body
from translation import (BatchTranslationError, retry_instruction, trace_event,
                         translate_batch, validate_text)


class ContextDiagnosticsTests(unittest.TestCase):
    def test_repair_report_preserves_translation_and_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp)
            notes=project/'staging/game/context';notes.mkdir(parents=True)
            (notes/'global.txt').write_text('Use formal narration.',encoding='utf-8')
            cfg={'_repair':True}
            ContextNotes(project,cfg,[])
            path=project/'data/context-report.json';before=path.read_bytes()
            ContextNotes(project,dict(cfg,_trace_phase='failed-repair'),[])
            self.assertEqual(path.read_bytes(),before)
            history=[json.loads(line) for line in (project/'data/context-history.jsonl').read_text().splitlines()]
            self.assertEqual([r['phase'] for r in history],['translation','failed-repair'])
            self.assertEqual(len(history[0]['file_sha256']['global.txt']),64)
            self.assertTrue((project/'data/context-report.failed-repair.json').exists())

    def test_exact_requests_responses_and_retry_ids_are_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg={'model':'fake','endpoint':'http://localhost:1','_trace_dir':tmp}
            row={'id':'sentence','kind':'dialogue','file':'chapter.rpy','source':'Hello.'}
            context={'translation_guidance':{'scene':'A parent addresses a child.'}}
            reply={'message':{'content':'{"0":"안녕."}'},'done_reason':'stop'}
            with patch('translation.request',return_value=reply) as api:
                translate_batch([row],context,cfg)
                first=api.call_args.args[2]
                translate_batch([row],context,dict(cfg,_trace_phase='failed-repair'),'Retry')
            events=[json.loads(s) for s in (Path(tmp)/'translation-requests.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual([e['event'] for e in events],['request','response','validation']*2)
            self.assertEqual(events[0]['request'],first)
            self.assertEqual(events[1]['response'],reply)
            self.assertEqual(events[2]['accepted_ids'],['sentence'])
            self.assertEqual(len({e['request_id'] for e in events}),2)
            self.assertEqual(events[3]['phase'],'failed-repair')

    def test_concurrent_events_remain_valid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg={'_trace_dir':tmp}
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(lambda i:trace_event(cfg,str(i),'request',text='문맥'*300),range(20)))
            records=[json.loads(s) for s in (Path(tmp)/'translation-requests.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual({r['request_id'] for r in records},{str(i) for i in range(20)})

    def test_transport_failure_has_correlated_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg={'model':'fake','endpoint':'http://localhost:1','_trace_dir':tmp}
            with patch('translation.request',side_effect=TimeoutError('timeout')):
                with self.assertRaises(TimeoutError):
                    translate_batch([dict(id='x',kind='dialogue',source='Hello.')],{},cfg)
            records=[json.loads(s) for s in (Path(tmp)/'translation-requests.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual(records[0]['request_id'],records[1]['request_id'])
            self.assertEqual(records[1]['event'],'request_error')

    def test_empty_links_fail_even_if_tag_counts_and_order_are_intact(self):
        source='See {a=details}these facts{/a}.'
        for target in ['사실을 보세요{a=details}{/a}.','{a=details}{b}  {/b}{/a} 사실을 보세요.']:
            self.assertIn('empty hyperlink text',validate_text(source,target,{},check_link_content=True))
        self.assertEqual(validate_text(source,'{a=details}{b}설명{/b}{/a}을 보세요.',{}),['markup mismatch'])
        self.assertEqual(validate_text(source,'{a=details}설명{/a}을 보세요.',{},check_link_content=True),[])
        source='{a=details}One{/a} {a=details}Two{/a}'
        self.assertIn('empty hyperlink text',validate_text(source,'{a=details}하나 둘{/a} {a=details}{/a}',{},check_link_content=True))
        # Rebuilding an old cache must not newly abort the whole project.
        self.assertEqual(validate_text(source,'{a=details}하나 둘{/a} {a=details}{/a}',{}),[])

    def test_intentional_empty_escaped_and_image_links_are_allowed(self):
        for source,target in [
            ('{a=x}{/a} Hello.','{a=x}{/a} 안녕.'),
            ('{{a=x} Literal syntax.','{{a=x} 문자 그대로.'),
            ('{a=x}{image=icon.png}{/a} Hello.','{a=x}{image=icon.png}{/a} 안녕.'),
            ('{a=[dest]}[name]{/a} Hello.','{a=[dest]}[name]{/a} 안녕.')]:
            self.assertEqual(validate_text(source,target,{},check_link_content=True),[])

    def test_empty_link_enters_existing_retry_with_specific_instruction(self):
        row=dict(id='x',kind='dialogue',source='See {a=more}more{/a}.')
        reply={'message':{'content':'{"0":"더 보기 <rpt000/><rpt001/>."}'}}
        with patch('translation.request',return_value=reply):
            with self.assertRaises(BatchTranslationError) as raised:
                translate_batch([row],{},dict(model='fake',endpoint='http://localhost:1'))
        error=raised.exception.failures[0]['error']
        self.assertIn('empty hyperlink text',error)
        self.assertIn('BETWEEN',retry_instruction([row],{'x':error}))

    def test_hy_priority_is_generic_and_reference_only(self):
        context={'translation_guidance':{'global':'해설은 합니다체.','scene':'현재 대상은 곤충이다.'}}
        prompt=body([{'id':'0','text':'It moves.'}],context,{'model':'fake'},{},'')['messages'][0]['content']
        for text in ('현재 장면을 우선','공통 배경의 주인공을 모든 문장의 대상으로 간주하지','원문과 충돌하면 원문','현재 대상은 곤충'):
            self.assertIn(text,prompt)


if __name__=='__main__': unittest.main()
