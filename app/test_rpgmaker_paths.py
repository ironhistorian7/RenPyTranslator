"""Synthetic saved-project path compatibility checks; no game/model/cache access."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gameloc_vendor import load
load()
from gameloc import jsonnest
from gameloc.model import Entry,Location,Project
from rpgmaker_paths import normalize_locations,PATH_FORMAT


class RPGMakerPathTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.snapshot=Path(self.temp.name)

    def document(self,relative,node,plugins=False):
        path=self.snapshot/relative
        path.parent.mkdir(parents=True,exist_ok=True)
        text=json.dumps(node,ensure_ascii=False)
        path.write_text('var $plugins = '+text+';' if plugins else text,encoding='utf-8')
        return path

    def project(self,locations,stats=None):
        return Project('synthetic-root',engine='rpgmaker',stats=stats or {},entries=[
            Entry('stable-id-'+str(i),'synthetic source',[loc],translations={'ko':'합성 번역'})
            for i,loc in enumerate(locations)])

    def test_special_literal_keys_keep_ids_translations_and_snapshot(self):
        keys=['plain','a.b','part[3]','!','say"hello','folder\\name','','line\nkey']
        path=self.document('data/Custom.json',{key:'synthetic' for key in keys})
        before=path.read_bytes()
        locations=[Location('data/Custom.json','loose','json',key) for key in keys]
        project=self.project(locations)
        ids=[entry.id for entry in project.entries]
        self.assertEqual(normalize_locations(project,self.snapshot),len(keys)-1)
        self.assertEqual([entry.id for entry in project.entries],ids)
        self.assertTrue(all(entry.translations=={'ko':'합성 번역'} for entry in project.entries))
        self.assertEqual([loc.ptr for loc in locations],[jsonnest.format_path([key]) for key in keys])
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(project.game_root,'synthetic-root')

    def test_multiline_span_uses_the_same_structural_mapping(self):
        self.document('data/Custom.json',{'group.name':[{'text':'one'},{'text':'two'}]})
        loc=Location('data/Custom.json','loose','json','group.name[0].text',
                     span=['group.name[0].text','group.name[1].text'])
        self.assertEqual(normalize_locations(self.project([loc]),self.snapshot),1)
        self.assertEqual(loc.ptr,'["group.name"][0].text')
        self.assertEqual(loc.span,['["group.name"][0].text','["group.name"][1].text'])

    def test_plugin_json_unwrap_and_literal_bang_are_distinct(self):
        node=[{'parameters':{'bundle':json.dumps({'display.name':'one','!':'two'})}}]
        self.document('js/plugins.js',node,plugins=True)
        locs=[Location('js/plugins.js','loose','rmplugins','[0].parameters.bundle!display.name'),
              Location('js/plugins.js','loose','rmplugins','[0].parameters.bundle!!')]
        normalize_locations(self.project(locs),self.snapshot)
        self.assertEqual([loc.ptr for loc in locs],
                         ['[0].parameters.bundle!["display.name"]','[0].parameters.bundle!["!"]'])
        self.assertEqual([jsonnest.get(node,loc.ptr) for loc in locs],['one','two'])

    def test_json_plugin_command_can_point_inside_a_json_string(self):
        node=[{'parameters':['','','',{'bundle':json.dumps({'display.name':'one'})}]}]
        self.document('data/Events.json',node)
        loc=Location('data/Events.json','loose','json','[0].parameters[3].bundle!display.name')
        normalize_locations(self.project([loc]),self.snapshot)
        self.assertEqual(loc.ptr,'[0].parameters[3].bundle!["display.name"]')
        self.assertEqual(jsonnest.get(node,loc.ptr),'one')

    def test_ambiguous_legacy_address_fails_without_partial_updates(self):
        self.document('data/First.json',{'other.key':'one'})
        self.document('data/Ambiguous.json',{'a.b':'same','a':{'b':'same'}})
        locs=[Location('data/First.json','loose','json','other.key'),
              Location('data/Ambiguous.json','loose','json','a.b')]
        project=self.project(locs)
        with self.assertRaisesRegex(ValueError,'여러 위치'):
            normalize_locations(project,self.snapshot)
        self.assertEqual([loc.ptr for loc in locs],['other.key','a.b'])
        self.assertNotIn('rpt_path_format',project.stats)

    def test_missing_address_never_searches_matching_source_text(self):
        self.document('data/Custom.json',{'existing':'synthetic source'})
        loc=Location('data/Custom.json','loose','json','missing')
        with self.assertRaisesRegex(ValueError,'위치를 찾을 수 없습니다'):
            normalize_locations(self.project([loc]),self.snapshot)
        self.assertEqual(loc.ptr,'missing')

    def test_new_format_skips_legacy_ambiguity_and_snapshot_reads(self):
        # Fresh extraction can safely distinguish nested a.b and literal ["a.b"].
        locs=[Location('data/NoRead.json','loose','json','a.b'),
              Location('data/NoRead.json','loose','json','["a.b"]')]
        project=self.project(locs,{'rpt_path_format':PATH_FORMAT})
        with patch.object(Path,'read_text',side_effect=AssertionError('No snapshot read')):
            self.assertEqual(normalize_locations(project,self.snapshot),0)
        self.assertEqual([loc.ptr for loc in locs],['a.b','["a.b"]'])

    def test_other_formats_are_not_json_migrated(self):
        loc=Location('notes.txt','loose','lines','[2]')
        with patch.object(Path,'read_text',side_effect=AssertionError('No snapshot read')):
            self.assertEqual(normalize_locations(self.project([loc]),self.snapshot),0)
        self.assertEqual(loc.ptr,'[2]')

    def test_referenced_snapshot_file_is_read_once(self):
        self.document('data/Custom.json',{'first.key':'one','second.key':'two'})
        locs=[Location('data/Custom.json','loose','json',key) for key in ('first.key','second.key')]
        read_text=Path.read_text
        with patch.object(Path,'read_text',autospec=True,side_effect=read_text) as reads:
            normalize_locations(self.project(locs),self.snapshot)
        self.assertEqual(reads.call_count,1)

    def test_snapshot_escape_is_rejected_before_reading(self):
        loc=Location('../outside.json','loose','json','key')
        with patch.object(Path,'read_text',side_effect=AssertionError('No outside read')):
            with self.assertRaisesRegex(ValueError,'폴더 밖'):
                normalize_locations(self.project([loc]),self.snapshot)


if __name__=='__main__':unittest.main()
