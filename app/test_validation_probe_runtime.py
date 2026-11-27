"""Probe-only invented text and mocked engine calls; no game/model/cache access."""
import ast
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


SOURCE = Path(__file__).with_name('validation_probe_runtime.py')


def probe_functions():
    # Importing the embedded helper would import an actual Ren'Py engine.
    # Load its plain functions only, supplying synthetic dependencies below.
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    namespace = {}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace


class ProbeTemplateTests(unittest.TestCase):
    def setUp(self):
        self.probe = probe_functions()
        self.sample = self.probe['_rpt_probe_sample_text']

    def test_confirmed_empty_string_fields_are_sampled_without_changing_row(self):
        row = {'kind': 'string', 'source': '{} files from {}.',
               'text': '{b}{}{/b} files from {}.'}
        original = dict(row)
        self.assertEqual(self.sample(row), ('{b}0{/b} files from 0.', 2))
        self.assertEqual(row, original)

    def test_escaped_braces_and_tag_payloads_remain_opaque(self):
        row = {'kind': 'string', 'source': 'Literal {{} and {}.',
               'text': 'Literal {{} with {custom={}} and {}.'}
        self.assertEqual(self.sample(row), ('Literal {{} with {custom={}} and 0.', 1))

    def test_interpolation_dictionary_and_nested_brackets_are_not_format_fields(self):
        text = '[items[0] or {}] [mapping["]"] or {}] {}'
        self.assertEqual(self.probe['_rpt_probe_format_slots'](text), [len(text) - 2])

    def test_triple_quoted_interpolation_is_not_a_format_template(self):
        text = '[mapping["""]"""] or {}] {}'
        self.assertEqual(self.probe['_rpt_probe_format_slots'](text), [len(text) - 2])

    def test_named_fields_and_custom_tags_are_not_guessed(self):
        text = '{invented_effect}{value}{value:04d}{0}{}{/invented_effect}'
        row = {'kind': 'string', 'source': text, 'text': text}
        self.assertEqual(self.sample(row),
                         ('{invented_effect}{value}{value:04d}{0}0{/invented_effect}', 1))

    def test_unconfirmed_or_mismatched_fields_are_unchanged(self):
        cases = [
            {'kind': 'dialogue', 'source': '{}', 'text': '{}'},
            {'kind': 'choice', 'source': '{}', 'text': '{}'},
            {'kind': 'string', 'source': 'A phrase.', 'text': '{}'},
            {'kind': 'string', 'source': '{} {}', 'text': '{}'},
            {'kind': 'string', 'source': '{}', 'text': '{} {}'},
            {'kind': 'string', 'text': '{}'},
        ]
        for row in cases:
            with self.subTest(row=row):
                self.assertEqual(self.sample(row), (row['text'], 0))

    def test_renderer_receives_inert_fields_and_original_markup(self):
        text_constructor = Mock(return_value=object())
        renderer = Mock()
        self.probe.update(
            _rpt_probe_engine=SimpleNamespace(text=SimpleNamespace(text=SimpleNamespace(Text=text_constructor))),
            renpy=SimpleNamespace(render=renderer),
            config=SimpleNamespace(screen_width=1280, screen_height=720),
        )
        result = self.probe['_rpt_probe_text'](
            {'kind': 'string', 'source': '{}', 'text': '{color=#fff}{}{/color}'})
        text_constructor.assert_called_once_with(
            '{color=#fff}0{/color}', substitute=False, slow=False, style='choice_button_text')
        renderer.assert_called_once()
        self.assertEqual(result, {'sampled_format_fields': 1})

    def test_text_failure_is_still_reported_after_template_sampling(self):
        events = []
        self.probe.update(_rpt_probe_event=events.append,
                          _rpt_probe_time=SimpleNamespace(time=lambda: 0),
                          _rpt_probe_traceback=SimpleNamespace(format_exc=lambda: 'synthetic traceback'))
        def invalid_markup():
            raise ValueError('unknown invented tag')
        self.probe['_rpt_probe_attempt']('text', invalid_markup, {'id': 'invented'})
        self.assertEqual(events[-1]['status'], 'failed')
        self.assertEqual(events[-1]['error_type'], 'ValueError')

    def test_passed_event_records_template_sampling_count(self):
        events = []
        self.probe.update(_rpt_probe_event=events.append,
                          _rpt_probe_time=SimpleNamespace(time=lambda: 0))
        self.probe['_rpt_probe_attempt']('text', lambda: {'sampled_format_fields': 2})
        self.assertEqual(events[-1]['status'], 'passed')
        self.assertEqual(events[-1]['sampled_format_fields'], 2)


