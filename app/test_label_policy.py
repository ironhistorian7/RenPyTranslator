"""Source-purpose and invented-response tests only; no game/model execution."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from automatic import add_literal_templates,outline
from engine import save_json
from failed_repair import saved_output
from hy_backend import body, DEFAULT_MODEL
from label_policy import annotate, context_for, expansion_errors
from source_context import SourceContext
from translation import read_catalog, translate_batch, _translate, fingerprint, BatchTranslationError, retry_instruction

CFG={'model':DEFAULT_MODEL,'language':'korean','endpoint':'http://127.0.0.1:1','num_ctx':16384,'parallel':1,'batch_size':8}


def row(uid='label',source='Secretary',usage='speaker_label'):
    return dict(id=uid,file='labels.rpy',block='strings',kind='string',source=source,usage=usage)


def context():
    return {'translation_guidance':{'global':'UNRELATED STORY BACKGROUND',
             'file':'UNRELATED CHAPTER','scene':'UNRELATED CURRENT EVENT',
             'terms':[{'source':'Secretary','target':'비서','meaning':'UNRELATED CHARACTER STORY'},
                      {'source':'Other','target':'다른 것'}]},
            'source_passage':[{'text':'UNRELATED NEIGHBOR'}],
            'current_state':'UNRELATED RELATIONSHIP',
            '_context_audit':{'scope':'chapter','files':{'global.txt':'global-hash','terms.txt':'terms-hash','chapter.txt':'chapter-hash'}}}


class LabelPolicyTests(unittest.TestCase):
    def test_source_evidence_not_length_distinguishes_labels_choices_and_dialogue(self):
        items=[row(source='Pilot'),row('choice','Pilot'),row('short','Yes'),row('button','Inspect'),row('long','This is a long screen description.')]
        items[0].pop('usage');items[1].update(kind='dialogue');items[2].update(kind='dialogue')
        items[3].pop('usage');items[4].pop('usage')
        result=annotate(items,{'characters':{'p':'Pilot'},'screen_literals':[{'source':'Inspect','widget':'textbutton'},{'source':items[4]['source'],'widget':'text'}]})
        self.assertEqual([r.get('usage') for r in result],['speaker_label','dialogue','dialogue','ui_label',None])
        choice=annotate([dict(items[0])],{'characters':{'p':'Pilot'}},{'choices':['Pilot']})[0]
        self.assertEqual(choice['usage'],'choice')

    def test_prepare_records_character_and_control_purposes_without_engine(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp);save_json(project/'data/catalog.json',[])
            (project/'data/templates').mkdir()
            scripts={'story.rpy':'define attendant = Character(_("Attendant"))\nscreen panel():\n    textbutton _("Inspect")\n    text "A long description."\n'}
            with patch('automatic.script_sources',return_value=scripts):
                add_literal_templates(project,CFG)
            records={r['source']:r for r in read_catalog(project)}
            self.assertEqual(records['Attendant']['usage'],'speaker_label')
            self.assertEqual(records['Inspect']['usage'],'ui_label')
            self.assertNotIn('usage',records['A long description.'])

    def test_multiline_keyword_character_names_and_dynamic_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            scripts={'definitions.rpy':'define -10 officer = Character(\n    _("Officer"),\n    color="#fff"\n)\ndefine messenger = Character(name="Messenger")\ndefine player = Character("player_name", dynamic=True)\n'}
            with patch('automatic.script_sources',return_value=scripts):
                structure=outline(Path(tmp))
            self.assertEqual(structure['characters']['officer'],'Officer')
            self.assertEqual(structure['characters']['messenger'],'Messenger')
            self.assertEqual(structure['characters']['player'],'(dynamic or unnamed)')

    def test_legacy_catalog_purposes_from_saved_metadata_without_rewriting_or_game_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp);legacy=row();legacy.pop('usage')
            save_json(project/'data/catalog.json',[legacy])
            save_json(project/'data/source-outline.json',{'characters':{'assistant':'Secretary'}})
            before=(project/'data/catalog.json').read_bytes()
            with patch('automatic.script_sources',side_effect=AssertionError('No game read')):
                records=read_catalog(project)
            self.assertEqual(records[0]['id'],legacy['id'])
            self.assertEqual(records[0]['usage'],'speaker_label')
            self.assertEqual((project/'data/catalog.json').read_bytes(),before)

    def test_minimal_label_context_drops_stories_and_term_explanations(self):
        original=context();result,policy=context_for([row()],original)
        self.assertEqual(result['translation_guidance'],{'terms':[{'source':'Secretary','target':'비서'}]})
        self.assertEqual(result['_context_audit']['files'],{'terms.txt':'terms-hash'})
        self.assertIn('translation_guidance.global',policy['excluded_story_parts'])
        self.assertNotIn('UNRELATED',json.dumps(result))
        self.assertIn('global',original['translation_guidance'])

    def test_dialogue_and_choice_context_unchanged(self):
        for usage in ('dialogue','choice',None):
            with self.subTest(usage=usage):
                result,policy=context_for([row(usage=usage)],context())
                self.assertEqual(result,context())
                self.assertEqual(policy['mode'],'story_or_text')

    def test_different_purposes_cannot_share_one_request_group(self):
        items=[row('name'),row('ui','Inspect','ui_label'),row('choice','Yes','choice')]
        index=SourceContext(items)
        self.assertFalse(index.same_scope(items[0],items[1]))
        self.assertFalse(index.same_scope(items[1],items[2]))

    def test_direct_hy_request_preserves_label_purpose_and_spellings_only(self):
        ctx=context();ctx['person_name_spellings']={'Secretary':'비서'}
        request=body([{'id':'0','kind':'string','usage':'speaker_label','text':'Secretary'}],ctx,CFG,{},'')
        prompt=request['messages'][0]['content']
        self.assertIn('화자 이름표',prompt)
        self.assertIn('speaker_label',prompt)
        self.assertIn('비서',prompt)
        self.assertNotIn('UNRELATED',prompt)
        self.assertIn('person_name_spellings',prompt)

    def test_general_model_also_omits_story_style_and_passes_purpose(self):
        with tempfile.TemporaryDirectory() as tmp,patch('translation.request',return_value={'message':{'content':'{"0":"비서"}'}}) as api:
            cfg=dict(CFG,model='fake',style='UNRELATED CUSTOM STORY STYLE',_trace_dir=tmp)
            result,_=translate_batch([row()],context(),cfg)
            request=api.call_args.args[2]
            self.assertNotIn('UNRELATED',json.dumps(request))
            payload=json.loads(request['messages'][1]['content'])
            self.assertEqual(payload['items'][0]['usage'],'speaker_label')
            self.assertEqual(result[0]['text'],'비서')
            event=json.loads((Path(tmp)/'translation-requests.jsonl').read_text(encoding='utf-8').splitlines()[0])
            self.assertEqual(event['context_policy']['mode'],'labels_only')
            self.assertEqual(event['item_purposes'][0]['usage'],'speaker_label')

    def test_invented_speech_rejected_without_a_name_length_cap(self):
        for target in ('비서: “준비가 다 되었어요. 이제 낭독할 거예요.”','준비가 다 되었습니다.','비서\n준비됐어요.'):
            self.assertTrue(expansion_errors(row(),target))
        self.assertTrue(expansion_errors(row(source='Mr. Example'),'미스터: “준비됐어요.”'))
        for source,target in [('Secretary','비서'),('Captain','선장: 김'),('Very Long Character Name','아주 긴 이름을 가진 인물'),('Go','계속하세요'),('Who are you?','누구세요?')]:
            self.assertEqual(expansion_errors(row(source=source),target),[])
        self.assertEqual(expansion_errors(row(usage='dialogue'),'“준비됐어요.”'),[])

    def test_partial_success_retained_but_expanded_label_rejected(self):
        reply={'message':{'content':'[0] 비서: “준비됐어요.”\n[1] 선장'}}
        with tempfile.TemporaryDirectory() as tmp,patch('translation.request',return_value=reply):
            with self.assertRaises(BatchTranslationError) as raised:
                translate_batch([row(),row('other','Captain')],context(),dict(CFG,_trace_dir=tmp))
            self.assertEqual([r['id'] for r in raised.exception.output],['other'])
            self.assertEqual(raised.exception.failures[0]['row']['id'],'label')
            self.assertIn('label expanded',raised.exception.failures[0]['error'])
            self.assertIn('noun phrase',retry_instruction([row()],{'label':raised.exception.failures[0]['error']}))

    def test_saved_bad_label_is_not_recovered_as_a_success(self):
        error='label expanded; output=\'비서: “준비됐어요.”\''
        self.assertIsNone(saved_output('Secretary',error,CFG,'speaker_label'))

    def exercise_queue(self,always_bad=False):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp);items=[row(),row('other','Captain')]
            save_json(project/'data/catalog.json',items)
            save_json(project/'data/source-outline.json',{'characters':{'s':'Secretary','c':'Captain'}})
            calls=[]
            def request(endpoint,route,payload):
                source=payload['messages'][0]['content'].split('[Source Text]\n',1)[1]
                calls.append(source)
                if len(calls)==1:return {'message':{'content':'[0] 비서: “준비됐어요.”\n[1] 선장'}}
                self.assertEqual(source.splitlines(),['[0] Secretary'])
                return {'message':{'content':'[0] 비서: “준비됐어요.”' if always_bad else '[0] 비서'}}
            with patch('translation.request',side_effect=request),patch('name_hints.ensure_metadata',return_value={'names':{}}),patch('automatic.script_sources',side_effect=AssertionError('No game read')):
                _translate(project,CFG,rows=items,known={})
            cached=[json.loads(s) for s in (project/'data/translations.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual(cached[0]['id'],'other')
            self.assertEqual(cached[-1]['text'],'Secretary' if always_bad else '비서')
            if always_bad:self.assertEqual(cached[-1]['status'],'source_fallback')
            self.assertEqual(len(calls),3 if always_bad else 2)
            before=(project/'data/translations.jsonl').read_bytes()
            with patch('translation.request',side_effect=AssertionError('Saved entries must not retranslate')),patch('name_hints.ensure_metadata',return_value={'names':{}}):
                _translate(project,CFG)
            self.assertEqual((project/'data/translations.jsonl').read_bytes(),before)

    def test_only_bad_label_retries_and_completed_entries_resume(self):
        self.exercise_queue()

    def test_failed_label_keeps_original_after_two_retries_and_job_continues(self):
        self.exercise_queue(always_bad=True)


if __name__=='__main__':unittest.main()
