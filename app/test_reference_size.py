"""Synthetic engine APIs and three invented lines; never read actual projects."""
from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest.mock import patch
from engine import save_json
from translation import catalog,render,fingerprint
from test_story_hints import runtime as hint_runtime


def runtime(version):
    class Layout:
        def segment(self,tokens,text_style,renders,text):return list(tokens)
    class Text:
        @staticmethod
        def apply_custom_tags(tokens):return [(k,{'India':'인디아'}.get(v,v)) for k,v in tokens]
    engine=SimpleNamespace(version_tuple=version,text=SimpleNamespace(text=SimpleNamespace(Layout=Layout,Text=Text)))
    ns=dict(renpy=SimpleNamespace(TEXT_TAG=1),config=SimpleNamespace(custom_text_tags={}))
    helper=Path(__file__).with_name('size_runtime.py').read_text(encoding='utf-8')
    with patch.dict('sys.modules',renpy=engine):exec(helper,ns)
    ns['renpy'].text=engine.text
    exec(Path(__file__).with_name('reference_runtime.py').read_text(encoding='utf-8'),ns)
    return engine,ns


class ReferenceSizeTests(unittest.TestCase):
    def test_version_boundary_and_resolved_size_not_a_global_setting(self):
        for version,native in (((7,3,5),False),((7,4,11),False),((7,5,3),False),((8,0,3),False),
                               ((7,6,0),True),((7,8,7),True),((8,1,0),True),((8,5,3),True)):
            engine,ns=runtime(version)
            for size in (28,32,40,56):
                tags=[(1,'size=_rpt_reference_55'),(0,'English'),(1,'/size')]
                out=engine.text.text.Layout().segment(tags,SimpleNamespace(size=size),{},None)
                self.assertEqual(out[0],(1,'size=*0.55' if native else 'size='+str(int(size*.55))))
                self.assertEqual(tags[0],(1,'size=_rpt_reference_55')) # do not mutate cached tokens
            original=[(1,'size=99'),(0,'Original game text'),(1,'/size')]
            self.assertEqual(engine.text.text.Layout().segment(original,SimpleNamespace(size=40),{},None),original)

    def test_new_and_wrong_legacy_reference_sizes_with_name_isolation(self):
        engine,ns=runtime((7,3,5))
        forms=[[(0,'India'),(1,'rpt_ref=0.55'),(1,'cps=0'),(0,'India'),(1,'/cps'),(1,'/rpt_ref')]]
        for value in ('*0.55','99','*0.1'):
            forms.append([(0,'India'),(1,'size='+value),(1,'cps=0'),(1,'rpt_ref'),(0,'India'),(1,'/rpt_ref'),(1,'/cps'),(1,'/size')])
        for tokens in forms:
            out=engine.text.text.Text.apply_custom_tags(tokens)
            out=engine.text.text.Layout().segment(out,SimpleNamespace(size=40),{},None)
            self.assertIn((1,'size=22'),out)
            self.assertEqual([v for k,v in out if k==0],['인디아','India'])
            self.assertEqual(sum(k==1 and v=='/size' for k,v in out),1)

    def test_hints_precede_reference_in_both_new_and_old_patches(self):
        ns,_,_=hint_runtime({},('fiction.rpy',1))
        for ref in ('{rpt_ref=0.55}{cps=0}English{/cps}{/rpt_ref}',
                    '{size=*0.55}{cps=0}{rpt_ref}English{/rpt_ref}{/cps}{/size}',
                    '{size=99}{cps=0}{rpt_ref}English{/rpt_ref}{/cps}{/size}'):
            result=ns['_rpt_hint_insert']('선택지\n'+ref,'{color=#00ff00}호감도 +2{/color}')
            self.assertLess(result.index('호감도'),result.index('\n'+ref))
            self.assertTrue(result.endswith(ref))

    def test_rerender_repairs_existing_size_without_translating_or_erasing_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);target=p/'staging/game/tl/korean/example.rpy';target.parent.mkdir(parents=True)
            target.write_text('translate korean example:\n    # centered "English reference."\n    centered ""\n',encoding='utf-8')
            cfg=dict(language='korean',model='fake');row=catalog(p,cfg)[0]
            cache=p/'data/translations.jsonl'
            cache.write_text(json.dumps(dict(row,text='한글 안내입니다.',model='fake',fingerprint=fingerprint(cfg)))+'\n',encoding='utf-8')
            before=cache.read_bytes()
            target.write_text('translate korean example:\n    # centered "English reference."\n    centered "이전 표시{size=*0.55}English{/size}"\n',encoding='utf-8')
            with patch('translation.request',side_effect=AssertionError('No retranslation')):
                render(p,cfg)
                after=target.read_bytes();render(p,cfg)
            self.assertEqual(cache.read_bytes(),before)
            self.assertEqual(target.read_bytes(),after)
            self.assertIn('{rpt_ref=0.55}',target.read_text(encoding='utf-8'))
            self.assertNotIn('{size=*0.55}',target.read_text(encoding='utf-8'))
            helper=(p/'staging/game/zz_rpt_reference.rpy').read_text(encoding='utf-8')
            self.assertIn('_rpt_reference_sizes',helper)


if __name__=='__main__':unittest.main()
