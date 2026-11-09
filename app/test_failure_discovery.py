"""Failure discovery/overlay regressions using invented temporary data only."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from engine import save_json
from failed_repair import run
from translation import cache, fingerprint


ABSENT = object()


class FailureDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.stack=contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        (self.root/'data').mkdir()
        (self.root/'output').mkdir()
        self.cfg={'model':'synthetic-failure-discovery','language':'korean',
                  'source_language':'english','_reuse_completed':True,'_fix':'failed',
                  'endpoint':'http://127.0.0.1:1','source':str(self.root/'no-original-game')}
        self.stack.enter_context(patch('translation.request',side_effect=AssertionError('No network/model request permitted')))
        self.stack.enter_context(patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('No network permitted')))
        self.stack.enter_context(patch('subprocess.Popen',side_effect=AssertionError('No child process permitted')))
        self.stack.enter_context(patch('hy_backend.is_hy',return_value=True))
        self.runtime=self.stack.enter_context(patch('hy_backend.runtime',side_effect=lambda cfg:contextlib.nullcontext(cfg)))
        self.stack.enter_context(patch('model_runtime.model_session',side_effect=lambda:contextlib.nullcontext()))
        self.stack.enter_context(patch('failed_repair.display_metadata',return_value={'names':{}}))
        self.stack.enter_context(patch('name_translation.mapping',return_value={}))
        self.stack.enter_context(patch('source_context.SourceContext',
            side_effect=lambda *_args,**_kwargs:SimpleNamespace(context=lambda *_a,**_k:{})))
        self.translate=self.stack.enter_context(patch('failed_repair.translate_batch',
            side_effect=AssertionError('Unexpected translation candidate')))

    def row(self,uid,source,kind='dialogue',usage=None):
        result={'id':uid,'source':source,'kind':kind,'file':'invented.rpy','block':'fixture_'+uid}
        if usage is not None:result['usage']=usage
        return result

    def entry(self,row,text=None,**extra):
        return dict(row,text=row['source'] if text is None else text,
                    model=self.cfg['model'],fingerprint=fingerprint(self.cfg),**extra)

    def saved_failure(self,row,text):
        return self.entry(row,status='source_fallback',
            error='Previously rejected output; output='+repr(text))

    def fixture(self,rows,entries,index=ABSENT,policy=None,recovered=None,reviewed=None):
        save_json(self.root/'data/catalog.json',rows)
        save_json(self.root/'data/ui-policy.json',policy or {'menu':{},'choices':[]})
        raw=''.join(json.dumps(item,ensure_ascii=False)+'\n' for item in entries).encode('utf-8')
        (self.root/'data/translations.jsonl').write_bytes(raw)
        if index is not ABSENT:save_json(self.root/'data/failed-items.json',index)
        if recovered is not None:save_json(self.root/'data/recovered-translations.json',recovered)
        if reviewed is not None:save_json(self.root/'data/reviewed-translations.json',reviewed)
        return raw

    def repair(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return run(self.root,self.cfg)

    def expect_raw(self,before):
        self.assertEqual((self.root/'data/translations.jsonl').read_bytes(),before)

    def fake_translations(self,expected):
        seen=[]
        def fake(batch,_context,cfg,*_args,**_kwargs):
            self.assertEqual(len(batch),1,'Discovery repairs only one selected item per request')
            row=batch[0];uid=row['id']
            self.assertIn(uid,expected,'Unrelated catalog content must never reach the model')
            seen.append(uid)
            return ([dict(id=uid,source=row['source'],text=expected[uid],
                          model=cfg['model'],fingerprint=fingerprint(cfg))],{'seconds':0,'items':1})
        self.translate.side_effect=fake
        return seen

    def test_empty_index_reapplies_two_valid_saved_recoveries_without_model(self):
        rows=[self.row('first','Please come here.'),self.row('second','We can leave.')]
        entries=[self.entry(row,status='source_fallback',error='Missing output ID') for row in rows]
        targets={'first':'이리 와 주세요.','second':'이제 가도 돼.'}
        recovered={row['id']:{'source':row['source'],'before':row['source'],
                   'text':targets[row['id']],'method':'recovered_saved'} for row in rows}
        raw=self.fixture(rows,entries,index=[],recovered=recovered)
        selected,report=self.repair()
        self.assertEqual(selected,set(targets))
        self.assertEqual(report['model_calls'],0)
        self.runtime.assert_not_called();self.translate.assert_not_called()
        self.assertEqual({uid:cache(self.root,self.cfg)[uid]['text'] for uid in targets},targets)
        self.expect_raw(raw)

    def test_missing_index_discovers_fallback_and_recovers_its_saved_output(self):
        row=self.row('saved','Hello there.')
        raw=self.fixture([row],[self.saved_failure(row,'안녕하세요.')])
        selected,report=self.repair()
        self.assertEqual(selected,{'saved'})
        self.assertEqual(report['model_calls'],0)
        self.assertEqual(report['recovered_saved'],1)
        self.assertEqual(cache(self.root,self.cfg)['saved']['text'],'안녕하세요.')
        self.runtime.assert_not_called();self.translate.assert_not_called()
        self.expect_raw(raw)

    def test_korean_font_filename_is_not_a_korean_translation(self):
        from failed_repair import saved_output
        source='{font=한글.ttf}We should go.{/font}'
        error='Old response; output='+repr('<rpt000/>We should go.<rpt001/>')
        self.assertIsNone(saved_output(source,error,self.cfg))

    def test_saved_preprocessed_numbering_uses_metadata_without_a_misleading_error_prefix(self):
        row=self.row('metadata','Ask {i}[reader]{/i} about {b}tomorrow{/b}.')
        entry=self.saved_failure(row,'내일 <rpt000/>에게 물어봐.')
        entry['input_format']='renpy_visual_stripped_v1'
        raw=self.fixture([row],[entry],index=[])
        selected,report=self.repair()
        self.assertEqual(selected,{'metadata'})
        self.assertEqual(report['recovered_saved'],1)
        self.assertEqual(report['model_calls'],0)
        self.assertEqual(cache(self.root,self.cfg)['metadata']['text'],'내일 {i}[reader]{/i}에게 물어봐.')
        self.runtime.assert_not_called();self.translate.assert_not_called()
        self.expect_raw(raw)

    def test_legacy_recovery_with_english_body_is_discovered_again(self):
        row=self.row('legacy','{font=한글.ttf}We should go.{/font}')
        raw=self.fixture([row],[self.entry(row)],index=[],recovered={row['id']:{
            'source':row['source'],'before':row['source'],'text':row['source'],'method':'retranslated'}})
        target='{font=한글.ttf}가야 해.{/font}'
        self.fake_translations({row['id']:target})
        selected,report=self.repair()
        self.assertEqual(selected,{row['id']})
        self.assertEqual(report['model_calls'],1)
        self.assertEqual(cache(self.root,self.cfg)[row['id']]['text'],target)
        _,again=self.repair()
        self.assertEqual(again['model_calls'],0)
        self.expect_raw(raw)

    def test_model_english_with_korean_tag_metadata_stays_unresolved(self):
        row=self.row('badbody','{font=한글.ttf}We should go.{/font}')
        raw=self.fixture([row],[self.entry(row)],index=[])
        self.fake_translations({row['id']:row['source']})
        selected,report=self.repair()
        self.assertFalse(selected)
        self.assertEqual(report['remaining'],1)
        self.assertEqual(report['model_calls'],2)
        failure=json.loads((self.root/'data/failed-items.json').read_text(encoding='utf-8'))
        self.assertEqual(failure[0]['row']['id'],row['id'])
        self.expect_raw(raw)

    def test_changed_english_legacy_recovery_is_not_counted_as_korean(self):
        row=self.row('changedbody','{font=한글.ttf}Hello there.{/font}')
        old='{font=한글.ttf}Hi there.{/font}'
        raw=self.fixture([row],[self.entry(row)],index=[],recovered={row['id']:{
            'source':row['source'],'before':row['source'],'text':old,'method':'retranslated'}})
        target='{font=한글.ttf}안녕하세요.{/font}'
        self.fake_translations({row['id']:target})
        selected,report=self.repair()
        self.assertEqual(selected,{row['id']})
        self.assertEqual(report['model_calls'],1)
        self.assertEqual(cache(self.root,self.cfg)[row['id']]['text'],target)
        _,again=self.repair()
        self.assertEqual(again['model_calls'],0)
        self.expect_raw(raw)

    def test_success_cache_with_empty_link_label_is_a_repair_candidate(self):
        row=self.row('linklabel','Click {a=destination}this link{/a} now.')
        raw=self.fixture([row],[self.entry(row,text='지금 {a=destination}{/a}를 누르세요.')],index=[])
        target='지금 {a=destination}이 링크{/a}를 누르세요.'
        self.fake_translations({row['id']:target})
        selected,report=self.repair()
        self.assertEqual(selected,{row['id']})
        self.assertEqual(report['model_calls'],1)
        self.assertEqual(cache(self.root,self.cfg)[row['id']]['text'],target)
        self.expect_raw(raw)

    def test_index_and_unindexed_cache_failure_are_unioned(self):
        rows=[self.row('indexed','We should wait.'),self.row('unindexed','Please sit down.')]
        targets={'indexed':'기다리는 게 좋겠어.','unindexed':'앉아 주세요.'}
        entries=[self.saved_failure(row,targets[row['id']]) for row in rows]
        raw=self.fixture(rows,entries,index=[{'row':rows[0],'error':entries[0]['error']}])
        selected,report=self.repair()
        self.assertEqual(selected,set(targets))
        self.assertEqual(report['recovered_saved'],2)
        self.assertEqual(report['model_calls'],0)
        self.runtime.assert_not_called();self.translate.assert_not_called()
        self.expect_raw(raw)

    def test_stale_sources_unknown_ids_and_reviewed_originals_are_not_repaired(self):
        current=self.row('current','This is current.')
        stale=self.row('stale-cache','My current source.')
        reviewed=self.row('reviewed','Please keep this phrase unchanged.')
        old_index=dict(current,source='An old source.')
        stale_entry=self.saved_failure(stale,'잘못된 오래된 복구.')
        stale_entry.update(source='My previous source.',text='My previous source.')
        raw=self.fixture([current,stale,reviewed],
            [self.entry(current,'현재 번역.'),stale_entry,self.saved_failure(reviewed,'검수값을 바꾸면 안 됩니다.')],
            index=[{'row':old_index,'error':'Old error; output='+repr('오래된 복구.')},
                   {'row':self.row('unknown','An unknown source.'),'error':'Missing output ID'}],
            reviewed={reviewed['source']:reviewed['source']})
        selected,report=self.repair()
        self.assertEqual(selected,set())
        self.assertEqual(report['model_calls'],0)
        self.assertEqual(cache(self.root,self.cfg)['current']['text'],'현재 번역.')
        self.assertEqual(cache(self.root,self.cfg)['reviewed']['text'],reviewed['source'])
        self.runtime.assert_not_called();self.translate.assert_not_called()
        recovered=self.root/'data/recovered-translations.json'
        if recovered.exists():self.assertEqual(json.loads(recovered.read_text(encoding='utf-8')), {})
        self.expect_raw(raw)

    def test_short_unchanged_dialogue_alone_reaches_fake_translation_and_is_reused(self):
        target=self.row('unchanged','Come with me.')
        good=[self.row('good'+str(i),'Normal line number '+str(i)+'.') for i in range(100)]
        raw=self.fixture([target]+good,[self.entry(target)]+[
            self.entry(row,'정상 번역 '+str(i)+'.') for i,row in enumerate(good)],index=[])
        seen=self.fake_translations({'unchanged':'나와 함께 가자.'})
        selected,report=self.repair()
        self.assertEqual(selected,{'unchanged'})
        self.assertEqual(seen,['unchanged'])
        self.assertEqual(report['model_calls'],1)
        self.assertEqual(cache(self.root,self.cfg)['unchanged']['text'],'나와 함께 가자.')
        for i,row in enumerate(good):self.assertEqual(cache(self.root,self.cfg)[row['id']]['text'],'정상 번역 '+str(i)+'.')
        again,second=self.repair()
        self.assertEqual(again,{'unchanged'})
        self.assertEqual(second['model_calls'],0)
        self.assertEqual(seen,['unchanged'])
        self.expect_raw(raw)

    def test_intentional_ui_names_links_identifiers_and_nonlanguage_are_excluded(self):
        rows=[self.row('custom','Visit the quiet corner now.','string'),
              self.row('ui-original','Leave this source text unchanged.','string','ui_label'),
              self.row('ui-fixed','A fixed control caption.','string','ui_label'),
              self.row('speaker','the friendly captain','string','speaker_label'),
              self.row('person','Rowan Ellis'),self.row('acronym','CPU'),
              self.row('key','Tab'),self.row('oneword','Ready.'),
              self.row('url','https://example.invalid/hidden/path'),
              self.row('windows',r'C:\invented\save_file.txt'),
              self.row('posix','/invented/save_file.txt'),self.row('code','state.player_name'),
              self.row('symbols','... * + -'),self.row('numbers','123 456'),
              self.row('format','%03d'),self.row('space','   ')]
        entries=[self.entry(row) for row in rows]
        entries[1]['status']='ui_original';entries[2]['status']='ui_fixed'
        entries[-2]['model']='literal format'
        raw=self.fixture(rows,entries,index=[],policy={
            'menu':{rows[0]['source']:rows[0]['source']},'choices':[]})
        selected,report=self.repair()
        self.assertEqual(selected,set())
        self.assertEqual(report['model_calls'],0)
        self.runtime.assert_not_called();self.translate.assert_not_called()
        self.expect_raw(raw)

    def test_short_unchanged_choice_is_a_candidate_without_ui_false_positive(self):
        choice=self.row('choice','Wait for me.','string')
        ui=self.row('menu','A custom menu entry.','string')
        raw=self.fixture([choice,ui],[self.entry(choice),self.entry(ui)],index=[],policy={
            'choices':[choice['source']],'menu':{ui['source']:ui['source']}})
        seen=self.fake_translations({'choice':'나를 기다려.'})
        selected,report=self.repair()
        self.assertEqual(selected,{'choice'})
        self.assertEqual(seen,['choice'])
        self.assertEqual(report['model_calls'],1)
        self.assertEqual(cache(self.root,self.cfg)['choice']['text'],'나를 기다려.')
        self.expect_raw(raw)

    def test_success_record_with_invalid_markup_is_discovered(self):
        row=self.row('bad-markup','Please greet [name] today.')
        raw=self.fixture([row],[self.entry(row,'오늘 인사해.')],index=[])
        seen=self.fake_translations({'bad-markup':'오늘 [name]에게 인사해.'})
        selected,report=self.repair()
        self.assertEqual(selected,{'bad-markup'})
        self.assertEqual(seen,['bad-markup'])
        self.assertEqual(report['model_calls'],1)
        self.assertEqual(cache(self.root,self.cfg)['bad-markup']['text'],'오늘 [name]에게 인사해.')
        self.expect_raw(raw)

    def test_only_missing_cache_item_is_translated_with_null_before_overlay(self):
        good=self.row('good','Already translated.')
        missing=self.row('missing','Please stay here.')
        raw=self.fixture([good,missing],[self.entry(good,'이미 번역됨.')],index=[])
        seen=self.fake_translations({'missing':'여기 있어 주세요.'})
        selected,report=self.repair()
        self.assertEqual(selected,{'missing'})
        self.assertEqual(seen,['missing'])
        self.assertEqual(report['model_calls'],1)
        stored=json.loads((self.root/'data/recovered-translations.json').read_text(encoding='utf-8'))
        self.assertIsNone(stored['missing']['before'])
        self.assertEqual(cache(self.root,self.cfg)['missing']['text'],'여기 있어 주세요.')
        self.assertEqual(cache(self.root,self.cfg)['good']['text'],'이미 번역됨.')
        again,second=self.repair()
        self.assertEqual(again,{'missing'})
        self.assertEqual(second['model_calls'],0)
        self.assertEqual(seen,['missing'])
        self.expect_raw(raw)


if __name__=='__main__':
    unittest.main()
