import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import vulkan_inventory

class ResolveTests(unittest.TestCase):
    def test_matches_identity_under_supplied_child_environment(self):
        device={'luid':'0000000000000001','vendor_id':4098,'device_id':5}
        match=dict(device,luid_valid=True,index=3,name='AMD')
        original=dict(os.environ)
        env=dict(os.environ,VK_LOADER_LAYERS_DISABLE='~implicit~')
        with patch('native_process.run',return_value=SimpleNamespace(stdout=json.dumps([match]))) as run:
            self.assertEqual(vulkan_inventory.resolve_selector(device,env)['index'],3)
            self.assertEqual(run.call_args.kwargs['env']['VK_LOADER_LAYERS_DISABLE'],'~implicit~')
            self.assertEqual(dict(os.environ),original)
    def test_rejects_ambiguous_and_missing_identity(self):
        device={'luid':'0000000000000001','vendor_id':4098,'device_id':5}
        match=dict(device,luid_valid=True,index=3)
        with patch('native_process.run',return_value=SimpleNamespace(stdout=json.dumps([match,match]))):
            with self.assertRaisesRegex(RuntimeError,'uniquely resolved'):
                vulkan_inventory.resolve_selector(device,{})
        with self.assertRaises(ValueError): vulkan_inventory.resolve_selector({'name':'AMD'},{})

if __name__=='__main__': unittest.main()
