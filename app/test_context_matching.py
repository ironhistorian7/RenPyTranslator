"""Scoped context regressions using invented dialogue only; no model or game."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from context_notes import ContextNotes, anchor_match, current_state, scope_labels
from request_budget import fit


class ContextMatchingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.project=Path(self.tmp.name)
        self.root=self.project/'staging/game/context';self.root.mkdir(parents=True)
        self.cfg={'_repair':True,'num_ctx':16384}
        self.rows=[dict(id=str(i),file='example.rpy',source_file='game/example.rpy',
                       source_line=i+10,block=label+'_%08x'%i,kind='dialogue',speaker='n',source=text)
                   for i,(label,text) in enumerate([
                       ('meeting','A conversation begins.'),('meeting','Really.'),
                       ('meeting','Just leave me alone.'),('later','A later conversation.'),
                       ('later','I will.'),('later','Just leave me alone.')])]

    def notes(self,text):
        (self.root/'example.rpy.txt').write_text(text,encoding='utf-8')
        return ContextNotes(self.project,self.cfg,self.rows)

    def test_short_exact_anchor_not_short_substring(self):
        self.assertTrue(anchor_match('Really.','Really.'))
        self.assertTrue(anchor_match('I will.','I will.'))
        self.assertFalse(anchor_match('I','I will.'))
        self.assertFalse(anchor_match('','Something.'))

    def test_backticks_plural_and_metadata_labels(self):
        self.assertEqual(scope_labels('label `meeting`',''),['meeting'])
        self.assertEqual(scope_labels('labels: `meeting`, later',''),['meeting','later'])
        self.assertEqual(scope_labels('labels:meeting … later',''),['meeting','later'])
        self.assertEqual(scope_labels('"A conversation begins." ~ "Really."','Management:\n- labels: meeting'),['meeting'])
        self.assertEqual(scope_labels('label meeting','Management:\n- labels: later'),['meeting'])

    def test_short_ending_maps_entire_scene(self):
        notes=self.notes('Scene ID: greeting\nApplies to: label `meeting` "A conversation begins." ~ "Really."\nInjection context:\nTwo old friends meet.')
        self.assertEqual(set(notes.scenes),{'0','1'})
        self.assertEqual(notes.report['scope_details'][0]['end_matches'],1)

    def test_metadata_scopes_repeated_ending_and_does_not_enter_prompt(self):
        notes=self.notes('Scene ID: greeting\nApplies to: "A conversation begins." ~ "Just leave me alone."\nInjection context:\nA tense conversation.\nManagement:\n- labels: meeting\nDO NOT INJECT THIS')
        self.assertEqual(set(notes.scenes),{'0','1','2'})
        ctx=notes.context(self.rows[:1],self.cfg)
        self.assertNotIn('DO NOT INJECT',json.dumps(ctx))
        self.assertNotIn('labels:',json.dumps(ctx))

    def test_repeated_ending_prefers_unique_match_in_starting_label(self):
        notes=self.notes('Scene ID: greeting\nApplies to: "A conversation begins." ~ "Just leave me alone."\nInjection context:\nA tense conversation.')
        self.assertEqual(set(notes.scenes),{'0','1','2'})
        self.assertEqual(notes.report['scope_details'][0]['end_resolution'],'unique within starting label')

    def test_repeated_ending_in_same_label_remains_explicitly_ambiguous(self):
        self.rows[1]['source']='Just leave me alone.'
        notes=self.notes('Scene ID: greeting\nApplies to: label meeting "A conversation begins." ~ "Just leave me alone."\nInjection context:\nA tense conversation.')
        self.assertFalse(notes.scenes)
        self.assertEqual(notes.report['scope_details'][0]['end_matches'],2)
        self.assertIn('repeated',notes.report['scope_details'][0]['reason'])

    def test_file_overview_and_directional_register_are_preserved(self):
        notes=self.notes('## File overview\nApplies to: entire example.rpy\nInjection context:\nShared file background.\nManagement:\nUNSENT EVIDENCE\n## Scene ID: greeting\nApplies to: label `meeting`\nInjection context:\n- 말투 A→B: 정중한 존댓말.\n- 말투 B→A: 친근한 반말.')
        ctx=notes.context(self.rows[:1],self.cfg)
        self.assertEqual(ctx['translation_guidance']['file'],'Shared file background.')
        self.assertIn('말투 A→B',ctx['current_state']);self.assertIn('말투 B→A',ctx['current_state'])
        self.assertNotIn('UNSENT',json.dumps(ctx))
        later=notes.context(self.rows[-1:],self.cfg)
        self.assertIn('file',later['translation_guidance']);self.assertNotIn('scene',later['translation_guidance'])
        self.assertNotIn('current_state',later)

    def test_empty_catalog_label_is_reported_without_fake_anchor_failure(self):
        notes=self.notes('Scene ID: setup\nApplies to: label `setup`\nInjection context:\nSetup only.')
        self.assertFalse(notes.scenes)
        self.assertEqual(notes.report['scope_details'][0]['status'],'no_catalog_entries')
        self.assertFalse(any('scope unmatched' in s for s in notes.report['issues']))

    def test_state_child_keeps_label_and_explicit_ids(self):
        notes=self.notes('Scene ID: conversation\nInjection context:\nShared facts.\nState ID: first\nStart ID: 0\nEnd ID: 1\nInjection context:\n말투 A→B: 존댓말.\nManagement:\nlabels: meeting\nState ID: next\nStart ID: 3\nEnd ID: 5\nInjection context:\n말투 A→B: 반말.\nManagement:\nlabels: later')
        self.assertEqual(set(notes.scenes),{'0','1','3','4','5'})
        self.assertFalse(notes.same_scope(self.rows[0],self.rows[3]))
        self.assertNotIn('반말',notes.context(self.rows[:1],self.cfg)['current_state'])

    def test_file_overview_can_be_reduced_by_shared_request_budget(self):
        class Counter:
            method='synthetic';reason=None
            def count(self,text):return len(text)
        ctx={'translation_guidance':{'file':'x'*3000,'scene':'Near scene.'}}
        build=lambda c:{'messages':[{'role':'user','content':json.dumps(c)+' SOURCE'}],
                        'options':{'num_ctx':500,'num_predict':100}}
        with patch('request_budget.counter',return_value=Counter()):body,meta=fit(ctx,{},build)
        self.assertIn('Near scene.',body['messages'][0]['content'])
        self.assertEqual(meta['dropped'][0]['part'],'TXT:file')


if __name__=='__main__':unittest.main()
