import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from kirikiri_reviewed_terms import apply

class ReviewedTermsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.project=Path(self.tmp.name);(self.project/'data').mkdir()
        self.cfg={'engine':'kirikiri','source_language':'japanese','model':'synthetic'}
        self.path=self.project/'data/reviewed-terms.json'
        self.rules=[dict(source_literals=['夏葉'],translation='나츠하',target_variants=['나츠바','나츠바짱'],exclude_source_literals=['別人'])]
        self.path.write_text(json.dumps({'rules':self.rules},ensure_ascii=False),encoding='utf8')
    def row(self,uid,source): return dict(id=uid,source=source,dialect='kagex_psb',usage='dialogue')
    def test_ruby_anchor_longest_one_pass_and_controls(self):
        rows=[self.row('a','[なつは,1]夏葉'),self.row('b','[夏葉]別人'),self.row('c','夏葉 別人')]
        known={'a':dict(text='나츠바짱 [나츠바] 나츠바',status='manual_translation'), 'b':dict(text='나츠바'), 'c':dict(text='나츠바')}
        out=apply(self.project,rows,known,self.cfg)
        self.assertEqual(out['a']['text'],'나츠하 [나츠바] 나츠하')
        self.assertEqual(out['b']['text'],'나츠바');self.assertEqual(out['c']['text'],'나츠바')
        self.assertEqual(known['a']['text'],'나츠바짱 [나츠바] 나츠바')
    def test_no_rule_file_or_other_engine_or_english_are_unchanged(self):
        rows=[self.row('a','夏葉')];known={'a':dict(text='나츠바')}
        for cfg in (dict(self.cfg,engine='rpgmaker'),dict(self.cfg,source_language='english')):
            self.assertIs(apply(self.project,rows,known,cfg),known)
        self.path.unlink();self.assertIs(apply(self.project,rows,known,self.cfg),known)
    def test_cache_manual_and_reviewed_precedence_without_writes(self):
        import translation
        rows=[self.row('a','夏葉'),self.row('b','夏葉')]
        cache=self.project/'data/translations.jsonl'
        original=(json.dumps(dict(id='a',source='夏葉',text='나츠바',status='manual_translation',model='manual',fingerprint='old'),ensure_ascii=False)+'\n').encode()
        cache.write_bytes(original)
        reviewed=self.project/'data/reviewed-translations.json';reviewed.write_text(json.dumps({'夏葉':'나츠바'},ensure_ascii=False),encoding='utf8')
        with patch.object(translation,'read_catalog',return_value=rows),patch('japanese_resume.annotate',side_effect=lambda project,rows,out,cfg:out):
            out=translation.cache(self.project,self.cfg)
        self.assertEqual(out['a']['text'],'나츠하');self.assertEqual(out['a']['status'],'manual_translation')
        self.assertEqual(out['b']['text'],'나츠하');self.assertEqual(cache.read_bytes(),original)
        self.assertEqual(json.loads(reviewed.read_text(encoding='utf8'))['夏葉'],'나츠바')
if __name__=='__main__':unittest.main()
