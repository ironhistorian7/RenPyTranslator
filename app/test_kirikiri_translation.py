"""Synthetic KiriKiri protocol checks; no game, network or model execution."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine import save_json
import failed_repair
import translation
from kirikiri_translation import row_config

BASE = dict(language='korean', source_language='japanese', engine='kirikiri',
            endpoint='http://127.0.0.1:1', num_ctx=16384, parallel=1,
            batch_size=8, context_txt_enabled=False)


class KiriTranslationTests(unittest.TestCase):
    def setUp(self):
        for target in ('request_budget.post', 'hy_backend.api'):
            p=patch(target,side_effect=AssertionError('No external calls'))
            p.start();self.addCleanup(p.stop)
        for target in ('translation.trace_event','diagnostics.report','job_events.warning'):
            p=patch(target,return_value=None);p.start();self.addCleanup(p.stop)

    def row(self, source, dialect, usage='dialogue'):
        return dict(id='a',source=source,kind='dialogue',usage=usage,dialect=dialect,
                    file='synthetic.scn',block='synthetic')

    def test_both_protocols_restore_kag_and_psb_controls(self):
        examples = [('kag','こんにちは[font color="red"]世界[l]','안녕<rpt000/>세계<rpt001/>','안녕[font color="red"]세계[l]'),
                    ('kagex_psb',r'こんにちは$actor;世界%1\n', '안녕<rpt000/>세계<rpt001/><rpt002/>',r'안녕$actor;세계%1\n')]
        for model in ('synthetic-hy','synthetic-gemma'):
            for dialect,source,answer,expected in examples:
                with self.subTest(model=model,dialect=dialect):
                    cfg=dict(BASE,model=model);row=self.row(source,dialect)
                    content='[0] '+answer if model.endswith('hy') else json.dumps({'0':answer},ensure_ascii=False)
                    response={'message':{'content':content},'done_reason':'stop'}
                    with patch('hy_backend.is_hy',return_value=model.endswith('hy')), \
                         patch('translation.request',return_value=response) as request:
                        result,_=translation.translate_batch([row],{},cfg)
                    prompt=request.call_args.args[2]
                    self.assertEqual(result[0]['text'],expected)
                    self.assertEqual(result[0]['dialect'],dialect)
                    self.assertIn('<rpt000/>',str(prompt))
                    self.assertNotIn('$actor;',str(prompt))

    def test_approved_body_name_and_newline_restore_with_both_protocols(self):
        from kirikiri_name_readings import collect
        row=self.row('雪森さんが来た。\n待っていた。','kagex_psb')
        index=collect([row],BASE,{'雪森':'유키모리'})
        for model in ('synthetic-hy','synthetic-gemma'):
            with self.subTest(model=model):
                is_hy=model.endswith('hy')
                cfg=dict(BASE,model=model,_kirikiri_name_index=index)
                answer='<rpt000/> 씨가 왔다. 기다렸다.'
                content='[0] '+answer if is_hy else json.dumps({'0':answer},ensure_ascii=False)
                response={'message':{'content':content},'done_reason':'stop'}
                with patch('hy_backend.is_hy',return_value=is_hy), \
                     patch('translation.request',return_value=response) as request:
                    result,_=translation.translate_batch([row],{},cfg)
                self.assertEqual(result[0]['text'],'유키모리 씨가 왔다.\n 기다렸다.')
                prompt=str(request.call_args.args[2])
                self.assertIn('유키모리',prompt)
                self.assertIn('<rpt000/>',prompt)
                self.assertNotIn('雪森さん',prompt)

    def test_missing_name_marker_is_not_recovered_as_a_linebreak(self):
        from kirikiri_name_readings import collect
        from kirikiri_tokens import NAME_ANCHOR_FORMAT
        row=self.row('雪森さんが来た。\n待っていた。','kagex_psb')
        index=collect([row],BASE,{'雪森':'유키모리'})
        cfg=dict(BASE,model='synthetic-hy',_kirikiri_name_index=index)
        answer='[0] 다른 이름이 왔다.<rpt001/>기다렸다.'
        with patch('hy_backend.is_hy',return_value=True), \
             patch('translation.request',return_value={'message':{'content':answer},'done_reason':'stop'}):
            with self.assertRaises(translation.BatchTranslationError) as failed:
                translation.translate_batch([row],{},cfg)
        self.assertEqual(failed.exception.failures[0]['input_format'],NAME_ANCHOR_FORMAT)
        self.assertIsNone(failed_repair.saved_output(row['source'],"failed; output='다른 이름이 왔다.'",
                          row_config(cfg,row),'dialogue',input_format=NAME_ANCHOR_FORMAT))

    def test_normalize_marker_delimiters_once_and_trace_only_changes(self):
        examples=[('kag','Hello[font size=20]world[wait time=1]',
                   '안녕{first}세계{second}','안녕[font size=20]세계[wait time=1]'),
                  ('kagex_psb','Hello$name;world%125',
                   '안녕{first}세계{second}','안녕$name;세계%125')]
        spellings=[('<rpt000>', '[rpt001]', True),
                   ('<rtpt000/>', 'rpt_001', True),
                   ('<rpt000/>', '<rpt001/>', False)]
        for model in ('synthetic-hy','synthetic-gemma'):
            for dialect,source,template,expected in examples:
                for first,second,repaired in spellings:
                    with self.subTest(model=model,dialect=dialect,first=first):
                        answer=template.format(first=first,second=second)
                        content='[0] '+answer if model.endswith('hy') else json.dumps({'0':answer},ensure_ascii=False)
                        response={'message':{'content':content},'done_reason':'stop'}
                        with patch('hy_backend.is_hy',return_value=model.endswith('hy')), \
                             patch('translation.request',return_value=response) as request, \
                             patch('translation.trace_event') as trace:
                            result,_=translation.translate_batch([self.row(source,dialect)],{},dict(BASE,model=model))
                        self.assertEqual(result[0]['text'],expected)
                        self.assertEqual(request.call_count,1)
                        events=[c for c in trace.call_args_list if len(c.args)>2 and c.args[2]=='kirikiri_protocol_repair']
                        self.assertEqual(len(events),int(repaired))

    def test_bad_marker_identity_counts_and_order_still_fail_once(self):
        examples=[('kag','Hello[font size=20]world[wait time=1]'),
                  ('kagex_psb','Hello$name;world%125')]
        bad=['안녕<rpt999>세계<rpt001/>',
             '안녕<rpt000/>세계',
             '안녕<rpt000/>세계<rpt001/><rpt001/>',
             '안녕<rpt001/>세계<rpt000/>']
        for model in ('synthetic-hy','synthetic-gemma'):
            for dialect,source in examples:
                for answer in bad:
                    with self.subTest(model=model,dialect=dialect,answer=answer):
                        content='[0] '+answer if model.endswith('hy') else json.dumps({'0':answer},ensure_ascii=False)
                        response={'message':{'content':content},'done_reason':'stop'}
                        with patch('hy_backend.is_hy',return_value=model.endswith('hy')), \
                             patch('translation.request',return_value=response) as request:
                            with self.assertRaises(translation.BatchTranslationError):
                                translation.translate_batch([self.row(source,dialect)],{},dict(BASE,model=model))
                        self.assertEqual(request.call_count,1)

    def test_source_owned_marker_literals_are_never_reinterpreted(self):
        cases=[('rpt0 [l]','rpt0',None),
               ('<rpt000/>','<rpt000/>','<rpt000/>'),
               ('<rpt0>','<rpt0>','<rpt0>')]
        for model in ('synthetic-hy','synthetic-gemma'):
            for source,answer,expected in cases:
                with self.subTest(model=model,source=source):
                    content='[0] '+answer if model.endswith('hy') else json.dumps({'0':answer},ensure_ascii=False)
                    response={'message':{'content':content},'done_reason':'stop'}
                    with patch('hy_backend.is_hy',return_value=model.endswith('hy')), \
                         patch('translation.request',return_value=response) as request, \
                         patch('translation.trace_event') as trace:
                        if expected is None:
                            with self.assertRaises(translation.BatchTranslationError):
                                translation.translate_batch([self.row(source,'kag')],{},dict(BASE,model=model))
                        else:
                            result,_=translation.translate_batch([self.row(source,'kag')],{},dict(BASE,model=model))
                            self.assertEqual(result[0]['text'],expected)
                    self.assertEqual(request.call_count,1)
                    events=[c for c in trace.call_args_list if len(c.args)>2 and c.args[2]=='kirikiri_protocol_repair']
                    self.assertEqual(events,[])

    def test_kagex_new_reserved_literals_render_without_changing_controls(self):
        from kirikiri_tokens import readable
        cases=[('Hello\nworld','성공률100%<rpt000/>괜찮아','성공률100\\%\n괜찮아','성공률100%\n괜찮아'),
               ('Hello%125world','성공률100%<rpt000/>괜찮아','성공률100\\%%125괜찮아','성공률100%괜찮아'),
               ('Hello\nworld','가격$1 # [A] 경로\\파일<rpt000/>끝',
                '가격\\$1 \\# \\[A] 경로\\\\파일\n끝','가격$1 # [A] 경로\\파일\n끝')]
        for model in ('synthetic-hy','synthetic-gemma'):
            for source,answer,expected,visible in cases:
                with self.subTest(model=model,case=source,answer=answer):
                    content='[0] '+answer if model.endswith('hy') else json.dumps({'0':answer},ensure_ascii=False)
                    response={'message':{'content':content},'done_reason':'stop'}
                    with patch('hy_backend.is_hy',return_value=model.endswith('hy')), \
                         patch('translation.request',return_value=response) as request, \
                         patch('translation.trace_event') as trace:
                        result,_=translation.translate_batch([self.row(source,'kagex_psb')],{},dict(BASE,model=model))
                    self.assertEqual(request.call_count,1)
                    self.assertEqual(result[0]['text'],expected)
                    self.assertEqual(readable(result[0]['text'],{'_kirikiri_dialect':'kagex_psb'}),visible)
                    self.assertEqual(len([c for c in trace.call_args_list if len(c.args)>2 and c.args[2]=='kirikiri_literal_escape']),1)

    def test_kagex_original_literal_placeholder_must_not_be_lost(self):
        source=r'Hello\% world\n'
        for model in ('synthetic-hy','synthetic-gemma'):
            for answer,accepted in [('안녕<rpt000/>세계<rpt001/>',True),('안녕 세계<rpt001/>',False)]:
                with self.subTest(model=model,answer=answer):
                    content='[0] '+answer if model.endswith('hy') else json.dumps({'0':answer},ensure_ascii=False)
                    response={'message':{'content':content},'done_reason':'stop'}
                    with patch('hy_backend.is_hy',return_value=model.endswith('hy')), \
                         patch('translation.request',return_value=response) as request:
                        if accepted:
                            result,_=translation.translate_batch([self.row(source,'kagex_psb')],{},dict(BASE,model=model))
                            self.assertEqual(result[0]['text'],r'안녕\%세계\n')
                        else:
                            with self.assertRaises(translation.BatchTranslationError):
                                translation.translate_batch([self.row(source,'kagex_psb')],{},dict(BASE,model=model))
                        self.assertEqual(request.call_count,1)

    def test_speaker_name_can_remain_unchanged_and_controls_cannot_change(self):
        cfg=dict(BASE,model='synthetic',_kirikiri_usage='speaker_label')
        self.assertEqual(translation.validate_text('Alice','Alice',cfg),[])
        self.assertTrue(translation.validate_text('Alice[l]','Alice[p]',cfg))
        cfg['_kirikiri_dialect']='kagex_psb'
        self.assertTrue(translation.validate_text(r'Hello$name;','안녕',cfg))

    def test_swapped_functional_tokens_fail_both_protocols(self):
        row=self.row('Hello[l]world[p]','kag')
        for model in ('synthetic-hy','synthetic-gemma'):
            with self.subTest(model=model):
                answer='안녕<rpt001/>세계<rpt000/>'
                content='[0] '+answer if model.endswith('hy') else json.dumps({'0':answer},ensure_ascii=False)
                response={'message':{'content':content},'done_reason':'stop'}
                with patch('hy_backend.is_hy',return_value=model.endswith('hy')), \
                     patch('translation.request',return_value=response):
                    with self.assertRaises(translation.BatchTranslationError):
                        translation.translate_batch([row],{},dict(BASE,model=model))

    def test_saved_failed_output_recovery_both_dialects(self):
        for dialect,source,output,expected in (
            ('kag','Hello[l]','안녕<rpt000/>','안녕[l]'),
            ('kagex_psb','Hello$name;','안녕<rpt000/>','안녕$name;')):
            with self.subTest(dialect=dialect):
                cfg=row_config(dict(BASE,model='synthetic'),self.row(source,dialect))
                recovered=failed_repair.saved_output(source,'invalid; output='+repr(output),cfg,'dialogue',input_format='kirikiri_protected_v1')
                self.assertEqual(recovered,expected)
                self.assertIsNone(failed_repair.saved_output(source,"invalid; output='안녕'",cfg,'dialogue'))

    def test_resume_saved_recovery_preserves_matching_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp);row=self.row('Hello$name;', 'kagex_psb')
            cfg=dict(BASE,model='synthetic',_skip_name_review=True)
            save_json(project/'data/catalog.json',[row])
            save_json(project/'data/recovered-translations.json',{'a':dict(id='a',source=row['source'],before=None,text='안녕$name;')})
            result=translation.cache(project,cfg)
            self.assertEqual(result['a']['text'],'안녕$name;')
            self.assertEqual(result['a']['status'],'failure_recovered')
            self.assertEqual(result['a']['dialect'],'kagex_psb')

    def test_resume_manual_overlays_recovered_and_reviewed(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp);row=self.row('Hello[l]','kag');cfg=dict(BASE,model='synthetic',_skip_name_review=True)
            save_json(project/'data/catalog.json',[row])
            manual=dict(id='a',source=row['source'],text='수동[l]',status='manual_translation',model='old',fingerprint='old')
            (project/'data/translations.jsonl').write_text(json.dumps(manual,ensure_ascii=False)+'\n',encoding='utf-8')
            save_json(project/'data/recovered-translations.json',{'a':dict(id='a',source=row['source'],before='수동[l]',text='복구[l]')})
            save_json(project/'data/reviewed-translations.json',{row['source']:'검토[l]'})
            result=translation.cache(project,cfg)
            self.assertEqual(result['a']['text'],'수동[l]')
            self.assertEqual(result['a']['status'],'manual_translation')
            self.assertEqual(result['a']['dialect'],'kag')

if __name__=='__main__': unittest.main()
