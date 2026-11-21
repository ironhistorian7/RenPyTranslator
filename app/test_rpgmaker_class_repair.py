"""Invented databases only: no game, model, process, or real project access."""
from copy import deepcopy
import unittest

from rpgmaker_class_repair import apply_overrides, class_problem, observe_actor_reference, reference_diagnostics
import test_rpg_patch_check as fixtures


def databases():
    classes = [None, dict(expParams=[30, 20, 30, 30]), dict(expParams=[35, 20, 30, 30])]
    actors = [None, dict(id=1, name='Actor one', classId=1, initialLevel=1),
              dict(id=2, name='Actor two', classId=0, initialLevel=1)]
    return actors, classes


class ActorClassRepairTests(unittest.TestCase):
    def test_unconfigured_is_identity_and_explicit_repair_changes_only_class_id(self):
        actors, classes = databases()
        before = deepcopy(actors)
        self.assertIs(apply_overrides(actors, {}, classes)[0], actors)
        repaired, changes = apply_overrides(actors, {'rpg_actor_class_overrides': {'2': 1}}, classes)
        expected = deepcopy(before); expected[2]['classId'] = 1
        self.assertEqual(repaired, expected)
        self.assertEqual(actors, before)
        self.assertEqual(changes, [dict(actor_id=2, before_class_id=0, after_class_id=1, reason='missing_class')])
        repeated, changes = apply_overrides(repaired, {'rpg_actor_class_overrides': {'2': 1}}, classes)
        self.assertIs(repeated, repaired)
        self.assertEqual(changes, [])

    def test_valid_source_class_cannot_be_reassigned(self):
        actors, classes = databases()
        with self.assertRaisesRegex(ValueError, 'valid source class'):
            apply_overrides(actors, {'rpg_actor_class_overrides': {'1': 2}}, classes)

    def test_existing_javascript_array_references_are_not_repair_candidates(self):
        actors, classes = databases()
        for value in ('1', 1.0):
            actors[2]['classId'] = value
            self.assertIsNone(class_problem(classes, value))
            self.assertIs(apply_overrides(actors, {'rpg_actor_class_overrides': {'2': 1}}, classes)[0], actors)
            with self.assertRaisesRegex(ValueError, 'valid source class'):
                apply_overrides(actors, {'rpg_actor_class_overrides': {'2': 2}}, classes)
        classes[0] = deepcopy(classes[1]); actors[2]['classId'] = 0
        with self.assertRaisesRegex(ValueError, 'valid source class'):
            apply_overrides(actors, {'rpg_actor_class_overrides': {'2': 1}}, classes)

    def test_bad_declarations_and_invalid_targets_are_rejected_without_mutation(self):
        actors, classes = databases(); before = deepcopy(actors)
        for overrides in ([], False, {'0': 1}, {'02': 1}, {'missing': 1}, {'9': 1},
                          {'2': 0}, {'2': True}, {'2': '1'}, {'2': 9}, {'2': 1, '1': 2}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                apply_overrides(actors, {'rpg_actor_class_overrides': overrides}, classes)
            self.assertEqual(actors, before)
        for value in (None, [], [1, 2, 3], [1, 2, 3, None], [1, 2, 3, True], [1, 2, 3, float('inf')], [1, 2, 3, 10**400]):
            with self.subTest(expParams=str(value)[:50]), self.assertRaises(ValueError):
                apply_overrides(actors, {'rpg_actor_class_overrides': {'2': 1}}, [None, {'expParams': value}])

    def test_only_known_invalid_references_are_diagnosed_with_literal_usage(self):
        actors, classes = databases()
        problems, count = reference_diagnostics(actors, classes, deepcopy(actors), deepcopy(classes),
                                                {'partyMembers': [1]}, actors_file='www/data/Actors.json')
        self.assertEqual(count, 2)
        self.assertEqual(set(problems), {2})
        self.assertEqual(problems[2]['attribution'], 'original')
        self.assertFalse(problems[2]['initial_party'])
        # Removing a never-initialized actor also calls actor(id) in MV/MZ.
        for params in ([2, 0, False], [2, 1, False]):
            observe_actor_reference({'code': 129, 'parameters': params}, 'www/data/Map001.json', '[0]', problems)
        observe_actor_reference({'code': 311, 'parameters': [1, 2, 0, 0, 1]}, 'Map001.json', '[1]', problems)
        observe_actor_reference({'code': 319, 'parameters': [2, 1, 0]}, 'Map001.json', '[2]', problems)
        self.assertEqual(problems[2]['static_event_references'], 3)
        self.assertEqual(problems[2]['static_reference_samples'][0]['code'], 129)
        self.assertIsNone(class_problem(classes, 1))


class ActorClassPatchCheckTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RPGPatchCheckTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def prepare(self, *, prefix=''):
        fixture = self.fixture
        actors, classes = databases()
        fixture.cfg['rpg_base'] = prefix or '.'
        actors_final = fixture.documents_for(actors, file=prefix+'data/Actors.json')
        fixture.documents_for(classes, file=prefix+'data/Classes.json')
        fixture.documents_for({'partyMembers': [1]}, file=prefix+'data/System.json')
        fixture.documents_for([{'code': 129, 'indent': 0, 'parameters': [2, 1, False]}],
                              file=prefix+'data/Map001.json')
        return actors_final

    def test_unchanged_invalid_original_is_warning_not_translation_failure(self):
        self.prepare()
        result = self.fixture.run_check()
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['coverage']['actor_class_references'], 2)
        self.assertEqual(result['coverage']['json_files'], 4)
        problem = result['diagnostics'][0]
        self.assertEqual(problem['attribution'], 'original')
        self.assertEqual(problem['static_event_references'], 1)
        self.assertEqual(result['manual_repairs'], [])

    def test_declared_expected_repair_is_exact_and_works_under_www(self):
        final = self.prepare(prefix='www/')
        self.fixture.cfg['rpg_actor_class_overrides'] = {'2': 1}
        final[2]['classId'] = 1
        result = self.fixture.run_check()
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(result['manual_repairs'][0]['file'], 'www/data/Actors.json')
        self.assertEqual(result['manual_repairs'][0]['actor_id'], 2)
        self.assertEqual(result['coverage']['json_files'], 4)
        # The one declared class repair cannot excuse any other numeric edit.
        final[2]['initialLevel'] = 9
        result = self.fixture.run_check()
        self.assertEqual(result['status'], 'failed')
        self.assertIn('RPGStructureMismatch', self.fixture.error_types(result))

    def test_unconfigured_numeric_change_and_bad_override_still_fail(self):
        final = self.prepare()
        final[2]['classId'] = 1
        result = self.fixture.run_check()
        self.assertEqual(result['status'], 'failed')
        self.assertIn('RPGStructureMismatch', self.fixture.error_types(result))
        self.fixture.cfg['rpg_actor_class_overrides'] = {'1': 2}
        result = self.fixture.run_check()
        self.assertIn('RPGClassOverrideInvalid', self.fixture.error_types(result))

    def test_unchanged_malformed_experience_data_is_advisory(self):
        self.prepare()
        original, final = self.fixture.documents['data/Classes.json']
        original[1]['expParams'] = final[1]['expParams'] = None
        result = self.fixture.run_check()
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['diagnostics'][0]['reason'], 'invalid_exp_params')
        self.assertTrue(result['diagnostics'][0]['initial_party'])


if __name__ == '__main__':
    unittest.main()
