"""Purpose metadata and saved-output recovery, using invented local fixtures only."""
from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine import save_json
from gameloc_vendor import load
load()
from gameloc.model import Entry,Location,Project
from rpgmaker_sources import refresh_usages,refresh
from rpgmaker_saved_repair import saved_records


def row(uid,source='Synthetic source',*,file='data/Bundle.json',ptr='entries.KEY.ja',usage='ui_label',kind='string'):
    return dict(id=uid,source=source,file=file,kind=kind,usage=usage,block='labels',
                location=dict(file=file,kind='loose',fmt='json',ptr=ptr))


class RPGMakerUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.project=self.root/'project'
        (self.project/'data').mkdir(parents=True)
        self.cfg=dict(engine='rpgmaker',source_language='japanese',model='synthetic',language='korean',num_ctx=16384)

    def test_unknown_text_and_locale_tables_do_not_become_ui_labels(self):
        rows=[row('en','An invented complete sentence',ptr='entries.KEY.en'),
              row('ja','合成の例文',ptr='entries.KEY.ja'),
              row('custom','A custom paragraph',file='custom/Other.csv',ptr='[0][1]'),
              row('unsafe','A source value',ptr='entries.literal[not-an-index].ja')]
        before=deepcopy(rows)
        self.assertEqual(refresh_usages(rows),4)
        self.assertTrue(all(r['usage']=='text' for r in rows))
        for original,changed in zip(before,rows):
            original['usage']='text';self.assertEqual(changed,original)
        self.assertEqual(refresh_usages(rows),0)

    def test_standard_menu_names_and_sentence_fields_keep_distinct_roles(self):
        cases=[('data/System.json','terms.commands[0]','ui_label'),
               ('www/data/System.json','terms.basic[0]','ui_label'),
               ('data/System.json','terms.messages.actionFailure','text'),
               ('data/Actors.json','[1].name','speaker_label'),
               ('data/Actors.json','[1].profile','text'),
               ('data/Items.json','[1].name','ui_label'),
               ('data/Items.json','[1].description','text'),
               ('data/Map001.json','displayName','ui_label'),
               ('custom/System.json','terms.commands[0]','text')]
        rows=[row(str(index),file=file,ptr=ptr) for index,(file,ptr,_) in enumerate(cases)]
        refresh_usages(rows)
        self.assertEqual([r['usage'] for r in rows],[expected for _,_,expected in cases])

    def test_proven_event_roles_survive_metadata_refresh(self):
        rows=[row('dialogue',kind='dialogue'),row('choice',usage='choice'),
              row('speaker',usage='speaker_label')]
        refresh_usages(rows)
        self.assertEqual([r['usage'] for r in rows],['dialogue','choice','speaker_label'])

    def test_existing_refresh_changes_metadata_without_touching_cache_or_ids(self):
        snapshot=self.project/'data/rpg-source';(snapshot/'data').mkdir(parents=True)
        save_json(snapshot/'data/Bundle.json',{'entries':{'KEY':{'ja':'合成の例文'}}})
        save_json(self.project/'data/source-manifest.json',{'data/Bundle.json':'synthetic manifest'})
        original=row('stable-id','合成の例文');save_json(self.project/'data/catalog.json',[original])
        cache=self.project/'data/translations.jsonl';cache.write_bytes(b'synthetic untouched bytes\r\n')
        with patch('rpgmaker_sources.copy_declared_tables',side_effect=AssertionError('No original access')):
            report=refresh(self.project,self.cfg)
        actual=json.loads((self.project/'data/catalog.json').read_text(encoding='utf-8'))[0]
        original['usage']='text';self.assertEqual(actual,original)
        self.assertEqual(report['usage_updates'],1)
        self.assertEqual(cache.read_bytes(),b'synthetic untouched bytes\r\n')

    def test_saved_korean_response_is_reused_after_purpose_correction(self):
        from failed_repair import saved_output
        original=row('saved','合成の例文');candidate='합성 문장입니다.'
        error="saved: ['label expanded into dialogue or explanation (added sentence)']; output="+repr(candidate)
        self.assertIsNone(saved_output(original['source'],error,self.cfg,'ui_label'))
        save_json(self.project/'data/failed-items.json',[dict(row=deepcopy(original),error=error)])
        refresh_usages([original]);known={'saved':dict(id='saved',source=original['source'],text=original['source'],status='source_fallback')}
        with patch('failed_repair.saved_output',wraps=saved_output) as validate:
            records,remaining=saved_records(self.project,self.cfg,{'saved':original},known)
        self.assertEqual(validate.call_count,1)
        self.assertEqual(records[0]['text'],candidate)
        self.assertEqual(records[0]['status'],'failure_recovered')
        self.assertEqual(remaining,[])
        self.assertEqual(known['saved']['status'],'source_fallback')

    def test_unresolved_japanese_and_stale_ids_are_not_falsely_recovered(self):
        original=row('residual','合成の例文',usage='text')
        failures=[dict(row=deepcopy(original),error="untranslated Japanese text; output='합성 テスト'"),
                  dict(row=row('unknown','Other source'),error="output='한국어'"),
                  dict(row=row('stale','Previous source'),error="error; output='한국어 문장입니다.'")]
        save_json(self.project/'data/failed-items.json',failures)
        records,remaining=saved_records(self.project,self.cfg,{'residual':original,'stale':row('stale','Current source')},{})
        self.assertEqual(records,[]);self.assertIsNone(remaining)

    def test_seed_recovery_appends_once_and_preserves_completed_records(self):
        from failed_repair import saved_output
        from rpgmaker_project import seed_defaults
        from translation import fingerprint
        rows=[row('done','完成した例',usage='text'),row('saved','合成の例文',usage='text')]
        save_json(self.project/'data/catalog.json',rows)
        upstream=Project('synthetic',engine='rpgmaker',entries=[
            Entry(r['id'],r['source'],[Location.from_dict(r['location'])]) for r in rows])
        upstream.save(self.project/'data/rpgmaker-project.json')
        candidate='합성 문장입니다.';error="label expanded into dialogue or explanation (added sentence); output="+repr(candidate)
        existing=[dict(id='done',source=rows[0]['source'],text='기존 번역',model='synthetic',fingerprint=fingerprint(self.cfg)),
                  dict(id='saved',source=rows[1]['source'],text=rows[1]['source'],model='synthetic',fingerprint=fingerprint(self.cfg),status='source_fallback',error=error)]
        cache=self.project/'data/translations.jsonl'
        before=''.join(json.dumps(record,ensure_ascii=False)+'\n' for record in existing).encode('utf-8');cache.write_bytes(before)
        save_json(self.project/'data/failed-items.json',[dict(row=rows[1],error=error)])
        with patch('failed_repair.saved_output',wraps=saved_output) as validate,contextlib.redirect_stdout(io.StringIO()):
            seed_defaults(self.project,self.cfg,recover_saved=True)
            first=cache.read_bytes()
            seed_defaults(self.project,self.cfg,recover_saved=True)
        self.assertEqual(validate.call_count,1)
        self.assertTrue(first.startswith(before));self.assertEqual(cache.read_bytes(),first)
        records=[json.loads(line) for line in first.decode().splitlines()]
        self.assertEqual(len(records),3);self.assertEqual(records[-1]['id'],'saved')
        self.assertEqual(records[-1]['text'],candidate)
        self.assertEqual(json.loads((self.project/'data/failed-items.json').read_text(encoding='utf-8')),[])


if __name__=='__main__':unittest.main()
