import unittest

from japanese_output_policy import classify, prepare_output
from japanese_resume import clean_history_recoveries


class HistoryRecoveryTests(unittest.TestCase):
    def test_confirmed_korean_name_allows_suffix_cleanup_only(self):
        for engine in ('renpy', 'kirikiri', 'rpgmaker'):
            cfg = dict(engine=engine, source_language='japanese',
                       _person_names={'ミッカ':'미카'}, _rpg_name_groups=(('ミッカ','미카'),))
            with self.subTest(engine=engine):
                source = 'ミッカの話'
                target = '미카, っッｯ!'
                result = classify(source, target, cfg)
                self.assertTrue(result['has_korean'])
                self.assertEqual(result['hangul_count'], 0)
                self.assertEqual(prepare_output(source, target, cfg)[0], '미카, !')
                self.assertEqual(prepare_output(source, 'ミッカ 미카っ', cfg)[0], 'ミッカ 미카')
                self.assertEqual(prepare_output('ミッカ', 'ミッカ', cfg)[0], 'ミッカ')

    def test_actual_engine_validators_count_names_but_not_control_arguments(self):
        from translation import validate_text
        cases = [('renpy', '{w}{color=한국}'), ('kirikiri', '[font face="한국"]'),
                 ('rpgmaker', r'\V[1]')]
        for engine, control in cases:
            cfg = dict(engine=engine, source_language='japanese',
                       _person_names={'ミカ':'미카'}, _rpg_name_groups=(('ミカ','미카'),))
            with self.subTest(engine=engine):
                source = 'ミカの話' + control
                self.assertEqual(validate_text(source, '미카 미카' + control, cfg), [])
                self.assertTrue(validate_text(source, '미카 미카', cfg))
                errors = validate_text(source, 'ミカ' + control, cfg)
                self.assertIn('no Korean translation', errors)
                self.assertNotIn('untranslated Japanese text', errors)

    def test_names_and_controls_do_not_hide_korean(self):
        for engine, control in [('renpy', '{color=한국}'), ('kirikiri', '[font face="한국"]'), ('rpgmaker', r'\V[1]')]:
            cfg = dict(engine=engine, source_language='japanese', _person_names={'ミカ': '미카'})
            with self.subTest(engine=engine):
                result = classify('ミカの話' + control, '미카 미카' + control, cfg)
                self.assertEqual(result['kind'], 'clean')
                self.assertEqual(result['hangul_count'], 0)
                self.assertEqual(result['japanese_count'], 0)
                self.assertEqual(classify('ミカの話' + control, 'ミカ' + control, cfg)['kind'], 'no_korean')
                self.assertEqual(classify('例' + control, control, cfg)['kind'], 'no_korean')

    def test_recovery_scope_and_validation(self):
        for engine in ('renpy', 'rpgmaker', 'kirikiri'):
            cfg = dict(engine=engine, source_language='japanese', _person_names={'ミカ':'미카'})
            row = dict(id='a', source='ミカの話', usage='dialogue', kind='dialogue', dialect='kag')
            base = dict(id='a', source=row['source'], model='m', fingerprint='fp')
            prior = dict(base, text='미카 미카', status='accepted')
            latest = dict(base, text=row['source'], status='source_fallback', error="a: ['no Korean translation']; output='미카 미카'")
            with self.subTest(engine=engine):
                found, events = clean_history_recoveries([row], {'a':latest}, {'a':[prior, latest]}, cfg)
                self.assertEqual(found['a']['text'], '미카 미카')
                self.assertEqual(found['a']['status'], 'failure_recovered')
                self.assertEqual(len(events), 1)
                for bad in (dict(prior, source='古い'), dict(prior, status='manual_translation'),
                            dict(prior, text='미카 日本語'), dict(prior, text='미카<rpt0/>')):
                    found, events = clean_history_recoveries([row], {'a':latest}, {'a':[bad, latest]}, cfg)
                    self.assertEqual(found['a'], latest)
                    self.assertFalse(events)
                for current in (dict(latest, status='manual_translation'),
                                dict(latest, model='reviewed override'),
                                dict(latest, error="a: ['markup mismatch', 'no Korean translation']; output='x'"),
                                dict(latest, status='accepted')):
                    found, events = clean_history_recoveries([row], {'a':current}, {'a':[prior,current]}, cfg)
                    self.assertEqual(found['a'], current)
                    self.assertFalse(events)
                found, events = clean_history_recoveries([row], {'a':latest}, {'a':[prior,latest]}, dict(cfg,source_language='english'))
                self.assertEqual(found['a'], latest)
                self.assertFalse(events)
                # A later selected success or overlay cannot be replaced.
                found, events = clean_history_recoveries([row], {'a':latest}, {'a':[prior,latest,prior]}, cfg)
                self.assertEqual(found['a'], latest)
                self.assertFalse(events)

    def test_structure_and_partial(self):
        cfg = dict(engine='renpy', source_language='japanese')
        row = dict(id='x', source='こんにちは{w}', usage='dialogue')
        base = dict(id='x', source=row['source'], model='m', fingerprint='fp')
        latest = dict(base, text='안녕日本{w}', status='partial_translation', error="x: ['untranslated Japanese text']; output='x'")
        bad = dict(base, text='안녕하세요', status='accepted')
        good = dict(base, text='안녕하세요{w}', status='accepted')
        found, events = clean_history_recoveries([row], {'x':latest}, {'x':[bad,latest]}, cfg)
        self.assertEqual(found['x'], latest)
        found, events = clean_history_recoveries([row], {'x':latest}, {'x':[good,bad,latest]}, cfg)
        self.assertEqual(found['x']['text'], good['text'])
        self.assertEqual(len(events), 1)

    def test_clean_partial_resume_without_model_and_structure_guard(self):
        from contextlib import redirect_stdout
        from unittest.mock import patch
        import io
        from test_japanese_resume import JapaneseResumeIntegrationTests, row
        import translation
        for engine, control in [('renpy', '{w}'), ('kirikiri', '[l]')]:
            with self.subTest(engine=engine):
                fixture = JapaneseResumeIntegrationTests()
                self.addCleanup(fixture.doCleanups)
                rows = [row('valid', 'ミカの話' + control), row('invalid', 'ミカの話' + control)]
                project, cfg, path = fixture.project(engine, rows,
                    {'valid': ('미카 미카' + control, 'partial_translation'),
                     'invalid': ('미카 미카', 'partial_translation')})
                def config(project, cfg, rows):
                    return dict(cfg, _person_names={'ミカ': '미카'})
                with patch('name_translation.mapping', return_value={'ミカ': '미카'}), \
                     patch('name_review.configuration', side_effect=config):
                    before = path.read_bytes()
                    found = translation.cache(project, cfg)
                    self.assertEqual(found['valid']['status'], 'failure_recovered')
                    self.assertIsNone(found['valid']['error'])
                    self.assertEqual(found['invalid']['status'], 'partial_translation')
                    self.assertEqual(path.read_bytes(), before)
                    # Isolate the valid row in the catalog: a resume must not call the model.
                    from engine import save_json
                    save_json(project/'data/catalog.json', rows[:1])
                    with patch('translation.request', side_effect=AssertionError('unexpected model request')), \
                         redirect_stdout(io.StringIO()):
                        translation.translate(project, cfg)
                    self.assertEqual(path.read_bytes(), before)

if __name__ == '__main__': unittest.main()
