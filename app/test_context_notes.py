"""Small invented games only; no game, cloud API or local model execution."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from context_notes import ContextNotes, terms_from
from engine import manifest, save_json, verify_source
from hy_backend import body
from source_context import SourceContext, token_estimate
from translation import _translate, fingerprint, translate_batch


CFG = {'model':'fake', 'language':'korean', 'num_ctx':8192,
       'batch_size':8, 'parallel':1, 'endpoint':'http://localhost:1'}


def records():
    return [dict(id=str(i),file='chapters/story.rpy',source_file='game/chapters/story.rpy',
                 source_line=i+10,block='opening_%08x'%i,kind='dialogue',speaker='n',source=s)
            for i,s in enumerate(('A quiet morning begins.', 'Dear, come here.',
                                  'That was the last morning.', 'Now the storm arrives.',
                                  'Take shelter with Mira.', 'The storm has passed.'))]


GLOBAL = '# project\n## 주입용 공통 지침\n- 내레이션은 합니다체.\n## 관리용 근거\nDO NOT SEND EVIDENCE\n'
SCENES = '''# original
## 장면 ID: MORNING
적용 구간: label opening — start "A quiet morning…" ~ end "That was the last morning."
주입용 문맥:
- 어머니가 자녀에게 말한다. dear는 자녀를 부르는 애칭이다.
관리용 근거: DO NOT SEND MORNING EVIDENCE
## 장면 ID: STORM
적용 구간: "Now the storm arrives." ~ jump next
주입용 문맥:
- 폭풍이 시작되었다. Mira는 구조대원이다.
관리용 근거: DO NOT SEND STORM EVIDENCE
'''


class ContextNotesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.project = self.root/'project'
        self.source = self.root/'source'; self.source.mkdir()
        self.folder = self.source/'game/context'
        self.cfg = dict(CFG,source=str(self.source))

    def put(self, name, text):
        path = self.folder/name; path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(text,encoding='utf-8-sig'); return path

    def fixture(self):
        self.put('global.txt',GLOBAL)
        self.put('terms.txt','Mira | 미라 | 인명 | 구조대원\ncar | 차 | 일반명사 | 기각: 카(검토 메모)\nOscar | 오스카 | 인명 |\n')
        self.put('files/chapters/story.rpy.txt',SCENES)

    def test_missing_folder_is_quiet_and_identical_to_old_context(self):
        items = records()
        with redirect_stdout(io.StringIO()) as output:
            notes = ContextNotes(self.project,self.cfg,items)
        self.assertEqual(output.getvalue(),'')
        self.assertEqual(notes.context([items[1]],self.cfg),{})
        self.assertFalse((self.project/'data/context-report.json').exists())
        self.assertEqual(SourceContext(items).context([items[1]],CFG),
                         SourceContext(items,self.project,self.cfg).context([items[1]],self.cfg))

    def test_scene_anchors_metadata_exclusion_and_batch_boundaries(self):
        self.fixture(); items=records(); index=SourceContext(items,self.project,self.cfg)
        morning=index.context([items[1]],self.cfg)
        self.assertIn('자녀',morning['translation_guidance']['scene'])
        self.assertNotIn('폭풍',json.dumps(morning,ensure_ascii=False))
        self.assertNotIn('Now the storm',json.dumps(morning))
        self.assertNotIn('EVIDENCE',json.dumps(morning))
        self.assertTrue(index.same_scope(items[0],items[2]))
        self.assertFalse(index.same_scope(items[2],items[3]))
        storm=index.context([items[4]],self.cfg)['translation_guidance']
        self.assertIn('폭풍',storm['scene']); self.assertEqual(storm['terms'][0]['target'],'미라')
        self.assertEqual(len(storm['terms']),1)
        mixed=index.notes.context([items[1],items[4]],self.cfg)['translation_guidance']
        self.assertNotIn('scene',mixed)

    def test_matching_terms_use_word_boundaries_and_do_not_send_rejected_spellings(self):
        self.fixture(); items=records(); items[1]['source']='Oscar needs a car.'
        data=ContextNotes(self.project,self.cfg,items).context([items[1]],self.cfg)['translation_guidance']
        self.assertEqual({t['source'] for t in data['terms']},{'car','Oscar'})
        self.assertNotIn('기각',json.dumps(data,ensure_ascii=False))
        items[1]['source']='Oscar is here.'
        data=ContextNotes(self.project,self.cfg,items).context([items[1]],self.cfg)['translation_guidance']
        self.assertEqual([t['source'] for t in data['terms']],['Oscar'])

    def test_pipe_pairs_and_verbose_format(self):
        text='''Snorb / Ribbon | 스노브 / 리본 | 종 | 두 생물
Brillo / Brillos | 브릴로 | 종 | 단복수
원문: Francis Crick
권장 한국어 표기: 프랜시스 크릭
분류: 인물
의미와 적용 조건: 과학자
근거: Do not send this
'''
        entries=[entry for _,entry in terms_from(text)]
        self.assertEqual([(e['source'],e['target']) for e in entries],
                         [('Snorb','스노브'),('Ribbon','리본'),('Brillo','브릴로'),('Brillos','브릴로'),('Francis Crick','프랜시스 크릭')])
        self.assertNotIn('Do not send',json.dumps(entries))

    def test_unmatched_or_overlapping_ranges_fall_back_without_stopping(self):
        self.fixture(); items=records()
        self.put('files/chapters/story.rpy.txt',SCENES.replace('A quiet morning…','Not a real anchor.'))
        notes=ContextNotes(self.project,self.cfg,items)
        self.assertNotIn('scene',notes.context([items[1]],self.cfg)['translation_guidance'])
        self.assertTrue(any('unmatched' in s for s in notes.report['issues']))
        self.put('files/chapters/story.rpy.txt',SCENES.replace('That was the last morning.','The storm has passed.'))
        notes=ContextNotes(self.project,self.cfg,items)
        self.assertNotIn('scene',notes.context([items[4]],self.cfg)['translation_guidance'])

    def test_plain_global_inline_ui_and_file_names_preserve_subdirectories(self):
        self.put('global.txt','# ignore\nUse a consistent tone.\n관리용 근거: omit\n')
        self.put('files/ui/screens.txt','## 장면 ID: MENU\n적용 구간: screen menu\n주입용 문맥: 짧은 메뉴 문구.\n관리용 근거: omit\n')
        self.put('files/screens.txt','Wrong file, do not select by basename.')
        item=dict(id='ui',file='ui/screens.rpy',kind='string',block='strings',source='Settings')
        data=SourceContext([item],self.project,self.cfg).context([item],self.cfg)['translation_guidance']
        self.assertEqual(data['global'],'Use a consistent tone.')
        self.assertEqual(data['scene'],'짧은 메뉴 문구.')

    def test_old_catalog_source_locations_are_used_without_changing_catalog(self):
        self.fixture(); item=records()[0]; item.pop('source_file'); item.pop('source_line')
        item.update(file='merged.rpy',line=3)
        path=self.project/'data/templates/merged.rpy'; path.parent.mkdir(parents=True)
        path.write_text('# game/chapters/story.rpy:10\ntranslate korean opening_00000000:\n    # n "Hello"\n    n ""\n')
        self.put('files/chapters/story.rpy.txt','## 장면 ID: ONE\n적용 구간: label opening 전체\n주입용 문맥: 옛 카탈로그도 사용.\n관리용 근거: omit\n')
        before=dict(item)
        ctx=SourceContext([item],self.project,self.cfg).context([item],self.cfg)
        self.assertIn('옛 카탈로그',ctx['translation_guidance']['scene'])
        self.assertEqual(item,before)

    def test_budget_whole_lines_and_shared_nearby_passage_limit(self):
        self.fixture(); self.put('global.txt','\n'.join(['- 공통 지침을 짧게 사용합니다.']*200))
        items=records(); index=SourceContext(items,self.project,self.cfg)
        data=index.context([items[1]],self.cfg)
        self.assertEqual(data['translation_guidance']['global'],'\n'.join(['- 공통 지침을 짧게 사용합니다.']*200))
        self.assertGreater(token_estimate(json.dumps(data['translation_guidance'],ensure_ascii=False)),900)
        self.assertTrue(data['translation_guidance']['global'].endswith('사용합니다.'))
        self.assertLessEqual(token_estimate(json.dumps(data,ensure_ascii=False)),6500)
        small=index.context([items[1]],dict(self.cfg,num_ctx=2048))
        self.assertEqual(small['translation_guidance'],data['translation_guidance'])

    def test_live_source_is_authoritative_and_offline_stage_is_fallback(self):
        stage=self.project/'staging/game/context';stage.mkdir(parents=True)
        (stage/'global.txt').write_text('Prepared note.',encoding='utf-8')
        self.assertEqual(ContextNotes(self.project,self.cfg,records()).context(records()[:1],CFG),{})
        cfg=dict(self.cfg,source=str(self.root/'moved-away'))
        data=ContextNotes(self.project,cfg,records()).context(records()[:1],CFG)
        self.assertEqual(data['translation_guidance']['global'],'Prepared note.')
        self.put('global.txt','Current note.')
        data=ContextNotes(self.project,self.cfg,records()).context(records()[:1],CFG)
        self.assertEqual(data['translation_guidance']['global'],'Current note.')

    def test_unreadable_note_does_not_fail_translation(self):
        self.fixture(); (self.folder/'global.txt').write_bytes(b'\xff\xfe\xff')
        notes=ContextNotes(self.project,self.cfg,records())
        self.assertTrue(notes.report['issues'])
        self.assertIn('scene',notes.context(records()[:1],self.cfg)['translation_guidance'])

    def test_standalone_repair_reads_prepared_context_only(self):
        self.fixture()
        stage=self.project/'staging/game/context';stage.mkdir(parents=True)
        (stage/'global.txt').write_text('Prepared repair note.',encoding='utf-8')
        original=Path.is_dir
        def checked(path):
            if path==self.source: raise AssertionError('Standalone repair must not inspect source')
            return original(path)
        with patch.object(Path,'is_dir',checked):
            notes=ContextNotes(self.project,dict(self.cfg,_repair=True),records())
        self.assertEqual(notes.context(records()[:1],CFG)['translation_guidance']['global'],'Prepared repair note.')

    def test_source_verification_allows_txt_changes_but_not_game_code_changes(self):
        self.fixture(); script=self.source/'game/story.rpy';script.write_text('original')
        save_json(self.project/'data/source-manifest.json',manifest(self.source))
        before=(self.project/'data/source-manifest.json').read_bytes()
        self.put('global.txt','Changed note.');self.put('new.txt','New note.')
        self.assertTrue(verify_source(self.project,self.cfg)['unchanged'])
        self.assertEqual((self.project/'data/source-manifest.json').read_bytes(),before)
        self.put('executable.rpy','changed script')
        with self.assertRaisesRegex(RuntimeError,'Original source changed'):verify_source(self.project,self.cfg)

    def test_context_survives_retry_and_completed_cache_is_preserved(self):
        self.fixture(); items=records();save_json(self.project/'data/catalog.json',items)
        completed=[dict(r,text='완료된 번역.',model=CFG['model'],fingerprint=fingerprint(CFG)) for r in items if r['id']!='1']
        cachefile=self.project/'data/translations.jsonl'
        before=''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in completed)
        cachefile.write_text(before,encoding='utf-8'); calls=[]
        def fake(batch,ctx,cfg,retry_note):
            self.assertEqual([r['id'] for r in batch],['1']);calls.append(ctx)
            if len(calls)==1: raise ValueError('Retry once')
            return [dict(id='1',source=items[1]['source'],text='이리 오렴.',model=cfg['model'],fingerprint=fingerprint(cfg))],{'seconds':0}
        with patch('name_hints.ensure_metadata',return_value={'names':{}}),patch('name_translation.mapping',return_value={}),patch('translation.translate_batch',side_effect=fake):
            _translate(self.project,self.cfg)
        self.assertEqual(calls[0],calls[1]);self.assertIn('translation_guidance',calls[0])
        self.assertTrue(cachefile.read_text(encoding='utf-8').startswith(before))
        self.put('global.txt','Changed after completion.')
        with patch('name_hints.ensure_metadata',return_value={'names':{}}),patch('name_translation.mapping',return_value={}),patch('translation.translate_batch') as api:
            _translate(self.project,self.cfg)
        api.assert_not_called()

    def test_both_backends_receive_reference_only_guidance(self):
        context={'translation_guidance':{'global':'합니다체','scene':'어머니가 자녀를 부른다.'}}
        prompt=body([{'id':'0','text':'Dear, come here.'}],context,CFG,{},'')['messages'][0]['content']
        self.assertIn('어머니가 자녀',prompt);self.assertIn('문맥 설명 자체를 번역하거나',prompt)
        with patch('translation.request',return_value={'message':{'content':'{"0":"이리 오렴."}'}}) as request:
            translate_batch([records()[1]],context,CFG)
        messages=request.call_args.args[2]['messages']
        self.assertIn('reference-only',messages[0]['content'])
        self.assertEqual(json.loads(messages[1]['content'])['context_do_not_translate'],context)


if __name__ == '__main__': unittest.main()
