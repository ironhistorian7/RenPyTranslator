import unittest
from ui_policy import discover,menu_translations,action_caption


class NestedMenuTests(unittest.TestCase):
    def test_gallery_is_a_standard_menu_caption(self):
        _,menu=self.policy('''screen navigation():
    textbutton _("Gallery") action ShowMenu("gallery")
    textbutton _("gallery") action ShowMenu("gallery")
    textbutton _("GALLERY{#menu}") action ShowMenu("gallery")
''')
        self.assertEqual(menu,{'Gallery':'갤러리','gallery':'갤러리','GALLERY{#menu}':'{#menu}갤러리'})
    def policy(self,text):
        result=discover({'arbitrary.rpy':text})
        return result,menu_translations(result)

    def test_init_screen_abbreviations_and_screen_boundary(self):
        result,menu=self.policy('''init 999:
    screen quick_menu():
        textbutton _("Hist") action ShowMenu("history")
        textbutton _("Q.S") action QuickSave()
        textbutton _("Q.L") action QuickLoad()
    style other:
        prefix _("Not a menu")
label start:
    menu:
        "A story choice":
            pass
''')
        self.assertEqual(menu,{'Hist':'기록','Q.S':'빠른 저장','Q.L':'빠른 불러오기'})
        self.assertEqual(result['choices'],['A story choice'])

    def test_icons_use_action_including_button_blocks_and_ignore_comments(self):
        result,menu=self.policy('''init -5:
    screen quick_menu():
        textbutton "◀-|" action Rollback() xpos -40 # tooltip _("Not a caption")
        textbutton "▶▶" action Skip() alternate Skip(fast=True)
        if _preferences.afm_enable:
            textbutton "a ■":
                text_hover_color "#F00"
                action Preference("auto-forward", "disable")
        else:
            textbutton "a ▶" action Preference("auto-forward", "enable")
        textbutton "▼" action If(renpy.get_screen("say"), HideInterface(), None)
''')
        self.assertEqual(menu,{'◀-|':'뒤로','▶▶':'건너뛰기','a ■':'자동 끄기','a ▶':'자동','▼':'숨기기'})
        self.assertNotIn('Not a caption',result['uses'])

    def test_custom_caption_and_unknown_action_stay_original(self):
        _,menu=self.policy('''screen quick_menu():
    textbutton "Secret Ending" action ShowMenu("history")
    textbutton "▼" action CustomAction()
    textbutton "▶" action If(flag, HideInterface(), CustomAction())
''')
        self.assertEqual(menu,{'Secret Ending':'Secret Ending','▼':'▼','▶':'▶'})

    def test_conflicting_icon_actions_stay_original(self):
        _,menu=self.policy('''screen quick_menu():
    textbutton "▶" action Skip()
    textbutton "▶" action QuickSave()
''')
        self.assertEqual(menu['▶'],'▶')

    def test_included_nested_screen_and_translation_tag(self):
        result,menu=self.policy('''init 10:
    screen navigation():
        use small_controls
    screen small_controls():
        textbutton _("Save{#menu}") action ShowMenu("save")
        textbutton _("Custom") action CustomAction()
    screen say(who,what):
        text _("Dialogue")
''')
        self.assertEqual(menu['Save{#menu}'],'{#menu}저장')
        self.assertEqual(menu['Custom'],'Custom')
        self.assertNotIn('Dialogue',result['uses'])

    def test_action_text_inside_caption_is_not_code(self):
        self.assertIsNone(action_caption('textbutton "action Skip()" action CustomAction()'))


if __name__=='__main__':unittest.main()
