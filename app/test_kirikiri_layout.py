import unittest

from kirikiri_layout import recover_linebreak_markers
from kirikiri_tokens import protect, restore


class LayoutRecoveryTests(unittest.TestCase):
    def recover(self, source, output):
        _, tokens = protect(source, 'kagex_psb')
        result = recover_linebreak_markers(source, output, tokens)
        self.assertIsNotNone(result)
        text, changed = result
        return restore(text, tokens), changed

    def test_single_missing_newline(self):
        self.assertEqual(self.recover('hello\nworld', '안녕 세상'), ('안녕 \n세상', [0]))

    def test_multiple_and_leading_trailing(self):
        self.assertEqual(self.recover('\nA\nB\n', '하나 둘'), ('\n하나 \n둘\n', [0, 1, 2]))

    def test_crlf(self):
        self.assertEqual(self.recover('A\r\nB', '단어'), ('단어\r\n', [0]))

    def test_functional_neighbors(self):
        text, changed = self.recover('[x]A\nB[y]', '<rpt000/>하나 둘<rpt002/>')
        self.assertEqual((text, changed), ('[x]하나 \n둘[y]', [1]))

    def test_reject_bad_markers_and_functional_omission(self):
        source = '[x]A\nB[y]'
        _, tokens = protect(source, 'kagex_psb')
        for output in ('<rpt000/>text<rpt999/>', '<rpt000/><rpt000/><rpt002/>',
                       '<rpt002/><rpt000/>', 'text', '<rpt000/>text'):
            with self.subTest(output=output):
                self.assertIsNone(recover_linebreak_markers(source, output, tokens))

    def test_opaque_controls_are_not_split(self):
        text, _ = self.recover('A\nB', '하나 [tag value]둘')
        self.assertNotIn('[tag\n value]', text)

    def test_source_tokens_must_match(self):
        self.assertIsNone(recover_linebreak_markers('A\nB', '한국어', ['[x]']))


if __name__ == '__main__':
    unittest.main()
