import unittest
from types import SimpleNamespace
from ui_policy import discover,standard,apply_policy
from display_text import compose
from translation import validate_text
from font_policy import details

class PresentationTests(unittest.TestCase):
    def test_standard_and_custom_items_on_same_reskinned_menu(self):
        p=discover({'anything.rpy':'''screen navigation():
    textbutton _("Save") action ShowMenu("save")
    textbutton _("Secret Gallery") action ShowMenu("trophies")
screen trophies():
    textbutton "Secret room"
    textbutton "Return"
label start:
    menu:
        "Return":
            pass
'''})
        self.assertIn('Secret Gallery',p['uses']);self.assertIn('Secret room',p['uses'])
        self.assertEqual(standard('Save'),'저장');self.assertIsNone(standard('Secret Gallery'))
        self.assertEqual(standard('Replay'),'다시보기');self.assertEqual(standard('Achievements'),'업적')
        self.assertIn('Return',p['choices'])

    def test_menu_and_story_choice_with_identical_source(self):
        metadata={'runtime_choices':True,'choices':['Return']}
        self.assertEqual(compose({'kind':'string','source':'Return'},{'text':'돌아가기'},metadata),'돌아가기')
        out=compose({'kind':'dialogue','source':'Return'},{'text':'돌아가'},metadata)
        self.assertIn('돌아가\n{size=*0.55}',out)

    def test_short_text_punctuation_and_real_symbols(self):
        self.assertIn('punctuation-only translation',validate_text('Save','.',{}))
        self.assertIn('punctuation-only translation',validate_text('Go there','*',{}))
        self.assertEqual(validate_text('...','...',{}),[])

    def test_runtime_preserves_actions_and_routes_fonts(self):
        from pathlib import Path
        class Group:
            def __init__(self):self.items=[]
            def add(self,f,a,b):self.items.append((f,a,b));return self
        cfg=SimpleNamespace(font_transforms={},font_name_map={},font_replacement_map={})
        prefs=SimpleNamespace(language='korean',font_transform=None)
        action=object()
        ns={'config':cfg,'preferences':prefs,'_preferences':prefs,'basestring':str,'FontGroup':Group,
            '_rpt_language':'korean','_rpt_choices':{'Return':'돌아가\n{size=*0.55}Return{/size}'},
            '_rpt_fontdata':{'fonts':{'writing.ttf':'hand','icons.ttf':'icon'},'coverage':{'regular':[[44032,55203]],'hand':[[44032,55203]]},
                            'preferred':{'regular':'neo.ttf','hand':'pen.ttf'},'fallback':'noto.otf','bold':'neob.ttf'},
            'menu':lambda items,**kw:items}
        exec(Path(__file__).with_name('presentation_runtime.py').read_text(encoding='utf-8'),ns)
        out=ns['menu']([('Return',action)])
        self.assertIs(out[0][1],action);self.assertIn('size=*0.55',out[0][0])
        self.assertEqual(ns['_rpt_transform']('writing.ttf').items[0][0],'pen.ttf')
        self.assertEqual(ns['_rpt_transform']('ordinary.ttf').items[-1],('ordinary.ttf',None,None))
        self.assertEqual(ns['_rpt_transform']('icons.ttf'),'icons.ttf')
        prefs.language=None
        self.assertEqual(ns['_rpt_transform']('writing.ttf'),'writing.ttf')
        self.assertEqual(ns['menu']([('Return',action)])[0][0],'Return')

    def test_legacy_style_registry_and_inline_aliases(self):
        from pathlib import Path
        class Group:
            def add(self,*args):return self
        original=SimpleNamespace(font='custom-handwriting.ttf')
        cfg=SimpleNamespace(font_name_map={},font_replacement_map={})
        prefs=SimpleNamespace(language='korean')
        ns={'config':cfg,'preferences':prefs,'_preferences':prefs,'basestring':str,'FontGroup':Group,
            'renpy':SimpleNamespace(style=SimpleNamespace(styles={('anything',):original})),
            '_rpt_language':'korean','_rpt_choices':{},'menu':lambda items:items,
            '_rpt_fontdata':{'fonts':{'custom-handwriting.ttf':'hand'},'coverage':{'regular':[],'hand':[]},
                            'preferred':{'regular':'neo.ttf','hand':'pen.ttf'},'fallback':'noto.otf','bold':'neob.ttf'}}
        exec(Path(__file__).with_name('presentation_runtime.py').read_text(encoding='utf-8'),ns)
        ns['_rpt_language_fonts']()
        self.assertIsInstance(original.font,Group)
        self.assertIsInstance(cfg.font_name_map['custom-handwriting.ttf'],Group)
        prefs.language=None;ns['_rpt_language_fonts']()
        self.assertEqual(original.font,'custom-handwriting.ttf')

if __name__=='__main__':unittest.main()
