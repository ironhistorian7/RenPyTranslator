"""Japanese-only integration: preserved cache, targeted resume, no live model."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout, nullcontext
from pathlib import Path
from unittest.mock import patch

import translation
from engine import save_json
from japanese_resume import pending
from kirikiri_translation import row_config
from translation_review import build_review

BASE = dict(engine='kirikiri', source_language='japanese', language='korean',
            model='synthetic-hy', endpoint='http://unused.invalid', num_ctx=16384,
            parallel=2, batch_size=8, context_txt_enabled=False)


def row(uid, source):
    return dict(id=uid, source=source, kind='dialogue', usage='dialogue',
                dialect='kagex_psb', file='synthetic.scn', block='one')


class JapaneseCacheChangesTests(unittest.TestCase):
    def project(self, rows, values, **options):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        project = Path(tmp.name); cfg = dict(BASE, **options)
        save_json(project/'data/catalog.json', rows)
        records = [dict(id=r['id'], source=r['source'], model=cfg['model'],
                        fingerprint=translation.fingerprint(cfg), **values[r['id']]) for r in rows]
        path = project/'data/translations.jsonl'
        path.write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in records), encoding='utf-8')
        return project, cfg, path

    def test_new_numbers_are_scripted_without_model_for_all_engines(self):
        for engine in ('renpy', 'rpgmaker', 'kirikiri'):
            cfg = dict(BASE, engine=engine)
            with self.subTest(engine=engine), patch('translation.request', side_effect=AssertionError('model called')):
                result, metrics = translation.translate_batch([row('n', '「さんびゃく、二十」')], {}, cfg)
            self.assertEqual(result[0]['text'], '「300、20」')
            self.assertEqual(metrics['tokens'], 0)

    def test_existing_wrong_and_rejected_numbers_recover_without_cache_writes(self):
        rows = [row('bad', '「よんひゃく、三十」'), row('correct', '「二百、三」')]
        project, cfg, path = self.project(rows, {
            'bad': dict(text='「よんひゃく、三十」', status='source_fallback', error="no Korean translation; output='40,30'"),
            'correct': dict(text='“200, 3”', status='accepted')})
        before = path.read_bytes()
        with patch('translation.request', side_effect=AssertionError('model called')):
            known = translation.cache(project, cfg)
        self.assertEqual(known['bad']['text'], '「400、30」')
        self.assertEqual(known['bad']['status'], 'failure_recovered')
        self.assertEqual(known['correct']['text'], '“200, 3”')
        self.assertEqual(build_review(rows, known, cfg)['entries'], [])
        self.assertEqual(path.read_bytes(), before)

    def test_old_linebreak_reply_recovers_without_gpu_or_cache_replacement(self):
        rows = [row('line', 'こんにちは\n世界。')]
        project, cfg, path = self.project(rows, {'line': dict(text=rows[0]['source'], status='source_fallback', error='KiriKiri token mismatch')})
        from kirikiri_tokens import protect
        source=protect(rows[0]['source'],row_config(cfg,rows[0]))[0]
        trace = [dict(event='request', request_id='r', items=[dict(id='line')], request=dict(model=cfg['model'],messages=[dict(role='user',content='\n[Source Text]\n[0] '+source)])),
                 dict(event='response', request_id='r', response=dict(message=dict(content='[0] 안녕하세요 반가워요.'), done_reason='stop'))]
        (project/'data/translation-requests.jsonl').write_text(''.join(json.dumps(x, ensure_ascii=False)+'\n' for x in trace), encoding='utf-8')
        before = path.read_bytes()
        known = translation.cache(project, cfg)
        self.assertEqual(known['line']['status'], 'failure_recovered')
        self.assertEqual(known['line']['text'].replace('\n', ''), '안녕하세요 반가워요.')
        self.assertEqual(known['line']['text'].count('\n'), 1)
        self.assertFalse(pending(known['line']))
        self.assertEqual(path.read_bytes(), before)

    def test_missing_functional_control_is_not_fabricated_from_trace(self):
        rows = [row('line', 'こんにちは\n$actor;。')]
        project, cfg, path = self.project(rows, {'line': dict(text=rows[0]['source'], status='source_fallback', error='KiriKiri token mismatch')})
        from kirikiri_tokens import protect
        source=protect(rows[0]['source'],row_config(cfg,rows[0]))[0]
        trace = [dict(event='request', request_id='r', items=[dict(id='line')], request=dict(model=cfg['model'],messages=[dict(role='user',content='\n[Source Text]\n[0] '+source)])),
                 dict(event='response', request_id='r', response=dict(message=dict(content='[0] 안녕하세요.'), done_reason='stop'))]
        (project/'data/translation-requests.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in trace), encoding='utf-8')
        self.assertEqual(translation.cache(project, cfg)['line']['status'], 'source_fallback')

    def test_saved_segment_reply_is_not_used_as_whole_translation(self):
        rows = [row('line', '最初の文。\n次の文。')]
        project, cfg, path = self.project(rows, {'line': dict(text=rows[0]['source'], status='source_fallback', error='KiriKiri token mismatch')})
        trace = [dict(event='request', request_id='segment', items=[dict(id='line')],
                      request=dict(model=cfg['model'], messages=[dict(role='user', content='\n[Source Text]\n[0] 最初の文。')])),
                 dict(event='response', request_id='segment', response=dict(message=dict(content='[0] 첫 문장.'), done_reason='stop'))]
        (project/'data/translation-requests.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in trace), encoding='utf-8')
        self.assertEqual(translation.cache(project, cfg)['line']['status'], 'source_fallback')

    def test_ruby_reading_reaches_both_model_contracts_and_is_not_restored(self):
        r = row('ruby', '[ことり,1]小鳥がいる。')
        for hy in (True, False):
            content = '[0] 작은 새가 있어요.' if hy else json.dumps({'0': '작은 새가 있어요.'}, ensure_ascii=False)
            with self.subTest(hy=hy), patch('hy_backend.is_hy', return_value=hy), \
                 patch('translation.request', return_value=dict(message=dict(content=content), done_reason='stop')) as request:
                result, _ = translation.translate_batch([r], {}, BASE)
            payload = request.call_args.args[2]
            prompt = json.dumps(payload, ensure_ascii=False)
            self.assertIn('ことり', prompt)
            self.assertIn('ruby_readings', prompt)
            self.assertNotIn('[ことり,1]', prompt)
            self.assertEqual(result[0]['text'], '작은 새가 있어요.')
            self.assertEqual(result[0]['input_format'], 'kirikiri_reading_v2')

    def test_only_legacy_ruby_resumes_and_next_resume_has_no_request(self):
        rows = [row('ruby', '[ことり,1]小鳥がいる。'), row('good', 'こんにちは。'), row('manual', '[ことり,1]小鳥だ。')]
        project, cfg, path = self.project(rows, {
            'ruby': dict(text='[ことり,1]작은 새가 있어요.', status='accepted'),
            'good': dict(text='안녕하세요.', status='accepted'),
            'manual': dict(text='[ことり,1]사용자 표기', status='manual_translation')})
        before = path.read_bytes()
        known = translation.cache(project, cfg)
        self.assertTrue(known['ruby']['_japanese_ruby_pending'])
        self.assertEqual(known['ruby']['text'], '작은 새가 있어요.')
        self.assertEqual(known['manual']['text'], '[ことり,1]사용자 표기')
        calls = []
        def translate(batch, context, settings, note=''):
            calls.extend(r['id'] for r in batch)
            return [dict(id=r['id'], source=r['source'], text='작은 새가 있어요.', model=cfg['model'], fingerprint=translation.fingerprint(cfg)) for r in batch], dict(seconds=0, items=len(batch))
        with redirect_stdout(io.StringIO()), patch('runtime_recovery.run', side_effect=lambda p,c,fn,count: fn(c)), \
             patch('translation.translate_batch', side_effect=translate), patch('source_context.SourceContext.context', return_value={}):
            translation.translate(project, cfg)
            translation.translate(project, cfg)
        self.assertEqual(calls, ['ruby'])
        self.assertTrue(path.read_bytes().startswith(before))

    def test_failed_ruby_update_keeps_korean_draft_for_final_review(self):
        rows = [row('ruby', '[ことり,1]小鳥がいる。')]
        project, cfg, path = self.project(rows, {'ruby': dict(text='[ことり,1]작은 새가 있어요.', status='accepted')})
        response = dict(message=dict(content='[0] 日本語だけ'), done_reason='stop')
        with redirect_stdout(io.StringIO()), patch('runtime_recovery.run', side_effect=lambda p,c,fn,count: fn(c)), \
             patch('hy_backend.is_hy', return_value=True), patch('translation.request', return_value=response), \
             patch('source_context.SourceContext.context', return_value={}):
            translation.translate(project, cfg)
        known = translation.cache(project, cfg)
        self.assertEqual(known['ruby']['text'], '작은 새가 있어요.')
        self.assertEqual(known['ruby']['status'], 'partial_translation')
        self.assertTrue(known['ruby']['_japanese_ruby_pending'])
        self.assertEqual([r['id'] for r in build_review(rows, known, cfg)['entries']], ['ruby'])

    def test_english_ruby_and_failed_number_cache_stay_byte_identical(self):
        rows = [row('ruby', '[ことり,1]小鳥'), row('number', '「二十」')]
        values = {'ruby': dict(text='[ことり,1]bird', status='accepted'),
                  'number': dict(text='「二十」', status='source_fallback', error='no Korean translation')}
        project, cfg, path = self.project(rows, values, source_language='english')
        before = path.read_bytes(); known = translation.cache(project, cfg)
        self.assertFalse(known['ruby'].get('_japanese_ruby_pending'))
        self.assertEqual(known['ruby']['text'], values['ruby']['text'])
        self.assertEqual(known['number']['text'], values['number']['text'])
        self.assertEqual(path.read_bytes(), before)

    def test_actual_retry_requests_keep_readings_and_change_second_instruction(self):
        r = row('ruby', '[ことり,1]小鳥は鳴いた。')
        for hy in (True, False):
            requests = []
            for attempt in (1, 2):
                cfg = dict(BASE, _item_retry_attempt=attempt, _japanese_retry_kind='minor',
                           _japanese_retry_attempt=attempt)
                reply = dict(message=dict(content=('[0] ' if hy and attempt == 1 else '')+'작은 새가 울었어요.'), done_reason='stop')
                with patch('hy_backend.is_hy', return_value=hy), patch('translation.request', return_value=reply) as request:
                    translation.translate_batch([r], {}, cfg)
                requests.append(json.dumps(request.call_args.args[2]['messages'], ensure_ascii=False))
            self.assertNotEqual(requests[0], requests[1])
            for prompt in requests:
                self.assertIn('ことり', prompt)
                self.assertIn('小鳥は鳴いた。', prompt)
                self.assertIn('말버릇', prompt)

    def test_lone_particle_and_confirmed_numeric_name_are_not_scripted(self):
        from japanese_cache_repair import numeric_candidate
        self.assertIsNone(numeric_candidate('「に」', BASE))
        self.assertIsNone(numeric_candidate('一', dict(BASE, _usage='speaker_label')))
        self.assertIsNone(numeric_candidate('一', dict(BASE, _person_names={'一':'하지메'})))

    def test_repair_overlay_clears_ruby_migration_after_success(self):
        from kirikiri_ruby import REVISION
        rows = [row('ruby', '[ことり,1]小鳥がいる。')]
        project, cfg, path = self.project(rows, {'ruby': dict(text='작은 새가 있어요.', status='partial_translation',
                _japanese_ruby_pending=True, error='untranslated Japanese text')})
        save_json(project/'data/recovered-translations.json', {'ruby':dict(source=rows[0]['source'],
            before='작은 새가 있어요.',text='작은 새가 있네요.',method='retranslated',ruby_revision=REVISION)})
        before=path.read_bytes();known=translation.cache(project,cfg)
        self.assertEqual(known['ruby']['text'],'작은 새가 있네요.')
        self.assertFalse(pending(known['ruby']))
        self.assertEqual(build_review(rows,known,cfg)['entries'],[])
        self.assertEqual(path.read_bytes(),before)

    def test_failed_repair_ruby_success_anchors_raw_cache_and_skips_old_reply(self):
        import failed_repair
        rows=[row('ruby','[ことり,1]小鳥がいる。')]
        original='[ことり,1]작은 새가 있어요.'
        project,cfg,path=self.project(rows,{'ruby':dict(text=original,status='accepted')})
        save_json(project/'data/failed-items.json',[dict(row=rows[0],error="Previous validation failed; output='이전 답안'",input_format='kirikiri_protected_v1')])
        before=path.read_bytes()
        reply=dict(message=dict(content='[0] 작은 새가 있네요.'),done_reason='stop')
        with redirect_stdout(io.StringIO()),patch('hy_backend.is_hy',return_value=True), \
             patch('hy_backend.runtime',side_effect=lambda settings:nullcontext(settings)), \
             patch('model_runtime.model_session',return_value=nullcontext()), \
             patch('source_context.SourceContext.context',return_value={}), \
             patch('translation.request',return_value=reply) as request:
            changed,report=failed_repair.run(project,cfg)
        self.assertEqual(changed,{'ruby'})
        self.assertEqual(report['retranslated'],1)
        self.assertEqual(report['recovered_saved'],0)
        self.assertEqual(request.call_count,1)
        overlay=json.loads((project/'data/recovered-translations.json').read_text(encoding='utf-8'))
        self.assertEqual(overlay['ruby']['before'],original)
        known=translation.cache(project,cfg)
        self.assertEqual(known['ruby']['text'],'작은 새가 있네요.')
        self.assertFalse(pending(known['ruby']))
        self.assertEqual(path.read_bytes(),before)


if __name__ == '__main__':
    unittest.main()
