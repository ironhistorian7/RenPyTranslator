import struct, unittest
from kirikiri_font_layout import FontLayoutUnsupported, patch_message_layer, patch_ui_font, patch_direct_ui_font, tft_hangul_coverage
PRE = b'''class BitmapWrapper {
function fontFilterFace(prop, value) { curInfo = value != "" ? fontInfo[value] : void; }
function fontChanged(prop, value) { return (this[prop] = value); }
function getCurrentTag() { curSizes = curInfo[num]; }
function searchNearSize(size) { return size; }
propertyHook("face", this, "fontFilterFace");
propertyHook("height", this, "fontChanged");
}'''
SRC = b'''class NameLayer extends RelativeLayer {
function processNameText(text) { if (text != "") {
var x = 0, y = 0; var w = font.getTextWidth(text);
draw(x, y, text); visible = true;
} else { visible = false; } } }
class MessageLayer extends AnimKAGLayerTransMove {
function processCh(ch) { var llfont = ll.font;
var cw = llfont.getTextWidth(ch);
drawTextToLayer(ll, dx, dy, ch, chColor);
cw += pitch; adjustAlign() if(align >= 0);
return void; } }
'''
def tft(codes):
    o=36; r=o+2*len(codes)
    return b'TVP pre-rendered font\x1a\x01\x02'+struct.pack('<III',len(codes),o,r)+struct.pack('<%dH'%len(codes),*codes)+b'\0'*(20*len(codes))
class LayoutTest(unittest.TestCase):
    def test_tft(self):
        self.assertEqual(tft_hangul_coverage(tft([0x41,0x3042])),0)
        self.assertEqual(tft_hangul_coverage(tft([0x41,0xac00])),1)
        with self.assertRaises(FontLayoutUnsupported):tft_hangul_coverage(tft([0xac00])[:-1])
        with self.assertRaises(FontLayoutUnsupported):tft_hangul_coverage(tft([0x42,0x41]))
    def test_patch(self):
        o=patch_message_layer(SRC,PRE,['font1_21.tft'])
        self.assertEqual(o.count(b'function __rptNeedsHangulFont'),2)
        self.assertEqual(o.count(b'if (__rptChange) font.face = __rptFace;'),2)
        self.assertEqual(o.count(b'if (__rptChange) llfont.face = __rptFace;'),2)
        self.assertIn(b'info[tag + ex.searchNearSize(size)]',o)
        self.assertNotIn(b'finally',o)
        self.assertLess(o.index(b'if (__rptChange) llfont.face = '),o.index(b'llfont.getTextWidth(ch)'))
        self.assertIn(b'\\xb9d1\\xc740\\x0020\\xace0\\xb515\\x002c',o)
        self.assertNotIn('맑은 고딕'.encode('utf-8'),o)
    def test_reject(self):
        for source,pre,names in [(SRC,b'bad',['a.tft']),(SRC+SRC,PRE,['a.tft']),(SRC.replace(b'cw += pitch;',b'return void; cw += pitch;'),PRE,['a.tft'])]:
            with self.assertRaises(FontLayoutUnsupported):patch_message_layer(source,pre,names)
    def test_direct_and_legacy_without_wrapper(self):
        out = patch_message_layer(SRC, unsupported_direct_faces=['MyNameFont'])
        self.assertIn(b'\\x004d\\x0079\\x004e\\x0061\\x006d\\x0065\\x0046\\x006f\\x006e\\x0074',out)
        self.assertIn(b'\\x004d\\x0061\\x006c\\x0067\\x0075\\x006e',out)
        self.assertLess(out.index(b'var face = lay.font.face;'), out.index(b'global.PreRenderFontEx'))
    def test_text_render_wraps_measure_and_draw_and_restores(self):
        source=b'''class TextRender {
function processCh(ch){ var w=font.getTextWidth(ch); }
function drawCh(layer, ox, oy, ch){ var dt = layer.drawText; }
function draw(layer, ox, oy, width, height, text, autoIndent=1, base=void) {
 // original comment
 var ret= render(width, height, layer.font, text, autoIndent, base);
 drawAll(layer, ox, oy); return ret;
}
function drawAll(layer, ox, oy) {}
}'''
        out=patch_ui_font(source,'textrender',['a.tft'])
        self.assertIn(b'function __rptOriginalDraw',out)
        self.assertLess(out.index(b'if (__rptChange) layer.font.face = '),out.index(b'__rptResult = __rptOriginalDraw'))
        self.assertEqual(out.count(b'if (__rptChange) layer.font.face = __rptFace;'),2)
        self.assertIn(b'// original comment',out)
        with self.assertRaises(FontLayoutUnsupported):patch_ui_font(source.replace(b'drawAll(layer, ox, oy);',b'noop();'), 'textrender')

    def test_kinsoku_guard_wraps_width_and_draw_with_exception_restore(self):
        source=b'''function drawTextKinsokuRect(layer, text, elm) {
var font=layer.font; font.face=elm.fontface; var gtw=font.getTextWidth;
var cur = "", lines = [], len = text.length;
if (gtw(cur + ch)) dt(layer, dx, y, text, color);
return lines;
}
function next() {}'''
        out=patch_ui_font(source,'kinsoku',['a.tft'])
        self.assertIn(b'typeof elm.gettextwidth != "Object"',out)
        self.assertLess(out.index(b'if (__rptChange) font.face = '),out.index(b'gtw(cur + ch)'))
        self.assertLess(out.index(b'gtw(cur + ch)'),out.index(b'dt(layer, dx, y, text'))
        self.assertEqual(out.count(b'if (__rptChange) font.face = __rptFace;'),2)
        with self.assertRaises(FontLayoutUnsupported):patch_ui_font(source.replace(b'gtw(cur + ch)',b'other(cur)'), 'kinsoku')

    def test_direct_ui_rewrites_proven_layer_only_and_counts_unknown(self):
        source=b'''function example(lay) {
with(lay) { .drawText(0,0,text,1); EdgeShadowDrawText(.drawText, lay, 0,0,text,1); }
with(obj.field) { .drawText(0,0,text,1); }
other.drawText(0,0,text,1);
}'''
        out,missing=patch_direct_ui_font(source,['font.tft'])
        self.assertEqual(missing,2)
        self.assertIn(b'__rptDirectDraw(lay,0,0,text,1)',out)
        self.assertIn(b'__rptEdgeDraw(lay, 0,0,text,1)',out)
        self.assertIn(b'with(obj.field) { .drawText(',out)
        self.assertEqual(out.count(b'if (change) lay.font.face = face;'),4)

    def test_deferred_mapping_is_rejected(self):
        deferred = PRE.replace(b'curInfo = value != "" ? fontInfo[value] : void;', b'checkUpdate = true;')
        with self.assertRaises(FontLayoutUnsupported):
            patch_message_layer(SRC, deferred, ['font.tft'])
    def test_commented_duplicate(self):
        o=patch_message_layer(b'/* class NameLayer extends RelativeLayer { */\n'+SRC,PRE,['a.tft'])
        self.assertEqual(o.count(b'function __rptNeedsHangulFont'),2)
if __name__=='__main__':unittest.main()
