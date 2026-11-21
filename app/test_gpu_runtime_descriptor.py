import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import desktop_bridge


class Response(io.BytesIO):
    def __enter__(self):return self
    def __exit__(self,*args):self.close()


class RuntimeDescriptorTests(unittest.TestCase):
    def test_device_metadata_and_only_loaded_process_owners(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);runtime=root/'data/runtime';runtime.mkdir(parents=True)
            for port,ident,backend,parallel in [(1234,'GPU-a','cuda',2),(1235,'DXGI-b','vulkan',3)]:
                (runtime/f'10-{port}.json').write_text(json.dumps(dict(pid=port,endpoint=f'http://127.0.0.1:{port}',model='local',
                    native_launch={'runtime_device':{'id':ident,'name':'GPU '+ident,'backend':backend}},
                    hardware_policy={'parallel':parallel,'num_ctx':16384},policy={})),encoding='utf8')
            class Opener:
                def open(self,url,timeout):
                    models=[] if ':1235/' in url else [{'name':'local','size':100,'size_vram':80,'details':{}}]
                    return Response(json.dumps({'models':models}).encode())
            with patch.object(desktop_bridge,'ROOT',root),patch.object(desktop_bridge,'descendants',side_effect=lambda p:set(p)),patch('urllib.request.build_opener',return_value=Opener()):
                result,owners=desktop_bridge.runtime_info()
            byid={x['device_id']:x for x in result}
            self.assertEqual(owners,{1234})
            self.assertEqual((byid['GPU-a']['device_name'],byid['GPU-a']['device_backend'],byid['GPU-a']['parallel'],byid['GPU-a']['num_ctx']),('GPU GPU-a','cuda',2,16384))
            self.assertEqual((byid['DXGI-b']['device_backend'],byid['DXGI-b']['parallel'],byid['DXGI-b']['loaded']),('vulkan',3,[]))

if __name__=='__main__':unittest.main()
