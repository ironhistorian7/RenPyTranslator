import unittest

from kirikiri_ruby import prepare, strip_output_ruby, needs_retranslation
from kirikiri_runtime import compose_reference
from kirikiri_tokens import validate

JA = {'engine': 'kirikiri', 'source_language': 'japanese', '_kirikiri_dialect': 'kagex_psb'}
EN = dict(JA, source_language='english')


class RubyTests(unittest.TestCase):
    def test_count_and_semantic_reading(self):
        body, readings, plan = prepare('[まめだ,1]豆狸と[ば]化[だ]狸', JA)
        self.assertEqual(body, '豆狸と化狸')
        self.assertEqual(readings, [{'reading': 'まめだ', 'base': '豆狸'},
                                    {'reading': 'ば', 'base': '化'},
                                    {'reading': 'だ', 'base': '狸'}])
        self.assertEqual(len(plan), 3)
        self.assertEqual(compose_reference('번역', '[べつ]名', 'kagex_psb', cfg=JA),
                         '번역')

    def test_other_tags_escapes_and_malformed_are_not_ruby(self):
        src = r'\[かな]字[font size=20]語[かな,x]字[かな,2]短'
        body, readings, _ = prepare(src, JA)
        self.assertEqual(body, src)
        self.assertEqual(readings, [])
        self.assertEqual(validate('[かな]字[wait]語', '글[wait]말', JA), [])
        self.assertTrue(validate('[かな]字[wait]語', '글말', JA))
        self.assertTrue(validate('[かな]字[wait]語', '글[other]말', JA))

    def test_cleanup_and_cached_selection(self):
        source = '[かな]字%r[wait]'
        self.assertEqual(strip_output_ruby(source, '[かな]글%r[wait]', JA), '글%r[wait]')
        self.assertEqual(strip_output_ruby(source, r'\[かな]글%r[wait]', JA), r'\[かな]글%r[wait]')
        self.assertTrue(needs_retranslation(source, '[かな]글%r[wait]', JA))
        self.assertFalse(needs_retranslation(source, '글%r[wait]', JA))
        self.assertFalse(needs_retranslation('[wait]字', '[wait]글', JA))

    def test_english_unchanged(self):
        source = '[かな]字[wait]'
        self.assertEqual(prepare(source, EN), (source, [], []))
        self.assertEqual(strip_output_ruby(source, '[かな]word[wait]', EN), '[かな]word[wait]')
        self.assertEqual(validate(source, '[かな]word[wait]', EN), [])
        self.assertTrue(validate(source, 'word[wait]', EN))


if __name__ == '__main__':
    unittest.main()
