"""Fixed Korean defaults: invented fixtures only, no game/model execution."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from engine import save_json
from name_hints import discover
from fixed_names import translations,apply
from display_text import compose


class FixedNameTests(unittest.TestCase):
    def metadata(self):
        script='''define name_hero = "Georgia"
define g = Character("[name_hero]", color="#fff")
define c = Character("Chris")
default player_name = "Alex"
define p = DynamicCharacter("player_name")
define alias = g
define config.name = "Game Title"
default nickname = "dude"
'''
        data=discover({'characters.rpy':script},[])
        data['fixed_names']=translations(data,{'Georgia':'조지아','Chris':'크리스','Alex':'알렉스','Game Title':'게임 제목'})
        data['reference_guard']=True
        return data

    def test_character_alias_and_dynamic_defaults_resolved_without_execution(self):
        data=self.metadata()
        for name in ('g','g.name','name_hero','alias'):self.assertEqual(data['fixed_names'][name],'조지아')
        self.assertEqual(data['fixed_names']['c'],'크리스')
        self.assertEqual(data['fixed_names']['p'],'알렉스')
        self.assertNotIn('config.name',data['fixed_names'])
        self.assertNotIn('nickname',data['fixed_names'])

    def test_korean_baked_default_and_particles_reference_remains_variable(self):
        row=dict(source='[g] and [c] arrived.',kind='dialogue')
        value=compose(row,{'text':'[g]와 [c]이 왔어요.'},self.metadata())
        ko,reference=value.split('\n',1)
        self.assertEqual(ko,'조지아와 크리스가 왔어요.')
        self.assertIn('[g] and [c]',reference)
        self.assertNotIn('rpt_display_name',value)
        # No runtime name lookup remains in Korean, so changing the player
        # value cannot change this output. Source and entry stay untouched.
        self.assertEqual(row['source'],'[g] and [c] arrived.')
        self.assertEqual(apply('[c]{/b}은 [nickname]와 왔어요.',self.metadata()['fixed_names']),'크리스{/b}는 [nickname]와 왔어요.')

    def test_name_only_unchanged_translation_and_escaped_brackets(self):
        value=compose({'source':'[g]','kind':'string'},{'text':'[g]'},self.metadata())
        self.assertEqual(value,'조지아')
        self.assertEqual(apply('[[g] [g!u] [unknown]',self.metadata()['fixed_names']),'[[g] 조지아 [unknown]')

    def test_unknown_real_player_name_falls_back_to_john_not_arbitrary_attributes(self):
        data=discover({'s.rpy':'$ player_name = renpy.input("Your name?")'},[{'source':'[gallery.name]'}])
        fixed=translations(data,{})
        self.assertEqual(fixed['player_name'],'존')
        self.assertNotIn('gallery.name',fixed)

    def test_multiline_keyword_name_nested_alias_and_cycle(self):
        data=discover({'s.rpy':'''default hero_name = "Mina"
define first = hero_name
define p = Character(
    name="[first]"
)
define loop1 = loop2
define loop2 = loop1
define x = DynamicCharacter("loop1")
'''},[])
        self.assertEqual(data['fixed_name_sources']['p'],'Mina')
        self.assertNotIn('x',data['fixed_name_sources'])

    def test_display_repair_migrates_metadata_and_never_loads_model(self):
        from translation import display_metadata,render,fingerprint
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);game=p/'staging/game';game.mkdir(parents=True)
            (game/'s.rpy').write_text('define name_hero = "Georgia"\ndefine g = Character("[name_hero]")\n',encoding='utf-8')
            row=dict(id='x',source='Hi [g].',kind='dialogue',file='s.rpy')
            save_json(p/'data/catalog.json',[row])
            save_json(p/'data/name-hints.json',{'version':4,'names':{}})
            save_json(p/'data/name-transliterations.json',{'names':{'Georgia':'조지아'}})
            cfg=dict(language='korean',model='fake',_fix='display')
            cache=p/'data/translations.jsonl';cache.write_text(json.dumps(dict(row,text='안녕 [g].',model='fake',fingerprint=fingerprint(cfg)))+'\n',encoding='utf-8')
            before=cache.read_bytes()
            with patch('hy_backend.runtime',side_effect=AssertionError('No inference')):
                data=display_metadata(p,cfg,[row])
                self.assertEqual(compose(row,{'text':'안녕 [g].'},data).split('\n')[0],'안녕 조지아.')
            with patch('automatic.script_sources',side_effect=AssertionError('No repeated extraction')):
                self.assertEqual(display_metadata(p,cfg,[row])['fixed_names'],{'g':'조지아','g.name':'조지아','name_hero':'조지아'})
            self.assertEqual(cache.read_bytes(),before)

    def test_multiple_game_structures_share_collection_and_rendering(self):
        from name_translation import collect
        cases={
            'variable':('default hero = "Mina"\ndefine a = Character("[hero]")','[hero]'),
            'keyword':('define a = Character(\n    name="Mina"\n)','[a]'),
            'priority':('define 10 a = Character(\n    "Mina"\n)','[a]'),
            'alias_order':('define outer = inner\ndefine inner = a\ndefine a = Character("Mina")','[outer]'),
            'dictionary':('default people = {"hero": "Mina"}\ndefine a = Character("[people[hero]]")','[people[hero]]'),
            'list':('default people = ["Mina"]\ndefine a = Character("[people[0]]")','[people[0]]'),
            'locals':('init python:\n    def helper():\n        hero = "Wrong"\nlabel start:\n    $ hero = "Mina"\ndefine a = Character("[hero]")','[hero]'),
            'wrapper':('init python:\n    def ask(prompt, initial):\n        return renpy.input(prompt, default=initial)\nlabel start:\n    $ hero = ask("Your name?", "Mina")\ndefine a = Character("[hero]")','[hero]'),
            'empty_default':('default player_name = ""\n$ player_name=renpy.input("Your name?", "Mina")\ndefine a=Character("[player_name]")','[a]'),
        }
        for label,(script,field) in cases.items():
            with self.subTest(label=label):
                scripts={'fixture.rpy':script};rows=[{'source':field}]
                metadata=discover(scripts,rows)
                self.assertIn('Mina',collect(scripts,rows,metadata))
                self.assertEqual(apply(field,translations(metadata,{'Mina':'미나'})),'미나')

    def test_alias_resolution_is_independent_of_file_order(self):
        from name_translation import collect
        scripts={'aliases.rpy':'define outer = inner\ndefine inner = a',
                 'people.rpy':'define a = Character("Mina")'}
        first=discover(scripts,[])
        second=discover(dict(reversed(list(scripts.items()))),[])
        self.assertEqual(first['fixed_name_sources'],second['fixed_name_sources'])
        self.assertIn('Mina',collect(scripts,[],first))

    def test_local_class_and_function_values_do_not_pollute_defaults(self):
        script='''init python:
    class Person:
        hero_name = "WrongClass"
    def helper():
        hero_name = "WrongLocal"
        hidden_name = renpy.input("Your name?", "Secret")
default hero_name = "Mina"
define a = Character("[hero_name]")
'''
        metadata=discover({'s.rpy':script},[])
        self.assertEqual(metadata['names']['hero_name']['representative'],'Mina')
        self.assertNotIn('hidden_name',metadata['names'])
        self.assertFalse(metadata['inputs'])
        self.assertEqual(set(metadata['fixed_name_sources'].values()),{'Mina'})

    def test_wrapper_defaults_keywords_and_chain(self):
        script='''init python:
    def first(prompt, initial="Mina"):
        return renpy.input(prompt, default=initial).strip()
    def second(question, initial="Mina"):
        return first(question, initial=initial)
label start:
    $ hero = second(question="Your name?")
define a = DynamicCharacter("hero")
'''
        metadata=discover({'s.rpy':script},[])
        self.assertEqual(metadata['fixed_name_sources']['a'],'Mina')
        self.assertEqual(metadata['fixed_name_sources']['hero'],'Mina')

    def test_addresses_relationships_and_answers_are_not_fixed_names(self):
        script='''init python:
    def ask(prompt, initial):
        return renpy.input(prompt, initial)
label start:
    $ nick = ask("What should I call you?", "dude")
    $ relation = ask("They are my...", "tenant")
    $ answer = ask("Password?", "Mina")
define n = Character("[nick]")
define r = Character("[relation]")
define a = Character("[answer]")
'''
        metadata=discover({'s.rpy':script},[])
        self.assertEqual(metadata['fixed_name_sources'],{})
        self.assertEqual(translations({'fixed_name_sources':{'x':'Pet'}},{'Pet':'펫'},
                                     {'Pet':{'kind':'address'}}),{})

    def test_missing_transliteration_still_fixes_original_default(self):
        metadata=discover({'s.rpy':'define a = Character("Mina")'},[])
        self.assertEqual(apply('[a]',translations(metadata,{})),'Mina')
        self.assertEqual(apply('[a]',translations(metadata,{'Mina':'미나'})),'미나')

    def test_indexed_names_preserve_reference_and_unrelated_values(self):
        metadata=discover({'s.rpy':'default people = {"hero": "Mina"}\ndefine a = Character("[people[hero]]")'},[])
        metadata['fixed_names']=translations(metadata,{'Mina':'미나'})
        metadata['reference_guard']=True
        row={'kind':'dialogue','source':'[people[hero]!t] has [score] points.'}
        text=compose(row,{'text':'[people[hero]!t]은 [score]점이다.'},metadata)
        korean,english=text.split('\n',1)
        self.assertEqual(korean,'미나는 [score]점이다.')
        self.assertIn('[people[hero]]',english)
        self.assertEqual(apply('[[people[hero]]',metadata['fixed_names']),'[[people[hero]]')

    def test_dynamic_calculations_and_recursive_wrappers_are_not_executed(self):
        script='''init python:
    def ask(prompt):
        return ask(prompt)
default hero = arbitrary_side_effect()
define a = Character("[hero]")
$ other = ask("Your name?")
'''
        self.assertEqual(discover({'s.rpy':script},[])['fixed_name_sources'],{})

    def test_screen_hidden_namespace_and_multiline_function_are_not_global(self):
        script='''screen test():
    default hero_name = "WrongScreen"
init python hide:
    hero_name = "WrongHidden"
init python in other:
    hero_name = "WrongStore"
init python:
    def helper(
        prompt,
    ):
        hero_name = "WrongLocal"
default hero_name = "Mina"
define a = Character("[hero_name]")
'''
        data=discover({'s.rpy':script},[])
        self.assertEqual(data['names']['hero_name']['representative'],'Mina')
        self.assertEqual(set(data['fixed_name_sources'].values()),{'Mina'})

    def test_multiline_wrapper_and_nested_function_scope(self):
        script='''init python:
    def ask(
        prompt,
    ):
        return renpy.input(prompt, "Mina")
    def outer(
        unused,
    ):
        def ask(prompt):
            return renpy.input(prompt, "Wrong")
label start:
    $ hero = ask("Your name?")
define a = Character("[hero]")
'''
        data=discover({'s.rpy':script},[])
        self.assertEqual(set(data['fixed_name_sources'].values()),{'Mina'})

    def test_new_indexed_name_repair_reuses_cache_and_second_run_skips_model(self):
        from contextlib import nullcontext
        from name_translation import run,mapping
        from translation import display_metadata,fingerprint
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);game=p/'staging/game';game.mkdir(parents=True)
            (game/'s.rpy').write_text('default people = {"hero": "Mina"}\ndefine a = Character("[people[hero]]")',encoding='utf-8')
            cfg=dict(language='korean',model='fake',endpoint='http://localhost',_names_only=True,_fix='names')
            row=dict(id='x',kind='dialogue',source='Hello [people[hero]].',file='s.rpy')
            save_json(p/'data/catalog.json',[row])
            raw=dict(row,text='안녕 [people[hero]].',model='fake',fingerprint=fingerprint(cfg))
            cache=p/'data/translations.jsonl';cache.write_text(json.dumps(raw)+'\n',encoding='utf-8')
            before=cache.read_bytes()
            responses=[{'message':{'content':'{"0":"name"}'}},{'message':{'content':'{"0":"미나"}'}}]
            with patch('hy_backend.runtime',return_value=nullcontext(dict(cfg,endpoint='http://localhost'))),patch('name_translation.request',side_effect=responses) as call:
                changed,report=run(p,cfg)
                self.assertEqual(call.call_count,2)
                self.assertEqual(changed,{'x'})
                self.assertEqual(mapping(p)['Mina'],'미나')
            metadata=display_metadata(p,cfg)
            self.assertEqual(compose(row,raw,metadata).split('\n')[0],'안녕 미나.')
            with patch('hy_backend.runtime',side_effect=AssertionError('Cached name must skip model')):
                _,report=run(p,cfg)
                self.assertEqual(report['model_calls'],0)
            self.assertEqual(cache.read_bytes(),before)


if __name__=='__main__':unittest.main()
