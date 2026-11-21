import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from rpgmaker_preprocessing import prepare, restore, validation_pair
from rpgmaker_tokens import protect, validate
from rpgmaker_output_policy import prepare_output, classify

CFG={'engine':'rpgmaker','source_language':'japanese'}
class RPGPreprocessingTests(unittest.TestCase):
    def roundtrip(self, source, translated):
        clean, plan=prepare(source)
        out, audit=restore(translated,plan)
        a,b=validation_pair(plan,out,audit)
        return clean,out,audit,validate(a,b)
    def test_whole_and_functional_codes(self):
        src=r'\C[3]\FS[28]Hello \V[1] \I[2]\.\C[0]'
        clean,out,audit,errors=self.roundtrip(src,r'안녕 \V[1] \I[2]\. ')
        self.assertEqual(clean,r'Hello \V[1] \I[2]\.')
        self.assertEqual(out,r'\C[3]\FS[28]안녕 \V[1] \I[2]\. \C[0]')
        self.assertEqual(errors,[]);self.assertEqual(audit['visual_codes_omitted'],0)
    def test_english_and_ambiguous_inline(self):
        clean,out,audit,errors=self.roundtrip(r'Please \C[2]keep this\C[0] here.', '여기에 남겨 주세요.')
        self.assertEqual(clean,'Please keep this here.');self.assertEqual(out,'여기에 남겨 주세요.')
        self.assertEqual(audit['visual_codes_omitted'],2);self.assertEqual(errors,[])
    def test_variable_anchor_moves(self):
        clean,out,audit,errors=self.roundtrip(r'Ask \C[2]\V[1]\C[0] about \V[2].',r'\V[2]에 대해 \V[1]에게 물어봐.')
        self.assertEqual(out,r'\V[2]에 대해 \C[2]\V[1]\C[0]에게 물어봐.');self.assertEqual(errors,[])
    def test_repeated_variable_not_guessed(self):
        clean,out,audit,errors=self.roundtrip(r'Ask \C[2]\V[1]\C[0] and \V[1].',r'\V[1]와 \V[1]에게 물어봐.')
        self.assertEqual(out,r'\V[1]와 \V[1]에게 물어봐.');self.assertEqual(audit['visual_codes_omitted'],2);self.assertEqual(errors,[])
    def test_unclosed_and_repeated(self):
        for src,expected in [(r'\C[1]\C[1]Hello',r'\C[1]\C[1]안녕'),(r'\{\{Hello\}\}',r'\{\{안녕\}\}')]:
            with self.subTest(src=src):
                clean,out,audit,errors=self.roundtrip(src,'안녕');self.assertEqual(out,expected);self.assertEqual(errors,[])
    def test_opaque_plugins_and_escaped_backslash(self):
        src=r'\Plugin[\C[2]] \\C[3] \PX[10] \N[1] \! \> text'
        clean,plan=prepare(src);self.assertEqual(clean,src);self.assertEqual(plan['source_style_codes'],0);self.assertTrue(protect(clean)[1])
    def test_persistent_validation_accepts_only_source_owned_visuals(self):
        source=r'Please \C[2]keep this\C[0] with \V[1].'
        self.assertEqual(validate(source,r'\V[1]와 함께 남겨 주세요.'),[])
        self.assertIn('RPG Maker control code mismatch',validate(source,r'\V[2]와 함께 남겨 주세요.'))
        self.assertIn('RPG Maker control code mismatch',validate(source,r'\C[9]\V[1]와 함께 남겨 주세요.'))
        self.assertIn('RPG Maker control code mismatch',validate(source,r'\C[2]\C[2]\V[1]와 함께 남겨 주세요.'))
    def test_non_reset_pairs_do_not_bind_a_variable(self):
        clean,out,audit,errors=self.roundtrip(r'Hello \C[2]\V[1]\FS[20] world',r'\V[1] 안녕')
        self.assertEqual(out,r'\V[1] 안녕');self.assertEqual(audit['visual_codes_omitted'],2)
    def test_generated_visual_only_removed_before_restoration(self):
        clean,plan=prepare(r'\C[2]Hello \V[1]\C[0]')
        target=r'\FS[32]안녕 \V[1] \{ \Plugin[\C[9]] <unknown> \!'
        out,audit=restore(target,plan)
        self.assertEqual(out,r'\C[2]안녕 \V[1]  \Plugin[\C[9]] <unknown> \!\C[0]')
        self.assertEqual(audit['model_visual_codes_removed'],2)
        self.assertIn('RPG Maker control code mismatch',validate(plan['source'],out))
    def test_style_only_source_roundtrips_exactly(self):
        source=r'\C[1]\FS[20]\{\}'
        clean,plan=prepare(source);self.assertEqual(clean,'')
        output,audit=restore('',plan)
        self.assertEqual(output,source);self.assertEqual(validate(source,output),[])
    def test_actual_cache_preserves_omissions_and_rejects_new_functional(self):
        from translation import cache, fingerprint
        source=r'Please \C[2]keep this\C[0] with \V[1].'
        rows=[{'id':id,'source':source} for id in ('ok','badvar','badplugin','badstyle')]
        targets=[r'\V[1]와 남겨 주세요.',r'\V[2]와 남겨 주세요.',
                 r'\V[1]와 \NewPlugin[3] 남겨 주세요.',r'\V[1]와 \C[9] 남겨 주세요.']
        cfg={'engine':'rpgmaker','source_language':'english','language':'korean',
             'model':'synthetic','_skip_name_review':True}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'data').mkdir()
            records=[dict(id=row['id'],source=source,text=target,model='synthetic',
                          fingerprint=fingerprint(cfg)) for row,target in zip(rows,targets)]
            path=root/'data'/'translations.jsonl'
            original=''.join(json.dumps(record,ensure_ascii=False)+'\n' for record in records)
            path.write_text(original,encoding='utf-8')
            with patch('translation.read_catalog',return_value=rows):result=cache(root,cfg)
            self.assertEqual(set(result),{'ok'});self.assertEqual(path.read_text(encoding='utf-8'),original)
            self.assertEqual(result['ok']['text'],targets[0])
    def test_small_tsu_punctuation_and_names(self):
        cfg=dict(CFG,_rpg_name_groups=(('ミッカ','미카'),));source='ミッカ「っ！」'
        out,details=prepare_output(source,'ミッカ「っ！」',cfg)
        self.assertEqual(out,'ミッカ「！」');self.assertEqual(classify(source,out,cfg)['kind'],'clean');self.assertEqual(validate(source,out,cfg),[])
        for source,target in [('っ','っ'),('っ！','っ'),('例っ！','っ！'),('っ！','あ！')]:
            with self.subTest(source=source,target=target):self.assertEqual(prepare_output(source,target,cfg)[0],target)
if __name__=='__main__':unittest.main()
