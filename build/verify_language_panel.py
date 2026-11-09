"""Official local SDKs, invented screens only; no installed game or model access."""
from pathlib import Path
import ctypes
from ctypes import wintypes
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'app'))
from engine import interpreter_args
from language_panel import install

CHECK=r'''
init -1 python:
    config.screen_width = 1280
    config.screen_height = 720
    config.save_directory = None
    config.sound = False
    config.developer = False

translate korean strings:
    old "Panel test"
    new "패널 시험"

label test_anchor:
    return

init 1500 python:
    import renpy as _engine
    def _panel_check():
        import json
        def require(value, message):
            if not value: raise AssertionError(message)
        renpy.game.context().init_phase = False
        renpy.game.context().current = 'test_anchor'
        _engine.display.interface.start()
        _rpt_show_language_panel()
        screen = renpy.get_screen('_rpt_language_panel')
        require(screen is not None, 'panel absent')
        results=[]
        def draw():
            _engine.display.screen.updated_screens.clear()
            screen.update()
            screen.visit_all(lambda d: d.per_interact())
            _engine.display.render.process_redraws()
            return renpy.render(screen,config.screen_width,config.screen_height,0.0,0.0)
        def click_language(language):
            buttons=[]
            screen.visit_all(lambda d: buttons.append(d) if hasattr(d,'clicked') else None)
            button=next(d for d in buttons if isinstance(d.clicked, Language) and d.clicked.language==language)
            button.clicked()
        def activate_toggle(action):
            _engine.display.screen.push_current_screen(screen)
            try:action()
            finally:_engine.display.screen.pop_current_screen()
        for width,height in ((1280,720),(1920,1080),(800,600)):
            config.screen_width,config.screen_height=width,height
            for corner in ('right','left'):
                store._rpt_panel_corner=corner
                for expanded in (False,True):
                    screen.scope['expanded']=expanded
                    render=draw()
                    if width==1280 and corner=='right' and expanded and hasattr(renpy,'render_to_file'):
                        renpy.render_to_file(screen,config.basedir+'/panel-expanded.png',width=width,height=height)
                    require(not screen.modal,'panel blocks the game')
                    require(renpy.get_widget('_rpt_language_panel','rpt_language_toggle') is not None,'toggle absent')
                    if expanded:
                        for widget,language in (('rpt_language_korean','korean'),('rpt_language_original',None)):
                            button=renpy.get_widget('_rpt_language_panel',widget)
                            require(button is not None,'language button absent')
                            click_language(language)
                            require(_preferences.language==language,'wrong language')
                        Language('korean')()
                        require(_engine.translation.translate_string('Panel test')==u'패널 시험','translation not activated')
                        Language(None)()
                        require(_engine.translation.translate_string('Panel test')=='Panel test','original not restored')
                    results.append([width,height,corner,expanded])
        # Simulate the game's forced English action, then recover using the panel.
        Language(None)()
        _rpt_show_language_panel()
        require(renpy.get_screen('_rpt_language_panel') is screen,'panel state reset')
        screen.scope['expanded']=True
        draw()
        click_language('korean')
        require(_preferences.language=='korean','forced English recovery failed')
        toggles=[]
        screen.visit_all(lambda d: toggles.append(d.clicked) if hasattr(d,'clicked') and isinstance(d.clicked,ToggleScreenVariable) else None)
        require(len(toggles)==1,'one collapse control expected')
        activate_toggle(toggles[0])
        require(not screen.scope['expanded'],'collapse action failed')
        draw()
        toggles=[]
        screen.visit_all(lambda d: toggles.append(d.clicked) if hasattr(d,'clicked') and isinstance(d.clicked,ToggleScreenVariable) else None)
        activate_toggle(toggles[0])
        require(screen.scope['expanded'],'expand action failed')
        renpy.hide_screen('_rpt_language_panel')
        _rpt_show_language_panel()
        require(renpy.get_screen('_rpt_language_panel') is not None,'panel not restored')
        with open(config.basedir+'/panel-result.json','w') as f:json.dump(results,f)
        _engine.display.interface.bgscreenshot_needed=False
        print('LANGUAGE_PANEL_OK')
        return False
    _engine.arguments.register_command('rpt-panel-check',_panel_check)
'''


def main():
    sdkroot=ROOT/'build/renpy-validation';out=sdkroot/'language-panel-20260929'
    versions=sys.argv[1:] or ['7.3.5','7.4.11','7.5.3','7.6.0','7.7.3','7.8.7','8.0.3','8.1.0','8.2.3','8.3.7','8.4.1','8.5.3']
    user32=ctypes.WinDLL('user32',use_last_error=True)
    user32.CreateDesktopW.argtypes=[wintypes.LPCWSTR,wintypes.LPCWSTR,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p]
    user32.CreateDesktopW.restype=wintypes.HANDLE
    user32.CloseDesktop.argtypes=[wintypes.HANDLE]
    desktop=user32.CreateDesktopW('RPTLanguageTest',None,None,0,0x10000000,None)
    if not desktop:raise ctypes.WinError(ctypes.get_last_error())
    def run(version):
        sdk=sdkroot/('renpy-'+version+'-sdk');project=out/version;stage=project/'staging'
        install(project,{'language':'korean'})
        (stage/'game/script.rpy').write_text(CHECK,encoding='utf8')
        interpreter=sorted((sdk/'lib').glob('*windows*/python.exe'),key=lambda p:'x86_64' not in str(p))[0]
        env={k:v for k,v in os.environ.items() if k.upper() not in ('PYTHONHOME','PYTHONPATH','PYTHONOPTIMIZE')}
        env.update(SDL_VIDEODRIVER='windows',SDL_AUDIODRIVER='dummy',RENPY_RENDERER='sw',RENPY_SCALE_FACTOR='1',RENPY_PATH_TO_SAVES=str(project/'saves'),PYTHONDONTWRITEBYTECODE='1')
        startup=subprocess.STARTUPINFO();startup.lpDesktop='RPTLanguageTest'
        startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
        try:
            result=subprocess.run(interpreter_args(interpreter)+[str(sdk/'renpy.py'),str(stage),'rpt-panel-check'],
                cwd=stage,env=env,capture_output=True,timeout=40,creationflags=subprocess.CREATE_NO_WINDOW,startupinfo=startup)
            text=(result.stdout+result.stderr).decode('utf8','replace')
            passed=result.returncode==0 and 'LANGUAGE_PANEL_OK' in text
        except subprocess.TimeoutExpired:
            text='timeout';passed=False
        (project/'test-output.txt').write_text(text,encoding='utf8')
        print(version,'PASS' if passed else text[-3500:],flush=True)
        return dict(version=version,passed=passed)
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(run,versions))
    finally:user32.CloseDesktop(desktop)
    (out/'matrix.json').write_text(json.dumps(results,indent=2),encoding='utf8')
    if not all(r['passed'] for r in results):raise SystemExit(1)


if __name__=='__main__':main()
