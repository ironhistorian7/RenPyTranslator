"""Official SDKs, invented text only. No user games, AI, or visible game window."""
from pathlib import Path
import json
import os
import subprocess
import sys
import ctypes
from ctypes import wintypes
import atexit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT/'app'))
from engine import interpreter_args, save_json
from packaging import install_support
from story_hints import write_support
from verify_compat_matrix import checked_code

CHECK = r'''
init -1 python:
    config.screen_width = 1280
    config.screen_height = 720
    config.save_directory = None
    config.developer = False
    config.sound = False
    style.say_window.yminimum = 200
    if 'gui' in globals():
        gui.textbox_height = 200

style say_dialogue is default
style choice_button is button

label rpt_test_anchor:
    return

label rpt_invented_dialogue:
    "This is an invented translation test."
    menu:
        "Take the fictional path.":
            return
    return

label start:
    $ raise Exception('Story must never execute')

translate korean strings:
    old "Help"
    new "도와준다"
    old "Code?"
    new "암호?"

init 1500 python:
    import renpy as _engine
    def _rpt_699_check():
        import inspect, json
        if renpy.game.interface is None:
            _engine.display.core.Interface()
        if hasattr(_engine.display.interface, 'start'):
            _engine.display.interface.start()
        renpy.game.context().init_phase = False
        renpy.game.context().current = 'rpt_test_anchor'
        _engine.translation.change_language('korean')
        assert renpy.has_screen('_rpt_language_panel')
        assert renpy.has_screen('_rpt_hint_choice')
        _rpt_show_language_panel()
        assert renpy.get_screen('_rpt_language_panel') is not None
        panel = renpy.get_screen('_rpt_language_panel')
        panel.update()
        panel.visit_all(lambda d: d.per_interact())
        assert renpy.render(panel,1280,720,0.0,0.0).get_size()[0] > 0
        panel.scope['expanded'] = True
        panel.update()
        panel.visit_all(lambda d: d.per_interact())
        assert renpy.render(panel,1280,720,0.0,0.0).get_size()[0] > 0
        renpy.hide_screen('_rpt_language_panel')
        group = _rpt_font_group('DejaVuSans.ttf')
        assert any(font.endswith('NanumSquareNeo-Regular.ttf') for font, text in group.segment(u'ABC 가나다') if u'가' in text)
        assert any(font == 'DejaVuSans.ttf' for font, text in group.segment(u'ABC 가나다') if 'A' in text)
        checks = []
        def text_layout(text, width=600, size=40):
            displayable = _engine.text.text.Text(text, style='say_dialogue', font='DejaVuSans.ttf', size=size, slow=False)
            displayable.update()
            parameters = (getattr(inspect, 'getfullargspec', None) or inspect.getargspec)(_engine.text.text.Layout.__init__).args
            kwargs = dict((key, value) for key, value in [('size_only', True), ('drawable_res', False)] if key in parameters)
            return _engine.text.text.Layout(displayable, width, 10000, {}, **kwargs)
        for size in (28, 32, 40):
            layout = text_layout(u'한글 본문\n{rpt_ref=0.55}{cps=0}English reference{/cps}{/rpt_ref}', size=size)
            parts = [(seg.size, text) for line in layout.paragraphs for seg, text in line if hasattr(seg, 'size')]
            assert any(s == int(size*.55) and 'English' in text for s, text in parts), repr(parts)
            assert any(s == size and u'한글' in text for s, text in parts), repr(parts)
            checks.append(dict(base_size=size, reference_size=int(size*.55), layout=list(layout.size)))
        store._rpt_name_map['Mira'] = u'미라'
        name_layout = text_layout('Mira')
        assert any(u'미라' in text for line in name_layout.paragraphs for seg, text in line)
        assert rpt_josa(u'민수', u'은/는') == u'는'
        store.nickname = u'민수'
        assert renpy.substitute(u"[rpt_josa(nickname, u'은/는')]") == u'는'
        _rpt_apply_layout(True)
        assert _rpt_layout_owner.properties[-1]['yminimum'] == 240
        _rpt_apply_layout(False)
        assert _rpt_layout_owner is None
        filename, line = renpy.get_filename_line()
        store._rpt_hint_index['routes'] = {line:[dict(file=filename,line=line,source='Help',hints=[dict(text=u'신뢰도 +2',color='#66DD88',bold=False)])]}
        original_show = _engine.exports.show_screen
        captured = {}
        def show(screen, *args, **kwargs):
            shown = original_show(screen, *args, **kwargs)
            if screen == '_rpt_hint_choice':
                captured['items'] = kwargs['items']
                captured['layout'] = _rpt_hint_screen_layout(kwargs['items'])
                displayable = renpy.get_screen(screen)
                displayable.update()
                displayable.visit_all(lambda d: d.per_interact())
                assert renpy.render(displayable,1280,720,0.0,0.0).get_size()[0] > 0
            return shown
        _engine.exports.show_screen = show
        try:
            menu([('Help',17),('Disabled',None)], interact=False)
        finally:
            _engine.exports.show_screen = original_show
        assert 'layout' in captured, 'Detailed choice screen was not used'
        item = captured['items'][0]
        action = item.action if hasattr(item,'action') else item[1]
        assert action.value == 17
        assert u'신뢰도' in captured['layout']['rows'][0]['caption']
        assert _rpt_hint_session is None
        renpy.hide_screen('_rpt_hint_choice')
        store._rpt_hint_index['routes'][line][0]['hints'] = [dict(text=u'장면 설명 '*1000,color='#66DD88',bold=True,kind='scene')]
        _engine.exports.show_screen = show
        try:
            menu([('Help',17),('Disabled',None)], interact=False)
        finally:
            _engine.exports.show_screen = original_show
        assert captured['layout']['panel'] is not None
        a, b = captured['layout']['list'], captured['layout']['panel']
        assert a[0]+a[2] <= b[0] or a[1]+a[3] <= b[1]
        renpy.hide_screen('_rpt_hint_choice')
        store._rpt_hint_index['answers'] = {line:[dict(file=filename,line=line,source='Code?',values=['orchid'])]}
        seen = []
        assert _rpt_hint_input(lambda prompt: seen.append(prompt) or 'typed')('Code?') == 'typed'
        assert u'정답: orchid' in seen[0]
        _engine.translation.change_language(None)
        assert _preferences.language is None
        result = dict(version=_engine.version, checks=['generated patch compile/init','language panel','font groups','actual text layouts','particles/interpolation','textbox restore','native choice actions and hints','answer prompts','language switch'], text_layouts=checks, display_visible=False, user_game_opened=False)
        with open(config.basedir+'/result.json','w') as stream:json.dump(result,stream)
        print('RPT_699_COMPAT_OK')
        return False
    _engine.arguments.register_command('rpt-699-check', _rpt_699_check)
'''


