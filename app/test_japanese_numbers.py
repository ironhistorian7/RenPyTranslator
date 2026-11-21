import unittest

from japanese_numbers import compare, normalize_source
from japanese_output_policy import classify, language_errors


class JapaneseNumberTests(unittest.TestCase):
    def test_number_sequences_and_punctuation(self):
        cases = [
            ('百九十四、百九十五', '194,195'),
            ('二百二十一、二百二十二', '２２１、２２２'),
            ('「ごひゃく、きゅうじゅう、ろく！」', '「500,90,6!」'),
            ('千二百三十四っ！', '1234!'),
            ('「さんびゃく、はっぴゃく、さんぜん」', '300,800,3000'),
            ('零、十、二十', '0,10,20'),
        ]
        cfg = {'source_language': 'ja'}
        for source, target in cases:
            with self.subTest(source=source):
                self.assertEqual(compare(source, target), 'match')
                self.assertEqual(classify(source, target, cfg)['kind'], 'clean')
                self.assertEqual(language_errors(source, target, cfg), [])
                self.assertIsNotNone(normalize_source(source))

    def test_mismatch_is_reported(self):
        cfg = {'source_language': 'ja'}
        source, target = 'ごひゃく、きゅうじゅう、ろく！', '100,90,6!'
        self.assertEqual(compare(source, target), 'mismatch')
        details = classify(source, target, cfg)
        self.assertEqual(details['kind'], 'numeric_mismatch')
        self.assertEqual(language_errors(source, target, cfg, details), ['numeric value mismatch'])
        self.assertEqual(normalize_source(source), '500、90、6！')
        self.assertEqual(normalize_source('［百］、 二十！'), '［100］、 20！')
        self.assertEqual(compare('百、二百', '100'), 'mismatch')

    def test_unsupported_content_is_not_extracted(self):
        for source in ('百人', '三月二日', '第百章', '百/二', '[value]百',
                       '{color=red}百{/color}', '一万', '一二三', '十十',
                       '三・一四', '123', '百01'):
            with self.subTest(source=source):
                self.assertIsNone(normalize_source(source))
                self.assertIsNone(compare(source, '100'))
        self.assertIsNone(compare('百', '1.0'))
        self.assertIsNone(compare('百', '百'))

    def test_name_and_english_exceptions(self):
        source = '百'
        for extra in ({'_name_preparation': True}, {'_usage': 'name'},
                      {'_person_names': {'百': '백'}}):
            cfg = dict(source_language='ja', **extra)
            self.assertEqual(classify(source, '1', cfg)['kind'], 'clean')
            self.assertEqual(language_errors(source, '1', cfg), [])
        cfg = {'source_language': 'english'}
        self.assertEqual(classify('百', '1', cfg)['kind'], 'not_applicable')
        self.assertEqual(language_errors('百', '1', cfg), [])


if __name__ == '__main__':
    unittest.main()
