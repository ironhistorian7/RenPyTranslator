import json
import tempfile
import threading
import time
import unittest
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import atomic_files
import gpu_dispatcher
import translation
from engine import save_json


class FakePool:
    def __init__(self):
        self.workers=[dict(device_id='nvidia',device_backend='cuda',worker_slots=2,num_ctx=2048,endpoint='http://gpu-n'),
                      dict(device_id='amd',device_backend='vulkan',worker_slots=3,num_ctx=2048,endpoint='http://gpu-a')]
        self.stops=[]; self.starts=[]; self.stopped=threading.Event()
    def stop(self,identity): self.stops.append(identity);self.stopped.set()
    def start(self,identity):
        self.starts.append(identity)
        return dict(next(w for w in self.workers if w['device_id']==identity))


class Evidence:
    def __init__(self,*args):pass
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def event(self,*args,**kwargs):pass
    def capture(self,*args):return 'incident'
    def finalize(self,*args):pass
    def generation_failed(self,*args):pass


class Context:
    def __init__(self,*args):pass
    def same_scope(self,*args):return True
    def context(self,*args,**kwargs):return {}


class TransportError(ConnectionError):
    _rpt_transport=True


class GPUTranslationDispatchTests(unittest.TestCase):
    def test_model_switch_retry_uses_clean_source_and_device_context(self):
        cfg=dict(language='korean',model='alpha',batch_size=1,parallel=1,num_ctx=8192,
                 context_txt_enabled=False)
        row=dict(id='1',source='Example',file='test.rpy',kind='dialogue')
        pool=FakePool()
        pool.workers=[dict(device_id='nvidia',device_backend='cuda',worker_slots=1,
                           model='alpha',num_ctx=8192,context_txt_enabled=False,endpoint='a'),
                      dict(device_id='amd',device_backend='vulkan',worker_slots=1,
                           model='beta',num_ctx=8192,context_txt_enabled=True,endpoint='b')]
        calls=[]; constructed=[]
        class CapturingContext(Context):
            def __init__(self,rows,project,config): constructed.append(config['context_txt_enabled'])
            def context(self,batch,config,**kwargs):
                return {'txt_enabled':config['context_txt_enabled']}
        def work(batch,context,active,note):
            calls.append((active['model'],context['txt_enabled'],note))
            if len(calls)==1:
                # The previous model is unavailable while the other GPU is healthy.
                dispatch.states['nvidia']['deadline']=float('inf')
                raise translation.BatchTranslationError([], [dict(row=batch[0],error='invalid')], {'seconds':0})
            return [dict(batch[0],text='번역',model=active['model'],fingerprint=translation.fingerprint(active))],{'seconds':0}
        with tempfile.TemporaryDirectory() as directory,ExitStack() as stack:
            project=Path(directory);save_json(project/'data/catalog.json',[row]);known={}
            for target,value in [('gpu_dispatcher.Evidence',Evidence),('gpu_dispatcher.cancelled',lambda:None),
                                 ('source_context.SourceContext',CapturingContext),('translation.translate_batch',work)]:
                stack.enter_context(patch(target,value))
            stack.enter_context(patch('name_hints.ensure_metadata',return_value={'names':{}}))
            stack.enter_context(patch('name_translation.mapping',return_value={}))
            with gpu_dispatcher.RuntimeDispatch(pool,project,cfg,lambda:len(known)) as dispatch:
                translation._translate(project,dict(cfg,_gpu_dispatch=dispatch),rows=[row],known=known)
        self.assertEqual(constructed,[True])
        self.assertEqual(calls,[('alpha',False,''),('beta',True,'')])
        self.assertEqual(known['1']['model'],'beta')

    def test_parallel_cache_and_independent_recovery(self):
        cfg=dict(language='korean',model='fake',batch_size=1,parallel=2,num_ctx=2048)
        rows=[dict(id=str(i),source='Example '+str(i),file='test.rpy',kind='dialogue') for i in range(12)]
        prefix=dict(rows[0],text='이미 번역',model='fake',fingerprint=translation.fingerprint(cfg))
        pool=FakePool();guard=threading.Lock();both=threading.Barrier(5,timeout=8)
        counts=Counter();peaks=Counter();calls=[];completion=[];writes=[];main=threading.get_ident()
        def work(batch,context,active,note):
            row=batch[0];identity=active['device_id'];generation=active['_dispatch_generation']
            with guard:
                counts[identity]+=1;peaks[identity]=max(peaks[identity],counts[identity])
                calls.append((row['id'],identity,generation));first_attempt=sum(c[0]==row['id'] for c in calls)==1
            try:
                if int(row['id'])<=5 and first_attempt:
                    both.wait()
                    if identity=='amd':
                        if row['id']!='2':pool.stopped.wait(5)
                        raise TransportError('same failed server generation')
                    pool.stopped.wait(5)
                    time.sleep(.02 if row['id']=='1' else .001)
                with guard:completion.append(row['id'])
                return [dict(row,text='번역 '+row['id'],model='fake',fingerprint=translation.fingerprint(cfg))],{'seconds':.01}
            finally:
                with guard:counts[identity]-=1
        original=atomic_files.replace_cache_file
        def replace(src,dst):
            writes.append(threading.get_ident());return original(src,dst)
        with tempfile.TemporaryDirectory() as directory,ExitStack() as stack:
            project=Path(directory);save_json(project/'data/catalog.json',rows)
            path=project/'data/translations.jsonl';prefix_bytes=(json.dumps(prefix,ensure_ascii=False)+'\n').encode('utf8');path.write_bytes(prefix_bytes)
            known={'0':prefix}
            for target,value in [('gpu_dispatcher.Evidence',Evidence),('gpu_dispatcher.RESTART_DELAYS',(0,0,0)),
                                 ('gpu_dispatcher.cancelled',lambda:None),('source_context.SourceContext',Context),
                                 ('translation.translate_batch',work),('atomic_files.replace_cache_file',replace)]:
                stack.enter_context(patch(target,value))
            stack.enter_context(patch('name_hints.ensure_metadata',return_value={'names':{}}))
            stack.enter_context(patch('name_translation.mapping',return_value={}))
            with gpu_dispatcher.RuntimeDispatch(pool,project,cfg,lambda:len(known)) as dispatch:
                translation._translate(project,dict(cfg,_gpu_dispatch=dispatch),rows=rows,known=known)
            records=[json.loads(line) for line in path.read_text(encoding='utf8').splitlines()]
            ids=[r['id'] for r in records]
            self.assertTrue(path.read_bytes().startswith(prefix_bytes))
            self.assertCountEqual(ids,[r['id'] for r in rows]);self.assertEqual(len(ids),len(set(ids)))
            self.assertTrue(writes);self.assertEqual(set(writes),{main})
            self.assertEqual(peaks,{'nvidia':2,'amd':3})
            self.assertEqual(pool.stops,['amd']);self.assertNotIn('nvidia',pool.starts)
            self.assertNotIn('0',[x[0] for x in calls])
            self.assertLess(completion.index('3'),completion.index('1'))
            for uid,device,generation in calls:
                if uid not in ('2','4','5'):
                    self.assertEqual(sum(c[0]==uid for c in calls),1,uid)
            for uid in ('2','4','5'):
                self.assertEqual(sum(c[0]==uid for c in calls),2,uid)
            self.assertEqual(set(known),set(ids))

if __name__=='__main__':unittest.main()
