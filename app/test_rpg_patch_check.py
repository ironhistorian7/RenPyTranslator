"""Synthetic RPG JSON only; no game, application, model, or real cache execution."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gameloc_vendor import load
load()
from gameloc.model import Project, Entry, Location
from patch_validation import _rpg_check


class RPGPatchCheckTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root/'project'
        self.artifact = self.project/'data/validation-runs/synthetic'
        self.artifact.mkdir(parents=True)
        self.cfg = {'engine':'rpgmaker', 'model':'synthetic', 'source_language':'english',
                    'language':'korean', 'show_reference':True}
        self.rows = []
        self.known = {}
        self.entries = []
        self.identifiers = []
        self.format_version = 1
        self.documents = {}

    def write(self, relative, value):
        target = self.project/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
        return target

    def documents_for(self, original, final=None, file='data/Map001.json'):
        self.documents[file] = (deepcopy(original), deepcopy(original if final is None else final))
        return self.documents[file][1]

    def row(self, uid, source, target, pointer, *, span=None, usage='text', kind='string',
            base_source=None, status=None, file='data/Map001.json'):
        location = Location(file=file,kind='loose',fmt='json',ptr=pointer,span=span)
        row = dict(id=uid,source=source,kind=kind,usage=usage,file=file,location=location.to_dict())
        if base_source is not None:row['base_source'] = base_source
        self.rows.append(row)
        self.entries.append(Entry(id=uid,text=source if base_source is None else base_source,locations=[location]))
        self.known[uid] = dict(source=source,text=target,**({'status':status} if status else {}))
        return row

    def run_check(self, *, validation=None):
        for file,(original,final) in self.documents.items():
            self.write('data/rpg-source/'+file,original)
            self.write('staging/'+file,final)
        self.write('data/rpg-output.json',dict(files=list(self.documents)))
        self.write('data/catalog.json',self.rows)
        upstream = Project(game_root=str(self.project/'data/rpg-source'),engine='rpgmaker',
                           entries=self.entries,identifiers=self.identifiers,
                           stats={'rpt_path_format':self.format_version})
        upstream.save(self.project/'data/rpgmaker-project.json')
        with patch('runtime_assets.runtime_executable',return_value=self.root/'no-node.exe'), \
                patch('app_paths.ROOT',self.root), \
                patch('translation.read_catalog',return_value=self.rows), \
                patch('translation.cache',return_value=self.known), \
                patch('gameloc.apply._reserved_names',return_value=set()), \
                patch('translation.request',side_effect=AssertionError('No model requests')), \
                patch('native_process.run',side_effect=AssertionError('No runtime or parser process')):
            if validation is None:return _rpg_check(self.project,self.cfg,self.artifact)
            with patch('translation.validate_entry',wraps=validation) as checked:
                return _rpg_check(self.project,self.cfg,self.artifact),checked.call_count

    def standard_event(self):
        original = {'events':[{'list':[
            {'code':101,'indent':0,'parameters':['Face',0,0,2,'Speaker']},
            {'code':401,'indent':0,'parameters':['Hello one.']},
            {'code':401,'indent':0,'parameters':['Hello two.']},
            {'code':102,'indent':0,'parameters':[['Go home','Stay here'],0,1,2,0]},
            {'code':122,'indent':0,'parameters':[1,1,0,0,7]},
            {'code':0,'indent':0,'parameters':[]},
        ]}]}
        final = self.documents_for(original)
        commands = final['events'][0]['list']
        commands[1].update(parameters=['첫 문장.'],_rpt_original='Hello one.\nHello two.',
                           _rpt_translation='첫 문장.\n둘째 문장.')
        commands[2]['parameters'] = ['둘째 문장.']
        commands[3]['parameters'][0] = ['집으로 가기','여기 머물기']
        commands[3]['_rpt_choice_originals'] = ['Go home','Stay here']
        commands[3]['_rpt_choice_translations'] = ['집으로 가기','여기 머물기']
        first = 'events[0].list[1].parameters[0]'
        second = 'events[0].list[2].parameters[0]'
        self.row('dialogue','Hello one.\nHello two.','첫 문장.\n둘째 문장.',first,
                 span=[first,second],kind='dialogue',usage='dialogue')
        self.row('choice-a','Go home','집으로 가기','events[0].list[3].parameters[0][0]',usage='choice')
        self.row('choice-b','Stay here','여기 머물기','events[0].list[3].parameters[0][1]',usage='choice')
        return commands

    def error_types(self, result):
        return {item['error_type'] for item in result['failures']}

    def test_catalog_span_choices_and_reference_pairs_match_once(self):
        self.standard_event()
        from translation import validate_entry
        result,calls = self.run_check(validation=validate_entry)
        self.assertEqual(result['status'],'passed')
        self.assertEqual(calls,3)
        self.assertEqual(result['coverage']['catalog_locations'],4)
        self.assertEqual(result['coverage']['control_pairs'],1)
        self.assertEqual(result['coverage']['event_commands'],6)
        self.assertEqual(result['coverage']['choice_groups'],1)
        self.assertEqual(result['coverage']['runtime'],'not_run')

    def test_wrong_rendered_value_is_reported_without_retranslating(self):
        commands = self.standard_event()
        commands[2]['parameters'][0] = '다른 문장.'
        result = self.run_check()
        self.assertEqual(result['status'],'failed')
        self.assertIn('RPGTranslationMismatch',self.error_types(result))

    def test_code_indent_and_nontext_arguments_cannot_change(self):
        for field,value in (('code',123),('indent',2),('parameters',[1,1,0,0,8])):
            with self.subTest(field=field):
                self.rows=[];self.entries=[];self.known={};self.documents={}
                commands=self.standard_event()
                commands[4][field]=value
                result=self.run_check()
                self.assertEqual(result['status'],'failed')
                self.assertIn('RPGStructureMismatch',self.error_types(result))

    def test_event_insertion_and_choice_count_changes_fail(self):
        for change in ('event','choice'):
            with self.subTest(change=change):
                self.rows=[];self.entries=[];self.known={};self.documents={}
                commands=self.standard_event()
                if change=='event':commands.insert(4,{'code':230,'indent':0,'parameters':[5]})
                else:commands[3]['parameters'][0].append('추가 선택지')
                result=self.run_check()
                self.assertEqual(result['status'],'failed')
                self.assertIn('RPGStructureMismatch',self.error_types(result))

    def test_original_choice_array_must_match_each_selected_source(self):
        commands=self.standard_event()
        commands[3]['_rpt_choice_originals']=['Stay here','Go home']
        result=self.run_check()
        self.assertIn('RPGChoiceReferenceMismatch',self.error_types(result))

    def test_choice_display_values_must_match_the_final_array(self):
        commands=self.standard_event()
        commands[3]['_rpt_choice_translations']=['다른 선택지','여기 머물기']
        result=self.run_check()
        self.assertIn('RPGChoiceTranslationMismatch',self.error_types(result))

    def test_display_metadata_is_checked_when_reference_is_disabled(self):
        commands=self.standard_event()
        self.cfg['show_reference']=False
        commands[1].pop('_rpt_original')
        commands[3].pop('_rpt_choice_originals')
        self.assertEqual(self.run_check()['status'],'passed')
        commands[1]['_rpt_translation']='다른 문장.'
        self.assertIn('RPGBilingualMismatch',self.error_types(self.run_check()))

    def test_missing_or_stale_message_reference_is_reported(self):
        for change in ('missing','stale'):
            with self.subTest(change=change):
                self.rows=[];self.entries=[];self.known={};self.documents={}
                commands=self.standard_event()
                if change=='missing':commands[1].pop('_rpt_original')
                else:commands[1]['_rpt_translation']='다른 문장.'
                result=self.run_check()
                self.assertIn('RPGBilingualMismatch',self.error_types(result))

    def test_selected_language_references_can_differ_from_base_json(self):
        original=[{'code':401,'indent':0,'parameters':['元の文。']},
                  {'code':102,'indent':0,'parameters':[['戻る','進む'],0,0,2,0]}]
        final=self.documents_for(original)
        final[0].update(parameters=['돌아와.'],_rpt_original='Come back.',_rpt_translation='돌아와.')
        final[1]['parameters'][0]=['뒤로','앞으로']
        final[1]['_rpt_choice_originals']=['Go back','Go ahead']
        final[1]['_rpt_choice_translations']=['뒤로','앞으로']
        self.row('line','Come back.','돌아와.','[0].parameters[0]',span=['[0].parameters[0]'],
                 base_source='元の文。',kind='dialogue')
        self.row('a','Go back','뒤로','[1].parameters[0][0]',base_source='戻る',usage='choice')
        self.row('b','Go ahead','앞으로','[1].parameters[0][1]',base_source='進む',usage='choice')
        self.assertEqual(self.run_check()['status'],'passed')

    def test_selected_source_fallback_is_not_forced_to_korean(self):
        original=[{'code':102,'indent':0,'parameters':[['戻る'],0,0,2,0]}]
        final=self.documents_for(original)
        final[0]['parameters'][0]=['Go back']
        final[0]['_rpt_choice_translations']=['Go back']
        self.row('a','Go back','Go back','[0].parameters[0][0]',base_source='戻る',usage='choice',status='source_fallback')
        result=self.run_check()
        self.assertEqual(result['status'],'passed')
        self.assertEqual(result['diagnostics'][0]['status'],'original_kept')

    def test_unchanged_choices_are_diagnostics_without_language_guessing(self):
        for source,language in (('Go back','english'),('戻る','japanese')):
            with self.subTest(language=language):
                self.rows=[];self.entries=[];self.known={};self.documents={}
                self.cfg['source_language']=language
                final=self.documents_for([{'code':102,'indent':0,'parameters':[[source],0,0,2,0]}])
                final[0]['_rpt_choice_translations']=[source]
                self.row('a',source,source,'[0].parameters[0][0]',usage='choice',status='source_fallback')
                result=self.run_check()
                self.assertEqual(result['status'],'passed')
                self.assertTrue(result['diagnostics'])

    def test_existing_identifier_guard_is_preserved_and_reported(self):
        final=self.documents_for([{'code':102,'indent':0,'parameters':[['Keep this name'],0,0,2,0]}])
        final[0]['_rpt_choice_translations']=['Keep this name']
        self.row('a','Keep this name','이름 유지','[0].parameters[0][0]',usage='choice')
        self.identifiers=['Keep this name']
        result=self.run_check()
        self.assertEqual(result['status'],'passed')
        self.assertEqual(result['diagnostics'][0]['reason'],'identifier_preserved')

    def test_unlisted_unchanged_choice_is_reported_without_requiring_translation(self):
        original=[{'code':102,'indent':0,'parameters':[['Original option'],0,0,2,0]}]
        final=self.documents_for(original)
        final[0]['_rpt_choice_translations']=['Original option']
        result=self.run_check()
        self.assertEqual(result['status'],'passed')
        self.assertEqual(result['diagnostics'][0]['reason'],'unchanged_choice')

    def test_message_display_metadata_uses_applied_slot_values(self):
        original=[{'code':401,'indent':0,'parameters':['First line.']},
                  {'code':401,'indent':0,'parameters':['Second line.']}]
        final=self.documents_for(original)
        final[0].update(parameters=['번역 문장.'],_rpt_original='First line.\nSecond line.',
                        _rpt_translation='번역 문장.\n')
        final[1]['parameters']=['']
        pointers=['[0].parameters[0]','[1].parameters[0]']
        self.row('a','First line.\nSecond line.','번역 문장.',pointers[0],span=pointers,kind='dialogue')
        # This fixture isolates the renderer's existing slot-distribution contract.
        with patch('translation.validate_entry',return_value=[]):
            self.assertEqual(self.run_check()['status'],'passed')

    def test_nested_json_key_addresses_and_nontext_fields_are_preserved(self):
        original={'a.b':json.dumps({'!':{'text':'Some words.','count':3}})}
        final=self.documents_for(original)
        final['a.b']=json.dumps({'!':{'text':'번역 문장.','count':3}},ensure_ascii=False)
        self.row('a','Some words.','번역 문장.','["a.b"]!["!"].text')
        self.assertEqual(self.run_check()['status'],'passed')
        final['a.b']=json.dumps({'!':{'text':'번역 문장.','count':4}},ensure_ascii=False)
        self.assertIn('RPGStructureMismatch',self.error_types(self.run_check()))

    def test_legacy_literal_key_location_is_normalized_only_in_memory(self):
        self.format_version=0
        self.documents_for({'a.b':'Some words.'},{'a.b':'번역 문장.'})
        self.row('a','Some words.','번역 문장.','a.b')
        result=self.run_check()
        self.assertEqual(result['status'],'passed')
        saved=json.loads((self.project/'data/rpgmaker-project.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['entries'][0]['locations'][0]['ptr'],'a.b')

    def test_render_replacements_are_applied_to_the_expected_value(self):
        self.documents_for({'text':'Some words.'},{'text':'바뀐 문장.'})
        self.row('a','Some words.','이전 문장.','text')
        self.write('replacements.json',{'replacements':{'이전':'바뀐'}})
        self.assertEqual(self.run_check()['status'],'passed')

    def test_missing_cached_row_is_an_explicit_failure(self):
        self.documents_for({'text':'Some words.'})
        self.row('a','Some words.','번역 문장.','text')
        self.known={}
        result=self.run_check()
        self.assertIn('RPGTranslationUnavailable',self.error_types(result))


if __name__=='__main__':unittest.main()
