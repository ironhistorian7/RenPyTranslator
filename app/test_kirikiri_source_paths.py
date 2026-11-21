import unittest
import json
import tempfile
from pathlib import Path

from kirikiri_patch_paths import PatchPathError
from kirikiri_source_paths import canonical_patch_member
from kirikiri_containers import DirectorySource, write_xp3
from kirikiri_project import _sources, prepare


class CanonicalPatchMemberTests(unittest.TestCase):
    def test_unique_nested_match_and_absent_member(self):
        self.assertEqual(canonical_patch_member('SCENE.ks', ['a/b/scene.ks', 'other.ks']), 'a/b/scene.ks')
        self.assertEqual(canonical_patch_member('new.ks', ['a/b/scene.ks']), 'new.ks')

    def test_deduplicates_case_insensitive_identical_paths(self):
        self.assertEqual(canonical_patch_member('scene.ks', ['A/Scene.ks', 'a/scene.KS']), 'A/Scene.ks')

    def test_ambiguous_including_root_and_nested(self):
        for names in (['a/scene.ks', 'b/SCENE.ks'], ['scene.ks', 'a/scene.ks']):
            with self.subTest(names=names), self.assertRaises(PatchPathError) as caught:
                canonical_patch_member('scene.ks', names)
            self.assertEqual(caught.exception.error_code, 'kirikiri_patch_conflict')

    def test_hierarchical_mode_and_nested_member_are_unchanged(self):
        names = ['a/scene.ks', 'b/scene.ks']
        self.assertEqual(canonical_patch_member('scene.ks', names, flat=False), 'scene.ks')
        self.assertEqual(canonical_patch_member('a/scene.ks', names), 'a/scene.ks')

    def test_rejects_unsafe_paths(self):
        for member, names in [('../scene.ks', []), ('scene.ks', ['../scene.ks']), ('a/../scene.ks', [])]:
            with self.subTest(member=member, names=names), self.assertRaises(PatchPathError):
                canonical_patch_member(member, names)


class SourceIntegrationTests(unittest.TestCase):
    def test_flat_patch_priority_and_gap(self):
        with tempfile.TemporaryDirectory() as temp:
            game = Path(temp)
            write_xp3(game / 'data.xp3', {'scenario/a.ks': b'base'})
            write_xp3(game / 'patch.xp3', {'a.ks': b'patch'})
            write_xp3(game / 'patch2.xp3', {'a.ks': b'patch2'})
            write_xp3(game / 'patch4.xp3', {'a.ks': b'ignored'})
            with DirectorySource(game) as source:
                selected, _, _ = _sources(source, '')
                self.assertEqual(list(selected), ['scenario/a.ks'])
                rel, physical, archive = next(iter(selected.values()))
                self.assertEqual((rel, physical), ('scenario/a.ks', 'a.ks'))
                self.assertEqual(archive.path, 'patch2.xp3')
                self.assertEqual(archive.read(physical), b'patch2')

    def test_flat_config_uses_physical_name_in_prepare(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); game = root / 'game'; game.mkdir()
            (game / 'game.exe').touch()
            config = b';userFace = global.SystemDefaultFontFace;'
            write_xp3(game / 'data.xp3', {'scenario/a.ks': b'hello\n', 'main/Config.tjs': b'old'})
            write_xp3(game / 'patch.xp3', {'Config.tjs': config})
            project = root / 'project'
            prepare(project, {'source': str(game), 'source_language': 'english', 'language': 'korean'})
            manifest = json.loads((project / 'data/kirikiri-manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['font']['path'], 'main/Config.tjs')
            self.assertEqual((project / 'data/kirikiri-source/__rpt_config_snapshot__.tjs').read_bytes(), config)

    def test_ambiguous_base_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            game = Path(temp)
            write_xp3(game / 'data.xp3', {'one/a.ks': b'one', 'two/a.ks': b'two'})
            write_xp3(game / 'patch.xp3', {'a.ks': b'patch'})
            with DirectorySource(game) as source, self.assertRaises(PatchPathError) as caught:
                _sources(source, '')
            self.assertEqual(caught.exception.error_code, 'kirikiri_patch_conflict')


if __name__ == '__main__':
    unittest.main()
