import unittest
from kirikiri_tokens import protect,restore,readable,validate


class UIControlsTests(unittest.TestCase):
    def test_tjs_captions_preserve_variables_without_treating_brackets_as_commands(self):
        cfg={'_kirikiri_dialect':'kirikiri_tjs_ui'}
        source='キー[Ctrl] ${caption}\n100%'
        text,tokens=protect(source,cfg)
        self.assertEqual(tokens,['${caption}','\n'])
        self.assertIn('[Ctrl]',text)
        self.assertEqual(restore(text,tokens,cfg),source)
        self.assertEqual(validate(source,'키[Ctrl] ${caption}\n100%',cfg),[])
        self.assertTrue(validate(source,'키[Ctrl]\n100%',cfg))
        self.assertIn('[Ctrl]',readable(source,cfg))

    def test_existing_kag_controls_are_unchanged(self):
        text,tokens=protect('[font size=21]text',{'_kirikiri_dialect':'kag'})
        self.assertEqual(tokens,['[font size=21]'])


if __name__=='__main__':unittest.main()