class ProbeInterpolationTests(unittest.TestCase):
    def setUp(self):
        self.probe = probe_functions()
        self.rendered = Mock(return_value=object())
        self.probe.update(
            _rpt_probe_engine=SimpleNamespace(text=SimpleNamespace(text=SimpleNamespace(Text=self.rendered))),
            renpy=SimpleNamespace(render=Mock()),
            config=SimpleNamespace(screen_width=1280, screen_height=720),
        )

    def test_frozen_values_quote_and_unresolved_are_inert(self):
        row = {'kind': 'dialogue', 'text': '[hero] [points] [hero!q] [unknown.x]',
               'interpolation': {'parts': [{'name': 'hero', 'quote': False}, ' ',
                                            {'name': 'points', 'quote': False}, ' ',
                                            {'name': 'hero', 'quote': True}, ' [unknown.x]'],
                                 'values': {'hero': '{b}Alex{/b}[evil]', 'points': 3},
                                 'unresolved': ['unknown.x']}}
        self.probe['_rpt_probe_text'](row)
        self.assertEqual(self.rendered.call_args.args[0],
                         '{b}Alex{/b}[evil] 3 {{b}Alex{{/b}[evil] [unknown.x]')
        self.assertFalse(self.rendered.call_args.kwargs['substitute'])
        self.assertEqual(self.probe['_rpt_probe_interpolation_coverage'](row['interpolation']),
                         {'scenario': 'initialized_literals', 'slots_total': 4,
                          'slots_resolved': 3, 'slots_unresolved': 1,
                          'comparable': False, 'names': ['hero', 'points']})

    def test_template_sampling_does_not_modify_inserted_values(self):
        row = {'kind': 'string', 'source': '{} [v]', 'text': '{} [v]',
               'interpolation': {'parts': ['{} ', {'name': 'v', 'quote': False}],
                                 'values': {'v': '{}'}, 'unresolved': []}}
        result = self.probe['_rpt_probe_text'](row)
        self.assertEqual(self.rendered.call_args.args[0], '0 {}')
        self.assertEqual(result['sampled_format_fields'], 1)

    def test_missing_slot_stays_literal_and_is_counted(self):
        recipe = {'parts': [{'name': 'missing', 'quote': True}], 'values': {}, 'unresolved': []}
        self.assertEqual(self.probe['_rpt_probe_interpolated_text'](
            {'text': '[missing!q]', 'interpolation': recipe}), ('[missing!q]', 0))
        self.assertEqual(self.probe['_rpt_probe_interpolation_coverage'](recipe)['slots_unresolved'], 1)

    def test_failure_has_phase_and_never_logs_value_or_traceback(self):
        events = []
        self.probe.update(_rpt_probe_event=lambda event: events.append(dict(event)),
                          _rpt_probe_time=SimpleNamespace(time=lambda: 0))
        self.rendered.side_effect = ValueError('private synthetic value')
        row = {'text': '[v]', 'interpolation': {'parts': [{'name': 'v', 'quote': False}],
               'values': {'v': 'private synthetic value'}, 'unresolved': [], 'scenario': 'synthetic'}}
        self.probe['_rpt_probe_attempt']('text', lambda: self.probe['_rpt_probe_text'](row), row)
        self.assertEqual(events[-1]['phase'], 'render')
        self.assertEqual(events[-1]['interpolation']['scenario'], 'synthetic')
        self.assertNotIn('private synthetic value', repr(events))
        self.assertNotIn('traceback', events[-1])

    def test_invalid_slot_reports_interpolation_phase(self):
        events = []
        self.probe.update(_rpt_probe_event=lambda event: events.append(dict(event)),
                          _rpt_probe_time=SimpleNamespace(time=lambda: 0))
        row = {'text': '[x]', 'interpolation': {'parts': [{'name': 'x', 'quote': 'yes'}],
                                                  'values': {}, 'unresolved': []}}
        self.probe['_rpt_probe_attempt']('text', lambda: self.probe['_rpt_probe_text'](row), row)
        self.assertEqual(events[-1]['phase'], 'interpolation')
        self.assertEqual(events[-1]['status'], 'failed')

    def test_unclosed_is_reported_without_slots(self):
        events = []
        self.probe.update(_rpt_probe_event=lambda event: events.append(dict(event)),
                          _rpt_probe_time=SimpleNamespace(time=lambda: 0))
        row = {'text': 'invented [', 'interpolation': {'parts': ['invented ['],
               'values': {}, 'unresolved': [], 'unclosed': True}}
        self.probe['_rpt_probe_attempt']('text', lambda: self.probe['_rpt_probe_text'](row), row)
        self.assertEqual(events[-1]['phase'], 'interpolation')
        self.assertEqual(events[-1]['diagnostic'], 'unclosed_interpolation')
        self.assertFalse(events[-1]['interpolation']['comparable'])
        self.assertEqual(events[-1]['interpolation']['slots_total'], 0)
        self.rendered.assert_not_called()

    def test_safe_diagnostics_match_whole_known_messages_only(self):
        diagnostic = self.probe['_rpt_probe_diagnostic']
        for message, expected in [("Unknown text tag 'shimmer'", 'unknown_text_tag'),
                                  ("'/shimmer' closes a text tag that isn't open", 'unexpected_closing_tag'),
                                  ("invalid conversion 'z'", 'invalid_conversion'),
                                  ("Unknown text tag 'shimmer' private synthetic value", 'unknown'),
                                  ("private synthetic value", 'unknown')]:
            with self.subTest(message=message):
                self.assertEqual(diagnostic(ValueError(message)), expected)

    def test_tag_name_is_extracted_only_from_anchored_diagnostic(self):
        tag = self.probe['_rpt_probe_diagnostic_tag']
        self.assertEqual(tag(ValueError("Unknown text tag 'k'")), 'k')
        self.assertEqual(tag(ValueError("'/other' closes a text tag that isn't open")), 'other')
        self.assertIsNone(tag(ValueError("Unknown text tag 'k' private synthetic value")))

    def test_comparable_and_unique_simple_names(self):
        recipe = {'parts': [{'name': 'alpha', 'quote': False}, {'name': 'alpha', 'quote': True},
                            {'name': 'beta_2', 'quote': False}],
                  'values': {'alpha': True, 'beta_2': 1.5}, 'unresolved': []}
        result = self.probe['_rpt_probe_interpolation_coverage'](recipe)
        self.assertTrue(result['comparable'])
        self.assertEqual(result['names'], ['alpha', 'beta_2'])