def main():
    versions = sys.argv[1:] or ['6.99.0','6.99.10','6.99.11','6.99.14.3']
    results = []
    user32 = ctypes.WinDLL('user32',use_last_error=True)
    user32.CreateDesktopW.argtypes = [wintypes.LPCWSTR,wintypes.LPCWSTR,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p]
    user32.CreateDesktopW.restype = wintypes.HANDLE
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    desktop_name = 'RPT699Validation'+str(os.getpid())
    desktop = user32.CreateDesktopW(desktop_name,None,None,0,0x10000000,None)
    if not desktop:raise ctypes.WinError(ctypes.get_last_error())
    atexit.register(user32.CloseDesktop,desktop)
    startup = subprocess.STARTUPINFO();startup.lpDesktop = desktop_name
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow = 0
    for version in versions:
        sdk = ROOT/'build/renpy-validation'/('renpy-'+version+'-sdk')
        project = ROOT/'build/renpy-validation/compat-699'/version
        stage = project/'staging'; (stage/'game').mkdir(parents=True,exist_ok=True)
        (stage/'game/script.rpy').write_text(checked_code(CHECK),encoding='utf-8')
        save_json(project/'data/catalog.json',[])
        cfg = dict(language='korean', model='unused', _reference_guard=True, textbox_scale=1.2)
        install_support(project,cfg)
        write_support(project,cfg,{'answers':[],'routes':[]},stage)
        interpreters = sorted((sdk/'lib').glob('*windows*/python.exe'),key=lambda p:'x86_64' not in str(p))
        if not interpreters:raise RuntimeError('Missing SDK: '+str(sdk))
        env = {k:v for k,v in os.environ.items() if k.upper() not in ('PYTHONHOME','PYTHONPATH','PYTHONOPTIMIZE')}
        env.update(SDL_VIDEODRIVER='windows',SDL_AUDIODRIVER='dummy',RENPY_RENDERER='sw',RENPY_PATH_TO_SAVES=str(project/'saves'),PYTHONDONTWRITEBYTECODE='1')
        command_base = interpreter_args(interpreters[0])+[str(sdk/'renpy.py'),str(stage)]
        prepared = subprocess.run(command_base+['translate','korean','--empty'],cwd=stage,env=env,capture_output=True,timeout=45,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),startupinfo=startup)
        prepared_output = (prepared.stdout+prepared.stderr).decode('utf-8','replace')
        (project/'generate-output.txt').write_text(prepared_output,encoding='utf-8')
        template = stage/'game/tl/korean/script.rpy'
        if prepared.returncode or not template.is_file() or 'This is an invented translation test.' not in template.read_text(encoding='utf-8'):
            raise RuntimeError('Translation template generation failed: '+version+'\n'+prepared_output[-4000:])
        command = command_base+['rpt-699-check']
        proc = subprocess.run(command,cwd=stage,env=env,capture_output=True,timeout=45,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),startupinfo=startup)
        output = (proc.stdout+proc.stderr).decode('utf-8','replace')
        (project/'engine-output.txt').write_text(output,encoding='utf-8')
        result = dict(version=version,exitcode=proc.returncode,passed=proc.returncode==0 and 'RPT_699_COMPAT_OK' in output)
        results.append(result);save_json(ROOT/'build/renpy-validation/compat-699/report.json',results)
        print(version,proc.returncode,output[-6500:],flush=True)
        if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()
