import unittest
from contextlib import contextmanager
from unittest.mock import patch
import gpu_runtime_pool

class PoolTests(unittest.TestCase):
    def test_device_policy_metadata_and_independent_generation(self):
        starts=[]; stops=[]
        @contextmanager
        def fake_runtime(cfg):
            starts.append(cfg)
            try: yield dict(cfg,endpoint='new',_runtime_pid=len(starts))
            finally: stops.append(cfg['_runtime_device']['id'])
        devices=[dict(id='nvidia',vendor='nvidia',backend='cuda',selector='GPU-123',
                      name='RTX',luid='0000000000000001',vendor_id=4318,device_id=123,
                      parallel=2,config={'num_ctx':16384}),
                 dict(id='amd',vendor='amd',backend='vulkan',selector=1,
                      name='Radeon',luid='0000000000000002',vendor_id=4098,device_id=456,
                      parallel=3,config={'num_ctx':16384})]
        original={'model':'local-hy','parallel':9,'num_ctx':4096,
                  '_hardware_policy':{'status':'ok','profile':'12gb'},
                  '_runtime_pid':999,'endpoint':'stale'}
        with patch.object(gpu_runtime_pool,'runtime',fake_runtime):
            with gpu_runtime_pool.runtime_pool(original,devices) as pool:
                self.assertEqual([w['worker_slots'] for w in pool.workers],[2,3])
                self.assertEqual([w['num_ctx'] for w in pool.workers],[16384,16384])
                self.assertEqual(starts[1]['_runtime_device']['luid'],'0000000000000002')
                self.assertEqual(starts[1]['_runtime_device']['device_id'],456)
                self.assertNotIn('endpoint',starts[1])
                self.assertNotIn('_runtime_pid',starts[1])
                self.assertEqual(starts[1]['_hardware_policy']['source'],'mixed_gpu_plan')
                self.assertEqual(starts[1]['_hardware_policy']['vendor'],'amd')
                self.assertEqual(starts[1]['_hardware_policy']['parallel'],3)
                restarted=pool.restart('amd')
                self.assertEqual(restarted['_runtime_generation'],1)
                self.assertEqual(starts[0]['_runtime_generation'],0)
                self.assertEqual(starts[1]['_runtime_generation'],0)
                self.assertEqual(stops,['amd'])
                self.assertEqual(next(w for w in pool.workers if w['device_id']=='nvidia')['_runtime_generation'],0)
        self.assertEqual(stops,['amd','nvidia','amd'])
        self.assertEqual(original['_hardware_policy'],{'status':'ok','profile':'12gb'})

    def test_distinct_models_survive_restart(self):
        starts=[]
        @contextmanager
        def fake_runtime(cfg):
            starts.append(dict(cfg))
            yield dict(cfg,endpoint='test')
        devices=[dict(id='n',vendor='nvidia',backend='cuda',selector='GPU-1',config={'model':'hy:test'}),
                 dict(id='a',vendor='amd',backend='vulkan',selector=0,config={'model':'generic:test'})]
        with patch.object(gpu_runtime_pool,'runtime',fake_runtime):
            with gpu_runtime_pool.runtime_pool({'model':'common'},devices) as pool:
                pool.restart('a')
        self.assertEqual([c['model'] for c in starts],['hy:test','generic:test','generic:test'])
        self.assertEqual(starts[2]['_hardware_policy']['effective_model'],'generic:test')

    def test_pending_model_rejected_before_server_start(self):
        device=dict(id='n',vendor='nvidia',backend='cuda',selector='GPU-1')
        with patch.object(gpu_runtime_pool,'runtime') as start:
            with self.assertRaisesRegex(ValueError,'Prepare the shared local model'):
                with gpu_runtime_pool.runtime_pool({'_hardware_pending_model':{'x':1}},[device]): pass
            start.assert_not_called()

    def test_per_device_settings_survive_independent_restart(self):
        starts=[]
        @contextmanager
        def fake_runtime(cfg):
            starts.append(cfg)
            yield dict(cfg,endpoint='owned-test-server')
        devices=[dict(id='n',vendor='nvidia',backend='cuda',selector='GPU-1',parallel=2,
                      config=dict(num_ctx=16384,kv_cache_type='q8_0',context_txt_enabled=True),
                      overrides=dict(parallel=2,kv_cache_type='q8_0')),
                 dict(id='a',vendor='amd',backend='vulkan',selector=1,parallel=4,
                      config=dict(num_ctx=16384,kv_cache_type='q4_0',context_txt_enabled=True),
                      overrides=dict(parallel=4,kv_cache_type='q4_0'))]
        original=dict(model='invented-model',parallel=9,kv_cache_type='f16',
                      _hardware_policy=dict(effective_settings=dict(parallel=9,kv_cache_type='f16')))
        with patch.object(gpu_runtime_pool,'runtime',fake_runtime):
            pool=gpu_runtime_pool.RuntimePool(original,devices)
            # Editing the caller's next-job plan must not change a running job.
            devices[1]['parallel']=7
            devices[1]['config']['kv_cache_type']='f16'
            devices[1]['overrides']['parallel']=7
            try:
                pool.start('n');pool.start('a')
                peer=pool.workers[0]
                restarted=pool.restart('a')
                self.assertIs(pool.workers[0],peer)
                self.assertEqual([c['parallel'] for c in starts],[2,4,4])
                self.assertEqual([c['kv_cache_type'] for c in starts],['q8_0','q4_0','q4_0'])
                self.assertEqual(restarted['worker_slots'],4)
                self.assertEqual(restarted['_hardware_policy']['effective_settings'],
                                 dict(num_ctx=16384,parallel=4,kv_cache_type='q4_0',context_txt_enabled=True))
                self.assertEqual(restarted['_hardware_policy']['device_overrides'],
                                 dict(parallel=4,kv_cache_type='q4_0'))
                self.assertEqual(peer['_runtime_generation'],0)
                self.assertEqual(restarted['_runtime_generation'],1)
            finally: pool.close()
        self.assertEqual(original['parallel'],9)
        self.assertEqual(original['kv_cache_type'],'f16')

if __name__=='__main__': unittest.main()
