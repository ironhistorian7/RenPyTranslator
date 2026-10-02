# Shared Python/Tk bundle for GUI and console entrypoints.
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules
root=Path(SPECPATH).parent
app=root/'app'
a=Analysis([str(app/'portable_cli.py')],pathex=[str(app),str(root/'vendor/unrpyc')],
           binaries=[],datas=[(str(app/n),'.') for n in ('josa_runtime.py','presentation_runtime.py','reference_runtime.py','size_runtime.py','hints_runtime.py','hints_conditions.py','hints_layout.py','hints_screen.rpy','layout_runtime.py','names_runtime.py','language_panel.rpy','workspace_runtime.py')],
           hiddenimports=['gui','unrpyc','deobfuscate']+collect_submodules('fontTools')+collect_submodules('decompiler'),
           hookspath=[],runtime_hooks=[],excludes=['pytest','IPython','numpy','matplotlib'])
pyz=PYZ(a.pure)
cli=EXE(pyz,a.scripts,[],exclude_binaries=True,name='RenPyTranslator-cli',console=True,upx=False)
gui=EXE(pyz,a.scripts,[],exclude_binaries=True,name='RenPyTranslator',console=False,upx=False)
COLLECT(cli,gui,a.binaries,a.datas,strip=False,upx=False,name='RenPyTranslator')