class ProbeProgressTests(unittest.TestCase):
    def environment(self, count):
        probe = probe_functions()
        calls, events, rendered = [], [], []
        clock = [0.0]
        context = SimpleNamespace()
        def show(name, **kwargs):
            calls.append(('show', name, kwargs))
        def call_screen(name, **kwargs):
            calls.append(('call_screen', name, kwargs))
            return 0
        def render(row):
            rendered.append(row['id'])
            clock[0] += .1
        renpy = SimpleNamespace(
            has_screen=lambda name: name == 'choice',
            show_screen=show, hide_screen=lambda name: calls.append(('hide', name)),
            call_screen=call_screen, display_menu=lambda items: calls.append(('menu', items)),
            pause=Mock(side_effect=AssertionError('A pause under the modal progress screen can deadlock')),
            free_memory=lambda: calls.append(('free_memory',)),
        )
        data = {'rows': [{'id': str(index)} for index in range(count)]}
        probe.update(
            _rpt_probe_io=SimpleNamespace(open=lambda *args, **kwargs: io.StringIO(json.dumps(data))),
            _rpt_probe_json=json,
            _rpt_probe_os=SimpleNamespace(environ={'RPT_VALIDATION_INPUT': 'invented-input'}),
            _rpt_probe_engine=SimpleNamespace(version_tuple=(6, 99, 0),
                                             game=SimpleNamespace(context=lambda: context)),
            _rpt_probe_time=SimpleNamespace(time=lambda: clock[0]),
            _rpt_probe_event=events.append,
            _rpt_probe_attempt=lambda name, operation, row=None: operation(),
            _rpt_probe_text=render,
            renpy=renpy,
        )
        return probe, calls, events, rendered

    def test_choice_is_followed_by_refreshed_progress_and_periodic_interactions(self):
        probe, calls, events, rendered = self.environment(12)
        probe['_rpt_probe_main']()
        self.assertEqual(rendered, [str(index) for index in range(12)])
        menu = next(index for index, call in enumerate(calls) if call[0] == 'menu')
        updates = [call[2] for call in calls if call[:2] == ('call_screen', '_rpt_validation_progress')]
        first = next(index for index, call in enumerate(calls)
                     if call[:2] == ('call_screen', '_rpt_validation_progress'))
        self.assertLess(menu, first)
        self.assertEqual(updates[0], {'done': 0, 'total': 12})
        self.assertEqual(updates[-1], {'done': 12, 'total': 12})
        self.assertGreater(len(updates), 2)
        probe['renpy'].pause.assert_not_called()
        self.assertEqual(calls[-1], ('hide', '_rpt_validation_progress'))
        self.assertEqual(events[-1], {'check': 'complete', 'status': 'passed'})

    def test_progress_screen_is_removed_when_a_refresh_fails(self):
        probe, calls, events, rendered = self.environment(12)
        def fail_screen(*args, **kwargs):
            raise RuntimeError('synthetic screen failure')
        probe['renpy'].call_screen = fail_screen
        with self.assertRaisesRegex(RuntimeError, 'synthetic screen failure'):
            probe['_rpt_probe_main']()
        self.assertEqual(calls[-1], ('hide', '_rpt_validation_progress'))
        self.assertFalse(rendered)
        self.assertFalse(any(event['check'] == 'complete' for event in events))

    def test_empty_input_still_refreshes_then_cleans_up(self):
        probe, calls, events, rendered = self.environment(0)
        probe['_rpt_probe_main']()
        self.assertEqual([call[2] for call in calls
                          if call[:2] == ('call_screen', '_rpt_validation_progress')],
                         [{'done': 0, 'total': 0}])
        self.assertEqual(calls[-1], ('hide', '_rpt_validation_progress'))
        self.assertEqual(events[-1], {'check': 'complete', 'status': 'passed'})


if __name__ == '__main__':
    unittest.main()
