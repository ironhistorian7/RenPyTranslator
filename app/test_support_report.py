"""Synthetic diagnostics only; no application/game/model or production log access."""
from contextlib import redirect_stderr,redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import diagnostics
import portable_cli
from support_report import build_report,classify_issue,export_report,format_report


class SupportReportTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.write('runtime-lock.json',{'release':{'version':'0.3.0'}})

    def write(self,name,value):
        path=self.root/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
        return path

    def diagnostic(self,**changes):
        value={'error_type':'ValueError','error':'RAW PRIVATE ORIGINAL AND TRANSLATION secret_token=abc',
               'traceback':'Traceback (most recent call last):\n  File "C:\\Users\\PrivateName\\tool\\app\\translation.py", line 642, in translate_batch\n    SECRET ORIGINAL TEXT\n  File "E:\\PrivateGame\\story.py", line 4, in scene\nValueError: PRIVATE TRANSLATION',
               'project':'E:/PrivateGame','stage':'translation request','recorded_at':'2026-11-11T00:00:00Z',
               'items':[{'id':'private-id','source_file':'private-story.rpy','source':'PRIVATE SOURCE'}],
               'settings':{'engine':'rpgmaker','model':'synthetic-hy:q6','source_language':'japanese',
                           'num_ctx':16384,'parallel':2,'endpoint':'http://user:secret@localhost:1',
                           'username':'PrivateName','pcname':'PrivatePC','api_key':'secret-value',
                           '_runtime_env':{'PRIVATE_TOKEN':'secret-value'},
                           '_hardware_policy':{'devices':[{'id':'GPU-private-uuid','name':'NVIDIA Example GPU','total_mib':12288,'serial':'private-serial'}]}},
               'context_budget':{'prompt_content_tokens':9000,'output_reserved':4000,
                                 'tokenizer':'owned_model_tokenizer','files':{'private.txt':'PRIVATE SOURCE'}},
               'validation_failures':[{'error_code':'missing_response_item','error':'RAW OUTPUT',
                                       'source':'PRIVATE SOURCE'}],
               'accepted_ids':['source-private-id'],'metrics':{'items':2,'private':'secret'},
               'native_launch':{'runtime_id':'ollama-0.34.2','api_version':'0.34.2',
                                'gui_bundle_directory':'C:/Users/PrivateName/tool','env':{'secret':'raw'}}}
        value.update(changes)
        return self.write('data/projects/synthetic/data/errors/20261111-000000-0123456789.json',value)

    def test_safe_report_contains_technical_fields_and_no_raw_payload_or_paths(self):
        path=self.diagnostic()
        original=path.read_bytes()
        report=build_report(self.root,path,{'state':'warning','counts':{'completed':1,'total':2,'username':'PrivateName'},
                                           'gpus':[{'id':'GPU-private-uuid','name':'NVIDIA Example GPU','total':12288,'pcname':'PrivatePC'}]})
        text=format_report(report)
        self.assertEqual(report['app_version'],'0.3.0')
        self.assertEqual(report['issue']['code'],'missing_response_item')
        self.assertEqual(report['phase'],'translation')
        self.assertEqual(report['counts']['affected'],1)
        self.assertEqual(report['counts']['accepted'],1)
        self.assertEqual(report['settings']['parallel'],2)
        self.assertEqual(report['gpus'],[{'name':'NVIDIA Example GPU','total_mib':12288}])
        self.assertEqual(report['tool_frames'],[{'module':'translation.py','line':642,'function':'translate_batch'}])
        self.assertTrue(text.startswith('### RenPyTranslator 오류 제보\n'))
        self.assertIn('- 프로그램: RenPyTranslator 0.3.0',text)
        self.assertIn('- 모델: synthetic-hy:q6',text)
        self.assertIn('- 작업 수치:',text)
        for private in ('PrivateName','PrivateGame','PRIVATE','GPU-private','private-serial','PrivatePC','secret-value','RAW OUTPUT','private-story','http:','C:\\Users','E:/'):
            self.assertNotIn(private,text)
        self.assertEqual(path.read_bytes(),original)

    def test_export_writes_safe_json_once_without_modifying_original(self):
        path=self.diagnostic()
        original=path.read_bytes()
        target=self.root/'exports/support.json'
        self.assertEqual(export_report(self.root,path,target),str(target.resolve()))
        self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['schema'],'renpy-translator-support-v1')
        self.assertNotIn('PRIVATE',target.read_text(encoding='utf-8'))
        self.assertEqual(path.read_bytes(),original)
        with self.assertRaises(FileExistsError):export_report(self.root,path,target)
        with self.assertRaises(ValueError):export_report(self.root,path,self.root/'raw.zip')

    def test_arbitrary_cache_and_settings_files_are_rejected_before_read(self):
        for name in ('data/translations.jsonl','game/saves/save.json','data/gui-settings.json'):
            path=self.write(name,{'error_type':'ValueError','traceback':'private'})
            with self.subTest(name=name),patch.object(Path,'read_text',side_effect=AssertionError('Must reject before reading')):
                with self.assertRaises(ValueError):build_report(self.root,path)

    def test_external_project_error_is_supported_without_exporting_its_path(self):
        with tempfile.TemporaryDirectory() as external:
            path=Path(external)/'data/errors/20261111-000000-0123456789.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'error_type':'PermissionError','error':'private','traceback':'private'}),encoding='utf-8')
            text=format_report(build_report(self.root,path))
            self.assertIn('permission_denied',text)
            self.assertNotIn(external,text)

    def test_no_log_report_uses_safe_job_context(self):
        report=build_report(self.root,None,{'code':'output_limit','state':'failed','phase':'translation',
                                           'settings':{'model':'C:/Private/model.gguf','parallel':2},
                                           'counts':{'completed':7,'total':10,'password':'private'},
                                           'tasks':['run','failed','run','PRIVATE_SOURCE']})
        self.assertEqual(report['diagnostic_kind'],'none')
        self.assertEqual(report['issue']['code'],'output_limit')
        self.assertEqual(report['counts'],{'completed':7,'total':10})
        self.assertNotIn('model',report['settings'])
        self.assertEqual(report['tasks'],['run','failed'])
        self.assertIn('- 선택 작업:',format_report(report))
        self.assertNotIn('PRIVATE_SOURCE',format_report(report))
        target=self.root/'no-log-report.json'
        self.assertTrue(Path(export_report(self.root,None,target)).is_file())

    def test_public_and_dotted_stage_names_are_normalized(self):
        from support_report import phase_name
        for name,phase in (('inspect and prepare','prepare'),('RPG Maker patch output','render'),
                           ('prepare.copy','prepare'),('render.write','render'),('repair.saved','repair'),
                           ('validate.final','validation'),('names.metadata','names'),('package.zip','package')):
            with self.subTest(name=name):self.assertEqual(phase_name(name),phase)
        self.assertIsNone(phase_name('E:/PrivateGame/story'))

    def test_active_issue_error_id_and_core_tool_frames_are_kept(self):
        path=self.diagnostic(traceback='  File "C:/Private/engine.py", line 3, in prepare\n'
                                      '  File "C:/Private/font_policy.py", line 8, in details\n'
                                      '  File "C:/Private/packaging.py", line 9, in package\n')
        report=build_report(self.root,path)
        self.assertEqual([frame['module'] for frame in report['tool_frames']],['engine.py','font_policy.py','packaging.py'])
        report=build_report(self.root,None,{'issue':{'code':'permission_denied','error_id':'rpt-0123456789abcdef'},'state':'failed'})
        self.assertEqual(report['issue']['error_id'],'rpt-0123456789abcdef')
        self.assertEqual(report['issue']['code'],'permission_denied')

    def test_helper_header_and_stderr_are_reduced_to_technical_metadata(self):
        path=self.root/'logs/desktop-helper-2026-11-11-1-1.log'
        path.parent.mkdir()
        path.write_text(json.dumps({'command':'--desktop-project','code':1})+'\n\n'
                        'Private request path E:/PrivateGame\n'
                        '  File "C:/PrivateUser/app/desktop_bridge.py", line 308, in read_request\n'
                        'UnicodeDecodeError: PRIVATE INPUT\n[PYI-1:ERROR] Failed to execute script portable_cli due to unhandled exception!\n',encoding='utf-8')
        report=build_report(self.root,path)
        text=format_report(report)
        self.assertEqual(report['issue']['code'],'request_encoding')
        self.assertEqual(report['tool_frames'][0]['module'],'desktop_bridge.py')
        self.assertNotIn('PRIVATE',text)
        self.assertNotIn('PrivateUser',text)
        self.assertNotIn('PrivateGame',text)

    def test_extraction_notices_export_only_codes_counts_and_basename(self):
        path=self.write('data/projects/synthetic/data/rpg-extraction-notices.json',
                        {'kind':'rpg_extraction','issues':[{'code':'rpg_read_failure','file':'data/CustomTable.json',
                                                          'error_type':'JSONDecodeError','count':3,'error':'PRIVATE SOURCE'}],
                         'details':['PRIVATE ORIGINAL AND TRANSLATION']})
        report=build_report(self.root,path)
        self.assertEqual(report['issue']['code'],'rpg_read_failure')
        self.assertEqual(report['read_notices'],[{'code':'rpg_read_failure','error_type':'JSONDecodeError','file':'CustomTable.json','count':3}])
        self.assertNotIn('PRIVATE',format_report(report))
        self.write('data/projects/synthetic/data/rpg-extraction-notices.json',{'kind':'other','issues':[]})
        with self.assertRaises(ValueError):build_report(self.root,path)

    def test_unknown_cuda_is_not_reported_as_memory_shortage(self):
        self.assertEqual(classify_issue({'error_type':'RuntimeError','error':'CUDA error: unspecified launch failure'})['code'],'model_runtime')
        self.assertEqual(classify_issue({'error_type':'RuntimeError','error':'CUDA error: out of memory'})['code'],'memory_allocation')
        self.assertEqual(classify_issue({'error_type':'HTTPError','error':'CUDA status unknown'})['code'],'model_server')


class DiagnosticIssueTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.token=diagnostics._STATE.set({'stage':'translation request','project':str(self.root)})
        self.addCleanup(diagnostics._STATE.reset,self.token)

    def test_gui_issue_record_is_saved_once_and_promoted_without_traceback_stderr(self):
        error=ValueError('PRIVATE SOURCE AND TRANSLATION')
        error.error_code='output_limit'
        stdout=io.StringIO();stderr=io.StringIO()
        with patch.dict(os.environ,{'RPT_DESKTOP_EVENTS':'1'}),patch('job_events.emit') as emit, \
                redirect_stdout(stdout),redirect_stderr(stderr):
            first=diagnostics.report(error,handled=True)
            self.assertEqual(diagnostics.report(error,handled=True),first)
            self.assertEqual(diagnostics.report(error,handled=False),first)
            diagnostics.report(error,handled=False)
        self.assertEqual(emit.call_count,2)
        self.assertEqual(emit.call_args_list[0].kwargs['state'],'warning')
        self.assertEqual(emit.call_args_list[1].kwargs['state'],'failed')
        self.assertEqual(emit.call_args_list[0].kwargs['error_id'],emit.call_args_list[1].kwargs['error_id'])
        self.assertEqual(list((self.root/'data/errors').glob('*.json')),[Path(first)])
        self.assertIn('PRIVATE SOURCE',json.loads(Path(first).read_text(encoding='utf-8'))['traceback'])
        self.assertEqual(stdout.getvalue()+stderr.getvalue(),'')

    def test_failed_issue_is_not_downgraded_by_a_later_handled_report(self):
        error=PermissionError('PRIVATE FILE PATH')
        with patch.dict(os.environ,{'RPT_DESKTOP_EVENTS':'1'}),patch('job_events.emit') as emit:
            diagnostics.report(error)
            diagnostics.report(error,handled=True)
        emit.assert_called_once()
        self.assertEqual(emit.call_args.kwargs['code'],'permission_denied')
        self.assertNotIn('PRIVATE',str(emit.call_args))

    def test_cli_summary_does_not_duplicate_raw_output_or_traceback(self):
        error=ValueError('PRIVATE TRANSLATED OUTPUT')
        stderr=io.StringIO()
        with patch.dict(os.environ,{'RPT_DESKTOP_EVENTS':'0'}),redirect_stderr(stderr):
            path=diagnostics.report(error)
            diagnostics.report(error)
        self.assertIn('Detailed error: '+path,stderr.getvalue())
        self.assertNotIn('PRIVATE',stderr.getvalue())
        self.assertNotIn('Traceback',stderr.getvalue())
        self.assertEqual(stderr.getvalue().count('Detailed error:'),1)

    def test_log_write_failure_does_not_replace_or_repeat_original_issue(self):
        error=PermissionError('PRIVATE ORIGINAL ERROR')
        with patch.dict(os.environ,{'RPT_DESKTOP_EVENTS':'1'}),patch('job_events.emit') as emit, \
                patch.object(Path,'write_text',side_effect=PermissionError('PRIVATE LOG PATH')) as write:
            self.assertIsNone(diagnostics.report(error,handled=True))
            diagnostics.report(error)
        write.assert_called_once()
        self.assertEqual(emit.call_count,2)
        self.assertIsNone(emit.call_args.kwargs['log_path'])
        self.assertEqual(emit.call_args.kwargs['code'],'permission_denied')

    def test_stage_public_hook_receives_stage_without_own_emit(self):
        from types import SimpleNamespace
        with patch.dict(sys.modules,{'job_stages':SimpleNamespace(public_stage=lambda *args:seen.append(args))}):
            seen=[]
            diagnostics.stage('translation request',cfg={'model':'synthetic'},reset=True)
        self.assertEqual(seen,[('translation request',None,{'model':'synthetic'})])


