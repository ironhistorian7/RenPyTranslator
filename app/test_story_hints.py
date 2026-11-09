"""In-memory scripts, stubbed Ren'Py display calls and disposable patch files only."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from engine import save_json
from story_guides import parse
from story_hints import infer_answers,infer_routes,install,GREEN,RED

SCRIPT='''label quiz:
    $ answer = renpy.input("Code?").strip().lower()
    if answer != "orchid":
        jump wrong
    else:
        jump accepted
label choose:
    menu:
        "Help":
            $ alice_affection += 2
            $ trust = trust - 1
            $ alice_route = False
            $ bonus = True
        "Wait":
            pass
label gates:
    if alice_route:
        jump alice_romance
    else:
        jump normal
    if bonus:
        call bonus_scene
label alice_romance:
    "Hello."
    return
label bonus_scene:
    "An extra conversation."
    return
label normal:
    return
'''


def runtime(data,location,choices=None):
    calls=[]
    renpy=SimpleNamespace(get_filename_line=lambda:location,
        translate_string=lambda s:{'Help':'도와준다','Code?':'암호를 입력하세요.'}.get(s,s),
        input=lambda prompt,*a,**k:calls.append(('input',prompt,a,k)) or 'typed')
    def menu(items,*a,**k):calls.append(('menu',items,a,k));return 'chosen'
    ns={'renpy':renpy,'_preferences':SimpleNamespace(language='korean'),'menu':menu,'nvl_menu':menu,
        '_rpt_hint_data':dict(data,language='korean',font='regular.ttf',bold_font='bold.ttf'),
        '_rpt_choices':choices or {},'_rpt_language':'korean'}
    # Install the actual existing bilingual wrapper first, matching init order.
    presentation=Path(__file__).with_name('presentation_runtime.py').read_text(encoding='utf-8')
    exec('def _rpt_wrap_menu'+presentation.split('def _rpt_wrap_menu',1)[1],ns)
    source=Path(__file__).with_name('hints_layout.py').read_text(encoding='utf-8')+'\n'+Path(__file__).with_name('hints_runtime.py').read_text(encoding='utf-8')
    exec(compile(source,'hints_runtime.py','exec'),ns)
    return ns,calls,source


class HintTests(unittest.TestCase):
    def test_four_colors_and_unaffected_choice_omitted(self):
        events,_=parse({'s.rpy':SCRIPT});routes=infer_routes(events,{'Alice':'앨리스'})
        self.assertEqual(len(routes),1)
        effects=routes[0]['hints']
        self.assertTrue(any(e['text']=='앨리스 호감도 +2' and e['color']==GREEN and not e['bold'] for e in effects))
        self.assertTrue(any(e['text']=='신뢰도 -1' and e['color']==RED for e in effects))
        self.assertTrue(any('닫힘' in e['text'] and e['color']==RED and e['bold'] for e in effects))
        self.assertTrue(any('추가 장면' in e['text'] and e['color']==GREEN and e['bold'] for e in effects))
        self.assertFalse(any('열림' in e['text'] for e in effects))

    def test_answer_success_branch_and_normalization(self):
        events,_=parse({'s.rpy':SCRIPT});answers=infer_answers(events)
        self.assertEqual(len(answers),1);self.assertEqual(answers[0]['values'],['orchid'])

    def test_wrong_answer_free_name_dynamic_or_changed_constant_not_claimed_correct(self):
        text='''default expected = "old"
label quiz:
    $ answer = renpy.input("Question?")
    $ expected = choose_random_answer()
    if answer == expected:
        jump accepted
    if answer == "wrong value":
        jump wrong
label name:
    $ name = renpy.input("Your name?")
    if name == "Alice":
        jump normal
'''
        self.assertEqual(infer_answers(parse({'s.rpy':text})[0]),[])

    def test_multiple_answers_and_python_block_location(self):
        script='''label quiz:
    python:
        raw = renpy.input("Code?")
        answer = raw.strip().lower()
    if answer in ("orchid", "rose"):
        jump success
    else:
        jump retry
'''
        answers=infer_answers(parse({'s.rpy':script})[0])
        self.assertEqual(answers[0]['line'],2)
        self.assertEqual(answers[0]['values'],['orchid','rose'])

    def test_conditional_effect_and_no_arbitrary_jump_scene(self):
        text='''label choose:
    menu:
        "Help":
            if trust >= 5:
                $ love += 1
            jump normal
        "Elsewhere":
            jump scene_two
label normal:
    return
label scene_two:
    "Ordinary next scene."
    return
'''
        hints=infer_routes(parse({'s.rpy':text})[0],{})
        self.assertEqual(len(hints),1)
        self.assertIn('신뢰도 >= 5',hints[0]['hints'][0]['text'])
        self.assertIn('일 때',hints[0]['hints'][0]['text'])

    def test_follow_choice_label_effects_and_optional_scene_to_shared_join(self):
        script='''label choose:
    menu:
        "Talk":
            jump private_talk
        "Leave":
            jump common
label private_talk:
    $ trust += 2
    "A private conversation."
    jump common
label common:
    $ day += 1
    return
'''
        routes=infer_routes(parse({'s.rpy':script})[0],{})
        self.assertEqual(len(routes),1)
        hints=routes[0]['hints']
        self.assertTrue(any(h['text']=='신뢰도 +2' for h in hints))
        self.assertTrue(any(h['text']=='추가 장면' and h['bold'] for h in hints))
        self.assertFalse(any('day' in h['text'] for h in hints))

    def test_menu_places_hint_above_untouched_english_and_preserves_actions(self):
        events,_=parse({'s.rpy':SCRIPT});routes=infer_routes(events,{})
        original='도와준다\n{size=*0.55}{cps=0}{rpt_ref}Help{/rpt_ref}{/cps}{/size}'
        ns,calls,source=runtime({'routes':routes},('game/s.rpy',routes[0]['line']),{'Help':original})
        action=object();self.assertEqual(ns['menu']([('Help',action),('Wait',7)],screen='custom'),'chosen')
        items=calls[-1][1];caption=items[0][0]
        self.assertIs(items[0][1],action);self.assertEqual(items[1],('Wait',7))
        self.assertEqual(caption.partition('\n{size=*0.55}')[2],original.partition('\n{size=*0.55}')[2])
        self.assertIn('{color='+GREEN+'}',caption);self.assertIn('{b}',caption)
        self.assertIn('[[',caption);self.assertEqual(calls[-1][3],{'screen':'custom'})
        previous=ns['menu'];exec(source,ns);self.assertIs(ns['menu'],previous)

    def test_input_prompt_translation_and_literal_answer_preserved(self):
        events,_=parse({'s.rpy':SCRIPT});answers=infer_answers(events)
        ns,calls,_=runtime({'answers':answers},('game/s.rpy',answers[0]['line']))
        ns['_rpt_reference_ready']=True
        value=ns['renpy'].input('Code?',default='keep',length=30)
        self.assertEqual(value,'typed')
        self.assertIn('암호를 입력하세요.\n',calls[-1][1]);self.assertIn('정답: orchid',calls[-1][1])
        self.assertIn('{rpt_ref}',calls[-1][1]);self.assertEqual(calls[-1][3],{'default':'keep','length':30})

    def test_scope_language_and_same_text_other_menu(self):
        events,_=parse({'s.rpy':SCRIPT});routes=infer_routes(events,{})
        ns,calls,_=runtime({'routes':routes},('game/s.rpy',999))
        ns['menu']([('Help',1)]);self.assertEqual(calls[-1][1],[('Help',1)])
        ns['renpy'].get_filename_line=lambda:('game/s.rpy',routes[0]['line'])
        ns['_preferences'].language='french';ns['menu']([('Help',1)])
        self.assertEqual(calls[-1][1],[('Help',1)])

    def test_patch_merge_repeat_separate_modes_preserve_cache_and_layout(self):
        events,warnings=parse({'s.rpy':SCRIPT})
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);(p/'data').mkdir()
            cache=p/'data/translations.jsonl';cache.write_bytes(b'unchanged-cache\n')
            cfg={'language':'korean'}
            output=p/'output';output.mkdir()
            label=p.name+'-korean-local-draft';archive=output/(label+'.zip')
            old={'game/tl/korean/s.rpy':b'original translation','game/zz_rpt_layout.rpy':b'leave layout untouched'}
            with zipfile.ZipFile(archive,'w') as z:
                for name,data in old.items():z.writestr(name,data)
            with (patch('engine.engine_command',side_effect=AssertionError('No game')),
                  patch('model_runtime.model_session',side_effect=AssertionError('No model')),
                  patch('scene_hints.summarize'),
                  patch('translation.read_catalog',side_effect=AssertionError('No translation scan'))):
                install(p,cfg,['routes'],events,warnings)
                install(p,cfg,['answers'],events,warnings)
                install(p,cfg,['answers'],events,warnings)
            self.assertEqual(cache.read_bytes(),b'unchanged-cache\n')
            state=json.loads((output/'game/tl/korean/rpt_hints/hints.json').read_text(encoding='utf-8'))
            self.assertEqual(len(state['answers']),1);self.assertEqual(len(state['routes']),1)
            with zipfile.ZipFile(archive) as z:
                self.assertEqual(len(z.namelist()),len(set(z.namelist())))
                for name,data in old.items():self.assertEqual(z.read(name),data)
                self.assertIn('game/zz_rpt_hints.rpy',z.namelist())
            self.assertFalse((output/'guides').exists())
            self.assertFalse((p/'staging').exists())
            # A later full translation restores these independently selected hints.
            from story_hints import restore
            (p/'staging/game').mkdir(parents=True)
            restore(p,cfg)
            self.assertEqual((p/'staging/game/zz_rpt_hints.rpy').read_bytes(),(output/'game/zz_rpt_hints.rpy').read_bytes())


if __name__=='__main__':unittest.main()
