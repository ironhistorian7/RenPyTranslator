"""Text-only synthetic fixtures; never run a game, model or real game data."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import game_engine
import rpgmaker_project as adapter
import translation
from engine import save_json


def fixture(root,variant='MV',www=False):
    base=root/'www' if www else root
    (base/'data').mkdir(parents=True);(base/'js').mkdir()
    (base/'js'/('rpg_core.js' if variant=='MV' else 'rmmz_core.js')).write_text('// synthetic core marker')
    (base/'js/plugins.js').write_text('var $plugins = [];')
    save_json(base/'data/System.json',{'gameTitle':'Test game','terms':{'commands':['Fight','Escape']},'currencyUnit':'Gold'})
    save_json(base/'data/Actors.json',[None,{'id':1,'name':'Alice','nickname':'Bear','profile':'A friendly traveler.'}])
    commands=[{'code':101,'indent':0,'parameters':['',0,0,2]+(['Alice'] if variant=='MZ' else [])},
        {'code':401,'indent':0,'parameters':['Hello, \\N[1]!']},
        {'code':401,'indent':0,'parameters':['We have \\V[2] coins.']},
        {'code':102,'indent':0,'parameters':[['Go home','Stay'],0,0,2,0]},
        {'code':402,'indent':0,'parameters':[0,'Go home']},
        {'code':401,'indent':1,'parameters':['Hello again.']},
        {'code':121,'indent':1,'parameters':[1,1,0]},
        {'code':402,'indent':0,'parameters':[1,'Stay']},
        {'code':401,'indent':1,'parameters':['Hello again.']},
        {'code':355,'indent':1,'parameters':['var secret = "not translation";']},
        {'code':0,'indent':0,'parameters':[]}]
    save_json(base/'data/Map001.json',{'displayName':'Camp','events':[None,{'name':'Event001','pages':[{'list':commands}]}]})
    (base/'img').mkdir();(base/'img/large.png').write_bytes(b'unchanged image')
    return base


class RPGMakerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'game';self.project=self.root/'project'
        self.project.mkdir();(self.project/'data').mkdir()
        self.cfg=dict(source=str(self.source),engine='rpgmaker',source_language='english',language='korean',
            model='synthetic',num_ctx=4096,batch_size=1,parallel=1,context_txt_enabled=False)

    def prepare(self,variant='MV',www=False):
        fixture(self.source,variant,www)
        with contextlib.redirect_stdout(io.StringIO()):adapter.prepare(self.project,self.cfg)
        return translation.read_catalog(self.project)

    def populate(self,rows):
        records=[]
        for row in rows:
            text='안녕하세요.' if row['kind']=='dialogue' else '항목'
            _,tokens=translation.protect(row['source'],self.cfg)
            text+=''.join(tokens)
            records.append(dict(id=row['id'],source=row['source'],text=text,model='synthetic',fingerprint=translation.fingerprint(self.cfg)))
        (self.project/'data/translations.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records),encoding='utf-8')

    def test_detect_mv_mz_www_and_preserve_renpy(self):
        fixture(self.source,'MZ',True)
        self.assertEqual(game_engine.detect(self.source)['variant'],'MZ')
        other=self.root/'renpy-game';(other/'renpy').mkdir(parents=True);(other/'game').mkdir()
        self.assertEqual(game_engine.detect(other)['engine'],'renpy')

    def test_unsupported_stops_before_model_or_project_creation(self):
        from translate_game import main
        self.source.mkdir();(self.source/'Sample_Data').mkdir()
        with patch('model_store.require_selected') as model,patch('automatic.resolve_project') as resolve:
            with self.assertRaisesRegex(ValueError,'Unity'):main(['run','--source',str(self.source)])
            model.assert_not_called();resolve.assert_not_called()

    def test_unsupported_rpg_task_stops_before_work(self):
        fixture(self.source)
        for task in ('routes','answers'):
            with self.subTest(task=task),self.assertRaisesRegex(ValueError,'지원하지 않는 작업'):
                game_engine.require(self.source,True,[task])

    def test_source_repairs_use_only_the_registered_prepared_project(self):
        from automatic import source_key
        from translate_game import main
        self.project=self.root/'projects/Existing'
        rows=self.prepare('MV',True);self.populate(rows)
        save_json(self.root/'projects/index.json',{source_key(self.source.resolve()):'Existing'})
        cachefile=self.project/'data/translations.jsonl';before=cachefile.read_bytes()
        for fix in ('font','failed','display','layout'):
            with self.subTest(fix=fix),patch('automatic.ROOT',self.root), \
                 patch('model_store.require_selected',return_value='synthetic'), \
                 patch('model_runtime.model_session',return_value=contextlib.nullcontext()), \
                 patch('gpu_policy.configure',side_effect=lambda cfg:cfg), \
                 patch('rpgmaker_project.prepare',side_effect=AssertionError('No preparation fallback')) as prepare, \
                 patch('rpgmaker_project.seed_defaults') as seed,patch('rpgmaker_scope.refresh'), \
                 patch('translation.read_catalog',return_value=[]),patch('translation.cache',return_value={}), \
                 patch('translation.translate',side_effect=AssertionError('No ordinary translation')) as translate, \
                 patch('translation._translate',side_effect=AssertionError('No failed items in this synthetic cache')), \
                 patch('hy_backend.runtime',side_effect=AssertionError('No model runtime')), \
                 patch('rpgmaker_project.render') as render,contextlib.redirect_stdout(io.StringIO()):
                main(['tasks','--tasks',fix,'--source',str(self.source)])
                prepare.assert_not_called();translate.assert_not_called()
                self.assertEqual(seed.call_args.args[0],self.project)
                render.assert_called_once()
                self.assertEqual(render.call_args.args[0],self.project)
            self.assertEqual(cachefile.read_bytes(),before)

    def test_repairs_without_cache_stop_before_seed_prepare_model_or_render(self):
        from automatic import source_key
        from translate_game import main
        self.project=self.root/'projects/Existing'
        self.prepare('MV',True)
        save_json(self.root/'projects/index.json',{source_key(self.source.resolve()):'Existing'})
        self.assertFalse((self.project/'data/translations.jsonl').exists())
        for fix in ('font','failed','display','layout'):
            for source in (True,False):
                with self.subTest(fix=fix,source=source),patch('automatic.ROOT',self.root), \
                     patch('model_store.require_selected',return_value='synthetic'), \
                     patch('model_runtime.model_session',side_effect=AssertionError('No model session')), \
                     patch('rpgmaker_project.prepare',side_effect=AssertionError('No preparation fallback')) as prepare, \
                     patch('rpgmaker_project.seed_defaults',side_effect=AssertionError('No cache seeding')) as seed, \
                     patch('rpgmaker_scope.refresh',side_effect=AssertionError('No refresh')), \
                     patch('gpu_policy.configure',side_effect=AssertionError('No GPU setup')), \
                     patch('translation.translate',side_effect=AssertionError('No ordinary translation')) as translate, \
                     patch('rpgmaker_project.render',side_effect=AssertionError('No rendering')) as render, \
                     contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaisesRegex(ValueError,'기존 번역 기록을 찾지 못했습니다'):
                        main(['tasks','--tasks',fix,'--source' if source else '--project',
                              str(self.source if source else self.project)])
                    prepare.assert_not_called();seed.assert_not_called();translate.assert_not_called();render.assert_not_called()

    def test_source_repairs_without_preparation_never_start_a_new_translation(self):
        from translate_game import main
        fixture(self.source,'MV',True)
        for fix in ('font','failed','display','layout'):
            with self.subTest(fix=fix),patch('automatic.ROOT',self.root/'tool'), \
                 patch('model_store.require_selected',return_value='synthetic'), \
                 patch('model_runtime.model_session',side_effect=AssertionError('No model session')), \
                 patch('rpgmaker_project.prepare',side_effect=AssertionError('No preparation fallback')) as prepare, \
                 patch('rpgmaker_project.seed_defaults',side_effect=AssertionError('No cache seeding')), \
                 patch('rpgmaker_scope.refresh',side_effect=AssertionError('No refresh')), \
                 patch('translation.translate',side_effect=AssertionError('No ordinary translation')) as translate, \
                 patch('rpgmaker_project.render',side_effect=AssertionError('No rendering')), \
                 contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(ValueError,'기존 RPG Maker 작업 자료를 찾지 못했습니다'):
                    main(['tasks','--tasks',fix,'--source',str(self.source)])
                prepare.assert_not_called();translate.assert_not_called()

    def test_extraction_preserves_locations_and_branch_scope(self):
        rows=self.prepare('MZ')
        duplicate=[r for r in rows if r['source']=='Hello again.']
        self.assertEqual(len(duplicate),2);self.assertNotEqual(duplicate[0]['id'],duplicate[1]['id'])
        self.assertNotEqual(duplicate[0]['block'],duplicate[1]['block'])
        self.assertTrue(any(r['usage']=='speaker_label' and r['source']=='Alice' for r in rows))
        self.assertTrue(any(r['usage']=='choice' for r in rows))
        self.assertFalse(any('secret' in r['source'] for r in rows))
        self.assertFalse((self.project/'data/rpg-source/img').exists())

    def test_control_codes_protection_and_movable_names(self):
        source=r'Hello \N[1], \C[2]take \V[2] coins\C[0]. %1'
        masked,tokens=translation.protect(source,self.cfg)
        self.assertIn('<rpt000/>',masked);self.assertEqual(len(tokens),5)
        self.assertEqual(translation.validate_text(source,r'\N[1], \C[2]\V[2] 코인을 받으세요\C[0]. %1',self.cfg),[])
        self.assertIn('RPG Maker control code mismatch',translation.validate_text(source,'안녕',self.cfg))
        self.assertEqual(translation.protect('[name]',{}),('<rpt000/>',['[name]']))

    def test_real_batch_protocol_restores_rpg_codes(self):
        row=dict(id='rpg',kind='dialogue',usage='dialogue',source=r'Hello, \N[1]! Take \V[2] coins.')
        cfg=dict(self.cfg,endpoint='http://synthetic.invalid',_names={})
        response={'message':{'content':json.dumps({'0':'안녕, <rpt000/>! <rpt001/> 코인을 받으세요.'})}}
        with patch('translation.request',return_value=response) as request:
            output,_=translation.translate_batch([row],{},cfg)
        self.assertEqual(output[0]['text'],r'안녕, \N[1]! \V[2] 코인을 받으세요.')
        body=request.call_args.args[2]
        self.assertIn('<rpt000/>',body['messages'][-1]['content'])

    def test_coordinates_stay_outside_inference_but_roundtrip(self):
        row=dict(id='positioned',kind='dialogue',usage='dialogue',source=r'\PY[24]\PX[90]Hello, \N[2]!')
        cfg=dict(self.cfg,endpoint='http://synthetic.invalid')
        response={'message':{'content':json.dumps({'0':'안녕, [rpt000/]!'})}}
        with patch('translation.request',return_value=response) as request:
            output,_=translation.translate_batch([row],{},cfg)
        self.assertEqual(output[0]['text'],r'\PY[24]\PX[90]안녕, \N[2]!')
        item=json.loads(request.call_args.args[2]['messages'][-1]['content'])['items'][0]
        self.assertNotIn('PY',item['text'])
        self.assertEqual(item['reading_text'],r'Hello, \N[2]!')

    def test_reading_reference_joins_soft_lines_without_losing_variables_or_unknown_codes(self):
        from rpgmaker_tokens import readable,split_outer_controls
        source='\\PY[20]\\PX[80]お\\.は\\.よう\n\\PX[80]\\N[2]、\\CUSTOM[7]'
        self.assertEqual(readable(source,dict(self.cfg,source_language='japanese')),r'おはよう\N[2]、\CUSTOM[7]')
        prefix,core,suffix=split_outer_controls(source)
        self.assertEqual(prefix,r'\PY[20]\PX[80]');self.assertEqual(prefix+core+suffix,source)
        self.assertEqual(readable('Hello\nthere',self.cfg),'Hello there')
        self.assertEqual(split_outer_controls(r'\N[2] Hello'),('',r'\N[2] Hello',''))

    def test_japanese_output_and_malformed_markers_cannot_pass_validation(self):
        cfg=dict(self.cfg,source_language='japanese')
        self.assertIn('untranslated Japanese text',translation.validate_text('こんにちは。','안녕하せ요.',cfg))
        self.assertIn('unrestored placeholder',translation.validate_text('こんにちは。','안녕 [rpt02]',cfg))
        self.assertIn('unrestored placeholder',translation.validate_text('こんにちは。','안녕 rpt02/>',cfg))
        self.assertEqual(translation.validate_entry('こんにちは。',{'text':'こんにちは。','status':'source_fallback'},cfg),[])
        self.assertEqual(translation.validate_text('CPU','CPU',cfg),[])

    def test_bad_cached_rpg_items_retry_without_deleting_valid_or_fallback_records(self):
        from translation import cache,fingerprint
        rows=self.prepare();cfg=dict(self.cfg,source_language='japanese')
        data=[dict(id=rows[0]['id'],source=rows[0]['source'],text='안녕 [rpt09]',model='synthetic',fingerprint=fingerprint(cfg)),
              dict(id=rows[1]['id'],source=rows[1]['source'],text=rows[1]['source'],status='source_fallback',model='synthetic',fingerprint=fingerprint(cfg))]
        path=self.project/'data/translations.jsonl';raw=''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in data).encode('utf-8');path.write_bytes(raw)
        known=cache(self.project,cfg)
        self.assertNotIn(rows[0]['id'],known);self.assertIn(rows[1]['id'],known)
        self.assertEqual(path.read_bytes(),raw)
        self.assertTrue((self.project/'data/rpg-invalid-cache.json').exists())

    def test_hy_reading_reference_is_source_only_not_a_translation_dictionary(self):
        from hy_backend import body
        item={'id':'0','kind':'dialogue','usage':'dialogue','text':'お<rpt000/>はよう','reading_text':'おはよう','movable_placeholders':[]}
        cfg=dict(self.cfg,model='rpt-hymt2-7b:q6_k',source_language='japanese')
        prompt=body([item],{},cfg,{},'')['messages'][0]['content']
        background=prompt.split('[Background Information]\n')[1].split('\n\n[Source Text]')[0]
        self.assertEqual(json.loads(background)['reading_text'],{'0':'おはよう'})
        self.assertEqual(json.loads(background)['glossary'],{})

    def test_patch_only_changed_files_original_unchanged_and_resume(self):
        rows=self.prepare('MZ',True);self.populate(rows)
        original={p.relative_to(self.source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in self.source.rglob('*') if p.is_file()}
        cached=(self.project/'data/translations.jsonl').read_bytes()
        with patch('translation.translate_batch',side_effect=AssertionError('resume must not call model')),patch('runtime_recovery.run',side_effect=AssertionError('resume must not load model')):
            with contextlib.redirect_stdout(io.StringIO()):translation.translate(self.project,self.cfg);adapter.render(self.project,self.cfg);adapter.render(self.project,self.cfg)
        self.assertEqual((self.project/'data/translations.jsonl').read_bytes(),cached)
        self.assertEqual(original,{p.relative_to(self.source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in self.source.rglob('*') if p.is_file()})
        output=self.project/'output';mapfile=output/'www/data/Map001.json'
        self.assertTrue(mapfile.exists());self.assertFalse((output/'www/img').exists());self.assertFalse((output/'www/js/rmmz_core.js').exists())
        commands=json.loads(mapfile.read_text(encoding='utf-8'))['events'][1]['pages'][0]['list']
        self.assertEqual(len(commands),11);self.assertEqual(commands[6]['parameters'],[1,1,0])
        self.assertEqual(commands[9]['parameters'],['var secret = "not translation";'])
        self.assertEqual(commands[1]['_rpt_original'],'Hello, \\N[1]!\nWe have \\V[2] coins.')
        self.assertEqual(commands[0]['parameters'][4],'항목')
        self.assertEqual(commands[3]['_rpt_choice_originals'],['Go home','Stay'])
        registry=(output/'www/js/plugins.js').read_text()
        self.assertEqual(sum(p['name']=='RPT_Bilingual' for p in json.loads(registry[registry.index('['):registry.rindex(']')+1])),1)
        link=json.loads((output/'project-link.json').read_text());self.assertEqual((output/link['project']).resolve(),self.project)

    def test_interrupted_batches_keep_committed_results_and_resume_missing_only(self):
        rows=self.prepare();self.cfg['batch_size']=1
        def batch(items,context,cfg,retry=''):
            row=items[0]
            if calls[0]:raise OSError('synthetic stopped transport')
            calls[0]+=1
            _,tokens=translation.protect(row['source'],cfg)
            return [dict(id=row['id'],source=row['source'],text='번역'+''.join(tokens),model='synthetic',fingerprint=translation.fingerprint(cfg))],{'seconds':0,'items':1}
        calls=[0]
        with contextlib.redirect_stdout(io.StringIO()),patch('translation.translate_batch',side_effect=batch):
            with self.assertRaises(OSError):translation._translate(self.project,self.cfg)
        committed=translation.cache(self.project,self.cfg);self.assertEqual(len(committed),1)
        seen=[]
        def finish(items,context,cfg,retry=''):
            seen.extend(r['id'] for r in items)
            results=[]
            for r in items:
                _,tokens=translation.protect(r['source'],cfg)
                results.append(dict(id=r['id'],source=r['source'],text='번역'+''.join(tokens),model='synthetic',fingerprint=translation.fingerprint(cfg)))
            return results,{'seconds':0,'items':len(items)}
        with contextlib.redirect_stdout(io.StringIO()),patch('translation.translate_batch',side_effect=finish):translation._translate(self.project,self.cfg)
        self.assertFalse(set(seen)&set(committed));self.assertEqual(len(translation.cache(self.project,self.cfg)),len(rows))

    def test_comparison_independent_cache_and_pending_resume(self):
        rows=self.prepare();self.populate(rows)
        with contextlib.redirect_stdout(io.StringIO()):target=adapter.comparison(self.project,self.cfg);again=adapter.comparison(self.project,self.cfg)
        self.assertEqual(target,again);self.assertFalse((target/'data/translations.jsonl').exists())
        self.assertTrue((self.project/'data/translations.jsonl').exists())

    def test_comparison_requires_prepared_inputs_before_creating_any_run(self):
        for missing in ('catalog.json','rpgmaker-project.json','rpg-source'):
            with self.subTest(missing=missing):
                project=self.root/('unprepared-'+missing)
                (project/'data').mkdir(parents=True)
                for name in ('catalog.json','rpgmaker-project.json'):
                    if name!=missing:save_json(project/'data'/name,{})
                if missing!='rpg-source':(project/'data/rpg-source').mkdir()
                with self.assertRaisesRegex(ValueError,'재번역할 RPG Maker 작업 자료가 없습니다'):
                    adapter.comparison(project,self.cfg)
                self.assertFalse((project/'data/rpg-retranslation-runs.json').exists())
                self.assertEqual(list(self.root.glob(project.name+'-retranslate-*')),[])

    def test_new_source_retranslation_stops_without_preparation_or_translation(self):
        from translate_game import main
        fixture(self.source,'MV',True)
        tool=self.root/'tool'
        with patch('automatic.ROOT',tool),patch('model_store.require_selected',return_value='synthetic'), \
             patch('model_runtime.model_session',return_value=contextlib.nullcontext()), \
             patch('gpu_policy.configure',side_effect=AssertionError('No GPU setup')), \
             patch('rpgmaker_project.prepare',side_effect=AssertionError('No preparation fallback')) as prepare, \
             patch('translation.translate',side_effect=AssertionError('No translation fallback')) as translate, \
             patch('rpgmaker_project.render',side_effect=AssertionError('No original rendering')) as render, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError,'재번역할 RPG Maker 작업 자료가 없습니다'):
                main(['tasks','--tasks','retranslate','--source',str(self.source)])
        prepare.assert_not_called();translate.assert_not_called();render.assert_not_called()
        project=tool/'data/projects/game'
        self.assertFalse((project/'data/rpg-retranslation-runs.json').exists())
        self.assertEqual(list(project.parent.glob('game-retranslate-*')),[])

    def test_cli_prepare_translate_output_and_resume_end_to_end(self):
        from translate_game import main
        fixture(self.source,'MV',True)
        requests=[]
        def request(endpoint,route,body):
            items=json.loads(body['messages'][-1]['content'])['items'];requests.extend(items)
            result={item['id']:'번역'+''.join(__import__('re').findall(r'<rpt\d+/>',item['text'])) for item in items}
            return {'message':{'content':json.dumps(result,ensure_ascii=False)}}
        with patch('automatic.ROOT',self.root/'tool'),patch('model_store.require_selected',return_value='synthetic'),\
                patch('gpu_policy.configure',side_effect=lambda cfg:cfg),\
                patch('runtime_recovery.run',side_effect=lambda project,cfg,operation,count:operation(cfg)) as runtime,\
                patch('translation.request',side_effect=request),contextlib.redirect_stdout(io.StringIO()):
            command=['tasks','--tasks','run','failed','--source',str(self.source),'--output',str(self.root/'result')]
            main(command);first=len(requests);self.assertGreater(first,0)
            internal=self.root/'tool/data/projects/game';before=(internal/'data/translations.jsonl').read_bytes()
            main(command);self.assertEqual(first,len(requests));runtime.assert_called_once()
            self.assertEqual(before,(internal/'data/translations.jsonl').read_bytes())
            output=self.root/'result/game-kr';self.assertTrue((output/'www/js/plugins/RPT_Bilingual.js').is_file())
            main(['rebuild','--project',str(output)])
            self.assertEqual(first,len(requests));runtime.assert_called_once()

    def test_optional_rpg_context_and_bom_without_touching_source(self):
        from context_notes import ContextNotes
        base=fixture(self.source,'MZ',True)
        notes=base/'context';(notes/'files/data').mkdir(parents=True)
        (notes/'global.txt').write_text('Keep narration calm.',encoding='utf-8-sig')
        (notes/'files/data/Map001.txt').write_text('These people are longtime friends.',encoding='utf-8')
        system=base/'data/System.json';system.write_bytes(b'\xef\xbb\xbf'+system.read_bytes())
        original=system.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):adapter.prepare(self.project,self.cfg)
        rows=translation.read_catalog(self.project);cfg=dict(self.cfg,context_txt_enabled=True)
        guide=ContextNotes(self.project,cfg,rows)
        self.assertIn('calm',guide.global_text);self.assertGreater(guide.report['mapped_entries'],0)
        # Moved original game: the prepared TXT remains available.
        portable=ContextNotes(self.project,dict(cfg,source=str(self.root/'moved-source')),rows)
        self.assertEqual(guide.global_text,portable.global_text)
        disabled=ContextNotes(self.project,dict(cfg,context_txt_enabled=False),rows)
        self.assertFalse(disabled.global_text);self.assertEqual(system.read_bytes(),original)


if __name__=='__main__':unittest.main()
