"""Synthetic source strings and fake counters only; no model or file access."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from hy_backend import body
from request_budget import BatchNeedsSplit, fit
from translation_output import estimate_output


class TranslationOutputTests(unittest.TestCase):
    def inputs(self, text, count=1):
        return [dict(id=str(index),text=text,kind='dialogue',usage='dialogue',
                     speaker='unknown',name_hints={},movable_placeholders=[])
                for index in range(count)]

    def cfg(self, **changes):
        return dict(model='synthetic',source_language='english',num_ctx=16384,parallel=2,**changes)

    def test_short_requests_keep_the_existing_reserve(self):
        self.assertEqual(estimate_output(self.inputs('An invented short line.')),1800)
        self.assertEqual(estimate_output(self.inputs('短い架空の文。',8)),1800)

    def test_long_english_and_japanese_both_receive_more_output_space(self):
        for text in ('Invented longer source words. '*150, 'これは架空の長い文章です。'*150):
            with self.subTest(script=text[:12]):
                self.assertGreater(estimate_output(self.inputs(text)),1800)

    def test_estimate_does_not_depend_on_a_japanese_language_switch(self):
        inputs=self.inputs('An invented longer phrase. '*150)
        self.assertEqual(estimate_output(inputs,{'source_language':'english'}),
                         estimate_output(inputs,{'source_language':'japanese'}))

    def test_expanded_model_items_and_response_ids_contribute(self):
        one=estimate_output(self.inputs('架空の文。'*250))
        expanded=estimate_output(self.inputs('架空の文。'*250,4))
        self.assertGreater(expanded,one)
        short_parts=self.inputs('',500)
        self.assertGreater(estimate_output(short_parts),1800)

    def test_longer_input_and_marker_payload_never_reduce_the_reserve(self):
        original=self.inputs('An invented phrase. '*150)
        longer=self.inputs(original[0]['text']+' <rpt000/> '*250)
        self.assertGreater(estimate_output(longer),estimate_output(original))
        self.assertGreater(estimate_output(self.inputs('⟦RPT000⟧ '*500)),1800)

    def test_optional_counter_sees_only_known_final_input_records(self):
        items=self.inputs('A source line.',2)
        seen=[]
        def count(text):
            seen.append(text)
            return 1500
        self.assertGreater(estimate_output(items,count=count),1800)
        self.assertEqual(seen,['[0] A source line.\n','[1] A source line.\n'])
        # A compact source tokenizer is not evidence of compact Korean output.
        long=self.inputs('架空の文。'*300)
        self.assertEqual(estimate_output(long,count=lambda _:1),estimate_output(long))

    def test_context_window_does_not_silently_lower_a_required_reserve(self):
        items=self.inputs('An invented phrase. '*400)
        reserve=estimate_output(items,{'num_ctx':16384})
        self.assertGreater(reserve,1800)
        self.assertEqual(estimate_output(items,{'num_ctx':1024}),reserve)
        self.assertEqual(estimate_output(self.inputs('Short.'),{'_output_minimum':4096}),4096)

    def test_hy_body_uses_final_inputs_without_changing_window_workers_or_sampling(self):
        cfg=self.cfg()
        before=deepcopy(cfg)
        items=self.inputs('An invented phrase. '*150)
        request=body(items,{'source_passage':[{'text':'Reference-only text.'}]},cfg,{},'')
        self.assertEqual(request['options']['num_predict'],estimate_output(items,cfg))
        self.assertGreater(request['options']['num_predict'],1800)
        self.assertEqual(request['options']['num_ctx'],16384)
        self.assertEqual(request['options']['temperature'],0.7)
        self.assertEqual(request['options']['top_p'],0.6)
        self.assertEqual(request['options']['top_k'],20)
        self.assertEqual(cfg,before)
        self.assertEqual(cfg['parallel'],2)

    def test_reference_background_does_not_count_as_requested_output(self):
        cfg=self.cfg()
        items=self.inputs('Short source.')
        small=body(items,{},cfg,{},'')
        large=body(items,{'source_passage':[{'text':'Long reference. '*3000}]},cfg,{},'')
        self.assertEqual(small['options']['num_predict'],large['options']['num_predict'])

    def test_existing_window_fit_splits_instead_of_shrinking_dynamic_output(self):
        class Counter:
            method='synthetic_known_input_counter'
            reason=None
            def count(self,text):return len(text)
        cfg=self.cfg(_allow_batch_split=True)
        items=self.inputs('An invented phrase. '*250,2)
        reserve=estimate_output(items,cfg)
        def build(context):
            return {'messages':[{'role':'user','content':'s'*9000}],
                    'options':{'num_ctx':16384,'num_predict':reserve}}
        with patch('request_budget.counter',return_value=Counter()):
            with self.assertRaises(BatchNeedsSplit) as caught:fit({},cfg,build)
        self.assertEqual(caught.exception.context_budget['output_reserved'],reserve)
        self.assertGreater(reserve,1800)

    def test_rpg_wire_inputs_are_the_values_budgeted_by_hy(self):
        from rpgmaker_protocol import wire_inputs
        cfg=self.cfg(engine='rpgmaker')
        items=self.inputs('An invented phrase. '*80+'<rpt000/>'*400)
        request=body(items,{},cfg,{},'')
        self.assertEqual(request['options']['num_predict'],estimate_output(wire_inputs(items),cfg))
        self.assertIn('⟦RPT000⟧',request['messages'][0]['content'])


if __name__=='__main__':unittest.main()
