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