class SupportHelperTests(unittest.TestCase):
    def call(self,payload,**patches):
        stdout=io.StringIO()
        with patch.object(sys,'argv',['synthetic-cli','--desktop-support']), \
                patch('desktop_bridge.read_request',return_value=payload), \
                patch('support_report.build_report',return_value={'schema':'synthetic'}) as build, \
                patch('support_report.export_report',return_value='synthetic-report.json') as export, \
                patch('diagnostics.report',side_effect=AssertionError('Do not overwrite original issue')), \
                redirect_stdout(stdout):
            portable_cli.main()
        return json.loads(stdout.getvalue()),build,export

    def test_copy_and_export_contracts_do_not_start_a_worker(self):
        copied,build,export=self.call({'op':'copy','log_path':None,'job_context':{'state':'warning'}})
        self.assertTrue(copied['text'].startswith('### RenPyTranslator 오류 제보\n'))
        self.assertIn('- 프로그램: RenPyTranslator',copied['text'])
        self.assertNotIn('"schema"',copied['text'])
        build.assert_called_once();export.assert_not_called()
        exported,build,export=self.call({'op':'export','log_path':None,'destination':'synthetic-report.json'})
        self.assertEqual(exported,{'path':'synthetic-report.json'})
        export.assert_called_once();build.assert_not_called()

    def test_support_failure_returns_short_json_without_reporting_another_error(self):
        stdout=io.StringIO()
        with patch.object(sys,'argv',['synthetic-cli','--desktop-support']), \
                patch('desktop_bridge.read_request',side_effect=ValueError('PRIVATE PAYLOAD')), \
                patch('diagnostics.report',side_effect=AssertionError('No secondary issue')),redirect_stdout(stdout):
            portable_cli.main()
        result=json.loads(stdout.getvalue())
        self.assertEqual(result['code'],'support_unavailable')
        self.assertNotIn('PRIVATE',result['error'])


if __name__=='__main__':unittest.main()
