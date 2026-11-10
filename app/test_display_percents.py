"""Invented strings only; no game, engine, model or existing cache access."""
import unittest

from display_text import compose,display_percents


class DisplayPercentTests(unittest.TestCase):
    def test_literal_percentage_works_with_legacy_mode_on_or_off(self):
        text=display_percents('할인 20% 적용, 0.2% 증가, 100% 완료.')
        self.assertEqual(text,'할인 20％ 적용, 0.2％ 증가, 100％ 완료.')
        self.assertEqual(text % {},text)
        self.assertEqual(display_percents(text),text)

    def test_genuine_fields_and_existing_escapes_are_kept(self):
        text='%(name)s: %03d, %.2f, %% / 할인 20% 적용'
        self.assertEqual(display_percents(text),'%(name)s: %03d, %.2f, %% / 할인 20％ 적용')
        self.assertEqual(display_percents('%(name)s: 20% 할인') % {'name':'민수'},'민수: 20％ 할인')

    def test_expression_and_tag_contents_are_not_changed(self):
        text='[score % 3] ["20%"] {custom="20%"} 할인 20% 적용'
        self.assertEqual(display_percents(text),'[score % 3] ["20%"] {custom="20%"} 할인 20％ 적용')

    def test_plain_percentage_followed_by_english_is_not_a_format_field(self):
        self.assertEqual(display_percents('20% off, 40% to go'),'20％ off, 40％ to go')

    def test_compose_changes_display_without_mutating_the_cache_entry(self):
        row={'kind':'dialogue','source':'A twenty percent reduction.'}
        entry={'text':'20% 할인입니다.'}
        expected='20％ 할인입니다.'
        self.assertEqual(compose(row,entry,{'show_reference':False}),expected)
        self.assertTrue(compose(row,entry,{}).startswith(expected+'\n'))
        self.assertEqual(entry,{'text':'20% 할인입니다.'})

    def test_reference_is_safe_and_source_fallback_is_kept(self):
        row={'kind':'dialogue','source':'20% off.'}
        output=compose(row,{'text':'할인 20% 적용.'},{})
        self.assertIn('20％ off.',output)
        self.assertEqual(output % {},output)
        self.assertEqual(compose(row,{'text':row['source'],'status':'source_fallback'},{}),row['source'])


if __name__=='__main__':unittest.main()
