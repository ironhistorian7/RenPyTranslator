"""Synthetic old APIs and hidden Tk only. No real project or subprocess."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch


class CompatibilityTests(unittest.TestCase):
    def test_unknown_config_key_proxy_and_unrelated_errors(self):
        class Config:
            font_transforms={}
            font_replacement_map={}
            def __getattr__(self,name):
                if name=='broken':raise RuntimeError('unexpected failure')
                raise Exception('config.%s is not a known configuration variable.' % name)
        class Group:
            def add(self,*args):return self
        prefs=SimpleNamespace(language='korean',font_transform=None)
        ns=dict(config=Config(),preferences=prefs,_preferences=prefs,basestring=str,FontGroup=Group,
                _rpt_language='korean',_rpt_choices={},menu=lambda items:items,
                _rpt_fontdata=dict(fonts={},coverage=dict(regular=[],hand=[]),
                                   preferred=dict(regular='neo.ttf',hand='pen.ttf'),fallback='noto.otf',bold='bold.ttf'))
        exec(Path(__file__).with_name('presentation_runtime.py').read_text(encoding='utf-8'),ns)
        ns['_rpt_language_fonts']()
        self.assertEqual(prefs.font_transform,'rpt_korean')
        self.assertIsInstance(ns['_rpt_transform']('original.ttf'),Group)
        with self.assertRaisesRegex(RuntimeError,'unexpected failure'):ns['_rpt_optional_config']('broken')

    def test_old_instance_reference_hook_keeps_original_english(self):
        class Text:
            def apply_custom_tags(self,tokens):
                return [(k,v.replace('India',self.name)) for k,v in tokens]
        fake=SimpleNamespace(TEXT_TAG=1,text=SimpleNamespace(text=SimpleNamespace(Text=Text)))
        ns=dict(renpy=fake,config=SimpleNamespace(custom_text_tags={}))
        exec(Path(__file__).with_name('reference_runtime.py').read_text(encoding='utf-8'),ns)
        text=Text();text.name='인디아'
        self.assertEqual(text.apply_custom_tags([(0,'India'),(1,'rpt_ref'),(0,'India'),(1,'/rpt_ref')]),
                         [(0,'인디아'),(0,'India')])

    def test_gui_repairs_collapsed_explicit_selection_and_reset(self):
        import gui
        with tempfile.TemporaryDirectory() as temp,patch.object(gui,'ROOT',Path(temp)),patch('gui.subprocess.Popen',side_effect=AssertionError('No execution')):
            window=tk.Tk();window.withdraw()
            try:
                app=gui.App(window)
                self.assertEqual(app.selected(),['run'])
                app.advanced_on.set(True);app.toggle()
                self.assertEqual(app.selected(),[])
                self.assertFalse(app.repairs_open)
                self.assertFalse(app.repairs.winfo_manager())
                self.assertIn('disabled',app.start.state())
                app.toggle_repairs()
                app.tasks['failed'].set(True);app.update_summary()
                self.assertEqual(app.selected(),['failed'])
                app.toggle_repairs()
                self.assertEqual(app.selected(),['failed'])
                self.assertIn('1개 선택',app.repairs_button.cget('text'))
                app.advanced_on.set(False);app.toggle()
                self.assertEqual(app.selected(),['run'])
                app.advanced_on.set(True);app.toggle()
                self.assertEqual(app.selected(),[])
                self.assertFalse(app.repairs_open)
            finally:
                window.destroy()


if __name__=='__main__':unittest.main()
