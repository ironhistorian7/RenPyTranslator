"""Invented captions only; real-engine display checks live in build/verify_hint_engines.py."""
import re
import unittest
from types import SimpleNamespace
from hints_layout import _rpt_hint_geometry, _rpt_hint_compact
from test_story_hints import runtime


def measure(text, width, size):
    plain = re.sub(r'\{[^}]*\}', '', text)
    return sum(max(1, (len(line)*size//2+width-1)//width) * size for line in plain.split('\n'))


def geometry(captions, effects, width=1280, height=720):
    return _rpt_hint_geometry(captions, effects, width, height, 28, measure,
        lambda e,t,s: t, lambda c,h: c+'\n'+h)


class HintLayoutTests(unittest.TestCase):
    def test_short_hints_fit_without_panel(self):
        result=geometry(['First','Second'],[[{'text':'trust +2'}],[]])
        self.assertIsNone(result['panel'])
        self.assertIn('trust +2', result['rows'][0]['caption'])

    def test_long_hints_keep_choices_and_disjoint_panes(self):
        captions=['선택지 %d\n{size=*0.55}English reference{/size}' % i for i in range(12)]
        effects=[[{'text':'장면: '+('설명 '*1000),'kind':'scene'}, {'text':'신뢰도 +2'}] for c in captions]
        for width,height in ((1920,1080),(800,600),(320,240)):
            result=geometry(captions,effects,width,height)
            a,b=result['list'],result['panel']
            self.assertIsNotNone(b)
            self.assertTrue(a[0]+a[2]<=b[0] or a[1]+a[3]<=b[1])
            for rect in (a,b):
                self.assertGreaterEqual(rect[0],0);self.assertGreaterEqual(rect[1],0)
                self.assertLessEqual(rect[0]+rect[2],width);self.assertLessEqual(rect[1]+rect[3],height)
            for caption,row in zip(captions,result['rows']):
                self.assertTrue(row['caption'].startswith(caption))
                self.assertTrue(row['more'])

    def test_conditions_never_become_unconditional(self):
        effect={'text':'신뢰도 +2 ('+'condition and '*90+'x일 때)'}
        self.assertEqual(_rpt_hint_compact(effect),'신뢰도 +2 (조건부)')
        self.assertIn('condition',effect['text'])

    def screen_runtime(self):
        entry={'file':'fiction.rpy','line':1,'source':'Help','hints':[dict(text='trust +2',color='#00ff00',bold=False)]}
        ns,_,_=runtime({'routes':[entry]},('fiction.rpy',1))
        exports=ns['renpy']
        exports.menu_kwargs={'custom':'keep'};exports.menu_args=None
        exports.has_screen=lambda name: True
        ns['ui']=SimpleNamespace(adjustment=lambda:object())
        return ns,exports

    def test_original_values_and_menu_scope_restored_even_on_error(self):
        for fail in (False,True):
            ns,exports=self.screen_runtime()
            original_kwargs=exports.menu_kwargs
            value=object();items=[('Help',value),('Disabled',None)]
            def previous(received,**kwargs):
                self.assertIs(received,items)
                self.assertIs(received[0][1],value)
                self.assertIsNone(received[1][1])
                self.assertEqual(exports.menu_args,())
                self.assertEqual(exports.menu_kwargs['screen'],'_rpt_hint_choice')
                self.assertEqual(kwargs['_kwargs']['screen'],'_rpt_hint_choice')
                if fail:raise RuntimeError('synthetic')
                return value
            wrapped=ns['_rpt_hint_menu'](previous)
            if fail:
                with self.assertRaisesRegex(RuntimeError,'synthetic'):wrapped(items,_args=())
            else:self.assertIs(wrapped(items,_args=()),value)
            self.assertIs(exports.menu_kwargs,original_kwargs)
            self.assertIsNone(exports.menu_args)
            self.assertIsNone(ns['_rpt_hint_session'])


if __name__=='__main__':unittest.main()
