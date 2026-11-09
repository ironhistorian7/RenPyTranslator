"""Invented syntax only: no engine, model, project or filesystem fixtures."""
import re
import unittest

from renpy_text_syntax import (TOKENS, BUILTIN_TAG_NAMES, PURE_VISUAL_TAG_NAMES,
                               ACCESSIBILITY_TAG_NAMES, RUBY_TAG_NAMES)


class RenpyTextSyntaxTests(unittest.TestCase):
    def test_nested_interpolation_keeps_expressions_flags_and_format(self):
        values = ('[player.names[0]]', '[mapping["close]bracket"]!q]',
                  "[mapping['it\\\'s ] quoted']!ti]", '[amount:08.2f]',
                  '[player.name!s!t!i!q!f!ulc]', '[func({"key": [1, 2]})!r]',
                  '[records[indexes[0]]["name"]!q]', '["""a ] bracket"""]')
        source = ' invented '.join(values)
        self.assertEqual(TOKENS.findall(source), list(values))

    def test_escapes_take_precedence_without_hiding_following_real_units(self):
        source = '{{i} [[name] 〖〖literal {i}[player.names[0]]{/i}'
        self.assertEqual(TOKENS.findall(source), ['{{', '[[', '〖〖', '{i}', '[player.names[0]]', '{/i}'])
        self.assertEqual(TOKENS.sub('', source), 'i} name] literal ')

    def test_tag_arguments_and_custom_and_style_references_are_opaque(self):
        values = ('{color=[palette[index]]}', '{=invented_style}', '{/}',
                  '{feature:liga=0}', '{a=call:invented_label}', '{image=small_icon}',
                  '{custom_control=a:b}', '{#invented-context}', '{w=0.4}', '{alt}', '{/alt}')
        self.assertEqual(TOKENS.findall(' words '.join(values)), list(values))

    def test_text_level_percent_and_space_escapes_are_preserved(self):
        source = r'An invented 50\% value\ space [items[0]]'
        self.assertEqual(TOKENS.findall(source), [r'\%', '\\ ', '[items[0]]'])
        multiline = '[lookup(\n    names[0], "quoted ] text"\n)!q]'
        self.assertEqual(TOKENS.findall(multiline), [multiline])

    def test_registry_preserves_functional_and_accessibility_distinctions(self):
        self.assertTrue({'alpha', 'feature', 'plain', 'k'} <= PURE_VISUAL_TAG_NAMES)
        self.assertEqual(ACCESSIBILITY_TAG_NAMES, {'alt', 'noalt'})
        self.assertEqual(RUBY_TAG_NAMES, {'art', 'rb', 'rt'})
        self.assertTrue({'w', 'p', 'nw', 'fast', 'done', 'clear', '', '#', 'horiz', 'vert', 'shader'} <= BUILTIN_TAG_NAMES)
        self.assertFalse({'cps', 'a', 'image', 'rb', 'alt'} & PURE_VISUAL_TAG_NAMES)

    def test_printf_extension_shares_offsets_without_matching_inside_expressions(self):
        formats = re.compile(r'%(?:\([^)]+\))?[#0 +\-]*(?:\d+|\*)?(?:\.\d+)?[diouxXeEfFgGcrsa%]')
        text = 'A %(name)s B %03d C %% D [table["%s"]]'
        self.assertEqual(TOKENS.findall(text), ['%(name)s', '[table["%s"]]'])
        self.assertEqual(TOKENS.findall(text, formats=formats),
                         ['%(name)s', '%03d', '%%', '[table["%s"]]'])

    def test_optional_response_tags_do_not_consume_normal_angle_prose(self):
        text = '2 < 3 <rpt000/> <widget data=">">[player.names[0]]</widget>'
        self.assertEqual(TOKENS.findall(text), ['[player.names[0]]'])
        self.assertEqual(TOKENS.findall(text, angle_tags=True),
                         ['<rpt000/>', '<widget data=">">', '[player.names[0]]', '</widget>'])

    def test_modern_ruby_protects_delimiters_and_keeps_both_payloads_visible(self):
        text = '〖base|reading〗 / 〖[names[0]]｜other reading〗 / 〖literal〗 / 〖〖escaped'
        self.assertEqual(TOKENS.findall(text),
                         ['〖', '|', '〗', '〖', '[names[0]]', '｜', '〗', '〖〖'])
        self.assertEqual(TOKENS.sub('', text), 'basereading / other reading / 〖literal〗 / escaped')

    def test_malformed_units_are_not_rejected_or_partially_protected(self):
        self.assertEqual(TOKENS.findall('plain [names[0]'), [])
        self.assertEqual(TOKENS.findall('plain {unclosed'), [])
        self.assertEqual(TOKENS.findall('〖missing|close'), [])
        self.assertEqual(TOKENS.findall('〖base{b}tag{/b}|reading〗'), ['{b}', '{/b}'])

    def test_match_records_and_pattern_like_operations(self):
        text = 'x [names[0]] y {w}'
        match = TOKENS.search(text)
        self.assertEqual(match.group(), '[names[0]]')
        self.assertEqual(match.group(0), match[0])
        self.assertEqual(match.group(0, 0), ('[names[0]]', '[names[0]]'))
        self.assertEqual(match.span(), (2, 12))
        self.assertEqual(match.string[match.start():match.end()], match.group())
        self.assertEqual(match.groups(), ())
        self.assertEqual(match.groupdict(), {})
        with self.assertRaises(IndexError):
            match.group(1)
        self.assertIsNone(TOKENS.match(text))
        self.assertEqual(TOKENS.match(text, 2).group(), '[names[0]]')
        self.assertEqual(TOKENS.fullmatch('[names[0]]').group(), '[names[0]]')
        self.assertIsNone(TOKENS.fullmatch('[names[0]] trailing'))
        self.assertEqual(TOKENS.sub(lambda m: '(' + m.group() + ')', text, count=1), 'x ([names[0]]) y {w}')
        self.assertEqual(TOKENS.sub(r'<\g<0>>', '{w}'), '<{w}>')
        self.assertEqual(TOKENS.findall(text, 13), ['{w}'])

    def test_expression_text_is_never_evaluated(self):
        source = '[__import__("invented_never_import").run(items[0])!q]'
        self.assertEqual(TOKENS.findall(source), [source])


if __name__ == '__main__':
    unittest.main()
