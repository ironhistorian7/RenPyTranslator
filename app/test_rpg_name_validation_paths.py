"""Invented name-only recovery paths; no real project, game or model access."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from failed_repair import run as repair, saved_output
from failure_candidates import classify
from rpgmaker_names import validation_config
from rpgmaker_project import seed_defaults
import test_rpg_patch_check as patch_fixture


CFG={'engine':'rpgmaker','source_language':'japanese','language':'korean',
     'model':'synthetic','_rpg_check_han_residue':True}


def actor():
    return dict(id='actor',source='ミラ',source_aliases=['ミラ','Mira','美羅'],
                file='data/Actors.json',kind='string',usage='speaker_label',
                location=dict(file='data/Actors.json',fmt='json',kind='loose',ptr='[1].name'))


def configured():
    row=actor()
    return row,validation_config(CFG,[row],{'actor':dict(source=row['source'],text='미라')})


class RPGNameValidationPaths(unittest.TestCase):
    def test_rpg_saved_markers_restore_rpg_controls_with_core_numbering(self):
        source=r'\PX[90]\PY[120]\C[2]挨拶\N[1]\.'
        target=r'\PX[90]\PY[120]\C[2]안녕 \N[1]\.'
        wire='⟦RPT000⟧안녕 ⟦RPT001⟧'
        for label in ('Previous validation failed','Wrapper-stripped: previous validation failed'):
            with self.subTest(label=label):
                self.assertEqual(saved_output(source,label+'; output='+repr(wire),CFG),target)

    def test_rpg_already_restored_output_gets_coordinates_once_and_one_validation(self):
        from translation import validate_text
        source=r'\PX[90]\PY[120]\C[2]挨拶\N[1]\.'
        core=r'\C[2]안녕 \N[1]\.'
        expected=r'\PX[90]\PY[120]'+core
        for answer in (core,expected):
            with self.subTest(prefixed=answer==expected), \
                 patch('failed_repair.validate_text',wraps=validate_text) as validate:
                self.assertEqual(saved_output(source,'Previous validation failed; output='+repr(answer),CFG),expected)
                validate.assert_called_once()

    def test_rpg_saved_recovery_does_not_fabricate_missing_controls(self):
        source=r'\PX[90]\C[2]挨拶\N[1]\.'
        for answer in ('안녕.',r'\C[2]안녕\.',
                       '⟦RPT000⟧안녕 ⟦RPT002⟧','⟦RPT000⟧안녕 ⟦RPT099⟧⟦RPT002⟧'):
            with self.subTest(answer=answer):
                self.assertIsNone(saved_output(source,'Previous validation failed; output='+repr(answer),CFG))

    def test_saved_names_accept_script_variants_without_requiring_hangul(self):
        row,cfg=configured()
        for target in ('ミラ','Mira','美羅','미라'):
            with self.subTest(target=target):
                error='Previous validation failed; output='+repr(target)
                self.assertEqual(saved_output(row['source'],error,cfg,'speaker_label'),target)

    def test_saved_name_exception_does_not_accept_untranslated_body_or_markers(self):
        row,cfg=configured()
        error='Previous validation failed; output='+repr('Mira가 向かった')
        self.assertIsNone(saved_output('ミラが向かった',error,cfg,'dialogue'))
        item=dict(source=row['source'],text='<rpt999/>Mira',status='failure_recovered')
        result=classify(row,item,cfg,{},indexed=True)
        self.assertEqual(result['origin'],'validation_error')
        self.assertIn('unrestored placeholder',result['errors'])

    def test_recovered_name_classification_matches_saved_recovery(self):
        row,cfg=configured()
        for target in ('Mira','美羅','미라'):
            with self.subTest(target=target):
                item=dict(source=row['source'],text=target,status='failure_recovered')
                self.assertEqual(classify(row,item,cfg,{},indexed=True)['origin'],'saved_recovery')

    def test_failed_repair_prepares_name_policy_before_saved_recovery(self):
        row=actor()
        known={row['id']:dict(id=row['id'],source=row['source'],text=row['source'],status='source_fallback')}
        failure=dict(row=row,error='Previous validation failed; output='+repr('Mira'))
        with tempfile.TemporaryDirectory(prefix='rpt-name-repair-') as temporary:
            project=Path(temporary);(project/'data').mkdir()
            (project/'data/failed-items.json').write_text(json.dumps([failure]),encoding='utf-8')
            with patch('failed_repair.read_catalog',return_value=[row]), \
                 patch('failed_repair.cache',return_value=known), \
                 patch('ui_policy.cached_policy',return_value={'choices':[],'menu':{}}), \
                 patch('name_translation.mapping',return_value={}), \
                 patch('translation.request',side_effect=AssertionError('No model requests')), \
                 contextlib.redirect_stdout(io.StringIO()):
                changed,report=repair(project,dict(CFG))
            self.assertEqual(changed,{row['id']})
            self.assertEqual(report['recovered_saved'],1)
            self.assertEqual(report['remaining'],0)
            self.assertEqual(report['model_calls'],0)

    def test_default_seeding_prepares_name_policy_for_indexed_outputs(self):
        row=actor()
        known={row['id']:dict(id=row['id'],source=row['source'],text=row['source'],status='source_fallback')}
        failure=dict(row=row,error='Previous validation failed; output='+repr('Mira'))
        with tempfile.TemporaryDirectory(prefix='rpt-name-seed-') as temporary:
            project=Path(temporary);(project/'data').mkdir()
            (project/'data/failed-items.json').write_text(json.dumps([failure]),encoding='utf-8')
            with patch('translation.read_catalog',return_value=[row]), \
                 patch('translation.cache',return_value=known), \
                 patch('gameloc.model.Project.load',return_value=type('Prepared',(),{'entries':[]})()), \
                 patch('translation.request',side_effect=AssertionError('No model requests')), \
                 contextlib.redirect_stdout(io.StringIO()):
                seed_defaults(project,dict(CFG),recover_saved=True)
            record=json.loads((project/'data/translations.jsonl').read_text(encoding='utf-8'))
            self.assertEqual(record['text'],'Mira')
            self.assertEqual(record['status'],'failure_recovered')

    def test_standalone_patch_check_prepares_policy_for_name_only_cache(self):
        fixture=patch_fixture.RPGPatchCheckTests(methodName='runTest');fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.cfg.update(CFG)
        actors=[None,dict(id=1,name='ミラ')]
        final=fixture.documents_for(actors,file='data/Actors.json')
        final[1]['name']='Mira'
        row=fixture.row('actor','ミラ','Mira','[1].name',usage='speaker_label',file='data/Actors.json')
        row['source_aliases']=['ミラ','Mira','美羅']
        result=fixture.run_check()
        self.assertEqual(result['status'],'passed',result.get('failures'))


if __name__=='__main__':unittest.main()
