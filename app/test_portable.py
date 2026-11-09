"""Small fixtures only; never inspect a real project or start a game/model."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from engine import save_json


class PortableTests(unittest.TestCase):
    def test_output_default_suffix_advanced_and_portable_move(self):
        from output_paths import destination,apply_options
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);project=root/'data/projects/My Game';project.mkdir(parents=True)
            with patch('output_paths.ROOT',root):
                cfg=apply_options(project,{'source':'D:/Games/My Game','language':'korean'})
                out,label=destination(project,cfg)
                self.assertEqual(out,root/'project/My Game-kr');self.assertEqual(label,'My Game-kr')
                out.mkdir(parents=True)
                self.assertEqual(destination(project,cfg)[0],out)
                cfg=apply_options(project,cfg,output='results',suffix='-ko',textbox='1.2')
                self.assertEqual(destination(project,cfg)[0],root/'results/My Game-ko')
                self.assertEqual(cfg['textbox_scale'],1.2)
                self.assertEqual(apply_options(project,cfg,textbox='default')['textbox_scale'],None)
            newroot=root/'moved';newroot.mkdir();moved=newroot/'data/projects/My Game'
            import shutil
            shutil.copytree(project,moved)
            (newroot/'results/My Game-ko').mkdir(parents=True)
            with patch('output_paths.ROOT',newroot):
                self.assertEqual(destination(moved,apply_options(moved,{'source':'D:/Games/My Game','language':'korean'}))[0],newroot/'results/My Game-ko')

    def test_layout_only_removes_legacy_expansion(self):
        from layout_policy import install
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);game=p/'staging/game';game.mkdir(parents=True)
            path=game/'zz_rpt_korean.rpy'
            path.write_text('init 900 python:\n    _rpt_textbox_before = getattr(gui, "textbox_height", None)\n    _rpt_textbox_height = 500\n    keep_fonts = True\ntranslate korean python:\n    _rpt_language_fonts()\n    if _rpt_textbox_height is not None:\n        gui.textbox_height = _rpt_textbox_height\ntranslate korean style say_window:\n    ysize _rpt_textbox_height\n',encoding='utf-8')
            install(p,{'language':'korean'})
            self.assertNotIn('_rpt_textbox',path.read_text(encoding='utf-8'))
            self.assertIn('keep_fonts = True',path.read_text(encoding='utf-8'))
            code=(game/'zz_rpt_layout.rpy').read_text(encoding='utf-8')
            self.assertIn('_rpt_layout_scale = None',code)
            install(p,{'language':'korean','textbox_scale':1.2})
            self.assertIn('_rpt_layout_scale = 1.2',(game/'zz_rpt_layout.rpy').read_text(encoding='utf-8'))

    def test_reference_bypasses_name_replacement(self):
        from display_text import compose,reference_text
        class Text:
            @staticmethod
            def apply_custom_tags(tokens):
                return [(k,{'India':'인디아'}.get(v,v)) for k,v in tokens]
        fake=SimpleNamespace(TEXT_TAG=1,text=SimpleNamespace(text=SimpleNamespace(Text=Text)))
        ns={'renpy':fake,'config':SimpleNamespace(custom_text_tags={})}
        exec(Path(__file__).with_name('reference_runtime.py').read_text(encoding='utf-8'),ns)
        self.assertEqual(Text.apply_custom_tags([(0,'India'),(1,'rpt_ref'),(0,'India'),(1,'/rpt_ref')]),[(0,'인디아'),(0,'India')])
        text=compose({'kind':'dialogue','source':'Hello [hero!t].'},{'text':'안녕 [hero].'},
                     {'names':{'hero':{}},'reference_guard':True})
        self.assertIn('{rpt_ref=0.55}{cps=0}Hello [rpt_reference_name(hero)!q].{/cps}{/rpt_ref}',text)
        self.assertEqual(reference_text('Hi [cast[0].name!tq].'),'Hi [cast[0].name!q].')
        self.assertEqual(reference_text('[[literal] [score:.2f]'),'[[literal] [score:.2f]')
        ns['_rpt_name_map']={'India':'인디아'}
        self.assertEqual(ns['rpt_reference_name']('인디아'),'India')
        self.assertEqual(ns['rpt_reference_name']('사용자 이름'),'사용자 이름')

    def test_gui_arguments_keep_each_fix_independent(self):
        from gui import arguments
        self.assertEqual(arguments(['layout'],'P',scale='default'),
                         ['tasks','--tasks','layout','--project','P','--suffix=-kr','--textbox-scale','default'])
        with self.assertRaises(ValueError):arguments([],'P')
        with self.assertRaises(ValueError):arguments(['failed'],'P',source=True)

    def test_layout_repair_does_not_render_scan_scripts_or_load_model(self):
        from translate_game import repair
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);(p/'data').mkdir();(p/'data/translations.jsonl').write_text('',encoding='utf-8')
            (p/'data/story-hints.json').write_text('{"routes": [], "answers": []}',encoding='utf-8')
            with patch('translate_game.render',side_effect=AssertionError('No render')),patch('translate_game.install_support',side_effect=AssertionError('No font work')),patch('automatic.script_sources',side_effect=AssertionError('No scripts')),patch('engine.engine_command'),patch('translate_game.package') as package:
                repair(p,{'language':'korean','model':'fake'},'layout')
                self.assertEqual(package.call_args.args[1]['_render_ids'],set())
            self.assertIn('screen _rpt_hint_choice', (p/'staging/game/zz_rpt_hints.rpy').read_text(encoding='utf-8'))


if __name__=='__main__':unittest.main()
