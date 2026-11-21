import json, os, shutil, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from patch_validation import run

class DeliveredRPGCheckTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.project = self.root/'project'; self.output = self.root/'output'
        self.project.mkdir(); self.output.mkdir(); self.cfg = {'engine':'rpgmaker'}
        self.write(self.output/'project-link.json', {'project':'../project','engine':'rpgmaker'})
    def write(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content) if not isinstance(content,str) else content,encoding='utf-8')
    def manifest(self, files):
        self.write(self.project/'data/rpg-output.json', {'files':files,'output':str(self.output)})
    def check(self, output=True):
        return run(self.project,self.cfg,output_root=self.output if output else None)
    def test_json_target_and_missing(self):
        name='data/Map001.json'; self.manifest([name])
        self.write(self.project/'data/rpg-source'/name, {'value':'original'})
        self.write(self.project/'staging'/name, {'value':'original'})
        self.write(self.output/name, '{ broken')
        self.assertEqual(self.check()['status'],'failed'); self.assertEqual(self.check(False)['status'],'passed')
        self.write(self.output/name, {'value':'original'}); self.write(self.project/'staging'/name, '{ broken')
        self.assertEqual(self.check()['status'],'passed'); self.assertEqual(self.check(False)['status'],'failed')
        (self.output/name).unlink(); self.assertEqual(self.check()['status'],'failed')
    def test_unchanged_database_uses_snapshot(self):
        self.manifest(['data/Map001.json'])
        for root in (self.project/'data/rpg-source',self.output): self.write(root/'data/Map001.json', {})
        self.write(self.project/'data/rpg-source/data/Actors.json', [])
        self.write(self.project/'staging/data/Actors.json', '{ broken')
        self.assertEqual(self.check()['status'],'passed')
    def test_ownership_manifest_and_boundaries(self):
        self.manifest(['../escape.json'])
        with self.assertRaises(ValueError): self.check()
        self.manifest([]); self.write(self.output/'project-link.json', {'project':'../other','engine':'rpgmaker'})
        with self.assertRaises(ValueError): self.check()
        self.write(self.output/'project-link.json', {'project':'../project','engine':'rpgmaker'})
        self.write(self.project/'data/rpg-output.json', {'files':[], 'output':str(self.root/'old')})
        with self.assertRaises(ValueError): self.check()
    def test_dispatch_preserves_selected_result_for_check(self):
        from types import SimpleNamespace
        from game_engine import dispatch
        self.write(self.project/'project.json', {'engine':'rpgmaker'})
        args=SimpleNamespace(source=None,project=self.output,command='check',tasks=None)
        with patch('rpgmaker_project.main') as main:
            self.assertTrue(dispatch(args,None))
        self.assertEqual(args.rpg_selected_output,self.output.resolve())
        self.assertEqual(args.project,self.project/'project.json')
        main.assert_called_once()

    @unittest.skipUnless(shutil.which('node'), 'Node parser unavailable')
    def test_real_javascript_parser_checks_selected_output(self):
        name='js/plugins/RPT_Bilingual.js'; self.manifest([name])
        self.write(self.project/'staging'/name, 'var ok = 1;'); self.write(self.output/name, 'var broken = ;')
        with patch('runtime_assets.runtime_executable',return_value=Path(shutil.which('node'))):
            result=self.check(); self.assertEqual(result['status'],'failed')
            self.assertEqual(result['failures'][0]['error_type'],'JavaScriptSyntaxError')
            self.assertEqual(result['failures'][0]['attribution'],'ours')
            self.assertEqual(self.check(False)['status'],'passed')
            self.write(self.output/name, 'var ok = 1;'); self.write(self.project/'staging'/name, 'var broken = ;')
            self.assertEqual(self.check()['status'],'passed'); self.assertEqual(self.check(False)['status'],'failed')

if __name__ == '__main__': unittest.main()
