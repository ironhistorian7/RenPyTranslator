"""Synthetic progress clocks and a stubbed recovery loop; no model/game/cache."""
from contextlib import ExitStack,contextmanager,redirect_stdout
from datetime import datetime
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error

import job_events as events
import runtime_recovery


class Clock:
    now=0.0
    def monotonic(self):return self.now
    def advance(self,seconds):self.now+=seconds


class JobEventTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.output=io.StringIO();self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(events,'time',SimpleNamespace(monotonic=self.clock.monotonic)))
        self.stack.enter_context(patch.object(events,'_active',None))
        self.stack.enter_context(patch.dict(os.environ,{'RPT_DESKTOP_EVENTS':'1'}))
        self.stack.enter_context(redirect_stdout(self.output))

    def emitted(self):
        return [json.loads(line[len('RPT_EVENT '):]) for line in self.output.getvalue().splitlines()
                if line.startswith('RPT_EVENT ')]

    def tracker(self):
        tracker=events.TranslationProgress()
        rows=[{'id':uid} for uid in ('old','a','b','c')]
        tracker.begin(rows,rows[1:],{'old':{'status':'complete'}},{'a':2,'b':4,'c':6})
        return tracker

    def test_desktop_opt_in_leaves_normal_cli_output_unchanged(self):
        with patch.dict(os.environ,{'RPT_DESKTOP_EVENTS':'0'}):
            events.set_phase('prepare','준비');events.progress('prepare',1,2);events.emit('issue',message='synthetic')
            print('existing CLI output')
        self.assertEqual(self.output.getvalue(),'existing CLI output\n')

    def test_phase_transition_resets_counter_but_same_phase_preserves_clock(self):
        first=events.set_phase('prepare','준비',4,'files')
        self.clock.advance(3);events.progress('prepare',1,4)
        self.assertIs(events.set_phase('prepare','준비',4,'files'),first)
        self.assertEqual(events._active.completed,1)
        self.assertEqual(self.emitted()[-1]['eta_seconds'],9.0)
        events.set_phase('render','패치 생성',2,'files')
        self.assertEqual(events._active.completed,0)
        self.assertEqual(self.emitted()[-1]['elapsed_seconds'],0)
        self.assertEqual(self.emitted()[-1]['event'],'stage')

    def test_unbounded_work_has_no_invented_eta(self):
        events.set_phase('compile','컴파일')
        self.clock.advance(30);events.progress('compile',1,None)
        self.assertIsNone(self.emitted()[-1]['total'])
        self.assertIsNone(self.emitted()[-1]['eta_seconds'])
        self.assertEqual(self.emitted()[-1]['elapsed_seconds'],30)

    def test_unique_commits_separate_cache_success_and_original_fallback(self):
        tracker=self.tracker();self.clock.advance(3)
        tracker.commit([{'id':'a'},{'id':'b','status':'source_fallback'}])
        tracker.commit([{'id':'a'},{'id':'old'}])
        event=self.emitted()[-1]
        self.assertEqual((event['completed'],event['total'],event['cached']),(2,3,1))
        self.assertEqual((event['successful'],event['original_kept']),(1,1))
        self.assertEqual((event['work_completed'],event['work_total']),(6,12))
        self.assertEqual(event['eta_seconds'],3)

    def test_close_worker_results_do_not_create_a_millisecond_rate_spike(self):
        tracker=self.tracker();self.clock.advance(3);tracker.commit([{'id':'a'}])
        rate=tracker.rate.rate
        self.clock.advance(.001);tracker.commit([{'id':'b'}])
        self.assertEqual(tracker.rate.rate,rate)
        self.assertEqual(tracker.snapshot()['completed'],2)
        self.clock.advance(.2);tracker.publish()
        self.assertEqual(self.emitted()[-1]['completed'],2)
        self.assertGreater(self.emitted()[-1]['eta_seconds'],0)

    def test_fast_progress_is_throttled_but_phase_entry_and_completion_are_immediate(self):
        events.set_phase('render','패치 생성',1000)
        events.progress('render',0,1000)
        for number in range(1,1000):
            self.clock.advance(.0001);events.progress('render',number,1000)
        self.assertEqual(events._active.completed,999)
        self.assertEqual(len([event for event in self.emitted() if event['event']=='progress']),1)
        events.progress('render',1000,1000)
        self.assertEqual(self.emitted()[-1]['completed'],1000)
        self.assertEqual(self.emitted()[-1]['eta_seconds'],0)
        events.set_phase('empty','준비',0);events.progress('empty',0,0)
        self.assertEqual(self.emitted()[-1]['total'],0)

    def test_restart_preserves_denominator_and_excludes_backoff_from_rate(self):
        tracker=self.tracker();self.clock.advance(3);tracker.commit([{'id':'a'}])
        tracker.pause();self.clock.advance(100);tracker.resume()
        rows=[{'id':uid} for uid in ('old','a','b','c')]
        tracker.begin(rows,rows[2:],{'old':{},'a':{}},None)
        self.clock.advance(3);tracker.commit([{'id':'b'}])
        snapshot=tracker.snapshot()
        self.assertEqual((snapshot['completed'],snapshot['pending_at_start'],snapshot['cached']),(2,3,1))
        self.assertEqual(snapshot['seconds'],106)
        self.assertLess(self.emitted()[-1]['eta_seconds'],10)
        self.assertEqual(len([e for e in self.emitted() if e['event']=='stage']),1)

    def test_empty_translation_keeps_cached_count_and_finishes_without_eta_warmup(self):
        tracker=events.TranslationProgress();tracker.begin([{'id':'old'}],[],{'old':{}},{})
        event=self.emitted()[-1]
        self.assertEqual((event['completed'],event['total'],event['cached']),(0,0,1))
        self.assertEqual(event['eta_seconds'],0)

    def test_commits_use_incremental_work_totals_without_rescanning_units(self):
        class Units(dict):
            calls=0
            def values(self):self.calls+=1;return super().values()
        units=Units({str(i):1 for i in range(20)});rows=[{'id':uid} for uid in units]
        tracker=events.TranslationProgress();tracker.begin(rows,rows,{},units)
        for uid in units:self.clock.advance(.2);tracker.commit([{'id':uid}])
        self.assertEqual(units.calls,1)
        self.assertEqual(tracker.snapshot()['completed'],20)

    def test_recovery_events_keep_the_same_tracker_and_use_existing_backoff(self):
        class Evidence:
            def __init__(self,*args):pass
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def event(self,*args,**kwargs):pass
            def configure(self,*args):pass
            def capture(self,*args):return 'synthetic-incident'
            def finalize(self,*args):pass
        @contextmanager
        def runtime(cfg):yield dict(cfg,endpoint='http://localhost')
        known={};trackers=[];pauses=[]
        def operation(cfg):
            tracker=events.translation_tracker(cfg);trackers.append(tracker)
            rows=[{'id':'a'},{'id':'b'}]
            tracker.begin(rows,[r for r in rows if r['id'] not in known],known,
                          {'a':1,'b':1} if tracker.units is None else None)
            self.clock.advance(3)
            uid='a' if not known else 'b';known[uid]={'id':uid};tracker.commit([known[uid]])
            if uid=='a':
                error=urllib.error.HTTPError('http://localhost',500,'synthetic',{},io.BytesIO())
                error._rpt_transport=True;raise error
            return 'done'
        def pause(seconds):pauses.append(seconds);self.clock.advance(seconds)
        with patch('runtime_recovery.Evidence',Evidence),patch('hy_backend.runtime',runtime), \
                patch('runtime_recovery.cancelled',lambda:None),patch('runtime_recovery.pause',pause), \
                patch('runtime_recovery.resources',lambda:{}):
            self.assertEqual(runtime_recovery.run(Path('synthetic'),{},operation,lambda:len(known)),'done')
        self.assertIs(trackers[0],trackers[1]);self.assertEqual(pauses,[10])
        recovery=[event for event in self.emitted() if event['event']=='recovery']
        self.assertEqual([event['state'] for event in recovery],['waiting','resuming'])
        self.assertEqual(recovery[0]['delay_seconds'],10)
        self.assertIsNotNone(datetime.fromisoformat(recovery[0]['next_retry_at']).tzinfo)
        progress=[event['completed'] for event in self.emitted() if event['event']=='progress']
        self.assertEqual(progress,[0,1,1,2])


if __name__=='__main__':unittest.main()
