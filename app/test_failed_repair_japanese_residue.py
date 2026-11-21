"""Offline regressions for previously accepted Japanese residue drafts."""
import contextlib
import io
import json
import unittest
from unittest.mock import patch

from test_failure_discovery import FailureDiscoveryTests


class JapaneseResidualRepairTests(FailureDiscoveryTests):
    def run_draft(self, text, reply, engine='renpy'):
        self.cfg.update(source_language='japanese', engine=engine)
        row=self.row('draft','これは例文です。')
        entry=self.entry(row,text=text,_japanese_residual_language=True)
        raw=self.fixture([row],[entry],index=[])
        seen=[]
        def fake(batch,context,cfg,note):
            seen.append((cfg,note))
            if isinstance(reply,Exception):raise reply
            return [dict(id=row['id'],text=reply)],{}
        self.translate.side_effect=fake
        with patch('failed_repair.cache',return_value={row['id']:entry}), \
             contextlib.redirect_stdout(io.StringIO()):
            selected,report=self.repair()
        self.expect_raw(raw)
        return selected,report,seen

    def test_minor_uses_full_source_and_saves_success(self):
        original='이 문장은 충분히 긴 한국어 문장이며 한 글자 カ 남아 있습니다.'
        selected,report,seen=self.run_draft(original,'이 문장은 충분히 긴 한국어 문장이며 한 글자만 남아 있습니다.')
        self.assertEqual(selected,{'draft'})
        self.assertEqual(report['model_calls'],1)
        self.assertEqual(seen[0][0]['_japanese_retry_kind'],'minor')
        self.assertNotIn('_japanese_validated_draft',seen[0][0])
        self.assertEqual(seen[0][0]['_japanese_retry_attempt'],1)
        self.assertEqual(seen[0][1],'')
        saved=json.loads((self.root/'data/recovered-translations.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['draft']['before'],original)

    def test_mixed_failure_keeps_original_and_sends_no_draft(self):
        selected,report,seen=self.run_draft('한국어와 日本語 が残る',ValueError('failed'))
        self.assertEqual(selected,set())
        self.assertEqual(report['remaining'],1)
        self.assertEqual(len(seen),2)
        self.assertEqual(seen[0][0]['_japanese_retry_kind'],'mixed')
        self.assertNotIn('_japanese_validated_draft',seen[0][0])
        self.assertEqual(seen[0][1],'')
        self.assertFalse((self.root/'data/recovered-translations.json').exists())

    def test_small_tsu_cleanup_needs_no_model(self):
        selected,report,seen=self.run_draft('긴 한국어 문장입니다っ','unused')
        self.assertEqual(selected,{'draft'})
        self.assertEqual(report['model_calls'],0)
        self.assertEqual(seen,[])
        saved=json.loads((self.root/'data/recovered-translations.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['draft']['text'],'긴 한국어 문장입니다')

    def test_residual_model_output_is_not_saved(self):
        selected,report,seen=self.run_draft('한국어와 日本語 が残る','한국어와 日本語 が残る')
        self.assertEqual(selected,set())
        self.assertEqual(report['remaining'],1)
        self.assertEqual(len(seen),2)
        self.assertEqual([call[0]['_japanese_retry_attempt'] for call in seen],[1,2])
        self.assertTrue(all('_japanese_validated_draft' not in call[0] for call in seen))
        self.assertFalse((self.root/'data/recovered-translations.json').exists())

    def test_marked_manual_and_english_never_become_residual_candidates(self):
        from failed_repair import residual_candidate
        item={'status':'manual_translation','_japanese_residual_language':True}
        self.assertFalse(residual_candidate(item,{'source_language':'japanese'}, {'excluded':'manual_translation'}))
        self.assertFalse(residual_candidate(item,{'source_language':'english'}, {}))
        item['status']='name_review'
        self.assertFalse(residual_candidate(item,{'source_language':'japanese'}, {}))


if __name__=='__main__':
    unittest.main()
