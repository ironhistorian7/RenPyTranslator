import unittest

import kirikiri_name_readings as names
import kirikiri_ruby


CFG = {'engine': 'kirikiri', 'source_language': 'japanese', '_kirikiri_dialect': 'kagex_psb'}


def row(source, kind='dialogue', speaker=None, dialect='kagex_psb'):
    return {'source': source, 'kind': 'dialogue' if kind == 'dialogue' else 'string',
            'usage': kind, 'speaker_internal': speaker, 'dialect': dialect,
            'location': ['scenes', 0, 'texts', 0, 2 if kind == 'dialogue' else 1]}


class KiriKiriNameReadingsTests(unittest.TestCase):
    def evidence(self, source, index, cfg=CFG):
        return [(item['base'], item['reading']) for item in names.evidence_for_row(source, cfg, index)]

    def mapped(self, source, index, cfg=CFG):
        body, _, plan = kirikiri_ruby.prepare(source, cfg)
        return [(body[start:end], target) for start, end, target
                in names.name_spans(body, plan, cfg, index)]

    def test_reuses_whole_readings_and_treats_kana_typography_as_equivalent(self):
        index = names.collect([
            row('。[たかなみ,1]高波が来た。'),
            row('。[ﾀｶﾅﾐ,1]高波。'),
            row('。[タカナミ,1]高波。'),
        ], CFG)
        self.assertEqual(index.readings, {'高波': 'たかなみ'})
        self.assertEqual(self.evidence('高波は来た。高波も来た。', index), [('高波', 'たかなみ')])
        self.assertEqual(self.evidence('高波さんが来た。', index), [('高波', 'たかなみ')])
        self.assertEqual(self.evidence('高波さんずい。', index), [])
        self.assertEqual(self.evidence('高は来た。', index), [])
        self.assertEqual(self.evidence('高波山に登る。高波たり。', index), [])

    def test_local_annotations_keep_conflicts_and_longer_base_ranges(self):
        index = names.collect([
            row('[たかなみ,1]高波。'),
            row('[たかなみ,1]高波。'),
            row('[こうは,1]高波。'),
            row('[たかなみやま,2]高波山。'),
            row('[たかなみむら,2]高波村。'),
        ], CFG, {'高波': '다카나미', '高': '다카'})
        self.assertEqual(index.ambiguous, {'高波'})
        self.assertEqual(self.evidence('高波。', index), [])
        self.assertEqual(self.evidence('[こうは,1]高波。', index), [('高波', 'こうは')])
        self.assertEqual(self.evidence('高波村。', index), [('高波村', 'たかなみむら')])
        self.assertEqual(self.mapped('[こうは,1]高波。高波山。', index), [])

    def test_approved_substitutions_only_use_visible_exact_text(self):
        index = names.collect([
            row('[たかなみ,1]高波。'),
            row('[たかなみさん,3]高波さん。'),
            row('[たかなみやま,2]高波山。'),
            row('高波', 'speaker_label', 'A'),
            row('たかなみ', 'speaker_label', 'A'),
            row('タカナミ', 'speaker_label', 'B'),
        ], CFG, {'高波': '다카나미'})
        self.assertEqual(index.aliases, {'高波': '다카나미', 'たかなみ': '다카나미'})
        source = ('[たかなみ,1]高波。[不明]たかなみちゃんが来た。高波山。[こうは,1]高波。'
                  '[たかなみさん,3]高波さん。タカナミ。')
        self.assertEqual(self.mapped(source, index), [('高波', '다카나미'), ('たかなみ', '다카나미')])
        self.assertEqual(self.mapped('[iscript x=高波]高波が来た。', index), [('高波', '다카나미')])

    def test_shared_speaker_labels_are_not_automatically_linked(self):
        index = names.collect([
            row('[ゆきもり,1]雪森。'),
            row('雪森', 'speaker_label', 'A'),
            row('ゆきもり', 'speaker_label', 'A'),
            row('ゆきもり', 'speaker_label', 'B'),
            row('ゆきもり', 'dialogue', 'A'),
        ], CFG, {'雪森': '유키모리'})
        self.assertEqual(index.aliases, {'雪森': '유키모리'})
        off_slot = dict(row('ゆきもり', 'speaker_label', 'A'), location=['scenes', 0, 'texts', 0, 2])
        index = names.collect([row('[ゆきもり,1]雪森。'), row('雪森', 'speaker_label', 'A'), off_slot],
                              CFG, {'雪森': '유키모리'})
        self.assertEqual(index.aliases, {'雪森': '유키모리'})

    def test_other_language_and_dialect_are_ignored(self):
        cfg = dict(CFG, source_language='english')
        index = names.collect([row('[たかなみ,1]高波。')], cfg, {'高波': '다카나미'})
        self.assertEqual(index.readings, {})
        index = names.collect([row('[たかなみ,1]高波。', dialect='kag')], CFG)
        self.assertEqual(index.readings, {})
        self.assertEqual(self.evidence('[たかなみ,1]高波。', index, cfg), [])
        self.assertEqual(self.mapped('高波。', index, cfg), [])

    def test_accepts_the_existing_prepared_tuple(self):
        index = names.collect([row('[たかなみ,1]高波。')], CFG)
        prepared = kirikiri_ruby.prepare('[ゆきもり,1]雪森。高波。', CFG)
        result = names.evidence_for_row('', CFG, index, prepared=prepared)
        self.assertEqual(result, [{'base': '雪森', 'reading': 'ゆきもり'},
                                  {'base': '高波', 'reading': 'たかなみ'}])

    def test_plain_kag_uses_only_approved_visible_body_names(self):
        cfg = dict(CFG, _kirikiri_dialect='kag')
        index = names.collect([row('[たかなみ,1]高波。')], cfg, {'高波': '다카나미'})
        source = '[eval exp="高波"]高波さんが来た。'
        evidence, spans = names.evidence_and_names(source, cfg, index)
        self.assertEqual(evidence, [])
        self.assertEqual([(source[start:end], target) for start, end, target in spans],
                         [('高波', '다카나미')])
        for usage in ('speaker_label', 'ui_label'):
            self.assertEqual(names.evidence_and_names(source, dict(cfg, _kirikiri_usage=usage), index),
                             ([], []))

    def test_cannot_admit_control_or_untranslated_target_as_approved_korean(self):
        index = names.collect([], CFG, {'高波': '高波', '古川': '후루카와[foo]',
                                        '富山': '도야마%1', '雪森': '유키모리<>'})
        self.assertEqual(index.aliases, {})

    def test_approved_nameplate_is_scoped_to_clear_name_use(self):
        index = names.collect([row('[ゆきもり,1]雪森。')], CFG,
                              nameplate_names={'雪森': '유키모리', '看護師': '간호사'})
        self.assertEqual(self.mapped('看護師が窓を開けた。雪森が来た。', index), [])
        self.assertEqual(self.mapped('看護師長に会った。雪森様式だ。', index), [])
        self.assertEqual(self.mapped('「雪森」', index), [('雪森', '유키모리')])
        self.assertEqual(self.mapped('雪森先生が来た。', index), [('雪森', '유키모리')])
        self.assertEqual(self.mapped('[ゆきもり,1]雪森が来た。', index), [('雪森', '유키모리')])

    def test_one_character_name_uses_whole_local_reading_without_propagation(self):
        index = names.collect([row('[とおる]透。')], CFG, {'透': '토오루'})
        self.assertEqual(index.readings, {})
        self.assertEqual(self.mapped('透と話した。', index), [])
        self.assertEqual(self.mapped('「透」', index), [('透', '토오루')])
        self.assertEqual(self.mapped('透さんが来た。', index), [('透', '토오루')])
        self.assertEqual(self.mapped('[とおる]透と話した。', index), [('透', '토오루')])
        self.assertEqual(self.mapped('[すく]透く。', index), [])


if __name__ == '__main__':
    unittest.main()
