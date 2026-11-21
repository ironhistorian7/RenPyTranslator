"""In-memory warning accounting only; no projects, model, server, or API."""
from contextlib import redirect_stdout
import io
import json
import os
import unittest
from unittest.mock import patch

import job_events as events


class JobWarningTests(unittest.TestCase):
    def setUp(self):
        self.output=io.StringIO()
        self.enterContext(patch.object(events,'_warnings',{}))
        self.enterContext(patch.object(events,'_active',None))
        self.enterContext(patch.dict(os.environ,{'RPT_DESKTOP_EVENTS':'1'}))
        self.enterContext(redirect_stdout(self.output))

    def records(self):
        return [json.loads(line[len('RPT_EVENT '):]) for line in self.output.getvalue().splitlines() if line.startswith('RPT_EVENT ')]

    def test_repeated_diagnostic_is_counted_once_and_retries_accumulate(self):
        events.warning('language','번역 확인',item_ids=['one','two'],count=2,error_id='first')
        events.warning('language','번역 확인',item_ids=['one','two'],count=2,error_id='first')
        events.warning('language','번역 확인',item_ids=['two','three'],count=2,error_id='retry')
        result=events.finish_warnings()[0]
        self.assertEqual((result['occurrences'],result['item_count']),(4,3))
        self.assertEqual(result['item_ids'],['one','two','three'])
        self.assertEqual(len(self.records()),3)
        self.assertEqual(self.records()[-1]['event'],'warning_summary')

    def test_large_id_set_has_bounded_wire_samples_and_exact_unique_counter(self):
        events.warning('format','응답 형식',item_ids=[str(i) for i in range(1000)]+['X:/private/path'],count=1000)
        result=self.records()[-1]
        self.assertEqual((result['occurrences'],result['item_count']),(1000,1000))
        self.assertEqual(len(result['item_ids']),24)
        self.assertNotIn('private',self.output.getvalue())
        events.set_phase('render','파일 생성',1)
        self.assertEqual(events.finish_warnings()[0]['item_count'],1000)

    def test_cli_prints_only_one_compact_final_summary(self):
        with patch.dict(os.environ,{'RPT_DESKTOP_EVENTS':'0'}):
            events.warning('language','번역 확인',item_ids=['one'],error_id='first')
            events.warning('original_kept','원문 유지',item_ids=['one'],error_id='second')
            self.assertEqual(self.output.getvalue(),'')
            events.finish_warnings()
        self.assertEqual(len(self.output.getvalue().splitlines()),1)
        self.assertIn('2개 유형',self.output.getvalue())
        self.assertIn('관련 항목 1개',self.output.getvalue())

    def test_empty_job_does_not_add_output(self):
        self.assertEqual(events.finish_warnings(),[])
        self.assertEqual(self.output.getvalue(),'')


if __name__=='__main__':unittest.main()
