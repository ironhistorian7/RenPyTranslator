"""Display labels from an invented saved library; no model/server or GPU use."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import model_store
from diagnostics import runtime_settings
from support_report import classify_issue,format_report


class ModelDisplayTests(unittest.TestCase):
    def test_label_comes_from_metadata_and_does_not_change_execution_id(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(model_store,'ROOT',Path(directory)):
            path=Path(directory)/'data/model-library.json';path.parent.mkdir()
            path.write_text(json.dumps({'rpt-local-example:latest':
                {'filename':'X:/private-folder/Invented-12B-IQ2_XS.gguf'}}),encoding='utf-8')
            cfg={'model':'rpt-local-example:latest','num_ctx':16384,'parallel':2}
            settings=runtime_settings(cfg)
            self.assertEqual(settings['model_display_name'],'Invented-12B-IQ2_XS.gguf')
            self.assertEqual(settings['model'],cfg['model'])
            self.assertEqual(model_store.model_title('unlisted:tag'),'unlisted:tag')
            self.assertEqual(model_store.model_title(None),'')
            report=format_report({'issue':{'code':'unknown'},'settings':settings})
            self.assertIn('- 모델: Invented-12B-IQ2_XS.gguf',report)
            self.assertIn('- 모델 ID: rpt-local-example:latest',report)
            self.assertNotIn('private-folder',report)

    def test_incomplete_project_has_specific_issue_code(self):
        issue=classify_issue({'error_type':'ValueError','error_code':'incomplete_translation'})
        self.assertEqual(issue['code'],'incomplete_translation')
        self.assertIn('남아',issue['title'])


if __name__=='__main__':unittest.main()
