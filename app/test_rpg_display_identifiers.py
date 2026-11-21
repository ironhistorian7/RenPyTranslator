"""Invented identifier/display collisions; no game or model execution."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine import save_json
from gameloc_vendor import load
load()
from gameloc.apply import apply
from gameloc.engines import rpgmaker
from gameloc.extract import extract
from gameloc.model import Entry, Location, Project
from gameloc.treepath import get_by_path
from rpgmaker_catalog_repair import recover_display_entries,reusable_display_records,_row
from rpgmaker_sources import bind_rows
from test_rpgmaker import fixture


class DisplayIdentifierTests(unittest.TestCase):
    def make(self,root,word,variant='MV',www=False):
        base=fixture(root,variant,www=www)
        common=[None,{'id':1,'name':word,'list':[{'code':0,'indent':0,'parameters':[]}]}]
        save_json(base/'data/CommonEvents.json',common)
        actors=json.loads((base/'data/Actors.json').read_text(encoding='utf-8'))
        actors[1]['name']=word;save_json(base/'data/Actors.json',actors)
        system=json.loads((base/'data/System.json').read_text(encoding='utf-8'))
        system['terms']['commands'][0]=word;save_json(base/'data/System.json',system)
        commands=[{'code':101,'indent':0,'parameters':['',0,0,2]+([word] if variant=='MZ' else [])},
                  {'code':401,'indent':0,'parameters':[word]},
                  {'code':102,'indent':0,'parameters':[[word,'Different label'],0,0,2,0]},
                  {'code':355,'indent':0,'parameters':['const setting = '+json.dumps(word)+';']},
                  {'code':357,'indent':0,'parameters':['Synthetic','Synthetic','',{'mode':word,'text':word}]},
                  {'code':0,'indent':0,'parameters':[]}]
        save_json(base/'data/Map001.json',{'displayName':'Camp','events':[None,{'name':'Event001','pages':[{'list':commands}]}]})
        return base,commands

    def test_extraction_protects_locations_not_every_equal_string(self):
        for variant,www,word in (('MV',False,'Pause'),('MZ',True,'休憩')):
            with self.subTest(variant=variant,www=www),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);base,_=self.make(root,word,variant,www)
                upstream=extract(root,dedup=False,source_lang='en' if word=='Pause' else 'ja')
                locations={(_file(e.locations[0].file),e.locations[0].ptr) for e in upstream.entries if e.text==word}
                prefix='www/' if www else ''
                expected={(prefix+'data/System.json','terms.commands[0]'),
                          (prefix+'data/Map001.json','events[1].pages[0].list[1].parameters[0]'),
                          (prefix+'data/Map001.json','events[1].pages[0].list[2].parameters[0][0]')}
                self.assertTrue(expected.issubset(locations))
                self.assertNotIn((prefix+'data/Actors.json','[1].name'),locations)
                self.assertIn(word,upstream.identifiers)
                self.assertFalse(any('parameters[3].text' in pointer for _file_name,pointer in locations))
                self.assertEqual(json.loads((base/'data/CommonEvents.json').read_text(encoding='utf-8'))[1]['name'],word)

    def test_apply_splits_a_deduplicated_entry_into_display_and_functional_locations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);base,commands=self.make(root,'休憩','MZ')
            pointers=[('data/Actors.json','[1].name'),('data/System.json','terms.commands[0]'),
                      ('data/Map001.json','events[1].pages[0].list[1].parameters[0]'),
                      ('data/Map001.json','events[1].pages[0].list[2].parameters[0][0]'),
                      ('data/CommonEvents.json','[1].name'),
                      ('data/Map001.json','events[1].pages[0].list[4].parameters[3].mode')]
            locations=[Location(file=file,kind='loose',fmt='json',ptr=pointer) for file,pointer in pointers]
            upstream=Project(game_root=str(root),engine='rpgmaker',identifiers=['休憩'],
                             entries=[Entry(id='shared',text='休憩',locations=locations,translations={'ko':'쉬기'})])
            report=apply(upstream,'ko',game_root=root,reserved_names={'休憩'})
            self.assertEqual(report.failures,[])
            self.assertEqual(len(report.risky),3)
            for index,(file,pointer) in enumerate(pointers):
                doc=json.loads((root/file).read_text(encoding='utf-8'))
                self.assertEqual(get_by_path(doc,pointer),'쉬기' if 0<index<4 else '休憩')
            final=json.loads((base/'data/Map001.json').read_text(encoding='utf-8'))
            self.assertEqual(final['events'][1]['pages'][0]['list'][3],commands[3])

    def test_repair_metadata_preserves_old_ids_cache_and_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            project=Path(temporary);snapshot=project/'data/rpg-source'
            base,_=self.make(snapshot,'退出','MZ',www=True)
            save_json(base/'data/Locale.json',{'label':'戻る'})
            manifest={p.relative_to(snapshot).as_posix():'synthetic' for p in snapshot.rglob('*') if p.is_file() and p.suffix in ('.json','.js')}
            save_json(project/'data/source-manifest.json',manifest)
            upstream=extract(snapshot,dedup=False,source_lang='ja')
            documents={};rows=[]
            for entry in upstream.entries:
                loc=entry.locations[0]
                entry.id=hashlib.sha256((loc.file+'\0'+loc.ptr+'\0'+entry.text).encode()).hexdigest()[:16]
                rows.append(_row(entry,snapshot,documents,len(rows)+1))
            removed={entry.id for entry in upstream.entries if entry.text=='退出' and rpgmaker.is_display_location(snapshot,entry.locations[0],documents)}
            upstream.entries[:]=[entry for entry in upstream.entries if entry.id not in removed]
            rows[:]=[row for row in rows if row['id'] not in removed]
            old_ids=[row['id'] for row in rows]
            cachefile=project/'data/translations.jsonl';cachefile.write_bytes(b'untouched synthetic cache\n')
            saved_cache=cachefile.read_bytes();saved_map=(base/'data/Map001.json').read_bytes()
            with patch('translation.request',side_effect=AssertionError('No model')):
                report=recover_display_entries(project,{'rpg_base':'www'},rows,upstream)
            self.assertEqual(report['added'],3)
            self.assertEqual(set(report['added_ids']),removed)
            self.assertEqual([row['id'] for row in rows if row['id'] in old_ids],old_ids)
            self.assertEqual(cachefile.read_bytes(),saved_cache)
            self.assertEqual((base/'data/Map001.json').read_bytes(),saved_map)
            class Mapping:
                def resolve(self,value):return {'text':'戻る','language':'ja','provenance':[]} if value=='退出' else None
                def runtime_aliases(self,value):return ['退出','戻る']
            bind_rows(rows,Mapping())
            old=next(row for row in rows if row['source']=='戻る' and row['id'] not in removed)
            known={old['id']:{'id':old['id'],'source':'戻る','text':'돌아가기','status':'translated','model':'synthetic'}}
            records=reusable_display_records(rows,known,report['added_ids'])
            self.assertEqual(len(records),3)
            self.assertTrue(all(record['source']=='戻る' and record['text']=='돌아가기' for record in records))
            self.assertEqual(recover_display_entries(project,{},rows,upstream)['added'],0)

    def test_targeted_repair_and_unknown_locations_remain_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            project=Path(temporary);snapshot=project/'data/rpg-source'
            self.make(snapshot,'休憩')
            save_json(project/'data/source-manifest.json',{p.relative_to(snapshot).as_posix():'synthetic' for p in snapshot.rglob('*.json')})
            upstream=Project(game_root=str(snapshot),engine='rpgmaker',identifiers=['休憩'])
            rows=[]
            self.assertEqual(recover_display_entries(project,{},rows,upstream,only_values={'Other'})['added'],0)
            result=recover_display_entries(project,{},rows,upstream,only_values={'休憩'})
            self.assertEqual(result['added'],3)
            for file,pointer in [('data/CommonEvents.json','[1].name'),
                                 ('data/Map001.json','events[1].pages[0].list[3].parameters[0]'),
                                 ('data/Map001.json','events[1].pages[0].list[4].parameters[3].text')]:
                self.assertFalse(rpgmaker.is_display_location(snapshot,Location(file=file,kind='loose',fmt='json',ptr=pointer)))

    def test_conflicting_spellings_and_failed_outputs_are_not_reused(self):
        rows=[{'id':'a','source':'Caption','usage':'text'},{'id':'b','source':'Caption','usage':'text'},
              {'id':'new','source':'Caption','usage':'choice'}]
        known={'a':{'text':'첫 표기'},'b':{'text':'다른 표기'}}
        self.assertEqual(reusable_display_records(rows,known,['new']),[])
        known['b'].update(status='source_fallback')
        self.assertEqual(reusable_display_records(rows,known,['new'])[0]['text'],'첫 표기')
        known['a']['_rpg_residual_language']=True
        self.assertEqual(reusable_display_records(rows,known,['new']),[])

    def test_guessed_function_keys_and_database_names_are_not_globally_unprotected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);base,commands=self.make(root,'休憩','MZ')
            (base/'data/CommonEvents.json').unlink()
            commands[3]['parameters']=['const setting = "Different key";']
            save_json(base/'data/Map001.json',{'events':[None,{'name':'Event001','pages':[{'list':commands}]}]})
            (base/'js/plugins').mkdir(parents=True)
            (base/'js/plugins/Native.js').write_text('const key = "休憩";',encoding='utf-8')
            save_json(base/'data/CustomData.json',{'value':'休憩'})
            save_json(base/'data/Skills.json',[None,{'name':'休憩','description':'休憩'}])
            system=json.loads((base/'data/System.json').read_text(encoding='utf-8'))
            system['skillTypes']=['休憩'];save_json(base/'data/System.json',system)
            upstream=extract(root,dedup=False,source_lang='ja')
            locations={(e.locations[0].file,e.locations[0].ptr) for e in upstream.entries if e.text=='休憩'}
            self.assertIn(('data/System.json','terms.commands[0]'),locations)
            self.assertIn(('data/Skills.json','[1].description'),locations)
            self.assertNotIn(('data/CustomData.json','value'),locations)
            self.assertNotIn(('data/Skills.json','[1].name'),locations)
            self.assertNotIn(('data/System.json','skillTypes[0]'),locations)
            speaker=Location(file='data/Map001.json',kind='loose',fmt='json',ptr='events[1].pages[0].list[0].parameters[4]')
            self.assertTrue(rpgmaker.is_display_location(root,speaker))


def _file(value):return value.replace('\\','/')


if __name__=='__main__':unittest.main()
