import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kirikiri_containers import write_xp3, DirectorySource, XP3Archive
from kirikiri_project import prepare, render, static_check
from kirikiri_ui_text_support import ensure_ui_catalog, refresh_ui_output


class UIProjectTests(unittest.TestCase):
    def test_migration_refresh_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); game=base/'game'; game.mkdir(); (game/'game.exe').touch()
            scenario=b'Hello world\n'
            menu=b'var item = new KAGMenuItem(window, "Start(&S)");\nvar internal = "dont touch";\n'
            write_xp3(game/'data.xp3', {'start.ks':scenario})
            project=base/'project'; cfg={'source':str(game), 'source_language':'english', 'language':'korean', 'show_reference':False, 'engine':'kirikiri'}
            prepare(project,cfg)
            rows=json.loads((project/'data/catalog.json').read_text(encoding='utf-8-sig'))
            known={r['id']:{'text':'translated story'} for r in rows}
            patch_path=render(project,cfg,rows=rows,known=known)
            with DirectorySource(patch_path.parent) as src:
                old=XP3Archive(src,patch_path.name).read('start.ks')
            cache_path=project/'data/translations.jsonl'; cache_path.write_bytes(b'old cache\n')
            marker=project/'data/catalog-format.json'; m=json.loads(marker.read_text(encoding='utf-8-sig')); m.pop('ui_text'); marker.write_text(json.dumps(m),encoding='utf-8')
            write_xp3(game/'patch.xp3', {'system/Menus.tjs':menu, 'sysscn/exmenuintf.tjs':b'pushButton("Apply"); var internal = 1;'})
            self.assertEqual(ensure_ui_catalog(project,cfg,dry_run=True)['added'],2)
            self.assertEqual(ensure_ui_catalog(project,cfg)['added'],2)
            self.assertTrue(ensure_ui_catalog(project,cfg)['already_current'])
            self.assertEqual(cache_path.read_bytes(),b'old cache\n')
            updated=json.loads((project/'data/catalog.json').read_text(encoding='utf-8-sig'))
            self.assertEqual(updated[:len(rows)],rows)
            ui=next(r for r in updated if r['source']=='Start'); self.assertTrue(ui['native_menu'])
            software=next(r for r in updated if r['source']=='Apply'); self.assertFalse(software['native_menu'])
            with patch('translation.cache', return_value={ui['id']:{'text':'시작'}, software['id']:{'text':'적용'}}):
                refresh_ui_output(project,cfg)
            self.assertEqual(static_check(project,cfg)['status'],'passed')
            with DirectorySource(patch_path.parent) as src:
                archive=XP3Archive(src,patch_path.name)
                self.assertEqual(archive.read('start.ks'),old)
                self.assertFalse(any(k.lower().endswith('menus.tjs') and not k.lower().endswith('exmenuintf.tjs') for k in archive.entries))
                ui_member=next(k for k in archive.entries if k.lower().endswith('exmenuintf.tjs'))
                changed=archive.read(ui_member)
                self.assertIn(b'internal = 1',changed)
                members={k:archive.read(k) for k in archive.entries}
            native=json.loads((patch_path.parent/'ui-native-menus.json').read_text(encoding='utf-8-sig'))
            self.assertEqual(native['captions']['Start(&S)'],'시작(&S)')
            members[ui_member]=changed.replace(b'internal = 1',b'internal = 2')
            write_xp3(patch_path.parent/'tampered.tmp',members)
            (patch_path.parent/'tampered.tmp').replace(patch_path)
            ledger=project/'data/kirikiri-output.json'; report=json.loads(ledger.read_text(encoding='utf-8-sig'))
            report['sha256']=hashlib.sha256(patch_path.read_bytes()).hexdigest(); ledger.write_text(json.dumps(report),encoding='utf-8')
            self.assertEqual(static_check(project,cfg)['status'],'failed')

    def test_japanese_ui_text_and_direct_font_share_one_verified_output(self):
        from test_kirikiri_font_layout import SRC,PRE,tft
        from kirikiri_font_layout import patch_direct_ui_font
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);game=base/'game';game.mkdir();(game/'game.exe').touch()
            custom=('function ui(lay) { drawTextKinsokuRect(lay, "保存", elm); '
                    'with(lay) { .drawText(0,0,text,1); } }').encode('utf8')
            write_xp3(game/'data.xp3',{'start.ks':b'Hello world\n','main/custom.tjs':custom,
                'system/MessageLayer.tjs':SRC,'system/PreRenderFontEx.tjs':PRE,'main/font.tft':tft([0x3042])})
            project=base/'project';cfg={'source':str(game),'source_language':'japanese','language':'korean','engine':'kirikiri'}
            prepare(project,cfg)
            rows=json.loads((project/'data/catalog.json').read_text(encoding='utf8'))
            known={r['id']:{'text':'저장' if r.get('source')=='保存' else '번역'} for r in rows}
            patch_path=render(project,cfg,rows=rows,known=known)
            report=json.loads((project/'data/kirikiri-output.json').read_text(encoding='utf8'))
            self.assertIn('main/custom.tjs',report['ui_direct_font'])
            self.assertEqual(static_check(project,cfg)['status'],'passed')
            with DirectorySource(patch_path.parent) as src:
                archive=XP3Archive(src,patch_path.name); inverse={v:k for k,v in report['member_map'].items()}
                actual=archive.read(inverse['main/custom.tjs']);self.assertIn(b'__rptDirectDraw(lay,',actual)
                members={k:archive.read(k) for k in archive.entries}
            from kirikiri_menu_text import render_ui_text
            expected=render_ui_text(custom,report['ui_text_edits']['main/custom.tjs'])
            self.assertEqual(actual,patch_direct_ui_font(expected,['font.tft'])[0])
            with patch('translation.cache',return_value=known):refresh_ui_output(project,cfg)
            self.assertEqual(static_check(project,cfg)['status'],'passed')
            report=json.loads((project/'data/kirikiri-output.json').read_text(encoding='utf8'))
            with DirectorySource(patch_path.parent) as src:
                archive=XP3Archive(src,patch_path.name);members={k:archive.read(k) for k in archive.entries}
            key=next(k for k,v in report['member_map'].items() if v=='main/custom.tjs')
            members[key]=members[key].replace(b'__rptDirectDraw(lay,',b'__rptDirectDraw(bad,',1)
            temp=patch_path.with_name('tampered.tmp');write_xp3(temp,members);temp.replace(patch_path)
            report['sha256']=hashlib.sha256(patch_path.read_bytes()).hexdigest()
            (project/'data/kirikiri-output.json').write_text(json.dumps(report),encoding='utf8')
            self.assertEqual(static_check(project,cfg)['status'],'failed')

    def test_prepare_includes_menu(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); game=base/'game'; game.mkdir(); (game/'game.exe').touch()
            write_xp3(game/'data.xp3', {'start.ks':b'Hello\n', 'system/Menus.tjs':b'new KAGMenuItem(window, "Start");'})
            project=base/'project'; cfg={'source':str(game), 'source_language':'english', 'language':'korean'}
            prepare(project,cfg)
            rows=json.loads((project/'data/catalog.json').read_text(encoding='utf-8-sig'))
            self.assertEqual([r['source'] for r in rows if r.get('usage')=='ui_label'],['Start'])
            self.assertTrue((project/'data/kirikiri-source/system/Menus.tjs').is_file())

    def test_upgrade_marker_one_and_native_conflict(self):
        from kirikiri_ui_text_support import native_map_bytes
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); game=base/'game'; game.mkdir(); (game/'game.exe').touch()
            menu=b'new KAGMenuItem(window, "Start(&S)");'
            write_xp3(game/'data.xp3', {'start.ks':b'Hello\n', 'system/Menus.tjs':menu})
            project=base/'project'; cfg={'source':str(game), 'source_language':'english', 'language':'korean'}
            prepare(project,cfg)
            catalog=project/'data/catalog.json'; rows=json.loads(catalog.read_text(encoding='utf-8-sig'))
            original=[dict(r) for r in rows]; ui=next(r for r in rows if r.get('usage')=='ui_label')
            ui.pop('native_menu'); catalog.write_text(json.dumps(rows),encoding='utf-8')
            marker=project/'data/catalog-format.json'; value=json.loads(marker.read_text(encoding='utf-8-sig')); value['ui_text']=1; marker.write_text(json.dumps(value),encoding='utf-8')
            cache=project/'data/translations.jsonl'; cache.write_bytes(b'unchanged\n')
            result=ensure_ui_catalog(project,cfg)
            self.assertEqual(result['added'],0); self.assertGreater(result['updated'],0)
            upgraded=json.loads(catalog.read_text(encoding='utf-8-sig'))
            self.assertEqual(upgraded,original); self.assertEqual(cache.read_bytes(),b'unchanged\n')
            self.assertEqual(json.loads(marker.read_text(encoding='utf-8-sig'))['ui_text'],3)
            one=dict(ui,native_menu=True,translation='시작'); two=dict(one,id='second',translation='개시')
            with self.assertRaisesRegex(ValueError,'second'): native_map_bytes([one,two])


    def test_refresh_merges_owned_and_new_images_preserving_story(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); game=base/'game'; game.mkdir(); (game/'game.exe').touch()
            write_xp3(game/'data.xp3', {'start.ks':b'Hello world\n'})
            project=base/'project'; cfg={'source':str(game), 'source_language':'english', 'language':'korean', 'engine':'kirikiri'}
            prepare(project,cfg)
            rows=json.loads((project/'data/catalog.json').read_text(encoding='utf-8-sig'))
            with patch('kirikiri_project.ui_overrides', return_value={'ui/old.png':b'old'}):
                patch_path=render(project,cfg,rows=rows,known={r['id']:{'text':'story translated'} for r in rows})
            with DirectorySource(patch_path.parent) as src:
                archive=XP3Archive(src,patch_path.name); before=archive.read('start.ks')
            with patch('translation.cache',return_value={}), patch('kirikiri_support.ui_overrides',return_value={'ui/old.png':b'updated','ui/new.png':b'new'}):
                refresh_ui_output(project,cfg)
            report=json.loads((project/'data/kirikiri-output.json').read_text(encoding='utf-8-sig'))
            self.assertEqual(report['ui_assets'],{'ui/old.png':hashlib.sha256(b'updated').hexdigest(),'ui/new.png':hashlib.sha256(b'new').hexdigest()})
            with DirectorySource(patch_path.parent) as src:
                archive=XP3Archive(src,patch_path.name); inverse={v:k for k,v in report['member_map'].items()}
                self.assertEqual(archive.read(inverse['start.ks']),before)
                self.assertEqual(archive.read(inverse['ui/old.png']),b'updated')
                self.assertEqual(archive.read(inverse['ui/new.png']),b'new')
            self.assertEqual(static_check(project,cfg)['status'],'passed')
            patch_before=patch_path.read_bytes()
            with patch('translation.cache',return_value={}), patch('kirikiri_support.ui_overrides',return_value={'start.ks':b'bad'}):
                with self.assertRaises(ValueError): refresh_ui_output(project,cfg)
            self.assertEqual(patch_path.read_bytes(),patch_before)


    def test_native_duplicate_canonicalization_respects_manual_and_cache(self):
        import copy
        from kirikiri_ui_text_support import canonical_native_rows, native_map_bytes
        rows=[{'id':'one','source':'普通','ui_suffix':'(&3)','native_menu':True},
              {'id':'two','source':'普通','ui_suffix':'(&3)','native_menu':True},
              {'id':'three','source':'普通','ui_suffix':'(&4)','native_menu':True}]
        known={'one':{'text':'보통'},'two':{'text':'일반'},'three':{'text':'일반'}}
        original_rows=copy.deepcopy(rows); original_known=copy.deepcopy(known)
        value=json.loads(native_map_bytes(canonical_native_rows(rows,known)))['captions']
        self.assertEqual(value,{'普通(&3)':'보통(&3)','普通(&4)':'보통(&4)'})
        self.assertEqual(rows,original_rows); self.assertEqual(known,original_known)
        known['two']['status']='manual_translation'
        value=json.loads(native_map_bytes(canonical_native_rows(rows,known)))['captions']
        self.assertEqual(value['普通(&3)'],'일반(&3)')
        known['one']['model']='reviewed override'
        with self.assertRaisesRegex(ValueError,'one, two'): canonical_native_rows(rows,known)
        with self.assertRaises(ValueError): native_map_bytes([dict(rows[0],translation='보통'),dict(rows[1],translation='일반')])

    def test_native_same_label_with_different_accelerators_uses_one_translation(self):
        from kirikiri_ui_text_support import canonical_native_rows, native_map_bytes
        rows=[{'id':'a','source':'クイックセーブ','ui_suffix':'','native_menu':True},
              {'id':'b','source':'クイックセーブ','ui_suffix':'(&Q)','native_menu':True},
              {'id':'c','source':'クイックセーブ','ui_suffix':'(F2)','native_menu':True}]
        known={'a':{'text':'빠른 세이브'},'b':{'text':'빠른 저장'},'c':{'text':'퀵 저장'}}
        captions=json.loads(native_map_bytes(canonical_native_rows(rows,known)))['captions']
        self.assertEqual(captions,{'クイックセーブ':'빠른 세이브',
                                   'クイックセーブ(&Q)':'빠른 세이브(&Q)',
                                   'クイックセーブ(F2)':'빠른 세이브(F2)'})
        self.assertEqual(known['b']['text'],'빠른 저장')
        known['b']['status']='manual_translation'
        captions=json.loads(native_map_bytes(canonical_native_rows(rows,known)))['captions']
        self.assertEqual(captions['クイックセーブ(F2)'],'빠른 저장(F2)')
        known['c']['status']='manual_translation'
        with self.assertRaisesRegex(ValueError,'b, c'): canonical_native_rows(rows,known)

    def test_refresh_canonicalizes_duplicate_native_captions(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); game=base/'game'; game.mkdir(); (game/'game.exe').touch()
            menu=b'new KAGMenuItem(window, "Normal(&3)");\nnew KAGMenuItem(window, "Normal(&3)");'
            write_xp3(game/'data.xp3', {'start.ks':b'Hello\n','system/Menus.tjs':menu})
            project=base/'project'; cfg={'source':str(game),'source_language':'english','language':'korean','engine':'kirikiri'}
            prepare(project,cfg); rows=json.loads((project/'data/catalog.json').read_text(encoding='utf-8-sig'))
            native=[r for r in rows if r.get('native_menu')]; story=next(r for r in rows if r.get('usage')!='ui_label')
            known={story['id']:{'text':'이야기'},native[0]['id']:{'text':'보통'},native[1]['id']:{'text':'일반'}}
            with patch('kirikiri_project.locale_output',return_value={'files':[]}):
                patch_path=render(project,cfg,rows=rows,known=known)
            result=json.loads((patch_path.parent/'ui-native-menus.json').read_text(encoding='utf-8-sig'))
            self.assertEqual(result['captions']['Normal(&3)'],'보통(&3)')
            with patch('translation.cache',return_value=known): refresh_ui_output(project,cfg)
            self.assertEqual(json.loads((patch_path.parent/'ui-native-menus.json').read_text(encoding='utf-8-sig')),result)
            self.assertEqual(known[native[1]['id']]['text'],'일반')
            self.assertEqual(static_check(project,cfg)['status'],'passed')

    def test_map_ownership_and_tamper(self):
        from kirikiri_ui_text_support import write_native_map, map_record
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp); raw=b'{"version":1,"captions":{}}'
            path, old=write_native_map(folder,{},raw); self.assertIsNone(old)
            with self.assertRaises(ValueError): write_native_map(folder,{},raw)
            write_native_map(folder,{'ui_native_map':map_record(raw)},b'new')
            with self.assertRaises(ValueError): write_native_map(folder,{'ui_native_map':map_record(raw)},raw)

if __name__ == '__main__': unittest.main()
