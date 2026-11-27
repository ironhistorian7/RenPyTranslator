import json
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch
import translation_review as review


class TranslationReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        (self.project/'data').mkdir()
        self.addCleanup(self.temp.cleanup)
        self.rows = [{'id':str(i),'source':'source '+str(i)} for i in range(6)]
        self.known = {'0':{'status':'source_fallback','text':'source 0'},
                      '1':{'status':'partial_translation','text':'partial'},
                      '2':{'_rpg_residual_language':True,'text':'mixed'},
                      '3':{'status':'ui_original','text':'source 3'},
                      '4':{'status':'manual_translation','text':''},
                      '5':{'text':'source 5'}}
        self.review = review.build_review(self.rows,self.known)
        self.cfg = {'model':'test'}

    def test_only_actual_failure_statuses(self):
        self.assertEqual([e['id'] for e in self.review['entries']], ['0','1','2'])

    def test_saved_unchanged_choice_also_appears(self):
        rows=[dict(id='old',source='Open the door',usage='choice')]
        pending=review.build_review(rows,{'old':dict(text='Open the door')},{'source_language':'english'})
        self.assertEqual([entry['id'] for entry in pending['entries']],['old'])

    def test_arbitrary_text_and_skip_append_without_losing_existing(self):
        data=self.project/'data'
        original='{"old":"kept"}\n'
        (data/'translations.jsonl').write_text(original,encoding='utf-8')
        (data/'failed-items.json').write_text(json.dumps([{'row':{'id':str(i)}} for i in range(3)]),encoding='utf-8')
        reply={'fingerprint':self.review['fingerprint'],'entries':[
            {'id':'0','apply':True,'text':''}, {'id':'1','apply':True,'text':'<x>\n  日本語  '},
            {'id':'2','apply':False,'text':'ignored'}]}
        saved=review.apply_review(self.project,self.cfg,self.review,reply)
        self.assertEqual([x['text'] for x in saved], ['', '<x>\n  日本語  '])
        text=(data/'translations.jsonl').read_text(encoding='utf-8')
        self.assertTrue(text.startswith(original))
        self.assertEqual(json.loads(text.splitlines()[-1])['text'],'<x>\n  日本語  ')
        self.assertEqual(json.loads((data/'failed-items.json').read_text(encoding='utf-8')), [{'row':{'id':'2'}}])
        self.assertEqual(saved[0]['status'],'manual_translation')
        self.assertEqual(saved[0]['source'],'source 0')

    def test_unity_saved_invalid_manual_can_be_fixed_without_overwriting_history(self):
        cfg = dict(self.cfg,engine='unity')
        rows = [dict(id='item',source='Due {0} | Later',unity_format='key_value',
                     source_file='localization/en/menu',source_line=25)]
        original = {'id':'item','source':rows[0]['source'],'text':'이번 주',
                    'status':'manual_translation','model':'test','fingerprint':'original'}
        path = self.project/'data/translations.jsonl'
        initial = json.dumps(original,ensure_ascii=False) + '\n'
        path.write_text(initial,encoding='utf-8')
        pending = review.build_review(rows,{'item':original},cfg)
        self.assertEqual(pending['entries'][0]['current'],'이번 주')
        self.assertIn('25행',pending['entries'][0]['issue'])
        reply = {'fingerprint':pending['fingerprint'],
                 'entries':[dict(id='item',apply=True,text='이번 달 | 나중에')]}
        with self.assertRaisesRegex(ValueError,'변수'):
            review.apply_review(self.project,cfg,pending,reply)
        self.assertEqual(path.read_text(encoding='utf-8'),initial)
        reply['entries'][0]['text']='{0} (800원) | 나중에'
        saved=review.apply_review(self.project,cfg,pending,reply)
        self.assertTrue(path.read_text(encoding='utf-8').startswith(initial))
        self.assertEqual(review.build_review(rows,{'item':saved[0]},cfg)['entries'],[])

    def test_unity_numeric_review_keeps_draft_and_respects_apply_and_skip(self):
        cfg = dict(self.cfg, engine='unity')
        rows = [dict(id='a', source='Price: 100', source_file='menu', source_line=4),
                dict(id='b', source='Count: 2')]
        known = {'a': dict(status='translated', text='가격: 200', _unity_numeric_review=True),
                 'b': dict(status='translated', text='개수: 3', _unity_numeric_review=True)}
        pending = review.build_review(rows, known, cfg)
        self.assertEqual([e['id'] for e in pending['entries']], ['a', 'b'])
        self.assertEqual(pending['entries'][0]['current'], '가격: 200')
        self.assertIn('숫자', pending['entries'][0]['issue'])
        self.assertIn('4행', pending['entries'][0]['issue'])
        self.assertEqual(review.build_review(rows, known, dict(cfg, engine='renpy'))['entries'], [])
        reply = {'fingerprint': pending['fingerprint'], 'entries': [
            dict(id='a', apply=True, text='가격: 100'),
            dict(id='b', apply=False, text='')]}
        saved = review.apply_review(self.project, cfg, pending, reply)
        self.assertEqual([x['id'] for x in saved], ['a'])
        known['a'] = saved[0]
        remaining = review.build_review(rows, known, cfg)['entries']
        self.assertEqual([e['id'] for e in remaining], ['b'])
        self.assertEqual(remaining[0]['current'], '개수: 3')

    def test_reject_unknown_duplicate_and_wrong_types_before_write(self):
        base={'fingerprint':self.review['fingerprint'],'entries':[{'id':str(i),'apply':True,'text':''} for i in range(3)]}
        for entries in (base['entries']+[base['entries'][0]],base['entries'][:2],
                        [dict(base['entries'][0],id='9')]+base['entries'][1:],
                        [dict(base['entries'][0],id=[])]+base['entries'][1:],
                        [dict(base['entries'][0],text=None)]+base['entries'][1:]):
            with self.subTest(entries=entries):
                with self.assertRaises(ValueError):review.apply_review(self.project,self.cfg,self.review,dict(base,entries=entries))
        self.assertFalse((self.project/'data/translations.jsonl').exists())

    def test_console_raw_blank_literal_and_json(self):
        entries = [{'id':str(i), 'source':'s', 'current':'c'} for i in range(5)]
        request = {'fingerprint':'f', 'entries':entries}
        answers = ['  그대로 <tag>  ', '', '/literal', '/skip', '/json', '"첫 줄\\n둘째 줄"', '/skip']
        with patch('translation_review.sys.stdin.isatty', return_value=True), patch('builtins.input', side_effect=answers):
            result = review._console_reply(request)['entries']
        self.assertEqual([item['text'] for item in result], ['  그대로 <tag>  ', '', '/skip', '첫 줄\n둘째 줄', ''])
        self.assertEqual([item['apply'] for item in result], [True, True, True, True, False])

    def test_console_cancel_and_json_type_validation(self):
        request = {'fingerprint':'f', 'entries':[{'id':'0','source':'s','current':'c'}]}
        with patch('translation_review.sys.stdin.isatty', return_value=True):
            with patch('builtins.input', side_effect=['/cancel']):
                with self.assertRaises(KeyboardInterrupt): review._console_reply(request)
            with patch('builtins.input', side_effect=['/json', '42', '/literal', '/cancel']):
                self.assertEqual(review._console_reply(request)['entries'][0]['text'], '/cancel')

    def test_noninteractive_fails_explicitly(self):
        with patch('translation_review.sys.stdin.isatty',return_value=False):
            with self.assertRaisesRegex(ValueError,'GUI'):review._console_reply(self.review)

    def test_sentence_selection_after_manual_review_reaches_rendered_patch(self):
        from translation import cache, catalog, fingerprint, render, rendered_text

        cfg = dict(self.cfg, engine='renpy', language='korean', source_language='english')
        data = self.project / 'data'
        template = data / 'templates' / 'chapter.rpy'
        template.parent.mkdir()
        source = 'Wait here for [name].'
        speeches = [('room_00000001', 'mira', source), ('room_00000002', 'mira', source),
                    ('room_00000003', 'mira', source), ('room_00000004', 'noah', source),
                    ('room_00000005', 'mira', 'Wait here for [name]. It is late.'),
                    ('room_00000006', 'mira', 'Wait here for [name]. It is late.'),
                    ('exit_00000007', 'mira', source)]
        template.write_text(''.join(f'translate korean {block}:\n    # {speaker} "{line}"\n'
                                    f'    {speaker} ""\n\n' for block, speaker, line in speeches), encoding='utf-8')
        rows = catalog(self.project, cfg)
        texts = [source, '여기서 [name]을 기다려.', '여기에서 [name]을 기다리세요.',
                 '여기서 [name]을 기다리십시오.', '[name]을 기다려. 시간이 늦었어.',
                 '[name]을 기다리세요. 늦었어요.', '여기서 [name]을 기다려요.']
        records = [dict(id=row['id'], source=row['source'], text=text, fingerprint=fingerprint(cfg),
                        model='test', status='source_fallback' if i == 0 else 'translated', context_scope='room')
                   for i, (row, text) in enumerate(zip(rows, texts))]
        cache_file = data / 'translations.jsonl'
        initial = ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in records).encode('utf-8')
        cache_file.write_bytes(initial)
        reply_path = data / 'reply.json'
        pending = []
        manual = '[name]을 여기서 기다려 줘.'

        def reply(request):
            pending.append(request)
            key = request['fingerprint']
            if 'entries' in request:
                self.assertEqual([entry['id'] for entry in request['entries']], [rows[0]['id']])
                return dict(fingerprint=key, entries=[dict(id=rows[0]['id'], apply=True, text=manual)])
            self.assertEqual(cache(self.project, cfg)[rows[0]['id']]['text'], manual)
            self.assertEqual(len(request['groups']), 1)
            group = request['groups'][0]
            self.assertEqual(group['source'], source)
            self.assertEqual([item['text'] for item in group['variants']], [manual, texts[1], texts[2]])
            self.assertEqual(group['variants'][0]['editable_count'], 0)
            if len(pending) == 2:
                return dict(kind='sentence', fingerprint=key,
                            selections=[dict(id=group['id'], text='여기서 기다려.')])
            self.assertEqual(key, pending[1]['fingerprint'])
            self.assertIn('형식', request['error'])
            return dict(kind='sentence', fingerprint=key,
                        selections=[dict(id=group['id'], variant=group['variants'][1]['id'])])

        def submit(event, review):
            self.assertEqual(event, 'translation_review')
            payload = reply(review)
            reply_path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')

        with patch.dict(os.environ, RPT_TRANSLATION_REVIEW_FILE=str(reply_path), RPT_DESKTOP_EVENTS='1'), \
                patch('translation_review.emit', side_effect=submit):
            changes = review.run(self.project, cfg, review_sentences=True)

        self.assertEqual([row['id'] for row in changes], [rows[0]['id'], rows[2]['id']])
        self.assertFalse(reply_path.exists())
        self.assertTrue(cache_file.read_bytes().startswith(initial))
        self.assertEqual(len(cache_file.read_bytes().splitlines()), len(rows) + 2)
        saved = cache(self.project, cfg)
        self.assertEqual([saved[row['id']]['text'] for row in rows], [manual, texts[1], texts[1], *texts[3:]])
        render(self.project, cfg)
        translated = (self.project/'staging/game/tl/korean/chapter.rpy').read_text(encoding='utf-8').splitlines()
        self.assertEqual(rendered_text(translated[rows[0]['line']], rows[0]), manual)
        self.assertEqual(rendered_text(translated[rows[2]['line']], rows[2]), texts[1])


if __name__=='__main__':unittest.main()
