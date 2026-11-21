import io
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch
import desktop_bridge
import gpu_policy
from gpu_execution_plan import device_plan

N=dict(id='GPU-0001',name='N',total=12288,selectable=True)
M=dict(id='GPU-0002',name='M',total=24576,selectable=True)
A=dict(id='DXGI-0000000000000001',name='A',vendor='amd',total=16384,selectable=True,backend='vulkan',vulkan_index=2)
LOST='GPU-0003'

class PerDeviceOverrides(unittest.TestCase):
    def test_preview_matches_plan_and_precedence(self):
        prefs={'gpu_mode':'all','inference_overrides':{'parallel':5,'kv_cache_type':'f16','num_ctx':8192},
               'gpu_overrides':{N['id']:{'model':'n','parallel':3,'kv_cache_type':'q4_0'},M['id']:{'model':'m','parallel':5},A['id']:{'model':'a','parallel':7},LOST:{'parallel':9}}}
        with patch.dict('os.environ',{},clear=True):
            preview=gpu_policy.inference_preview([N,M,A],prefs,{})
            plan=device_plan({},[N,M,A],prefs,{})
        views={d['id']:d for d in preview['devices']}
        self.assertEqual(views[N['id']]['recommended']['parallel'],2)
        self.assertEqual(views[M['id']]['recommended']['parallel'],4)
        self.assertEqual([d['parallel'] for d in plan],[3,5,7])
        self.assertEqual([d['config']['kv_cache_type'] for d in plan],['q4_0','q8_0','q8_0'])
        for d in plan:
            self.assertEqual(d['parallel'],views[d['id']]['effective']['parallel'])
            self.assertEqual({k:v for k,v in d['config'].items() if k!='model'},{k:views[d['id']]['effective'][k] for k in ('num_ctx','kv_cache_type','context_txt_enabled')})
        self.assertEqual(gpu_policy.validate_inference(prefs)['gpu_overrides'][LOST],{'parallel':9})
        self.assertNotIn(LOST,views)
    def test_dual_ignores_common_overrides_and_uses_device_defaults(self):
        small=dict(N,id='GPU-0004',total=6144)
        overrides={small['id']:{'model':'small','parallel':1},N['id']:{'model':'large','parallel':3}}
        for common in ({},{'inference_overrides':{'num_ctx':8192,'context_txt_enabled':True}},
                       {'inference_preset':'8gb'}):
            prefs=dict(gpu_mode='all',gpu_overrides=overrides,**common)
            preview=gpu_policy.inference_preview([small,N],prefs,{})
            plan=device_plan({},[small,N],prefs,{})
            expected=[(4096,False),(16384,True)]
            self.assertEqual([(d['effective']['num_ctx'],d['effective']['context_txt_enabled']) for d in preview['devices']],expected)
            self.assertEqual([(d['config']['num_ctx'],d['config']['context_txt_enabled']) for d in plan],expected)
            self.assertIsNone(preview['selected_preset'])

    def test_incomplete_preview_and_required_execution(self):
        for overrides,missing in [({},['model','parallel']),({N['id']:{'model':'x'}},['parallel'])]:
            prefs=dict(gpu_mode='all',gpu_overrides=overrides)
            view=gpu_policy.inference_preview([N,A],prefs,{})
            self.assertEqual(view['devices'][0]['missing'],missing)
            with self.assertRaises(ValueError):device_plan({},[N,A],prefs,{})
        prefs=dict(gpu_mode='all',gpu_overrides={N['id']:{'model':'one','parallel':2},A['id']:{'model':'two','parallel':4}},
                   inference_preset='8gb',inference_overrides={'parallel':10,'num_ctx':8192})
        configured=gpu_policy.configure({'model':'common'},[N,A],prefs,{})
        self.assertEqual(configured['model'],'one')
        self.assertEqual(configured['num_ctx'],16384)
        self.assertEqual(configured['parallel'],2)
        self.assertEqual(configured['_hardware_policy']['source'],'per_gpu')
        self.assertIsNone(configured['_hardware_policy']['selected_preset'])
        serial=gpu_policy.configure({'model':'common','_serial_model_request':True},[N,A],prefs,{})
        self.assertEqual(serial['model'],'one')
        self.assertEqual(serial['parallel'],1)
        self.assertEqual(serial['num_ctx'],16384)
        self.assertEqual(gpu_policy.inference_preview([N],prefs,{})['selected_preset'],'8gb')
        self.assertEqual(gpu_policy.inference_preview([N],prefs,{})['effective']['parallel'],10)

    def test_single_override_and_serial(self):
        prefs={'gpu_mode':'auto','gpu_overrides':{N['id']:{'parallel':6,'kv_cache_type':'f16'}}}
        with patch.dict('os.environ',{},clear=True),redirect_stdout(io.StringIO()):
            regular=gpu_policy.configure({},[N],prefs,{})
            serial=gpu_policy.configure({'_serial_model_request':True},[N],prefs,{})
        self.assertNotIn('_gpu_device_plan',regular)
        self.assertEqual(serial['parallel'],1)
        self.assertNotEqual(serial.get('kv_cache_type'),'f16')
    def test_validation_and_missing_preservation(self):
        saved=desktop_bridge.validate_settings({'gpu_overrides':{LOST:{'parallel':1,'kv_cache_type':'q8_0'}}})
        self.assertIn(LOST,saved['gpu_overrides'])
        for bad in ({N['id']:{'parallel':True}},{N['id']:{'parallel':33}},{N['id']:{'kv_cache_type':'bad'}},{N['id']:{'num_ctx':511}},{'bad':{}}):
            with self.subTest(bad=bad),self.assertRaises(ValueError): gpu_policy.validate_inference({'gpu_overrides':bad})
    def test_selected_and_large_warning_rules(self):
        prefs={'gpu_mode':'selected','gpu_ids':[M['id']], 'gpu_overrides':{M['id']:{'parallel':5}}}
        with patch.dict('os.environ',{},clear=True):
            self.assertEqual([d['id'] for d in gpu_policy.inference_preview([N,M,A],prefs,{})['devices']],[M['id']])
            self.assertEqual(gpu_policy.inference_preview([N,M,A],prefs,{})['devices'][0]['warnings'],[])
            prefs['gpu_overrides'][M['id']]['parallel']=1
            self.assertEqual(gpu_policy.inference_preview([N,M,A],prefs,{})['devices'][0]['warnings'],[])
    def test_models_and_gate(self):
        overrides={N['id']:{'model':'hy:test','num_ctx':8192,'context_txt_enabled':False,'parallel':3},
                   A['id']:{'model':'generic:test','parallel':4,'kv_cache_type':'f16'}}
        for mode in ('all','selected','auto'):
            prefs=dict(gpu_mode=mode,gpu_ids=[N['id'],A['id']],gpu_overrides=overrides)
            plan=device_plan({'model':'common'},[N,A],prefs,{})
            enabled=mode!='auto'
            self.assertEqual(plan[0]['config'].get('model'), 'hy:test' if enabled else None)
            self.assertEqual(plan[1]['config'].get('model'), 'generic:test' if enabled else None)
            self.assertEqual(plan[0]['config']['num_ctx'],8192 if enabled else 16384)
        self.assertNotIn('model',device_plan({'model':'common'},[A],dict(gpu_mode='all',gpu_overrides=overrides),{})[0]['config'])
        for bad in ({'model':5},{'model':' x '},{'context_txt_enabled':1},{'num_ctx':True}):
            with self.assertRaises(ValueError): gpu_policy.validate_inference({'gpu_overrides':{N['id']:bad}})

    def test_readonly_preview_unselected(self):
        with patch('desktop_bridge.policy_devices',return_value=[N]),patch.dict('os.environ',{},clear=True):
            self.assertEqual(desktop_bridge.preview_inference_settings({'gpu_mode':'selected','gpu_ids':[]})['devices'],[])

    def test_saved_settings_roundtrip_drives_device_plan(self):
        overrides={N['id']:{'model':'n','parallel':2,'kv_cache_type':'q8_0'},
                   A['id']:{'model':'a','parallel':4,'kv_cache_type':'q4_0'},LOST:{'parallel':1}}
        with tempfile.TemporaryDirectory() as directory,patch.object(desktop_bridge,'ROOT',Path(directory)):
            desktop_bridge.save_settings(dict(gpu_mode='all',gpu_overrides=overrides))
            saved=desktop_bridge.preferences()
            plan=device_plan({},[N,A],saved,{})
        self.assertEqual(saved['gpu_overrides'],overrides)
        self.assertEqual([d['parallel'] for d in plan],[2,4])
        self.assertEqual([d['config']['kv_cache_type'] for d in plan],['q8_0','q4_0'])
        self.assertEqual([d['id'] for d in plan],[N['id'],A['id']])

if __name__=='__main__': unittest.main()
