"""Invented event graphs and protocol outputs; no game-specific translations."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine import save_json
from rpgmaker_scope import Helpers,refresh
from rpgmaker_protocol import normalize_markers,parse,retry_instruction,correction_draft,correction_parts,apply_corrections,align_display_anchors
from rpgmaker_tokens import protect
from source_context import SourceContext
from translation import restore,translate,cache,fingerprint,translate_batch,_translate,BatchTranslationError


CFG={'engine':'rpgmaker','model':'synthetic','source_language':'japanese','language':'korean',
     'num_ctx':16384,'parallel':2,'batch_size':8,'endpoint':'http://unused.invalid','context_txt_enabled':False}


class RecoveryTests(unittest.TestCase):
    def test_marker_repairs_do_not_guess_missing_duplicate_or_unknown_ids(self):
        tokens=['\\.','\\PX[123]']
        self.assertEqual(restore(normalize_markers(r'안녕 \\<rpt000/\\> [rpt001>]'),tokens),r'안녕 \. \PX[123]')
        for bad in (r'<rpt000/>안녕',r'[rpt000][rpt000][rpt001]',r'[rpt000][rpt009]'):
            with self.subTest(bad=bad),self.assertRaises(ValueError):restore(normalize_markers(bad),tokens)
        self.assertEqual(normalize_markers('<rpt000/>]'),'<rpt000/>]')

    def test_single_item_prefixed_id_only_when_unambiguous(self):
        self.assertEqual(parse('[nr_0] 안녕',1),({'0':'안녕'},True))
        self.assertEqual(parse('[0] 안녕',1),({'0':'안녕'},False))
        for text,count in [('[nr_3] 안녕',1),('[nr_0] 안녕\n[nr_0] 다시',1),('[nr_0] 안녕',2),('설명\n[nr_0] 안녕',1)]:
            self.assertEqual(parse(text,count),({},False))

    def test_wire_protocol_is_rpg_only_and_retry_is_outside_source_text(self):
        from hy_backend import body
        item={'id':'0','kind':'dialogue','usage':'dialogue','text':'Hello <rpt000/>','movable_placeholders':['<rpt000/>'],'name_hints':{}}
        rpg=body([item],{},CFG,{},'retry <rpt000/>')['messages'][0]['content']
        self.assertIn('[0] Hello ⟦RPT000⟧',rpg)
        self.assertNotIn('<rpt000/>',rpg)
        self.assertLess(rpg.index('retry ⟦RPT000⟧'),rpg.index('[Source Text]'))
        renpy=body([item],{},dict(CFG,engine='renpy'),{},'retry <rpt000/>')['messages'][0]['content']
        self.assertIn('[0] Hello <rpt000/>',renpy);self.assertNotIn('⟦0⟧',renpy)
        self.assertGreater(renpy.index('retry <rpt000/>'),renpy.index('[Source Text]'))
        self.assertEqual(restore(normalize_markers('안녕 ⟦0⟧'),['\\N[1]']),r'안녕 \N[1]')
        self.assertEqual(restore(normalize_markers('안녕 ⟦RPT000⟦'),['\\N[1]']),r'안녕 \N[1]')

    def test_literal_numeric_brackets_do_not_collide_with_wire_tokens(self):
        masked,tokens=protect(r'Notation ⟦0⟧ and \V[1]')
        self.assertEqual(tokens,['⟦0⟧',r'\V[1]'])
        self.assertEqual(restore(normalize_markers('기호 ⟦0⟧ 값 ⟦1⟧'),tokens),r'기호 ⟦0⟧ 값 \V[1]')
        from rpgmaker_tokens import validate
        self.assertIn('unrestored placeholder',validate('Hello','안녕 ⟦3⟧',CFG))

    def test_retry_never_discloses_engine_code_values(self):
        row={'id':'x','source':r'\PY[22]\PX[91]안녕\.다시\PX[77]','kind':'dialogue'}
        note=retry_instruction([row],{'x':'Protected token mismatch: []; output=\'안녕\''},CFG)
        self.assertIn('<rpt000/>',note);self.assertIn('<rpt001/>',note)
        self.assertNotIn('\\PX',note);self.assertNotIn('\\PY',note);self.assertNotIn('\\.',note)
        self.assertNotIn('= ',note)

    def test_kana_retry_uses_previous_translation_without_deleting_kana(self):
        row={'id':'x','source':'おはよう','kind':'dialogue'}
        note=retry_instruction([row],{'x':"['untranslated Japanese text']; output='안녕ッ'"},CFG)
        self.assertIn('교정할 이전 번역: 안녕ッ',note)
        self.assertIn('이미 번역된 한국어',note)

    def test_partial_correction_requires_complete_control_codes_and_existing_korean(self):
        source=r'\PX[80]おはよう\.'
        error="['untranslated Japanese text']; output='안녕ッ<rpt000/>'"
        self.assertEqual(correction_draft(source,error,CFG),'안녕ッ<rpt000/>')
        self.assertIsNone(correction_draft(source,error.replace('<rpt000/>',''),CFG))
        self.assertIsNone(correction_draft(source,error.replace('안녕ッ','おはよう'),CFG))

    def test_partial_correction_sends_korean_draft_instead_of_retranslating_original(self):
        cfg=dict(CFG,model='rpt-hymt2-7b:q6_k',_rpg_retry_errors={'x':"['untranslated Japanese text']; output='안녕ッ<rpt000/>'"})
        row=dict(id='x',source=r'\PX[80]おはよう\.',kind='dialogue',usage='dialogue')
        from contextlib import nullcontext
        with patch('translation.request',return_value={'message':{'content':'[0] 안녕'}}) as request,patch('hy_backend.runtime',side_effect=lambda cfg:nullcontext(cfg)):
            result,_=translate_batch([row],{'source_passage':[{'text':'Other original text'}]},cfg)
        prompt=request.call_args.args[2]['messages'][0]['content']
        self.assertIn('[0] 안녕ッ',prompt);self.assertNotIn('おはよう',prompt)
        self.assertNotIn('Other original text',prompt)
        self.assertNotIn('RPT000',prompt)
        self.assertEqual(result[0]['text'],r'\PX[80]안녕\.')

    def test_fragment_splicing_preserves_codes_and_existing_korean_exactly(self):
        draft='기존 안녕ッ<rpt000/>안녕ッ 문장'
        words,positions=correction_parts(draft)
        self.assertEqual(words,['안녕ッ'])
        self.assertEqual(apply_corrections(draft,positions,{'0':'안녕'}),'기존 안녕<rpt000/>안녕 문장')
        for value in ('안녕ッ','<haha>','안녕\n다시','',None):
            with self.subTest(value=value),self.assertRaises(ValueError):apply_corrections(draft,positions,{'0':value})

    def test_fragment_echoed_punctuation_only_when_original_has_same_suffix(self):
        draft='웃음ッ!!<rpt000/>'
        words,positions=correction_parts(draft)
        self.assertEqual(apply_corrections(draft,positions,{'0':'웃음!!'}),'웃음!!<rpt000/>')
        for value in ('웃음!', '웃음!! 새 문장', '웃음??'):
            if value=='웃음!':
                self.assertEqual(apply_corrections(draft,positions,{'0':value}),'웃음!!<rpt000/>')
            else:
                with self.assertRaises(ValueError):apply_corrections(draft,positions,{'0':value})
        draft='웃음ッ<rpt000/>'
        _,positions=correction_parts(draft)
        with self.assertRaises(ValueError):apply_corrections(draft,positions,{'0':'웃음!!'})

    def test_phrase_crosses_display_codes_but_not_variables_icons_or_unknown_codes(self):
        draft='앞 깜<rpt000/>짝ッ! 뒤 이름<rpt001/>씨ッ 아이<rpt002/>콘ッ 미<rpt003/>지ッ'
        tokens=[r'\.\|',r'\V[8]',r'\I[3]',r'\Custom[9]']
        words,positions=correction_parts(draft,tokens=tokens)
        self.assertEqual(words,['깜<rpt000/>짝ッ!','씨ッ','콘ッ','지ッ'])
        result=apply_corrections(draft,positions,{'0':'깜⟦RPT000⟧짝!','1':'씨','2':'콘','3':'지'})
        self.assertEqual(result,'앞 깜<rpt000/>짝! 뒤 이름<rpt001/>씨 아이<rpt002/>콘 미<rpt003/>지')
        with self.assertRaises(ValueError):apply_corrections(draft,positions,{'0':'깜짝!','1':'씨','2':'콘','3':'지'})

    def test_plain_correction_uses_unique_edit_boundary_without_deleting_kana_itself(self):
        original='가ッ<rpt003/>나!!'
        corrected,placements=align_display_anchors(original,'가나!!')
        self.assertEqual(corrected,'가<rpt003/>나!!')
        self.assertEqual(placements,[dict(anchor='<rpt003/>',source_offset=2,target_offset=1)])
        unchanged,_=align_display_anchors(original,'가ッ나!!')
        self.assertEqual(unchanged,original)
        for original,target in [('가<rpt000/>가','가'),('ab<rpt000/>cd','wxyz')]:
            with self.assertRaises(ValueError):align_display_anchors(original,target)

    def test_plain_joined_correction_keeps_codes_and_text_outside_selected_span(self):
        source=r'가ッ\.나!! 그 다음 대사'
        error="['untranslated Japanese text']; output='가ッ<rpt000/>나!! 그 다음 대사'"
        cfg=dict(CFG,model='rpt-hymt2-7b:q6_k',_rpg_retry_errors={'x':error})
        row=dict(id='x',source=source,kind='dialogue',usage='dialogue')
        from contextlib import nullcontext
        with patch('translation.request',return_value={'message':{'content':'[0] 가나!!'}}) as request,patch('hy_backend.runtime',side_effect=lambda cfg:nullcontext(cfg)):
            result,_=translate_batch([row],{},cfg)
        prompt=request.call_args.args[2]['messages'][0]['content'].split('[Source Text]',1)[1]
        self.assertEqual(prompt.strip(),'[0] 가ッ나!!')
        self.assertNotIn('RPT',prompt)
        self.assertEqual(result[0]['text'],r'가\.나!! 그 다음 대사')

    def test_punctuation_width_and_repetition_do_not_change_original_bytes_or_control_position(self):
        original='가ッ<rpt001/>나！！！！'
        for value in ('가나!!!','가나!!!!!','가나！','가나'):
            with self.subTest(value=value):
                result,positions=align_display_anchors(original,value)
                self.assertEqual(result,'가<rpt001/>나！！！！')
                self.assertEqual(positions[0]['target_offset'],1)
        with self.assertRaises(ValueError):align_display_anchors(original,'가나??')

    def test_joined_phrase_requires_original_anchor_order_and_does_not_copy_next_sentence(self):
        draft='웃ッ<rpt000/>음<rpt001/>소리!!<rpt002/> 다음 대사.'
        words,positions=correction_parts(draft,tokens=[r'\.',r'\C[2]',r'\PX[11]'])
        self.assertEqual(words,['웃ッ<rpt000/>음<rpt001/>소리!!'])
        value='웃<rpt000/>음<rpt001/>소리!!'
        self.assertEqual(apply_corrections(draft,positions,{'0':value}),value+'<rpt002/> 다음 대사.')
        for changed in (value.replace('000','001',1),value+' 다음 대사.',value.replace('<rpt001/>','')):
            with self.assertRaises(ValueError):apply_corrections(draft,positions,{'0':changed})

    def test_last_translation_failure_still_gets_two_independent_correction_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp);row=dict(id='x',source='あッ',kind='dialogue',usage='dialogue',file='test',block='one')
            save_json(project/'data/catalog.json',[row]);seen=[]
            cfg=dict(CFG,model='rpt-hymt2-7b:q6_k',parallel=1)
            residual="['untranslated Japanese text']; output='아ッ'"
            def batch(items,context,active,retry=''):
                phase=active['_rpg_correction_enabled'];seen.append(phase)
                if len(seen)==4:raise ValueError('Response parse error')
                if len(seen)<5:
                    error='Missing response ID' if len(seen)<3 else residual
                    raise BatchTranslationError([],[dict(row=row,error=error)],dict(seconds=0,items=1))
                self.assertEqual(correction_draft(row['source'],active['_rpg_retry_errors']['x'],active),'아ッ')
                return [dict(id='x',source=row['source'],text='아',model=cfg['model'],fingerprint=fingerprint(cfg))],dict(seconds=0,items=1)
            with redirect_stdout(io.StringIO()),patch('translation.translate_batch',side_effect=batch):_translate(project,cfg)
            self.assertEqual(seen,[False,False,False,True,True])
            self.assertEqual(cache(project,cfg)['x']['text'],'아')
            events=[json.loads(x) for x in (project/'data/translation-requests.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual([e['attempt'] for e in events if e.get('retry_phase')=='correction'],[0,1])

    def test_correction_exhaustion_retains_valid_draft_for_future_resume_and_stops(self):
        cfg=dict(CFG,model='rpt-hymt2-7b:q6_k',_rpg_retry_errors={'x':"['untranslated Japanese text']; output='아ッ'"})
        row=dict(id='x',source='あッ',kind='dialogue',usage='dialogue')
        from contextlib import nullcontext
        with patch('translation.request',return_value={'message':{'content':'[0] 아! 다음 대사.'}}),patch('hy_backend.runtime',side_effect=lambda cfg:nullcontext(cfg)):
            with self.assertRaises(BatchTranslationError) as raised:translate_batch([row],{},cfg)
        error=raised.exception.failures[0]['error']
        self.assertEqual(correction_draft(row['source'],error,cfg),'아ッ')

    def test_resume_uses_saved_valid_draft_and_stops_after_two_correction_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp);row=dict(id='x',source='あッ',kind='dialogue',usage='dialogue',file='test',block='one')
            save_json(project/'data/catalog.json',[row]);seen=[]
            residual="['untranslated Japanese text']; output='아ッ'"
            cfg=dict(CFG,model='rpt-hymt2-7b:q6_k',parallel=1,_rpg_previous_failures={'x':residual})
            def batch(items,context,active,retry=''):
                seen.append(active['_rpg_correction_enabled'])
                raise ValueError('Unparseable correction response')
            with redirect_stdout(io.StringIO()),patch('translation.translate_batch',side_effect=batch):_translate(project,cfg)
            self.assertEqual(seen,[True,True])
            record=cache(project,cfg)['x'];self.assertEqual(record['status'],'source_fallback')
            self.assertEqual(correction_draft(row['source'],record['error'],cfg),'아ッ')

    def test_helpers_follow_nested_calls_but_stop_on_story_scripts_cycles_and_unknowns(self):
        c=lambda code,args=[]:dict(code=code,parameters=args)
        events=[None,dict(id=41,list=[c(111,[1,2,0,3,0]),c(231,[1,'image']),c(122,[1,1,0,0,0]),c(412),c(0)]),
                dict(id=42,list=[c(117,[41]),c(0)]),dict(id=43,list=[c(101),c(401,['text']),c(0)]),
                dict(id=44,list=[c(355,['unknown script'])]),dict(id=45,list=[c(117,[46])]),dict(id=46,list=[c(117,[45])]),
                dict(id=47,list=[c(111,[12,'unknown script'])]),dict(id=48,list=[c(201,[0,1,2,3])])]
        helpers=Helpers(events)
        self.assertTrue(helpers.check(41));self.assertTrue(helpers.check(42))
        for uid in (43,44,45,46,47,48,99):self.assertFalse(helpers.check(uid))

    def test_metadata_migration_retains_ids_cache_and_keeps_branches_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);base=p/'data/rpg-source';(base/'data').mkdir(parents=True)
            command=lambda code,args,indent=0:dict(code=code,parameters=args,indent=indent)
            commands=[command(101,[]),command(401,['first']),command(117,[42]),command(101,[]),command(401,['second']),
                      command(102,[['yes','no']]),command(402,[0,'yes']),command(401,['third'],1),
                      command(402,[1,'no']),command(401,['fourth'],1)]
            save_json(base/'data/Map001.json',{'events':[None,{'pages':[{'list':commands}]}]})
            save_json(base/'data/CommonEvents.json',[None,{'id':42,'list':[command(231,[1,'image']),command(0,[])]}])
            rows=[dict(id=str(i),source=text,kind='dialogue',usage='dialogue',file='data/Map001.json',block='old'+str(i),
                       location={'ptr':'events[1].pages[0].list[%d].parameters[0]'%idx})
                  for i,(idx,text) in enumerate([(1,'first'),(4,'second'),(7,'third'),(9,'fourth')])]
            save_json(p/'data/catalog.json',rows);saved=b'preserved user cache\n';(p/'data/translations.jsonl').write_bytes(saved)
            with redirect_stdout(io.StringIO()):audit=refresh(p,CFG)
            new=json.loads((p/'data/catalog.json').read_text(encoding='utf-8'))
            self.assertEqual(new[0]['block'],new[1]['block']);self.assertNotEqual(new[2]['block'],new[3]['block'])
            self.assertEqual([(r['id'],r['source'],r['location']) for r in rows],[(r['id'],r['source'],r['location']) for r in new])
            self.assertEqual((p/'data/translations.jsonl').read_bytes(),saved)
            index=SourceContext(new,p,CFG);context=index.context([new[1]],CFG)
            self.assertTrue(any(r.get('text')=='first' for r in context['source_passage']))
            with redirect_stdout(io.StringIO()):self.assertEqual(refresh(p,CFG),audit)

    def test_resume_retries_failed_only_and_retains_successful_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);rows=[dict(id='ok',source='Hello',kind='dialogue',file='test',block='same'),dict(id='bad',source='Bye',kind='dialogue',file='test',block='same')]
            save_json(p/'data/catalog.json',rows)
            records=[dict(id=r['id'],source=r['source'],text='안녕' if r['id']=='ok' else r['source'],model='synthetic',fingerprint=fingerprint(CFG),
                          status='translated' if r['id']=='ok' else 'source_fallback',error='no Korean translation') for r in rows]
            path=p/'data/translations.jsonl';raw=''.join(json.dumps(r)+'\n' for r in records).encode();path.write_bytes(raw);seen=[]
            def batch(items,context,cfg,retry=''):
                seen.extend(r['id'] for r in items);self.assertIn('source_passage',context)
                return [dict(records[1],text='잘 가',status='translated',error=None)],{'seconds':0,'items':1}
            with redirect_stdout(io.StringIO()),patch('runtime_recovery.run',side_effect=lambda project,cfg,op,count:op(cfg)),patch('translation.translate_batch',side_effect=batch):
                translate(p,CFG)
            self.assertEqual(seen,['bad']);self.assertTrue(path.read_bytes().startswith(raw));self.assertEqual(cache(p,CFG)['ok']['text'],'안녕')

    def test_rpg_batch_repairs_prefixed_header_and_escaped_markers(self):
        cfg=dict(CFG,model='rpt-hymt2-7b:q6_k')
        row=dict(id='x',source=r'\PX[90]こんにちは\.',kind='dialogue',usage='dialogue')
        response={'message':{'content':r'[nr_0] 안녕하세요\\<rpt000/\\>'}}
        with patch('translation.request',return_value=response),patch('request_budget.fit',wraps=__import__('request_budget').fit):
            # Avoid a real runtime/tokenizer in this protocol unit test.
            from contextlib import nullcontext
            with patch('hy_backend.runtime',side_effect=lambda cfg:nullcontext(cfg)):
                result,_=translate_batch([row],{},cfg)
        self.assertEqual(result[0]['text'],r'\PX[90]안녕하세요\.')


if __name__=='__main__':unittest.main()
