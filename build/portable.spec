# Shared Python/Tk bundle for GUI and console entrypoints.
from pathlib import Path
import runpy
from PyInstaller.utils.hooks import collect_submodules
root=Path(SPECPATH).parent
app=root/'app'
windows_resource=runpy.run_path(str(app/'app_version.py'))['windows_resource']
version_file=root/'build'/'version-resource.txt'
version_file.write_text(windows_resource(root),encoding='utf-8')
a=Analysis([str(app/'portable_cli.py')],pathex=[str(app),str(root/'vendor/unrpyc'),str(root/'vendor/gameloc')],
           binaries=[],datas=[(str(app/n),'.') for n in ('gpu_profiles.json','rpgmaker_bilingual.js','josa_runtime.py','presentation_runtime.py','reference_runtime.py','size_runtime.py','hints_runtime.py','hints_conditions.py','hints_layout.py','hints_screen.rpy','layout_runtime.py','names_runtime.py','language_panel.rpy','workspace_runtime.py','validation_probe_runtime.py')],
           hiddenimports=['gui','unrpyc','deobfuscate']+collect_submodules('fontTools')+collect_submodules('decompiler'),
           hookspath=[],runtime_hooks=[],excludes=['pytest','IPython','numpy','matplotlib'])
pyz=PYZ(a.pure)
cli=EXE(pyz,a.scripts,[],exclude_binaries=True,name='RenPyTranslator-cli',console=True,upx=False,version=str(version_file))
gui=EXE(pyz,a.scripts,[],exclude_binaries=True,name='RenPyTranslator',console=False,upx=False,version=str(version_file))
COLLECT(cli,gui,a.binaries,a.datas,strip=False,upx=False,name='RenPyTranslator')
