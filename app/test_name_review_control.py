"""Checkpoint protocol tests; no model, game, or normal project access."""
from pathlib import Path
from contextlib import nullcontext
import json
import tempfile
import unittest
from unittest.mock import patch

from name_review_control import run,_desktop_reply,prepare_rpg

REVIEW={'version':1,'fingerprint':'test','confirmed':False,'entries':[
    {'key':'actor:1','source':'試験','proposed':'시켄','value':'試験','needs_review':True,'reason':'reading'}]}


class NameReviewControlTests(unittest.TestCase):
    def test_approved_checkpoint_returns_without_prompt_or_model(self):
        for value in (dict(REVIEW,confirmed=True),dict(REVIEW,entries=[])):
            with patch('name_review.build_review',return_value=value),patch('name_review_control._console_reply') as ask:
                self.assertEqual(run(Path('.'),{}),value)
                ask.assert_not_called()

    def test_confirmation_precedes_the_callers_translation(self):
        order=[]
        with patch('name_review.build_review',return_value=REVIEW),\
             patch('name_review_control._console_reply',side_effect=lambda r:order.append('confirm') or r),\
             patch('name_review.apply_review',side_effect=lambda *a:order.append('save') or {'confirmed':True}):
            result=run(Path('.'),{})
            order.append('translate')
        self.assertTrue(result['confirmed'])
        self.assertEqual(order,['confirm','save','translate'])

    def test_invalid_edit_remains_at_the_gate_without_losing_edits(self):
        invalid=dict(REVIEW,entries=[dict(REVIEW['entries'][0],value='입력값')])
        with patch.dict('os.environ',{'RPT_DESKTOP_EVENTS':'1','RPT_NAME_REVIEW_FILE':'unused'}),\
             patch('name_review.build_review',return_value=REVIEW),\
             patch('name_review_control._desktop_reply',side_effect=[invalid,invalid]),\
             patch('name_review.apply_review',side_effect=[ValueError('이름을 확인하세요.'),{'confirmed':True}]),\
             patch('name_review_control.emit') as emit:
            self.assertTrue(run(Path('.'),{})['confirmed'])
        last=emit.call_args.kwargs['review']
        self.assertEqual(last['entries'][0]['value'],'입력값')
        self.assertIn('error',last)

    def test_cancel_does_not_approve_or_start_translation(self):
        with patch('name_review.build_review',return_value=REVIEW),\
             patch('name_review_control._cancelled',side_effect=KeyboardInterrupt),\
             patch('name_review.apply_review') as save:
            with self.assertRaises(KeyboardInterrupt):run(Path('.'),{})
            save.assert_not_called()

    def test_atomic_reply_is_consumed_once(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'reply.json';path.write_text('{"fingerprint":"test"}',encoding='utf-8')
            self.assertEqual(_desktop_reply(REVIEW,path),{'fingerprint':'test'})
            self.assertFalse(path.exists())

    def test_automatic_names_never_send_dialogue_or_write_translation_cache(self):
        actor=dict(id='a',source='春',kind='string',usage='speaker_label',file='data/Actors.json',
                   location={'fmt':'json','ptr':'[1].name'},source_aliases=['春','ハル'])
        dialogue=dict(id='d',source='これは試験です。',kind='dialogue',usage='dialogue',file='data/Map001.json')
        review=dict(REVIEW,entries=[dict(REVIEW['entries'][0],key='rpg:a',reading='ハル')])
        cfg=dict(engine='rpgmaker',model='synthetic',source_language='japanese')
        with tempfile.TemporaryDirectory() as root:
            project=Path(root);(project/'data').mkdir()
            with patch('name_review.build_review',return_value=review),\
                 patch('translation.read_catalog',return_value=[actor,dialogue]),\
                 patch('translation.cache',return_value={}),\
                 patch('hy_backend.runtime',side_effect=lambda c:nullcontext(c)),\
                 patch('translation.translate_batch',return_value=([{'id':'a','text':'하루'}],{})) as request:
                prepare_rpg(project,cfg)
                sent=request.call_args.args[0]
                self.assertEqual([(r['id'],r['source']) for r in sent],[('a','ハル')])
                self.assertTrue(request.call_args.args[2]['_name_preparation'])
                proposals=json.loads((project/'data/name-proposals.json').read_text(encoding='utf-8'))
                self.assertEqual(proposals['a']['source'],'春')
                self.assertEqual(proposals['a']['text'],'하루')
                self.assertFalse((project/'data/translations.jsonl').exists())
                request.reset_mock();prepare_rpg(project,cfg);request.assert_not_called()

    def test_failed_name_proposal_is_kept_for_review_without_repeat_translation(self):
        from translation import BatchTranslationError
        actor=dict(id='a',source='春',kind='string',usage='speaker_label',file='data/Actors.json',
                   location={'fmt':'json','ptr':'[1].name'})
        review=dict(REVIEW,entries=[dict(REVIEW['entries'][0],key='rpg:a')])
        failure=BatchTranslationError([], [{'row':actor,'error':"error; output='ハル'"}],{})
        cfg=dict(engine='rpgmaker',model='synthetic',source_language='japanese')
        with tempfile.TemporaryDirectory() as root:
            project=Path(root);(project/'data').mkdir()
            with patch('name_review.build_review',return_value=review),\
                 patch('translation.read_catalog',return_value=[actor]),\
                 patch('translation.cache',return_value={}),\
                 patch('hy_backend.runtime',side_effect=lambda c:nullcontext(c)),\
                 patch('translation.translate_batch',side_effect=failure):
                prepare_rpg(project,cfg)
            value=json.loads((project/'data/name-proposals.json').read_text(encoding='utf-8'))
            self.assertEqual(value['a']['text'],'ハル')

    def test_approved_overlay_reaches_cache_without_changing_saved_translation(self):
        from engine import save_json
        from name_review import build_review,apply_review,configuration
        from translation import cache,fingerprint
        actor=dict(id='a',source='春（私服）',kind='string',usage='speaker_label',file='data/Actors.json',
                   location={'fmt':'json','ptr':'[1].name'})
        speaker=dict(id='s',source='春（私服）',kind='string',usage='speaker_label',file='data/Map001.json',
                     rpg_command_code=101,location={'fmt':'json','ptr':'events[1].pages[0].list[0].parameters[4]'})
        cfg=dict(engine='rpgmaker',model='synthetic',language='korean',source_language='japanese',num_ctx=16384)
        with tempfile.TemporaryDirectory() as root:
            p=Path(root);save_json(p/'data/catalog.json',[actor,speaker])
            original=json.dumps(dict(id='a',source=actor['source'],text='하루（사복）',
                                     fingerprint=fingerprint(cfg),model='synthetic'),ensure_ascii=False)+'\n'
            path=p/'data/translations.jsonl';path.write_text(original,encoding='utf-8')
            review=build_review(p,cfg);self.assertEqual(len(review['entries']),1)
            review['entries'][0]['value']='봄이2（사복）';apply_review(p,cfg,review)
            effective=cache(p,cfg)
            self.assertEqual(effective['a']['text'],'봄이2（사복）')
            self.assertEqual(effective['s']['text'],'봄이2（사복）')
            self.assertEqual(configuration(p,cfg,[actor,speaker])['_person_names']['春'],'봄이2')
            self.assertTrue(build_review(p,cfg)['confirmed'])
            self.assertEqual(path.read_text(encoding='utf-8'),original)

    def test_failed_korean_proposal_still_requires_manual_review(self):
        from engine import save_json
        from name_review import build_review
        row=dict(id='a',source='Mira',kind='string',usage='speaker_label',file='data/Actors.json',
                 location={'fmt':'json','ptr':'[1].name'})
        with tempfile.TemporaryDirectory() as root:
            p=Path(root);save_json(p/'data/catalog.json',[row])
            save_json(p/'data/name-proposals.json',{'a':{'source':'Mira','text':'미라가 문을 열고 말합니다.',
                'error':'label expansion','model':'synthetic'}})
            entry=build_review(p,dict(engine='rpgmaker',model='synthetic',language='korean'))['entries'][0]
            self.assertTrue(entry['needs_review']);self.assertEqual(entry['reason'],'automatic_failed')
            self.assertEqual(entry['value'],'Mira');self.assertIn('미라',entry['proposed'])


if __name__=='__main__':unittest.main()
