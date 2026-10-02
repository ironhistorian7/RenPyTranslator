"""Exercise the existing run pipeline twice on three invented entries, no engine/model."""
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine import save_json,manifest,verify_source
from name_translation import edit_digest
from translation import catalog, cache, translate
from translate_game import main
from automatic import settings


class RunResumeTests(unittest.TestCase):
    def test_run_reuses_complete_cache_repairs_only_failures_and_packages_twice(self):
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp)/'workspace'
            game=project/'staging/game'
            target=game/'tl/korean/story.rpy'
            target.parent.mkdir(parents=True)
            (game/'script.rpy').write_text('define d = Character("Donny")\n',encoding='utf-8')
            sources=['Donny already brought the package home.',
                     'I need Donny at 100/% to help today.',
                     'Please tell Donny [secret] before we leave.']
            template='\n'.join('translate korean scene_%d:\n    # d %s\n    d ""\n' %
                               (i,json.dumps(source)) for i,source in enumerate(sources))
            target.write_text(template,encoding='utf-8')
            cfg={'source':str(Path(temp)/'invented-game'),'language':'korean','model':'fake',
                 'endpoint':'http://localhost','batch_size':8,'num_ctx':8192}
            source=Path(cfg['source']);(source/'game').mkdir(parents=True)
            (source/'game/scripts.rpa').write_bytes(b'invented original archive')
            save_json(project/'data/source-manifest.json',manifest(source))
            original_manifest=(project/'data/source-manifest.json').read_bytes()
            # Applying this tool's patch and then playing must not block normal run.
            (source/'game/tl/korean').mkdir(parents=True)
            (source/'game/tl/korean/story.rpy').write_text('# applied patch',encoding='utf-8')
            (source/'game/cache').mkdir();(source/'game/cache/bytecode.rpyb').write_bytes(b'runtime cache')
            (source/'traceback.txt').write_text('previous display error',encoding='utf-8')
            rows=catalog(project,cfg)
            # External mods/new story bytes must not block or contaminate a
            # resume of the already prepared project.
            (source/'game/scripts.rpa').write_bytes(b'new external game version')
            (source/'game/external-mod.rpa').write_bytes(b'unrelated mod')
            with self.assertRaisesRegex(RuntimeError,'Original source changed'):
                verify_source(project,cfg)
            external_report=(project/'data/source-verification.json').read_bytes()
            save_json(project/'data/static-screen-literals.json',[])
            save_json(project/'project.json',cfg)
            names={'Donny':'도니'}
            save_json(project/'data/name-transliterations.json',{'names':names,'attempted':['Donny']})
            # Simulate a previous completed translation run, including stale fingerprints.
            entries=[dict(row,text='도니는 이미 꾸러미를 집에 가져왔다.' if i==0 else row['source'],
                          model='fake',fingerprint='previous-settings',
                          status='translated' if i==0 else 'source_fallback',error='old failure')
                     for i,row in enumerate(rows)]
            cachefile=project/'data/translations.jsonl'
            cachefile.write_text(''.join(json.dumps(e,ensure_ascii=False)+'\n' for e in entries),encoding='utf-8')
            original_cache=cachefile.read_bytes()
            save_json(project/'data/failed-items.json',[{'row':row,'error':'old failure'} for row in rows[1:]])
            # Old persisted no-op name correction must work without manual cache edits.
            failed=rows[2]
            save_json(project/'data/name-edits.json',{failed['id']:{'source':failed['source'],
                'before':failed['source'],'after':failed['source'],
                'digest':edit_digest(names,failed['source']),'decided':True}})
            sentinel=target.parent/'keep-existing.txt'
            sentinel.write_text('keep',encoding='utf-8')
            requests=[]
            def request(endpoint,route,body):
                self.assertEqual(cachefile.read_bytes(),original_cache)
                self.assertTrue(target.exists())
                self.assertEqual(sentinel.read_text(encoding='utf-8'),'keep')
                items=json.loads(body['messages'][1]['content'])['items']
                self.assertEqual(len(items),1)
                text=items[0]['text'];requests.append(text)
                self.assertNotEqual(text,sources[0])
                result='오늘 도니의 도움이 100% 필요해.' if '100/%' in text else '도니에게 말해 줘.'
                return {'message':{'content':json.dumps({'0':result},ensure_ascii=False)}}
            command=['tasks','--tasks','run','--project',str(project),'--output',str(Path(temp)/'result')]
            name_requests=[]
            def name_request(endpoint,route,body):
                prompt=body['messages'][0]['content'];name_requests.append(prompt)
                self.assertIn('표시 명칭을 분류',prompt)
                return {'message':{'content':'{"0":"name"}'}}
            with (patch('translate_game.resolve_project',return_value=(project,cfg)),
                  patch('engine.engine_command'),patch('translation.engine_command'),
                  patch('translate_game.prepare',side_effect=AssertionError('Must reuse existing preparation')),
                  patch('translate_game.catalog',side_effect=AssertionError('Must reuse existing catalog')),
                  patch('engine.manifest',side_effect=AssertionError('Resume must not scan the source')),
                  patch('hy_backend.runtime',side_effect=lambda active:nullcontext(active)),
                  patch('name_translation.request',side_effect=name_request),
                  patch('translation.request',side_effect=request)):
                main(command)
                self.assertEqual(len(requests),3) # one recovery + two bounded failed attempts
                self.assertEqual(sum('100/%' in item for item in requests),1)
                first_output=target.read_bytes()
                # GUI source selection resolves this same project, then runs
                # through the same snapshot validation and packaging path.
                main(['tasks','--tasks','run','--source',str(source),'--output',str(Path(temp)/'result')])
            # Only the still-failed item is retried on the second normal run.
            self.assertEqual(len(requests),5)
            self.assertEqual(len(name_requests),1) # one legacy identity migration, no fallback name edits
            self.assertEqual(sum('100/%' in item for item in requests),1)
            self.assertEqual(cachefile.read_bytes(),original_cache)
            self.assertEqual((project/'data/source-manifest.json').read_bytes(),original_manifest)
            self.assertEqual((project/'data/source-verification.json').read_bytes(),external_report)
            self.assertFalse((game/'external-mod.rpa').exists())
            self.assertEqual(target.read_bytes(),first_output)
            self.assertEqual(sentinel.read_text(encoding='utf-8'),'keep')
            known=cache(project,settings(project,cfg))
            self.assertEqual(known[rows[0]['id']]['text'],entries[0]['text'])
            self.assertEqual(known[rows[1]['id']]['status'],'failure_recovered')
            self.assertEqual(known[rows[2]['id']]['status'],'source_fallback')
            report=json.loads((project/'output/build-report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['source_fallbacks'],1)
            self.assertEqual(report['source_verification']['scope'],'prepared project snapshot')
            self.assertFalse(report['source_verification']['original_source_accessed'])
            self.assertTrue(list((Path(temp)/'result').rglob('*.zip')))

    def test_partial_translation_resume_only_requests_missing_rows_and_appends(self):
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp)
            rows=[dict(id=str(i),kind='dialogue',source=source,file='story.rpy')
                  for i,source in enumerate(('Already completed before cancellation.', 'One new line to translate.'))]
            save_json(project/'data/catalog.json',rows)
            cfg=settings(project,{'model':'fake','language':'korean','endpoint':'http://localhost'})
            old=dict(rows[0],text='취소 전에 이미 완료된 문장입니다.',model='fake',fingerprint='old-settings')
            cachefile=project/'data/translations.jsonl'
            cachefile.write_text(json.dumps(old,ensure_ascii=False)+'\n',encoding='utf-8')
            before=cachefile.read_bytes()
            def request(endpoint,route,body):
                items=json.loads(body['messages'][1]['content'])['items']
                self.assertEqual([i['text'] for i in items],[rows[1]['source']])
                return {'message':{'content':'{"0":"번역할 새 문장 하나입니다."}'}}
            with patch('hy_backend.runtime',side_effect=lambda active:nullcontext(active)),patch('translation.request',side_effect=request) as api:
                translate(project,cfg)
            self.assertEqual(api.call_count,1)
            self.assertTrue(cachefile.read_bytes().startswith(before))
            self.assertEqual(len(cachefile.read_text(encoding='utf-8').splitlines()),2)
            self.assertEqual(cache(project,cfg)['0']['text'],old['text'])


if __name__=='__main__':unittest.main()
