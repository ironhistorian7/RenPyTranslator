"""Synthetic regressions for RPG whole-source retry."""
from contextlib import redirect_stdout, nullcontext
import io, json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import translation
from engine import save_json
from translation import BatchTranslationError, fingerprint, cache, _translate, translate_batch

CFG = {'engine':'rpgmaker', 'model':'synthetic', 'source_language':'japanese',
       'language':'korean', 'num_ctx':16384, 'parallel':1, 'batch_size':8,
       'endpoint':'http://unused.invalid', 'context_txt_enabled':False}

class CleanRetryTests(unittest.TestCase):
    def run_schedule(self, errors, previous=None, source='おはよう', language='japanese'):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        project=Path(tmp.name)
        rows=[dict(id=k,source=s,kind='dialogue',usage='dialogue',file='test',block='one')
              for k,s in [('cached1','こんにちは'),('cached2','さようなら'),('pending',source)]]
        cfg=dict(CFG,source_language=language)
        if previous: cfg['_rpg_previous_failures']={'pending':previous}
        save_json(project/'data/catalog.json',rows)
        cached=[dict(id=r['id'],source=r['source'],text=t,model=cfg['model'],fingerprint=fingerprint(cfg))
                for r,t in zip(rows,['안녕하세요','안녕히 가세요'])]
        cachefile=project/'data/translations.jsonl'
        cachefile.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in cached),encoding='utf-8')
        original=cachefile.read_bytes(); calls=[]
        def batch(items,context,active,retry=''):
            calls.append((items,dict(active),retry))
            self.assertEqual([x['id'] for x in items],['pending'])
            if len(calls)<=len(errors):
                failure=errors[len(calls)-1]
                failure={'error':failure} if isinstance(failure,str) else dict(failure)
                raise BatchTranslationError([], [dict(row=items[0],**failure)], dict(seconds=0,items=1))
            return [dict(id='pending',source=source,text='안녕하세요',model=cfg['model'],fingerprint=fingerprint(cfg))],dict(seconds=0,items=1)
        with redirect_stdout(io.StringIO()),patch('translation.translate_batch',side_effect=batch): _translate(project,cfg)
        self.assertTrue(cachefile.read_bytes().startswith(original))
        return calls,cache(project,cfg),project

    def test_language_failure_uses_kind_specific_retry(self):
        cases=[("['untranslated Japanese text']; output='안녕하세요반갑습니다世'",'minor',False),
               ("['untranslated Japanese text']; output='안녕世界'",'mixed',True),
               ('no Korean translation','no_korean',True)]
        for error,kind,clean in cases:
            with self.subTest(kind=kind):
                calls,known,_=self.run_schedule([error])
                self.assertEqual(len(calls),2)
                self.assertFalse(calls[0][1].get('_rpg_clean_retry',False))
                self.assertEqual(calls[1][1].get('_rpg_retry_kind'),kind)
                self.assertEqual(calls[1][1].get('_rpg_clean_retry'),clean)
                self.assertEqual(calls[1][0][0]['source'],'おはよう')
                if clean:self.assertEqual(calls[1][2],'')
                self.assertEqual(known['pending']['text'],'안녕하세요')
                self.assertEqual(known['cached2']['text'],'안녕히 가세요')

    def test_exhaustion_preserves_valid_partial_and_source(self):
        candidate=dict(text='안녕世界',residue_kind='mixed',japanese_count=2,hangul_count=2)
        initial=dict(error="['untranslated Japanese text']; output='안녕世界'",
                     candidate=candidate,residue_kind='mixed')
        calls,known,project=self.run_schedule([initial,'no Korean translation','no Korean translation'])
        self.assertEqual(len(calls),3)
        self.assertEqual([c[1].get('_rpg_retry_kind') for c in calls[1:]],['mixed','no_korean'])
        record=known['pending']
        self.assertEqual((record['id'],record['source'],record['text'],record['status']),
                         ('pending','おはよう','안녕世界','partial_translation'))
        failure=json.loads((project/'data/failed-items.json').read_text(encoding='utf-8'))[0]
        self.assertEqual(failure['row']['id'],'pending')
        self.assertTrue(failure['kept_partial'])

    def test_error_only_failure_does_not_invent_validated_partial(self):
        error="['untranslated Japanese text']; output='안녕世界'"
        _,known,_=self.run_schedule([error,'no Korean translation','no Korean translation'])
        self.assertEqual((known['pending']['text'],known['pending']['status']),('おはよう','source_fallback'))

    def test_previous_failure_classifies_minor_without_inventing_proof(self):
        calls,known,_=self.run_schedule([],previous="['untranslated Japanese text']; output='안녕하세요반갑습니다反'")
        self.assertFalse(calls[0][1].get('_rpg_clean_retry',False))
        self.assertEqual(calls[0][1].get('_rpg_retry_kind'),'minor')
        self.assertNotIn('_rpg_validated_draft',calls[0][1])
        self.assertEqual(known['pending']['text'],'안녕하세요')

    def test_small_tsu_cleaned_before_validation(self):
        row=dict(id='x',source='おはよう',kind='dialogue',usage='dialogue')
        cfg=dict(CFG,model='rpt-hymt2-7b:q6_k')
        with patch('translation.request',return_value={'message':{'content':'[0] 안녕ッ'}}), \
             patch('hy_backend.runtime',side_effect=lambda cfg:nullcontext(cfg)):
            result,_=translate_batch([row],{},cfg)
        self.assertEqual(result[0]['text'],'안녕')

    def test_retry_prompts_distinguish_residue_kinds(self):
        from rpgmaker_protocol import japanese_instruction
        prompts={kind:japanese_instruction(dict(CFG,_rpg_retry_kind=kind))
                 for kind in ('minor','mixed','no_korean')}
        self.assertIn('남은 작은 일본어 잔류',prompts['minor'])
        self.assertIn('섞여 있는 일본어',prompts['mixed'])
        self.assertIn('원문 전체를 처음부터',prompts['no_korean'])
        self.assertEqual(len(set(prompts.values())),3)

    def test_english_ordinary_retry(self):
        calls,known,_=self.run_schedule(['Missing response ID'],source='Hello there',language='english')
        self.assertEqual(len(calls),2)
        self.assertFalse(any(c[1].get('_rpg_clean_retry',False) for c in calls))
        self.assertEqual(known['pending']['text'],'안녕하세요')

    def test_clean_prompt_and_single_validation(self):
        row=dict(id='x',source=r'おはよう\V[1]',kind='dialogue',usage='dialogue')
        cfg=dict(CFG,model='rpt-hymt2-7b:q6_k',context_txt_enabled=True,_rpg_full_source_retry=True,
                 _rpg_retry_errors={'x':"untranslated Japanese text; output='안녕ッ'"})
        context={'source_passage':[{'text':'CONTEXT_SENTINEL'}], 'translation_guidance':{'global':'GUIDANCE_SENTINEL'}}
        with patch('translation.request',return_value={'message':{'content':'[0] 안녕하세요 ⟦RPT000⟧'}}) as req, \
             patch('hy_backend.runtime',side_effect=lambda cfg:nullcontext(cfg)), \
             patch('translation.validate_text',wraps=translation.validate_text) as validation:
            result,_=translate_batch([row],context,cfg,'DRAFT_SENTINEL 안녕ッ')
        prompt=req.call_args.args[2]['messages'][0]['content']
        for value in ('おはよう','GUIDANCE_SENTINEL','RPT000'): self.assertIn(value,prompt)
        for value in ('CONTEXT_SENTINEL','reading_text','DRAFT_SENTINEL','안녕ッ'): self.assertNotIn(value,prompt)
        self.assertEqual(result[0]['text'],r'안녕하세요 \V[1]')
        self.assertEqual(validation.call_count,1)

    def test_original_known_name_remains_valid(self):
        cfg=dict(CFG,_rpg_name_groups=(('ハル',),),_rpg_clean_retry=True)
        self.assertEqual(translation.validate_text('ハル','ハル',cfg),[])

    def test_split_known_header_restored_before_single_validation(self):
        from rpgmaker_input import configuration
        actor=dict(id='actor',source='ユリ',file='data/Actors.json',usage='speaker_label',
                   location={'fmt':'json','ptr':'[1].name'})
        cfg=configuration(dict(CFG,model='rpt-hymt2-7b:q6_k',_rpg_clean_retry=True),
                          [actor],{'actor':{'source':'ユリ','text':'유리'}})
        source='ユリ\r\nこんにちは\\V[1]'
        row=dict(id='x',source=source,kind='dialogue',usage='dialogue',speaker='unknown')
        with patch('translation.request',return_value={'message':{'content':'[0] 안녕하세요⟦RPT000⟧'}}) as req, \
             patch('hy_backend.runtime',side_effect=lambda cfg:nullcontext(cfg)), \
             patch('translation.validate_text',wraps=translation.validate_text) as validation:
            result,_=translate_batch([row],{},cfg)
        self.assertEqual(result[0]['text'],'유리\r\n안녕하세요\\V[1]')
        prompt=req.call_args.args[2]['messages'][0]['content']
        self.assertNotIn('ユリ',prompt.split('[Source Text]',1)[1])
        self.assertIn('유리',prompt)
        self.assertEqual(row['source'],source)
        self.assertEqual(validation.call_count,1)
        self.assertEqual(validation.call_args.args[:2],(source,'유리\r\n안녕하세요\\V[1]'))

    def test_plain_retry_validation_failure_preserves_restored_header_and_control(self):
        from rpgmaker_input import configuration
        from rpgmaker_protocol import previous_output
        actor=dict(id='actor',source='ユリ',file='data/Actors.json',usage='speaker_label',
                   location={'fmt':'json','ptr':'[1].name'})
        source='ユリ\nおはよう\\C[2]'
        row=dict(id='x',source=source,kind='dialogue',usage='dialogue',speaker='unknown')
        cfg=configuration(dict(CFG,model='rpt-hymt2-7b:q6_k',
                         _rpg_retry_errors={'x':"Protected token mismatch: [] != [0]; output='안녕'"}),
                          [actor],{'actor':{'source':'ユリ','text':'유리'}})
        with patch('translation.request',return_value={'message':{'content':'[0] 안녕世界'}}) as req, \
             patch('hy_backend.runtime',side_effect=lambda cfg:nullcontext(cfg)):
            with self.assertRaises(BatchTranslationError) as raised:
                translate_batch([row],{},cfg)
        failure=raised.exception.failures[0]
        self.assertEqual(failure['row']['id'],'x')
        self.assertEqual(row['source'],source)
        self.assertEqual(previous_output(failure['error']),'유리\n안녕世界\\C[2]')
        self.assertIn('untranslated Japanese text',failure['error'])
        self.assertIn('[0] おはよう',req.call_args.args[2]['messages'][0]['content'])

    def test_normal_rpg_request_keeps_source_context(self):
        row=dict(id='x',source='おはよう',kind='dialogue',usage='dialogue')
        cfg=dict(CFG,model='rpt-hymt2-7b:q6_k')
        with patch('translation.request',return_value={'message':{'content':'[0] 안녕하세요'}}) as req, \
             patch('hy_backend.runtime',side_effect=lambda cfg:nullcontext(cfg)):
            translate_batch([row],{'source_passage':[{'text':'CONTEXT_SENTINEL'}]},cfg)
        prompt=req.call_args.args[2]['messages'][0]['content']
        self.assertIn('CONTEXT_SENTINEL',prompt)
        self.assertNotIn('reading_text',prompt)

    def test_normal_visual_control_has_reading_reference(self):
        row=dict(id='x',source=r'おは\.よう',kind='dialogue',usage='dialogue')
        cfg=dict(CFG,model='rpt-hymt2-7b:q6_k')
        with patch('translation.request',return_value={'message':{'content':'[0] 안녕⟦RPT000⟧하세요'}}) as req, \
             patch('hy_backend.runtime',side_effect=lambda cfg:nullcontext(cfg)):
            translate_batch([row],{},cfg)
        self.assertIn('reading_text',req.call_args.args[2]['messages'][0]['content'])

    def test_clean_flag_does_not_change_renpy_request(self):
        row=dict(id='x',source='Hello there.',kind='dialogue',usage='dialogue')
        cfg=dict(CFG,engine='renpy',source_language='english')
        bodies=[]
        for extra in ({},{'_rpg_clean_retry':True,'_rpg_full_source_retry':True}):
            with patch('translation.request',return_value={'message':{'content':'{"0":"안녕하세요."}'}}) as req:
                translate_batch([row],{},dict(cfg,**extra),'RETRY_SENTINEL')
            bodies.append(req.call_args.args[2])
        self.assertEqual(bodies[0],bodies[1])

if __name__=='__main__': unittest.main()
