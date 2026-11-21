import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine import save_json
import translation


class SubsetTranslationTests(unittest.TestCase):
    def test_only_selected_ui_is_requested_and_old_failure_and_cache_are_retained(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder);(p/'data').mkdir()
            cfg={'engine':'kirikiri','language':'korean','source_language':'japanese',
                 'model':'mock','endpoint':'http://unused','num_ctx':16384,'parallel':2,
                 'batch_size':8,'_reuse_completed':True}
            rows=[dict(id=uid,source=source,kind='string',usage='ui_label' if uid=='ui' else 'dialogue',
                       dialect='kirikiri_tjs_ui' if uid=='ui' else 'kagex_psb',
                       file='system/Menus.tjs' if uid=='ui' else 'story.scn',block=uid)
                  for uid,source in [('saved','前文'),('failed','未完成'),('ui','設定')]]
            save_json(p/'data/catalog.json',rows)
            original=''.join(json.dumps(dict(r,text='기존 번역' if r['id']=='saved' else r['source'],
                                            model='mock',fingerprint=translation.fingerprint(cfg),
                                            status='source_fallback' if r['id']=='failed' else 'translated'),ensure_ascii=False)+'\n'
                             for r in rows[:2]).encode('utf-8')
            cachefile=p/'data/translations.jsonl';cachefile.write_bytes(original)
            requested=[]
            def fake(batch,context,active,retry_note=''):
                requested.extend(r['id'] for r in batch)
                return [dict(id=r['id'],source=r['source'],text='설정',model='mock',
                             fingerprint=translation.fingerprint(active)) for r in batch],{'seconds':0,'items':len(batch)}
            with patch('runtime_recovery.run',side_effect=lambda project,cfg,operation,saved:operation(cfg)), \
                 patch('translation.translate_batch',side_effect=fake):
                translation.translate_subset(p,cfg,['ui'])
            self.assertEqual(requested,['ui'])
            self.assertTrue(cachefile.read_bytes().startswith(original))
            failed=json.loads((p/'data/failed-items.json').read_text(encoding='utf-8'))
            self.assertEqual([item['row']['id'] for item in failed],['failed'])

    def test_unknown_ids_cannot_fall_back_to_whole_project_translation(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder);save_json(p/'data/catalog.json',[])
            with patch('translation.translate') as translate:
                with self.assertRaises(ValueError):translation.translate_subset(p,{},['missing'])
            translate.assert_not_called()


if __name__=='__main__':unittest.main()
