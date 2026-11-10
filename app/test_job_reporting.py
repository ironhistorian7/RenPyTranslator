"""Synthetic stage and upstream-notice checks; no game or inference runs."""
from contextlib import ExitStack,redirect_stdout
import importlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import job_stages
from rpgmaker_notices import Notices
from gameloc_vendor import load


class ReportingTests(unittest.TestCase):
    def test_worker_diagnostics_do_not_reset_the_public_phase(self):
        with patch.object(job_stages,'set_phase') as phase:
            job_stages.public_stage('translation request')
            job_stages.public_stage('model runtime')
            phase.assert_not_called()
            job_stages.public_stage('render')
            phase.assert_called_once_with('render','번역 파일 생성 중')

    def test_notice_levels_and_details_are_separate(self):
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp);notice=Notices()
            notice.progress('synthetic per-file success')
            notice.notice('rpg_js_candidates',count=9)
            notice.notice('rpg_identifiers_preserved',count=4)
            notice.notice('rpg_read_failure',file='data/Extra.json',error_type='ValueError')
            with patch('rpgmaker_notices.emit') as emit,redirect_stdout(io.StringIO()) as stdout:
                target=notice.save(project)
            data=json.loads(target.read_text(encoding='utf-8'))
            self.assertEqual(data['kind'],'rpg_extraction')
            self.assertEqual(data['details'],['synthetic per-file success'])
            events=[(call.args[0],call.kwargs) for call in emit.call_args_list]
            self.assertEqual(events[0][0],'issue')
            self.assertEqual(events[0][1]['counts'],{'files':1})
            self.assertEqual(events[0][1]['state'],'warning')
            self.assertEqual([event[1]['count'] for event in events[1:]],[9,4])
            self.assertNotIn('고급 설정',stdout.getvalue())
            self.assertNotIn('synthetic per-file success',stdout.getvalue())

    def test_upstream_callback_retains_real_failure_without_obsolete_ui_advice(self):
        load();extract=importlib.import_module('gameloc.extract')
        from gameloc.engines import rpgmaker
        with tempfile.TemporaryDirectory() as temp,ExitStack() as stack:
            root=Path(temp);bad=root/'data/Extra.json'
            info=SimpleNamespace(root=root,rm_base=root,engine='rpgmaker',asset_files=[],rm_variant='MZ',summary=lambda:'synthetic RPG Maker')
            stack.enter_context(patch.object(extract,'detect',return_value=info))
            for name,value in {'extract_entries':[],'custom_data_files':[bad],
                               'extract_plugins':[],'count_js_dialogue':11,
                               'collect_identifiers_certain':set(),'collect_identifiers_guess':set(),
                               'known_display_texts':set()}.items():
                stack.enter_context(patch.object(rpgmaker,name,return_value=value))
            stack.enter_context(patch.object(extract,'_from_loose',side_effect=ValueError('synthetic failure')))
            notices=[];lines=[]
            result=extract.extract(root,progress=lines.append,on_notice=lambda code,**fields:notices.append(dict(code=code,**fields)))
            self.assertEqual(result.engine,'rpgmaker')
            self.assertEqual(notices,[{'code':'rpg_read_failure','file':str(Path('data/Extra.json')),'error_type':'ValueError'},
                                      {'code':'rpg_js_candidates','count':11}])
            self.assertFalse(any('고급 설정' in line or '파일까지' in line for line in lines))


if __name__=='__main__':unittest.main()
