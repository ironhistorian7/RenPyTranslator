"""Invented text-only projects. No user games, models or GPU inference."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine import save_json
from patch_validation import (classify, validate_final, _baseline, _commit_repairs,
                              _repair_candidates, _signature, _font_path_repairs, _legacy_reference_text, _snapshot, _source_authenticity, run)


def success(check='render', events=None):
    return dict(check=check, status='passed', events=events or [])


def failure(check='render', error='broken', frames='', **kwargs):
    return dict(check=check, status='failed', error_type='Exception', error=error,
                traceback=frames, **kwargs)


class FinalTextTests(unittest.TestCase):
    def test_bilingual_repeated_variables_are_not_false_mismatches(self):
        row = dict(source='Hello [name].')
        self.assertEqual(validate_final(row, '안녕 [name].\n{rpt_ref=0.55}Hello [name].{/rpt_ref}', {}), [])

    def test_generated_reference_and_protocol_markers(self):
        row = dict(source='Hello.')
        self.assertIn('generated reference wrapper mismatch', validate_final(row, '안녕{rpt_ref=0.55}', {}))
        self.assertIn('unrestored protocol marker in rendered text', validate_final(row, '<rpt000/>안녕', {}))

    def test_game_owned_sizes_and_markers_are_not_reinterpreted(self):
        self.assertEqual(validate_final(dict(source='{size=custom}Example{/size}'), '{size=custom}예시{/size}', {}), [])
        self.assertEqual(validate_final(dict(source='<rpt000/>'), '<rpt000/>', {}), [])

    def test_only_invalid_new_numeric_sizes_fail(self):
        self.assertEqual(validate_final(dict(source='Example'), '{size=*0.55}예시{/size}', {}), [])
        self.assertEqual(validate_final(dict(source='Example'), '{size=-1}예시{/size}', {}), [])
        self.assertTrue(validate_final(dict(source='Example'), '{size=0}예시{/size}', {}))
        self.assertTrue(validate_final(dict(source='Example'), '{size=oops}예시{/size}', {}))


class AttributionTests(unittest.TestCase):
    def setUp(self):
        self.evidence = dict(confident=True, code_inventory_equal=True, probe_equal=True, source_authenticity={'verified':True,'target_hints_complete':True,'targets_present':[]}, language='korean', changed_text_ids=['a'])

    def test_matching_original_missing_menu_is_original(self):
        missing = dict(failure(check='menu_target', error='Missing ShowMenu target: credits', target='credits'), error_type='MissingMenuTarget')
        a = dict(check='render', status='failed', events=[missing])
        self.assertEqual(classify([a], [a], self.evidence)['attribution'], 'original')

    def test_patch_filename_alone_is_insufficient(self):
        bad = failure(frames='File "game/zz_rpt_layout.rpy", line 4\nException: broken')
        self.assertEqual(classify([failure(error='Different failure')], [bad], self.evidence)['attribution'], 'unknown')

    def test_changed_tool_frame_and_original_success_is_ours(self):
        bad = failure(frames='File "game/zz_rpt_layout.rpy", line 4\nException: broken')
        self.assertEqual(classify([success()], [bad], self.evidence)['attribution'], 'ours')

    def test_original_parity_and_snapshot_confidence_are_required(self):
        bad = failure(frames='File "game/zz_rpt_layout.rpy", line 4\nException: broken')
        self.assertEqual(classify([success()], [bad], dict(self.evidence, code_inventory_equal=False))['attribution'], 'unknown')
        self.assertEqual(classify([success()], [bad], dict(self.evidence, confident=False))['attribution'], 'unknown')
        self.assertEqual(classify([success()], [bad], dict(self.evidence, probe_equal=False))['attribution'], 'unknown')
        self.assertEqual(classify([success()], [bad], dict(self.evidence, source_authenticity={'verified':False}))['attribution'], 'unknown')

    def test_one_original_failure_does_not_hide_independent_tool_failure(self):
        original = dict(failure(check='menu_target', error='Missing target', target='credits'), error_type='MissingMenuTarget')
        text = failure(check='text', error='Invalid tag', id='a')
        baseline = dict(check='render', status='failed', events=[original, dict(check='text', id='a', status='passed')])
        translated = dict(check='render', status='failed', events=[original, text])
        result = classify([baseline], [translated], self.evidence)
        self.assertEqual(result['attribution'], 'mixed')
        self.assertEqual([f['attribution'] for f in result['failures']], ['original', 'ours'])

    def test_same_exception_at_different_text_is_not_original(self):
        a = dict(check='render', status='failed', events=[failure(check='text', id='first')])
        b = dict(check='render', status='failed', events=[failure(check='text', id='second')])
        self.assertEqual(classify([a], [b], self.evidence)['attribution'], 'unknown')

    def test_workspace_prefixes_do_not_change_signature(self):
        a = failure(frames='File "C:/check/baseline/staging/renpy/text/text.py", line 9\nException: broken')
        b = failure(frames='File "C:/check/patched/staging/renpy/text/text.py", line 9\nException: broken')
        self.assertEqual(_signature(a), _signature(b))

    def test_shared_probe_argument_failure_is_unknown(self):
        issue = failure(check='screen:input', error='Unexpected argument', frames='File "game/zzzz_rpt_validation_probe.rpy", line 20')
        check = dict(check='render', status='failed', events=[issue])
        self.assertEqual(classify([check], [check], self.evidence)['attribution'], 'unknown')

    def test_matching_exit_codes_without_diagnostics_are_not_original_proof(self):
        a = dict(check='compile', status='failed', error_type='EngineExit', error='Engine exit 1')
        self.assertEqual(classify([a], [a], self.evidence)['attribution'], 'unknown')


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'prepared'
        self.original = self.root / 'original'
        self.project.mkdir()
        self.original.mkdir()
        self.cfg = dict(source=str(self.original), language='korean', model='not-used')

    def write(self, relative, text, root=None):
        path = (root or self.project) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def test_baseline_preserves_custom_code_and_restores_original_language_input(self):
        original = self.write('game/tl/korean/custom.rpy', '# original native language', self.original)
        self.write('data/source-manifest.json', json.dumps({'game/tl/korean/custom.rpy': {'sha256': hashlib.sha256(original.read_bytes()).hexdigest()}}))
        workspace = self.root / 'check'
        self.write('staging/game/zz_rpt_layout.rpy', '# generated', workspace)
        self.write('staging/game/zz_custom_fix.rpy', '# unrelated user fix', workspace)
        self.write('staging/game/tl/korean/custom.rpy', '# overwritten translation', workspace)
        evidence = _baseline(self.project, workspace, self.cfg)
        self.assertTrue(evidence['confident'])
        self.assertFalse((workspace/'staging/game/zz_rpt_layout.rpy').exists())
        self.assertTrue((workspace/'staging/game/zz_custom_fix.rpy').exists())
        self.assertEqual((workspace/'staging/game/tl/korean/custom.rpy').read_bytes(), original.read_bytes())

    def test_available_native_input_is_restored_without_hash_gate(self):
        original = self.write('game/tl/korean/custom.rpy', '# changed', self.original)
        self.write('data/source-manifest.json', json.dumps({'game/tl/korean/custom.rpy': {'sha256': 'old'}}))
        workspace = self.root/'check'
        self.write('staging/game/tl/korean/custom.rpy', '# patch', workspace)
        evidence = _baseline(self.project, workspace, self.cfg)
        self.assertTrue(evidence['confident'])
        self.assertEqual(original.read_text(), '# changed')
        self.assertEqual((workspace/'staging/game/tl/korean/custom.rpy').read_text(), '# changed')

    def source_proof(self, archived=False):
        runtime = '# invented engine\n'
        code = 'screen credits():\n    text "Fixture"\n'
        self.write('renpy/config.py', runtime, self.original)
        self.write('staging/renpy/config.py', runtime)
        manifest = {'renpy/config.py':{'sha256':hashlib.sha256((self.original/'renpy/config.py').read_bytes()).hexdigest()}}
        if archived:
            from test_workspace_files import archive
            archive(self.original/'game/scripts.rpa', {'screens.rpy':code.encode()})
            archive(self.project/'staging/game/_rpt_original_scripts.rpa', {'screens.rpy':code.encode()})
            manifest['game/scripts.rpa'] = {'sha256':hashlib.sha256((self.original/'game/scripts.rpa').read_bytes()).hexdigest()}
        else:
            self.write('game/screens.rpy', code, self.original)
            self.write('staging/game/screens.rpy', code)
            manifest['game/screens.rpy'] = {'sha256':hashlib.sha256((self.original/'game/screens.rpy').read_bytes()).hexdigest()}
        self.write('data/source-manifest.json', json.dumps(manifest))
        return _source_authenticity(self.project, self.cfg)

    def test_matching_prepared_code_is_proven_against_source_manifest(self):
        self.assertTrue(self.source_proof()['verified'])

    def test_regenerated_engine_bytecode_does_not_fail_inventory(self):
        self.source_proof()
        self.write('renpy/common/invented.rpyc', 'original compiled data', self.original)
        self.write('staging/renpy/common/invented.rpyc', 'regenerated compiled data')
        manifest_path=self.project/'data/source-manifest.json'
        manifest=json.loads(manifest_path.read_text())
        manifest['renpy/common/invented.rpyc']={'sha256':'old unused digest'}
        manifest_path.write_text(json.dumps(manifest),encoding='utf-8')
        proof=_source_authenticity(self.project,self.cfg)
        self.assertTrue(proof['verified'])
        self.assertFalse(proof['hashes_checked'])

    def test_shared_preparation_loss_is_unknown_not_original(self):
        self.source_proof()
        (self.project/'staging/game/screens.rpy').unlink()
        proof = _source_authenticity(self.project, self.cfg)
        self.assertFalse(proof['verified'])
        issue = dict(check='menu_target', status='failed', target='credits', error_type='MissingMenuTarget', error='Missing target')
        checks = [dict(check='render',status='failed',events=[issue])]
        evidence = dict(confident=True, code_inventory_equal=True, probe_equal=True, source_authenticity=proof)
        self.assertEqual(classify(checks, checks, evidence)['attribution'], 'unknown')

    def test_original_script_omitted_from_manifest_is_not_trusted(self):
        self.assertTrue(self.source_proof()['verified'])
        self.write('game/not-captured.rpy', 'label missing_capture:\n    return\n', self.original)
        proof = _source_authenticity(self.project, self.cfg)
        self.assertFalse(proof['verified'])
        self.assertTrue(any(item['kind']=='source_code_missing_from_capture_manifest' for item in proof['issues']))

    def test_original_menu_definition_unavailable_to_shared_bridge_stays_unknown(self):
        proof = self.source_proof()
        proof.update(target_hints_complete=True, targets_present=['credits'])
        issue = dict(check='menu_target', status='failed', target='credits', error_type='MissingMenuTarget', error='Missing target')
        checks = [dict(check='render',status='failed',events=[issue])]
        evidence = dict(confident=True, code_inventory_equal=True, probe_equal=True, source_authenticity=proof)
        self.assertEqual(classify(checks, checks, evidence)['attribution'], 'unknown')

    def test_compact_archive_missing_original_definition_is_not_trusted(self):
        self.assertTrue(self.source_proof(archived=True)['verified'])
        from test_workspace_files import archive
        archive(self.project/'staging/game/_rpt_original_scripts.rpa', {})
        proof = _source_authenticity(self.project, self.cfg)
        self.assertFalse(proof['verified'])
        self.assertTrue(any(item['kind']=='prepared_original_code_missing' for item in proof['issues']))

    def test_prior_tool_patch_in_input_blocks_attribution(self):
        self.write('data/source-manifest.json', json.dumps({'game/zz_rpt_korean.rpyc': {'sha256': 'old'}}))
        workspace = self.root/'check'
        self.write('staging/game/script.rpy', '# original', workspace)
        evidence = _baseline(self.project, workspace, self.cfg)
        self.assertFalse(evidence['confident'])
        self.assertEqual(evidence['issues'][0]['kind'], 'source_already_patched')

    def test_unknown_original_errors_are_never_repaired(self):
        workspace = self.root/'check'
        p = self.write('staging/game/zz_rpt_layout.rpy', 'style.say_window.ysize', workspace)
        before = p.read_bytes()
        out = _repair_candidates(workspace, self.cfg, [dict(attribution='original', error='AttributeError ysize')])
        self.assertEqual(out, [])
        self.assertEqual(p.read_bytes(), before)

    def test_owned_font_rule_uses_existing_bundle_and_never_external_path(self):
        workspace = self.root/'check'
        self.write('staging/game/tl/korean/fonts/NanumSquareNeo-Regular.ttf', 'owned font', workspace)
        self.write('staging/game/tl/korean/_rpt_presentation.json', json.dumps({'fonts':{'preferred':{'regular':'X:/old/NanumSquareNeo-Regular.ttf'}}}), workspace)
        _, _, changes = _font_path_repairs(workspace, self.cfg, "Missing font: X:/old/NanumSquareNeo-Regular.ttf")
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0][2], 'tl/korean/fonts/NanumSquareNeo-Regular.ttf')
        self.assertEqual(_font_path_repairs(workspace, self.cfg, 'unrelated error')[2], [])

    def test_legacy_wrapper_rule_only_recognizes_tool_suffix(self):
        old = '번역\n{size=*0.55}{cps=0}Original.{/cps}{/size}{nw}'
        self.assertEqual(_legacy_reference_text(old, 'Original.'), '번역\n{rpt_ref=0.55}{cps=0}Original.{/cps}{/rpt_ref}{nw}')
        self.assertIsNone(_legacy_reference_text(old, old))
        self.assertIsNone(_legacy_reference_text('{size=*0.55}번역{/size}', 'Original.'))

    def test_commit_is_owned_only_and_cache_unchanged(self):
        workspace = self.root/'check'
        p = self.write('staging/game/zz_rpt_layout.rpy', 'old')
        c = self.write('data/translations.jsonl', 'preserved cache')
        self.write('staging/game/zz_rpt_layout.rpy', 'new', workspace)
        artifact = self.project/'data/validation-runs/example'
        _commit_repairs(self.project, workspace, [dict(file='game/zz_rpt_layout.rpy')], artifact)
        self.assertEqual(p.read_text(), 'new')
        self.assertEqual(c.read_text(), 'preserved cache')
        self.assertEqual((artifact/'repair-backup/game/zz_rpt_layout.rpy').read_text(), 'old')
        with self.assertRaises(ValueError):
            _commit_repairs(self.project, workspace, [dict(file='game/script.rpy')], artifact)

    def test_failed_commit_restores_previously_changed_files(self):
        workspace = self.root/'check'
        a = self.write('staging/game/zz_rpt_layout.rpy', 'old a')
        b = self.write('staging/game/zz_rpt_names.rpy', 'old b')
        self.write('staging/game/zz_rpt_layout.rpy', 'new a', workspace)
        self.write('staging/game/zz_rpt_names.rpy', 'new b', workspace)
        calls = []
        def fail_second(temporary, target):
            calls.append(target)
            if len(calls) == 2:
                raise PermissionError('blocked')
            temporary.replace(target)
        with patch('atomic_files.replace_cache_file', side_effect=fail_second):
            with self.assertRaises(PermissionError):
                _commit_repairs(self.project, workspace,
                                [dict(file='game/zz_rpt_layout.rpy'), dict(file='game/zz_rpt_names.rpy')], self.root/'report')
        self.assertEqual(a.read_text(), 'old a')
        self.assertEqual(b.read_text(), 'old b')

    def test_concurrent_patch_edit_is_not_overwritten(self):
        workspace = self.root/'check'
        p = self.write('staging/game/zz_rpt_layout.rpy', 'user edited meanwhile')
        self.write('staging/game/zz_rpt_layout.rpy', 'automatic fix', workspace)
        with self.assertRaisesRegex(RuntimeError, 'changed during validation'):
            _commit_repairs(self.project, workspace, [dict(file='game/zz_rpt_layout.rpy', before_state=[0,0])], self.root/'report')
        self.assertEqual(p.read_text(), 'user edited meanwhile')

    def test_static_mode_never_starts_engine(self):
        self.write('data/catalog.json', json.dumps([dict(id='a', source='Example.', kind='dialogue', file='scene.rpy', line=0)]))
        self.write('staging/game/tl/korean/scene.rpy', '"예시."\n')
        with patch('patch_validation._snapshot', side_effect=AssertionError('No engine workspace')), patch('patch_validation._execute', side_effect=AssertionError('No execution')):
            result = run(self.project, self.cfg, mode='static')
        self.assertEqual(result['status'], 'passed')
        self.assertFalse(result['coverage']['compile'])

    def test_inspection_snapshot_does_not_serialize_live_runtime_objects(self):
        import threading
        cfg = dict(self.cfg, _render_ids={'a'}, _runtime_exit={'event': threading.Event()},
                   _request_callback=lambda: None)
        target = self.root/'check'
        with patch('workspace_files.clone'):
            _snapshot(self.project, cfg, target)
        saved = json.loads((target/'project.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['source'], self.cfg['source'])
        self.assertFalse(any(key.startswith('_') for key in saved))

    def test_missing_prepared_catalog_is_reported_not_silently_passed(self):
        result = run(self.project, self.cfg, mode='compile')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['attribution'], 'unknown')
        self.assertTrue((self.project/'data/patch-validation.json').exists())

    def test_mocked_pair_reports_and_does_not_start_models(self):
        self.write('data/catalog.json', json.dumps([dict(id='a', source='Example.', kind='dialogue', file='scene.rpy', line=0)]))
        self.write('staging/game/tl/korean/scene.rpy', '"예시."\n')
        self.write('data/translations.jsonl', 'cached')
        def snapshot(project, cfg, target):
            target.mkdir(parents=True)
            (target/'staging/game').mkdir(parents=True)
            (target/'data').mkdir()
            return target
        with patch('patch_validation._snapshot', side_effect=snapshot), \
             patch('patch_validation._baseline', return_value={'confident':True}), \
             patch('patch_validation._source_authenticity', return_value={'verified':True}), \
             patch('patch_validation._original_code_files', return_value={'game/original.rpyc'}), \
             patch('patch_validation._menu_targets', return_value=[]), \
             patch('patch_validation._execute', side_effect=lambda workspace, operation, timeout=180: success(operation)):
            result = run(self.project, self.cfg, mode='compile')
        self.assertEqual(result['status'], 'passed')
        self.assertFalse(result['coverage']['startup'])
        self.assertEqual((self.project/'data/translations.jsonl').read_text(), 'cached')

    def test_rpg_static_scope_passes_without_claiming_runtime(self):
        self.write('data/rpg-output.json', json.dumps({'files':['data/Map001.json']}))
        self.write('data/rpg-source/data/Map001.json', '[null]')
        self.write('staging/data/Map001.json', '[null]')
        with patch('runtime_assets.runtime_executable', return_value=self.root/'no-node.exe'):
            result = run(self.project, dict(self.cfg, engine='rpgmaker'), mode='static')
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['coverage']['runtime'], 'not_run')
        self.assertEqual(result['scope'], 'rpgmaker_static')

    def test_rpg_missing_node_for_javascript_is_incomplete(self):
        self.write('data/rpg-output.json', json.dumps({'files':['js/plugins/RPT_Bilingual.js']}))
        self.write('staging/js/plugins/RPT_Bilingual.js', 'var x=1;')
        with patch('runtime_assets.runtime_executable', return_value=self.root/'no-node.exe'), patch('app_paths.ROOT', self.root):
            result = run(self.project, dict(self.cfg, engine='rpgmaker'), mode='static')
        self.assertEqual(result['status'], 'incomplete')

    def test_rpg_portable_electron_parser_requires_no_system_node_or_gui(self):
        from types import SimpleNamespace
        self.write('data/rpg-output.json', json.dumps({'files':['js/plugins/RPT_Bilingual.js']}))
        self.write('staging/js/plugins/RPT_Bilingual.js', 'var x=1;')
        electron = self.write('_desktop/RenPyTranslator-UI.exe', 'synthetic executable', self.root)
        with patch('runtime_assets.runtime_executable', return_value=self.root/'no-node.exe'), \
             patch('app_paths.ROOT', self.root), \
             patch('native_process.run', return_value=SimpleNamespace(returncode=0)) as parser:
            result = run(self.project, dict(self.cfg, engine='rpgmaker'), mode='static')
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['coverage']['javascript_checker'], 'electron_node')
        self.assertEqual(result['coverage']['runtime'], 'not_run')
        command = parser.call_args.args[0]
        self.assertEqual(command[:2], [str(electron), '--check'])
        self.assertEqual(parser.call_args.kwargs['env']['ELECTRON_RUN_AS_NODE'], '1')
        self.assertNotIn('RPT_TOOL_ROOT', parser.call_args.kwargs['env'])

    def test_rpg_rendered_control_pair_is_checked_without_model(self):
        self.write('data/rpg-output.json', json.dumps({'files':['data/Map001.json']}))
        self.write('data/rpg-source/data/Map001.json', '[null]')
        self.write('staging/data/Map001.json', json.dumps({'_rpt_original':r'Example \V[1]', '_rpt_translation':'예시'}))
        with patch('runtime_assets.runtime_executable', return_value=self.root/'no-node.exe'):
            result = run(self.project, dict(self.cfg, engine='rpgmaker'), mode='static')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['attribution'], 'ours')
        self.assertEqual(result['coverage']['control_pairs'], 1)


if __name__ == '__main__':
    unittest.main()
