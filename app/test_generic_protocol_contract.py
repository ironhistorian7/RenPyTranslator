"""Generic protocol regression tests using fake local responses; never call a model."""
from contextlib import ExitStack, redirect_stdout
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine import save_json
import translation

CFG = dict(language='korean', source_language='english', engine='renpy',
           model='synthetic-generic', endpoint='http://127.0.0.1:1',
           num_ctx=16384, parallel=1, batch_size=8, context_txt_enabled=False)

def row(uid='item', source='Hello there, friend.'):
    return dict(id=uid, source=source, kind='dialogue', usage='dialogue',
                file='synthetic.rpy', block='synthetic')

def response(text, reason='stop'):
    return {'message':{'content':text}, 'done_reason':reason}

class GenericProtocolContract(unittest.TestCase):
    def setUp(self):
        stack=ExitStack();self.addCleanup(stack.close)
        for target in ('translation.trace_event','diagnostics.stage','diagnostics.report','job_events.warning'):
            stack.enter_context(patch(target,return_value=None))
        stack.enter_context(patch('translation.runtime_status',return_value={}))
        stack.enter_context(patch('hy_backend.is_hy',return_value=False))
        for target in ('request_budget.post','hy_backend.api'):
            stack.enter_context(patch(target,side_effect=AssertionError('Unexpected model API call')))

    def run_job(self, source, answers):
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp);r=row(source=source);save_json(project/'data/catalog.json',[r]);calls=[]
            def fake(endpoint,route,body,**kwargs):
                calls.append(copy.deepcopy(body));return response(answers[min(len(calls)-1,len(answers)-1)])
            with (patch('translation.request',side_effect=fake),
                  patch('source_context.SourceContext') as context,
                  patch('name_hints.ensure_metadata',return_value={'names':{}}),
                  patch('name_translation.mapping',return_value={}),redirect_stdout(io.StringIO())):
                context.return_value.context.return_value={}
                context.return_value.same_scope.return_value=True
                translation._translate(project,dict(CFG))
            records=[json.loads(s) for s in (project/'data/translations.jsonl').read_text(encoding='utf-8').splitlines()]
            return calls,records[0]

    def test_first_request_is_coherent_structured_json(self):
        rows=[row('a'),row('b','Goodbye for now.')]
        with patch('translation.request',return_value=response('{"0":"안녕하세요.","1":"다음에 봐요."}')) as request:
            values,_=translation.translate_batch(rows,{},dict(CFG))
        body=request.call_args.args[2]
        schema=body['format'];self.assertEqual(schema['type'],'object')
        self.assertEqual(schema['required'],['0','1']);self.assertFalse(schema['additionalProperties'])
        self.assertEqual(set(schema['properties']),{'0','1'})
        self.assertTrue(all(x['type']=='string' and x['minLength']==1 for x in schema['properties'].values()))
        user=json.loads(next(m['content'] for m in body['messages'] if m['role']=='user'))
        self.assertEqual([x['id'] for x in user['items']],['0','1'])
        self.assertIn('context_do_not_translate',user)
        self.assertEqual([x['text'] for x in values],['안녕하세요.','다음에 봐요.'])

    def test_untranslated_short_english_is_retried_as_plain(self):
        calls,result=self.run_job('Hello there, friend.',['{"0":"Hello there, friend."}','안녕하세요, 친구야.'])
        self.assertEqual(len(calls),2)
        self.assertIn('format',calls[0]);self.assertNotIn('format',calls[1])
        self.assertEqual(len(calls[1]['messages']),1)
        self.assertEqual(result['text'],'안녕하세요, 친구야.')
        self.assertNotEqual(result.get('status'),'source_fallback')

    def test_fallback_failures_share_existing_two_retry_budget(self):
        calls,result=self.run_job('Hello there, friend.',['{"0":"Hello there, friend."}','Hello there, friend.'])
        self.assertEqual(len(calls),3)
        self.assertIn('format',calls[0]);self.assertTrue(all('format' not in call for call in calls[1:]))
        self.assertNotEqual(calls[1]['messages'],calls[2]['messages'])
        self.assertEqual(result['status'],'source_fallback')

    def test_json_parser_keeps_valid_siblings_and_rejects_ambiguous_structure(self):
        from generic_protocol import parse
        expected={'0':'Hello there.','1':'Goodbye for now.'}
        self.assertEqual(parse('{"0":"안녕하세요."}',expected),{'0':'안녕하세요.'})
        for invalid in ('{"0":42}', '{"2":"안녕"}', '{"0":"안녕","0":"잘 가"}',
                        '["안녕"]','[0] 안녕'):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):parse(invalid,expected)
        self.assertEqual(parse('{"0":"안녕"}',expected,truncated=True),{})

    def test_duplicate_json_key_or_headerless_first_reply_is_retried(self):
        for invalid in ('{"0":"안녕","0":"다시 안녕"}', '안녕하세요.'):
            with self.subTest(invalid_kind='duplicate' if invalid.startswith('{') else 'headerless'):
                calls,result=self.run_job('Hello there, friend.',[invalid,'안녕하세요.'])
                self.assertEqual(len(calls),2)
                self.assertIn('format',calls[0]);self.assertNotIn('format',calls[1])
                self.assertEqual(result['text'],'안녕하세요.')

    def test_missing_json_id_retries_only_failed_item(self):
        calls,result=self.run_job('Hello there, friend.',['{}','안녕하세요.'])
        self.assertEqual(len(calls),2);self.assertIn('format',calls[0]);self.assertNotIn('format',calls[1])
        self.assertEqual(result['text'],'안녕하세요.')

if __name__=='__main__':unittest.main()
