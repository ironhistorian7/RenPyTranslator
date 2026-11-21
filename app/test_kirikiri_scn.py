import copy
import unittest
from app.kirikiri_scn import extract, apply


class SceneTests(unittest.TestCase):
    def test_default_speakers_and_anchored_apply(self):
        rows = [
            ['arbitrary_id', None, 'One', None, 0, {}],
            ['another_id', None, 'Two', None, 0, {}, None, 'speech', 'search'],
            ['explicit_id', 'Visible', 'Three', None, 0, {}],
            ['blank_id', '', 'Four', None, 0, {}],
            [None, None, 'Narration', None, 0, {}],
            ['', None, 'Empty ID', None, 0, {}],
        ]
        root = {'scenes': [{'texts': rows}]}
        before = copy.deepcopy(root)
        self.assertEqual([x['text'] for x in extract(root) if x['usage'] == 'speaker'], ['Visible'])
        records = [x for x in extract(root, include_defaults=True,
                   name_aliases={'arbitrary_id': 'First', 'another_id': 'Second'})
                   if x['usage'] == 'speaker']
        self.assertEqual([x['text'] for x in records], ['First', 'Second', 'Visible'])
        self.assertTrue(records[0]['derived_speaker'])
        self.assertEqual((records[0]['speaker_internal'], records[0]['speaker_display'],
                          records[0]['speaker_default']), ('arbitrary_id', None, 'First'))
        path = lambda i, field=1: ('scenes', 0, 'texts', i, field)
        for bad_path, anchor in [(path(0), 'wrong'), (path(0, 0), 'arbitrary_id'),
                                 (path(3), 'blank_id'), (path(4), 'narrator'),
                                 (path(5), '')]:
            with self.assertRaises(ValueError):
                apply(root, {bad_path: 'Changed'}, default_speakers={bad_path: anchor})
            self.assertEqual(root, before)
        apply(root, {path(0): '첫째', path(1): '둘째'},
              default_speakers={path(0): 'arbitrary_id', path(1): 'another_id'})
        before['scenes'][0]['texts'][0][1] = '첫째'
        before['scenes'][0]['texts'][1][1] = '둘째'
        self.assertEqual(root, before)

    def test_exact_paths_and_search(self):
        root = {'name': 'internal', 'scenes': [{'title': 'Title', 'label': 'internal', 'texts': [
            ['id', 'Name', 'Text', ['voice'], 7, {'state': 'internal'}, None, None, 'text'],
            [None, None, 'Other', None, 0, {}]],
            'selects': [{'text': 'Choice', 'target': 'internal', 'curLine': 1, 'curLineStr': 'internal'}]}]}
        original = copy.deepcopy(root)
        items = extract(root)
        self.assertEqual([x['usage'] for x in items], ['title', 'speaker', 'dialogue', 'dialogue', 'choice'])
        path = ('scenes', 0, 'texts', 0, 2)
        with self.assertRaises(ValueError): apply(root, {path: 'New'})
        self.assertEqual(root, original)
        apply(root, {path: 'New', ('scenes', 0, 'selects', 0, 'text'): 'Pick'}, str.lower)
        row = root['scenes'][0]['texts'][0]
        self.assertEqual(row, ['id', 'Name', 'New', ['voice'], 7, {'state': 'internal'}, None, None, 'new'])
        self.assertEqual(root['scenes'][0]['selects'][0]['target'], 'internal')
        with self.assertRaises(ValueError): apply(root, {('name',): 'oops'})
        self.assertEqual(extract({'unrelated': ['hello']}), [])

    def test_optional_speech(self):
        rows = [
            ['id', None, 'one', None, 0, {}, None, 'old speech', 'old search'],
            ['id', None, 'two', None, 0, {}, None, None, 'old search'],
            ['id', None, 'three', None, 0, {}, None, {'unknown': 1}, 'old search'],
            ['id', None, 'four', None, 0, {}],
        ]
        root = {'scenes': [{'texts': rows}]}
        replacements = {('scenes', 0, 'texts', i, 2): 'new' for i in range(4)}
        with self.assertWarnsRegex(RuntimeWarning, 'Unsupported speech'):
            apply(root, replacements, str.upper, lambda text: 'spoken ' + text)
        self.assertEqual(rows[0][7:], ['spoken new', 'NEW'])
        self.assertEqual(rows[1][7:], [None, 'NEW'])
        self.assertEqual(rows[2][7:], [{'unknown': 1}, 'NEW'])
        self.assertEqual(len(rows[3]), 6)
        with self.assertWarnsRegex(RuntimeWarning, 'No speech normalizer'):
            apply(root, {('scenes', 0, 'texts', 0, 2): 'later'}, str.upper)
        self.assertEqual(rows[0][2], 'later')
        self.assertEqual(rows[0][7], 'spoken new')
        with self.assertWarnsRegex(RuntimeWarning, 'Speech normalization failed'):
            apply(root, {('scenes', 0, 'texts', 0, 2): 'last'}, str.upper, lambda _: None)
        self.assertEqual(rows[0][2], 'last')
        self.assertEqual(rows[0][7], 'spoken new')

if __name__ == '__main__': unittest.main()
