"""Source-to-existing-project routing using temporary synthetic metadata only."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from automatic import source_key
from engine import save_json
from task_plan import FIXES, arguments, plan
import translate_game as cli


class SourceRepairTests(unittest.TestCase):
    def fixture(self, root, *, registered=True, prepared=True, cached=True):
        source=root/'Invented original game'
        (source/'game').mkdir(parents=True);(source/'renpy').mkdir()
        project=root/'projects/Existing'
        (project/'data').mkdir(parents=True)
        save_json(project/'project.json',{'source':str(source),'language':'korean',
                                        'source_language':'english','model':'synthetic',
                                        'automatic_version':1})
        if prepared:
            save_json(project/'data/catalog.json',[])
            (project/'data/templates').mkdir();(project/'staging/game').mkdir(parents=True)
        if cached:
            # The route must not need to parse a saved translation to locate it.
            (project/'data/translations.jsonl').write_bytes(b'synthetic preserved cache sentinel\n')
        if registered:
            save_json(root/'projects/index.json',{source_key(source.resolve()):project.name})
        return source,project

    @contextlib.contextmanager
    def no_fallback(self, root):
        # Keep the initial entrypoint callable while detecting recursive run.
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch('automatic.ROOT',root))
            stack.enter_context(patch('model_store.require_selected',return_value='synthetic'))
            stack.enter_context(patch('diagnostics.stage'))
            stack.enter_context(patch('diagnostics.report'))
            for name in ('translate_game.main','translate_game.prepare','translate_game.translate',
                         'translate_game.rebuild','translate_game.model_session',
                         'retranslation.comparison','translation.request','hy_backend.runtime'):
                stack.enter_context(patch(name,side_effect=AssertionError('Unexpected fallback: '+name)))
            yield stack.enter_context(patch('translate_game.repair'))

    def test_every_source_fix_routes_only_to_the_registered_project(self):
        entrypoint=cli.main
        for fix in sorted(FIXES):
            with self.subTest(fix=fix),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);source,project=self.fixture(root)
                cachefile=project/'data/translations.jsonl';before=cachefile.read_bytes()
                with self.no_fallback(root) as repair,contextlib.redirect_stdout(io.StringIO()):
                    entrypoint(arguments([fix],str(source),source=True))
                repair.assert_called_once()
                self.assertEqual(repair.call_args.args[0],project)
                self.assertEqual(repair.call_args.args[2],[fix])
                self.assertEqual(cachefile.read_bytes(),before)

    def test_source_fix_combinations_keep_the_existing_task_normalization(self):
        entrypoint=cli.main
        selected=['failed','names','font','display','layout']
        self.assertEqual(plan(selected),['failed','names','font'])
        self.assertEqual(plan(['display','layout']),['display'])
        self.assertEqual(plan(['run',*selected]),['run'])
        self.assertEqual(plan(['retranslate',*selected]),['retranslate'])
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,project=self.fixture(root)
            with self.no_fallback(root) as repair,contextlib.redirect_stdout(io.StringIO()):
                entrypoint(arguments(selected,str(source),source=True))
            repair.assert_called_once()
            self.assertEqual(repair.call_args.args[0],project)
            self.assertEqual(repair.call_args.args[2],['failed','names','font'])

    def test_missing_preparation_or_cache_stops_each_source_fix(self):
        entrypoint=cli.main
        for fix in sorted(FIXES):
            for prepared,cached,expected in ((False,True,'기존 번역 작업'),
                                             (True,False,'기존 번역 기록')):
                with self.subTest(fix=fix,prepared=prepared,cached=cached),tempfile.TemporaryDirectory() as temp:
                    root=Path(temp);source,_=self.fixture(root,prepared=prepared,cached=cached)
                    with self.no_fallback(root) as repair,contextlib.redirect_stdout(io.StringIO()), \
                            contextlib.redirect_stderr(io.StringIO()) as errors:
                        with self.assertRaises(SystemExit) as caught:
                            entrypoint(arguments([fix],str(source),source=True))
                        self.assertEqual(caught.exception.code,2)
                    self.assertIn(expected,errors.getvalue())
                    repair.assert_not_called()

    def test_unregistered_source_does_not_trigger_an_ordinary_translation(self):
        entrypoint=cli.main
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source,_=self.fixture(root,registered=False)
            with self.no_fallback(root) as repair,contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()) as errors:
                with self.assertRaises(SystemExit) as caught:
                    entrypoint(arguments(['failed'],str(source),source=True))
                self.assertEqual(caught.exception.code,2)
            self.assertIn('기존 번역 작업을 찾지 못했습니다',errors.getvalue())
            repair.assert_not_called()


if __name__=='__main__':unittest.main()
