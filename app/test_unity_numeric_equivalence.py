import unittest

from unity_text_formats import quantity_mismatch, restore_outer_parentheses, validate_replacement


class UnityNumericEquivalenceTests(unittest.TestCase):
    def check(self, source, target, expected=True):
        self.assertEqual(validate_replacement(source, target, kind='command'), expected)

    def test_english_numbers_can_become_digits(self):
        self.check('Ten fish', '물고기 10마리')
        self.check('Twenty-one fish and two birds', '물고기 21마리와 새 2마리')
        self.check('Eight hundred and twelve fish', '물고기 812마리')
        self.check('One thousand two hundred fish', '물고기 1200마리')
        self.check('Ten fish', '물고기 11마리')
        self.check('Ten fish', '물고기 10마리와 새 10마리')
        self.check('There are fish', '물고기 10마리')

    def test_digit_and_control_integrity(self):
        self.check('Level 12, ten fish {0} <size=20>', '레벨 12, 물고기 10마리 {0} <size=20>')
        self.check('Level 12, ten fish {0}', '레벨 13, 물고기 10마리 {0}')
        self.check('Ten fish {1}', '물고기 10마리 {2}', False)
        self.check('{ten} fish', '{ten} 물고기 10마리')
        self.check('Ten fish', '물고기 열 마리')

    def test_grouped_and_scaled_numbers(self):
        self.check('Two thousand fish', '물고기 2,000마리')
        self.check('Four thousand fish', '물고기 4천 마리')
        self.check('Eighty thousand fish', '물고기 8만 마리')
        self.check('Two million fish', '물고기 2백만 마리')
        self.check('Four thousand fish', '물고기 5천 마리')
        self.check('Two thousand fish', '물고기 2,001마리')
        self.check('Two fish', '물고기 2.0마리')

    def test_ordinals_require_a_korean_ordinal_unit(self):
        self.check('The third floor', '3층')
        self.check('The twenty-first floor', '21층')
        self.check('The third floor', '4층')
        self.check('The third floor', '3마리')

    def test_quantity_review_is_conservative(self):
        self.assertTrue(quantity_mismatch('a thousand and fifty an hour', '시간당 15만'))
        self.assertTrue(quantity_mismatch('Four thousand fish', '물고기 5천 마리'))
        self.assertFalse(quantity_mismatch('Four thousand fish', '물고기 4천 마리'))
        self.assertFalse(quantity_mismatch('Eighty thousand fish', '물고기 8만 마리'))
        self.assertFalse(quantity_mismatch('Two fish and three birds', '물고기 3마리와 새 2마리'))
        self.assertFalse(quantity_mismatch('The third floor', '3층'))

    def test_outer_pair_restore_is_conservative(self):
        self.assertEqual(restore_outer_parentheses('(Hello)', '안녕'), '(안녕)')
        self.assertEqual(restore_outer_parentheses('（Hello）', '안녕'), '（안녕）')
        for source, value in [('(Hello)', '(안녕)'), ('(Hello)', '안녕)'),
                              ('(Hello) world', '안녕'), ('((Hello))', '안녕'),
                              ('(Hello) (there)', '안녕')]:
            with self.subTest(source=source, value=value):
                self.assertEqual(restore_outer_parentheses(source, value), value)


if __name__ == '__main__':
    unittest.main()
