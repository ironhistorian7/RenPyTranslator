"""Synthetic request contracts only; no installed games, caches or model calls."""
from contextlib import ExitStack, nullcontext, redirect_stdout
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine import save_json
import failed_repair
import translation


BASE = dict(language='korean', source_language='japanese', engine='rpgmaker',
            endpoint='http://127.0.0.1:1', num_ctx=16384, parallel=1,
            batch_size=8, context_txt_enabled=False)
MODELS = ('invented-generic', 'invented-hy')


def answer(model, values, *, retry=False):
    if model == 'invented-generic':
        content = next(iter(values.values())) if retry else json.dumps(values, ensure_ascii=False)
    else:
        content = '\n'.join('[' + key + '] ' + value for key, value in values.items())
    return dict(message=dict(content=content), done_reason='stop')


class RetryResponseContractTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch('hy_backend.is_hy',
                                  side_effect=lambda cfg: cfg['model'] == 'invented-hy'))
        for target, value in (('translation.runtime_status', {}),
                              ('translation.trace_event', None),
                              ('diagnostics.stage', None), ('diagnostics.report', None),
                              ('job_events.warning', None)):
            stack.enter_context(patch(target, return_value=value))
        # Any accidental tokenizer/model API request is a test failure.
        for target in ('request_budget.post', 'hy_backend.api'):
            stack.enter_context(patch(target, side_effect=AssertionError('No model API')))

    def assert_contract(self, body, model, ids=('0',), *, retry=False):
        self.assertEqual(body['options']['num_ctx'], 16384)
        prompt = next(m['content'] for m in reversed(body['messages']) if m['role']=='user')
        if model == 'invented-generic' and not retry:
            self.assertEqual(body['format']['required'], list(ids))
            data=json.loads(prompt)
            self.assertEqual([item['id'] for item in data['items']],list(ids))
            return '[Source Text]\n'+'\n'.join('['+item['id']+'] '+item['text'] for item in data['items'])
        self.assertNotIn('format', body)
        self.assertIn('[Source Text]\n', prompt)
        source_text = prompt.split('[Source Text]\n',1)[1]
        if model == 'invented-hy':
            for key in ids:self.assertIn('[' + key + '] ', source_text)
        else:
            self.assertEqual(len(ids),1)
        return prompt

    def row(self, source='こんにちは。', **extra):
        return dict(id='invented-item', source=source, kind='dialogue',
                    usage='dialogue', file='data/Map001.json', block='invented', **extra)

    def test_first_batch_and_real_single_item_retry_keep_one_contract(self):
        for model in MODELS:
            for engine in ('rpgmaker', 'renpy'):
                with self.subTest(model=model, engine=engine), tempfile.TemporaryDirectory() as temp:
                    project = Path(temp)
                    rows = [self.row(), dict(self.row('また会いましょう。'), id='second-item')]
                    if engine == 'renpy':
                        rows = [dict(row, source=source, file='invented.rpy') for row, source in
                                zip(rows, ('Hello there.', 'Goodbye for now.'))]
                    save_json(project/'data/catalog.json', rows)
                    cfg = dict(BASE, model=model, engine=engine,
                               source_language='japanese' if engine == 'rpgmaker' else 'english')
                    calls = []
                    def respond(endpoint, route, body, **kwargs):
                        calls.append(copy.deepcopy(body))
                        if len(calls) == 1:
                            # A missing item triggers Ren'Py's retry independently
                            # of its intentional allowance for non-Korean strings.
                            values = {'0':'안녕하세요.'}
                            if engine == 'rpgmaker':values['1']='Not translated.'
                            return answer(model, values)
                        return answer(model, {'0':'또 만나요.'}, retry=True)
                    with (patch('translation.request', side_effect=respond),
                          patch('translation.translate_batch', wraps=translation.translate_batch) as batch,
                          patch('source_context.SourceContext') as context,
                          patch('name_hints.ensure_metadata', return_value={'names':{}}),
                          patch('name_translation.mapping', return_value={}),
                          redirect_stdout(io.StringIO())):
                        context.return_value.context.return_value = {}
                        context.return_value.same_scope.return_value = True
                        translation._translate(project, cfg)
                    self.assertEqual(len(calls), 2)
                    self.assert_contract(calls[0], model, ('0', '1'))
                    retry = self.assert_contract(calls[1], model, retry=True)
                    self.assertIn(rows[1]['source'],retry)
                    if engine == 'rpgmaker':
                        self.assertTrue(batch.call_args_list[1].args[2]['_rpg_clean_retry'])
                        self.assertEqual(batch.call_args_list[1].args[0][0]['source'],rows[1]['source'])
                    records = [json.loads(line) for line in
                               (project/'data/translations.jsonl').read_text(encoding='utf-8').splitlines()]
                    self.assertEqual([entry['id'] for entry in records], [row['id'] for row in rows])
                    self.assertTrue(all(entry.get('status') != 'source_fallback' for entry in records))

    def test_partial_batch_retries_only_missing_item_with_shared_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp)
            rows=[self.row('Hello, friend.'),dict(self.row('Goodbye, friend.'),id='second-item')]
            for item in rows:item['file']='invented.rpy'
            cfg=dict(BASE,model='invented-generic',engine='renpy',source_language='english')
            save_json(project/'data/catalog.json',rows);calls=[]
            def respond(endpoint,route,body,**kwargs):
                calls.append(copy.deepcopy(body))
                return answer('invented-generic',{'0':'안녕, 친구야.'}) if len(calls)==1 else answer('invented-generic',{'0':'Goodbye, friend.'},retry=True)
            with (patch('translation.request',side_effect=respond),
                  patch('source_context.SourceContext') as context,
                  patch('name_hints.ensure_metadata',return_value={'names':{}}),
                  patch('name_translation.mapping',return_value={}),redirect_stdout(io.StringIO())):
                context.return_value.context.return_value={};context.return_value.same_scope.return_value=True
                translation._translate(project,cfg)
            self.assertEqual(len(calls),3)
            self.assert_contract(calls[0],'invented-generic',('0','1'))
            for body in calls[1:]:
                prompt=self.assert_contract(body,'invented-generic',retry=True)
                self.assertIn(rows[1]['source'],prompt)
                self.assertNotIn(rows[0]['source'],prompt)
            records={x['id']:x for x in (json.loads(line) for line in (project/'data/translations.jsonl').read_text(encoding='utf-8').splitlines())}
            self.assertNotEqual(records[rows[0]['id']].get('status'),'source_fallback')
            self.assertEqual(records[rows[1]['id']]['status'],'source_fallback')

    def test_marker_failure_preserves_functional_placeholder_on_retry(self):
        row = self.row(r'こ\.んにちは\V[1]。')
        previous = "Protected token mismatch: [] != [0]; output='안녕'"
        for model in MODELS:
            with self.subTest(model=model):
                cfg = dict(BASE, model=model, _rpg_retry_errors={row['id']:previous})
                with patch('translation.request', return_value=answer(model, {'0':'안⟦RPT000⟧녕하세요⟦RPT001⟧.'})) as request:
                    result, _ = translation.translate_batch([row], {}, cfg)
                request.assert_called_once()
                prompt = self.assert_contract(request.call_args.args[2], model)
                source = prompt.split('[Source Text]\n',1)[1]
                self.assertIn('こ',source)
                self.assertIn('んにちは',source)
                self.assertRegex(source,r'(?i)rpt000')
                self.assertRegex(source,r'(?i)rpt001')
                self.assertEqual(result[0]['text'],r'안\.녕하세요\V[1].')
                self.assertEqual(row['source'],r'こ\.んにちは\V[1]。')

    def test_both_japanese_correction_attempts_keep_the_selected_contract(self):
        row = self.row()
        previous = "['untranslated Japanese text']; output='안녕ッ'"
        for model in MODELS:
            for attempt in (0, 1):
                with self.subTest(model=model, attempt=attempt):
                    cfg = dict(BASE, model=model, _rpg_retry_errors={row['id']:previous},
                               _rpg_correction_attempt=attempt)
                    with patch('translation.request', return_value=answer(model, {'0':'안녕'})) as request:
                        result, _ = translation.translate_batch([row], {}, cfg)
                    request.assert_called_once()
                    payload = self.assert_contract(request.call_args.args[2], model)
                    self.assertIn('[Source Text]\n[0] 안녕ッ', payload)
                    self.assertEqual(result[0]['text'], '안녕')

    def test_failed_repair_passes_active_rpg_config_to_the_same_request_path(self):
        row = self.row()
        previous = 'no Korean translation'
        for model in MODELS:
            with self.subTest(model=model), tempfile.TemporaryDirectory() as temp:
                project = Path(temp)
                cfg = dict(BASE, model=model)
                save_json(project/'data/failed-items.json', [dict(row=row, error=previous)])
                saved = dict(row, text=row['source'], model=model, status='source_fallback', error=previous)
                with (patch('failed_repair.read_catalog', return_value=[row]),
                      patch('failed_repair.cache', return_value={row['id']:saved}),
                      patch('failed_repair.display_metadata', return_value={'names':{}}),
                      patch('failed_repair.retry_instruction', wraps=translation.retry_instruction) as note,
                      patch('failed_repair.translate_batch', wraps=translation.translate_batch) as batch,
                      patch('source_context.SourceContext') as context,
                      patch('hy_backend.runtime', side_effect=lambda active: nullcontext(active)),
                      patch('model_runtime.model_session', return_value=nullcontext()),
                      patch('translation.request', return_value=answer(model, {'0':'안녕하세요.'}, retry=True)) as request,
                      redirect_stdout(io.StringIO())):
                    context.return_value.context.return_value = {}
                    changed, report = failed_repair.run(project, cfg)
                request.assert_called_once()
                note.assert_not_called()
                self.assertTrue(batch.call_args.args[2]['_rpg_clean_retry'])
                self.assertEqual(batch.call_args.args[0][0]['source'],row['source'])
                payload = self.assert_contract(request.call_args.args[2], model, retry=True)
                self.assertIn(row['source'],payload)
                self.assertEqual(changed, {row['id']})
                self.assertEqual(report['retranslated'], 1)
                self.assertEqual(report['remaining'], 0)

    def test_failed_repair_keeps_clean_phase_and_approved_names_after_other_error(self):
        row=self.row(); previous='no Korean translation'
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp);cfg=dict(BASE,model='invented-generic')
            save_json(project/'data/failed-items.json',[dict(row=row,error=previous)])
            saved=dict(row,text=row['source'],model=cfg['model'],status='source_fallback',error=previous)
            calls=[]
            def batch(items,context,active,note=''):
                calls.append((items,dict(active),note))
                if len(calls)==1:
                    raise translation.BatchTranslationError([], [dict(row=row,error='Missing response item')],dict(seconds=0,items=1))
                return [dict(id=row['id'],source=row['source'],text='안녕하세요.')],dict(seconds=0,items=1)
            def approved(project,active,rows):
                return dict(active,_person_names={'ユリ':'유리'})
            with (patch('failed_repair.read_catalog',return_value=[row]),
                  patch('failed_repair.cache',return_value={row['id']:saved}),
                  patch('failed_repair.display_metadata',return_value={'names':{}}),
                  patch('name_review.configuration',side_effect=approved),
                  patch('failed_repair.translate_batch',side_effect=batch),
                  patch('source_context.SourceContext') as context,
                  patch('hy_backend.runtime',side_effect=lambda active:nullcontext(active)),
                  patch('model_runtime.model_session',return_value=nullcontext()),
                  redirect_stdout(io.StringIO())):
                context.return_value.context.return_value={}
                changed,report=failed_repair.run(project,cfg)
            self.assertEqual(len(calls),2)
            for items,active,note in calls:
                self.assertTrue(active['_rpg_clean_retry'])
                self.assertEqual(active['_person_names'],{'ユリ':'유리'})
                self.assertEqual(items[0]['source'],row['source'])
                self.assertEqual(note,'')
            self.assertEqual(changed,{row['id']})
            self.assertEqual((report['model_calls'],report['retranslated'],report['remaining']),(2,1,0))


if __name__ == '__main__':
    unittest.main()
