import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import name_review
from test_rpgmaker_names import actor, speaker

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.p = Path(self.tmp.name); (self.p/'data').mkdir()
        self.cfg = {'engine':'rpgmaker'}
        self.rows = [actor('a','藤堂（覆面）'), actor('b','Mira',2)]
        self.known = {'a':{'text':'도도（복면）'},'b':{'text':'미라'}}
        for patcher in (patch('translation.read_catalog',side_effect=lambda p:copy.deepcopy(self.rows)),patch('translation.cache',side_effect=lambda p,c:copy.deepcopy(self.known))):
            patcher.start(); self.addCleanup(patcher.stop)
    def test_unknown_reading_and_changed_candidate(self):
        data = name_review.build_review(self.p,self.cfg); e=data['entries'][0]
        self.assertEqual(e['value'],'藤堂（覆面）'); self.assertEqual(e['proposed'],'도도（복면）'); self.assertTrue(e['needs_review'])
        name_review.apply_review(self.p,self.cfg,data); self.assertTrue(name_review.build_review(self.p,self.cfg)['confirmed'])
        self.known['a']['text']='다른 후보'; self.assertFalse(name_review.build_review(self.p,self.cfg)['confirmed'])
    def test_tampered_duplicate_and_stale(self):
        data = name_review.build_review(self.p,self.cfg); bad=copy.deepcopy(data); bad['entries'][1]=bad['entries'][0]
        with self.assertRaises(ValueError): name_review.apply_review(self.p,self.cfg,bad)
        bad=copy.deepcopy(data); bad['entries'][0]['source']='X'
        with self.assertRaises(ValueError): name_review.apply_review(self.p,self.cfg,bad)
        self.rows[0]['source']='X'
        with self.assertRaises(ValueError): name_review.apply_review(self.p,self.cfg,data)
    def test_numeric_and_identity(self):
        self.rows=[actor('a','007-!'),actor('b','007-!',2)]; self.known={}
        data=name_review.build_review(self.p,self.cfg); data['entries'][1]['value']='007?'
        name_review.apply_review(self.p,self.cfg,data)
        effective,report=name_review.overlay(self.p,self.rows+[speaker('s','007-!')],self.known)
        self.assertEqual(effective['a']['text'],'007-!'); self.assertEqual(effective['b']['text'],'007?')
        self.assertNotIn('s',effective); self.assertIn('007-!',report['ambiguous_sources']); self.assertEqual(self.known,{})
    def test_shared_actor_spelling_preserves_identity_and_conflicts(self):
        self.rows=[actor('a','Mira'),actor('b','Mira',2)]
        self.known={}
        data=name_review.build_review(self.p,self.cfg)
        for entry in data['entries']: entry['value']='미라'
        name_review.apply_review(self.p,self.cfg,data)
        rows=self.rows+[speaker('s','Mira')]
        terms=name_review.rpg_terms(self.p,rows)
        self.assertEqual(terms['terms']['Mira'],'미라')
        self.assertIn(('Mira','미라'),terms['groups'])
        effective,report=name_review.overlay(self.p,rows,{})
        self.assertEqual({k:effective[k]['text'] for k in ('a','b','s')},
                         {'a':'미라','b':'미라','s':'미라'})
        self.assertEqual(report['updated_ids'],['s'])
        data['entries'][1]['value']='미라나'
        name_review.apply_review(self.p,self.cfg,data)
        terms=name_review.rpg_terms(self.p,rows)
        self.assertNotIn('Mira',terms['terms'])
        self.assertIn('Mira',terms['ambiguous_sources'])
        effective,_=name_review.overlay(self.p,rows,{})
        self.assertEqual(effective['a']['text'],'미라')
        self.assertEqual(effective['b']['text'],'미라나')
        self.assertNotIn('s',effective)
    def test_qualified_base_alias(self):
        data=name_review.build_review(self.p,self.cfg); data['entries'][0]['value']='도도（복면）'
        name_review.apply_review(self.p,self.cfg,data)
        effective,_=name_review.overlay(self.p,self.rows+[speaker('s','藤堂')],{})
        self.assertEqual(effective['s']['text'],'도도')
    def test_structured_reading_and_conflicting_readings(self):
        self.rows=[actor('a','春',source_aliases=['春','ハル']),actor('b','光',2,source_aliases=['光','ヒカリ','コウ'])]
        self.known={'a':{'text':'하루'},'b':{'text':'히카리'}}
        entries=name_review.build_review(self.p,self.cfg)['entries']
        self.assertEqual(entries[0]['reading'],'ハル'); self.assertFalse(entries[0]['needs_review'])
        self.assertEqual(entries[1]['value'],'光'); self.assertTrue(entries[1]['needs_review'])
    def test_untranslated_letters_and_cache_skip(self):
        self.rows=[actor('a','One'),actor('b','Two',2),actor('c','Three',3)]
        self.known={'a':{'text':'FBDJ'},'b':{'text':'미나2'},'c':{'text':'カナ'}}
        data=name_review.build_review(self.p,self.cfg)
        self.assertTrue(data['entries'][0]['needs_review']); self.assertFalse(data['entries'][1]['needs_review'])
        self.assertTrue(data['entries'][2]['needs_review'])
        with patch('translation.cache',return_value=self.known) as cache:
            name_review.build_review(self.p,self.cfg)
            self.assertTrue(cache.call_args.args[1]['_skip_name_review'])
    def test_configuration_base_agreement_and_anchors(self):
        self.rows=[actor('a','藤堂（覆面）'),actor('b','藤堂（私服）',2)]
        self.known={}
        data=name_review.build_review(self.p,self.cfg)
        data['entries'][0]['value']='도도（복면）'; data['entries'][1]['value']='도도（사복）'
        self.assertTrue(name_review.apply_review(self.p,self.cfg,data)['confirmed'])
        cfg=name_review.configuration(self.p,{},self.rows)
        self.assertEqual(cfg['_person_names']['藤堂'],'도도')
        self.assertIn(('藤堂','도도'),cfg['_rpg_name_groups'])
        self.rows[0]['source']='바뀜'
        self.assertNotIn('藤堂（覆面）',name_review.configuration(self.p,{},self.rows)['_person_names'])
    def test_speaker_grouping_actor_association_and_ambiguous_actor(self):
        self.rows=[actor('a','Mira'),speaker('s1','Mira'),speaker('s2','Mira'),
                   speaker('x2','Ghost'),speaker('x1','Ghost')]
        self.known={}
        data=name_review.build_review(self.p,self.cfg)
        self.assertEqual([e['key'] for e in data['entries']],['rpg:a','rpg:x1'])
        data['entries'][0]['value']='미라'; data['entries'][1]['value']='고스트'
        name_review.apply_review(self.p,self.cfg,data)
        effective,_=name_review.overlay(self.p,self.rows,{})
        self.assertEqual([effective[k]['text'] for k in ('s1','s2','x1','x2')],['미라','미라','고스트','고스트'])
        altered=copy.deepcopy(self.rows); altered[-1]['source']='Changed'
        effective,_=name_review.overlay(self.p,altered,{})
        self.assertNotIn('x1',effective)
        self.rows += [actor('b','Mira',2)]
        data=name_review.build_review(self.p,self.cfg)
        self.assertIn('rpg:s1',[e['key'] for e in data['entries']]); self.assertIn('rpg:s2',[e['key'] for e in data['entries']])
    def test_proposal_precedes_cached_name(self):
        self.rows=[actor('a','Mira')]; self.known={'a':{'text':'이전'}}; self.cfg['model']='local'
        (self.p/'data/name-proposals.json').write_text(json.dumps({'a':{'source':'Mira','model':'local','text':'제안'}}),encoding='utf8')
        self.assertEqual(name_review.build_review(self.p,self.cfg)['entries'][0]['proposed'],'제안')
    def test_manual_override_change_invalidates_saved_approval(self):
        self.rows=[actor('a','Mira')]; self.known={'a':{'text':'자동'}}
        path=self.p/'names.json'
        path.write_text(json.dumps({'overrides':{'Mira':'기존'}}),encoding='utf8')
        data=name_review.build_review(self.p,self.cfg)
        self.assertEqual(data['entries'][0]['proposed'],'기존')
        data['entries'][0]['value']='승인'; name_review.apply_review(self.p,self.cfg,data)
        path.write_text(json.dumps({'overrides':{'Mira':'새이름'}}),encoding='utf8')
        self.assertFalse(name_review.build_review(self.p,self.cfg)['confirmed'])
        self.assertEqual(name_review.build_review(self.p,self.cfg)['entries'][0]['proposed'],'새이름')
        self.assertNotIn('a',name_review.overlay(self.p,self.rows,{})[0])
    def test_renpy_stale_override_and_fixed_source_are_excluded(self):
        self.cfg={'engine':'renpy'}
        hint=self.p/'data/name-hints.json'; path=self.p/'names.json'
        hint.write_text(json.dumps({'fixed_name_sources':{'hero':'Mina'}}),encoding='utf8')
        path.write_text(json.dumps({'overrides':{'Mina':'미나'}}),encoding='utf8')
        data=name_review.build_review(self.p,self.cfg); name_review.apply_review(self.p,self.cfg,data)
        self.assertEqual(name_review.confirmed_map(self.p),{'Mina':'미나'})
        path.write_text(json.dumps({'overrides':{'Mina':'새표기'}}),encoding='utf8')
        self.assertEqual(name_review.confirmed_map(self.p),{})
        path.write_text(json.dumps({'overrides':{'Mina':'미나'}}),encoding='utf8')
        hint.write_text(json.dumps({'fixed_name_sources':{'hero':'Other'}}),encoding='utf8')
        self.assertEqual(name_review.confirmed_map(self.p),{})
    def test_renpy_excludes_address(self):
        self.cfg={'engine':'renpy'}
        for f,obj in [('name-hints.json',{'fixed_name_sources':{'hero':'Mina','pet':'Dad'},'names':{'pet':{'kind':'address'}}}),('identity-types.json',{'Dad':{'kind':'address'},'Mina':{'kind':'name'},'Rin':{'kind':'name'}}),('name-transliterations.json',{'names':{'Mina':'미나','Rin':'린','Dad':'아빠'}})]:
            (self.p/'data'/f).write_text(json.dumps(obj),encoding='utf-8')
        data=name_review.build_review(self.p,self.cfg); self.assertEqual({e['source'] for e in data['entries']},{'Mina','Rin'})
        name_review.apply_review(self.p,self.cfg,data); self.assertEqual(name_review.confirmed_map(self.p),{'Mina':'미나','Rin':'린'})
if __name__=='__main__': unittest.main()
