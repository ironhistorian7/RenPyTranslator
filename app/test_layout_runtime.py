"""Execute the generated init code with write-only style properties; no game engine."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import textwrap
import unittest
from layout_policy import install


class WriteOnlyStyle:
    def __init__(self,props=None,parent=None):
        self.properties=list(props or [])
        self.parent=parent
    def __getattr__(self,name):
        raise AttributeError('Style values cannot be read: '+name)
    def add_properties(self,props):self.properties.append(dict(props))


def generated(scale,gui,styles):
    with tempfile.TemporaryDirectory() as temp:
        p=Path(temp);install(p,{'language':'korean','textbox_scale':scale})
        code=(p/'staging/game/zz_rpt_layout.rpy').read_text(encoding='utf-8')
        init=code.split('init 1050 python:\n',1)[1].split('\ntranslate ',1)[0]
        ns={'gui':gui,'style':styles}
        exec(compile(textwrap.dedent(init),'generated-layout','exec'),ns)
        return ns


class LayoutRuntimeTests(unittest.TestCase):
    def test_default_does_not_access_style_or_change_gui(self):
        class NoStyle:
            def __getattr__(self,name):raise AssertionError('Default must not access styles')
        gui=SimpleNamespace(textbox_height=320)
        ns=generated(None,gui,NoStyle())
        ns['_rpt_apply_layout'](True);ns['_rpt_apply_layout'](False)
        self.assertEqual(gui.textbox_height,320)

    def test_write_only_style_scaling_idempotence_and_exact_restore(self):
        original={'ysize':320,'font':'original.ttf','yalign':1.0}
        obj=WriteOnlyStyle([original]);gui=SimpleNamespace(textbox_height=320)
        ns=generated(1.2,gui,SimpleNamespace(say_window=obj))
        ns['_rpt_apply_layout'](True)
        self.assertEqual(gui.textbox_height,384)
        self.assertEqual(obj.properties[-1],{'yminimum':384,'ymaximum':384})
        obj.add_properties({'font':'later-font.ttf'})
        ns['_rpt_apply_layout'](True)
        self.assertEqual(gui.textbox_height,384)
        self.assertEqual(sum('yminimum' in p for p in obj.properties),1)
        ns['_rpt_apply_layout'](False)
        self.assertEqual(gui.textbox_height,320)
        self.assertEqual(obj.properties,[original,{'font':'later-font.ttf'}])

    def test_inherited_height_and_missing_gui_attribute_restore(self):
        parent=WriteOnlyStyle([{'yminimum':200,'ymaximum':400}])
        obj=WriteOnlyStyle([{'font':'keep.ttf'}],('window',))
        gui=SimpleNamespace()
        ns=generated(1.5,gui,SimpleNamespace(say_window=obj,get=lambda name:parent))
        ns['_rpt_apply_layout'](True)
        self.assertEqual(gui.textbox_height,300)
        ns['_rpt_apply_layout'](False)
        self.assertFalse(hasattr(gui,'textbox_height'))
        self.assertEqual(obj.properties,[{'font':'keep.ttf'}])
        self.assertEqual(parent.properties,[{'yminimum':200,'ymaximum':400}])

    def test_relative_height_stays_relative_and_unknown_height_unchanged(self):
        obj=WriteOnlyStyle();gui=SimpleNamespace(textbox_height=.25)
        ns=generated(1.2,gui,SimpleNamespace(say_window=obj))
        ns['_rpt_apply_layout'](True);self.assertAlmostEqual(gui.textbox_height,.3)
        obj=WriteOnlyStyle();gui=SimpleNamespace()
        ns=generated(1.2,gui,SimpleNamespace(say_window=obj))
        ns['_rpt_apply_layout'](True)
        self.assertEqual(obj.properties,[]);self.assertFalse(hasattr(gui,'textbox_height'))


if __name__=='__main__':unittest.main()
