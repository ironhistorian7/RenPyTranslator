import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import unity_project as unity
from engine import save_json


class UnityProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root/'game'; (self.source/'Game_Data').mkdir(parents=True)
        (self.source/'Game_Data/resources.assets').write_bytes(b'serialized original')
        self.project = self.root/'project'; (self.project/'data').mkdir(parents=True)
        save_json(self.project/'replacements.json', {'replacements':{}})
        self.cfg = {'source':str(self.source),'source_language':'english','language':'korean','engine':'unity'}
        self.en={'file':'Game_Data/resources.assets','path_id':1,'name':'en','resource_path':'lang/en/ui'}
        self.ja={'file':'Game_Data/resources.assets','path_id':2,'name':'ja','resource_path':'lang/ja/ui'}
        self.inventory={'data_dir':'Game_Data','groups':[{'group':'ui','languages':{'en':[self.en],'ja':[self.ja]}}], 'exclusions':[]}
        self.payloads={1:b'Hello = Welcome\nExit = Quit\n',2:'Hello = ようこそ\nExit = 終了\n'.encode()}
        self.patches=[patch('unity_assets.inspect_unity',return_value=self.inventory),
                      patch('unity_assets.capture_assets',side_effect=lambda root,assets:[self.payloads[a['path_id']] for a in assets])]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def test_prepare_deterministic_and_preserves_game(self):
        unity.prepare(self.project,self.cfg)
        rows=json.loads((self.project/'data/catalog.json').read_text(encoding='utf-8'))
        self.assertEqual([r['source'] for r in rows],['Welcome','Quit'])
        self.assertEqual((self.source/'Game_Data/resources.assets').read_bytes(),b'serialized original')
        with self.assertRaisesRegex(ValueError,'덮어쓰지'):
            unity.prepare(self.project,self.cfg)

    def test_existing_english_catalog_adds_verified_base_without_replacing_ui_or_cache(self):
        unity.prepare(self.project,self.cfg)
        catalog = self.project/'data/catalog.json'
        previous = json.loads(catalog.read_text(encoding='utf-8'))
        save_json(self.project/'data/catalog-format.json',{'engine':'unity','version':1})
        cache = self.project/'data/translations.jsonl'
        cache.write_bytes(b'old translation cache\n')
        paths = {name:{'file':self.en['file'],'path_id':number,'name':name,'resource_path':name}
                 for name,number in (('dialogue/entry',3),('localization/ja/dialogue/entry',4),
                                     ('localization/zh/dialogue/entry',5),('dialogue/foreign',6))}
        self.payloads.update({3:b'N You are going to the store with me in the morning.\nEND\n',
                              4:'N お店へ行きましょう。\nEND\n'.encode(),
                              5:'N 繁体测试。\nEND\n'.encode(),
                              6:'N 日本語の基本資料です。\nEND\n'.encode()})
        variants = {'ja':[paths['localization/ja/dialogue/entry']],
                    'zh':[paths['localization/zh/dialogue/entry']]}
        self.inventory['groups'] += [
            {'group':'dialogue/entry','reference':paths['dialogue/entry'],'languages':variants},
            {'group':'dialogue/foreign','reference':paths['dialogue/foreign'],'languages':variants},
        ]
        unity.upgrade_english_catalog(self.project,self.cfg)
        updated = json.loads(catalog.read_text(encoding='utf-8'))
        self.assertEqual(updated[:-1],previous)
        self.assertEqual((updated[-1]['source_file'],updated[-1]['kind']),('dialogue/entry','dialogue'))
        self.assertEqual(cache.read_bytes(), b'old translation cache\n')
        manifest=unity._verified(self.project)
        self.assertEqual(sum(bool(i['base_locale']) for i in manifest['assets']),1)
        before=catalog.read_bytes()
        unity.upgrade_english_catalog(self.project,self.cfg)
        self.assertEqual(catalog.read_bytes(),before)

    def test_render_checks_before_packaging_and_rejects_noop(self):
        unity.prepare(self.project,self.cfg)
        rows=json.loads((self.project/'data/catalog.json').read_text(encoding='utf-8'))
        known={r['id']:{'text':r['source'],'status':'manual_translation'} for r in rows}
        with self.assertRaisesRegex(ValueError,'변경된 Unity'):
            unity.render(self.project,self.cfg,rows=rows,known=known)
        known[rows[0]['id']]['text']='환영합니다'
        def repack(src,dst,changes): Path(dst).write_bytes(b'patch')
        with patch('unity_assets.repack_asset',side_effect=ValueError('bad structure')):
            with self.assertRaisesRegex(ValueError,'bad structure'):
                unity.render(self.project,self.cfg,rows=rows,known=known)
        self.assertFalse((self.project/'output').exists())
        with patch('unity_assets.repack_asset',side_effect=repack), patch('unity_assets.verify_asset',return_value=True):
            report=unity.render(self.project,self.cfg,rows=rows,known=known)
        self.assertEqual(report['status'],'passed')
        self.assertTrue(Path(report['archive']).is_file())
        self.assertEqual((self.source/'Game_Data/resources.assets').read_bytes(),b'serialized original')

    def test_invalid_values_keep_only_their_own_original(self):
        unity.prepare(self.project,self.cfg)
        rows=json.loads((self.project/'data/catalog.json').read_text(encoding='utf-8'))
        known={rows[0]['id']:{'text':'잘못된 | 내용','status':'translated'},
               rows[1]['id']:{'text':'종료','status':'translated'}}
        rejected=[]
        changes=unity._replacements(self.project,self.cfg,unity._verified(self.project),rows,known,rejected=rejected)
        self.assertEqual(rejected,[rows[0]['id']])
        self.assertIn('Welcome',changes['Game_Data/resources.assets'][1].decode())
        known[rows[0]['id']]['status']='manual_translation'
        rejected, manual = [], []
        actual=unity._replacements(self.project,self.cfg,unity._verified(self.project),rows,known,
                                   rejected=rejected,rejected_manual=manual)
        self.assertEqual(actual,changes)
        self.assertEqual(rejected,[])
        self.assertEqual(manual,[rows[0]['id']])
        self.assertEqual(known[rows[0]['id']]['text'],'잘못된 | 내용')

    def test_valid_manual_values_export_with_display_numbers_and_pipe(self):
        self.payloads[1]=b'Price = Eight hundred coins\nOptions = Yes | No\n'
        unity.prepare(self.project,self.cfg)
        rows=json.loads((self.project/'data/catalog.json').read_text(encoding='utf-8'))
        known={row['id']:{'text':value,'status':'manual_translation'} for row,value in
               zip(rows,('800코인 (변경 가능)','예 | 아니요'))}
        rejected, manual = [], []
        changes=unity._replacements(self.project,self.cfg,unity._verified(self.project),rows,known,
                                    rejected=rejected,rejected_manual=manual)
        self.assertEqual((rejected,manual),([],[]))
        self.assertEqual(changes['Game_Data/resources.assets'][1].decode(),
                         'Price = 800코인 (변경 가능)\nOptions = 예 | 아니요\n')

    def test_untouched_value_does_not_block_a_neighboring_change(self):
        self.payloads[1]=b'Hello = Welcome | Home\nExit = Quit\n'
        unity.prepare(self.project,self.cfg)
        rows=json.loads((self.project/'data/catalog.json').read_text(encoding='utf-8'))
        known={rows[0]['id']:{'text':rows[0]['source'],'status':'source_fallback'},
               rows[1]['id']:{'text':'종료','status':'translated'}}
        rejected=[]
        changes=unity._replacements(self.project,self.cfg,unity._verified(self.project),rows,known,rejected=rejected)
        self.assertEqual(rejected,[])
        self.assertEqual(changes['Game_Data/resources.assets'][1].decode(),
                         'Hello = Welcome | Home\nExit = 종료\n')

    def test_snapshot_hash_and_missing_cache_are_enforced(self):
        unity.prepare(self.project,self.cfg)
        rows=json.loads((self.project/'data/catalog.json').read_text(encoding='utf-8'))
        with self.assertRaisesRegex(ValueError,'번역하지 않은'):
            unity.render(self.project,self.cfg,rows=rows,known={})
        (self.project/'data/unity-source/Game_Data/resources.assets').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError,'변경'):
            unity._verified(self.project)


if __name__=='__main__': unittest.main()
