import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from engine import save_json
from language_panel import install,export,options
from translate_game import main


class LanguagePanelTests(unittest.TestCase):
    def test_panel_only_does_not_open_catalog_run_engine_or_model(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);project=root/'internal';out=root/'result'
            save_json(project/'project.json',{'language':'korean','model':'fake','source':'Z:/Forbidden/Game'})
            save_json(project/'data/tool-options.json',{'output_root':str(out)})
            save_json(root/'old-output/project-link.json',{'project':'../internal'})
            untouched=project/'staging/game/tl/korean/script.rpy'
            untouched.parent.mkdir(parents=True);untouched.write_text('# unchanged',encoding='utf8')
            with (patch('translate_game.read_catalog',side_effect=AssertionError('No catalog')),
                  patch('engine.engine_command',side_effect=AssertionError('No engine')),
                  patch('translation.request',side_effect=AssertionError('No model')),
                  patch('translate_game.prepare',side_effect=AssertionError('No source read'))):
                main(['language','--project',str(root/'old-output'),'--language-corner','left'])
            self.assertEqual(untouched.read_text(encoding='utf8'),'# unchanged')
            exported=out/'Game-kr'
            self.assertTrue((exported/'game/zz_rpt_language.rpy').exists())
            import zipfile
            with zipfile.ZipFile(exported/'Game-kr-language-panel.zip') as z:
                self.assertIn('game/zz_rpt_language.rpy',z.namelist())
                self.assertNotIn('game/tl/korean/script.rpy',z.namelist())

    def test_defaults_and_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp)
            cfg=options(project,{'language':'korean'},'left',25)
            files=install(project,cfg)
            before=files[0].read_bytes()
            self.assertIn(b"_rpt_panel_corner = \"left\"",before)
            install(project,cfg)
            self.assertEqual(files[0].read_bytes(),before)
            with self.assertRaises(ValueError):options(project,cfg,margin=-1)
            self.assertEqual(options(project,{'language':'korean'})['language_margin'],25)

    def test_task_selection_and_help(self):
        from task_plan import plan,arguments
        self.assertEqual(plan(['run','language']),['run'])
        self.assertEqual(plan(['language']),['language'])
        with self.assertRaises(ValueError):arguments(['language'],'source',source=True)
        with contextlib.redirect_stdout(io.StringIO()) as output:main([])
        self.assertIn('--language-corner',output.getvalue())


if __name__=='__main__':unittest.main()
