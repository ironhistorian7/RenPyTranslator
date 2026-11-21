import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import amd_rocm


class ROCmResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.exe=self.base/'ollama.exe';self.exe.touch()
        self.gpu={'name':'Example AMD','luid':'ab12','total_mib':16312}
        self.peer={'name':'Example AMD','luid':'ab12','total':16312}
        self.hip={'index':0,'name':'Example AMD','total_mib':16368,'pci_bus_id':'0000:08:00.0'}

    def pack(self,name):
        path=self.base/'lib'/'ollama'/name;path.mkdir(parents=True)
        for n in ('ggml-hip.dll','amdhip64_7.dll'):(path/n).touch()

    def resolve(self, side_effect, peers=None, env=None):
        with patch('gpu_inventory.dxgi_adapters',return_value=[self.peer] if peers is None else peers), patch('native_process.run',side_effect=side_effect) as run:
            result=amd_rocm.resolve_rocm(self.exe,self.gpu,env or {})
            return result,run

    def output(self,entries):return SimpleNamespace(stdout=json.dumps(entries))

    def test_official_preferred_and_environment_isolated(self):
        self.pack('rocm_v7_1');self.pack('rocm_gfx906')
        result,run=self.resolve([self.output([self.hip])],env={'CUDA_VISIBLE_DEVICES':'-1','HIP_VISIBLE_DEVICES':'1','X':'y'})
        self.assertEqual(result['variant'],'rocm_v7_1');self.assertEqual(result['index'],0)
        self.assertEqual(run.call_count,1);self.assertEqual(run.call_args.kwargs['env'],{'X':'y'})

    def test_experimental_pack_is_ignored(self):
        self.pack('rocm_gfx906')
        result,run=self.resolve([])
        self.assertIsNone(result);self.assertFalse(run.called)

    def test_fails_closed_for_ambiguous_peers_and_hip(self):
        self.pack('rocm_v7_1')
        result,_=self.resolve([self.output([self.hip])],peers=[self.peer,dict(self.peer,luid='other')]);self.assertIsNone(result)
        result,_=self.resolve([self.output([self.hip,dict(self.hip,index=1)])]);self.assertIsNone(result)

    def test_pci_identity_without_dxgi(self):
        self.pack('rocm_v7_1');self.gpu['pci_bus_id']='08:00.0'
        result,_=self.resolve([self.output([self.hip])],peers=[])
        self.assertEqual(result['pci_bus_id'],'0000:08:00.0')

    def test_missing_pack_and_failed_probe_fall_through(self):
        result,run=self.resolve([]);self.assertIsNone(result);self.assertFalse(run.called)
        self.pack('rocm_v7_1');self.pack('rocm_gfx906')
        result,run=self.resolve([OSError('bad DLL')])
        self.assertIsNone(result);self.assertEqual(run.call_count,1)


if __name__=='__main__':unittest.main()
