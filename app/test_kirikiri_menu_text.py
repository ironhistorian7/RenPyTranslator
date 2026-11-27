import unittest
from kirikiri_menu_text import extract_ui_text, render_ui_text, native_caption_pairs

class MenuTextTests(unittest.TestCase):
    def test_renderer_helper_template_and_fallback_preserve_properties(self):
        from kirikiri_tokens import protect, restore, validate
        source=('function getDisplay(tag, missing) { '
                'if (tag === void) return missing; '
                'with (tag) return @"${.volume}ルート ${.title1}"; } '
                'drawInfoText(target, rect, getDisplay(data, "ルート不明"), 1); '
                'other(getDisplay(data,"操作名"));').encode('utf-8')
        rows=extract_ui_text(source,'main/custom.tjs',japanese_ui=True)
        self.assertEqual([r['source'] for r in rows],['${.volume}ルート ${.title1}','ルート不明'])
        protected,tokens=protect(rows[0]['source'],'kirikiri_tjs_ui')
        self.assertEqual(tokens,['${.volume}','${.title1}'])
        translated=restore('<rpt000/> 루트 <rpt001/>',tokens)
        self.assertEqual(validate(rows[0]['source'],translated,'kirikiri_tjs_ui'),[])
        rows[0]['translation']=translated; rows[1]['translation']='루트 미상'
        rendered=render_ui_text(source,rows).decode('utf-8')
        self.assertIn('${.volume}',rendered)
        self.assertIn('${.title1}',rendered)
        self.assertIn('other(getDisplay(data,"操作名"))',rendered)
        rows[0]['translation']='루트 ${.title1}'
        with self.assertRaises(ValueError): render_ui_text(source,rows)

    def test_duplicate_helper_names_are_not_guessed(self):
        source=('function x(a) { return @"${.id}内容"; } '
                'function x(a) { return @"${.id}別"; } '
                'drawInfoText(t,p,x(data));').encode('utf-8')
        self.assertEqual(extract_ui_text(source,'main/custom.tjs',japanese_ui=True),[])

    def test_route_volume_is_readonly_and_deduplicated(self):
        raw=('[(const) %[ "tag" => "a", "title1" => "Scene.01", "title2" => "場面一", "volume" => "春" ],'
             '(const) %[ "tag" => "b", "title1" => "Scene.02", "title2" => "場面二", "volume" => "春" ],'
             '(const) %[ "tag" => "c", "title1" => "Scene.03", "title2" => "場面三", "volume" => "夏" ]];').encode('utf-8')
        rows=extract_ui_text(raw,'main/scnlist.tjs',japanese_ui=True)
        volume=[r for r in rows if r.get('ui_readonly_volume')]
        self.assertEqual([r['source'] for r in volume],['春','夏'])
        self.assertEqual([r['source'] for r in rows if not r.get('ui_readonly_volume')],['場面一','場面二','場面三'])
        for r in volume:r['translation']='별도 표시'
        self.assertEqual(render_ui_text(raw,volume),raw)
        self.assertEqual(extract_ui_text(raw,'main/scnlist.tjs'),[])

    def test_japanese_scene_labels_preserve_identifiers_and_code(self):
        raw = ('(const) %[ "tag" => "scene01", "name" => "元の識別子", '
               '"title1" => "Scene.01", "title2" => "新しい場面", '
               '"next" => "scene02" ];').encode('cp932')
        self.assertEqual(extract_ui_text(raw,'main/scnlist.tjs'),[])
        rows=extract_ui_text(raw,'main/scnlist.tjs',japanese_ui=True)
        self.assertEqual([r['source'] for r in rows],['新しい場面'])
        rows[0]['translation']='새 장면'
        out=render_ui_text(raw,rows).decode('cp932')
        self.assertIn('"tag" => "scene01"',out)
        self.assertIn('"name" => "元の識別子"',out)
        self.assertIn('"next" => "scene02"',out)
        self.assertIn('"title2" => "\\xC0C8',out)
        self.assertEqual(extract_ui_text(raw,'main/other.tjs',japanese_ui=True),[])

    def test_japanese_literal_renderer_only(self):
        raw=('drawInfoText(target, rect, "保存枠", 1);'
             'drawInfoText(target, rect, "接頭" + id, 1);'
             'drawInfoText(target, "画像名", computed, 1);'
             'drawTextKinsokuRect(layer, "確認", flags);'
             'var path = "画像名";').encode('utf-8')
        self.assertEqual(extract_ui_text(raw,'main/custom.tjs'),[])
        rows=extract_ui_text(raw,'main/custom.tjs',japanese_ui=True)
        self.assertEqual([r['source'] for r in rows],['保存枠','確認'])
        for r in rows: r['translation']='확인'
        result=render_ui_text(raw,rows).decode('utf-8')
        self.assertIn('var path = "画像名"',result)
        self.assertIn('"接頭" + id',result)

    def test_menu_literals_and_control_suffix(self):
        text = ('// new KAGMenuItem(w, "誤り")\n'
                'new KAGMenuItem(w, "設定(&S) ...\\tCtrl+S", 0, action);\n'
                'new MenuItem(w, "-"); new MenuItem(w, "");\n'
                'new MenuItem(w, "abc" + command); var id = "秘密";\n')
        raw = text.encode('cp932'); rows = extract_ui_text(raw, 'system/Menus.tjs')
        self.assertEqual([r['source'] for r in rows], ['設定'])
        self.assertEqual(rows[0]['ui_suffix'], '(&S) ...\tCtrl+S')
        self.assertEqual(render_ui_text(raw, rows), raw)
        rows[0]['translation'] = '설정 "보기"'
        result = render_ui_text(raw, rows).decode('cp932')
        self.assertEqual(result, text)
        self.assertEqual(native_caption_pairs(rows), [{'source':'設定(&S) ...\tCtrl+S', 'translation':'설정 "보기"(&S) ...\tCtrl+S'}])
        self.assertIn('var id = "秘密"', result)

    def test_settings_strict_context(self):
        raw = ('title:"設定", typeFace:"書体",\n'
               'add(PushButton("閉じる", IDCANCEL));\n'
               'addTrackBar("拡大率", "track", 1, 2, 3, 4, "内接", "外接");\n'
               'var text = @"警告 ${reason}"; var thing = "番号";\n'
               'add(PushButton("接頭" + x, 1));').encode('utf-8')
        rows = extract_ui_text(raw, 'sysscn/exmenuintf.tjs')
        self.assertEqual([r['source'] for r in rows], ['設定','閉じる','拡大率','内接','外接'])
        self.assertEqual(extract_ui_text(raw, 'story.tjs'), [])

    def test_popup_and_shortcut_template_context(self):
        popup = ('menuBarHidePopupMenu = [%[caption:"常に表示", group:1], '
                 '%[caption:"メニューバー", children:menu]]; '
                 'other = %[caption:"警告", message:"秘密"];').encode('utf-8')
        rows = extract_ui_text(popup, 'system/MainWindow.tjs')
        self.assertEqual([r['source'] for r in rows], ['常に表示', 'メニューバー'])
        self.assertTrue(all(r['native_menu'] for r in rows))
        config = ('.ShortCutBindCaptions = %[save:"保存", load:"読込"];'
                  '.ShortCutBindTags = ["save", "load"];'
                  '.ShortCutInputBind = "「${caption}」に";'
                  '.ShortCutInputHelp = ("${bindtext}押す${lf}" "中止${half_lf}");'
                  '.ShortCutNoBind = "未設定";').encode('utf-8')
        rows = extract_ui_text(config, 'main/default.tjs')
        self.assertEqual([r['source'] for r in rows],
                         ['保存','読込','「${caption}」に','${bindtext}押す${lf}',
                          '中止${half_lf}','未設定'])
        rows[2]['translation'] = '「${caption}」에'
        self.assertIn(b'${caption}', render_ui_text(config, rows))
        rows[2]['translation'] = '누락'
        with self.assertRaises(ValueError): render_ui_text(config, rows)

    def test_only_initialization_confirmation(self):
        raw = ('function setDefault() { askYesNo("初期化しますか", "確認", init); }'
               'function other() { askYesNo("他の文", "警告", go); }').encode('utf-8')
        self.assertEqual([r['source'] for r in extract_ui_text(raw, 'sysscn/option.tjs')],
                         ['初期化しますか', '確認'])

    def test_ternary_menu_literals_and_conflicts(self):
        raw = ('new KAGMenuItem(w, state ? "次へ(&F)" : "進む(&F)", 0); '
               'new MenuItem(w, call() ? "危険" : "対象外"); '
               'new MenuItem(w, state ? "危険" + id : "対象外");').encode('utf-8')
        rows = extract_ui_text(raw, 'system/Menus.tjs')
        self.assertEqual([r['source'] for r in rows], ['次へ', '進む'])
        for row in rows: row['translation'] = '다음'
        self.assertEqual(render_ui_text(raw, rows), raw)
        self.assertEqual(native_caption_pairs(rows), [
            {'source':'次へ(&F)', 'translation':'다음(&F)'},
            {'source':'進む(&F)', 'translation':'다음(&F)'}])
        rows.append(dict(rows[0], translation='다른'))
        with self.assertRaises(ValueError): native_caption_pairs(rows)

    def test_custom_menu_dict_radio_and_inline_accelerators(self):
        raw = ('createMenuFromList(w, m, [%[name:"root", caption:"ファイル(&F)", children:[]],'
               'getSysArgRadioMenu("id", "表示(&D)", "tag", true, '
               '["*auto:自動(&A)", "off:無効(&N):cmd:2", "-"])]);'
               'getSysArgCheckMenuItem("x", "等倍時にDI&B方式", "tag", true, "yes");'
               'createMenuFromList(w,m,[%[name:"id",caption:"&WEBサイト",exp:action]]);'
               'var unrelated=%[caption:"警告",message:"秘密"];').encode('utf-8')
        rows = extract_ui_text(raw, 'sysscn/Override.tjs')
        self.assertEqual([r['source'] for r in rows], ['ファイル', '表示', '自動', '無効', '等倍時にDIB方式', 'WEBサイト'])
        rows[4]['translation'] = 'DIB 방식'; rows[5]['translation'] = '웹사이트'
        pairs = native_caption_pairs(rows)
        self.assertEqual(pairs[0], {'source':'等倍時にDI&B方式', 'translation':'DI&B 방식'})
        self.assertEqual(pairs[1], {'source':'&WEBサイト', 'translation':'웹사이트(&W)'})
        self.assertEqual(render_ui_text(raw, rows), raw)

    def test_flattened_radio_caption_map(self):
        raw = ('getSysArgRadioMenu("vid", "画面", "tag", true, '
               '["*auto:自動", "off:無効"]);').encode('utf-8')
        rows = extract_ui_text(raw, 'sysscn/Override.tjs')
        for row, target in zip(rows, ('화면', '자동', '끄기')): row['translation'] = target
        pairs = {p['source']:p['translation'] for p in native_caption_pairs(rows)}
        self.assertEqual(pairs['[画面] 自動'], '[화면] 자동')
        self.assertEqual(pairs['[画面] 無効'], '[화면] 끄기')

    def test_movie_menu_config_only(self):
        raw = ('var MovieQualitySelectConfig = %[menu:"画質", types:["hd"], '
               'hd:%[menu:"高画質",suffix:"_hd"], sd:%[menu:"低画質"]]; '
               'var unrelated=%[menu:"秘密"];').encode('utf-8')
        rows = extract_ui_text(raw, 'sysscn/movieqsel.tjs')
        self.assertEqual([r['source'] for r in rows], ['画質', '高画質', '低画質'])
        for row, target in zip(rows, ('화질', '고화질', '저화질')): row['translation'] = target
        pairs = {p['source']:p['translation'] for p in native_caption_pairs(rows)}
        self.assertEqual(pairs['[画質] 高画質'], '[화질] 고화질')

    def test_numeric_dynamic_menu_template(self):
        raw = ('.disableWindowResizeMenuItem.caption = @"${dw} x ${dh} で固定" + "(&F)"; '
               '.unrelated.caption = @"${name}の文章" + "(&F)";').encode('utf-8')
        rows = extract_ui_text(raw, 'sysscn/Override.tjs')
        self.assertEqual([r['source'] for r in rows], ['${dw} x ${dh} で固定'])
        rows[0]['translation'] = '${dw} x ${dh} 고정'
        self.assertEqual(native_caption_pairs(rows), [{'source':'${dw} x ${dh} で固定(&F)',
                                                        'translation':'${dw} x ${dh} 고정(&F)'}])
        rows[0]['translation'] = '${dw} 고정'
        with self.assertRaises(ValueError): native_caption_pairs(rows)

    def test_menu_hook_caption_only(self):
        raw = ('addKagHookCallback("onKAGSetupMenu", function(kag) {'
               'storeMenu.caption = "クイックセーブ(&Q)"; }, true);'
               'other.caption = "対象外";').encode('utf-8')
        rows = extract_ui_text(raw, 'main/custom.tjs')
        self.assertEqual([r['source'] for r in rows], ['クイックセーブ'])
        self.assertTrue(rows[0]['native_menu'])

    def test_gallery_button_draw_and_hint_preserve_other_properties(self):
        raw = ('drawButtonText(target, "簡易", sz); '
               'drawButtonText(target, 4, face, sz, "詳細"); '
               'setItemProp("mode", "hint", "表示を切り替え"); '
               'setItemProp("mode", "storage", "絵.png");').encode('utf-8')
        rows = extract_ui_text(raw, 'main/custom.tjs', japanese_ui=True)
        self.assertEqual([r['source'] for r in rows], ['簡易', '詳細', '表示を切り替え'])
        for row, translated in zip(rows, ('간단히', '자세히', '표시 전환')):
            row['translation'] = translated
        self.assertIn('"storage", "絵.png"', render_ui_text(raw, rows).decode('utf-8'))
        self.assertFalse(extract_ui_text(raw, 'main/custom.tjs'))

    def test_skip_display_and_save_dialog_keep_format_arguments(self):
        from kirikiri_tokens import protect
        raw = ('if (win) {var title = "選択肢まで %s".sprintf(value()); '
               'win.fillRect(0, 0, x, y, color); win.drawText(1, 2, title, color); } '
               'var _errorInformMessageMap = %[savefail:"保存できません", '
               'load:%[exp:function(mes){return @"読み込み失敗 (${mes})";}, '
               'caption:"Warning", storage:"画像.png"]]; '
               'var elsewhere=%[message:"対象外"]; '
               'if (win) { var tag = "表示内部"; win.drawText(1, 2, tag, c); routes[tag](); }').encode('utf-8')
        rows = extract_ui_text(raw, 'system/MainWindow.tjs', japanese_ui=True)
        self.assertEqual([r['source'] for r in rows], [
            '選択肢まで %s', '保存できません', '読み込み失敗 (${mes})', 'Warning'])
        self.assertEqual(protect(rows[0]['source'], 'kirikiri_tjs_ui')[1], ['%s'])
        rows[0]['translation'] = '선택지까지 %s'
        self.assertIn(b'%s".sprintf(value())', render_ui_text(raw, rows))
        rows[0]['translation'] = '선택지까지'
        with self.assertRaises(ValueError): render_ui_text(raw, rows)

    def test_browser_hints_and_confirmation_ignore_navigation_data(self):
        raw = ('new KAGMenuItem(w, "RightClick", callback); '
               'System.inform("ファイルがありません"); '
               'link(%[exp:"exec()", hint:"無効", storage:"記録.ks"]); '
               'lookup(%[ hint:"内部設定", key:"識別子" ]);').encode('utf-8')
        self.assertEqual([r['source'] for r in extract_ui_text(raw, 'sysscn/browswindow.tjs', japanese_ui=True)],
                         ['ファイルがありません', '無効'])
        raw = ('function askYesNo(message, caption="確認", yes, no) { '
               'createButton("はい", 0, 0); call("保存しますか"); }').encode('utf-8')
        self.assertEqual([r['source'] for r in extract_ui_text(raw, 'system/YesNoDialog.tjs', japanese_ui=True)],
                         ['確認', 'はい'])

    def test_speech_dialog_localizes_text_without_changing_native_map(self):
        raw = ('var items=[%[name:"speech", caption:"読み上げ(&S)", exp:"run()"]];').encode('utf-8')
        rows = extract_ui_text(raw, 'sysscn/speech.tjs', japanese_ui=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['ui_suffix'], '(&S)')
        rows[0]['translation'] = '읽기'
        self.assertIn(b'(&S)", exp:"run()"', render_ui_text(raw, rows))
        self.assertEqual(native_caption_pairs(rows), [])
        raw = ('var dialog=%[title:"音声設定", filter:["実行ファイル|*.exe"], '
               'name:filename]; var children=%[Speed:%[text:"速さ", cmd:"Handler"], '
               'Header:"音量", Sample:%[ini:"こんにちは"]];').encode('utf-8')
        rows = extract_ui_text(raw, 'sysscn/speech_dlg.tjs', japanese_ui=True)
        self.assertEqual([r['source'] for r in rows], ['音声設定', '実行ファイル', '速さ', '音量'])
        self.assertEqual(rows[1]['ui_suffix'], '|*.exe')
        rows[1]['translation'] = '실행 파일'
        self.assertIn(b'|*.exe', render_ui_text(raw, rows))

    def test_utf16_and_changed_source_guard(self):
        raw = b'\xff\xfe' + 'add(PushButton("戻る(&B)", 0));'.encode('utf-16-le')
        rows = extract_ui_text(raw, 'sysscn/exmenuintf.tjs'); rows[0]['translation'] = '뒤로'
        result = render_ui_text(raw, rows)
        self.assertTrue(result.startswith(b'\xff\xfe'))
        self.assertIn('\\xB4A4\\xB85C(&B)', result[2:].decode('utf-16-le'))
        rows[0]['source'] = '別'
        with self.assertRaises(ValueError): render_ui_text(raw, rows)

if __name__ == '__main__': unittest.main()
