import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch
import name_review
from name_review_control import prepare_kirikiri, pending_kirikiri

class KiriNames(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.p=Path(self.tmp.name); (self.p/'data').mkdir()
        self.cfg={'engine':'kirikiri','model':'synthetic'}
        self.rows=[dict(id='a',source='Alice',usage='speaker_label',dialect='kagex_psb',location=['scenes',0,'texts',0,1]),
                   dict(id='b',source='Alice',usage='speaker',dialect='kag',location={'start':0,'end':5}),
                   dict(id='c',source='Internal',usage='speaker_label',dialect='kagex_psb',location=['scenes',0,'texts',0,0]),
                   dict(id='d',source='Story',usage='dialogue',dialect='kagex_psb',location=['scenes',0,'texts',0,2])]
        for patcher in (patch('translation.read_catalog',side_effect=lambda p:self.rows),patch('translation.cache',return_value={})):
            patcher.start(); self.addCleanup(patcher.stop)
    def test_group_confirm_and_anchored_only(self):
        review=name_review.build_review(self.p,self.cfg)
        self.assertEqual(len(review['entries']),1)
        self.assertEqual(review['entries'][0]['key'],'kirikiri:a')
        self.assertEqual(review['entries'][0]['members'],['a','b'])
        review['entries'][0]['value']='앨리스'
        name_review.apply_review(self.p,self.cfg,review)
        self.assertEqual(name_review.configuration(self.p,self.cfg,self.rows)['_person_names'], {'Alice':'앨리스'})
        output,_=name_review.overlay(self.p,self.rows,{})
        self.assertEqual({k: v['text'] for k,v in output.items()}, {'a':'앨리스','b':'앨리스'})
        self.rows[1]['source']='different'
        output,_=name_review.overlay(self.p,self.rows,{})
        self.assertNotIn('b',output)

    def test_generation_index_reuses_approved_spellings_and_reviewed_aliases(self):
        cfg=dict(self.cfg,source_language='japanese')
        self.rows[0]['source']='雪森'
        self.rows.extend([
            dict(id='ruby',source='[ゆきもり,1]雪森が来た。',kind='dialogue',usage='dialogue',dialect='kagex_psb'),
            dict(id='alias',source='朔じぃが呼んだ。',kind='dialogue',usage='dialogue',dialect='kagex_psb'),
        ])
        review=name_review.build_review(self.p,cfg)
        next(entry for entry in review['entries'] if entry['source']=='雪森')['value']='유키모리'
        name_review.apply_review(self.p,cfg,review)
        rules=[dict(source_literals=['朔じぃ','さくじぃ'],target_variants=['사쿠'],translation='사쿠지이'),
               dict(source_literals=['春原','はるはら'],target_variants=['하루'],translation='하루하라',exclude_source_literals=['別人'])]
        (self.p/'data/reviewed-terms.json').write_text(json.dumps({'rules':rules},ensure_ascii=False),encoding='utf8')
        actual=name_review.configuration(self.p,cfg,self.rows,prepare_names=True)['_kirikiri_name_index']
        self.assertEqual(actual.readings['雪森'],'ゆきもり')
        self.assertEqual(actual.aliases,{'雪森':'유키모리','朔じぃ':'사쿠지이','さくじぃ':'사쿠지이'})
        self.assertNotIn('_kirikiri_name_index',name_review.configuration(self.p,cfg,self.rows))
        self.assertNotIn('_kirikiri_name_index',name_review.configuration(self.p,self.cfg,self.rows,prepare_names=True))
    def test_growth_retains_approved_value_and_only_new_group_is_proposed(self):
        review=name_review.build_review(self.p,self.cfg)
        review['entries'][0]['value']='앨리스'
        name_review.apply_review(self.p,self.cfg,review)
        cache_path=self.p/'data/translations.jsonl'
        cache_path.write_text('unchanged cache',encoding='utf8')
        self.rows.extend([
            dict(id='0',source='Alice',usage='speaker_label',dialect='kagex_psb',location=['scenes',0,'texts',1,1]),
            dict(id='z',source='Bob',usage='speaker_label',dialect='kagex_psb',location=['scenes',0,'texts',2,1])])
        updated=name_review.build_review(self.p,self.cfg)
        self.assertFalse(updated['confirmed'])
        alice=next(e for e in updated['entries'] if e['source']=='Alice')
        self.assertEqual((alice['key'],alice['value'],alice['retained_decision']),('kirikiri:a','앨리스','앨리스'))
        self.assertFalse(alice['needs_review'])
        self.assertEqual(alice['members'],['0','a','b'])
        def infer(rows,context,cfg):
            self.assertEqual([r['id'] for r in rows],['z'])
            return ([dict(id='z',text='밥')],[])
        with patch('hy_backend.runtime',side_effect=lambda cfg:nullcontext(cfg)), patch('translation.translate_batch',side_effect=infer) as call:
            prepare_kirikiri(self.p,self.cfg)
            self.assertEqual(call.call_count,1)
        self.assertEqual(cache_path.read_text(encoding='utf8'),'unchanged cache')
        updated=name_review.build_review(self.p,self.cfg)
        name_review.apply_review(self.p,self.cfg,updated)
        self.assertTrue(name_review.build_review(self.p,self.cfg)['confirmed'])
        output,_=name_review.overlay(self.p,self.rows,{})
        self.assertEqual(output['0']['text'],'앨리스')

    def test_derived_slot_requires_existing_same_source_anchor(self):
        review=name_review.build_review(self.p,self.cfg)
        review['entries'][0]['value']='앨리스';name_review.apply_review(self.p,self.cfg,review)
        derived=dict(id='new',source='Alice',usage='speaker_label',dialect='kagex_psb',location=['scenes',0,'texts',1,1],derived_speaker=True)
        dialogue=dict(id='dialogue',source='Alice',usage='dialogue',dialect='kagex_psb',location=['scenes',0,'texts',2,2])
        rows=self.rows+[derived,dialogue]
        result,_=name_review.overlay(self.p,rows,{})
        self.assertEqual(result['new']['text'],'앨리스');self.assertNotIn('dialogue',result)
        for row in self.rows[:2]:row['source']='Changed'
        result,_=name_review.overlay(self.p,rows,{})
        self.assertNotIn('new',result)

    def test_retention_requires_matching_anchor_and_manual_override(self):
        review=name_review.build_review(self.p,self.cfg)
        review['entries'][0]['value']='앨리스'; name_review.apply_review(self.p,self.cfg,review)
        self.rows[0]['source']='Changed'; self.rows[1]['source']='Changed'
        self.rows.append(dict(id='0',source='Alice',usage='speaker',dialect='kag'))
        updated=name_review.build_review(self.p,self.cfg)
        alice=next(e for e in updated['entries'] if e['source']=='Alice')
        self.assertNotIn('retained_decision',alice)
        self.rows[0]['source']='Alice'
        (self.p/'names.json').write_text(json.dumps({'overrides':{'Alice':'새 이름'}},ensure_ascii=False),encoding='utf8')
        updated=name_review.build_review(self.p,self.cfg)
        alice=next(e for e in updated['entries'] if e['source']=='Alice')
        self.assertNotIn('retained_decision',alice)
        self.assertEqual(alice['value'],'새 이름')

    def test_reuses_other_member_proposal_without_inference(self):
        self.rows.insert(0,dict(id='0',source='Alice',usage='speaker',dialect='kag'))
        proposals={'a':dict(source='Alice',model='synthetic',text='앨리스')}
        (self.p/'data/name-proposals.json').write_text(json.dumps(proposals,ensure_ascii=False),encoding='utf8')
        with patch('translation.translate_batch') as call:
            prepare_kirikiri(self.p,self.cfg)
            call.assert_not_called()
        self.assertEqual(name_review.build_review(self.p,self.cfg)['entries'][0]['value'],'앨리스')

    def test_changed_representative_does_not_collide_and_bulk_decisions_survive(self):
        self.rows = [dict(id=f'id{i:02}',source=f'Name{i:02}',usage='speaker',dialect='kag') for i in range(37)]
        self.rows.append(dict(id='extra',source='Name00',usage='speaker',dialect='kag'))
        review=name_review.build_review(self.p,self.cfg)
        for e in review['entries']: e['value']='결정'+e['source']
        name_review.apply_review(self.p,self.cfg,review)
        # Name00 retains "extra" while its previous representative becomes a new source.
        self.rows[-1]['id']='zextra'
        self.rows[0]['id']='extra'
        self.rows[0]['source']='NewName'
        self.rows.append(dict(id='0',source='Name01',usage='speaker',dialect='kag'))
        updated=name_review.build_review(self.p,self.cfg)
        self.assertFalse(updated['confirmed'])
        self.assertEqual(len({e['key'] for e in updated['entries']}),len(updated['entries']))
        for e in updated['entries']:
            if e['source'] not in ('Name00','NewName'):
                self.assertEqual(e['value'],'결정'+e['source'])
                self.assertFalse(e['needs_review'])
        # Name00 has no remaining original ID; the old decision cannot transfer.
        self.assertNotIn('retained_decision',next(e for e in updated['entries'] if e['source']=='Name00'))
        self.assertNotIn('retained_decision',next(e for e in updated['entries'] if e['source']=='NewName'))

    def test_37_approved_names_only_new_name_is_sent_for_inference(self):
        self.rows=[dict(id=f'id{i:02}',source=f'Name{i:02}',usage='speaker',dialect='kag') for i in range(37)]
        review=name_review.build_review(self.p,self.cfg)
        for e in review['entries']: e['value']='결정'+e['source']
        name_review.apply_review(self.p,self.cfg,review)
        self.rows.extend(dict(id=f'0{i:02}',source=f'Name{i:02}',usage='speaker',dialect='kag') for i in range(37))
        self.rows.append(dict(id='new',source='NewName',usage='speaker',dialect='kag'))
        updated=name_review.build_review(self.p,self.cfg)
        self.assertFalse(updated['confirmed'])
        retained=[e for e in updated['entries'] if 'retained_decision' in e]
        self.assertEqual(len(retained),37)
        self.assertTrue(all(e['value']=='결정'+e['source'] and not e['needs_review'] for e in retained))
        def infer(rows,context,cfg):
            self.assertEqual([r['id'] for r in rows],['new'])
            return ([dict(id='new',text='새 이름')],[])
        with patch('hy_backend.runtime',side_effect=lambda cfg:nullcontext(cfg)), patch('translation.translate_batch',side_effect=infer) as call:
            prepare_kirikiri(self.p,self.cfg)
            self.assertEqual(call.call_count,1)
        updated=name_review.build_review(self.p,self.cfg)
        name_review.apply_review(self.p,self.cfg,updated)
        self.assertTrue(name_review.build_review(self.p,self.cfg)['confirmed'])
        output,_=name_review.overlay(self.p,self.rows,{})
        self.assertEqual(output['000']['text'],'결정Name00')

    def test_reassigned_representative_with_surviving_anchor_has_distinct_keys(self):
        review=name_review.build_review(self.p,self.cfg)
        review['entries'][0]['value']='앨리스'; name_review.apply_review(self.p,self.cfg,review)
        self.rows[0]['source']='NewName'
        updated=name_review.build_review(self.p,self.cfg)
        self.assertEqual(len({e['key'] for e in updated['entries']}),2)
        alice=next(e for e in updated['entries'] if e['source']=='Alice')
        self.assertEqual((alice['key'],alice['value']),('kirikiri:b','앨리스'))
        name_review.apply_review(self.p,self.cfg,updated)
        self.assertTrue(name_review.build_review(self.p,self.cfg)['confirmed'])

    def test_approved_only_growth_requires_no_model_runtime(self):
        review=name_review.build_review(self.p,self.cfg)
        review['entries'][0]['value']='앨리스'; name_review.apply_review(self.p,self.cfg,review)
        self.rows.append(dict(id='0',source='Alice',usage='speaker',dialect='kag'))
        current=name_review.build_review(self.p,self.cfg)
        self.assertFalse(current['confirmed'])
        self.assertEqual(pending_kirikiri(self.p,self.cfg,current),[])
        with patch('hy_backend.runtime') as runtime:
            prepare_kirikiri(self.p,self.cfg)
            runtime.assert_not_called()
        name_review.apply_review(self.p,self.cfg,current)
        self.assertEqual(pending_kirikiri(self.p,self.cfg),[])

    def test_reject_control_code_in_decision(self):
        review=name_review.build_review(self.p,self.cfg);review['entries'][0]['value']='bad%r'
        with self.assertRaises(ValueError):name_review.apply_review(self.p,self.cfg,review)
    def test_force_review_reopens_confirmed_checkpoint(self):
        from name_review_control import run
        review=name_review.build_review(self.p,self.cfg)
        review['entries'][0]['value']='앨리스'; name_review.apply_review(self.p,self.cfg,review)
        with patch('name_review_control._console_reply', side_effect=lambda data:data) as reply:
            run(self.p,self.cfg,force=True)
            reply.assert_called_once()

    def test_propose_once_without_cache_mutation(self):
        def infer(rows,context,cfg):
            self.assertTrue(cfg['_name_preparation']); self.assertEqual(cfg['_kirikiri_dialect'],'kagex_psb')
            return ([dict(id=r['id'],text='앨리스') for r in rows],[])
        with patch('hy_backend.runtime',side_effect=lambda cfg:nullcontext(cfg)), patch('translation.translate_batch',side_effect=infer) as call:
            prepare_kirikiri(self.p,self.cfg)
            self.assertEqual(call.call_count,1)
            saved=json.loads((self.p/'data/name-proposals.json').read_text(encoding='utf8'))
            self.assertEqual(saved['a']['text'],'앨리스');self.assertEqual(list(saved),['a'])
            self.assertFalse((self.p/'data/translations.jsonl').exists())
            prepare_kirikiri(self.p,self.cfg); self.assertEqual(call.call_count,1)

if __name__=='__main__': unittest.main()
