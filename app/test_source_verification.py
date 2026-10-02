"""Disposable invented distributions only; no game or model execution."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from engine import manifest,save_json,sha,verify_source,verify_prepared_project


class SourceVerificationTests(unittest.TestCase):
    def test_snapshot_missing_files_does_not_fall_back_to_original_or_delete_cache(self):
        (self.project/'data').mkdir(parents=True)
        cachefile=self.project/'data/translations.jsonl';cachefile.write_bytes(b'keep this cache')
        with patch('engine.manifest',side_effect=AssertionError('No source scan')):
            with self.assertRaisesRegex(ValueError,'Prepared project is incomplete'):
                verify_prepared_project(self.project)
        self.assertEqual(cachefile.read_bytes(),b'keep this cache')

    def test_snapshot_does_not_require_original_source_or_change_its_report(self):
        (self.project/'staging/game').mkdir(parents=True)
        (self.project/'data/templates').mkdir(parents=True)
        save_json(self.project/'data/catalog.json',[])
        with patch('engine.manifest',side_effect=AssertionError('No source scan')):
            result=verify_source(self.project,{'source':'Z:/absent','_prepared_snapshot':True})
        self.assertFalse(result['original_source_accessed'])
        self.assertIsNone(result['unchanged'])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source';self.project=self.root/'project'
        self.cfg=dict(source=str(self.source),language='korean')
        for name in ('game/scripts.rpa','game/compiled_only.rpyc','game/loose.rpy',
                     'game/loose.rpyc','renpy/bootstrap.py','lib/site.pyo','Game.exe'):
            self.write(name,'original '+name)

    def write(self,name,text='generated'):
        path=self.source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8')

    def baseline(self):
        save_json(self.project/'data/source-manifest.json',manifest(self.source))
        return (self.project/'data/source-manifest.json').read_bytes()

    def test_playing_and_applying_patch_does_not_invalidate_baseline_or_hash_mutable_files(self):
        before=self.baseline()
        for name in ('game/tl/korean/scripts/story.rpy','game/tl/korean/scripts/story.rpyc',
                     'game/tl/korean/fonts/Neo.ttf','game/tl/korean/rpt_hints/hints.json',
                     'game/zz_rpt_korean.rpy','game/zz_rpt_reference.rpyc','game/zz_rpt_language.rpy','game/zz_rpt_language.rpyc',
                     'game/cache/bytecode.rpyb','game/saves/persistent','log.txt','traceback.txt',
                     'errors.txt','renpy/__pycache__/bootstrap.cpython-312.pyc','renpy/bootstrap.pyc','game/loose.rpyc'):
            self.write(name)
        with patch('engine.sha',wraps=sha) as digest:
            result=verify_source(self.project,self.cfg)
        checked={call.args[0].relative_to(self.source).as_posix() for call in digest.call_args_list}
        self.assertTrue(result['unchanged']);self.assertEqual(result['changed'],[])
        self.assertEqual(checked,{'game/scripts.rpa','game/compiled_only.rpyc','game/loose.rpy','renpy/bootstrap.py','lib/site.pyo','Game.exe'})
        self.assertEqual((self.project/'data/source-manifest.json').read_bytes(),before)
        self.assertIn('game/tl/korean',result['ignored_paths'])

    def test_old_manifest_with_runtime_files_remains_usable_after_they_change_or_disappear(self):
        for name in ('game/cache/screens.rpyb','log.txt','game/saves/persistent','game/tl/korean/old.rpy'):
            self.write(name,'old')
        before=self.baseline()
        (self.source/'log.txt').unlink()
        self.write('game/cache/screens.rpyb','new');self.write('game/tl/korean/old.rpy','new patch')
        self.assertTrue(verify_source(self.project,self.cfg)['unchanged'])
        self.assertEqual((self.project/'data/source-manifest.json').read_bytes(),before)

    def test_real_original_and_compiled_only_changes_still_fail(self):
        before=self.baseline()
        for name in ('game/scripts.rpa','game/compiled_only.rpyc','game/loose.rpy','renpy/bootstrap.py','lib/site.pyo','Game.exe'):
            with self.subTest(name=name):
                self.write(name,'changed')
                with self.assertRaisesRegex(RuntimeError,'Original source changed'):verify_source(self.project,self.cfg)
                result=json.loads((self.project/'data/source-verification.json').read_text(encoding='utf-8'))
                self.assertEqual(result['changed'],[name])
                self.write(name,'original '+name)
        self.assertEqual((self.project/'data/source-manifest.json').read_bytes(),before)

    def test_original_deletion_new_mod_and_other_language_not_silently_ignored(self):
        self.baseline()
        for name in ('game/new_story.rpy','game/zz_rpt_unrelated.rpy','game/tl/french/story.rpy'):
            self.write(name)
            with self.assertRaisesRegex(RuntimeError,'Original source changed'):verify_source(self.project,self.cfg)
            (self.source/name).unlink()
        (self.source/'game/loose.rpy').unlink()
        with self.assertRaisesRegex(RuntimeError,'Original source changed'):verify_source(self.project,self.cfg)

    def test_missing_source_is_not_a_successful_check(self):
        self.baseline()
        with self.assertRaisesRegex(FileNotFoundError,'source folder not found'):
            verify_source(self.project,dict(self.cfg,source=str(self.root/'missing')))


if __name__=='__main__':unittest.main()
