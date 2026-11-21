"""Synthetic tests only: no game, cache, or model access."""
import unittest
from rpgmaker_input import clean_context, configuration, prepare, reading_text, restore


def actor(uid, name, index=1, **extra):
    return dict(id=uid, source=name, file='www/data/Actors.json', usage='speaker_label',
                location={'fmt': 'json', 'ptr': f'[{index}].name'}, **extra)


class InputTests(unittest.TestCase):
    def cfg(self, rows, known=None, **extra):
        return configuration(dict(engine='rpgmaker', **extra), rows, known)

    def test_restore_header_and_body_without_replacement(self):
        cfg = self.cfg([actor('a', 'Mira')], {'a': {'source': 'Mira', 'text': '미라'}})
        plan = prepare('  Mira  \r\nMira is nearby.\r\n', cfg)
        self.assertEqual(plan['text'], 'Mira is nearby.\r\n')
        self.assertEqual(restore('Mira가 근처에 있다.\r\n', plan), '  미라  \r\nMira가 근처에 있다.\r\n')

    def test_only_exact_confirmed_plain_header_and_body(self):
        cfg = self.cfg([actor('a', 'Mira')])
        for source in ('Unknown\nHello', 'Mira', 'Mira\n  ', '\nMira\nHello',
                       r'\C[1]Mira'+'\nHello', r'\N[1]'+'\nHello', 'Mira!\nHello'):
            with self.subTest(source=source):
                self.assertEqual(prepare(source, cfg)['text'], source)
        self.assertEqual(prepare('Mira\nHello', cfg, 'speaker_label')['text'], 'Mira\nHello')
        self.assertEqual(restore('안녕', prepare('Mira\nHello', cfg)), 'Mira\n안녕')

    def test_echoed_canonical_header_is_not_duplicated(self):
        cfg = self.cfg([actor('a', 'Mira')], {'a': {'source': 'Mira', 'text': '미라'}})
        plan = prepare('Mira\r\nHello', cfg)
        self.assertEqual(restore('미라\n안녕', plan), '미라\r\n안녕')
        self.assertEqual(restore('Mira\n안녕', plan), '미라\r\nMira\n안녕')

    def test_body_starting_with_name_is_preserved(self):
        cfg = self.cfg([actor('a', 'Mira', base_source='ミラ')],
                       {'a': {'source': 'Mira', 'text': '미라'}})
        for body_name in ('Mira', 'ミラ', '미라'):
            with self.subTest(body_name=body_name):
                plan = prepare('Mira\n' + body_name + '\nHello', cfg)
                self.assertEqual(restore('미라\n안녕', plan), '미라\n미라\n안녕')

    def test_speaker_guard_and_raw_header_metadata(self):
        row = actor('a', 'Mira', base_source='ミラ')
        cfg = self.cfg([row], {'a': {'source': 'Mira', 'text': '미라'}})
        for speaker in (None, '', '?', 'none', 'unknown', 'UNKNOWN', 'unknown speaker', 'なし', '不明', 'Mira', 'ミラ', '미라'):
            with self.subTest(speaker=speaker):
                plan = prepare('Mira\nHello', cfg, speaker=speaker)
                self.assertEqual(plan['text'], 'Hello')
                self.assertEqual(plan['source_header'], 'Mira')
                self.assertEqual(plan['header'], '미라')
        plan = prepare('Mira\nHello', cfg, speaker='Other')
        self.assertEqual(plan['text'], 'Mira\nHello')
        self.assertEqual(plan['source_header'], '')

    def test_plain_multiline_is_not_a_reading_reference(self):
        for source in ('Hello\nworld', 'こんにちは\n世界'):
            self.assertIsNone(reading_text(source))
        source = 'Mira\r\n'+r'Hello\C[2] \N[2]\V[1]\.'
        self.assertEqual(reading_text(source), 'Mira\r\n'+r'Hello \N[2]\V[1]')

    def test_approved_values_and_cache_anchors(self):
        row = actor('a', 'Mira', base_source='ミラ')
        cfg = self.cfg([row], {'a': {'source': 'Mira', 'text': '미라'}}, _person_names={'ミラ': '밀라'})
        self.assertEqual(cfg['_rpg_display_names'], {'Mira': '밀라', 'ミラ': '밀라'})
        for saved in ({'source': 'other', 'text': '미라'},
                      {'source': 'Mira', 'text': '미라', 'error': 'failure'},
                      {'source': 'Mira', 'text': '미라', 'status': 'source_fallback'}):
            self.assertEqual(self.cfg([row], {'a': saved})['_rpg_display_names']['Mira'], 'Mira')

    def test_conflicting_forms_have_no_canonical_mapping(self):
        rows = [actor('a', 'Mira', 1), actor('b', 'Mira', 2)]
        known = {'a': {'source': 'Mira', 'text': '미라'}, 'b': {'source': 'Mira', 'text': '미라2'}}
        self.assertNotIn('Mira', self.cfg(rows, known)['_rpg_display_names'])
        cfg = self.cfg([actor('c', 'Mira', base_source='ミラ')],
                       _person_names={'Mira': '미라', 'ミラ': '밀라'})
        self.assertEqual(cfg['_rpg_display_names'], {})

    def test_clean_context_keeps_guidance_and_does_not_echo_dropped_text(self):
        ctx = {'source_passage': [{'text': 'private stale'}], 'original_source': 'old',
               'reading_text': 'old', 'spelling_context': 'old', 'correction_reading': 'old',
               'translation_guidance': 'user text', 'current_state': 'state', 'glossary': {'a': 'b'}}
        cleaned, dropped = clean_context(ctx)
        self.assertEqual(cleaned, {'translation_guidance': 'user text', 'current_state': 'state', 'glossary': {'a': 'b'}})
        self.assertNotIn('private stale', str(dropped))
        self.assertEqual(len(dropped), 5)


if __name__ == '__main__':
    unittest.main()
