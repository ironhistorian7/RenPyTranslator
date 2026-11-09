import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from engine import save_json
from replacements import replace_visible, load_rules, effective_cache
from automatic import analyze, settings, merge_analysis, review, outline, add_literal_templates, DEFAULT_STYLE
from translation import fingerprint, catalog, read_catalog

def row(uid,text,kind='dialogue',file='story.rpy'):
    return dict(id=uid,source=text,kind=kind,file=file,block='scene_'+uid,speaker='a')

def fixture(root,rows,translations):
    save_json(root/'data/catalog.json',rows)
    cfg={'model':'fake','endpoint':'http://test.invalid','language':'korean','style':DEFAULT_STYLE,'glossary':{},'num_ctx':4096}
    (root/'data/translations.jsonl').write_text(''.join(json.dumps(dict(id=r['id'],source=r['source'],text=t,model='fake',fingerprint=fingerprint(cfg)),ensure_ascii=False)+'\n' for r,t in zip(rows,translations)),encoding='utf-8')
    return cfg

class OfflineTests(unittest.TestCase):
    def test_quoted_speaker_names_are_automatically_collected_once(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);d=p/'staging/game/tl/korean';d.mkdir(parents=True)
            (d/'scene.rpy').write_text('translate korean scene_a:\n    # "Cashier" "Hello."\n    "Cashier" ""\n',encoding='utf-8')
            cfg={'language':'korean'};catalog(p,cfg)
            add_literal_templates(p,cfg)
            add_literal_templates(p,cfg)
            self.assertEqual([r['source'] for r in read_catalog(p) if r['kind']=='string'],['Cashier'])
            with (p/'data/templates/scene.rpy').open('a',encoding='utf-8') as f:
                f.write('\ntranslate korean scene_b:\n    # "Guard" "Stop."\n    "Guard" ""\n')
            catalog(p,cfg);add_literal_templates(p,cfg)
            self.assertEqual({r['source'] for r in read_catalog(p) if r['kind']=='string'},{'Cashier','Guard'})
    def test_outline_does_not_mistake_color_for_character_name(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);d=p/'staging/game';d.mkdir(parents=True)
            (d/'script.rpy').write_text('define n = Character(None, color="#fff")\ndefine a = Character("Aster")\nlabel start:\n    jump ending\n',encoding='utf-8')
            result=outline(p)
            self.assertEqual(result['characters'],{'n':'(dynamic or unnamed)','a':'Aster'})
            self.assertEqual(result['labels']['start']['jumps'],['ending'])
    def test_only_visible_text_changes(self):
        before='해면동물 {a=해면동물}해면동물{/a} [해면동물] %(해면동물)s images/해면동물.png https://x/해면동물 해면동물.ogg'
        after,counts=replace_visible(before,{'해면동물':'해면'})
        self.assertEqual(counts,{'해면동물':2})
        self.assertEqual(after,'해면 {a=해면동물}해면{/a} [해면동물] %(해면동물)s images/해면동물.png https://x/해면동물 해면동물.ogg')
    def test_longest_match_once_no_cascade(self):
        after,counts=replace_visible('해면동물과 해면',{'해면동물':'해면','해면':'스펀지'})
        self.assertEqual(after,'해면과 스펀지')
        self.assertEqual(sum(counts.values()),2)
    def test_bom_and_reject_markup_rules(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'rules.json';p.write_text('{"replacements":{"해면":"스펀지"}}',encoding='utf-8-sig')
            self.assertEqual(load_rules(p),{'해면':'스펀지'})
            save_json(p,{'replacements':{'해면':'[variable]'}})
            with self.assertRaises(ValueError):load_rules(p)
    def test_rebuild_rules_are_reversible_and_never_call_model(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);cfg=fixture(p,[row('1','A sponge')],['해면동물'])
            original=(p/'data/translations.jsonl').read_bytes()
            save_json(p/'replacements.json',{'replacements':{'해면동물':'해면'}})
            with patch('translation.request',side_effect=AssertionError('network called')):
                self.assertEqual(effective_cache(p,cfg,True)['1']['text'],'해면')
                save_json(p/'replacements.json',{'replacements':{}})
                self.assertEqual(effective_cache(p,cfg)['1']['text'],'해면동물')
            self.assertEqual(original,(p/'data/translations.jsonl').read_bytes())
    def test_two_games_build_distinct_context_and_terms_without_config(self):
        for noun,target in [('Captain Nova','노바 선장'),('Professor Iris','아이리스 교수')]:
            with self.subTest(noun=noun),tempfile.TemporaryDirectory() as temp:
                p=Path(temp);rows=[row('a',noun+' returns.'),row('b','Welcome back.',file='next.rpy')]
                cfg=fixture(p,rows,['귀환한다.','돌아왔군.'])
                def fake(batch,config,structure):
                    term=[{'source':noun,'target':target,'confidence':'high'}] if noun in batch[0]['source'] else []
                    return {'summary':batch[0]['source'],'register':'해라체','characters':[], 'terms':term,'uncertainties':[]}
                with patch('automatic.analyze_chunk',side_effect=fake) as api:
                    profile=analyze(p,cfg)
                    self.assertEqual(profile['entries_analyzed'],2)
                    self.assertEqual(profile['glossary'],{noun:target})
                    self.assertEqual(api.call_count,2)
                    analyze(p,cfg)
                    self.assertEqual(api.call_count,2,'cached analysis must not call model')
                self.assertEqual(settings(p,cfg,True)['glossary'],{noun:target})
    def test_sample_cannot_replace_full_analysis(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);cfg=fixture(p,[row('1','Hello.')],['안녕.'])
            save_json(p/'data/analysis.json',{'sentinel':True})
            response={'summary':'인사','register':'대화','characters':[],'terms':[],'uncertainties':[]}
            with patch('automatic.analyze_chunk',return_value=response):analyze(p,cfg,sample=1)
            self.assertEqual(json.loads((p/'data/analysis.json').read_text()),{'sentinel':True})
    def test_review_retries_only_flagged_entry(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);cfg=fixture(p,[row('1','Hello.'),row('2','Captain Nova returns.')],['안녕.','노바가 돌아온다.'])
            cfg['imported_cache_fingerprint']=fingerprint(cfg);cfg['glossary']={'Captain Nova':'노바 선장'}
            repaired={'id':'2','source':'Captain Nova returns.','text':'노바 선장이 돌아온다.','model':'fake','fingerprint':fingerprint(cfg)}
            with patch('translation.translate_batch',return_value=([repaired],{})) as api:
                review(p,cfg,repair=True)
                self.assertEqual(api.call_count,1)
                self.assertEqual(api.call_args.args[0][0]['id'],'2')
    def test_glossary_conflict_is_provisional_and_deterministic(self):
        rows=[row('1','Nova'),row('2','Nova')]
        def result(target):return {'summary':'','register':'','characters':[],'uncertainties':[], 'terms':[{'source':'Nova','target':target,'confidence':'low'}]}
        p=merge_analysis(rows,[([rows[0]],result('노바')),([rows[1]],result('노바르'))],'fake')
        self.assertEqual(len(p['terminology_review']),1)
        self.assertEqual(p['terminology_review'][0]['status'],'provisional')

if __name__=='__main__':unittest.main()
