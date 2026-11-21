import unittest
from unittest.mock import patch
import rpgmaker_project
import translate_game
import translation_review
import failure_candidates
import rpgmaker_residue


class ManualOutputIntegration(unittest.TestCase):
    def test_rpg_gate_then_reload_and_unrelated_jobs_skip(self):
        prepared={'rows':[{'id':'1','source':'x'}], 'known':{'1':{'status':'source_fallback'}}, 'missing':[]}
        updated={'rows':prepared['rows'],'known':{'1':{'status':'manual_translation','text':''}},'missing':[]}
        order=[]
        with patch.object(translation_review,'run',side_effect=lambda *a,**k:order.append('review')), \
             patch.object(rpgmaker_project,'render_inputs',side_effect=lambda *a:order.append('reload') or updated):
            self.assertIs(rpgmaker_project.review_render_inputs(None,{},prepared,['run']),updated)
            self.assertEqual(order,['review','reload'])
            order.clear()
            self.assertIs(rpgmaker_project.review_render_inputs(None,{},prepared,['font']),prepared)
            self.assertEqual(order,[])
            self.assertIs(rpgmaker_project.review_render_inputs(None,{},dict(prepared,missing=['2']),['run'])['known'],prepared['known'])
            self.assertEqual(order,[])

    def test_renpy_rebuild_review_before_render_and_package(self):
        calls=[]
        with patch.object(translation_review,'run',side_effect=lambda *a,**k:calls.append('review')), \
             patch('diagnostics.stage'), patch.object(translate_game,'presentation'), \
             patch('display_policy.install'), \
             patch.object(translate_game,'render',side_effect=lambda *a:calls.append('render')), \
             patch.object(translate_game,'install_support'),patch.object(translate_game,'validate'), \
             patch.object(translate_game,'package',side_effect=lambda *a:calls.append('package')):
            translate_game.rebuild(None,{})
        self.assertEqual(calls,['review','render','package'])

    def test_rpg_manual_empty_is_written_to_json(self):
        import json, tempfile
        from pathlib import Path
        from gameloc_vendor import load
        load()
        from gameloc.model import Project, Entry, Location
        from gameloc.apply import apply
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'data').mkdir()
            path=root/'data/System.json';path.write_text(json.dumps({'gameTitle':'Hello'}),encoding='utf-8')
            location=Location(file='data/System.json',kind='loose',fmt='json',ptr='gameTitle')
            entry=Entry(id='x',text='Hello',locations=[location],translations={'ko':''})
            project=Project(game_root=directory,engine='rpgmaker',entries=[entry],stats={'extract_source':'en'})
            report=apply(project,'ko',game_root=root,reserved_names=set(),allow_empty_ids={'x'})
            self.assertFalse(report.failures)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['gameTitle'],'')

    def test_rpg_manual_identifier_bypasses_guards_only_for_manual_ids(self):
        import json, tempfile
        from pathlib import Path
        from gameloc_vendor import load
        load()
        from gameloc.model import Project, Entry, Location
        from gameloc.apply import apply
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'data').mkdir()
            path=root/'data/Synthetic.json'
            path.write_text(json.dumps({'manual_empty':'Enemy_001','manual_tag':'Enemy_001','automatic':'Enemy_001'}),encoding='utf-8')
            def entry(uid,key,value):
                return Entry(id=uid,text='Enemy_001',locations=[Location(file='data/Synthetic.json',kind='loose',fmt='json',ptr=key)],translations={'ko':value})
            project=Project(game_root=directory,engine='rpgmaker',entries=[entry('m1','manual_empty',''),entry('m2','manual_tag','<tag>\n한 줄'),entry('a','automatic','자동')],stats={'extract_source':'en'})
            report=apply(project,'ko',game_root=root,reserved_names={'Enemy_001'},allow_empty_ids={'m1','m2'},manual_ids={'m1','m2'})
            self.assertFalse(report.failures)
            output=json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(output,{'manual_empty':'','manual_tag':'<tag>\n한 줄','automatic':'Enemy_001'})
            self.assertTrue(report.risky)

    def test_repair_never_reexamines_manual(self):
        row={'id':'1','source':'hello'};entry={'source':'hello','status':'manual_translation','text':''}
        self.assertEqual(failure_candidates.classify(row,entry,{},{}),{'excluded':'manual_translation'})
        self.assertFalse(rpgmaker_residue.needs_repair(row,entry,{'engine':'rpgmaker','source_language':'japanese'},errors=['x']))


if __name__=='__main__':unittest.main()
