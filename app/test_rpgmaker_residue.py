"""Invented partial drafts only; no game, model or network runs."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine import save_json
from rpgmaker_residue import needs_repair
from rpgmaker_protocol import correction_plan
from translation import cache,fingerprint,translate_batch,BatchTranslationError


CFG={'engine':'rpgmaker','source_language':'japanese','language':'korean','model':'synthetic',
     'num_ctx':16384,'batch_size':8,'parallel':2,'endpoint':'http://unused.invalid','context_txt_enabled':False}


class ResidueTests(unittest.TestCase):
    def test_only_visible_residue_becomes_a_repair_candidate(self):
        row={'source':'星空の物語'}
        self.assertTrue(needs_repair(row,{'text':'새로운 星空 이야기'},CFG))
        self.assertFalse(needs_repair(row,{'text':r'새로운 이야기\Font[架空]'},CFG))
        self.assertFalse(needs_repair(row,{'text':'새로운 星空 이야기','model':'reviewed override'},CFG))
        self.assertFalse(needs_repair(row,{'text':'새로운 星空 이야기'},dict(CFG,engine='renpy')))

    def test_old_partial_cache_is_retained_and_manual_replacements_are_respected(self):
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp)
            row={'id':'invented','source':'星空の物語','base_source':'星空故事',
                 'source_aliases':['星空故事','星空の物語'],'kind':'dialogue','file':'data/Map001.json'}
            save_json(project/'data/catalog.json',[row])
            original=json.dumps(dict(id=row['id'],source='星空故事',text='새로운 星空 이야기',
                                     fingerprint=fingerprint(CFG),model=CFG['model']),ensure_ascii=False)+'\n'
            path=project/'data/translations.jsonl';path.write_text(original,encoding='utf-8')
            known=cache(project,CFG)
            self.assertTrue(known[row['id']]['_rpg_residual_language'])
            self.assertEqual(known[row['id']]['text'],'새로운 星空 이야기')
            self.assertEqual(path.read_text(encoding='utf-8'),original)
            save_json(project/'replacements.json',{'replacements':{'星空':'별하늘'}})
            self.assertNotIn('_rpg_residual_language',cache(project,CFG)[row['id']])
            self.assertEqual(path.read_text(encoding='utf-8'),original)

    def test_new_hy_partial_response_is_rejected_and_uses_existing_correction_budget(self):
        row={'id':'invented','source':'星空の物語','kind':'dialogue','usage':'dialogue','file':'data/Map001.json'}
        response={'message':{'content':'[0] 새로운 星空 이야기'},'done_reason':'stop'}
        with (patch('hy_backend.is_hy',return_value=True),patch('translation.request',return_value=response),
              patch('diagnostics.report')):
            with self.assertRaises(BatchTranslationError) as caught:
                translate_batch([row],{},CFG)
        self.assertIn('untranslated Japanese text',caught.exception.failures[0]['error'])
        plan=correction_plan(row['source'],caught.exception.failures[0]['error'],CFG)
        self.assertIsNotNone(plan)


if __name__=='__main__':unittest.main()
