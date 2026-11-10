"""Synthetic marker-loss cases; no game files or model inference."""
from contextlib import nullcontext, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine import save_json
from rpgmaker_plain_retry import plan, restore, representatives
from rpgmaker_tokens import validate
from translation import translate_batch, _translate, cache, fingerprint

CFG = {'engine':'rpgmaker', 'model':'rpt-hymt2-7b:q6_k', 'source_language':'japanese',
       'language':'korean', 'num_ctx':16384, 'parallel':2, 'batch_size':8,
       'endpoint':'http://unused.invalid', 'context_txt_enabled':False}
ERROR = "Protected token mismatch: [] != [0]; output='안녕'"


class PlainRetryTests(unittest.TestCase):
    def test_only_marker_loss_uses_plain_retry_and_not_repeatedly(self):
        self.assertIsNone(plan(r'こんにちは\.', 'untranslated Japanese text', CFG))
        self.assertIsNone(plan(r'こんにちは\.', 'RPG plain retry failed: '+ERROR, CFG))
        self.assertIsNone(plan(r'こんにちは\.', 'Wrapper-stripped: RPG plain retry failed: '+ERROR, CFG))
        self.assertEqual(plan(r'こんにちは\.', ERROR, CFG)['text'], 'こんにちは')

    def test_full_rewrite_does_not_require_character_alignment(self):
        source=r'\C[2]今日は晴れ\.ですね\C[0]\!'
        retry=plan(source, ERROR, CFG)
        self.assertEqual(retry['text'], '今日は晴れですね')
        result,audit=restore(retry, '오늘은 날씨가 참 좋네요.')
        self.assertEqual(validate(source,result,CFG), [])
        self.assertTrue(any(p['approximate'] for p in audit['controls']))
        # Interior codes land at word or punctuation boundaries, not within a word.
        for p in audit['controls']:
            self.assertIn(p['target_offset'], {0,3,7,9,13,14})

    def test_names_and_numbers_are_readable_and_restored_in_korean_order(self):
        cfg=dict(CFG,_rpg_representatives={r'\N[3]':{'source':'Aster','text':'아스터'}})
        source=r'I gave \N[3] \V[7] gifts\.'
        retry=plan(source,ERROR,cfg)
        self.assertEqual(retry['text'], 'I gave 아스터 1 gifts')
        result,audit=restore(retry,'선물 1개를 아스터에게 줬어요.')
        self.assertEqual(result,r'선물 \V[7]개를 \N[3]에게 줬어요.\.')
        self.assertEqual(validate(source,result,cfg), [])
        self.assertEqual(len(audit['variables']), 2)

    def test_missing_name_default_is_john_and_value_never_replaces_engine_variable(self):
        retry=plan(r'\N[9]は帰った。',ERROR,CFG)
        self.assertEqual(retry['text'],'존は帰った。')
        self.assertEqual(restore(retry,'존은 돌아갔다.')[0],r'\N[9]은 돌아갔다.')

    def test_name_particles_are_allowed_but_common_words_are_not_names(self):
        retry=plan(r'\N[9]は帰った。',ERROR,CFG)
        self.assertEqual(restore(retry,'존에게는 할 말이 있다.')[0],r'\N[9]에게는 할 말이 있다.')
        with self.assertRaises(ValueError):restore(retry,'존재를 존경한다.')

    def test_soft_line_breaks_keep_words_complete_in_plain_input(self):
        source='お\\.は\nよう\\!'
        retry=plan(source,ERROR,CFG)
        self.assertEqual(retry['text'],'おはよう')
        result,_=restore(retry,'안녕하세요')
        self.assertEqual(validate(source,result,CFG),[])
        english=plan('Hello\\.\nthere\\!',ERROR,dict(CFG,source_language='english'))
        self.assertEqual(english['text'],'Hello there')

    def test_renpy_translation_does_not_enable_rpg_plain_retry(self):
        row=dict(id='x',source='Hello [friend]',kind='dialogue',usage='dialogue')
        cfg=dict(CFG,engine='renpy',_rpg_retry_errors={'x':ERROR})
        with patch('translation.request',return_value={'message':{'content':'[0] 안녕 <rpt000/>'}}) as request,patch('hy_backend.runtime',side_effect=lambda cfg:nullcontext(cfg)):
            result,_=translate_batch([row],{},cfg)
        self.assertEqual(result[0]['text'],'안녕 [friend]')
        self.assertIn('<rpt000/>',request.call_args.args[2]['messages'][0]['content'])

    def test_aggregated_controls_round_trip_and_unknown_codes_use_existing_retry(self):
        source=r'\C[2]\N[2]\C[0]、おはよう\.'
        retry=plan(source,ERROR,CFG)
        result,_=restore(retry,'존, 안녕.')
        self.assertEqual(validate(source,result,CFG), [])
        self.assertIn(r'\C[2]\N[2]\C[0]',result)
        for source in (r'おはよう\Plugin[3]',r'\I[3]おはよう',r'%1 おはよう',r'おはよう picture.png'):
            with self.subTest(source=source),self.assertRaises(ValueError):plan(source,ERROR,CFG)

    def test_plain_response_rejects_only_structural_losses(self):
        retry=plan(r'\N[2]、おはよう\.',ERROR,CFG)
        for value in ('',None,'안녕','존과 존, 안녕',r'존, 안녕\.', '존 <rpt000/>'):
            with self.subTest(value=value),self.assertRaises(ValueError):restore(retry,value)

    def test_prepared_actor_names_and_party_are_used_without_original_game_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp)
            snapshot=project/'data/rpg-source/www/data'
            save_json(snapshot/'Actors.json',[None,dict(id=1,name='Aster'),dict(id=2,name='Birch')])
            save_json(snapshot/'System.json',dict(partyMembers=[2,1],currencyUnit='G'))
            rows=[dict(id='name',file='www/data/Actors.json',source='Aster',location={'ptr':'[1].name'})]
            values=representatives(project,dict(CFG,rpg_base='www'),rows,{'name':{'text':'아스터'}})
            self.assertEqual(values[r'\N[1]']['text'],'아스터')
            self.assertEqual(values[r'\P[1]']['text'],'Birch')
            self.assertEqual(values[r'\P[2]']['text'],'아스터')

    def test_transport_sends_no_markers_and_logs_reconstruction(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=dict(CFG,_trace_dir=tmp,_rpg_retry_errors={'x':ERROR})
            row=dict(id='x',source=r'\PX[90]こんにちは\.',kind='dialogue',usage='dialogue')
            with patch('translation.request',return_value={'message':{'content':'[0] 안녕하세요'}}) as request,patch('hy_backend.runtime',side_effect=lambda cfg:nullcontext(cfg)):
                result,_=translate_batch([row],{},cfg)
            prompt=request.call_args.args[2]['messages'][0]['content']
            self.assertEqual(prompt.split('[Source Text]',1)[1].strip(),'[0] こんにちは')
            self.assertNotIn('RPT000',prompt);self.assertNotIn('<rpt',prompt)
            self.assertEqual(result[0]['text'],r'\PX[90]안녕하세요\.')
            events=[json.loads(line) for line in (Path(tmp)/'translation-requests.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertTrue(any(e['event']=='rpg_plain_restoration' for e in events))

    def test_plain_failure_returns_to_marked_retry_within_existing_budget(self):
        from translation import BatchTranslationError
        row=dict(id='x',source=r'こんにちは\.',kind='dialogue',usage='dialogue')
        cfg=dict(CFG,_rpg_retry_errors={'x':ERROR})
        with patch('translation.request',return_value={'message':{'content':'[0] <rpt000/>안녕'}}),patch('hy_backend.runtime',side_effect=lambda cfg:nullcontext(cfg)):
            with self.assertRaises(BatchTranslationError) as failed:translate_batch([row],{},cfg)
        self.assertIn('RPG plain retry failed:',failed.exception.failures[0]['error'])
        self.assertIsNone(plan(row['source'],failed.exception.failures[0]['error'],cfg))

    def test_pipeline_retries_only_failed_row_and_keeps_successful_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);rows=[dict(id='ok',source='晴れ',kind='dialogue',file='test',block='one'),
                             dict(id='x',source=r'こんにちは\.',kind='dialogue',file='test',block='one')]
            save_json(p/'data/catalog.json',rows)
            saved=dict(id='ok',source='晴れ',text='맑음',model=CFG['model'],fingerprint=fingerprint(CFG))
            cache_file=p/'data/translations.jsonl';raw=json.dumps(saved,ensure_ascii=False)+'\n';cache_file.write_text(raw,encoding='utf-8')
            responses=[{'message':{'content':'[0] 안녕'}},{'message':{'content':'[0] 안녕'}}]
            with redirect_stdout(io.StringIO()),patch('translation.request',side_effect=responses) as request,patch('hy_backend.runtime',side_effect=lambda cfg:nullcontext(cfg)):
                _translate(p,dict(CFG,parallel=1))
            self.assertEqual(request.call_count,2)
            first=request.call_args_list[0].args[2]['messages'][0]['content']
            last=request.call_args_list[1].args[2]['messages'][0]['content']
            self.assertIn('⟦RPT000⟧',first);self.assertNotIn('⟦RPT000⟧',last)
            self.assertTrue(cache_file.read_text(encoding='utf-8').startswith(raw))
            self.assertEqual(cache(p,CFG)['x']['text'],r'안녕\.')


if __name__=='__main__':
    unittest.main()
