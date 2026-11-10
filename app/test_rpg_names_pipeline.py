"""Existing-task routing on invented RPG names; no real game or model access."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine import save_json
from automatic import source_key
import rpgmaker_project as adapter
from test_rpgmaker import fixture
from translation import fingerprint,protect,read_catalog


class RPGNamesPipelineTests(unittest.TestCase):
    def scenario(self,variant,www,language,legacy=False):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=Path(temporary.name);source=root/'source';project=root/'projects/Names'
        (project/'data').mkdir(parents=True);base=fixture(source,variant,www)
        name='ミラ' if language=='japanese' else 'Mira'
        actors=json.loads((base/'data/Actors.json').read_text(encoding='utf-8'))
        actors[1]['name']=name;save_json(base/'data/Actors.json',actors)
        document=json.loads((base/'data/Map001.json').read_text(encoding='utf-8'))
        commands=document['events'][1]['pages'][0]['list']
        if variant=='MZ':commands[0]['parameters'][4]=name
        commands[-1:-1]=[dict(code=320,indent=0,parameters=[1,name]),dict(code=324,indent=0,parameters=[1,name])]
        save_json(base/'data/Map001.json',document)
        cfg=dict(source=str(source),engine='rpgmaker',source_language=language,language='korean',
                 model='synthetic',num_ctx=4096,parallel=1,batch_size=8,context_txt_enabled=False,
                 output_root=str(root/'output'))
        with contextlib.redirect_stdout(io.StringIO()):adapter.prepare(project,cfg)
        rows=read_catalog(project);records=[]
        for row in rows:
            _,tokens=protect(row['source'],cfg);text='합성 번역'+''.join(tokens)
            if row['file'].endswith('Actors.json') and row['location']['ptr']=='[1].name':text='정식 이름'
            elif row.get('rpg_command_code')==324:text='독립 별명'
            elif row['usage']=='speaker_label':text='틀린 이름'
            records.append(dict(id=row['id'],source=row['source'],text=text,model=cfg['model'],fingerprint=fingerprint(cfg)))
        cache=project/'data/translations.jsonl'
        cache.write_text(''.join(json.dumps(item,ensure_ascii=False)+'\n' for item in records),encoding='utf-8')
        if legacy:
            for row in rows:row.pop('rpg_command_code',None)
            save_json(project/'data/catalog.json',rows)
        save_json(root/'projects/index.json',{source_key(source.resolve()):project.name})
        return root,source,project,cfg,base.relative_to(source),cache.read_bytes()

    def test_existing_checkbox_task_repairs_legacy_names_without_a_model(self):
        from translate_game import main
        from patch_validation import run as check_patch
        for variant,www,language,use_source in (('MZ',False,'japanese',False),('MV',True,'english',True)):
            with self.subTest(variant=variant,www=www,source=use_source):
                root,source,project,cfg,base,before=self.scenario(variant,www,language,legacy=True)
                ids=[row['id'] for row in read_catalog(project)]
                original=(source/base/'data/Actors.json').read_bytes()
                with patch('automatic.ROOT',root),patch('automatic.settings',return_value=dict(cfg)), \
                     patch('model_store.require_selected',side_effect=AssertionError('No model selection')), \
                     patch('gpu_policy.configure',side_effect=AssertionError('No GPU setup')), \
                     patch('translation.translate',side_effect=AssertionError('No translation')), \
                     patch('rpgmaker_project.seed_defaults',side_effect=AssertionError('Names only, no menu changes')), \
                     patch('translation.request',side_effect=AssertionError('No inference')), \
                     patch('patch_validation.run',wraps=check_patch) as checked, \
                     contextlib.redirect_stdout(io.StringIO()):
                    main(['tasks','--tasks','names','--source' if use_source else '--project',str(source if use_source else project)])
                checked.assert_called_once()
                self.assertEqual((project/'data/translations.jsonl').read_bytes(),before)
                self.assertEqual((source/base/'data/Actors.json').read_bytes(),original)
                self.assertEqual([row['id'] for row in read_catalog(project)],ids)
                final=json.loads((project/'staging'/base/'data/Map001.json').read_text(encoding='utf-8'))
                commands=final['events'][1]['pages'][0]['list']
                if variant=='MZ':self.assertEqual(commands[0]['parameters'][4],'정식 이름')
                self.assertEqual(next(c for c in commands if c['code']==320)['parameters'][1],'정식 이름')
                self.assertEqual(next(c for c in commands if c['code']==324)['parameters'][1],'독립 별명')
                report=json.loads((project/'data/rpg-name-report.json').read_text(encoding='utf-8'))
                self.assertTrue(report['updated_ids']);self.assertFalse(report['cache_modified'])

    def test_standalone_check_uses_the_same_name_overlay(self):
        from patch_validation import run as check_patch
        _,_,project,cfg,_,before=self.scenario('MZ',False,'japanese')
        with patch('translation.request',side_effect=AssertionError('No inference')),contextlib.redirect_stdout(io.StringIO()):
            adapter.render(project,cfg)
            fresh={key:value for key,value in cfg.items() if not key.startswith('_rpg_render')}
            result=check_patch(project,fresh,mode='static')
        self.assertEqual(result['status'],'passed',result.get('failures'))
        self.assertEqual((project/'data/translations.jsonl').read_bytes(),before)


if __name__=='__main__':unittest.main()
