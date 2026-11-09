"""Synthetic launchers only. Never execute game/engine code."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from engine import find_launcher, engine_command, interpreter_args, select_windows_runtime, windows_architecture, play


OLD = '''def path_to_renpy_base():
    return "."
def main():
    import renpy.bootstrap
    renpy.bootstrap.bootstrap(path_to_renpy_base())
if __name__ == "__main__":
    main()
'''
NEW = 'def path_to_gamedir(basedir, name):\n    return basedir\n' + OLD


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, code):
        path = self.root / name
        path.write_text(code, encoding='utf-8')
        return path

    def test_old_without_path_to_gamedir(self):
        launcher = self.write('OldGame.py', OLD)
        (self.root / 'OldGame.exe').touch()
        self.assertEqual(find_launcher(self.root), launcher)

    def test_new_launcher(self):
        launcher = self.write('NewGame.py', NEW)
        self.assertEqual(find_launcher(self.root), launcher)

    def test_executable_pair_preferred_among_bootstrap_scripts(self):
        self.write('renpy.py', NEW)
        chosen = self.write('ActualGame.py', OLD)
        (self.root / 'ACTUALGAME.exe').touch()
        self.assertEqual(find_launcher(self.root), chosen)

    def test_single_launcher_without_matching_exe(self):
        launcher = self.write('UnusualName.py', OLD)
        (self.root / 'Play.exe').touch()
        self.assertEqual(find_launcher(self.root), launcher)

    def test_comment_and_string_markers_are_not_launchers(self):
        self.write('helper.py', '# path_to_gamedir\n# renpy.bootstrap.bootstrap(root)\ns = "renpy.bootstrap.bootstrap(root)"\n')
        (self.root / 'helper.exe').touch()
        launcher = self.write('Actual.py', OLD)
        self.assertEqual(find_launcher(self.root), launcher)

    def test_python2_encoding_and_print_syntax_do_not_require_python3_ast(self):
        launcher = self.root / 'Legacy.py'
        launcher.write_bytes(('# coding: cp1252\nprint "caf\u00e9"\n' + OLD).encode('cp1252'))
        self.assertEqual(find_launcher(self.root), launcher)

    def test_from_import_bootstrap(self):
        launcher = self.write('Game.py', 'from renpy import bootstrap\nbootstrap.bootstrap(".")\n')
        self.assertEqual(find_launcher(self.root), launcher)

    def test_missing_and_ambiguous_candidates_explain_failure(self):
        with self.assertRaisesRegex(ValueError, r'Python files checked: \(none\)'):
            find_launcher(self.root)
        self.write('One.py', OLD)
        self.write('Two.py', NEW)
        with self.assertRaisesRegex(ValueError, 'Multiple RenPy launchers.*One.py, Two.py'):
            find_launcher(self.root)

    def test_unreadable_helper_does_not_hide_valid_launcher(self):
        (self.root / 'helper.py').write_bytes(b'\xff\xfeinvalid')
        launcher = self.write('Game.py', OLD)
        self.assertEqual(find_launcher(self.root), launcher)

    def test_engine_command_uses_old_launcher_and_existing_bundled_python(self):
        project = self.root / 'project'
        stage = project / 'staging'
        runtime = stage / 'lib/windows-i686/python.exe'
        runtime.parent.mkdir(parents=True)
        runtime.touch()
        launcher = stage / 'Game.py'
        launcher.write_text(OLD, encoding='utf-8')
        (project / 'data').mkdir()
        with patch('engine.owned_run', return_value=subprocess.CompletedProcess([], 0)) as run:
            log = engine_command(project, ['translate', 'korean', '--empty'], 'generate.log')
        self.assertEqual(run.call_args.args[0], [str(runtime), '-B', str(launcher), str(stage),
                                               'translate', 'korean', '--empty'])
        self.assertEqual(log, project / 'data/generate.log')

    def test_pyo_stdlib_adds_optimization_before_launcher(self):
        project = self.root / 'optimized'
        stage = project / 'staging'
        runtime = stage / 'lib/windows-i686/python.exe'
        (runtime.parent / 'Lib').mkdir(parents=True)
        runtime.touch()
        (runtime.parent / 'Lib/site.pyo').touch()
        launcher = stage / 'Game.py'
        launcher.write_text(OLD, encoding='utf-8')
        (project / 'data').mkdir()
        with patch('engine.owned_run', return_value=subprocess.CompletedProcess([], 0)) as run:
            engine_command(project, ['translate', 'korean', '--empty'], 'generate.log')
        self.assertEqual(run.call_args.args[0], [str(runtime), '-B', '-O', str(launcher), str(stage),
                                               'translate', 'korean', '--empty'])

    def test_stdlib_variants_and_source_bytecode_distributions(self):
        for location in ('windows-i686/Lib', 'pythonlib2.7', 'python2.7'):
            with self.subTest(location=location), tempfile.TemporaryDirectory() as temp:
                library = Path(temp) / 'lib'
                interpreter = library / 'windows-i686/python.exe'
                stdlib = library / location
                stdlib.mkdir(parents=True)
                # Source and ordinary bytecode do not force optimization.
                for filename in ('site.py', 'site.pyc'):
                    (stdlib / filename).touch()
                self.assertEqual(interpreter_args(interpreter), [str(interpreter), '-B'])
                (stdlib / 'site.pyo').touch()
                self.assertEqual(interpreter_args(interpreter), [str(interpreter), '-B', '-O'])

    def test_modern_python_and_unrelated_pyo_do_not_change_options(self):
        interpreter = self.root / 'lib/py3-windows-x86_64/python.exe'
        (self.root / 'game').mkdir()
        (self.root / 'game/site.pyo').touch()
        self.assertEqual(interpreter_args(interpreter), [str(interpreter), '-B'])

    def runtime(self,directory,root=None):
        path=(root or self.root)/'lib'/directory/'python.exe'
        path.parent.mkdir(parents=True,exist_ok=True)
        path.touch()
        return path

    def test_dual_architecture_distribution_uses_64_bit_on_64_bit_windows(self):
        self.runtime('windows-i686')
        x64=self.runtime('windows-x86_64')
        with patch('engine.windows_architecture',return_value='x86_64'):
            self.assertEqual(select_windows_runtime(self.root),x64)

    def test_dual_architecture_distribution_uses_32_bit_on_32_bit_windows(self):
        self.runtime('windows-x86_64')
        x86=self.runtime('windows-i686')
        with patch('engine.windows_architecture',return_value='x86'):
            self.assertEqual(select_windows_runtime(self.root),x86)

    def test_32_bit_only_remains_usable_on_64_bit_windows(self):
        x86=self.runtime('windows-i686')
        with patch('engine.windows_architecture',return_value='x86_64'):
            self.assertEqual(select_windows_runtime(self.root),x86)

    def test_modern_py3_directory_and_creation_order_do_not_change_selection(self):
        x64=self.runtime('py3-windows-x86_64')
        self.runtime('py3-windows-i686')
        with patch('engine.windows_architecture',return_value='x86_64'):
            self.assertEqual(select_windows_runtime(self.root),x64)

    def test_windows_os_architecture_not_python_process_bitness(self):
        with patch.dict('os.environ',{'PROCESSOR_ARCHITEW6432':'AMD64','PROCESSOR_ARCHITECTURE':'x86'},clear=True),patch('engine.platform.machine',return_value='x86'):
            self.assertEqual(windows_architecture(),'x86_64')
        with patch.dict('os.environ',{'PROCESSOR_ARCHITECTURE':'x86'},clear=True),patch('engine.platform.machine',return_value='AMD64'):
            self.assertEqual(windows_architecture(),'x86')
        with patch.dict('os.environ',{},clear=True),patch('engine.platform.machine',return_value='AMD64'):
            self.assertEqual(windows_architecture(),'x86_64')

    def test_missing_and_incompatible_runtime_errors_explain_candidates(self):
        with self.assertRaisesRegex(ValueError,'No bundled Windows runtime'):
            select_windows_runtime(self.root)
        self.runtime('windows-x86_64')
        with patch('engine.windows_architecture',return_value='x86'),self.assertRaisesRegex(ValueError,'No matching.*x86:.*windows-x86_64'):
            select_windows_runtime(self.root)

    def test_single_nonstandard_runtime_preserves_previous_behavior(self):
        chosen=self.runtime('windows')
        self.assertEqual(select_windows_runtime(self.root),chosen)

    def test_same_architecture_different_python_families_are_not_picked_arbitrarily(self):
        self.runtime('windows-x86_64')
        self.runtime('py3-windows-x86_64')
        with patch('engine.windows_architecture',return_value='x86_64'),self.assertRaisesRegex(ValueError,'Multiple bundled Windows runtimes for x86_64'):
            select_windows_runtime(self.root)

    def test_native_arm64_is_not_selected_for_x64_windows(self):
        self.runtime('windows-arm64')
        x64=self.runtime('windows-x86_64')
        with patch('engine.windows_architecture',return_value='x86_64'):
            self.assertEqual(select_windows_runtime(self.root),x64)

    def test_engine_commands_share_selection_and_keep_pyo_options(self):
        project=self.root/'project';stage=project/'staging'
        self.runtime('windows-i686',stage)
        x64=self.runtime('windows-x86_64',stage)
        (x64.parent/'Lib').mkdir();(x64.parent/'Lib/site.pyo').touch()
        (stage/'Game.py').write_text(OLD,encoding='utf-8')
        (project/'data').mkdir()
        with patch('engine.windows_architecture',return_value='x86_64'),patch('engine.owned_run',return_value=subprocess.CompletedProcess([],0)) as run:
            for args in (['translate','korean','--empty'],['compile'],['lint']):
                engine_command(project,args,'fixture.log')
                command=run.call_args.args[0]
                self.assertEqual(command[:3],[str(x64),'-B','-O'])
                self.assertEqual(command[-len(args):],args)

    def test_explicit_play_uses_same_selector_without_launching_a_game(self):
        project=self.root/'project';stage=project/'staging'
        self.runtime('windows-i686',stage)
        x64=self.runtime('windows-x86_64',stage)
        (stage/'Game.py').write_text(OLD,encoding='utf-8')
        (project/'data').mkdir()
        with patch('engine.windows_architecture',return_value='x86_64'),patch('engine.subprocess.Popen') as process:
            play(project)
            self.assertEqual(process.call_args.args[0][0],str(x64))


if __name__ == '__main__':
    unittest.main()
