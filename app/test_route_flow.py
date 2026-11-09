"""Invented scripts only; no installed games, model, or GPU access."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from route_flow import branches
from story_guides import parse
from story_hints import infer_routes
from story_media import enrich, navigation, navigation_destinations, asset_index
from scene_hints import update


def effects(script):
    events=parse({'fixture.rpy':script})[0]
    plans=branches(events)
    return plans,infer_routes(events,{},contexts={c['id']:unique for c,unique,_,_ in plans})


class RouteFlowTests(unittest.TestCase):
    def test_composite_is_not_animation_and_image_alias_resolves(self):
        scripts={'f.rpy':'''image combo = Composite((100, 100), (0, 0), "images/a.png", (0, 0), "images/b.png")
image alias = "combo"
label start:
    show alias
'''}
        events=parse(scripts)[0];media=next(e for e in events if e['kind']=='media')
        self.assertEqual(media['assets'],['images/a.png','images/b.png'])
        self.assertFalse(media['animation'])
    def test_assigned_state_skips_false_if_without_else(self):
        plans,rows=effects('''label start:
    menu:
        "Go":
            $ ready = False
            if ready:
                $ love += 99
            $ trust += 1
''')
        texts=[h['text'] for r in rows for h in r['hints']]
        self.assertEqual(texts,['신뢰도 +1'])

    def test_repeated_call_returns_and_accumulates(self):
        plans,rows=effects('''label start:
    menu:
        "Go":
            call reward
            call reward
            $ trust += 1
            return
label reward:
    $ love += 2
    return
''')
        self.assertEqual([h['text'] for h in rows[0]['hints']],['호감도 +4','신뢰도 +1'])

    def test_call_writes_affect_caller_condition(self):
        plans,rows=effects('''label start:
    menu:
        "Go":
            call prepare
            if flag:
                $ trust += 2
            else:
                $ trust -= 50
label prepare:
    $ flag = True
    return
''')
        self.assertEqual([h['text'] for h in rows[0]['hints']],['신뢰도 +2'])

    def test_nested_conditions_and_virtual_increment(self):
        plans,rows=effects('''label start:
    menu:
        "Go":
            $ trust += 1
            if trust >= 3:
                if friend:
                    $ love += 2
''')
        hint=next(h for h in rows[0]['hints'] if h['base_text']=='호감도 +2')
        self.assertEqual(hint['guards'],['trust + 1 >= 3','friend'])

    def test_continuation_after_menu_uses_each_choice_state(self):
        plans,rows=effects('''label start:
    menu:
        "Accept":
            $ flag = True
        "Refuse":
            $ flag = False
    if flag:
        $ trust += 2
    else:
        $ trust -= 1
    return
''')
        self.assertEqual([r['hints'][0]['text'] for r in rows],['신뢰도 +2','신뢰도 -1'])

    def test_attribute_and_dictionary_writes(self):
        plans,rows=effects('''label start:
    menu:
        "Go":
            $ hero.trust = 3
            $ stats["trust"] = 4
            if hero.trust + stats["trust"] == 7:
                $ love += 2
''')
        self.assertEqual(rows[0]['hints'][0]['text'],'호감도 +2')

    def test_stop_cycle_dynamic_and_loop(self):
        for command,reason in [('jump start','cycle'),('jump expression target','dynamic_target'),('while flag:\n                $ love += 2','next_unsupported_flow')]:
            script='label start:\n    menu:\n        "Go":\n            '+command+'\n            $ trust += 99\n'
            diagnostics={};plans=branches(parse({'f.rpy':script})[0],diagnostics)
            self.assertTrue(any(e['reason']==reason for e in diagnostics[plans[0][0]['id']]))
            self.assertFalse(any(e['kind']=='assignment' for e in plans[0][1]))

    def test_media_only_animation_video_and_reuse_without_gpu(self):
        script='''image dance:
    "images/frame1.png"
    0.1
    "images/frame2.png"
    0.1
    repeat
image film = Movie(play="movies/clip.webm")
label start:
    menu:
        "Watch":
            scene intro
            show dance
            show film
            return
        "Leave":
            return
'''
        scripts={'fixture.rpy':script};events=parse(scripts)[0]
        enrich(scripts,events,['images/intro.jpg','images/frame1.png','images/frame2.png','movies/clip.webm'])
        media=[e for e in events if e['kind']=='media']
        self.assertEqual(len(media),3)
        self.assertEqual(media[0]['assets'],['images/intro.jpg'])
        self.assertTrue(media[1]['animation']);self.assertTrue(media[2]['video'])
        with tempfile.TemporaryDirectory() as tmp,patch('scene_hints.runtime',side_effect=AssertionError('No GPU')):
            state={};first=update(Path(tmp),{},['routes'],events,{},state)
            self.assertEqual(first['model_calls'],0)
            self.assertIn('애니메이션 1개',state['routes'][0]['hints'][0]['text'])
            second=update(Path(tmp),{},['routes'],events,{},state)
            self.assertEqual(second['effects_analyzed'],0)
            diagnostic=json.loads((Path(tmp)/'data/scene-diagnostics.json').read_text(encoding='utf-8'))
            self.assertEqual(next(iter(diagnostic['choices'].values()))['reason'],'media_only')

    def test_navigation_chapter_loop_and_links(self):
        script='''define chapters = ["chapter1", "chapter2"]
screen chapters_screen():
    for chapt in chapters:
        if renpy.seen_label(chapt):
            textbutton chapt action Start(chapt)
label start:
    "Read {a=chapter1}first{/a} and {a=jump:chapter2}second{/a}."
    call screen chapters_screen
label chapter1:
    return
label chapter2:
    return
'''
        scripts={'f.rpy':script};events=parse(scripts)[0];data=navigation(scripts,events)
        self.assertEqual([e['target'] for e in data['screens']['chapters_screen']],['chapter1','chapter2'])
        self.assertTrue(all(e['resolved'] for e in data['links']))
        self.assertEqual(len(next(e for e in events if e['kind']=='screen_call')['destinations']),2)
        navigation_destinations(data,events)
        self.assertEqual(set(data['destinations']),{'chapter1','chapter2'})
        self.assertTrue(all(any(s['reason']=='return' for s in d['stops']) for d in data['destinations'].values()))

    def test_custom_link_handler_not_assumed_default(self):
        script='''define config.hyperlink_handlers = custom_handlers
label start:
    "{a=here}Link{/a}"
label here:
    return
'''
        scripts={'s.rpy':script};data=navigation(scripts,parse(scripts)[0])
        self.assertFalse(data['links'][0]['resolved'])

    def test_asset_index_reads_archive_names_not_frames(self):
        import pickle,zlib
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'images').mkdir();(root/'images/loose.png').write_bytes(b'not an image')
            payload=zlib.compress(pickle.dumps({'images/packed.jpg':[(123,900)]}))
            header=b'RPA-3.0 0000000000000022 00000000\n'
            (root/'archive.rpa').write_bytes(header.ljust(34,b' ')+payload)
            assets,warnings=asset_index(root)
            self.assertEqual(assets,['images/loose.png','images/packed.jpg']);self.assertFalse(warnings)

    def test_runtime_filters_with_pre_choice_values_and_preserves_unknown(self):
        source=Path(__file__).with_name('hints_conditions.py').read_text(encoding='utf-8')
        ns={'trust':2};exec(compile(source,'hints_conditions.py','exec'),ns)
        entry={'hints':[
            {'text':'조건부','base_text':'호감도 +2','guards':['trust + 1 >= 3']},
            {'text':'숨김','guards':['trust > 10']},
            {'text':'모르는 조건일 때','guards':['custom_check()']}]}
        result=ns['_rpt_hint_current'](entry)
        self.assertEqual([h['text'] for h in result['hints']],['호감도 +2','모르는 조건일 때'])
        self.assertEqual(ns['trust'],2)


if __name__=='__main__':unittest.main()
