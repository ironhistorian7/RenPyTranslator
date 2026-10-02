"""Real SDK choice-screen rendering in SDL dummy mode, synthetic fixtures only."""
from pathlib import Path
import json
import os
import subprocess
import sys
import ctypes
from ctypes import wintypes
from verify_compat_matrix import ROOT, SDKROOT, OUT, constant, checked_code, interpreter_args

code=constant(ROOT/'build/verify_hint_engines.py','CHECK')
start=code.index('        if sys.version_info[0] == 2:')
end=code.index('        _engine.display.interface.start()',start)
code=code[:start]+code[end:]
code=code.replace("        _engine.display.interface.start()", "        renpy.game.context().init_phase = False\n        renpy.game.context().current = 'rpt_test_anchor'\n        _preferences.language = 'korean'\n        _engine.display.interface.start()")
code=code.replace("    _engine.arguments.register_command('rpt-layout-check',_rpt_validation_command,uses_display=False)", "    _engine.arguments.register_command('rpt-layout-check',_rpt_render_validation_command)")
code=code.replace('def _rpt_validation_command():','def _rpt_render_validation_command():')
code=code.replace('init -1 python:','init -3 python:').replace('init 1500 python:','init 1700 python:')
code=code[:code.index('\nlabel start:')]
code=code.replace("'/engine-result.json'", "'/render-result.json'")
code=code.replace("                    pygame.image.save(final_render.pygame_surface(True),config.basedir+'/layout-%dx%d.png' % (width,height))", """                    surface = final_render.pygame_surface(True)
                    if not isinstance(surface, pygame.Surface):
                        surface = _engine.display.draw.screenshot(final_render)
                    assert len(set(tuple(surface.get_at((x,y))) for x in range(0,surface.get_width(),7) for y in range(0,surface.get_height(),7))) > 16, 'Blank render capture'
                    pygame.image.save(surface,config.basedir+'/layout-%dx%d.png' % (width,height))""")
code=checked_code(code).replace('init -2 python:','init -4 python:')

versions=sys.argv[1:] or ['7.3.5','7.4.11','7.5.3','7.6.0','7.7.3','7.8.7','8.0.3','8.1.0','8.2.3','8.3.7','8.4.1','8.5.3']
matrix=OUT/'render-matrix.json'
results=[r for r in json.loads(matrix.read_text(encoding='utf-8')) if r['version'] not in versions] if matrix.exists() else []
user32=ctypes.WinDLL('user32',use_last_error=True)
user32.CreateDesktopW.argtypes=[wintypes.LPCWSTR,wintypes.LPCWSTR,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p]
user32.CreateDesktopW.restype=wintypes.HANDLE
user32.CloseDesktop.argtypes=[wintypes.HANDLE]
desktop_name='RPTCompatibilityTest'
desktop=user32.CreateDesktopW(desktop_name,None,None,0,0x10000000,None)
if not desktop:raise ctypes.WinError(ctypes.get_last_error())
startup=subprocess.STARTUPINFO()
startup.lpDesktop=desktop_name
startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW
startup.wShowWindow=0
for version in versions:
    sdk=SDKROOT/('renpy-'+version+'-sdk')
    project=OUT/version
    stage=project/'staging'
    (stage/'game/render_check.rpy').write_text(code,encoding='utf-8')
    interpreters=sorted((sdk/'lib').glob('*windows*/python.exe'),key=lambda p:'x86_64' not in str(p))
    env={k:v for k,v in os.environ.items() if k.upper() not in ('PYTHONHOME','PYTHONPATH','PYTHONOPTIMIZE')}
    env.update(SDL_VIDEODRIVER='windows',SDL_AUDIODRIVER='dummy',RENPY_RENDERER='sw',RENPY_SCALE_FACTOR='1',RENPY_PATH_TO_SAVES=str(project/'saves'),PYTHONDONTWRITEBYTECODE='1')
    command=interpreter_args(interpreters[0])+[str(sdk/'renpy.py'),str(stage),'rpt-layout-check']
    try:
        p=subprocess.run(command,cwd=stage,env=env,capture_output=True,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW,startupinfo=startup)
        output=(p.stdout+p.stderr).decode('utf-8','replace')
        status=p.returncode
    except subprocess.TimeoutExpired as e:
        output=((e.stdout or b'')+(e.stderr or b'')).decode('utf-8','replace')+'\nTIMEOUT'
        status='timeout'
    (project/'render-output.txt').write_text(output,encoding='utf-8')
    entry=dict(version=version,exitcode=status,passed=status==0 and 'RPT_HEADLESS_LAYOUT_OK' in output)
    rp=stage/'render-result.json'
    if rp.exists():entry['layouts']=json.loads(rp.read_text(encoding='utf-8'))
    results.append(entry)
    (OUT/'render-matrix.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    print(version,status,output[-4500:],flush=True)
user32.CloseDesktop(desktop)
