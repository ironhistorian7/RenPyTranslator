import io
import json
import tempfile
import unittest
import urllib.error
from contextlib import contextmanager, ExitStack
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

import runtime_recovery as recovery
import translation
from engine import save_json


def failure(code=500):
    e=urllib.error.HTTPError('http://127.0.0.1/api/chat',code,'server error',{},io.BytesIO(b'{"error":"runner crashed"}'))
    e._rpt_transport=True
    return e


class EvidenceStub:
    events=[]
    finalizations=[]
    def __init__(self,*args):pass
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def event(self,name,**kw):self.events.append(name)
    def configure(self,cfg):pass
    def capture(self,*args):self.events.append('capture');return 'incident-test'
    def finalize(self,incident,cfg,exit_state):
        self.events.append('finalize');self.finalizations.append(dict(exit_state))


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        EvidenceStub.events=[]
        EvidenceStub.finalizations=[]
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.pauses=[]
        for target,value in [('runtime_recovery.Evidence',EvidenceStub),('runtime_recovery.time.sleep',lambda _:None),
                             ('runtime_recovery.pause',self.pauses.append),
                             ('runtime_recovery.resources',lambda:{}),('runtime_recovery.cancelled',lambda:None)]:
            self.stack.enter_context(patch(target,value))

    def runtime(self,order,cleanup_error=False):
        @contextmanager
        def runtime(cfg):
            order.append('start')
            try:yield dict(cfg,endpoint='http://localhost',_runtime_pid=123)
            finally:
                order.append('stop')
                if cleanup_error:raise RuntimeError('could not stop old server')
        return runtime

    def test_restart_once_after_teardown_and_keep_known_results(self):
        order=[];known={};seen=[]
        def work(cfg):
            seen.append(set(known))
            if not known:known['saved']='ok';raise failure()
            return 'completed'
        with patch('hy_backend.runtime',self.runtime(order)):
            self.assertEqual(recovery.run(Path('.'),{},work,lambda:len(known)),'completed')
        self.assertEqual(order,['start','stop','start','stop'])
        self.assertEqual(seen,[set(),{'saved'}])
        self.assertIn('resume_completed',EvidenceStub.events)

    def test_three_restarts_limit_and_no_source_fallback(self):
        order=[]
        def work(cfg):raise failure()
        with patch('hy_backend.runtime',self.runtime(order)),self.assertRaises(urllib.error.HTTPError):
            recovery.run(Path('.'),{},work,lambda:10)
        self.assertEqual(order,['start','stop']*4)
        self.assertEqual(self.pauses,[10,30,180,2])
        self.assertIn('restart_limit',EvidenceStub.events)

    def test_http_success_resets_streak_instead_of_limiting_the_entire_job(self):
        order=[];calls=[];run_ids=[];generations=[]
        def work(cfg):
            run_ids.append(cfg['_recovery_run_id']);generations.append(cfg['_runtime_generation'])
            calls.append(1)
            cfg['_recovery_request_succeeded'](request_id='invented-'+str(len(calls)),
                metrics={'eval_count':3,'prompt_eval_count':10,'done_reason':'stop'})
            if len(calls)<5:raise failure()
            return 'completed'
        with patch('hy_backend.runtime',self.runtime(order)):
            self.assertEqual(recovery.run(Path('.'),{},work,lambda:len(calls)),'completed')
        self.assertEqual(order,['start','stop']*5)
        self.assertEqual(self.pauses,[10,10,10,10])
        self.assertEqual(len(set(run_ids)),1)
        self.assertEqual(generations,list(range(5)))
        self.assertIn('restart_streak_reset',EvidenceStub.events)

    def test_success_from_an_old_generation_cannot_reset_its_replacement(self):
        callbacks=[];order=[]
        def work(cfg):
            callbacks.append(cfg['_recovery_request_succeeded'])
            if len(callbacks)==3:
                callbacks[0](request_id='late-old-response',metrics={'done_reason':'stop'})
            raise failure()
        with patch('hy_backend.runtime',self.runtime(order)),self.assertRaises(urllib.error.HTTPError):
            recovery.run(Path('.'),{},work,lambda:0)
        self.assertEqual(len(callbacks),4)
        self.assertEqual(self.pauses,[10,30,180,2])
        self.assertNotIn('restart_streak_reset',EvidenceStub.events)

    def test_post_exit_finalization_observes_the_shared_cleanup_state(self):
        order=[];calls=[]
        @contextmanager
        def runtime(cfg):
            state={};cfg=dict(cfg,_runtime_exit=state)
            order.append('start')
            try:yield cfg
            finally:
                state.update(termination='application_cleanup',process_returncode=137,
                             cleanup_finished_utc='invented-finished')
                order.append('stop')
        def work(cfg):
            calls.append(1)
            if len(calls)==1:raise failure()
            return 'done'
        with patch('hy_backend.runtime',runtime):
            self.assertEqual(recovery.run(Path('.'),{},work,lambda:0),'done')
        self.assertEqual(EvidenceStub.finalizations[0]['process_returncode'],137)
        self.assertEqual(EvidenceStub.finalizations[0]['cleanup_finished_utc'],'invented-finished')

    def test_success_diagnostic_failure_does_not_discard_its_signal(self):
        order=[];calls=[]
        original=EvidenceStub.event
        def event(collector,name,**details):
            if name=='request_succeeded':raise OSError('invented log failure')
            original(collector,name,**details)
        def work(cfg):
            calls.append(1)
            if len(calls)==2:cfg['_recovery_request_succeeded'](request_id='ok',metrics={})
            if len(calls)<3:raise failure()
            return 'done'
        with patch('hy_backend.runtime',self.runtime(order)),patch.object(EvidenceStub,'event',event):
            self.assertEqual(recovery.run(Path('.'),{},work,lambda:0),'done')
        self.assertEqual(self.pauses,[10,10])

    def test_cancel_and_validation_and_bad_request_do_not_restart(self):
        for err in (KeyboardInterrupt(),ValueError('bad tokens'),failure(400)):
            order=[]
            def work(cfg):raise err
            with self.subTest(err=type(err).__name__),patch('hy_backend.runtime',self.runtime(order)),self.assertRaises(type(err)):
                recovery.run(Path('.'),{},work,lambda:0)
            self.assertEqual(order,['start','stop'])

    def test_cancel_flag_after_transport_failure_prevents_restart(self):
        order=[];cancel=[False]
        def check():
            if cancel[0]:raise KeyboardInterrupt
        def work(cfg):cancel[0]=True;raise failure()
        with patch('hy_backend.runtime',self.runtime(order)),patch('runtime_recovery.cancelled',check),self.assertRaises(KeyboardInterrupt):
            recovery.run(Path('.'),{},work,lambda:0)
        self.assertEqual(order,['start','stop'])

    def test_cleanup_failure_prevents_second_server(self):
        order=[]
        def work(cfg):raise failure()
        with patch('hy_backend.runtime',self.runtime(order,True)),self.assertRaisesRegex(RuntimeError,'could not stop'):
            recovery.run(Path('.'),{},work,lambda:0)
        self.assertEqual(order,['start','stop'])

    def test_diagnostic_failure_does_not_break_recovery(self):
        order=[];calls=[]
        def work(cfg):
            calls.append(1)
            if len(calls)==1:raise failure()
        with patch('hy_backend.runtime',self.runtime(order)),patch.object(EvidenceStub,'capture',side_effect=OSError('disk issue')):
            recovery.run(Path('.'),{},work,lambda:0)
        self.assertEqual(len(calls),2)

    def test_unfinished_old_worker_prevents_restart(self):
        order=[];worker=Future()
        def work(cfg):
            exc=failure();exc._rpt_workers=[worker];raise exc
        with patch('hy_backend.runtime',self.runtime(order)),patch('concurrent.futures.wait',return_value=(set(),{worker})),self.assertRaises(urllib.error.HTTPError):
            recovery.run(Path('.'),{},work,lambda:0)
        self.assertEqual(order,['start','stop'])


