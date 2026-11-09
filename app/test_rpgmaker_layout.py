"""Layout regressions use invented dialogue, never expected game translations."""
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine import save_json
from rpgmaker_layout import repair, segments
from rpgmaker_tokens import protect, validate
from rpgmaker_plain_retry import plan, restore
from translation import cache, fingerprint, translate_batch

CFG={'engine':'rpgmaker','model':'rpt-hymt2-7b:q6_k','source_language':'english',
     'language':'korean','num_ctx':16384,'parallel':2,'batch_size':8,'endpoint':'http://unused.invalid'}
ERROR="Protected token mismatch: [] != [0, 1]; output='안녕'"


class LayoutTests(unittest.TestCase):
    def test_positioned_newline_is_protected_but_soft_newline_is_not(self):
        text,tokens=protect('hel\nlo\\.\n\\PX[90]world')
        self.assertIn('hel\nlo',text)
        self.assertIn('\n\\PX[90]',tokens)
        self.assertIn('RPG Maker control code mismatch',validate('Hello\n\\PX[90]world',r'안녕\PX[90]세계',CFG))

    def test_restore_by_position_occurrence_only_and_idempotently(self):
        source='\\PY[35]\\PX[100]First\\.\n\\PX[100]Second'
        broken=r'\PY[35]\PX[100]첫 문장\.\PX[100]다음 문장'
        fixed,changes=repair(source,broken)
        self.assertEqual(fixed,'\\PY[35]\\PX[100]첫 문장\\.\n\\PX[100]다음 문장')
        self.assertEqual(len(changes),1);self.assertEqual(repair(source,fixed),(fixed,[]))
        self.assertEqual(validate(source,fixed,CFG),[])
        # Never insert a line break before an unrelated or renumbered coordinate.
        changed=broken.replace('PX[100]다음','PX[200]다음')
        self.assertEqual(repair(source,changed),(changed,[]))

    def test_inline_coordinates_are_not_moved_or_forced_to_new_lines(self):
        source=r'Before\PX[50]After\PY[75]Below'
        self.assertEqual(segments(source),[{'prefix':'','source':'Before'},
            {'prefix':r'\PX[50]','source':'After'},{'prefix':r'\PY[75]','source':'Below'}])
        retry=plan(source,ERROR,CFG)
        result,audit=restore(retry,{'0':'앞','1':'뒤','2':'아래'})
        self.assertEqual(result,r'앞\PX[50]뒤\PY[75]아래')
        self.assertEqual(audit['policy'],'exact_position_boundaries; local_display_restoration')

    def test_formatting_before_coordinate_stays_on_the_new_line(self):
        source='First\\.\n\\C[2]\\PX[100]Second'
        target=r'첫 문장\.\C[2]\PX[100]다음 문장'
        fixed,_=repair(source,target)
        self.assertEqual(fixed,'첫 문장\\.\n\\C[2]\\PX[100]다음 문장')
        self.assertEqual(validate(source,fixed,CFG),[])
        self.assertIn('\n\\C[2]\\PX[100]',protect(source)[1])
        retry=plan(source,ERROR,CFG)
        self.assertEqual(restore(retry,{'0':'첫 문장','1':'다음 문장'})[0],fixed)

    def test_plain_retry_assembles_original_layout_from_separate_readable_items(self):
        source='\\PY[35]\\PX[100]First\\.\n\\PX[100]Second'
        row=dict(id='x',source=source,kind='dialogue',usage='dialogue')
        cfg=dict(CFG,_rpg_retry_errors={'x':ERROR})
        with patch('translation.request',return_value={'message':{'content':'[0] 첫 문장\n[1] 다음 문장'}}) as request,patch('hy_backend.runtime',side_effect=lambda cfg:nullcontext(cfg)):
            result,_=translate_batch([row],{},cfg)
        prompt=request.call_args.args[2]['messages'][0]['content']
        text=prompt.split('[Source Text]',1)[1].strip()
        self.assertEqual(text,'[0] First\n[1] Second')
        self.assertNotIn('RPT',text);self.assertNotIn('\\PX',text);self.assertNotIn('\\PY',text)
        self.assertIn('layout_reading',prompt)
        self.assertEqual(result[0]['text'],'\\PY[35]\\PX[100]첫 문장\\.\n\\PX[100]다음 문장')

    def test_mixed_rows_keep_response_ids_after_layout_expansion(self):
        rows=[dict(id='x',source='First\n\\PX[100]Second',kind='dialogue',usage='dialogue'),
              dict(id='y',source='Another',kind='dialogue',usage='dialogue')]
        cfg=dict(CFG,_rpg_retry_errors={'x':ERROR})
        with patch('translation.request',return_value={'message':{'content':'[0] 첫 문장\n[1] 다음 문장\n[2] 다른 문장'}}),patch('hy_backend.runtime',side_effect=lambda cfg:nullcontext(cfg)):
            result,_=translate_batch(rows,{},cfg)
        self.assertEqual(result[0]['text'],'첫 문장\n\\PX[100]다음 문장')
        self.assertEqual(result[1]['text'],'다른 문장')

    def test_cached_translation_repairs_layout_in_memory_without_model_or_cache_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp);source='First\n\\PX[100]Second'
            save_json(project/'data/catalog.json',[dict(id='x',source=source,kind='dialogue')])
            saved=dict(id='x',source=source,text=r'첫 문장\PX[100]다음 문장',model=CFG['model'],fingerprint=fingerprint(CFG))
            path=project/'data/translations.jsonl';raw=json.dumps(saved)+'\n';path.write_text(raw,encoding='utf-8')
            with patch('translation.request',side_effect=AssertionError('No model request')):
                known=cache(project,CFG)
            self.assertEqual(known['x']['text'],'첫 문장\n\\PX[100]다음 문장')
            self.assertEqual(path.read_text(encoding='utf-8'),raw)
            self.assertFalse((project/'data/rpg-invalid-cache.json').exists())


if __name__=='__main__':unittest.main()
