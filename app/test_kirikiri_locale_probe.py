import unittest
from app.kirikiri_locale_probe import classify_boot_error, compare_boot_results

class BootPolicyTests(unittest.TestCase):
    def test_error_classification(self):
        for code in ('encoding_error','invalid_multibyte','filepath_conversion_error','filename_conversion_error','unicode_decode_error'):
            self.assertTrue(classify_boot_error({'code':code})['locale_candidate'])
        self.assertTrue(classify_boot_error({'win32_code':1113})['locale_candidate'])
        for code in ('cuda_error','script_syntax_error','generic_error','file_not_found'):
            self.assertFalse(classify_boot_error({'code':code})['locale_candidate'])
        self.assertFalse(classify_boot_error('invalid_multibyte')['locale_candidate'])
    def test_boot_comparison(self):
        self.assertEqual(compare_boot_results({'code_page':949,'outcome':'initialization_ready'})['status'],'not_required')
        native={'code_page':949,'outcome':'initialization_failed','error':{'code':'invalid_multibyte'}}
        self.assertTrue(compare_boot_results(native)['should_retry_japanese'])
        ja={'code_page':932,'outcome':'initialization_ready'}
        self.assertEqual(compare_boot_results(native,ja)['status'],'required')
        for outcome in ('window_visible','process_alive','initialization_failed'):
            self.assertEqual(compare_boot_results(native,{'code_page':932,'outcome':outcome})['status'],'unknown')
        self.assertEqual(compare_boot_results({'code_page':949,'outcome':'window_visible'},ja)['status'],'unknown')
        for code in ('cuda_error','script_syntax_error','generic_error'):
            result=compare_boot_results({'code_page':949,'outcome':'initialization_failed','error':{'code':code}},ja)
            self.assertEqual(result['status'],'unknown'); self.assertFalse(result['should_retry_japanese'])
        self.assertEqual(compare_boot_results({'code_page':932,'outcome':'initialization_ready'})['status'],'unknown')

if __name__ == '__main__': unittest.main()
