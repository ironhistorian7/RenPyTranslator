"""Prepared-project migrations on small invented data; no game or model runs."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine import save_json
from test_rpgmaker import fixture
import rpgmaker_project as adapter
from translation import cache,fingerprint,read_catalog
from gameloc_vendor import load


class MigrationTests(unittest.TestCase):
    def scenario(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=Path(temporary.name);source=root/'source';project=root/'project'
        (project/'data').mkdir(parents=True);base=fixture(source,'MZ')
        path=base/'data/Map001.json';document=json.loads(path.read_text(encoding='utf-8'))
        document['events'][1]['pages'][0]['list'][3]['parameters'][0][0]='戻る'
        save_json(path,document)
        actors=json.loads((base/'data/Actors.json').read_text(encoding='utf-8'))
        actors[1]['classId']=0;save_json(base/'data/Actors.json',actors)
        save_json(base/'data/Classes.json',[None,{'id':1,'name':'Example class','expParams':[30,20,30,30]}])
        cfg=dict(source=str(source),engine='rpgmaker',source_language='japanese',language='korean',
                 model='synthetic',num_ctx=4096,parallel=1,batch_size=8,context_txt_enabled=False,
                 output_root=str(root/'output'))
        with redirect_stdout(io.StringIO()):adapter.prepare(project,cfg)
        return project,cfg

    def test_old_display_omission_is_recovered_once_and_prefilled_without_model(self):
        load()
        from gameloc.model import Project
        project,cfg=self.scenario();rows=read_catalog(project)
        omitted={row['id'] for row in rows if row['source']=='戻る'}
        self.assertTrue(omitted)
        kept=[row for row in rows if row['id'] not in omitted]
        save_json(project/'data/catalog.json',kept)
        upstream=Project.load(project/'data/rpgmaker-project.json')
        upstream.entries=[entry for entry in upstream.entries if entry.id not in omitted]
        upstream.identifiers=list(set(upstream.identifiers)|{'戻る'})
        upstream.save(project/'data/rpgmaker-project.json')
        save_json(project/'data/catalog-format.json',{'engine':'rpgmaker','version':1})
        original=''.join(json.dumps(dict(id=row['id'],source=row['source'],text='합성 번역',
                                        model=cfg['model'],fingerprint=fingerprint(cfg)),ensure_ascii=False)+'\n' for row in kept)
        path=project/'data/translations.jsonl';path.write_text(original,encoding='utf-8')
        with (patch('translation.request',side_effect=AssertionError('No model')),
              redirect_stdout(io.StringIO())):
            report=adapter.recover_display_catalog(project,cfg)
            adapter.seed_defaults(project,cfg,recovered_display_ids=report['added_ids'])
        self.assertEqual(set(report['added_ids']),omitted)
        self.assertEqual([row['id'] for row in read_catalog(project) if row['id'] not in omitted],[row['id'] for row in kept])
        self.assertTrue(path.read_text(encoding='utf-8').startswith(original))
        self.assertTrue(all(cache(project,cfg)[uid]['text']=='돌아가기' for uid in omitted))
        with patch('rpgmaker_catalog_repair.recover_display_entries',side_effect=AssertionError('Do not re-extract')):
            self.assertTrue(adapter.recover_display_catalog(project,cfg)['already_current'])

    def test_declared_class_fix_survives_render_and_static_comparison(self):
        from patch_validation import run as check_patch
        project,cfg=self.scenario();rows=read_catalog(project)
        before=(project/'data/rpg-source/data/Actors.json').read_bytes()
        path=project/'data/translations.jsonl'
        path.write_text(''.join(json.dumps(dict(id=row['id'],source=row['source'],text=row['source'],
                                               status='source_fallback',model=cfg['model'],fingerprint=fingerprint(cfg)),ensure_ascii=False)+'\n' for row in rows),encoding='utf-8')
        cfg['rpg_actor_class_overrides']={'1':1}
        with redirect_stdout(io.StringIO()),patch('translation.request',side_effect=AssertionError('No model')):
            adapter.render(project,cfg)
            result=check_patch(project,cfg,mode='static')
        self.assertEqual(result['status'],'passed',result.get('failures'))
        self.assertEqual(json.loads((project/'staging/data/Actors.json').read_text(encoding='utf-8'))[1]['classId'],1)
        self.assertEqual((project/'data/rpg-source/data/Actors.json').read_bytes(),before)
        self.assertTrue(result.get('manual_repairs'))


if __name__=='__main__':unittest.main()
