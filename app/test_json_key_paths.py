"""Synthetic JSON structures only; no games, projects, models or filesystem data."""
import json
import unittest

from gameloc_vendor import load

load()
from gameloc import containers, jsonnest, treepath


class JsonKeyPathTests(unittest.TestCase):
    def test_existing_plain_paths_and_array_indices_keep_their_spelling(self):
        parts = ['a', 'b', 0, 'c']
        for module in (treepath, jsonnest):
            with self.subTest(module=module.__name__):
                self.assertEqual(module.format_path(parts), 'a.b[0].c')
                self.assertEqual(module.parse_path('a.b[0].c'), parts)
                self.assertEqual(module.parse_path('a.["b.c"][0].name'), ['a', 'b.c', 0, 'name'])

    def test_literal_keys_roundtrip_without_losing_characters(self):
        keys = ['a.b', '[0]', 'a[]b', '!', 'a!b', '', '"quoted"',
                'back\\slash', 'line\nbreak', '日本語', '한글🙂', 'x!"\\.[0]']
        for key in keys:
            for module in (treepath, jsonnest):
                with self.subTest(key=key, module=module.__name__):
                    parts = ['root', key, 0, 'value']
                    self.assertEqual(module.parse_path(module.format_path(parts)), parts)
        self.assertEqual(treepath.format_path(['a.b']), '["a.b"]')
        self.assertEqual(treepath.format_path(['!']), '["!"]')
        self.assertEqual(treepath.format_path(['']), '[""]')
        self.assertEqual(treepath.format_path(['日本語']), '日本語')

    def test_dotted_key_and_nested_keys_have_distinct_addresses(self):
        document = {'a.b': {'value': 'literal'}, 'a': {'b': {'value': 'nested'}}}
        found = dict(treepath.walk_strings(document))
        self.assertEqual(found, {'["a.b"].value': 'literal', 'a.b.value': 'nested'})
        treepath.set_by_path(document, '["a.b"].value', 'changed')
        self.assertEqual(treepath.get_by_path(document, '["a.b"].value'), 'changed')
        self.assertEqual(treepath.get_by_path(document, 'a.b.value'), 'nested')
        self.assertEqual(set(document), {'a.b', 'a'})

    def test_plain_json_apply_changes_values_without_changing_literal_keys(self):
        document = {'a.b...': {'[key]!': ['', 'original']}, '': 'empty-key value',
                    '!': 'bang-key value', 'quote"\\': 'quoted-key value'}
        text = json.dumps(document, ensure_ascii=False)
        edits = {ptr: 'changed ' + value for ptr, value in containers.explode(text, 'json')}
        updated = json.loads(containers.implode(text, 'json', edits))
        self.assertEqual(set(updated), set(document))
        self.assertEqual(updated['a.b...']['[key]!'][1], 'changed original')
        self.assertEqual(updated[''], 'changed empty-key value')
        self.assertEqual(updated['!'], 'changed bang-key value')
        self.assertEqual(updated['quote"\\'], 'changed quoted-key value')

    def test_nested_json_unwrap_keeps_legacy_operator_syntax(self):
        path = 'parameters.list![0]!text'
        parts = ['parameters', 'list', jsonnest.UNWRAP, 0, jsonnest.UNWRAP, 'text']
        self.assertEqual(jsonnest.format_path(parts), path)
        parsed = jsonnest.parse_path(path)
        self.assertIs(parsed[2], jsonnest.UNWRAP)
        self.assertIs(parsed[4], jsonnest.UNWRAP)
        document = {'parameters': {'list': json.dumps([json.dumps({'text': 'original'})])}}
        jsonnest.set_(document, path, 'changed')
        self.assertEqual(jsonnest.get(document, path), 'changed')

    def test_literal_bang_is_distinct_from_unwrap_even_in_nested_json(self):
        document = {'outer': json.dumps({'!': {'a.b': [{'[key]': 'original'}]}}, ensure_ascii=False),
                    '!': 'outer bang'}
        paths = dict(jsonnest.walk(document))
        path = 'outer!["!"]["a.b"][0]["[key]"]'
        self.assertEqual(paths, {path: 'original', '["!"]': 'outer bang'})
        parts = jsonnest.parse_path(path)
        self.assertIs(parts[1], jsonnest.UNWRAP)
        self.assertEqual(parts[2], '!')
        self.assertIsNot(parts[2], jsonnest.UNWRAP)
        jsonnest.set_(document, path, 'changed')
        self.assertEqual(jsonnest.get(document, path), 'changed')
        self.assertEqual(jsonnest.get(document, '["!"]'), 'outer bang')

    def test_invalid_quoted_paths_fail_instead_of_using_a_partial_key(self):
        for path in ('["unfinished]', '["bad\\q"]', '[true]', '["key" trailing]'):
            for module in (treepath, jsonnest):
                with self.subTest(path=path, module=module.__name__), self.assertRaises(ValueError):
                    module.parse_path(path)


if __name__ == '__main__':
    unittest.main()
