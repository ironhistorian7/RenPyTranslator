"""Synthetic CPU-only project checks; no game, model or external assets."""
import json
import hashlib
import sys
import shutil
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kirikiri_containers import write_xp3, DirectorySource, XP3Archive
from kirikiri_project import prepare, render, static_check, KiriError, _rows
from kirikiri_psb import PsbDocument, _pack_array, dumps, loads
from translation import cache, fingerprint


def psb(root, names):
    offsets=[0]; tree=[0]; children={}; nextblock=1
    for name in names:
        node=0
        for byte in name.encode():
            if (node,byte) not in children:
                if not offsets[node]:
                    offsets[node]=nextblock; nextblock+=256
                    if len(tree)<nextblock:
                        tree.extend([0]*(nextblock-len(tree))); offsets.extend([0]*(nextblock-len(offsets)))
                idx=offsets[node]+byte; tree[idx]=node; children[node,byte]=idx
            node=children[node,byte]
        children['terminal',name]=node
    indexes=[]
    for name in names:
        indexes.append(len(tree)); tree.append(children['terminal',name]); offsets.append(0)
    return dumps(PsbDocument(root,names,_pack_array(offsets)+_pack_array(tree)+_pack_array(indexes),[]))


class ProjectTests(unittest.TestCase):
    def test_offline_update_and_conflict(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); game=root/'game'; game.mkdir(); (game/'game.exe').touch()
            write_xp3(game/'data.xp3',{'main.ks':b'hello\n','Config.tjs':b';userFace = global.SystemDefaultFontFace;'})
            (game/'context').mkdir(); (game/'context'/'global.txt').write_text('root',encoding='utf-8')
            (game/'game'/'context').mkdir(parents=True); (game/'game'/'context'/'global.txt').write_text('game',encoding='utf-8')
            (game/'game'/'context'/'not-text.png').write_bytes(b'ignore')
            project=root/'project'; cfg={'source':str(game),'source_language':'english','language':'korean','show_reference':True}
            prepare(project,cfg)
            self.assertEqual((project/'data/kirikiri-context/global.txt').read_text(encoding='utf-8'),'game')
            self.assertFalse((project/'data/kirikiri-context/not-text.png').exists())
            rows=json.loads((project/'data/catalog.json').read_text(encoding='utf-8')); uid=rows[0]['id']
            shutil.rmtree(game)
            patch=render(project,cfg,rows=rows,known={uid:{'text':'안녕'}})
            self.assertEqual(static_check(project)['status'],'passed')
            self.assertEqual(patch,render(project,cfg,rows=rows,known={uid:{'text':'안녕하세요'}}))
            self.assertEqual(len(list(patch.parent.glob('*.xp3'))),1)
            self.assertEqual(static_check(project)['status'],'passed')
            guide=(patch.parent/'APPLY.txt').read_text(encoding='utf-8'); self.assertIn(patch.name,guide); self.assertIn('삭제하지',guide)
            patch.write_bytes(b'external change')
            with self.assertRaises(KiriError) as error: render(project,cfg,rows=rows,known={uid:{'text':'다시'}})
            self.assertEqual(error.exception.error_code,'kirikiri_patch_conflict')

    def test_scn_shapes(self):
        names=['scenes','texts']
        with self.assertRaises(KiriError): _rows(psb({'scenes':[{'texts':[['bad']]}]},names),'bad.scn')
        with self.assertRaises(KiriError): _rows(psb({'texts':[]},names),'unknown.scn')
        self.assertEqual(_rows(psb({'list':[], 'map':{}}, ['list','map']),'helper.scn'), [])

    def test_scn_cached_translation_without_saved_name_review(self):
        content={'scenes':[{'texts':[['original-id','名前','こんにちは',[],0,{}]]}]}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            game=root/'game'; game.mkdir(); (game/'game.exe').touch()
            write_xp3(game/'data.xp3',{'main.scn':psb(content,['scenes','texts'])})
            project=root/'project'
            cfg={'source':str(game),'engine':'kirikiri','source_language':'japanese','language':'korean','model':'fixture'}
            prepare(project,cfg)
            rows=json.loads((project/'data/catalog.json').read_text(encoding='utf-8'))
            self.assertTrue(all(isinstance(row['location'],list) for row in rows))
            self.assertEqual(cache(project,cfg),{})
            row=next(row for row in rows if row['usage']=='dialogue')
            values={'名前':'이름','こんにちは':'안녕하세요'}
            records=[dict(id=r['id'],source=r['source'],text=values[r['source']],model='fixture',
                          fingerprint=fingerprint(cfg)) for r in rows]
            path=project/'data/translations.jsonl'
            original=''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records)
            path.write_text(original,encoding='utf-8')
            known=cache(project,cfg)
            self.assertEqual(known[row['id']]['text'],'안녕하세요')
            self.assertEqual(path.read_text(encoding='utf-8'),original)
            self.assertIsNotNone(render(project,cfg,rows=rows,known=known))
            self.assertEqual(static_check(project)['status'],'passed')

    def test_scn_static_check_rejects_changed_control_field(self):
        names=['scenes','texts']
        content={'scenes':[{'texts':[['original-id','Name','Hello',[],0,{}]]}]}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); game=root/'game'; game.mkdir(); (game/'game.exe').touch()
            write_xp3(game/'data.xp3',{'main.scn':psb(content,names)})
            project=root/'project'; cfg={'source':str(game),'source_language':'english','language':'korean','show_reference':True}
            prepare(project,cfg); rows=json.loads((project/'data/catalog.json').read_text(encoding='utf-8'))
            known={r['id']:{'text':{'Name':'이름','Hello':'안녕'}[r['source']]} for r in rows}
            patch=render(project,cfg,rows=rows,known=known)
            self.assertEqual(static_check(project)['status'],'passed')
            with DirectorySource(patch.parent) as source:
                doc=loads(XP3Archive(source,patch.name).read('main.scn'))
            doc.root['scenes'][0]['texts'][0][0]='altered-id'
            replacement=patch.parent/'replacement.tmp'; write_xp3(replacement,{'main.scn':dumps(doc)})
            replacement.replace(patch)
            reportfile=project/'data/kirikiri-output.json'; report=json.loads(reportfile.read_text(encoding='utf-8'))
            report['sha256']=hashlib.sha256(patch.read_bytes()).hexdigest()
            reportfile.write_text(json.dumps(report),encoding='utf-8')
            self.assertEqual(static_check(project)['status'],'failed')

    def test_no_changes_is_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); game=root/'game'; game.mkdir(); (game/'game.exe').touch()
            write_xp3(game/'data.xp3',{'main.ks':b'hello\n'})
            project=root/'project'; cfg={'source':str(game),'source_language':'english','language':'korean'}
            prepare(project,cfg); rows=json.loads((project/'data/catalog.json').read_text(encoding='utf-8'))
            self.assertIsNone(render(project,cfg,rows=rows,known={rows[0]['id']:{'text':'hello'}}))
            result=static_check(project); self.assertEqual(result['status'],'passed'); self.assertTrue(result['unchanged'])

if __name__ == '__main__': unittest.main()
