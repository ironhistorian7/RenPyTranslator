"""Synthetic source binding provenance; no game, cache, or model access."""
import json
from pathlib import Path
import tempfile
import unittest

from rpgmaker_language import prepare_sources
from rpgmaker_sources import bind_rows, source_binding_trace


class SourceProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def mapping(self, document, language):
        (self.root/'table.json').write_text(json.dumps(document,ensure_ascii=False),encoding='utf-8')
        return prepare_sources(self.root,language,files=['table.json'])

    def test_selected_languages_and_anchors_are_preserved(self):
        document={'secret key':{'en':'Example text','ja':'例文','zh':'示例'}}
        for language,expected in [('english','Example text'),('japanese','例文')]:
            with self.subTest(language=language):
                rows=[dict(id='stable-id',source='secret key',location={'ptr':'/1/name'})]
                bind_rows(rows,self.mapping(document,language))
                self.assertEqual(rows[0]['source'],expected)
                self.assertEqual(rows[0]['id'],'stable-id')
                self.assertEqual(rows[0]['base_source'],'secret key')
                self.assertEqual(set(rows[0]['source_aliases']),{'secret key','Example text','例文','示例'})
                trace=source_binding_trace(rows,{'source_language':language})[0]
                self.assertEqual(trace['binding_mode'],'declared_table')
                self.assertEqual(trace['status'],'selected')
                self.assertTrue(trace['input_is_selected_table'])
                self.assertFalse(trace['input_is_base'])
                self.assertEqual(trace['table_provenance'][0]['file'],'table.json')
                self.assertNotIn('secret key',json.dumps(trace))
                self.assertNotIn(expected,json.dumps(trace,ensure_ascii=False))

    def test_native_base_and_missing_locale_are_distinct(self):
        rows=[dict(id='a',source='Native text')]
        bind_rows(rows,prepare_sources(self.root,'english'))
        item=source_binding_trace(rows,{'source_language':'english'})[0]
        self.assertEqual(item['status'],'native_base')
        self.assertTrue(item['input_is_base'])
        self.assertFalse(item['input_is_selected_table'])
        rows=[dict(id='b',source='key')]
        bind_rows(rows,self.mapping({'key':{'ja':'例文'}},'english'))
        self.assertEqual(source_binding_trace(rows,'english')[0]['status'],'selected_locale_missing')
        self.assertEqual(rows[0]['source'],'key')

    def test_ambiguous_selection_does_not_choose_a_locale_or_touch_cache(self):
        path=self.root/'table.csv'
        path.write_text('source,ja\nkey,例文\nkey,別の例\n',encoding='utf-8')
        cache=self.root/'translations.jsonl';cache.write_bytes(b'legacy cache sentinel\n')
        rows=[dict(id='c',source='key')]
        bind_rows(rows,prepare_sources(self.root,'japanese',files=['table.csv']))
        self.assertEqual(rows[0]['source'],'key')
        self.assertEqual(source_binding_trace(rows,'japanese')[0]['status'],'ambiguous')
        self.assertEqual(cache.read_bytes(),b'legacy cache sentinel\n')


if __name__=='__main__':unittest.main()
