"""Synthetic tests only: no installed game or inference server is accessed."""
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from engine import save_json
from name_translation import collect,run,apply_names,candidate_spans,replace_selected,edit_digest
from translation import fingerprint

class NameRepairTests(unittest.TestCase):
    def test_extract_persons_not_roles_and_keep_variables(self):
        found=collect({'story.rpy':'''define i = Character("India")
define dad = Character("Dad")
default player_name = "Alex"
define p = Character("[player_name]")
'''},[{'source':'[player_name] met India.'}])
        self.assertEqual(set(found),{'India','Alex'})

    def test_person_country_selection_only_changes_selected_occurrence(self):
        text='인도는 인도를 방문했다.'
        spans=candidate_spans('India, my friend, visited India.',text,{'India':'인디아'},{'India':['인도']})
        self.assertEqual(len(spans),2)
        self.assertEqual(replace_selected(text,spans,[0]),'인디아는 인도를 방문했다.')
        self.assertEqual(replace_selected(text,spans,[]),text)
        self.assertEqual(candidate_spans('[India] visited a country.','[India]는 인도를 방문했다.',{'India':'인디아'},{'India':['인도']}),[])

    def test_cached_names_and_decisions_skip_model_and_keep_raw_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);(p/'staging/game').mkdir(parents=True)
            (p/'staging/game/script.rpy').write_text('define i = Character("India")\n',encoding='utf-8')
            cfg={'model':'rpt-hymt2-7b:q6_k','language':'korean'}
            rows=[{'id':'name','kind':'string','source':'India','file':'story.rpy'},
                  {'id':'line','kind':'dialogue','source':'India, my friend, visited India.','file':'story.rpy'}]
            save_json(p/'data/catalog.json',rows)
            raw=[dict(r,text=t,model=cfg['model'],fingerprint=fingerprint(cfg)) for r,t in zip(rows,['인도','인도는 인도를 방문했다.'])]
            cachefile=p/'data/translations.jsonl';cachefile.write_text(''.join(json.dumps(r)+'\n' for r in raw),encoding='utf-8')
            before=cachefile.read_bytes()
            responses=[{'message':{'content':'{"0":"name"}'}},{'message':{'content':'{"0":"인디아"}'}},{'message':{'content':'{"0":[0]}'}}]
            with patch('hy_backend.runtime',return_value=nullcontext(dict(cfg,endpoint='http://localhost'))),patch('name_translation.request',side_effect=responses) as call:
                ids,report=run(p,cfg)
                self.assertEqual(call.call_count,3)
                self.assertEqual(ids,{'name','line'})
                self.assertIn('고유 이름',call.call_args_list[1].args[2]['messages'][0]['content'])
            with patch('hy_backend.runtime',side_effect=AssertionError('Must not start model')):
                ids,report=run(p,cfg)
                self.assertEqual(report['model_calls'],0)
            updated=apply_names(p,rows,{r['id']:r for r in raw})
            self.assertEqual(updated['line']['text'],'인디아는 인도를 방문했다.')
            self.assertEqual(updated['name']['text'],'인디아')
            self.assertEqual(cachefile.read_bytes(),before)

    def test_unrelated_name_override_does_not_repeat_other_decisions(self):
        self.assertEqual(edit_digest({'India':'인디아'},'India smiled.'),
                         edit_digest({'India':'인디아','Alex':'알렉스'},'India smiled.'))

    def test_legacy_name_edits_cannot_promote_failed_original(self):
        source='I need Donny to help me today.'
        names={'Donny':'도니'}
        row={'id':'failed','source':source,'kind':'dialogue'}
        entry={'text':source,'status':'source_fallback'}
        for after in (source,source.replace('Donny','도니')):
            edit={'source':source,'before':source,'after':after,'digest':edit_digest(names,source),'decided':True}
            with patch('name_translation.mapping',return_value=names),patch('name_translation.read',return_value={'failed':edit}):
                result=apply_names(Path('DO-NOT-READ'),[row],{'failed':entry})
            self.assertIs(result['failed'],entry)

    def test_noop_correction_preserves_success_status(self):
        source='India came here today.'; names={'India':'인디아'}
        entry={'text':'인디아는 오늘 여기에 왔다.','status':'failure_recovered'}
        edit={'source':source,'before':entry['text'],'after':entry['text'],'digest':edit_digest(names,source)}
        with patch('name_translation.mapping',return_value=names),patch('name_translation.read',return_value={'ok':edit}):
            result=apply_names(Path('DO-NOT-READ'),[{'id':'ok','source':source,'kind':'dialogue'}],{'ok':entry})
        self.assertIs(result['ok'],entry)

    def test_names_only_repair_does_not_install_fonts(self):
        from translate_game import repair
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);(p/'data').mkdir();(p/'data/translations.jsonl').write_text('',encoding='utf-8')
            cfg={'language':'korean','model':'fake'}
            with patch('name_translation.run',return_value=(set(),{'model_calls':0})), \
                 patch('translate_game.render') as render,patch('translate_game.install_support',side_effect=AssertionError('Fonts must not be touched')), \
                 patch('name_translation.install_support'),patch('translate_game.read_catalog',return_value=[]), \
                 patch('replacements.effective_cache',return_value={}),patch('engine.engine_command'),patch('translate_game.package'):
                repair(p,cfg,'names')
                self.assertEqual(render.call_args.args[1]['_render_ids'],set())

    def test_healthy_font_install_skips_inventory_and_copy(self):
        from font_policy import install
        data={'preferred':{'regular':'neo.ttf'}}
        with patch('font_policy.installed',return_value=data),patch('font_policy.inventory',side_effect=AssertionError('No rescan')),patch('font_policy.shutil.copy2',side_effect=AssertionError('No copy')):
            self.assertEqual(install(Path('unused'),'korean'),data)

    def test_names_render_keeps_existing_menu_and_other_lines(self):
        from translation import catalog,render
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);target=p/'staging/game/tl/korean/story.rpy'
            target.parent.mkdir(parents=True)
            template='translate korean strings:\n    old "Replay"\n    new ""\n    old "India"\n    new ""\n'
            target.write_text(template,encoding='utf-8')
            cfg={'language':'korean','model':'fake','_fix':'names','_repair':True}
            rows=catalog(p,cfg);name=rows[1]
            existing=template.replace('new ""','new "다시보기"',1)
            existing=existing.replace('new ""','new "인도"',1)
            target.write_text(existing,encoding='utf-8')
            cfg['_render_ids']={name['id']}
            with patch('replacements.effective_cache',return_value={name['id']:dict(name,text='인디아')}),patch('name_hints.ensure_metadata',return_value={}):
                render(p,cfg)
            self.assertEqual(target.read_text(encoding='utf-8'),existing.replace('new "인도"','new "인디아"'))

    def test_font_check_accepts_ready_patch_and_rejects_missing_font(self):
        from font_policy import installed
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);game=p/'staging/game';game.mkdir(parents=True)
            (game/'zz_rpt_korean.rpy').write_text('def _rpt_font_group():\n    pass\ntranslate korean python:\n    _rpt_language_fonts()\n',encoding='utf-8')
            (game/'font.ttf').write_bytes(b'synthetic font handled by mock')
            data={'fonts':{},'coverage':{'regular':[[44032,55203]],'hand':[[44032,55203]]},'preferred':{'regular':'font.ttf','hand':'font.ttf'},'fallback':'font.ttf','bold':'font.ttf'}
            save_json(game/'tl/korean/_rpt_presentation.json',{'fonts':data})
            with patch('font_policy.TTFont') as font:
                font.return_value.__enter__.return_value.getBestCmap.return_value={ord(c):c for c in '가나다'}
                self.assertEqual(installed(p,'korean'),data)
                (game/'font.ttf').unlink()
                self.assertIsNone(installed(p,'korean'))

if __name__=='__main__':unittest.main()
