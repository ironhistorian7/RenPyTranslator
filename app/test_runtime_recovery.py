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
    def __init__(self,*args):pass
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def event(self,name,**kw):self.events.append(name)
    def configure(self,cfg):pass
    def capture(self,*args):self.events.append('capture');return 'incident-test'


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        EvidenceStub.events=[]
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for target,value in [('runtime_recovery.Evidence',EvidenceStub),('runtime_recovery.time.sleep',lambda _:None),
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

    def test_two_restarts_limit_and_no_source_fallback(self):
        order=[]
        def work(cfg):raise failure()
        with patch('hy_backend.runtime',self.runtime(order)),self.assertRaises(urllib.error.HTTPError):
            recovery.run(Path('.'),{},work,lambda:10)
        self.assertEqual(order,['start','stop']*3)
        self.assertIn('restart_limit',EvidenceStub.events)

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
