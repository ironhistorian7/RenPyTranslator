import io
import pickle
from pathlib import Path
import tempfile
import unittest
import zlib
from engine import extract_scripts
from translation import protect, restore, quote, catalog, validate_text, dialogue_literal, rendered_text, render, fingerprint
from engine import save_json
from unittest.mock import patch
import json
import zipfile
import ast

class IntegrityTests(unittest.TestCase):
    def test_say_text_slot_in_common_renpy_forms(self):
        cases=[
            ('"Cashier" "Just a sec."','"Cashier" ""',1),
            ('n happy "Just a sec." nointeract','n happy "" nointeract',0),
            ('actors["cashier"] "Just a sec."','actors["cashier"] ""',1),
            ('"Cashier" "Just a sec." (what_prefix="(")','"Cashier" "" (what_prefix="(")',1),
            ('extend "Just a sec." with Dissolve(0.2)','extend "" with Dissolve(0.2)',0),
            ('"Just a sec."','""',0),
            ('"Cashier" "Say \\"hello\\"."','"Cashier" ""',1),
        ]
        for before,after,index in cases:
            with self.subTest(before=before):
                old,new,slot=dialogue_literal(before,after)
                self.assertEqual(slot,index)
                self.assertEqual(ast.literal_eval(new.group()),'')
                output=after[:new.start()]+quote('잠깐만요.')+after[new.end():]
                self.assertEqual(rendered_text(output,{'literal_index':slot}),'잠깐만요.')
                self.assertEqual(output[:new.start()],after[:new.start()])

    def test_quoted_speaker_catalog_render_and_package_offline(self):
        from packaging import package
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);game=p/'staging/game';d=game/'tl/korean';d.mkdir(parents=True)
            text='translate korean scene_a:\n    # "Cashier" "Just a sec."\n    "Cashier" ""\n\ntranslate korean scene_b:\n    # actors["cashier"] "Thanks." (what_prefix="(")\n    actors["cashier"] "" (what_prefix="(")\n'
            (d/'scene.rpy').write_text(text,encoding='utf-8')
            cfg={'language':'korean','model':'test'};rows=catalog(p,cfg)
            self.assertEqual([r['source'] for r in rows],['Just a sec.','Thanks.'])
            self.assertEqual(rows[0]['speaker_name'],'Cashier')
            entries=[dict(id=r['id'],source=r['source'],text=t,model='test',fingerprint=fingerprint(cfg)) for r,t in zip(rows,['잠깐만요.','고마워요.'])]
            (p/'data/translations.jsonl').write_text(''.join(json.dumps(e,ensure_ascii=False)+'\n' for e in entries),encoding='utf-8')
            with patch('translation.request',side_effect=AssertionError('No inference')):
                render(p,cfg)
                output=(d/'scene.rpy').read_text(encoding='utf-8')
                self.assertIn('"Cashier" "잠깐만요.\\n{rpt_ref=0.55}',output)
                self.assertIn('actors["cashier"] "고마워요.\\n{rpt_ref=0.55}',output)
                self.assertIn('(what_prefix="(")',output)
                (game/'zz_rpt_korean.rpy').write_text('# test',encoding='utf-8')
                save_json(p/'data/validation.json',{'errors':[]})
                with patch('packaging.verify_source',return_value={'unchanged':True}):package(p,cfg)
            with zipfile.ZipFile(next((p/'output').glob('*.zip'))) as z:
                self.assertEqual(z.read('game/tl/korean/scene.rpy'),(d/'scene.rpy').read_bytes())
            # The package verifier must check dialogue, even if speaker is unchanged.
            (d/'scene.rpy').write_text(output.replace('잠깐만요.','잘못된 값'),encoding='utf-8')
            with patch('packaging.verify_source',return_value={'unchanged':True}):
                with self.assertRaisesRegex(ValueError,'stale'):package(p,cfg)
    def test_date_fields_survive_localization(self):
        source='{#file_time}%A, %B %d %Y, %H:%M'
        target='{#file_time}%Y년 %B %d일 (%A) %H:%M'
        self.assertEqual(validate_text(source,target,{}),[])
        self.assertIn('format field mismatch',validate_text('%b %d, %H:%M','10월 1일, 12:00',{}))
        self.assertIn('format field mismatch',validate_text('%(count)d files','파일 %(count)s개',{}))
    def test_hyperlink_and_interpolation_roundtrip(self):
        text='Click {a=chapter_one}[name]{/a}, then {{ or [[.'
        protected,tokens=protect(text)
        self.assertEqual(restore(protected,tokens),text)
        with self.assertRaises(ValueError):restore(protected.replace('<rpt001/>',''),tokens)
    def test_quoted_translation_remains_data(self):
        text='"안녕"\n[who] {a=target}링크{/a} \\ 경로'
        self.assertEqual(ast.literal_eval(quote(text)),text)
    def test_archive_path_cannot_escape_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);p=root/'bad.rpa';raw=pickle.dumps({'../escape.rpyc':[(0,0)]},protocol=2)
            header=b'RPA-3.0 0000000000000022 00000000\n'
            self.assertEqual(len(header),34)
            p.write_bytes(header+zlib.compress(raw))
            with self.assertRaises(ValueError):extract_scripts(p,root/'output')
            self.assertFalse((root/'escape.rpyc').exists())
    def test_catalog_preserves_voice_and_blank_say(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);d=p/'staging/game/tl/korean';d.mkdir(parents=True)
            (d/'scene.rpy').write_text('translate korean scene_a:\n    # voice "a.wav"\n    # n "Hello [name]."\n    voice "a.wav"\n    n ""\n\ntranslate korean scene_b:\n    # n ""\n    n ""\n\ntranslate korean strings:\n    old "Save"\n    new ""\n',encoding='utf-8')
            rows=catalog(p,{'language':'korean','expected_dialogue':1})
            self.assertEqual(len(rows),2)
            self.assertEqual(rows[0]['source'],'Hello [name].')
            self.assertEqual(rows[1]['source'],'Save')
    def test_archive_prefix_does_not_read_next_file_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);p=root/'prefix.rpa'
            header=b'RPA-3.0 0000000000000028 00000000\n'
            # Logical script is prefix+body = b'HEADbody'; trailing ZZ belongs elsewhere.
            index={'script.rpyc':[(34,8,'HEAD')]}
            p.write_bytes(header+b'bodyZZ'+zlib.compress(pickle.dumps(index,protocol=2)))
            extract_scripts(p,root/'out')
            self.assertEqual((root/'out/script.rpyc').read_bytes(),b'HEADbody')

if __name__=='__main__':unittest.main()
