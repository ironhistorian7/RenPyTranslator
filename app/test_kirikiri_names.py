import unittest

from kirikiri_names import is_alias_table, parse_alias_tables, resolve_default_name


class KiriNamesTests(unittest.TestCase):
    def test_recognizes_only_table_names(self):
        self.assertTrue(is_alias_table(r'data\CHARVOICE.TXT'))
        self.assertFalse(is_alias_table('data/charvoice_backup.csv'))
        self.assertEqual(parse_alias_tables({'other.csv': b'x'}),
                         {'version': 1, 'aliases': {}, 'issues': [], 'files': []})

    def test_encodings_tab_header_comment_and_blank(self):
        first = '# comment\ninternal,voicefile,config,volume,NameAlias\nUnitA,v,c,100,\u540d\u524d\nUnitB,v,c,100,\n'
        second = 'UnitC\tv\tc\t100\t\u58f0\n'
        third = 'UnitD,v,c,100,\u7532\n'
        result = parse_alias_tables({'a/charvoice.csv': first.encode('utf-8-sig'),
                                     'b/CHARVOICE.TXT': second.encode('utf-16'),
                                     'c/charvoice.csv': third.encode('cp932')})
        self.assertEqual(result['aliases'], {'unita': '\u540d\u524d', 'unitb': 'UnitB',
                                             'unitc': '\u58f0', 'unitd': '\u7532'})
        self.assertFalse(result['issues'])
        self.assertEqual(resolve_default_name('UNITA', result['aliases']), '\u540d\u524d')
        self.assertEqual(resolve_default_name('Unlisted_02', result['aliases']), 'Unlisted_02')

    def test_conflicts_dynamic_invalid_and_malformed(self):
        data = ('ID,v,c,0,First\nID,v,c,0,First\nID,v,c,0,Second\n'
                'Dynamic,v,c,0,&expression\nBroken\nControl,v,c,0,Bad\x01Name\n')
        result = parse_alias_tables({'charvoice.csv': data.encode()})
        self.assertEqual(result['aliases'], {})
        self.assertEqual({x['code'] for x in result['issues']},
                         {'conflicting_alias', 'dynamic_alias', 'malformed_row', 'invalid_alias'})
        self.assertEqual(resolve_default_name('ID', result['aliases']), 'ID')

    def test_one_step_raw_id_and_quoted_csv(self):
        result = parse_alias_tables({'charvoice.csv': b'A,v,c,0,B\nB,v,c,0,"A, B"\n'})
        self.assertEqual(resolve_default_name('A', result['aliases']), 'B')
        self.assertEqual(resolve_default_name('B', result['aliases']), 'A, B')
        self.assertEqual(resolve_default_name('A_1', result['aliases']), 'A_1')
        self.assertIsNone(resolve_default_name(None, result['aliases']))

    def test_invalid_names_are_not_display_rows(self):
        for name in ('', '  ', '\x01bad', '&call', 'abc{d}', 'abc[d]',
                     r'abc\d', 'abc$d', 'abc%d', 'abc#d'):
            with self.subTest(name=name):
                self.assertIsNone(resolve_default_name(name, {}))
        for value in ('bad{value}', 'bad[value]', r'bad\value', 'bad$value',
                      'bad%value', 'bad#value'):
            with self.subTest(value=value):
                self.assertIsNone(resolve_default_name('ID', {'id': value}))
        self.assertEqual(resolve_default_name('ID', {'id': 'Valid Name'}), 'Valid Name')

    def test_optional_columns_and_tab_delimiter(self):
        for filename, data in (
            ('csv/charvoice.csv', 'Two,v\nThree,v,c\nFour,v,c,0\nBad\n'),
            ('tab/charvoice.txt', 'Two\tv\nThree\tv\tc\nFour\tv\tc\t0\nBad\n'),
        ):
            with self.subTest(filename=filename):
                result = parse_alias_tables({filename: data.encode()})
                self.assertEqual(result['aliases'], {'two': 'Two', 'three': 'Three', 'four': 'Four'})
                self.assertEqual([x['code'] for x in result['issues']], ['malformed_row'])

    def test_unreadable_bytes(self):
        result = parse_alias_tables({'charvoice.csv': b'\x81'})
        self.assertEqual(result['issues'][0]['code'], 'decode_error')


if __name__ == '__main__':
    unittest.main()
