"""Short invented scripts only; no real projects, game engine or inference."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import warnings
from unittest.mock import patch

from story_guides import parse,answer_items,route_items,render_guide,read_scripts,run
from task_plan import arguments,plan

SCRIPT='''default affection = 0
default password = "orchid"
label start:
    $ answer = renpy.input("Password?").strip().lower()
    if answer != "orchid":
        jump wrong
    elif answer == password:
        jump right
    else:
        jump unknown
label right:
    menu:
        "Help her":
            $ affection += 2
            jump gate
        "Leave" if unlocked:
            $ affection -= 1
            jump finish
label gate:
    if affection >= 5:
        jump romance
    else:
        jump friend
label romance:
    jump finish
label friend:
    jump finish
label finish:
    return
'''


class GuideTests(unittest.TestCase):
    def test_unknown_escapes_in_input_conditions_and_choices_do_not_warn(self):
        script=r'''label quiz:
    $ answer = renpy.input("\Answer?\nNext")
    if answer == "\A":
        jump accepted
    menu:
        "\Accept":
            $ route = "\A"
'''
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            events,_=parse({'synthetic.rpy':script})
            answers=answer_items(events);routes=route_items(events)
        self.assertEqual(caught,[])
        self.assertEqual(answers[0]['prompt'],'\\Answer?\nNext')
        self.assertEqual(answers[0]['checks'][0]['candidates'][0]['value'],repr(r'\A'))
        self.assertEqual(routes[0]['title'],r'\Accept')
        self.assertEqual(routes[0]['direct'][0]['value'],r'\A')

    def test_statement_normalization_preserves_valid_escapes_and_syntax_errors(self):
        import ast
        from script_literals import parse_script,parse_expression
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            statement=parse_script(r'value = "\A\n\t\\A\uAC00"')
            self.assertEqual(ast.literal_eval(statement.body[0].value),'\\A\n\t\\A가')
            self.assertEqual(ast.literal_eval(parse_expression(r'r"\A\n"').body),r'\A\n')
            with self.assertRaises(SyntaxError):parse_script(r'value = "\xZZ"')
        self.assertEqual(caught,[])

    def test_answers_normalization_and_failure_comparison_are_not_claimed_success(self):
        events,_=parse({'s.rpy':SCRIPT});items=answer_items(events)
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]['prompt'],'Password?')
        self.assertIn('.strip().lower()',items[0]['expression'])
        self.assertIn("answer != 'orchid'",items[0]['checks'][0]['condition'].replace('"',"'"))
        self.assertTrue(any(v['value']=="'orchid'" for c in items[0]['checks'] for v in c['candidates']))
        self.assertIn('not (answer != "orchid")',items[0]['checks'][1]['condition'])
        self.assertIn('정답 확정이 아닙니다',render_guide('answers',items,[]))

    def test_routes_ties_choices_to_effects_and_downstream_gates(self):
        events,_=parse({'s.rpy':SCRIPT});items=route_items(events)
        help_choice,leave=items
        self.assertEqual(help_choice['title'],'Help her')
        self.assertIn('affection += 2',[e['code'].removeprefix('$ ') for e in help_choice['direct']])
        self.assertTrue(any('affection >= 5' in c['condition'] for c in help_choice['dependent_conditions']))
        self.assertEqual({e['target'] for e in help_choice['downstream']},{'romance','friend','finish'})
        self.assertEqual(leave['guards'],['unlocked'])
        self.assertFalse(any(e.get('target')=='romance' for e in leave['downstream']))

    def test_alias_multiline_membership_comments_and_python_blocks(self):
        script='''label quiz:
    python:
        raw = renpy.input(
            "Name # prompt?"
        )
        answer = raw.strip().casefold() # normalize
    if answer in ("alice", "alicia"):
        jump accepted
'''
        events,_=parse({'quiz.rpy':script});items=answer_items(events)
        self.assertEqual(items[0]['prompt'],'Name # prompt?')
        self.assertTrue(items[0]['transforms'])
        self.assertEqual(items[0]['checks'][0]['candidates'][0]['value'],"('alice', 'alicia')")

    def test_dynamic_inputs_and_local_labels_cycles(self):
        text='''label scene:
    $ code = renpy.input(prompt_text)
    if code == get_answer():
        jump expression target
    menu:
        "Again":
            jump .retry
label .retry:
    jump scene.retry
'''
        events,_=parse({'s.rpy':text})
        self.assertEqual(answer_items(events)[0]['checks'][0]['candidates'],[])
        choice=route_items(events)[0]
        self.assertEqual(choice['direct'][0]['target'],'scene.retry')
        self.assertEqual(len(choice['downstream']),1)
        self.assertFalse(choice['truncated'])

    def test_relationship_attributes_and_dictionary_flags(self):
        script='''label choice:
    menu:
        "Help":
            $ alice.affection += 1
            $ flags["helped"] = True
    if alice.affection >= 3 and flags["helped"]:
        jump alice_route
'''
        events,_=parse({'s.rpy':script});choice=route_items(events)[0]
        self.assertEqual(len(choice['dependent_conditions']),1)
        self.assertEqual(choice['dependent_conditions'][0]['branches'][0]['target'],'alice_route')

    def test_never_evaluates_game_python(self):
        with tempfile.TemporaryDirectory() as temp:
            marker=Path(temp)/'BAD'
            events,_=parse({'s.rpy':f'label a:\n    $ value = __import__("pathlib").Path({str(marker)!r}).touch()\n'})
            self.assertFalse(marker.exists());self.assertEqual(len(events),2)

    def test_source_scripts_only_output_and_no_engine_or_model(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);source=p/'Source';(source/'game').mkdir(parents=True)
            (source/'game/story.rpy').write_text(SCRIPT,encoding='utf-8')
            (source/'game/tl/korean').mkdir(parents=True)
            (source/'game/tl/korean/ignore.rpy').write_text('label WRONG:',encoding='utf-8')
            project=p/'Workspace';(project/'data').mkdir(parents=True)
            cfg={'source':str(source),'language':'korean'}
            with (patch('engine.engine_command',side_effect=AssertionError('No engine')),
                 patch('story_guides.owned_run',side_effect=AssertionError('No decompiler for loose scripts')),
                 patch('model_runtime.model_session',side_effect=AssertionError('No model'))):
                scripts=read_scripts(project,cfg);self.assertEqual(list(scripts),['story.rpy'])
                run(project,cfg,['answers','routes'])
            report=json.loads((project/'output/game/tl/korean/rpt_hints/hints.json').read_text(encoding='utf-8'))
            self.assertEqual(len(report['answers']),1)
            self.assertEqual(report['answers'][0]['values'],['orchid'])
            self.assertEqual((source/'game/story.rpy').read_text(encoding='utf-8'),SCRIPT)
            self.assertFalse((project/'data/translations.jsonl').exists())
            self.assertFalse(list((project/'data').glob('guide-*')))
            self.assertEqual(read_scripts(project,cfg),scripts)

    def test_existing_project_never_reads_original_source(self):
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp);base=project/'data/recovered-scripts';base.mkdir(parents=True)
            (base/'s.rpy').write_text(SCRIPT,encoding='utf-8')
            scripts=read_scripts(project,{'source':'DO-NOT-READ'})
            self.assertEqual(scripts,{'s.rpy':SCRIPT})

    def test_explicit_selection_and_deduplication(self):
        with self.assertRaises(ValueError):plan([])
        self.assertEqual(plan(['run','font','names','failed','display','layout','answers']),['run','answers'])
        self.assertEqual(plan(['font','display','layout','names','failed']),['failed','names','font'])
        self.assertEqual(plan(['answers']),['answers'])
        self.assertIn('--source',arguments(['answers','routes'],'Game',source=True))

    def test_missing_selection_cli_help_does_not_resolve_project(self):
        from translate_game import main
        with patch('translate_game.resolve_project',side_effect=AssertionError('No project')):
            with contextlib.redirect_stdout(io.StringIO()) as out:main(['tasks','--project','DO-NOT-READ'])
            self.assertIn('--tasks',out.getvalue())

    def test_cli_guide_tasks_do_not_translate_or_prepare_engine(self):
        from engine import save_json
        from translate_game import main
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp)/'project';scripts=project/'data/recovered-scripts';scripts.mkdir(parents=True)
            (scripts/'s.rpy').write_text(SCRIPT,encoding='utf-8')
            save_json(project/'project.json',{'source':'DO-NOT-READ','language':'korean'})
            with (patch('translate_game.prepare',side_effect=AssertionError('No engine')),
                  patch('translate_game.translate',side_effect=AssertionError('No translation')),
                  patch('translate_game.model_session',side_effect=AssertionError('No model'))):
                main(['tasks','--project',str(project),'--tasks','answers','routes','--output',str(Path(temp)/'result')])
            self.assertEqual(len(list((Path(temp)/'result').rglob('zz_rpt_hints.rpy'))),1)
            self.assertFalse(list((Path(temp)/'result').rglob('guides')))

    def test_combined_repairs_compile_and_package_once(self):
        from translate_game import repair
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);(p/'data').mkdir();(p/'data/translations.jsonl').write_text('',encoding='utf-8')
            with (patch('failed_repair.run',return_value=({'a'},{'model_calls':0})),
                  patch('name_translation.run',return_value=({'b'},{'model_calls':0})),
                  patch('name_translation.install_support'),patch('replacements.effective_cache',return_value={}),
                  patch('translate_game.read_catalog',return_value=[]),patch('failed_repair.refresh_choices'),
                  patch('translate_game.render') as render,patch('display_policy.install'),
                  patch('layout_policy.install') as layout,patch('engine.engine_command') as compile,
                  patch('translate_game.install_support',side_effect=AssertionError('No fonts selected')),
                  patch('translate_game.package') as package):
                repair(p,{'language':'korean','model':'fake'},['failed','names','layout'])
                self.assertEqual(render.call_args.args[1]['_render_ids'],{'a','b'})
                render.assert_called_once();compile.assert_called_once();package.assert_called_once();layout.assert_called_once()

    def test_default_translation_includes_name_guidance_recovery_and_correction(self):
        from engine import save_json
        from translate_game import main
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);(p/'data').mkdir();cfg={'source':'DO-NOT-READ','language':'korean','model':'fake'}
            (p/'staging/game').mkdir(parents=True);(p/'data/templates').mkdir()
            save_json(p/'project.json',cfg)
            for name in ('catalog.json','catalog-format.json','static-screen-literals.json'):save_json(p/'data'/name,{})
            order=[]
            with (patch('translate_game.resolve_project',return_value=(p,cfg)),patch('translate_game.verify_source'),
                  patch('translate_game.settings',return_value=cfg),patch('translate_game.model_session',return_value=contextlib.nullcontext()),
                  patch('name_translation.run',side_effect=lambda p,c:order.append('name-map' if c.get('_names_only') else 'name-fix')),
                  patch('failed_repair.run',side_effect=lambda *a:order.append('failed')),
                  patch('translate_game.translate',side_effect=lambda *a:order.append('translate')),
                  patch('translate_game.rebuild',side_effect=lambda *a:order.append('rebuild'))):
                main(['run','--project',str(p)])
            self.assertEqual(order,['name-map','translate','failed','name-fix','rebuild'])

    def test_gui_advanced_requires_fresh_selection_and_never_starts_on_toggle(self):
        import tkinter as tk
        from gui import App
        window=tk.Tk();window.withdraw()
        try:
            app=App(window)
            self.assertEqual(app.selected(),['run'])
            self.assertEqual(app.start.cget('text'),'번역 시작')
            app.advanced_on.set(True);app.toggle()
            self.assertEqual(app.selected(),[])
            self.assertEqual(str(app.start.cget('state')),'disabled')
            app.tasks['answers'].set(True);app.update_summary()
            self.assertEqual(app.selected(),['answers'])
            self.assertEqual(str(app.start.cget('state')),'normal')
            app.advanced_on.set(False);app.toggle()
            self.assertEqual(app.selected(),['run'])
            app.advanced_on.set(True);app.toggle()
            self.assertEqual(app.selected(),[])
            self.assertIsNone(app.proc)
        finally:window.destroy()


if __name__=='__main__':unittest.main()
