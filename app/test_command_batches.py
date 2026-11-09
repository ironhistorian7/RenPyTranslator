"""Synthetic paths/fixtures only; no game scripts, engine or model execution."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from engine import command_batches, command_line_units, prepare, WINDOWS_COMMAND_BUDGET


class CommandBatchTests(unittest.TestCase):
    def test_short_commands_and_empty_input(self):
        prefix=['portable-cli.exe','--unrpyc','-p','1']
        self.assertEqual(command_batches(prefix,['a.rpyc','b.rpymc']),[prefix+['a.rpyc','b.rpymc']])
        self.assertEqual(command_batches(prefix,[]),[])

    def test_large_lists_fit_for_frozen_and_source_mode_without_reordering(self):
        paths=['E:/Example workspace/game/nested/'+'segment_'*8+str(n)+'.rpyc' for n in range(397)]
        for prefix in ([r'E:\Portable tool\RenPyTranslator-cli.exe','--unrpyc','-p','1'],
                       [r'E:\개발 폴더\.venv\Scripts\python.exe','-B','-X','utf8',r'E:\개발 폴더\app\portable_cli.py','--unrpyc','-p','1']):
            with self.subTest(prefix=prefix):
                self.assertGreater(command_line_units(prefix+paths),32767)
                batches=command_batches(prefix,paths)
                self.assertGreater(len(batches),1)
                self.assertEqual([p for batch in batches for p in batch[len(prefix):]],paths)
                self.assertTrue(all(command_line_units(batch)<=WINDOWS_COMMAND_BUDGET for batch in batches))

    def test_quoting_non_bmp_and_exact_limit(self):
        prefix=['C:\\한글 폴더\\😀\\tool.exe','--unrpyc']
        args=['a file.rpyc','quotes"inside.rpyc','trailing space\\','😀'*12+'.rpyc','',r'normal\path.rpyc']
        combined=subprocess.list2cmdline(prefix+args)
        exact=len(combined.encode('utf-16-le'))//2+1
        self.assertGreater(exact,len(combined)+1)
        self.assertEqual(command_line_units(prefix+args),exact)
        self.assertEqual(command_batches(prefix,args,limit=exact),[prefix+args])
        batches=command_batches(prefix,args,limit=exact-1)
        self.assertGreater(len(batches),1)
        self.assertEqual([p for batch in batches for p in batch[len(prefix):]],args)
        self.assertTrue(all(command_line_units(batch)<exact for batch in batches))

    def test_single_argument_and_prefix_overflow_are_explicit(self):
        prefix=['tool.exe']
        with self.assertRaisesRegex(ValueError,'prefix'):
            command_batches(['x'*100],['a'],limit=20)
        with self.assertRaisesRegex(ValueError,'Recovery path cannot fit'):
            command_batches(prefix,['x'*100],limit=30)

    def test_surrogate_file_names_can_be_measured(self):
        args=['tool.exe','unpaired_\ud800.rpyc']
        self.assertEqual(command_line_units(args),len(subprocess.list2cmdline(args).encode('utf-16-le','surrogatepass'))//2+1)


class RecoveryResumeTests(unittest.TestCase):
    def test_partial_failure_is_logged_and_successful_files_are_skipped_on_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';project=root/'project';game=project/'staging/game'
            (source/'game').mkdir(parents=True);(source/'renpy').mkdir()
            game.mkdir(parents=True);data=project/'data';data.mkdir()
            (data/'source-manifest.json').write_text('{}',encoding='utf-8')
            (data/'workspace-files.json').write_text(json.dumps({'status':'ready'}),encoding='utf-8')
            compiled=[]
            for n in range(397):
                p=game/('invented_long_filename_'+'a'*65+f'_{n:03}.rpyc')
                p.touch();compiled.append(p)
            existing=game/'already_recovered.rpyc';existing.touch();existing.with_suffix('.rpy').write_text('# saved fixture',encoding='utf-8')
            prefix=['portable-cli.exe','--unrpyc','-p','1']
            calls=[]
            def recover(command,**kwargs):
                calls.append(command)
                evidence=(data/'decompile.log').read_text(encoding='utf-8')
                self.assertIn('Script recovery batch',evidence)
                self.assertIn('UTF-16 units',evidence)
                self.assertIn('Command:',evidence)
                if len(calls)==2:raise OSError(206,'fixture launch failure')
                for name in command[len(prefix):]:Path(name).with_suffix('.rpy').write_text('# recovered fixture',encoding='utf-8')
                return subprocess.CompletedProcess(command,0)
            cfg={'source':str(source),'language':'korean'}
            with contextlib.redirect_stdout(io.StringIO()),patch('engine.verify_source'),patch('engine.cli_command',return_value=prefix[:-3]),patch('engine.engine_command') as engine_command,patch('engine.owned_run',side_effect=recover):
                with self.assertRaises(OSError):prepare(project,cfg)
                self.assertEqual(len(calls),2)
                self.assertFalse(engine_command.called)
                first=set(calls[0][len(prefix):])
                self.assertTrue(all(Path(name).with_suffix('.rpy').exists() for name in first))
                resumed=[]
                def finish(command,**kwargs):
                    resumed.extend(command[len(prefix):])
                    for name in command[len(prefix):]:Path(name).with_suffix('.rpy').write_text('# recovered fixture',encoding='utf-8')
                    return subprocess.CompletedProcess(command,0)
                with patch('engine.owned_run',side_effect=finish):prepare(project,cfg)
                self.assertTrue(engine_command.called)
                self.assertTrue(first.isdisjoint(resumed))
                self.assertNotIn(str(existing),resumed)
                self.assertEqual(len(first)+len(resumed),len(compiled))
                self.assertTrue(all(p.with_suffix('.rpy').exists() for p in compiled))


if __name__=='__main__':unittest.main()
