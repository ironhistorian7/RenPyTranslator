import unittest
from kirikiri_route_display import patch_route_display,patch_chapter_fallback
class RouteDisplayTests(unittest.TestCase):
    def test_only_display_expression_changes_and_fallback_is_preserved(self):
        raw=b'function getRouteText(tag, unknown) { var info=getChapterInfo(tag); if(info===void) return unknown; with (info) return @"${.volume} route ${.title1}"; }'
        output=patch_route_display(raw,{'route-A':'route-B','route-B':'route-C'}).decode()
        self.assertIn('var __rpt_route_volume = .volume',output)
        self.assertIn('else if (__rpt_route_volume ==',output)
        self.assertIn('${__rpt_route_volume} route ${.title1}',output)
        self.assertIn('if(info===void) return unknown;',output)
        self.assertEqual(patch_route_display(raw,{}),raw)
        with self.assertRaises(ValueError):patch_route_display(raw.replace(b'getChapterInfo(tag)',b'unknownFn(tag)'),{'a':'b'})
    def test_saved_title_only_when_current_chapter_is_unavailable(self):
        raw=b'function getChapterText(tag, unknown) { var info = getChapterInfo(tag); if (info === void) return unknown; return getChapterState(tag) > 0 ? info.title2 : "????"; }'
        out=patch_chapter_fallback(raw,{'Old title':'New title'}).decode()
        self.assertIn('if (unknown == "Old title") return "New title";',out)
        self.assertIn('return unknown;',out)
        self.assertIn('getChapterState(tag) > 0 ? info.title2',out)
        self.assertEqual(patch_chapter_fallback(raw,{}),raw)
    def test_synthetic_project_keeps_metadata_and_verifies_final_patch(self):
        import json, tempfile
        from pathlib import Path
        from kirikiri_containers import write_xp3, DirectorySource, XP3Archive
        from kirikiri_project import prepare,render,static_check
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);game=base/'game';game.mkdir();(game/'game.exe').touch();project=base/'project'
            listing='(const) %[ "one" => (const) %[ "tag" => "one", "title1" => "Scene.01", "title2" => "見出し", "volume" => "共通" ] ];'
            custom='function getRouteText(tag, unknown) { var info=getChapterInfo(tag); if(info===void) return unknown; with (info) return @"${.volume}ルート ${.title1}"; }\nfunction getChapterText(tag, unknown) { var info=getChapterInfo(tag); if (info === void) return unknown; return getChapterState(tag) > 0 ? info.title2 : "????"; }\ndrawInfoText(x,y,getRouteText(info.sceneTag, "不明"));'
            write_xp3(game/'data.xp3',{'main/scnlist.tjs':listing.encode(),'main/custom.tjs':custom.encode()})
            cfg=dict(source=str(game),source_language='japanese',language='korean',engine='kirikiri',show_reference=False)
            prepare(project,cfg)
            rows=json.loads((project/'data/catalog.json').read_text(encoding='utf8'))
            values={'見出し':'제목','共通':'공통','${.volume}ルート ${.title1}':'${.volume} 루트 ${.title1}','不明':'미상'}
            known={r['id']:{'text':values.get(r['source'],r['source'])} for r in rows}
            virtual=[r for r in rows if r.get('ui_readonly_volume')]
            self.assertEqual(len(virtual),1)
            patch=render(project,cfg,rows=rows,known=known)
            self.assertEqual(static_check(project,cfg)['status'],'passed')
            ledger=project/'data/kirikiri-output.json'
            report=json.loads(ledger.read_text(encoding='utf8'))
            forged=dict(virtual[0],translation='위조');forged['ui_readonly_volume']=False
            report['ui_text_edits']['main/scnlist.tjs'].append(forged)
            for mode in ('false','missing'):
                if mode=='missing': forged.pop('ui_readonly_volume')
                ledger.write_text(json.dumps(report,ensure_ascii=False),encoding='utf8')
                self.assertEqual(static_check(project,cfg)['status'],'failed')
            with DirectorySource(patch.parent) as ds:
                arc=XP3Archive(ds,patch.name);mapping=json.loads((project/'data/kirikiri-output.json').read_text(encoding='utf8'))['member_map'];inverse={v:k for k,v in mapping.items()}
                script=arc.read(inverse['main/custom.tjs']).decode();metadata=arc.read(inverse['main/scnlist.tjs']).decode()
            self.assertIn('"volume" => "共通"',metadata)
            self.assertIn('__rpt_route_volume',script);self.assertIn('if (unknown ==',script)
            self.assertIn('getChapterState(tag) > 0 ? info.title2',script)
if __name__=='__main__':unittest.main()
