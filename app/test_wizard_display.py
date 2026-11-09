"""Wizard metadata and cached presentation checks with invented fixtures only."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import desktop_bridge as bridge
from display_text import compose
from output_paths import apply_options
from task_plan import arguments
import test_rpgmaker as rpg_tests
import rpgmaker_project

class WizardDisplayTests(unittest.TestCase):
    def test_auto_folder_kind_and_saved_display_without_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);game=root/'SampleVN';(game/'game').mkdir(parents=True);(game/'renpy').mkdir()
            result=bridge.project_language({'path':str(game)},root)
            self.assertTrue(result['source']);self.assertEqual(result['engine'],'renpy')
            project=root/'internal';(project/'data').mkdir(parents=True)
            (project/'project.json').write_text(json.dumps({'engine':'renpy','source':str(game),'source_language':'japanese','language':'korean'}))
            apply_options(project,{},textbox='1.2',show_reference=False)
            output=root/'SampleVN-kr';output.mkdir();(output/'project-link.json').write_text(json.dumps({'project':'../internal'}))
            for p in (project,output):
                result=bridge.project_language({'path':str(p)},root)
                self.assertFalse(result['source']);self.assertFalse(result['show_reference'])
                self.assertEqual(result['game_name'],'SampleVN');self.assertEqual(result['textbox_scale'],1.2)
                self.assertEqual(result['source_language'],'japanese')

    def test_settings_cli_and_presentation_persistence(self):
        self.assertTrue(bridge.validate_settings({})['show_reference'])
        self.assertFalse(bridge.validate_settings({'show_reference':False})['show_reference'])
        with self.assertRaises(ValueError):bridge.validate_settings({'show_reference':'false'})
        self.assertIn('--hide-reference',arguments(['display'],'X:/sample',show_reference=False))
        self.assertNotIn('--hide-reference',arguments(['display'],'X:/sample'))
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);cfg=apply_options(p,{},show_reference=False)
            self.assertFalse(cfg['show_reference']);self.assertFalse(apply_options(p,{})['show_reference'])

    def test_hidden_reference_preserves_controls_and_failed_original(self):
        row={'kind':'dialogue','source':'Hello, child.{nw}'}
        target={'text':'안녕, 얘야.{nw}'}
        self.assertEqual(compose(row,target,{'show_reference':False}),'안녕, 얘야.{nw}')
        self.assertIn('Hello, child.',compose(row,target,{'show_reference':True}))
        row={'kind':'string','source':'Go home'}
        self.assertEqual(compose(row,{'text':'집으로'},{'choices':['Go home'],'show_reference':False}),'집으로')
        self.assertEqual(compose(row,{'text':'Go home','status':'source_fallback'},{'show_reference':False}),'Go home')

    def test_rpg_reference_toggle_reuses_cache_and_retains_command_structure(self):
        sample=rpg_tests.RPGMakerTests();sample.setUp();self.addCleanup(sample.doCleanups)
        rows=sample.prepare('MZ',True);sample.populate(rows)
        saved=(sample.project/'data/translations.jsonl').read_bytes()
        with patch('translation.translate_batch',side_effect=AssertionError('No inference for display changes')):
            for visible in (True,False,True):
                with contextlib.redirect_stdout(io.StringIO()):rpgmaker_project.render(sample.project,dict(sample.cfg,show_reference=visible))
                commands=json.loads((sample.project/'output/www/data/Map001.json').read_text(encoding='utf-8'))['events'][1]['pages'][0]['list']
                self.assertEqual(len(commands),11)
                self.assertEqual('_rpt_original' in commands[1],visible)
                self.assertEqual('_rpt_choice_originals' in commands[3],visible)
                self.assertEqual(commands[6]['parameters'],[1,1,0])
                self.assertEqual((sample.project/'data/translations.jsonl').read_bytes(),saved)

if __name__=='__main__':unittest.main()
