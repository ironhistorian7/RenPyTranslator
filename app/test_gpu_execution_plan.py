import os
import unittest
from unittest.mock import patch
from gpu_execution_plan import device_plan
N=dict(id='GPU-0001',name='N',total=12288,selectable=True)
A=dict(id='DXGI-0000000000000001',name='A',vendor='amd',total=16384,selectable=True,backend='vulkan',vulkan_index=2,luid='0000000000000001',vendor_id=4098,device_id=100)
class PlanTests(unittest.TestCase):
    def plan(self,devices,**prefs):
        with patch.dict(os.environ,{},clear=True): return device_plan({},devices,dict(gpu_mode='auto',**prefs))
    def test_mixed_and_amd_tiers(self):
        plan=self.plan([N,A]);self.assertEqual([x['backend'] for x in plan],['cuda','vulkan']);self.assertEqual([x['parallel'] for x in plan],[2,3]);self.assertEqual(plan[1]['selector'],2);self.assertEqual(plan[1]['config']['kv_cache_type'],'q8_0')
    def test_nvidia_only_remains_legacy(self): self.assertEqual(self.plan([N]),[])
    def test_official_rocm_plan_preserves_separate_vulkan_fallback(self):
        amd=dict(A,backend='rocm',rocm_index=7)
        nvidia, rocm=self.plan([N,amd])
        self.assertEqual(nvidia['backend'],'cuda')
        self.assertEqual(rocm['backend'],'rocm')
        self.assertEqual(rocm['selector'],7)
        self.assertEqual(rocm['vulkan_index'],2)
    def test_backend_discovery_uses_selected_runtime_and_keeps_vulkan(self):
        from desktop_bridge import _select_amd_backend
        gpu={k:A[k] for k in ('id','name','total','vendor','luid','vendor_id','device_id')}
        physical=[dict(luid_valid=True,luid=A['luid'],vendor_id=A['vendor_id'],device_id=A['device_id'],index=2)]
        for runtime_id,hip_match,expected in (
            ('ollama-0.40.15',{'index':7},'rocm'),
            ('ollama-0.34.2',None,'vulkan'),
        ):
            with self.subTest(runtime_id=runtime_id), \
                 patch('runtime_assets.runtime_executable',return_value=runtime_id) as executable, \
                 patch('amd_rocm.resolve_rocm',return_value=hip_match) as probe:
                adapter=dict(gpu)
                _select_amd_backend(adapter,physical,runtime_id)
                executable.assert_called_once()
                self.assertEqual(executable.call_args.kwargs['runtime_id'],runtime_id)
                self.assertEqual(probe.call_args.args[0],runtime_id)
                self.assertEqual(adapter['backend'],expected)
                self.assertEqual(adapter['vulkan_index'],2)
    def test_selected_amd_and_override(self):
        with patch.dict(os.environ,{},clear=True): plan=device_plan({},[N,A],dict(gpu_mode='selected',gpu_ids=[A['id']],inference_overrides={'parallel':1,'num_ctx':8192}))
        self.assertEqual(len(plan),1);self.assertEqual(plan[0]['parallel'],1);self.assertEqual(plan[0]['config']['num_ctx'],8192)
    def test_selected_unavailable_rejected(self):
        with self.assertRaises(ValueError): device_plan({},[N,dict(A,selectable=False)],dict(gpu_mode='selected',gpu_ids=[A['id']]))
    def test_environment_mask_excludes_amd(self):
        with patch.dict(os.environ,{'OLLAMA_VULKAN':'0'},clear=True): self.assertEqual(device_plan({},[N,A],{'gpu_mode':'auto'}),[])
    def test_configure_attaches_plan_and_serial_only_amd_device(self):
        import gpu_policy
        from contextlib import redirect_stdout
        import io
        with patch.dict(os.environ,{},clear=True),redirect_stdout(io.StringIO()):
            mixed=gpu_policy.configure({},devices=[N,A],preferences={'gpu_mode':'auto'},environment={})
            serial=gpu_policy.configure({'_serial_model_request':True},devices=[A],preferences={'gpu_mode':'auto'},environment={})
            nvidia=gpu_policy.configure({},devices=[N],preferences={'gpu_mode':'auto'},environment={})
        self.assertEqual(len(mixed['_gpu_device_plan']),2)
        self.assertNotIn('_gpu_device_plan',serial);self.assertEqual(serial['_runtime_device']['id'],A['id'])
        self.assertNotIn('_gpu_device_plan',nvidia)
    def test_baseline_prefers_usable_gpu_and_respects_cuda_mask(self):
        import gpu_policy
        from contextlib import redirect_stdout
        import io
        small=dict(N,total=4096)
        with patch.dict(os.environ,{},clear=True),redirect_stdout(io.StringIO()):
            prefs={'gpu_mode':'auto'}
            mixed=gpu_policy.configure({},devices=[N,A],preferences=prefs,environment={})
            fallback=gpu_policy.configure({},devices=[small,A],preferences=prefs,environment={})
            selected=gpu_policy.capacity_profile([small,A],{'gpu_mode':'selected','gpu_ids':[small['id']]},{},gpu_policy.rules())[1]
            masked=gpu_policy.capacity_profile([N,A],prefs,{'CUDA_VISIBLE_DEVICES':'-1'},gpu_policy.rules())[1]
        self.assertEqual(mixed['_hardware_policy']['capacity_device'],N['id'])
        self.assertEqual(fallback['_hardware_policy']['capacity_device'],A['id'])
        self.assertNotIn('_hardware_error',fallback)
        self.assertEqual(selected['status'],'unsupported')
        self.assertEqual(masked['capacity_device'],A['id'])
        with patch.dict(os.environ,{},clear=True),self.assertRaises(ValueError):
            gpu_policy.configure({},devices=[small,A],preferences={'gpu_mode':'selected','gpu_ids':[small['id']]},environment={})
    def test_multiple_amd(self):
        b=dict(A,id='DXGI-0000000000000002',luid='0000000000000002',vulkan_index=3,total=8192)
        self.assertEqual([x['parallel'] for x in self.plan([A,b])],[3,2])
if __name__=='__main__': unittest.main()
