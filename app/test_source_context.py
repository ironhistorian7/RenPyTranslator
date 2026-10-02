import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine import save_json
from hy_backend import body, DEFAULT_MODEL
from source_context import SourceContext, token_estimate
from translation import _translate, catalog, fingerprint
from request_budget import BatchNeedsSplit


CFG = {'model':DEFAULT_MODEL, 'language':'korean', 'num_ctx':8192,
       'batch_size':8, 'parallel':1, 'endpoint':'http://127.0.0.1:1'}


def rows(count=15):
    return [dict(id=str(i), file='story.rpy', block='start_%08x'%i,
                 kind='dialogue', speaker='n', source='Original sentence %d.'%i)
            for i in range(count)]


class SourceContextTests(unittest.TestCase):
    def test_more_than_two_lines_order_cached_gap_and_no_truncation(self):
        items=rows()
        items[4]['source']='A complete sentence. '*20
        ctx=SourceContext(items).context([items[6],items[8]],CFG)
        passage=ctx['source_passage']
        self.assertGreater(len(passage),5)
        self.assertIn(items[4]['source'],[r.get('text') for r in passage])
        a=passage.index({'target_id':'0'});b=passage.index({'target_id':'1'})
        self.assertEqual(passage[a+1:b],[{'speaker':'n','text':items[7]['source']}])
        self.assertNotIn(items[6]['source'],json.dumps(ctx))

    def test_file_label_and_ui_boundaries(self):
        items=rows(4)
        items[2]['block']='other_00000002'
        items[3].update(kind='string',block='strings')
        index=SourceContext(items)
        self.assertTrue(index.same_scope(items[0],items[1]))
        self.assertFalse(index.same_scope(items[1],items[2]))
        self.assertEqual(index.context([items[2]],CFG),{})
        self.assertEqual(index.context([items[3]],CFG),{})
        items[1]['file']='another.rpy'
        self.assertFalse(SourceContext(items).same_scope(items[0],items[1]))

    def test_budget_and_oversized_neighbor_keep_whole_sentences(self):
        items=rows(40)
        for row in items:row['source']='A longer source sentence. '*8
        ctx=SourceContext(items).context([items[20]],CFG)
        passages=[r for r in ctx['source_passage'] if 'text' in r]
        self.assertGreater(sum(token_estimate(json.dumps(r,ensure_ascii=False)) for r in passages),1200)
        self.assertGreater(len(passages),24)
        small=SourceContext(items).context([items[20]],dict(CFG,num_ctx=2048))
        self.assertEqual([p['text'] for p in small['source_passage'] if 'text' in p],
                         [items[19]['source'],items[21]['source']])
        items[19]['source']='long '*10000
        ctx=SourceContext(items).context([items[20]],CFG)
        self.assertEqual(ctx['source_passage'],[{'speaker':'n','text':items[19]['source']},
                         {'target_id':'0'},{'speaker':'n','text':items[21]['source']}])
        self.assertTrue(ctx['_context_audit']['preselection_dropped'])

    def test_long_intervening_entry_blocks_farther_neighbors_only_on_that_side(self):
        items=rows(8);items[2]['source']='Long complete sentence. '*10000
        ctx=SourceContext(items).context([items[4]],CFG)
        texts=[p['text'] for p in ctx['source_passage'] if 'text' in p]
        self.assertIn(items[3]['source'],texts);self.assertIn(items[5]['source'],texts)
        self.assertNotIn(items[1]['source'],texts);self.assertNotIn(items[0]['source'],texts)
        self.assertNotIn(items[2]['source'],texts)

    def test_sentence_fragments_retain_adjacent_items_and_target_marker(self):
        items=rows(3);items[0]['source']='I love you';items[1]['source']='baby.'
        ctx=SourceContext(items).context([items[1]],CFG)
        self.assertEqual(ctx['source_passage'][:2],[{'speaker':'n','text':'I love you'},{'target_id':'0'}])

    def test_budget_split_requeues_without_retry_or_losing_rows(self):
        items=rows(4);calls=[]
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp);save_json(project/'data/catalog.json',items)
            def fake(batch,context,cfg,retry_note):
                calls.append((len(batch),retry_note))
                if len(batch)>1:
                    self.assertTrue(cfg['_allow_batch_split']);raise BatchNeedsSplit('synthetic overflow')
                self.assertFalse(cfg['_allow_batch_split'])
                return [dict(id=r['id'],source=r['source'],text='완성된 번역.',model=cfg['model'],fingerprint=fingerprint(cfg)) for r in batch],{'seconds':0}
            with patch('name_hints.ensure_metadata',return_value={'names':{}}),patch('name_translation.mapping',return_value={}),patch('translation.translate_batch',side_effect=fake):
                _translate(project,CFG)
            saved=[json.loads(line) for line in (project/'data/translations.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual([r['id'] for r in saved],['0','1','2','3'])
            self.assertTrue(all(not retry for _,retry in calls))
            self.assertEqual(json.loads((project/'data/failed-items.json').read_text()),[])

    def test_old_catalog_uses_template_locations_and_branch_scene_boundaries(self):
        script='label start:\n    n "Before"\n    if flag:\n        n "Branch A"\n        n "A next"\n    else:\n        n "Branch B"\n    n "After"\n    scene beach\n    n "Beach"\n'
        locations=[2,4,5,7,8,10]
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp);src=project/'data/recovered-scripts/story.rpy';src.parent.mkdir(parents=True);src.write_text(script)
            template=project/'data/templates/story.rpy';template.parent.mkdir(parents=True)
            lines=[];items=rows(len(locations))
            for row,number in zip(items,locations):
                lines.extend(['# game/story.rpy:%d'%number,'translate korean '+row['block']+':', '    # n "Hello"','    n ""'])
                row['line']=len(lines)-1
            template.write_text('\n'.join(lines))
            original=json.dumps(items)
            index=SourceContext(items,project)
            self.assertTrue(index.same_scope(items[1],items[2]))
            for a,b in [(0,1),(2,3),(3,4)]:self.assertFalse(index.same_scope(items[a],items[b]))
            self.assertTrue(index.same_scope(items[4],items[5]),'An image change alone is not a conversation boundary')
            self.assertEqual(json.dumps(items),original,'Do not migrate cached catalogs in place')

    def test_image_changes_leave_neighbors_available_across_translation_batches(self):
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp);src=project/'data/recovered-scripts/story.rpy';src.parent.mkdir(parents=True)
            items=rows(18);lines=['label start:']
            for i,row in enumerate(items):
                if i%3==0:lines.append('    scene camera_shot_%d with dissolve'%i)
                lines.append('    n "Original sentence %d."'%i)
                row.update(source_file='game/story.rpy',source_line=len(lines))
            src.write_text('\n'.join(lines))
            index=SourceContext(items,project)
            self.assertTrue(index.same_scope(items[0],items[-1]))
            ctx=index.context(items[3:11],CFG)
            neighbors=[p['text'] for p in ctx['source_passage'] if 'text' in p]
            self.assertIn(items[2]['source'],neighbors);self.assertIn(items[11]['source'],neighbors)

    def test_catalog_records_original_location_without_changing_id(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);target=p/'staging/game/tl/korean';target.mkdir(parents=True)
            (target/'story.rpy').write_text('# game/story.rpy:20\ntranslate korean start_12345678:\n    # n "Hello"\n    n ""\n',encoding='utf-8')
            result=catalog(p,CFG)
            self.assertEqual(result[0]['source_file'],'game/story.rpy')
            self.assertEqual(result[0]['source_line'],20)

    def test_hy_request_has_speakers_and_context_without_translating_background(self):
        request=body([{'id':'0','text':'Dear, come here.','speaker':'mother'}],
                     {'source_passage':[{'speaker':'child','text':'Mom!'}]},CFG,{},'')
        prompt=request['messages'][0]['content']
        self.assertIn('"target_speakers":{"0":"mother"}',prompt)
        self.assertIn('Mom!',prompt)
        self.assertIn('Source Text의 항목만',prompt)
        self.assertEqual(len(request['messages']),1)
        self.assertNotIn('<rpt000/>',prompt)

    def test_retry_keeps_context_and_completed_cache_is_never_retranslated(self):
        items=rows(4);calls=[]
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp);save_json(project/'data/catalog.json',items)
            completed=[dict(id=r['id'],source=r['source'],text='완료된 번역.',model=CFG['model'],fingerprint=fingerprint(CFG)) for r in items if r['id']!='1']
            cachefile=project/'data/translations.jsonl'
            before=''.join(json.dumps(r)+'\n' for r in completed)
            cachefile.write_text(before,encoding='utf-8')
            def fake(batch,context,cfg,retry_note):
                self.assertEqual([r['id'] for r in batch],['1'])
                calls.append(context)
                if len(calls)==1:raise ValueError('Incomplete model response')
                return [dict(id='1',source=items[1]['source'],text='새 번역.',model=cfg['model'],fingerprint=fingerprint(cfg))],{'seconds':0}
            with patch('name_hints.ensure_metadata',return_value={'names':{}}),patch('name_translation.mapping',return_value={}),patch('translation.translate_batch',side_effect=fake):
                _translate(project,CFG)
            self.assertEqual(calls[0],calls[1])
            self.assertIn('source_passage',calls[0])
            self.assertTrue(cachefile.read_text(encoding='utf-8').startswith(before))
            with patch('name_hints.ensure_metadata',return_value={'names':{}}),patch('name_translation.mapping',return_value={}),patch('translation.translate_batch') as model,patch('source_context.SourceContext') as context:
                _translate(project,CFG)
                model.assert_not_called();context.assert_not_called()


if __name__=='__main__':unittest.main()
