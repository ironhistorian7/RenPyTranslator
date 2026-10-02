"""Bounded synthetic tests only: no installed game, project or local model."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import warnings
from unittest.mock import patch
from engine import save_json
from translation import fingerprint,cache,restore,retry_instruction,translate_batch
from failed_repair import run,saved_output


class FailedRepairTests(unittest.TestCase):
    def test_saved_reordered_variables_recover_offline_without_cache_rewrite(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);cfg={'model':'fake','language':'korean','_reuse_completed':True}
            samples=[('Image [index] of [count] locked.','전체 <rpt001/>개 중 <rpt000/>번 이미지는 잠겨 있습니다.'),
                     ('Ask [a] and [b] about [c].','<rpt002/>에 관해 <rpt000/>와 <rpt001/>에게 물어보세요.')]
            rows=[dict(id=str(i),source=s,kind='dialogue',file='invented.rpy') for i,(s,t) in enumerate(samples)]
            entries=[dict(row,text=row['source'],model='fake',fingerprint='old',status='source_fallback',
                          error='Protected token mismatch: old order; output='+repr(samples[i][1])) for i,row in enumerate(rows)]
            save_json(p/'data/catalog.json',rows)
            save_json(p/'data/failed-items.json',[dict(row=row,error=entry['error']) for row,entry in zip(rows,entries)])
            path=p/'data/translations.jsonl';path.write_text(''.join(json.dumps(e)+'\n' for e in entries),encoding='utf-8')
            before=path.read_bytes()
            with patch('hy_backend.runtime',side_effect=AssertionError('No model for saved translations')):
                ids,report=run(p,cfg)
                self.assertEqual(report['recovered_saved'],2)
                self.assertEqual(report['remaining'],0)
                self.assertEqual(report['model_calls'],0)
                self.assertEqual(len(ids),2)
                _,again=run(p,cfg)
                self.assertEqual(again['already_resolved'],2)
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(cache(p,cfg)['0']['text'],'전체 [count]개 중 [index]번 이미지는 잠겨 있습니다.')

    def test_hy_prompt_without_markers_has_no_marker_example(self):
        from hy_backend import body
        cfg={'model':'rpt-hymt2-7b:q6_k'}
        plain=[{'id':'0','text':'Hello.'}]
        self.assertNotIn('<rpt',body(plain,{},cfg,{},'')['messages'][0]['content'])
        marked=[{'id':'0','text':'Hello <rpt000/>.'}]
        self.assertIn('실제로 있는',body(marked,{},cfg,{},'')['messages'][0]['content'])
        self.assertNotIn('<rpt',retry_instruction([{'source':'Hello.'}]))

    def test_general_model_prompt_is_conditional(self):
        cfg={'model':'fake','endpoint':'http://localhost','language':'korean'}
        with patch('translation.request',return_value={'message':{'content':'{"0":"안녕."}'}}) as call:
            translate_batch([{'id':'one','kind':'dialogue','source':'Hello.'}],{},cfg)
            self.assertNotIn('<rpt',call.call_args.args[2]['messages'][0]['content'])

    def test_saved_output_cleanup_and_real_variables(self):
        source='I do not think we ever had peace.'
        error="Protected token mismatch: [0] != []; output='<rpt000/> 평화로웠던 적은 없었던 것 같아.'"
        self.assertEqual(saved_output(source,error,{}),'평화로웠던 적은 없었던 것 같아.')
        self.assertEqual(saved_output('[name], '+source,error,{}),'[name] 평화로웠던 적은 없었던 것 같아.')
        self.assertIsNone(saved_output('[name], [other], '+source,error,{}))
        self.assertIsNone(saved_output(source,'Protected token mismatch: output=bad',{}))
        with self.assertRaises(ValueError):restore('안녕',['[name]'])
        self.assertEqual(restore('<rpt000/> 안녕',[]),'안녕')
        self.assertEqual(restore('<rpt000/> 안녕',['[name]']),'[name] 안녕')

    def test_only_indexed_failures_processed_and_results_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);cfg={'model':'rpt-hymt2-7b:q6_k','language':'korean','_reuse_completed':True}
            rows=[{'id':n,'source':s,'kind':'dialogue','file':'example.rpy'} for n,s in
                  [('saved','Hello.'),('retry','Goodbye.'),('good','Thanks.'),('unindexed','Unknown.'),('menu','Custom Gallery')]]
            rows[-1]['kind']='string'
            save_json(p/'data/ui-policy.json',{'menu':{'Custom Gallery':'Custom Gallery'},'choices':[]})
            save_json(p/'data/catalog.json',rows)
            entries=[dict(r,text=r['source'],model=cfg['model'],fingerprint=fingerprint(cfg),status='source_fallback',error='Missing ID') for r in rows]
            entries[0]['error']="Protected token mismatch: [0] != []; output='<rpt000/> 안녕.'"
            entries[2].update(text='고마워.',status='translated')
            raw=''.join(json.dumps(e)+'\n' for e in entries).encode()
            (p/'data/translations.jsonl').write_bytes(raw)
            save_json(p/'data/failed-items.json',[{'row':rows[i],'error':entries[i]['error']} for i in [0,1,4]])
            with patch('hy_backend.runtime',return_value=contextlib.nullcontext(cfg)),patch('failed_repair.translate_batch',return_value=([dict(rows[1],text='잘 가.')],{})) as translate:
                selected,report=run(p,cfg)
                self.assertEqual(selected,{'saved','retry'})
                self.assertEqual(translate.call_count,1)
                self.assertEqual(translate.call_args.args[0],[dict(rows[1],usage='dialogue')])
                self.assertEqual(report['recovered_saved'],1)
            self.assertEqual((p/'data/translations.jsonl').read_bytes(),raw)
            known=cache(p,cfg)
            self.assertEqual(known['good']['text'],'고마워.')
            self.assertEqual(known['unindexed']['status'],'source_fallback')
            with patch('hy_backend.runtime',side_effect=AssertionError('No model for cached repair')):
                selected,report=run(p,cfg)
                self.assertEqual(report['model_calls'],0)
                self.assertEqual(selected,{'saved','retry'})

    def test_missing_index_does_not_scan_catalog_or_cache(self):
        with tempfile.TemporaryDirectory() as temp,patch('failed_repair.read_catalog',side_effect=AssertionError('No scan')),patch('failed_repair.cache',side_effect=AssertionError('No scan')):
            self.assertEqual(run(Path(temp),{})[0],set())

    def test_help_without_command_or_fix_never_opens_project(self):
        from translate_game import main
        for args in ([],['--source','DO-NOT-READ'],['repair','--project','DO-NOT-READ']):
            with patch('sys.argv',['translate_game.py']+args),patch('translate_game.resolve_project',side_effect=AssertionError('No project')),patch.object(Path,'is_dir',side_effect=AssertionError('No stat')),contextlib.redirect_stdout(io.StringIO()) as output:
                main()
                self.assertIn('--fix failed',output.getvalue())

    def test_unknown_escape_preserved_without_warning_or_double_parse(self):
        from script_literals import literal_eval,parse_expression
        from ui_policy import discover
        source='screen navigation():\n    textbutton _("'+chr(92)+'Achievements") action NullAction()\n'
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            with patch('ui_policy.literal_eval',wraps=literal_eval) as parse:
                result=discover({'example.rpy':source})
                self.assertEqual(parse.call_count,1)
            self.assertIn(chr(92)+'Achievements',result['uses'])
            self.assertEqual(literal_eval(r'"\A\n\t\\A"'),'\\A\n\t\\A')
            self.assertEqual(literal_eval(r'r"\A\n"'),r'\A\n')
            self.assertEqual(literal_eval(r'"\uAC00"'),'가')
            self.assertEqual(literal_eval(parse_expression(r'_("\A")').body.args[0]),r'\A')
            self.assertEqual(caught,[])

    def test_failed_display_does_not_scan_source_or_change_font_payload(self):
        from failed_repair import refresh_choices
        from translation import display_metadata
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);cfg={'language':'korean','_fix':'failed'}
            path=p/'staging/game/tl/korean/_rpt_presentation.json'
            data={'fonts':{'sentinel':'keep'},'literal':{'Replay':'다시보기'},'choices':{'Go':'Go'}}
            save_json(path,data)
            row={'id':'one','kind':'string','source':'Go'}
            with patch('name_hints.ensure_metadata',side_effect=AssertionError('No source scan')),patch('failed_repair.read_catalog',return_value=[row]),patch('replacements.effective_cache',return_value={'one':{'text':'가자'}}):
                display_metadata(p,cfg)
                refresh_choices(p,cfg,{'one'})
            actual=json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(actual['fonts'],data['fonts']);self.assertEqual(actual['literal'],data['literal'])
            self.assertIn('가자',actual['choices']['Go'])

    def test_targeted_repair_packages_only_changed_text_and_preserves_cache(self):
        import zipfile
        from translation import catalog
        from translate_game import repair
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);target=p/'staging/game/tl/korean/example.rpy'
            target.parent.mkdir(parents=True)
            template='translate korean a:\n    # c "Hello."\n    c ""\n\ntranslate korean b:\n    # c "Goodbye."\n    c ""\n'
            target.write_text(template,encoding='utf-8')
            cfg={'model':'fake','language':'korean'};rows=catalog(p,cfg)
            entries=[dict(r,text=r['source'],model='fake',fingerprint=fingerprint(cfg)) for r in rows]
            entries[0].update(status='source_fallback',error="Protected token mismatch: [0] != []; output='<rpt000/> 안녕.'")
            entries[1]['text']='잘 가.'
            raw=''.join(json.dumps(e)+'\n' for e in entries).encode()
            (p/'data/translations.jsonl').write_bytes(raw)
            save_json(p/'data/failed-items.json',[{'row':rows[0],'error':entries[0]['error']}])
            existing=template.replace('c ""','c "Hello."',1).replace('c ""','c "기존 표시 그대로"',1)
            target.write_text(existing,encoding='utf-8')
            with patch('engine.engine_command') as engine,patch('automatic.script_sources',side_effect=AssertionError('No source traversal')),patch('hy_backend.runtime',side_effect=AssertionError('No model needed')),patch('translate_game.install_support',side_effect=AssertionError('No font install')):
                repair(p,cfg,'failed')
                self.assertEqual(engine.call_args.args[1],['compile'])
            self.assertEqual((p/'data/translations.jsonl').read_bytes(),raw)
            with zipfile.ZipFile(p/'output'/(p.name+'-korean-local-draft.zip')) as archive:
                text=archive.read('game/tl/korean/example.rpy').decode('utf-8')
                self.assertIn('안녕.',text)
                self.assertIn('c "기존 표시 그대로"',text)

    def test_model_runtime_is_closed_on_transport_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);cfg={'model':'rpt-hymt2-7b:q6_k','language':'korean'}
            row={'id':'one','source':'Hello.','kind':'dialogue','file':'example.rpy'}
            save_json(p/'data/catalog.json',[row])
            entry=dict(row,text='Hello.',status='source_fallback',error='Missing ID',model=cfg['model'],fingerprint=fingerprint(cfg))
            (p/'data/translations.jsonl').write_text(json.dumps(entry)+'\n',encoding='utf-8')
            save_json(p/'data/failed-items.json',[{'row':row,'error':'Missing ID'}])
            closed=[]
            @contextlib.contextmanager
            def runtime(cfg):
                try:yield cfg
                finally:closed.append(True)
            with patch('hy_backend.runtime',runtime),patch('failed_repair.translate_batch',side_effect=OSError('Synthetic connection error')):
                with self.assertRaises(OSError):run(p,cfg)
            self.assertEqual(closed,[True])


if __name__=='__main__':unittest.main()
