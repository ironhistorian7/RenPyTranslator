import unittest
from kirikiri_runtime import compose_reference, patch_config_font

class ReferenceTest(unittest.TestCase):
    def test_reference_is_off_for_supported_dialects(self):
        for dialect in ('kag', 'kagex_psb'):
            for enabled in (True, False):
                with self.subTest(dialect=dialect, enabled=enabled):
                    self.assertEqual(compose_reference('한국어$', 'Hello [reading]字', dialect, enabled), '한국어$')
    def test_config_preserves_other_bytes_and_checks_exact_template(self):
        original = b'// bytes \x81\x40\r\n;userFace = global.SystemDefaultFontFace; // note\r\n'
        self.assertEqual(patch_config_font(original), original.replace(b';userFace = global.SystemDefaultFontFace;', b';userFace = "\\xB9D1\\xC740 \\xACE0\\xB515,Malgun Gothic";'))
        with self.assertRaises(ValueError): patch_config_font(b';userFace = "other";')
        with self.assertRaises(ValueError): patch_config_font(original + original)

    def test_unknown_dialect_is_rejected_even_when_off(self):
        for enabled in (True, False):
            with self.assertRaises(ValueError): compose_reference('한국어', 'Hello', 'unknown', enabled)

if __name__ == '__main__': unittest.main()
