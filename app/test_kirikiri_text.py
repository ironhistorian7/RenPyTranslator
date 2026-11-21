import codecs
import unittest
from kirikiri_tokens import protect, restore, validate, readable
from kirikiri_text import extract_text, render_text

class KiriKiriTests(unittest.TestCase):
    def test_kagex_controls(self):
        s = 'Hi $actor.name + 1; #ff0000;red#;%125big%r[dummy,2]字\\nEnd\\\\'
        cfg = {'_kirikiri_dialect':'kagex_psb'}
        text, tokens = protect(s, cfg)
        self.assertEqual(restore(text, tokens, cfg), s)
        self.assertEqual(validate(s, restore(text, tokens), cfg), [])
        self.assertIn('$actor.name + 1;', tokens)
        self.assertIn('%r', tokens)
        self.assertNotIn('actor', readable(s, cfg))
        with self.assertRaises(ValueError): restore(text.replace('<rpt000/>', ''), tokens)
    def test_extraction_and_encoding(self):
        s = '*start\r\n;comment\r\n#名前\r\nこんにちは[wait time="2"]世界\r\n[button storage="x" text="選択"]\r\n[iscript]\r\nfoo="not prose";\r\n[endscript]\r\n'
        raw=s.encode('cp932'); rows=extract_text(raw,'x.ks')
        self.assertEqual([r['source'] for r in rows], ['名前','こんにちは[wait time="2"]世界','選択'])
        row=rows[1]; out=render_text(raw,[dict(row,translation='안녕하세요[wait time="2"]세계')])
        self.assertTrue(out.startswith(codecs.BOM_UTF16_LE))
        self.assertEqual(out[2:].decode('utf-16-le'),s.replace('こんにちは[wait time="2"]世界','안녕하세요[wait time="2"]세계'))
        self.assertEqual(render_text(raw,[]),raw)
    def test_nested_quoted_attribute_is_not_extracted(self):
        raw=b'[button storage="text=\'malicious\'" text="OK"]\n'
        self.assertEqual([r['source'] for r in extract_text(raw)], ['OK'])
        row=extract_text(raw)[0]
        with self.assertRaises(ValueError): render_text(raw,[dict(row,translation='a"b')])
    def test_legacy_reference_flag_does_not_append_original_dialogue(self):
        raw = 'Hello[wait]world'.encode('cp932')
        row = extract_text(raw)[0]
        out = render_text(raw, [dict(row, translation='안녕[wait]세계', show_reference=True)])
        self.assertEqual(out[2:].decode('utf-16-le'), '안녕[wait]세계')
        with self.assertRaises(ValueError): render_text(raw, [dict(row, translation='[jump target="*x"]', show_reference=True)])
        label = extract_text(b'#Speaker\n')[0]
        with self.assertRaises(ValueError): render_text(b'#Speaker\n', [dict(label, translation='name', show_reference=True)])

    def test_inline_controls_keep_sentence_and_require_exact_restore(self):
        raw = b'Hello [font size="20"]dear[resetfont] [ruby text="x"]friend[wait time=10]!'
        row = extract_text(raw)[0]
        self.assertEqual(row['source'], raw.decode('ascii'))
        result = '안녕 [font size="20"]친애하는[resetfont] [ruby text="x"]친구[wait time=10]!'
        rendered = render_text(raw, [dict(row, translation=result)])
        self.assertEqual(rendered[2:].decode('utf-16-le'), result)
        with self.assertRaises(ValueError): render_text(raw, [dict(row, translation=result.replace('[wait time=10]', ''))])
        with self.assertRaises(ValueError): render_text(raw, [dict(row, translation=result + '[jump target="x"]')])
        with self.assertRaises(ValueError): render_text(raw, [dict(row, translation=result + '[')])
    def test_directive_caption_and_control_boundary(self):
        raw = b'@button storage=img text="Start" target="*x"\nHello[unknown foo="bar"]world\n'
        rows = extract_text(raw)
        self.assertEqual([r['source'] for r in rows], ['Start','Hello','world'])
        self.assertEqual(rows[0]['usage'], 'choice')
        self.assertIn(b'text="Go"', render_text(raw,[dict(rows[0],translation='Go')]))
    def test_escape_literals_readable_and_roundtrip(self):
        cfg={'_kirikiri_dialect':'kagex_psb'}
        source='Price: \\$100 \\% \\# \\[x \\\\ $danger();[ruby]字\nnext'
        self.assertEqual(readable(source,cfg),'Price: $100 % # [x \\ 字\nnext')
        encoded,tokens=protect(source,cfg)
        self.assertEqual(restore(encoded,tokens),source)
        with self.assertRaises(ValueError):restore(encoded.replace('<rpt000/>',''),tokens)
        kag='A[[bracket] and [font size=20]text[resetfont]'
        self.assertEqual(readable(kag),'A[bracket] and text')
        encoded,tokens=protect(kag)
        self.assertEqual(tokens[0],'[[')
        self.assertEqual(restore(encoded,tokens),kag)
        self.assertEqual(extract_text(b'[[')[0]['source'], '[[')
        row=extract_text(kag.encode('ascii'))[0]
        self.assertEqual(row['source'],kag)
        out='가[[괄호] 와 [font size=20]글[resetfont]'
        self.assertEqual(render_text(kag.encode('ascii'),[dict(row,translation=out)])[2:].decode('utf-16-le'),out)

    def test_missing_token_fails(self):
        text, tokens = protect('a[font size=20]b', {'_kirikiri_dialect':'kag'})
        with self.assertRaises(ValueError): restore(text.replace('<rpt000/>',''),tokens)

    def test_invalid_encoding_rejected(self):
        with self.assertRaises(UnicodeDecodeError): extract_text(b'\x81')
    def test_quoted_command_and_script_skipped(self):
        raw=b'hello[foo data="a]b"]world\n[iscript] hidden [endscript]\n'
        self.assertEqual([r['source'] for r in extract_text(raw)],['hello','world'])

if __name__ == '__main__': unittest.main()
