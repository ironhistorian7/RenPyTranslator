"""Invented single/multilingual data; never launch a game or a local model."""
import contextlib
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine import save_json
import rpgmaker_project as adapter
from rpgmaker_sources import refresh
from test_rpgmaker import fixture
from translation import cache, fingerprint, protect, read_catalog


class SourceIntegrationTests(unittest.TestCase):
    def scenario(self, language, variant='MZ', native=False, reference=True, *, www=False, actor_name=None):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=Path(temporary.name);source=root/'game';project=root/'project'
        (project/'data').mkdir(parents=True);base=fixture(source,variant,www=www)
        if actor_name is not None:
            path=base/'data/Actors.json';actors=json.loads(path.read_text(encoding='utf-8'))
            actors[1]['name']=actor_name;save_json(path,actors)
            path=base/'data/Map001.json';document=json.loads(path.read_text(encoding='utf-8'))
            if variant=='MZ':document['events'][1]['pages'][0]['list'][0]['parameters'][4]=actor_name
            save_json(path,document)
        table={}
        if language=='japanese' and not native:
            path=base/'data/Map001.json';document=json.loads(path.read_text(encoding='utf-8'))
            commands=document['events'][1]['pages'][0]['list']
            commands[1]['parameters'][0]='こんにちは、\\N[1]！'
            commands[2]['parameters'][0]='\\V[2]枚あります。'
            commands[3]['parameters'][0]=['帰る','待つ']
            commands[4]['parameters'][1]='帰る';commands[7]['parameters'][1]='待つ'
            save_json(path,document)
        if native:
            table={'Hello, \\N[1]!':'こんにちは、\\N[1]！',
                   'We have \\V[2] coins.':'\\V[2]枚あります。',
                   'Go home':'帰る','Stay':'待つ','Alice':'アリス'}
            plugins=[dict(name='MultiLanguageTranslation',status=True,
                          parameters=dict(startupLanguage='ja',translationFile='locale/display.csv'))]
            (base/'js/plugins.js').write_text('var $plugins = '+json.dumps(plugins)+';',encoding='utf-8')
            (base/'locale').mkdir()
            with (base/'locale/display.csv').open('w',encoding='utf-8',newline='') as stream:
                writer=csv.writer(stream);writer.writerow(['source','translation']);writer.writerows(table.items())
        cfg=dict(source=str(source),engine='rpgmaker',source_language=language,
                 language='korean',model='synthetic',num_ctx=4096,batch_size=1,
                 parallel=1,context_txt_enabled=False,show_reference=reference)
        with contextlib.redirect_stdout(io.StringIO()):adapter.prepare(project,cfg)
        return source,project,cfg,read_catalog(project),table

    def populate(self,project,cfg,rows,old_base=False,fallback=None):
        records=[]
        for row in rows:
            original=row.get('base_source',row['source']) if old_base else row['source']
            _,tokens=protect(original,cfg)
            kept=row['source']==fallback
            item=dict(id=row['id'],source=original,text=original if kept else '합성 번역'+''.join(tokens),
                      model='synthetic',fingerprint=fingerprint(cfg))
            if kept:item.update(status='source_fallback',error='synthetic prior failure')
            records.append(item)
        path=project/'data/translations.jsonl'
        path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records),encoding='utf-8')
        return path.read_bytes()

    def test_single_language_english_and_japanese_mv_mz(self):
        for language in ('english','japanese'):
            for variant in ('MV','MZ'):
                with self.subTest(language=language,variant=variant):
                    _,project,cfg,rows,_=self.scenario(language,variant)
                    self.assertFalse(any('base_source' in row for row in rows))
                    before=self.populate(project,cfg,rows)
                    with patch('translation.request',side_effect=AssertionError('No model')), \
                         contextlib.redirect_stdout(io.StringIO()):adapter.render(project,cfg)
                    self.assertEqual((project/'data/translations.jsonl').read_bytes(),before)
                    report=json.loads((project/'data/rpg-output.json').read_text(encoding='utf-8'))
                    output=json.loads((Path(report['output'])/'data/Map001.json').read_text(encoding='utf-8'))
                    commands=output['events'][1]['pages'][0]['list']
                    self.assertTrue(commands[1]['_rpt_translation'].startswith('합성 번역'))
                    self.assertEqual(commands[3]['_rpt_choice_translations'],commands[3]['parameters'][0])

    def test_explicit_native_table_is_selected_without_changing_ids_or_saved_korean(self):
        _,project,cfg,rows,_=self.scenario('japanese',native=True)
        mapped=[r for r in rows if r.get('base_source')]
        self.assertTrue(mapped)
        self.assertTrue(any(r['source']=='帰る' and r['base_source']=='Go home' for r in mapped))
        self.assertTrue((project/'data/rpg-source/locale/display.csv').is_file())
        ids=[r['id'] for r in rows]
        before=self.populate(project,cfg,rows,old_base=True,fallback='待つ')
        known=cache(project,cfg)
        good=next(r for r in rows if r['source']=='帰る')
        self.assertEqual(known[good['id']]['text'],'합성 번역')
        self.assertEqual(known[good['id']]['source'],'帰る')
        with patch('translation.request',side_effect=AssertionError('No model')), \
             contextlib.redirect_stdout(io.StringIO()):
            refresh(project,cfg)
            adapter.render(project,cfg)
        self.assertEqual(ids,[r['id'] for r in read_catalog(project)])
        self.assertEqual((project/'data/translations.jsonl').read_bytes(),before)
        report=json.loads((project/'data/rpg-output.json').read_text(encoding='utf-8'))
        output=json.loads((Path(report['output'])/'data/Map001.json').read_text(encoding='utf-8'))
        choice=output['events'][1]['pages'][0]['list'][3]
        self.assertEqual(choice['_rpt_choice_originals'],['帰る','待つ'])
        self.assertEqual(choice['_rpt_choice_translations'],['합성 번역','待つ'])
        plugin=(Path(report['output'])/'js/plugins/RPT_Bilingual.js').read_text(encoding='utf-8')
        options=json.loads(plugin.split('var RPT_BilingualOptions = ',1)[1].split(';\n',1)[0])
        self.assertEqual(options['originalActors']['1'],'アリス')
        from patch_validation import run as check_patch
        with patch('translation.request',side_effect=AssertionError('No model')), \
             patch('translation.cache',side_effect=AssertionError('Reuse the rendered cache')), \
             patch('translation.validate_entry',side_effect=AssertionError('Reuse validated output')), \
             contextlib.redirect_stdout(io.StringIO()):result=check_patch(project,cfg,mode='static')
        self.assertEqual(result['status'],'passed',result.get('failures'))
        self.assertEqual(result['coverage']['runtime'],'not_run')

    def test_unchanged_base_output_is_pending_after_explicit_locale_binding(self):
        _,project,cfg,rows,_=self.scenario('japanese',native=True)
        self.populate(project,cfg,rows,old_base=True,fallback='帰る')
        path=project/'data/translations.jsonl'
        records=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        row=next(r for r in rows if r['source']=='帰る')
        for record in records:
            if record['id']==row['id']:record.pop('status',None)
        path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records),encoding='utf-8')
        before=path.read_bytes();known=cache(project,cfg)
        self.assertEqual(known[row['id']]['status'],'source_fallback')
        self.assertEqual(known[row['id']]['text'],'帰る')
        self.assertEqual(path.read_bytes(),before)

    def test_native_locale_does_not_override_selected_english_and_reference_off(self):
        _,project,cfg,rows,_=self.scenario('english',native=True,reference=False)
        self.assertTrue(any(r['source']=='Go home' for r in rows))
        before=self.populate(project,cfg,rows)
        with patch('translation.request',side_effect=AssertionError('No model')), \
             contextlib.redirect_stdout(io.StringIO()):adapter.render(project,cfg)
        report=json.loads((project/'data/rpg-output.json').read_text(encoding='utf-8'))
        document=json.loads((Path(report['output'])/'data/Map001.json').read_text(encoding='utf-8'))
        commands=document['events'][1]['pages'][0]['list']
        self.assertIn('_rpt_translation',commands[1]);self.assertNotIn('_rpt_original',commands[1])
        self.assertIn('_rpt_choice_translations',commands[3]);self.assertNotIn('_rpt_choice_originals',commands[3])
        self.assertEqual((project/'data/translations.jsonl').read_bytes(),before)

    def test_rerender_ignores_generated_identifiers_and_reuses_source_guards(self):
        from gameloc.apply import _reserved_names
        from patch_validation import run as check_patch
        for variant,www,native in (('MZ',False,True),('MV',True,False)):
            with self.subTest(variant=variant,www=www,native=native):
                _,project,cfg,rows,_=self.scenario('japanese',variant,native,www=www,actor_name='ミナト')
                before=self.populate(project,cfg,rows)
                base=Path('www') if www else Path('.')
                # Native plugin identifiers must remain protected; generated
                # plugins of any name must not influence that source decision.
                snapshot=project/'data/rpg-source'
                native_plugin=snapshot/base/'js/plugins/NativeControl.js'
                native_plugin.parent.mkdir(parents=True,exist_ok=True)
                native_plugin.write_text('const control = "機能キー";',encoding='utf-8')
                manifest=json.loads((project/'data/source-manifest.json').read_text(encoding='utf-8'))
                import hashlib
                manifest[native_plugin.relative_to(snapshot).as_posix()]=hashlib.sha256(native_plugin.read_bytes()).hexdigest()
                save_json(project/'data/source-manifest.json',manifest)
                plugins=project/'staging'/base/'js/plugins';plugins.mkdir(parents=True,exist_ok=True)
                for name in ('RPT_Bilingual.js','OtherGenerated.js'):
                    (plugins/name).write_text('var prior = {"originalActors":{"1":"ミナト"}};',encoding='utf-8')
                with patch('translation.request',side_effect=AssertionError('No model')), \
                     patch('gameloc.apply._reserved_names',wraps=_reserved_names) as collect, \
                     contextlib.redirect_stdout(io.StringIO()):
                    adapter.render(project,cfg)
                    adapter.render(project,cfg)
                    with patch('translation.cache',side_effect=AssertionError('Reuse rendered cache')):
                        result=check_patch(project,cfg,mode='static')
                self.assertEqual(result['status'],'passed',result.get('failures'))
                self.assertEqual(collect.call_count,2)  # Once per render, never again in the checker.
                self.assertTrue(all(call.args[0]==snapshot for call in collect.call_args_list))
                self.assertIn('機能キー',cfg['_rpg_render_reserved_names'])
                self.assertNotIn('ミナト',cfg['_rpg_render_reserved_names'])
                actors=json.loads((project/'staging'/base/'data/Actors.json').read_text(encoding='utf-8'))
                self.assertEqual(actors[1]['name'],'합성 번역')
                if variant=='MZ':
                    doc=json.loads((project/'staging'/base/'data/Map001.json').read_text(encoding='utf-8'))
                    self.assertEqual(doc['events'][1]['pages'][0]['list'][0]['parameters'][4],'합성 번역')
                self.assertEqual((project/'data/translations.jsonl').read_bytes(),before)

    def test_empty_source_identifier_set_does_not_trigger_another_scan(self):
        from patch_validation import run as check_patch
        _,project,cfg,rows,_=self.scenario('english')
        self.populate(project,cfg,rows)
        metadata=project/'data/rpgmaker-project.json'
        upstream=json.loads(metadata.read_text(encoding='utf-8'))
        upstream['identifiers']=[];save_json(metadata,upstream)
        with patch('translation.request',side_effect=AssertionError('No model')), \
             patch('gameloc.apply._reserved_names',return_value=set()) as collect, \
             contextlib.redirect_stdout(io.StringIO()):
            adapter.render(project,cfg)
            result=check_patch(project,cfg,mode='static')
        self.assertEqual(result['status'],'passed',result.get('failures'))
        self.assertEqual(cfg['_rpg_render_reserved_names'],[])
        collect.assert_called_once()


if __name__=='__main__':unittest.main()
