"""CPU-only regression tests for nearby source-context selection."""
import copy
import json
import unittest
from unittest.mock import patch
from source_context import SourceContext, token_estimate

CFG = {'engine':'kirikiri', 'model':'synthetic-hy', 'source_language':'english',
       'language':'korean', 'num_ctx':16384, 'parallel':2, 'kv_cache_type':'q8_0',
       'context_txt_enabled':True}

def make_rows(count=300):
    return [dict(id=str(i), file='scene.scn', block='scene:1', kind='dialogue',
                 usage='dialogue', dialect='kagex_psb', speaker='Narrator',
                 source=f'Complete synthetic sentence number {i}.') for i in range(count)]

def item(row):
    return {'speaker':row['speaker'], 'text':row['source']}

def context(rows, targets, cfg=CFG):
    return SourceContext(rows).context([rows[i] for i in targets], cfg)

def external_cost(passage):
    return sum(token_estimate(json.dumps(p,ensure_ascii=False)) for p in passage if 'text' in p)

class KiriContextBudgetTests(unittest.TestCase):
    def test_eight_before_four_after_in_300_line_group(self):
        rows=make_rows(); before=copy.deepcopy(CFG)
        result=context(rows,[150]); passage=result['source_passage']
        self.assertEqual(passage,[*(item(rows[i]) for i in range(142,150)),
                                  {'target_id':'0'},*(item(rows[i]) for i in range(151,155))])
        self.assertLessEqual(external_cost(passage),2000)
        self.assertEqual(CFG,before)
        audit=result['_context_audit']
        selected=audit['surrounding_selection']
        self.assertEqual((selected['selected_before'],selected['selected_after']),(8,4))
        self.assertEqual(selected['estimated_surrounding_tokens'],external_cost(passage))
        self.assertEqual((selected['stop_before'],selected['stop_after']),('before_items','after_items'))
        self.assertTrue(all(r['reason']=='policy_cap' for r in audit['preselection_dropped']))
    def test_2000_budget_counts_serialized_complete_items(self):
        rows=make_rows(220)
        for row in rows:row['source'] = 'Long sentence %s: '%row['id']+'words '*105
        passage=context(rows,[110])['source_passage']
        self.assertLessEqual(external_cost(passage),2000)
        texts=[p['text'] for p in passage if 'text' in p]
        self.assertTrue(texts)
        self.assertTrue(all(t in [r['source'] for r in rows] for t in texts))
        self.assertEqual(passage.count({'target_id':'0'}),1)
    def test_oversized_immediate_neighbor_stops_its_direction(self):
        rows=make_rows();rows[149]['source']='Oversized '*1000
        passage=context(rows,[150])['source_passage']
        self.assertEqual(passage,[{'target_id':'0'},*(item(rows[i]) for i in range(151,155))])
        self.assertLessEqual(external_cost(passage),2000)
        rows=make_rows();rows[152]['source']='Oversized '*1000
        passage=context(rows,[150])['source_passage']
        self.assertEqual(passage,[*(item(rows[i]) for i in range(142,150)),{'target_id':'0'},item(rows[151])])
    def test_pending_targets_and_cached_intervening_sentence_preserved(self):
        rows=make_rows(); rows[151]['source']='Cached mandatory full sentence. '*300
        passage=context(rows,[150,152])['source_passage']
        first=passage.index({'target_id':'0'});last=passage.index({'target_id':'1'})
        self.assertEqual(passage[first+1:last],[item(rows[151])])
        self.assertLessEqual(external_cost(passage[:first]+passage[last+1:]),2000)
        self.assertLessEqual(first,8);self.assertLessEqual(len(passage)-last-1,4)
    def test_scope_and_labels_stop_context(self):
        rows=make_rows(220)
        for row in rows[:110]:row['context_scope']='before'
        for row in rows[110:]:row['context_scope']='after'
        index=SourceContext(rows,cfg=CFG)
        self.assertFalse(index.same_scope(rows[109],rows[110]))
        passage=index.context([rows[110]],CFG)['source_passage']
        self.assertEqual(passage,[{'target_id':'0'},*(item(rows[i]) for i in range(111,115))])
        rows=make_rows(220)
        for row in rows[110:]:row['block']='other-label'
        index=SourceContext(rows,cfg=CFG)
        self.assertFalse(index.same_scope(rows[109],rows[110]))
        self.assertEqual(index.context([rows[110]],CFG)['source_passage'][0],{'target_id':'0'})

    def test_isolated_target_keeps_zero_selection_audit(self):
        rows=make_rows(1)
        selected=context(rows,[0])
        self.assertEqual(selected['source_passage'],[{'target_id':'0'}])
        audit=selected['_context_audit']
        self.assertEqual(audit['preselection_dropped'],[])
        self.assertEqual(audit['surrounding_selection']['selected_before'],0)
        self.assertEqual(audit['surrounding_selection']['selected_after'],0)
        self.assertEqual(audit['surrounding_selection']['stop_before'],'scope_boundary')
        self.assertEqual(audit['surrounding_selection']['stop_after'],'scope_boundary')
    def test_final_request_budget_still_checks_and_splits(self):
        from request_budget import fit, BatchNeedsSplit
        class SyntheticCounter:
            method='test'; reason=None
            def count(self,text): return len(text)
        rows=make_rows(); selected=context(rows,[150])
        def build(candidate):
            return {'messages':[{'content':'X'*2000+json.dumps(candidate,ensure_ascii=False)}],
                    'options':{'num_predict':100,'num_ctx':1024}}
        with patch('request_budget.counter',return_value=SyntheticCounter()):
            with self.assertRaises(BatchNeedsSplit):
                fit(selected,dict(CFG,_allow_batch_split=True),build)

    def test_txt_notes_and_instruction_settings_are_retained(self):
        rows=make_rows()
        notes={'translation_guidance':{'global':'Keep the supplied narrator style.',
                                      'scene':'Preserve the stated relationship.'},
               'current_state':'A confirmed state supplied by the user.'}
        index=SourceContext(rows)
        index.notes=unittest.mock.Mock()
        index.notes.context.return_value=copy.deepcopy(notes)
        index.notes.same_scope.return_value=True
        result=index.context([rows[150]],CFG)
        self.assertEqual({key:result[key] for key in notes},notes)
        self.assertEqual(len(result['source_passage']),13)
        self.assertEqual(result['_context_audit']['surrounding_selection']['estimated_token_limit'],2000)

    def test_model_independence_and_rpgmaker_unchanged(self):
        rows=make_rows(); index=SourceContext(rows)
        values=[index.context([rows[150]],dict(CFG,model=model))['source_passage']
                for model in ('hy-mt2','gemma','qwen')]
        self.assertEqual(values[0],values[1]);self.assertEqual(values[1],values[2])
        cfg=dict(CFG,engine='rpgmaker')
        output=index.context([rows[150]],cfg)['source_passage']
        self.assertGreater(len(output),13)
        self.assertEqual(output,index.context([rows[150]],dict(cfg,model='another-model'))['source_passage'])

    def test_renpy_long_scope_limits_only_external_context_in_both_languages(self):
        for language, text in [('english', 'A complete English sentence.'), ('japanese', 'これは日本語の台詞です。')]:
            with self.subTest(language=language):
                rows = make_rows(600)
                for row in rows:
                    row.update(file='sample.rpy', source=f"{row['id']}. {text}")
                rows[301]['source'] = text * 200
                cfg = dict(CFG, engine='renpy', source_language=language)
                result = context(rows, [300, 302], cfg)
                passage = result['source_passage']
                self.assertEqual(passage, [*(item(r) for r in rows[292:300]), {'target_id':'0'},
                                           item(rows[301]), {'target_id':'1'}, *(item(r) for r in rows[303:307])])
                self.assertTrue(all(d['reason']=='policy_cap' for d in result['_context_audit']['preselection_dropped']))

if __name__=='__main__':unittest.main()
