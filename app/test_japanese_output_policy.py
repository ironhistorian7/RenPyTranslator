import unittest
from japanese_output_policy import enabled, classify, prepare_output, visible_view, language_errors

class SharedJapanesePolicyTests(unittest.TestCase):
    def test_scope_and_english_unchanged(self):
        for engine in ('renpy', 'rpgmaker', 'kirikiri', None):
            cfg = {'source_language':'ja'}
            if engine is not None: cfg['engine'] = engine
            self.assertTrue(enabled(cfg))
            self.assertEqual(prepare_output('例', '한국어っッｯ', cfg)[0], '한국어')
            cfg['source_language'] = 'english'
            self.assertFalse(enabled(cfg))
            self.assertEqual(prepare_output('Example', '한국어っ', cfg)[0], '한국어っ')
        self.assertFalse(enabled({'engine':'unknown','source_language':'ja'}))

    def test_engine_tokens_and_offsets(self):
        samples = [
            ('renpy', '{color=赤}[名前!t]한국어っ{/color}'),
            ('kirikiri', '[chara name="名前っ"]한국어っ'),
            ('rpgmaker', r'\Plugin[名前っ]한국어っ'),
        ]
        for engine, target in samples:
            cfg = {'engine':engine,'source_language':'japanese'}
            with self.subTest(engine=engine):
                view = visible_view('例',target,cfg)
                self.assertEqual(len(view),len(target))
                result = classify('例',target,cfg)
                self.assertEqual(result['spans'], [[len(target)-1,len(target)] if engine != 'renpy' else [target.index('っ'),target.index('っ')+1]])
                output, _ = prepare_output('例',target,cfg)
                self.assertEqual(output, target[:target.index('っ', target.index('한국어'))] + target[target.index('っ', target.index('한국어'))+1:])

    def test_existing_placeholder_is_masked_without_index_collision(self):
        cfg = {'source_language':'ja'}
        target = '<rpt999/>[名前]한국어っ'
        self.assertEqual(len(visible_view('例', target, cfg)), len(target))
        self.assertEqual(prepare_output('例', target, cfg)[0], '<rpt999/>[名前]한국어')

    def test_confirmed_names_only_when_present_in_source(self):
        cfg = {'source_language':'ja', '_person_names':{'ミッカ':'미카'}}
        self.assertEqual(classify('ミッカ', 'ミッカ', cfg)['kind'], 'clean')
        self.assertEqual(prepare_output('ミッカの例', 'ミッカ 한국어っ', cfg)[0], 'ミッカ 한국어')
        self.assertEqual(classify('別の例', '한국어 ミッカ', cfg)['kind'], 'mixed')
        self.assertEqual(prepare_output('ミッカ、例', '미카 っ', cfg)[0], '미카 ')

    def test_rpg_wrapper_preserves_confirmed_name_and_gate(self):
        import rpgmaker_output_policy as rpg
        for extra in ({'_person_names':{'ミッカ':'미카'}}, {'_rpg_name_groups':(('ミッカ','미카'),)}):
            cfg = dict(engine='rpgmaker', source_language='ja', **extra)
            text, details = rpg.prepare_output('ミッカ', 'ミッカ', cfg)
            self.assertEqual(text, 'ミッカ')
            self.assertEqual(details['kind'], 'clean')
            self.assertEqual(details['japanese_count'], 0)
        self.assertEqual(rpg.prepare_output('例', '한국어っ', {'engine':'renpy','source_language':'ja'})[0], '한국어っ')

    def test_name_preparation_retains_small_tsu_with_korean(self):
        for extra in ({'_name_preparation': True}, {'_usage': 'name'}, {'_kirikiri_usage': 'speaker_label'}):
            cfg = dict(source_language='ja', **extra)
            self.assertEqual(prepare_output('名前', '미카ッ', cfg)[0], '미카ッ')

    def test_non_japanese_source_and_name_exceptions(self):
        cfg = {'source_language':'ja'}
        for value in ('123', 'OK', '...'):
            self.assertEqual(classify(value, value, cfg)['kind'], 'clean')
            self.assertEqual(language_errors(value, value, cfg), [])
        self.assertEqual(classify('例文', '...', cfg)['kind'], 'no_korean')
        self.assertEqual(language_errors('例文', '...', cfg), ['no Korean translation'])
        self.assertEqual(classify('OK', '日本語', cfg)['kind'], 'mixed')
        self.assertEqual(language_errors('OK', '日本語', cfg), ['untranslated Japanese text'])
        for extra in ({'_name_preparation':True}, {'_kirikiri_usage':'speaker_label'}, {'_usage':'name'}):
            active = dict(cfg, **extra)
            self.assertEqual(classify('名前', '名前', active)['kind'], 'clean')
            self.assertEqual(language_errors('名前', '名前', active), [])
        self.assertEqual(language_errors('例文', '日本語', cfg), ['untranslated Japanese text', 'no Korean translation'])
        self.assertEqual(language_errors('例文', '日本語', cfg, classify('例文', '日本語', cfg)), ['untranslated Japanese text', 'no Korean translation'])

    def test_thresholds_and_safe_cleanup(self):
        cfg = {'source_language':'ja'}
        self.assertEqual(classify('例','이것은충분히긴한국어문장あ입니다',cfg)['kind'], 'minor')
        self.assertEqual(classify('例','짧아あ',cfg)['kind'], 'mixed')
        self.assertEqual(classify('例','한국어',cfg)['kind'], 'clean')
        for target in ('っ', '', '한국어っかな'):
            self.assertEqual(prepare_output('例',target,cfg)[0],target)
        self.assertEqual(prepare_output('っ！','っ！',cfg)[0], '！')
        self.assertEqual(prepare_output('っ！','っ',cfg)[0], 'っ')

if __name__ == '__main__': unittest.main()