class CacheRecoveryTests(unittest.TestCase):
    def test_runner_failure_markers_do_not_infer_parent_exit_or_print_prose(self):
        tail=('An invented ordinary line.\n'
              'CUDA error: an illegal memory access was encountered in fattn-common.cuh:1127\n'
              'level=ERROR msg="llama runner terminated" error="exit status 0xc0000409"\n'
              'runner_pid=789 exit code 17\n')
        reports=recovery.runner_failure_reports(tail)
        self.assertEqual(len(reports),3)
        self.assertEqual(reports[0]['native_site'],'fattn-common.cuh:1127')
        self.assertIsNone(reports[1]['runner_pid'])
        self.assertEqual(reports[1]['reported_returncode'],'0xc0000409')
        self.assertEqual(reports[2]['runner_pid'],789)
        self.assertEqual(reports[2]['reported_returncode'],'17')
        self.assertNotIn('ordinary',json.dumps(reports))
        self.assertNotIn('message',reports[0])

    def test_incident_keeps_http_resources_options_and_server_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);log=p/'server.log';log.write_text('startup\nCUDA illegal access\n',encoding='utf8')
            collector=recovery.Evidence(p)
            collector.history.append({'time':'before','gpu':{'stdout':'VRAM history'}})
            collector.module_history.append({'runtime_pid':123,'processes':[{'pid':456,'modules':[{'path':'native.dll','version':'1'}]}]})
            err=failure();err.http_details={'status':500,'body':'runner died'};err.request_id='request-one'
            cfg={'endpoint':'http://localhost','_runtime_log':str(log),'_runtime_pid':123,'model':'fake','num_ctx':16384,'_runtime_launch':{'parent_directory_restored':True}}
            with patch('runtime_recovery.resources',return_value={'gpu':'snapshot'}),patch('runtime_recovery.command',return_value={'collection_error':'probe unavailable'}),patch('runtime_recovery.powershell',return_value={'collection_error':'event access denied'}),patch('hy_backend.api',side_effect=ConnectionError('server gone')):
                incident=collector.capture(err,cfg,0,42)
            data=json.loads((p/'data/errors'/('runtime-'+incident+'.json')).read_text(encoding='utf8'))
            self.assertEqual(data['request_id'],'request-one')
            self.assertEqual(data['http']['body'],'runner died')
            self.assertEqual(data['saved_entries'],42)
            self.assertTrue(data['resource_history'])
            self.assertEqual(data['module_history'][0]['processes'][0]['modules'][0]['version'],'1')
            self.assertTrue(data['settings']['_runtime_launch']['parent_directory_restored'])
            self.assertIn('collection_error',data['/api/ps'])
            self.assertIn('CUDA illegal access',Path(data['server_tail']).read_text())

    def test_post_exit_retains_initial_tail_and_adds_late_exit_and_events(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);log=p/'server.log';log.write_text('first CUDA fault\n',encoding='utf8')
            collector=recovery.Evidence(p)
            cfg={'endpoint':'http://localhost','_runtime_log':str(log),'_runtime_pid':123,
                 '_recovery_run_id':'invented-run','_runtime_generation':2}
            collector.configure(cfg)
            collector.event('request_started',request_id='one',item_ids=['a'],monotonic_ns=10)
            collector.event('request_started',request_id='two',item_ids=['b'],monotonic_ns=11)
            collector.event('request_finished',request_id='one',status='transport_error',http_status=500)
            with patch('runtime_recovery.resources',return_value={}),patch('runtime_recovery.command',return_value={}),\
                 patch('runtime_recovery.powershell',return_value={'stdout':'invented Windows event'}),\
                 patch('hy_backend.api',return_value={}):
                incident=collector.capture(failure(),cfg,1,8)
                log.write_text('first CUDA fault\nlate runner exit\n',encoding='utf8')
                collector.finalize(incident,cfg,{'termination':'natural_exit','process_returncode':17})
            data=json.loads((p/'data/errors'/('runtime-'+incident+'.json')).read_text(encoding='utf8'))
            self.assertEqual(data['run_id'],'invented-run')
            self.assertEqual(data['generation'],2)
            self.assertEqual([r['request_id'] for r in data['inflight_requests']],['two'])
            self.assertEqual(len(data['recent_requests']),3)
            self.assertNotIn('late runner exit',Path(data['server_tail']).read_text())
            self.assertIn('late runner exit',Path(data['post_exit']['server_tail']).read_text())
            self.assertEqual(data['post_exit']['runtime_exit']['process_returncode'],17)
            self.assertNotIn(incident,collector.records)
            if recovery.os.name=='nt':self.assertIn('windows_events_after_exit',data['post_exit'])

    def test_request_evidence_is_bounded_and_does_not_copy_payloads(self):
        with tempfile.TemporaryDirectory() as directory:
            collector=recovery.Evidence(Path(directory))
            collector.configure({'_recovery_run_id':'invented','_runtime_generation':0})
            for index in range(140):
                collector.event('request_started',request_id=str(index),item_ids=['invented-id'],
                                prompt_body='must not enter incident metadata',response='neither this')
            self.assertEqual(len(collector.requests),128)
            self.assertEqual(len(collector.inflight),128)
            self.assertNotIn('prompt_body',collector.requests[-1])
            self.assertNotIn('response',collector.requests[-1])
            events=(collector.directory/'runtime-recovery.jsonl').read_text(encoding='utf8')
            self.assertNotIn('must not enter incident metadata',events)
            self.assertNotIn('neither this',events)
            collector.configure({'_recovery_run_id':'invented','_runtime_generation':1})
            self.assertFalse(collector.inflight)

    def test_unfinalized_incident_memory_is_bounded_without_deleting_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);collector=recovery.Evidence(p);captured=[]
            cfg={'endpoint':'http://localhost','_recovery_run_id':'invented','_runtime_generation':0}
            with patch('runtime_recovery.resources',return_value={}),patch('runtime_recovery.command',return_value={}),\
                 patch('runtime_recovery.powershell',return_value={}),patch('hy_backend.api',return_value={}),\
                 unittest.mock.patch('sys.stdout',new=io.StringIO()):
                for _ in range(recovery.MAX_INCIDENT_RECORDS+2):
                    captured.append(collector.capture(failure(),cfg,0,0))
            self.assertEqual(len(collector.records),recovery.MAX_INCIDENT_RECORDS)
            self.assertNotIn(captured[0],collector.records)
            self.assertTrue(all((collector.directory/('runtime-'+uid+'.json')).is_file() for uid in captured))

    def test_two_simultaneous_failures_have_one_recovery_owner(self):
        cfg={'model':'fake','language':'korean','batch_size':1,'parallel':2}
        rows=[dict(id=str(i),source='Example '+str(i),file='test.rpy',kind='dialogue') for i in range(2)]
        order=[];instances=[]
        class Pool:
            def __init__(self,*a,**k):self.first=not instances;instances.append(self)
            def submit(self,func,translate_func,batch,context,active,note):
                f=Future()
                if self.first:f.set_exception(failure())
                else:f.set_result(([dict(r,text='번역',model='fake',fingerprint=translation.fingerprint(cfg)) for r in batch],{'seconds':1}))
                return f
            def shutdown(self,**k):pass
        class Context:
            def __init__(self,*a):pass
            def same_scope(self,*a):return True
            def context(self,*a):return {}
        @contextmanager
        def runtime(active):
            order.append('start')
            try:yield active
            finally:order.append('stop')
        with tempfile.TemporaryDirectory() as directory,ExitStack() as stack:
            p=Path(directory);save_json(p/'data/catalog.json',rows)
            for target,value in [('hy_backend.runtime',runtime),('runtime_recovery.Evidence',EvidenceStub),
                                 ('runtime_recovery.resources',lambda:{}),('runtime_recovery.time.sleep',lambda _:None),
                                 ('runtime_recovery.cancelled',lambda:None),('translation.ThreadPoolExecutor',Pool),
                                 ('source_context.SourceContext',Context)]:stack.enter_context(patch(target,value))
            stack.enter_context(patch('name_hints.ensure_metadata',return_value={'names':{}}))
            stack.enter_context(patch('name_translation.mapping',return_value={}))
            translation.translate(p,cfg)
            self.assertEqual(order,['start','stop','start','stop'])
            self.assertEqual(len(translation.cache(p,cfg)),2)

    def test_http_body_is_preserved_before_exception_is_reported(self):
        err=failure();opener=unittest.mock.Mock();opener.open.side_effect=err
        with patch('translation.urllib.request.build_opener',return_value=opener),self.assertRaises(urllib.error.HTTPError):
            translation.request('http://localhost','/api/chat',{'model':'fake'})
        self.assertEqual(err.http_details['body'],'{"error":"runner crashed"}')
        self.assertEqual(err.http_details['status'],500)
        self.assertTrue(recovery.recoverable(err))

    def test_completed_sibling_saved_and_only_missing_entry_retried(self):
        cfg={'language':'korean','model':'fake','batch_size':1,'parallel':2}
        rows=[dict(id=str(i),source='Example '+str(i),file='test.rpy',kind='dialogue') for i in range(2)]
        def translated(row):return dict(row,text='번역',model='fake',fingerprint=translation.fingerprint(cfg))
        failed=Future();failed.set_exception(failure())
        success=Future();success.set_result(([translated(rows[1])],{'seconds':1}))
        class Pool:
            def __init__(self,*a,**k):self.futures=iter([failed,success])
            def submit(self,*a,**k):return next(self.futures)
            def shutdown(self,**k):pass
        class Context:
            def __init__(self,*a):pass
            def same_scope(self,*a):return True
            def context(self,*a):return {}
        with tempfile.TemporaryDirectory() as directory,ExitStack() as stack:
            p=Path(directory);save_json(p/'data/catalog.json',rows);known={}
            stack.enter_context(patch('name_hints.ensure_metadata',return_value={'names':{}}))
            stack.enter_context(patch('name_translation.mapping',return_value={}))
            stack.enter_context(patch('source_context.SourceContext',Context))
            with patch('translation.ThreadPoolExecutor',Pool),patch('translation.wait',return_value=({failed},set())),self.assertRaises(urllib.error.HTTPError):
                translation._translate(p,cfg,rows=rows,known=known)
            self.assertEqual(set(known),{'1'})
            self.assertEqual(set(translation.cache(p,cfg)),{'1'})
            calls=[]
            def work(batch,*args):calls.extend(r['id'] for r in batch);return [translated(r) for r in batch],{'seconds':1}
            with patch('translation.translate_batch',side_effect=work):translation._translate(p,cfg,rows=rows,known=known)
            self.assertEqual(calls,['0'])
            cache=[json.loads(line) for line in (p/'data/translations.jsonl').read_text(encoding='utf8').splitlines()]
            self.assertEqual([r['id'] for r in cache],['1','0'])
            # A second invocation is entirely offline, including no server startup.
            with patch('hy_backend.runtime',side_effect=AssertionError('must not start model')):
                translation.translate(p,cfg)


if __name__=='__main__':unittest.main()
