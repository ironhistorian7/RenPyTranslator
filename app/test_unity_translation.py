from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

from translation import BatchTranslationError, translate_batch


class UnityTranslationTest(unittest.TestCase):
    cfg = {'model':'synthetic:model', 'engine':'unity', 'source_language':'japanese', 'num_ctx':8192}
    row = {'id':'ui', 'source':r'<b>残り {0} 20 円</b>\n', 'kind':'string', 'usage':'ui_label',
           'unity_format':'key_value'}

    def engine_reply(self, reply):
        def send(items, cfg, *_args, **_kwargs):
            self.assertEqual(len(items),1)
            self.assertEqual(items[0]['text'],'<rpt000/>残り <rpt001/> 20 円<rpt002/><rpt003/>')
            self.assertEqual(items[0]['movable_placeholders'],[])
            return {'0':reply}, {}, {'budget':{'context_sha256':'test'},'request_id':'test','attempts':{},
                                     'request_ids':['test'],'seconds':0,'tokens':0,'eval_seconds':0}
        return patch('translation_segments.execute', side_effect=send)

    def test_unity_controls_are_restored_before_cache(self):
        with self.engine_reply('<rpt000/>남은 금액 <rpt001/> 20원<rpt002/><rpt003/>'):
            result,_=translate_batch([self.row],{},self.cfg)
        self.assertEqual(result[0]['text'],r'<b>남은 금액 {0} 20원</b>\n')

    def test_missing_unity_control_is_rejected(self):
        with self.engine_reply('<rpt000/>남은 금액 <rpt001/> 20원<rpt003/>'):
            with self.assertRaises(BatchTranslationError) as exc:
                translate_batch([self.row],{},self.cfg)
        self.assertEqual(exc.exception.failures[0]['input_format'],'unity_textasset_v2')
        self.assertIn('Protected token mismatch',exc.exception.failures[0]['error'])

    def test_single_hy_without_header_still_restores_and_validates_choice(self):
        row = dict(self.row,id='choice',source='(Wait ten minutes)',usage='choice',unity_format='command')
        cfg = dict(self.cfg,model='rpt-hymt2-7b:q6_k',source_language='english',endpoint='http://127.0.0.1:1')
        reply = {'message': {'content': '10분을 기다린다'}, 'done_reason': 'stop'}
        with (patch('translation.request',return_value=reply) as request,
              patch('translation.runtime_status',return_value={})):
            result,_=translate_batch([row],{},cfg)
        self.assertEqual(result[0]['text'],'(10분을 기다린다)')
        request.assert_called_once()

    def test_saved_replies_restore_numbers_from_old_markers_and_not_new_markers(self):
        from failed_repair import saved_output
        source='Wait 20 seconds, {name}.'
        cfg=dict(self.cfg,source_language='english',_unity_text_kind='command')
        for version,reply in [
            ('unity_textasset_v1','<rpt000/>초 기다리세요, <rpt001/>님.'),
            ('unity_textasset_v2','<rpt000/>님, 20초 기다리세요.'),
        ]:
            self.assertIn('20초',saved_output(source,'failed; output='+repr(reply),cfg,'dialogue',input_format=version))

    def test_renderable_numeric_change_reaches_review_with_draft(self):
        from translation_review import build_review
        row=dict(self.row,source='Pay a thousand and fifty an hour. This is the one option.',
                 usage='dialogue',unity_format='command')
        cfg=dict(self.cfg,model='rpt-hymt2-7b:q6_k',source_language='english',endpoint='http://127.0.0.1:1')
        draft='시간당 15만 원을 지불합니다.'
        reply={'message': {'content':'[0] '+draft}, 'done_reason':'stop'}
        with (patch('translation.request',return_value=reply) as request,
              patch('translation.runtime_status',return_value={})):
            result,_=translate_batch([row],{},cfg)
        entry=build_review([row],{row['id']:result[0]},cfg)['entries'][0]
        self.assertEqual(entry['current'],draft)
        request.assert_called_once()

    def test_recovered_number_warning_is_saved_in_the_cache(self):
        from failed_repair import run
        from translation import cache, fingerprint
        row=dict(self.row,source='The hourly price is a thousand and fifty.',
                 usage='dialogue',unity_format='command')
        cfg=dict(self.cfg,source_language='english',language='korean')
        draft='시간당 15만 원입니다.'
        entry=dict(id=row['id'],source=row['source'],text=row['source'],
                   model=cfg['model'],fingerprint=fingerprint(cfg),status='source_fallback')
        error='Unity text structure mismatch; output='+repr(draft)
        with TemporaryDirectory() as tmp:
            project=Path(tmp)
            data=project/'data'
            data.mkdir()
            (data/'catalog.json').write_text(json.dumps([row]),encoding='utf-8')
            (data/'translations.jsonl').write_text(json.dumps(entry)+'\n',encoding='utf-8')
            (data/'failed-items.json').write_text(json.dumps([dict(row=row,error=error)]),encoding='utf-8')
            _,report=run(project,cfg)
            restored=cache(project,cfg)[row['id']]
        self.assertEqual(report['model_calls'],0)
        self.assertEqual(restored['text'],draft)
        self.assertTrue(restored['_unity_numeric_review'])
