"""Invented actor/label fixtures only; no game, project cache or model access."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from rpgmaker_names import align_names,apply_names


def actor(uid,source,index=1,**extra):
    return dict(id=uid,source=source,file='www/data/Actors.json',kind='string',usage='speaker_label',
                location=dict(fmt='json',ptr=f'[{index}].name'),**extra)


def speaker(uid,source,code=101,field=4,**extra):
    result=dict(id=uid,source=source,file='www/data/Map001.json',kind='string',usage='speaker_label',
                location=dict(fmt='json',ptr=f'events[1].pages[0].list[2].parameters[{field}]'),**extra)
    if code is not None:result['rpg_command_code']=code
    return result


class RPGMakerNameTests(unittest.TestCase):
    def test_a_dialogue_sentence_or_control_code_is_not_an_actor_name_authority(self):
        rows=[actor('actor','Mira'),speaker('label','Mira')]
        for bad in ('준비가 다 되었습니다.','미라\\C[2]','미라\n대사'):
            with self.subTest(bad=bad):
                known={'actor':{'text':bad},'label':{'text':'기존 이름'}}
                effective,report=align_names(rows,known)
                self.assertEqual(effective,known);self.assertFalse(report['canonical'])

    def test_conflicting_manual_aliases_do_not_choose_an_arbitrary_actor_name(self):
        rows=[actor('actor','Mira',base_source='ミラ',source_aliases=['Mira','ミラ']),speaker('label','Mira')]
        known={'actor':{'text':'미라'},'label':{'text':'기존 이름'}}
        effective,report=align_names(rows,known,{'Mira':'미라','ミラ':'다른 이름'})
        self.assertEqual(effective,known)
        self.assertEqual(report['override_conflict_actor_ids'],['actor'])
        self.assertFalse(report['canonical'])

    def test_explicit_english_japanese_and_kanji_aliases_share_actor_spelling(self):
        rows=[actor('actor','ハル',base_source='Haru',source_aliases=['Haru','ハル','春']),
              speaker('english','Haru'),speaker('kana','ハル'),speaker('kanji','春')]
        known={'actor':{'text':'하루'},'english':{'text':'하로'},'kana':{'text':'하루우'},'kanji':{'text':'춘'}}
        before_rows=deepcopy(rows);before_known=deepcopy(known)
        effective,report=align_names(rows,known)
        self.assertEqual([effective[r['id']]['text'] for r in rows],['하루']*4)
        self.assertEqual(report['updated_ids'],['english','kana','kanji'])
        self.assertEqual(rows,before_rows);self.assertEqual(known,before_known)

    def test_only_proven_speaker_fields_are_aligned(self):
        rows=[actor('actor','Mira'),speaker('mz','Mira'),speaker('rename','Mira',320,1),
              speaker('nickname','Mira',324,1),speaker('wrong_field','Mira',101,0),
              speaker('unknown_code','Mira',None)]
        known={r['id']:{'text':'별도 표기'} for r in rows};known['actor']['text']='미라'
        effective,report=align_names(rows,known)
        self.assertEqual(report['updated_ids'],['mz','rename'])
        self.assertEqual(report['missing_command_evidence_ids'],['unknown_code'])
        for uid in ('nickname','wrong_field','unknown_code'):
            self.assertEqual(effective[uid]['text'],'별도 표기')

    def test_dialogue_choices_ui_and_unknown_people_remain_unchanged(self):
        rows=[actor('actor','Mira'),speaker('unknown','Other'),
              dict(speaker('dialogue','Mira'),kind='dialogue',usage='dialogue'),
              dict(speaker('choice','Mira'),usage='choice'),dict(speaker('ui','Mira'),usage='ui_label')]
        known={r['id']:{'text':'기존 값'} for r in rows};known['actor']['text']='미라'
        effective,report=align_names(rows,known)
        self.assertEqual(report['updated_ids'],[])
        self.assertEqual(effective,known)

    def test_valid_actor_can_restore_a_failed_or_missing_label(self):
        rows=[actor('actor','Mira'),speaker('failed','Mira'),speaker('missing','Mira')]
        known={'actor':{'text':'미라'},'failed':{'text':'Mira','status':'source_fallback','error':'synthetic failure'}}
        effective,report=align_names(rows,known)
        for uid in ('failed','missing'):
            self.assertEqual(effective[uid]['text'],'미라')
            self.assertEqual(effective[uid]['status'],'name_alignment')
        self.assertEqual(report['updated_ids'],['failed','missing'])

    def test_untranslated_or_failed_actor_is_not_an_authority(self):
        for item in ({'text':'Mira'},{'text':'ミラ'},{'text':'미라','status':'source_fallback'}):
            with self.subTest(item=item):
                rows=[actor('actor','Mira'),speaker('literal','Mira')]
                known={'actor':item,'literal':{'text':'기존 값'}}
                effective,report=align_names(rows,known)
                self.assertEqual(effective,known)
                self.assertEqual(report['untranslated_actor_ids'],['actor'])

    def test_conflicting_same_source_blocks_only_that_alias(self):
        rows=[actor('one','Shared',1,base_source='UniqueOne'),
              actor('two','Shared',2,base_source='UniqueTwo'),
              speaker('ambiguous','Shared'),speaker('unique','UniqueOne')]
        known={'one':{'text':'하나'},'two':{'text':'둘'},'ambiguous':{'text':'기존 이름'},'unique':{'text':'잘못된 표기'}}
        effective,report=align_names(rows,known)
        self.assertEqual(report['ambiguous_sources'],['Shared'])
        self.assertEqual(effective['ambiguous']['text'],'기존 이름')
        self.assertEqual(effective['unique']['text'],'하나')
        self.assertEqual(effective['one']['text'],'하나');self.assertEqual(effective['two']['text'],'둘')

    def test_existing_manual_override_updates_actor_and_literal_but_not_unknown_name(self):
        rows=[actor('actor','ハル',base_source='Haru',source_aliases=['Haru','ハル']),
              speaker('literal','Haru'),speaker('unknown','Other')]
        known={'actor':{'text':'하로'},'literal':{'text':'하르'},'unknown':{'text':'기존 표기'}}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'names.json').write_text(json.dumps({'overrides':{'Haru':'하루','Other':'다른 이름'}},ensure_ascii=False),encoding='utf-8')
            effective,report=apply_names(root,{},rows,known)
        self.assertEqual(effective['actor']['text'],'하루');self.assertEqual(effective['literal']['text'],'하루')
        self.assertEqual(effective['unknown']['text'],'기존 표기')
        self.assertNotIn('Other',report['canonical'])

    def test_manual_names_are_plain_text_and_cannot_inject_control_codes(self):
        with self.assertRaisesRegex(ValueError,'plain text'):
            align_names([actor('actor','Mira')],{'actor':{'text':'미라'}},{'Mira':'미라\\C[2]'})


if __name__=='__main__':unittest.main()
