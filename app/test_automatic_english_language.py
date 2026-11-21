import unittest

from label_policy import automatic_english_errors, unchanged_choice_errors


class AutomaticEnglishLanguageTests(unittest.TestCase):
    def setUp(self):
        self.cfg={'source_language':'english'}

    def check(self, source, target, usage='dialogue', cfg=None):
        return automatic_english_errors({'source':source,'usage':usage},target,cfg or self.cfg)

    def test_untranslated_and_reworded_prose_are_rejected(self):
        for source, target in [('I see.','I see.'),
                               ('You too, [player_name].','You too, [player_name].'),
                               ('Please come back.','Come back later.')]:
            with self.subTest(source=source):
                self.assertEqual(self.check(source,target),['no Korean translation'])
        self.assertEqual(self.check('Go back','Go back',usage='choice'),['no Korean translation'])

    def test_translated_and_non_english_are_preserved(self):
        self.assertEqual(self.check('I see.','알겠어.'),[])
        self.assertEqual(self.check('I see.','I see.',cfg={'source_language':'japanese'}),[])
        self.assertEqual(self.check('I see.','I see.',cfg={'source_language':'english','_name_preparation':True}),[])

    def test_names_labels_and_technical_strings_are_preserved(self):
        cfg={'source_language':'english','_person_names':{'Jane Doe':'제인 도'},
             '_rpg_name_groups':[('Alice Smith','앨리스')]}
        for source in ['Jane Doe','Alice Smith']:
            self.assertEqual(self.check(source,source,cfg=cfg),[])
        for usage in ['speaker_label','ui_label',None]:
            self.assertEqual(self.check('Unknown Person','Unknown Person',usage=usage),[])
        for source in ['123 456','HP MP','https://example.org/a page',
                       'C:/folder/file name','foo_bar value','example.org docs']:
            self.assertEqual(self.check(source,source),[],source)

    def test_controls_are_removed_before_counting(self):
        self.assertEqual(self.check(r'\C[2]Hello\C[0] [player_name]',r'\C[2]Hello\C[0] [player_name]'),[])
        self.assertEqual(self.check(r'\C[2]I see\C[0]',r'\C[2]I see\C[0]'),['no Korean translation'])
        self.assertEqual(self.check('You too, [player_name].','너도, [player_name].'),[])

    def test_existing_review_helper_remains_choice_only(self):
        row={'source':'I see.','usage':'dialogue'}
        self.assertEqual(unchanged_choice_errors(row,'I see.',self.cfg),[])


if __name__=='__main__':
    unittest.main()
