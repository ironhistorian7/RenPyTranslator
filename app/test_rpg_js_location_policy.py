"""Synthetic JS location and RPG display policy tests; no game execution."""
import json
from pathlib import Path
import tempfile
import unittest

from gameloc_vendor import load
load()
from gameloc.apply import _patch_js, apply
from gameloc.engines import rpgmaker
from gameloc.model import Entry, Location, Project


class JsLocationPolicyTests(unittest.TestCase):
    def test_exact_locations_and_legacy_never_change_keys(self):
        value = '長い共通の文章です。'
        source = (f'const x = {{"{value}": 1}};\n'
                  f'if (mode === "{value}") {{}}\n'
                  f'window.drawText("{value}", 0, 0);\n'
                  f'window.drawText("{value}", 1, 0);\n'
                  'window.drawText("戻る", 0, 0);\n')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); plugins = root / 'js/plugins'; plugins.mkdir(parents=True)
            path = plugins / 'Synthetic.js'; path.write_text(source, encoding='utf-8')
            found = rpgmaker.extract_plugin_scripts(root, root)
            matching = [loc for text, loc in found if text == value]
            self.assertEqual(len(matching), 2)
            self.assertTrue(any(text == '戻る' for text, _ in found))
            loc = Location.from_dict(matching[0].to_dict())
            self.assertEqual(_patch_js(path, root, {loc.ptr: '번역'}, False), 1)
            actual = path.read_text(encoding='utf-8')
            self.assertEqual(actual.count(value), 3)
            self.assertIn(f'{{"{value}": 1}}', actual)
            self.assertIn(f'mode === "{value}"', actual)
            path.write_text(source, encoding='utf-8')
            self.assertEqual(_patch_js(path, root, {value: '번역'}, False), 2)
            actual = path.read_text(encoding='utf-8')
            self.assertIn(f'{{"{value}": 1}}', actual)
            self.assertIn(f'mode === "{value}"', actual)
            path.write_text(' ' + source, encoding='utf-8')
            self.assertEqual(_patch_js(path, root, {loc.ptr: '번역'}, False), 0)

    def test_ternary_display_values_are_not_object_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); path = root / 'js/plugins/Synthetic.js'; path.parent.mkdir(parents=True)
            source = ('const lookup = {/* key */ "表示する長い文章です。" /* separator */ : 1};\n'
                      'window.drawText(ok ? "表示する長い文章です。" : "別の長い文章です。", 0, 0);')
            path.write_text(source, encoding='utf-8')
            found = rpgmaker.extract_plugin_scripts(root, root)
            self.assertEqual([text for text, _ in found], ['表示する長い文章です。', '別の長い文章です。'])
            edits = {loc.ptr: '번역' for _, loc in found}
            self.assertEqual(_patch_js(path, root, edits, False), 2)
            actual = path.read_text(encoding='utf-8')
            self.assertIn('{/* key */ "表示する長い文章です。" /* separator */ : 1}', actual)
            self.assertIn('ok ? "번역" : "번역"', actual)

    def test_apply_allows_display_occurrence_when_value_is_reserved(self):
        value = '長い共通の文章です。'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); path = root / 'js/plugins/Synthetic.js'; path.parent.mkdir(parents=True)
            source = f'const obj = {{"{value}": 1}}; window.drawText("{value}", 0, 0);'
            path.write_text(source, encoding='utf-8')
            locations = [loc for text, loc in rpgmaker.extract_plugin_scripts(root, root) if text == value]
            project = Project(game_root=str(root), engine='rpgmaker', identifiers=[value], entries=[
                Entry(id='x', text=value, locations=locations, translations={'ko': '번역'})])
            report = apply(project, 'ko', game_root=root, reserved_names={value})
            self.assertEqual(report.failures, [])
            self.assertEqual(report.strings_written, 1)
            self.assertIn(f'{{"{value}": 1}}', path.read_text(encoding='utf-8'))
            self.assertIn('drawText("번역"', path.read_text(encoding='utf-8'))

    def test_identifier_shape_is_checked_at_each_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); data = root / 'data'; data.mkdir()
            path = data / 'System.json'
            path.write_text(json.dumps({'terms': {'commands': ['Hero_Name']}}), encoding='utf-8')
            locations = [Location(file='data/System.json', kind='loose', fmt='json', ptr='terms.commands[0]'),
                         Location(file='data/System.json', kind='loose', fmt='json', ptr='switches[0]')]
            project = Project(game_root=str(root), engine='rpgmaker', entries=[
                Entry(id='x', text='Hero_Name', locations=locations, translations={'ko': '영웅'})])
            report = apply(project, 'ko', game_root=root, reserved_names={'Hero_Name'})
            self.assertEqual(report.failures, [])
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['terms']['commands'][0], '영웅')
            self.assertEqual(len(report.risky), 1)

    def test_project_roundtrip_and_bad_pointer_rejected(self):
        loc = Location(file='js/plugins/Synthetic.js', kind='jsliteral', fmt='jsliteral',
                       ptr='jspos:[0,"다른 문자열입니다."]')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); path = root / loc.file; path.parent.mkdir(parents=True)
            path.write_text('"다른 문자열입니다."', encoding='utf-8')
            project = Project(game_root=str(root), engine='rpgmaker', entries=[Entry(
                id='x', text='원래 문자열입니다.', locations=[loc], translations={'ko': '번역'})])
            project_path = root / "project.json"; project.save(project_path)
            restored = Project.load(project_path)
            report = apply(restored, 'ko', game_root=root, reserved_names=set())
            self.assertEqual(report.files_written, 0)
            self.assertEqual(path.read_text(encoding='utf-8'), '"다른 문자열입니다."')
            self.assertEqual(len(report.risky), 1)

if __name__ == '__main__':
    unittest.main()
