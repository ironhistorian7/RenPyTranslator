"""Synthetic resume regressions; no external service or game is used."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine import save_json
import translation

BASE = dict(model='synthetic-gemma', source_language='japanese', language='korean',
            endpoint='http://unused.invalid', num_ctx=16384, parallel=1, batch_size=8,
            context_txt_enabled=False)


def row(uid, source='こんにちは', usage='dialogue'):
    return dict(id=uid, source=source, kind='dialogue', usage=usage,
                dialect='kag', file='synthetic', block='one')


class JapaneseResumeIntegrationTests(unittest.TestCase):
    def project(self, engine, rows, values, **options):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        project = Path(tmp.name); cfg = dict(BASE, engine=engine, **options)
        save_json(project/'data/catalog.json', rows)
        records=[]
        for r in rows:
            value=values[r['id']]
            records.append(dict(id=r['id'], source=r['source'], text=value[0],
                                status=value[1], model=cfg['model'],
                                fingerprint=translation.fingerprint(cfg)))
        path=project/'data/translations.jsonl'
        path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records),encoding='utf-8')
        return project,cfg,path

    def test_cache_marks_only_residue_and_preserves_manual_and_names(self):
        for engine in ('renpy','kirikiri','rpgmaker'):
            with self.subTest(engine=engine):
                rows=[row('clean'),row('mixed'),row('none'),row('minor'),row('manual'),row('name', 'ミッカ', 'speaker_label')]
                values={'clean':('안녕하세요','accepted'), 'mixed':('안녕日本語','accepted'),
                        'none':('日本語','accepted'), 'minor':('안녕っッｯ','partial_translation'),
                        'manual':('수동っ日本語','manual_translation'), 'name':('ミッカ','accepted')}
                project,cfg,path=self.project(engine,rows,values); before=path.read_bytes()
                found=translation.cache(project,cfg)
                self.assertTrue(found['mixed'].get('_japanese_residual_language') or found['mixed'].get('_rpg_residual_language'))
                self.assertTrue(found['none'].get('_japanese_residual_language') or found['none'].get('_rpg_residual_language'))
                self.assertEqual(found['minor']['text'],'안녕')
                self.assertEqual(found['manual']['text'],'수동っ日本語')
                self.assertEqual(found['name']['text'],'ミッカ')
                self.assertEqual(path.read_bytes(),before)

    def test_resume_only_residue_and_second_resume_no_request(self):
        for engine in ('renpy','kirikiri','rpgmaker'):
            with self.subTest(engine=engine):
                rows=[row('clean'),row('mixed'),row('minor'),row('manual')]
                values={'clean':('안녕하세요','accepted'), 'mixed':('안녕日本語','accepted'),
                        'minor':('안녕っッｯ','accepted'), 'manual':('수동日本語','manual_translation')}
                project,cfg,path=self.project(engine,rows,values); before=path.read_bytes()
                response={'message':{'content':'{"0":"안녕하세요"}'},'done_reason':'stop'}
                with redirect_stdout(io.StringIO()), patch('runtime_recovery.run',side_effect=lambda p,c,fn,count:fn(c)), \
                     patch('translation.request',return_value=response) as request, \
                     patch('source_context.SourceContext.context',return_value={}):
                    translation.translate(project,cfg)
                    self.assertEqual(request.call_count,1)
                    request.reset_mock()
                    translation.translate(project,cfg)
                    self.assertEqual(request.call_count,0)
                data=path.read_bytes(); self.assertTrue(data.startswith(before))
                appended=[json.loads(line) for line in data[len(before):].decode('utf-8').splitlines()]
                self.assertEqual([x['id'] for x in appended],['mixed'])
                self.assertEqual(appended[0]['text'],'안녕하세요')

    def test_control_bytes_preserved_when_small_tsu_cleaned(self):
        examples = [('renpy','こんにちは{color=赤}', '안녕っッｯ{color=赤}', '안녕{color=赤}'),
                    ('kirikiri','こんにちは[font face="赤っ"]', '안녕っッｯ[font face="赤っ"]', '안녕[font face="赤っ"]'),
                    ('rpgmaker',r'こんにちは\V[1]', r'안녕っッｯ\V[1]', r'안녕\V[1]')]
        for engine, source, target, expected in examples:
            with self.subTest(engine=engine):
                project,cfg,path=self.project(engine,[row('a',source)],{'a':(target,'accepted')})
                before=path.read_bytes()
                self.assertEqual(translation.cache(project,cfg)['a']['text'],expected)
                self.assertEqual(path.read_bytes(),before)

    def test_hy_and_gemma_frames_clean_small_tsu(self):
        for is_hy in (True, False):
            with self.subTest(hy=is_hy):
                content='[0] 안녕하세요っッｯ' if is_hy else '{"0":"안녕하세요っッｯ"}'
                response={'message':{'content':content},'done_reason':'stop'}
                with patch('hy_backend.is_hy',return_value=is_hy), patch('translation.request',return_value=response) as request:
                    result,_=translation.translate_batch([row('a')],{},dict(BASE,engine='renpy'))
                self.assertEqual(request.call_count,1)
                self.assertEqual(result[0]['text'],'안녕하세요')

    def test_failed_structure_preserves_saved_korean_draft(self):
        for engine in ('renpy','kirikiri','rpgmaker'):
            with self.subTest(engine=engine):
                project,cfg,path=self.project(engine,[row('a')],{'a':('안녕日本語','accepted')})
                before=path.read_bytes()
                response={'message':{'content':'not a valid response'},'done_reason':'stop'}
                with redirect_stdout(io.StringIO()), patch('runtime_recovery.run',side_effect=lambda p,c,fn,count:fn(c)), \
                     patch('translation.request',return_value=response), \
                     patch('source_context.SourceContext.context',return_value={}):
                    translation.translate(project,cfg)
                self.assertTrue(path.read_bytes().startswith(before))
                found=translation.cache(project,cfg)['a']
                self.assertEqual(found['text'],'안녕日本語')

    def test_english_cache_and_resume_unchanged(self):
        rows=[row('clean','Hello')]
        project,cfg,path=self.project('renpy',rows,{'clean':('English text','accepted')},source_language='english')
        before=path.read_bytes()
        with redirect_stdout(io.StringIO()), patch('translation.request',side_effect=AssertionError('unexpected request')):
            translation.translate(project,cfg)
        self.assertEqual(path.read_bytes(),before)

    def test_partial_with_last_structure_error_still_resumes_one_at_a_time(self):
        rows=[row('a'),row('b')]
        values={'a':('이것은긴한국어문장反입니다','partial_translation'),
                'b':('안녕日本語','partial_translation')}
        for engine in ('renpy','kirikiri','rpgmaker'):
            with self.subTest(engine=engine):
                project,cfg,path=self.project(engine,rows,values)
                records=[dict(json.loads(line),error='markup mismatch') for line in path.read_text(encoding='utf-8').splitlines()]
                path.write_text(''.join(json.dumps(item,ensure_ascii=False)+'\n' for item in records),encoding='utf-8')
                calls=[]
                def translate_batch(batch,context,settings,note=''):
                    calls.append((len(batch),settings.get('_japanese_retry_kind',settings.get('_rpg_retry_kind'))))
                    return [dict(id=r['id'],source=r['source'],text='안녕하세요',model=cfg['model'],fingerprint=translation.fingerprint(cfg)) for r in batch],{'seconds':0,'items':len(batch)}
                with redirect_stdout(io.StringIO()),patch('runtime_recovery.run',side_effect=lambda p,c,fn,count:fn(c)), \
                     patch('translation.translate_batch',side_effect=translate_batch),patch('source_context.SourceContext.context',return_value={}):
                    translation.translate(project,cfg)
                self.assertEqual(sorted(calls),[(1,'minor'),(1,'mixed')])

    def test_non_rpg_minor_retries_complete_source_for_both_protocols(self):
        for engine,control in (('renpy',''),('kirikiri','')):
            source='こんにちは'+control+'世界'
            draft='좋은 아침입니다, 반갑습니다反'+control
            for hy in (True,False):
                with self.subTest(engine=engine,hy=hy):
                    cfg=dict(BASE,engine=engine,_item_retry_attempt=1,_japanese_retry_kind='minor',
                             _japanese_validated_draft={'source':source,'text':draft})
                    response={'message':{'content':('[0] 안녕하세요'+control+' 세계' if hy else json.dumps({'0':'안녕하세요'+control+' 세계'},ensure_ascii=False))},'done_reason':'stop'}
                    with patch('hy_backend.is_hy',return_value=hy),patch('translation.request',return_value=response) as request:
                        result,_=translation.translate_batch([row('a',source)],{'old_draft':'STALE_SENTINEL'},cfg)
                    self.assertEqual(result[0]['text'],'안녕하세요'+control+' 세계')
                    self.assertEqual(request.call_count,1)
                    self.assertNotIn('STALE_SENTINEL',str(request.call_args.args[2]))
                    self.assertIn('こんにちは',str(request.call_args.args[2]))
                    self.assertNotIn(draft,str(request.call_args.args[2]))

    def test_final_review_includes_old_accepted_residue_only_for_japanese(self):
        from translation_review import build_review
        rows=[row('a')]
        known={'a':dict(status='accepted',text='한국어日本語',_japanese_residual_language=True)}
        self.assertEqual(len(build_review(rows,known,BASE)['entries']),1)
        self.assertEqual(build_review(rows,known,dict(BASE,source_language='english'))['entries'],[])

    def test_japanese_hy_prompt_changes_on_second_retry_but_english_does_not(self):
        from hy_backend import body
        items=[{'id':'0','text':'こんにちは','usage':'dialogue'}]
        cfg=dict(BASE,engine='renpy',_japanese_retry_kind='minor')
        first=body(items,{},dict(cfg,_japanese_retry_attempt=1),{},'')['messages'][0]['content']
        second=body(items,{},dict(cfg,_japanese_retry_attempt=2),{},'')['messages'][0]['content']
        self.assertIn('말버릇',first)
        self.assertIn('원문 전체를 처음부터',second)
        self.assertNotEqual(first,second)
        first=body(items,{},dict(cfg,source_language='english',_japanese_retry_attempt=1),{},'')
        second=body(items,{},dict(cfg,source_language='english',_japanese_retry_attempt=2),{},'')
        self.assertEqual(first,second)

    def test_controlled_japanese_dialogue_is_not_excluded_as_a_path(self):
        from failure_candidates import classify
        for engine,source in (('rpgmaker',r'こんにちは\V[1]'),('kirikiri',r'こんにちは\n'),('renpy','こんにちは[name]')):
            cfg=dict(BASE,engine=engine,_kirikiri_dialect='kagex_psb')
            item=dict(source=source,text=source,status='accepted')
            source_row=row('a',source)
            if engine=='kirikiri':source_row['dialect']='kagex_psb'
            result=classify(source_row,item,cfg,{})
            self.assertNotEqual(result.get('excluded'),'path')

    def test_unresolved_korean_draft_can_render_but_is_not_automatically_complete(self):
        for engine in ('renpy','kirikiri','rpgmaker'):
            cfg=dict(BASE,engine=engine)
            item=dict(source='こんにちは',text='안녕日本語',status='partial_translation')
            self.assertIn('untranslated Japanese text',translation.validate_entry(item['source'],item,cfg))
            self.assertEqual(translation.validate_entry(item['source'],item,cfg,allow_partial=True),[])
        item=dict(source='こんにちは{w}',text='안녕日本語',status='partial_translation')
        self.assertIn('markup mismatch',translation.validate_entry(item['source'],item,dict(BASE,engine='renpy'),allow_partial=True))

    def test_script_cleanup_clears_previous_partial_markers_without_request(self):
        for engine in ('renpy','kirikiri','rpgmaker'):
            project,cfg,path=self.project(engine,[row('a')],{'a':('안녕っッｯ','partial_translation')})
            item=json.loads(path.read_text(encoding='utf-8'))
            item.update(_japanese_residual_language=True,_rpg_residual_language=engine=='rpgmaker')
            path.write_text(json.dumps(item,ensure_ascii=False)+'\n',encoding='utf-8')
            before=path.read_bytes()
            with patch('translation.request',side_effect=AssertionError('script cleanup needs no model')),redirect_stdout(io.StringIO()):
                translation.translate(project,cfg)
            found=translation.cache(project,cfg)['a']
            self.assertFalse(found.get('_japanese_residual_language'))
            self.assertFalse(found.get('_rpg_residual_language'))
            self.assertEqual(found['text'],'안녕')
            self.assertEqual(path.read_bytes(),before)

if __name__ == '__main__': unittest.main()
