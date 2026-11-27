import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import kirikiri_project
import kirikiri_font_support as support
from kirikiri_containers import DirectorySource,XP3Archive,write_xp3
from test_kirikiri_font_layout import SRC,PRE,tft


class FontSupportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.project=self.root/'project';self.data=self.project/'data';self.data.mkdir(parents=True)
        self.game=self.root/'game';self.game.mkdir()
        self.cache=self.data/'translations.jsonl';self.cache.write_bytes(b'preserve cache\n')
        write_xp3(self.game/'data.xp3',{
            'system/MessageLayer.tjs':SRC,'system/PreRenderFontEx.tjs':PRE,
            'main/jp.tft':tft([0x41,0x3042]),'main/ko.tft':tft([0x41,0xac00]),
            'main/custom.tjs':b'with (layer.font) { .face="PreviewBitmap"; .mapPrerenderedFont("jp.tft"); }'})
        self.manifest={'source':{'container_path':str(self.game),'game_root':''}}

    def test_english_does_not_collect_or_change_fonts(self):
        with patch.object(support,'_capture') as collect:
            self.assertEqual(support.font_layout_overrides(self.project,{'source_language':'english'}),{})
        collect.assert_not_called()

    def test_bitmap_coverage_and_direct_faces_are_derived_and_reusable_offline(self):
        with patch.object(kirikiri_project,'_verified',return_value=self.manifest):
            overrides=support.font_layout_overrides(self.project,{'source_language':'japanese'})
        profile=json.loads((self.data/'kirikiri-font-layout.json').read_text(encoding='utf-8'))
        self.assertEqual(profile['unsupported_storages'],['jp.tft'])
        self.assertEqual(profile['unsupported_direct_faces'],['PreviewBitmap'])
        self.assertIn('system/MessageLayer.tjs',overrides)
        with patch.object(support,'open_source',side_effect=AssertionError('source must not be opened')):
            self.assertEqual(support.font_layout_overrides(self.project,{'source_language':'japanese'}),overrides)
        self.assertEqual(self.cache.read_bytes(),b'preserve cache\n')
        (self.data/'kirikiri-font-layout/message.tjs').write_bytes(b'changed')
        with self.assertRaises(ValueError):support.font_layout_overrides(self.project,{'source_language':'japanese'})

    def test_ui_sources_are_captured_and_overlayed_with_message(self):
        text=b'''class TextRender {
function processCh(ch){ var w=font.getTextWidth(ch); }
function drawCh(layer, ox, oy, ch){ var dt = layer.drawText; }
function draw(layer, ox, oy, width, height, text, autoIndent=1, base=void) {
 var ret= render(width, height, layer.font, text, autoIndent, base);
 drawAll(layer, ox, oy); return ret;
}
function drawAll(layer, ox, oy) {}
}'''
        util=b'''function drawTextKinsokuRect(layer, text, elm) {
var font=layer.font; font.face=elm.fontface; var gtw=font.getTextWidth;
var cur = "", lines = [], len = text.length;
if (gtw(cur + ch)) dt(layer, dx, y, text, color);
return lines;
}
function next() {}'''
        (self.game/'data.xp3').unlink()
        write_xp3(self.game/'data.xp3',{'system/MessageLayer.tjs':SRC,'system/PreRenderFontEx.tjs':PRE,
                   'main/jp.tft':tft([0x3042]),'system/TextRender.tjs':text,'system/Utils.tjs':util})
        with patch.object(kirikiri_project,'_verified',return_value=self.manifest):
            out=support.font_layout_overrides(self.project,{'source_language':'japanese'})
        self.assertEqual(set(out),{'system/MessageLayer.tjs','system/TextRender.tjs','system/Utils.tjs'})
        self.assertIn(b'__rptOriginalDraw',out['system/TextRender.tjs'])
        self.assertIn(b'__rptKinsokuNeedsHangulFont',out['system/Utils.tjs'])
        self.assertEqual(self.cache.read_bytes(),b'preserve cache\n')
        with patch.object(support,'open_source',side_effect=AssertionError('source must not be opened')):
            self.assertEqual(support.font_layout_overrides(self.project,{'source_language':'japanese'}),out)

    def test_duplicate_bitmap_basenames_are_not_assumed_to_be_same_font(self):
        (self.game/'data.xp3').unlink()
        write_xp3(self.game/'data.xp3',{'system/MessageLayer.tjs':SRC,'system/PreRenderFontEx.tjs':PRE,
                                     'a/common.tft':tft([0x3042]),'b/common.tft':tft([0xac00])})
        with patch.object(kirikiri_project,'_verified',return_value=self.manifest):
            support.font_layout_overrides(self.project,{'source_language':'japanese'})
        profile=json.loads((self.data/'kirikiri-font-layout.json').read_text(encoding='utf-8'))
        self.assertEqual(profile['unsupported_storages'],[])

    def test_refresh_preserves_finished_scenarios_and_never_loads_translation_cache(self):
        out=self.root/'out';out.mkdir();path=out/'patch.xp3'
        write_xp3(path,{'intro.ks':b'preserved translated bytes','Config.tjs':b'old config',
                        'custom.tjs':b'preserved display code','unrelated.png':b'preserved unrelated image'})
        route_map={'main/custom.tjs':{'source route':'translated route'}}
        chapter_map={'main/custom.tjs':{'source chapter':'translated chapter'}}
        button_path='ui/scene@jump%sysbutton;normal.png'
        button=b'approved replacement image'
        ledger={'patch':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'layout':'flat','files':['Config.tjs','custom.tjs','intro.ks','unrelated.png'],
                'member_map':{'Config.tjs':'main/Config.tjs','custom.tjs':'main/custom.tjs',
                              'intro.ks':'scn/intro.ks','unrelated.png':'ui/unrelated.png'},
                'ui_assets':{},'ui_route_map':route_map,'ui_chapter_map':chapter_map}
        report=self.data/'kirikiri-output.json'
        report.write_text(json.dumps(ledger),encoding='utf-8')
        with patch.object(kirikiri_project,'_verified',return_value={}), \
             patch.object(kirikiri_project,'_font_overlay',return_value=('main/Config.tjs',b'new config')), \
             patch.object(kirikiri_project,'font_layout_overrides',return_value={'system/MessageLayer.tjs':b'new renderer'}), \
             patch.object(kirikiri_project,'ensure_patch_layout',return_value={'layout':'flat','verified':True}), \
             patch.object(kirikiri_project,'ui_overrides',return_value={button_path:button}) as images, \
             patch('translation.cache',side_effect=AssertionError('cache must not be loaded')):
            result=kirikiri_project.repack_output(self.project,{'source_language':'japanese'},refresh_support=True)
            updated_archive=path.read_bytes()
            updated_report=report.read_bytes()
            no_change=kirikiri_project.repack_output(self.project,{'source_language':'japanese'},refresh_support=True)
            self.assertEqual(no_change['status'],'unchanged')
            images.return_value={'ui/unrelated.png':b'unapproved overwrite'}
            with self.assertRaises(kirikiri_project.KiriError):
                kirikiri_project.repack_output(self.project,{'source_language':'japanese'},refresh_support=True)
            self.assertEqual(path.read_bytes(),updated_archive)
            self.assertEqual(report.read_bytes(),updated_report)
        self.assertEqual(result['status'],'repacked')
        with DirectorySource(out) as source:
            packed=XP3Archive(source,'patch.xp3')
            self.assertEqual(packed.read('intro.ks'),b'preserved translated bytes')
            self.assertEqual(packed.read('Config.tjs'),b'new config')
            self.assertEqual(packed.read('MessageLayer.tjs'),b'new renderer')
            self.assertEqual(packed.read('custom.tjs'),b'preserved display code')
            self.assertEqual(packed.read(Path(button_path).name),button)
        updated_ledger=json.loads(updated_report)
        self.assertEqual(updated_ledger['ui_route_map'],route_map)
        self.assertEqual(updated_ledger['ui_chapter_map'],chapter_map)
        self.assertEqual(updated_ledger['ui_assets'],{button_path:hashlib.sha256(button).hexdigest()})
        self.assertEqual(self.cache.read_bytes(),b'preserve cache\n')
        self.assertTrue((Path(result['backup'])/'patch.xp3').is_file())


if __name__=='__main__':unittest.main()
