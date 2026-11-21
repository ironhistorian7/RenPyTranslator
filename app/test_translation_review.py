import json
from pathlib import Path
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


if __name__=='__main__':unittest.main()
