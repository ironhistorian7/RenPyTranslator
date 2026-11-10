"""Engine-aware names-only gates; all paths and workers are mocked."""
import contextlib
import io
import sys
import unittest
from unittest.mock import patch

import desktop_bridge as bridge
import game_engine
import model_store


class RPGNamesGateTests(unittest.TestCase):
    def test_only_rpg_name_and_display_fixes_do_not_need_a_model(self):
        self.assertFalse(model_store.needs_model(['names'],'rpgmaker'))
        self.assertFalse(model_store.needs_model(['names','font','display','layout','check'],'rpgmaker'))
        for jobs in (['run'],['names','failed'],['retranslate'],['update']):
            with self.subTest(jobs=jobs):
                self.assertTrue(model_store.needs_model(jobs,'rpgmaker'))
        self.assertTrue(model_store.needs_model(['names'],'renpy'))
        self.assertTrue(model_store.needs_model(['names']))

    def test_model_preflight_uses_the_inspected_engine(self):
        with patch.object(model_store,'problem',return_value={'code':'model_required'}) as problem:
            self.assertIsNone(model_store.dispatch({'op':'preflight','tasks':['names'],'engine':'rpgmaker'}))
            problem.assert_not_called()
            self.assertEqual(model_store.dispatch({'op':'preflight','tasks':['names'],'engine':'renpy'}),
                             {'code':'model_required'})
            problem.assert_called_once()

    def test_rpg_names_are_supported_without_enabling_unimplemented_tasks(self):
        with patch.object(game_engine,'detect',return_value={'engine':'rpgmaker','engine_label':'RPG Maker MZ','supported':True}):
            info=game_engine.require('X:/invented-source',True,['names'])
            self.assertIn('names',info['supported_tasks'])
            for task in ('answers','routes','language'):
                with self.subTest(task=task),self.assertRaisesRegex(ValueError,'지원하지 않는 작업'):
                    game_engine.require('X:/invented-source',True,[task])

    def test_rpg_source_and_project_name_repairs_never_require_a_model(self):
        for source in (True,False):
            with self.subTest(source=source), \
                 patch('game_engine.require',return_value={'engine':'rpgmaker'}), \
                 patch('model_store.require_selected',side_effect=AssertionError('No model needed')), \
                 patch.object(bridge,'save_settings'), \
                 patch('translate_game.main') as run, \
                 patch.object(sys,'argv',['synthetic-cli']),contextlib.redirect_stdout(io.StringIO()):
                bridge.run_request({'path':'X:/invented','source':source,'tasks':['names'],
                                    'settings':dict(bridge.DEFAULTS)})
                self.assertNotIn('--model',sys.argv)
                self.assertIn('--source' if source else '--project',sys.argv)
                self.assertEqual(sys.argv[3],'names')
                run.assert_called_once()

    def test_renpy_name_repairs_keep_the_existing_model_requirement(self):
        with patch('game_engine.require',return_value={'engine':'renpy'}), \
             patch('model_store.require_selected',return_value='synthetic-model') as require, \
             patch.object(bridge,'save_settings'),patch('translate_game.main'), \
             patch.object(sys,'argv',['synthetic-cli']),contextlib.redirect_stdout(io.StringIO()):
            bridge.run_request({'path':'X:/invented','source':False,'tasks':['names'],
                                'settings':dict(bridge.DEFAULTS)})
            require.assert_called_once()
            self.assertEqual(sys.argv[-2:],['--model','synthetic-model'])


if __name__=='__main__':unittest.main()
