"""Synthetic localization-table checks; no game, plugin or model execution."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rpgmaker_language import discover_tables,prepare_sources


class RPGMakerLanguageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def write(self,relative,text):
        path=self.root/relative
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(text,encoding='utf-8')
        return relative

    def plugin(self,*,base='',enabled=True,filename='translations/ja.csv',locale='ja'):
        plugins=[dict(name='MultiLanguageTranslation',status=enabled,
                      parameters=dict(translationFile=filename,startupLanguage=locale))]
        self.write(base+'js/plugins.js','var $plugins = '+json.dumps(plugins)+';')

    def test_known_plugin_declares_selected_source_and_records_provenance(self):
        self.plugin(base='www/')
        self.write('www/translations/ja.csv','source,translation\nStart the sample.,サンプルを開始する。\n')
        declarations=discover_tables(self.root/'www')
        self.assertEqual(declarations,[dict(file='translations/ja.csv',locale='ja',plugin='MultiLanguageTranslation')])
        selected=prepare_sources(self.root,'japanese',base='www')
        value=selected.resolve('Start the sample.')
        self.assertEqual(value['text'],'サンプルを開始する。')
        self.assertEqual(value['provenance'][0]['file'],'www/translations/ja.csv')
        self.assertEqual(value['provenance'][0]['locale'],'ja')
        english=prepare_sources(self.root,'english',base='www')
        self.assertIsNone(english.resolve('Start the sample.'))
        self.assertFalse(english.report()['selected_locale_available'])

    def test_csv_locale_columns_and_explicit_row_locales(self):
        first=self.write('locale/table.csv','source,en,ja\nSAMPLE_KEY,An example.,例文。\n')
        second=self.write('locale/rows.csv','source,language,translation\nNEXT_KEY,ja,次の例。\n')
        mapping=prepare_sources(self.root,'ja',files=[first,second])
        self.assertEqual(mapping.resolve('SAMPLE_KEY')['text'],'例文。')
        self.assertEqual(mapping.resolve('An example.')['text'],'例文。')
        self.assertEqual(mapping.resolve('NEXT_KEY')['text'],'次の例。')

    def test_generic_json_locale_maps_transposed_maps_and_rows(self):
        documents={
            'locale/a.json':{'translations':{'en':{'ONE':'First example.'},'ja':{'ONE':'一つ目の例。'}}},
            'locale/b.json':{'TWO':{'english':'Second example.','japanese':'二つ目の例。'}},
            'locale/c.json':[{'source':'THREE','en':'Third example.','ja':'三つ目の例。'}],
        }
        for path,document in documents.items():self.write(path,json.dumps(document,ensure_ascii=False))
        mapping=prepare_sources(self.root,'japanese',files=documents)
        self.assertEqual([mapping.resolve(key)['text'] for key in ('ONE','TWO','THREE')],
                         ['一つ目の例。','二つ目の例。','三つ目の例。'])

    def test_conflicting_table_values_are_not_arbitrarily_selected(self):
        path=self.write('locale/table.csv','source,ja\nKEY,一つの例。\nKEY,別の例。\n')
        mapping=prepare_sources(self.root,'japanese',files=[path])
        self.assertIsNone(mapping.resolve('KEY'))
        self.assertEqual(mapping.report()['ambiguous_keys'],1)

    def test_unknown_and_single_language_files_do_not_guess_a_translation(self):
        paths=[self.write('data/Map001.json',json.dumps({'events':[{'list':[{'code':401,'parameters':['Synthetic source.']}]}]})),
               self.write('custom.csv','source,translation\nSynthetic source.,別の例。\n'),
               self.write('custom.json',json.dumps({'Synthetic source.':'別の例。'}))]
        mapping=prepare_sources(self.root,'japanese',files=paths)
        self.assertIsNone(mapping.resolve('Synthetic source.'))
        self.assertEqual(mapping.report()['mapped_keys'],0)
        self.assertFalse(mapping.report()['issues'])

    def test_disabled_plugins_and_unparsed_javascript_are_not_executed(self):
        self.plugin(enabled=False)
        self.write('translations/ja.csv','source,translation\nKEY,例文。\n')
        self.assertEqual(discover_tables(self.root),[])
        self.assertIsNone(prepare_sources(self.root,'japanese').resolve('KEY'))
        self.write('js/plugins.js','throw new Error("do not execute");')
        mapping=prepare_sources(self.root,'japanese')
        self.assertIsNone(mapping.resolve('KEY'))
        self.assertEqual(mapping.report()['issues'][0]['file'],'js/plugins.js')

    def test_multiline_entries_use_whole_declared_lines_without_fragment_guesses(self):
        source='Example \\N[1].'
        path=self.write('locale/map.json',json.dumps({'ja':{source:'例文 \\N[1]。','Next example.':'次の例。'}},ensure_ascii=False))
        mapping=prepare_sources(self.root,'japanese',files=[path])
        value=mapping.resolve(source+'\nNext example.')
        self.assertEqual(value['text'],'例文 \\N[1]。\n次の例。')
        self.assertEqual(len(value['provenance']),2)
        self.assertIsNone(mapping.resolve('Prefix '+source))

    def test_native_display_aliases_include_declared_locales_without_forcing_one(self):
        path=self.write('locale/map.json',json.dumps({'KEY':{'en':'An example.','ja':'例文。','zh':'示例。'}},ensure_ascii=False))
        mapping=prepare_sources(self.root,'japanese',files=[path])
        self.assertEqual(set(mapping.runtime_aliases('KEY')),{'KEY','An example.','例文。','示例。'})

    def test_invalid_external_paths_are_not_read(self):
        self.plugin(filename='../outside.csv')
        read_text=Path.read_text
        with patch.object(Path,'read_text',autospec=True,side_effect=read_text) as reads:
            with self.assertRaisesRegex(ValueError,'폴더 밖'):discover_tables(self.root)
        self.assertEqual(reads.call_count,1)  # Static plugin registry only.

    def test_declared_two_column_csv_and_quoted_newlines_are_supported(self):
        path=self.write('locale/declared.csv','source,translation\n"First line.\nSecond line.","一行目。\n二行目。"\n')
        mapping=prepare_sources(self.root,'ja',declared_tables=[dict(file=path,locale='ja')])
        self.assertEqual(mapping.resolve('First line.\nSecond line.')['text'],'一行目。\n二行目。')

    def test_unavailable_optional_plugin_table_does_not_block_a_single_language_game(self):
        self.plugin(filename='../outside.csv')
        mapping=prepare_sources(self.root,'english')
        self.assertIsNone(mapping.resolve('A regular English message.'))
        self.assertTrue(mapping.report()['issues'])


if __name__=='__main__':unittest.main()
