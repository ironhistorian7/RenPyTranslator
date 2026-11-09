import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import translate_game


class PatchCommandTests(unittest.TestCase):
    def test_confirmed_repair_publishes_without_rendering_or_retranslation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'data').mkdir()
            result={'status':'passed','report':str(root/'data/check/report.json'),
                    'repairs':[{'status':'applied','file':'game/zz_rpt_layout.rpy'}]}
            with patch('patch_validation.run',return_value=result),patch.object(translate_game,'read_catalog',return_value=[{'id':'one'}]),\
                 patch.object(translate_game,'package') as publish,patch.object(translate_game,'render') as render,\
                 patch.object(translate_game,'translate') as translate,contextlib.redirect_stdout(io.StringIO()):
                translate_game.check_and_export(root,{'language':'korean'})
            publish.assert_called_once_with(root,{'language':'korean','_prepared_snapshot':True})
            render.assert_not_called();translate.assert_not_called()
            self.assertEqual(json.loads((root/'data/validation.json').read_text())['errors'],[])

    def test_successful_check_without_repairs_does_not_republish(self):
        with patch('patch_validation.run',return_value={'status':'passed','repairs':[]}),\
             patch.object(translate_game,'package') as publish,contextlib.redirect_stdout(io.StringIO()):
            translate_game.check_and_export(Path('synthetic-only'),{})
        publish.assert_not_called()

    def test_original_unknown_or_incomplete_checks_do_not_publish(self):
        for result in ({'status':'failed','attribution':'original'},
                       {'status':'failed','attribution':'unknown'}, {'status':'incomplete'}):
            with self.subTest(result=result),patch('patch_validation.run',return_value=result),\
                 patch.object(translate_game,'package') as publish,contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(RuntimeError):translate_game.check_and_export(Path('synthetic-only'),{})
                publish.assert_not_called()

    def test_normal_build_does_not_repeat_successful_paired_engine_checks(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'data').mkdir()
            with patch('diagnostics.stage'),patch.object(translate_game,'read_catalog',return_value=[{'id':'one'}]),patch.object(translate_game,'presentation'),\
                 patch('display_policy.install'),patch.object(translate_game,'render'),\
                 patch.object(translate_game,'install_support'),patch.object(translate_game,'package') as publish,\
                 patch.object(translate_game,'validate',side_effect=[RuntimeError('Engine exit 1: compile.log'),None]) as validate,\
                 patch('patch_validation.run',return_value={'status':'passed','repairs':[{'status':'applied'}]}) as check:
                translate_game.rebuild(root,{})
            check.assert_called_once_with(root,{'_reference_guard':True},mode='compile')
            self.assertEqual(validate.call_count,1);publish.assert_called_once()

    def test_failed_causal_check_keeps_original_build_error(self):
        with patch('diagnostics.stage'),patch.object(translate_game,'presentation'),patch('display_policy.install'),\
             patch.object(translate_game,'render'),patch.object(translate_game,'install_support'),\
             patch.object(translate_game,'package') as publish,\
             patch.object(translate_game,'validate',side_effect=RuntimeError('Engine exit 1: compile.log')),\
             patch('patch_validation.run',return_value={'status':'failed','attribution':'unknown'}):
            with self.assertRaisesRegex(RuntimeError,'Engine exit 1'):translate_game.rebuild(Path('synthetic-only'),{})
            publish.assert_not_called()


if __name__=='__main__':unittest.main()
