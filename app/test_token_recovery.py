import json
from contextlib import nullcontext
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from engine import save_json
from packaging import package
from term_memory import TermMemory
from translation import (protect, restore, translate_batch, BatchTranslationError,
                         translate, cache, catalog, render, validate, validate_entry, validate_text, FORMATS)

CFG={'language':'korean','model':'fake','endpoint':'http://127.0.0.1:11434','batch_size':8}

def response(items):
    return {'message':{'content':json.dumps(items,ensure_ascii=False)}}

class TokenRecoveryTests(unittest.TestCase):
    def setUp(self):
        runtime=patch('hy_backend.runtime',side_effect=lambda cfg:nullcontext(cfg))
        runtime.start();self.addCleanup(runtime.stop)

    def test_named_variables_can_reorder_but_tags_and_counts_stay_intact(self):
        source='{i}Image [index] of [count]{/i}'
        _,tokens=protect(source)
        text=restore('<rpt000/>전체 <rpt002/>개 중 <rpt001/>번<rpt003/>',tokens)
        self.assertEqual(text,'{i}전체 [count]개 중 [index]번{/i}')
        self.assertEqual(validate_text(source,text,CFG),[])
        for bad in ('<rpt003/><rpt002/>장 중 <rpt001/>번<rpt000/>',
                    '<rpt000/><rpt002/>장<rpt003/>',
                    '<rpt000/><rpt002/><rpt002/>장<rpt001/><rpt003/>',
                    '<rpt000/><rpt002/>장<rpt001/><rpt004/>'):
            with self.subTest(bad=bad),self.assertRaises(ValueError):restore(bad,tokens)
        self.assertIn('markup mismatch',validate_text(source,'{/i}[count]개 중 [index]번{i}',CFG))
        self.assertIn('markup mismatch',validate_text(source,'{i}[count]개 중{/i}',CFG))
        self.assertEqual(validate_text('Hello %(first)s and %(last)s','%(last)s와 %(first)s 안녕',CFG),[])

    def test_batch_prompt_and_final_validation_allow_variable_reordering(self):
        row=dict(id='one',kind='dialogue',source='Image [index] of [count] locked.')
        with patch('translation.request',return_value=response({'0':'전체 <rpt001/>개 중 <rpt000/>번 이미지는 잠겨 있습니다.'})) as call:
            out,_=translate_batch([row],{},CFG)
        item=json.loads(call.call_args.args[2]['messages'][1]['content'])['items'][0]
        self.assertEqual(item['movable_placeholders'],['<rpt000/>','<rpt001/>'])
        self.assertEqual(validate_text(row['source'],out[0]['text'],CFG),[])

    def test_literal_percent_to_is_not_a_format_field(self):
        self.assertEqual(FORMATS.findall('I need 100/% to finish this.'),[])
        self.assertEqual(validate_text('I need 100/% to finish this.','이 일을 끝내려면 100%가 필요해.',CFG),[])

    def test_real_percent_fields_still_require_preservation(self):
        for field in ('%s','%(name)s','%03d','%.2f','%+8.2f','%*.*f','%ld','%%'):
            with self.subTest(field=field):
                self.assertEqual(FORMATS.findall(field),[field])
                self.assertIn('format field mismatch',validate_text('Value: '+field,'값: 없음',CFG))

    def test_markers_roundtrip_and_delimiter_repairs(self):
        source='known {i}Ariel{/i}'
        protected,tokens=protect(source)
        self.assertEqual(protected,'known <rpt000/>Ariel<rpt001/>')
        self.assertEqual(restore(protected,tokens),source)
        for text in ('알려진 <rpt000/>아리엘<rpt001/>',
                     '알려진 < RPT 000 >아리엘<rpt001 />',
                     '알려진 **RPT_000__아리엘__RPT_001**',
                     r'알려진 **RPT\_000\_\_아리엘\_\_RPT\_001**'):
            with self.subTest(text=text):
                self.assertEqual(restore(text,tokens),'알려진 {i}아리엘{/i}')
        self.assertEqual(restore('__RPT_000____RPT_001__',tokens),'{i}{/i}')

    def test_missing_duplicate_reordered_and_unknown_ids_are_not_invented(self):
        tokens=['{i}','{/i}']
        for text in ('<rpt000/>아리엘','<rpt001/>아리엘<rpt000/>',
                     '<rpt000/><rpt000/><rpt001/>','<rpt000/><rpt002/>'):
            with self.subTest(text=text),self.assertRaises(ValueError):restore(text,tokens)

    def test_valid_items_survive_a_partial_batch_failure(self):
        rows=[{'id':'ok','kind':'dialogue','source':'known {i}Ariel{/i}'},
              {'id':'bad','kind':'dialogue','source':'Hello [name]'}]
        with patch('translation.request',return_value=response({'0':'알려진 <rpt000/>아리엘<rpt001/>','1':'안녕'})):
            with self.assertRaises(BatchTranslationError) as caught:translate_batch(rows,{},CFG)
        self.assertEqual([r['id'] for r in caught.exception.output],['ok'])
        self.assertEqual([f['row']['id'] for f in caught.exception.failures],['bad'])

    def test_missing_response_item_preserves_other_items(self):
        rows=[{'id':'ok','kind':'string','source':'Save'},
              {'id':'missing','kind':'string','source':'Load'}]
        with patch('translation.request',return_value=response({'0':'저장'})):
            with self.assertRaises(BatchTranslationError) as caught:translate_batch(rows,{},CFG)
        self.assertEqual(caught.exception.output[0]['text'],'저장')
        self.assertEqual(caught.exception.failures[0]['row']['id'],'missing')

    def test_only_bad_item_retried_then_fallback_renders_validates_and_packages(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);target=p/'staging/game/tl/korean';target.mkdir(parents=True)
            template=('translate korean first:\n    # n "known {i}Ariel{/i}"\n    n ""\n\n'
                      'translate korean second:\n    # n "Please come and meet [name] here today."\n    n ""\n')
            (target/'story.rpy').write_text(template,encoding='utf-8')
            rows=catalog(p,CFG);calls=[]
            def fake_request(endpoint,route,body):
                payload=json.loads(body['messages'][1]['content']);calls.append(payload)
                if len(calls)==1:
                    return response({'0':'알려진 <rpt000/>아리엘<rpt001/>','1':'여기로 와요.'})
                return response({'0':'여기로 와요.'})
            with patch('translation.request',side_effect=fake_request),patch('source_context.SourceContext.same_scope',return_value=True):translate(p,CFG)
            self.assertEqual(len(calls),3)
            self.assertEqual([len(c['items']) for c in calls],[2,1,1])
            for retry in calls[1:]:
                self.assertIn('Please come',retry['items'][0]['text'])
                self.assertEqual(set(retry['context_do_not_translate']),{'variable_identities'})
                self.assertEqual(retry['context_do_not_translate']['variable_identities']['name']['kind'],'name')
            known=cache(p,CFG);fallback=known[rows[1]['id']]
            self.assertEqual(fallback['text'],rows[1]['source'])
            self.assertEqual(fallback['status'],'source_fallback')
            self.assertEqual(known[rows[0]['id']]['text'],'알려진 {i}아리엘{/i}')
            with patch('translation.request',side_effect=AssertionError('No more inference')):
                translate(p,CFG)
                self.assertEqual(len(json.loads((p/'data/failed-items.json').read_text(encoding='utf-8'))),1)
                render(p,CFG)
                with patch('translation.engine_command'),patch('translation.verify_source',return_value={'files':0}):
                    validate(p,CFG)
                (p/'staging/game/zz_rpt_korean.rpy').write_text('# fixture',encoding='utf-8')
                with patch('packaging.verify_source',return_value={'files':0}):package(p,CFG)
            report=json.loads((p/'output/build-report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['source_fallbacks'],1)
            self.assertEqual(len(list((p/'output').glob('*.zip'))),1)

    def test_isolated_retry_can_succeed_without_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);row=dict(id='a',source='Hi [name]',kind='dialogue',file='test.rpy')
            save_json(p/'data/catalog.json',[row])
            with patch('translation.request',side_effect=[response({'0':'안녕'}),response({'0':'안녕 <rpt000/>'})]) as api:
                translate(p,CFG)
            self.assertEqual(api.call_count,2)
            self.assertEqual(cache(p,CFG)['a']['text'],'안녕 [name]')
            self.assertEqual(json.loads((p/'data/failed-items.json').read_text(encoding='utf-8')),[])

    def test_network_failure_stops_instead_of_falling_back_everything(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);save_json(p/'data/catalog.json',[dict(id='a',source='Hello',kind='string',file='test.rpy')])
            with patch('translation.request',side_effect=urllib.error.URLError('offline')) as api:
                with self.assertRaises(urllib.error.URLError):translate(p,CFG)
            self.assertEqual(api.call_count,1)
            self.assertFalse((p/'data/translations.jsonl').exists())

    def test_fallback_does_not_relax_markup_checks_or_teach_terms(self):
        source='Please come and meet [name] here today.'
        good={'text':source,'status':'source_fallback'}
        self.assertEqual(validate_entry(source,good,CFG),[])
        self.assertIn('no Korean translation',validate_entry(source,{'text':source},CFG))
        broken=dict(good,text=source.replace('[name]',''))
        self.assertIn('markup mismatch',validate_entry(source,broken,CFG))
        rows=[dict(id='a',source='Nova',kind='string')]
        memory=TermMemory(rows,{'a':dict(source='Nova',text='Nova',status='source_fallback')})
        self.assertEqual(memory.translations,{})

if __name__=='__main__':unittest.main()
