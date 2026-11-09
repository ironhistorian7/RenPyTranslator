"""Official SDKs + invented fixture only. Dummy display; no game/story/model run."""
from pathlib import Path
import json
import os
import subprocess
import sys

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'app'))
from story_hints import write_support
from display_policy import install as install_reference

CHECK=r'''
init -1 python:
    config.screen_width = 1280
    config.screen_height = 720
    config.save_directory = None
    config.developer = False
    config.sound = False

init 1500 python:
    import renpy as _engine
    def _rpt_validation_command():
        import json
        import sys
        if sys.version_info[0] == 2:
            # SDL2 bundled with 7.8.7 cannot create a dummy window on this host.
            # Validate the actual Python 2 init and screen compilation; no fake renderer.
            assert renpy.has_screen('_rpt_hint_choice')
            assert callable(_rpt_hint_screen_layout)
            with open(config.basedir + '/engine-result.json','w') as f:
                json.dump(dict(check='compile and init',rendered=False),f)
            print('RPT_HEADLESS_LAYOUT_OK')
            return False
        _engine.display.interface.start()
        checks = []
        for width, height, count, long_hint in ((1280,720,2,False),(1920,1080,6,True),(800,600,12,True)):
            config.screen_width, config.screen_height = width, height
            entries = []
            items = []
            for i in range(count):
                caption = u'선택지 %d: 함께 이야기한다\n{rpt_ref=0.55}{cps=0}Talk together %d{/cps}{/rpt_ref}' % (i,i)
                effects = [dict(text=u'신뢰도 +2',color='#66DD88',bold=False)]
                if long_hint:
                    effects += [dict(text=(u'장면: 비밀을 듣고 다음 여행을 약속함 ' * 30) + u' (아주 긴 조건 그리고 조건일 때)',color='#66DD88',bold=True)] * 5
                entries.append(dict(file='fiction.rpy',line=1,source=caption,hints=effects))
                items.append((caption,i))
            _rpt_hint_index['routes'] = {1:entries}
            old_location = store._rpt_hint_location
            store._rpt_hint_location = lambda: ('fiction.rpy',1)
            try:
                def previous(menu_items, **kwargs):
                    renpy.display_menu(menu_items,interact=False)
                    screen = renpy.get_screen('_rpt_hint_choice')
                    assert screen is not None
                    screen.update()
                    screen.visit_all(lambda d: d.per_interact())
                    rendered = renpy.render(screen,width,height,0.0,0.0)
                    layout = store._rpt_hint_session['layout']
                    viewport = renpy.get_widget('_rpt_hint_choice','rpt_hint_choices')
                    assert viewport.width > layout['text_width']
                    assert viewport.height > 0
                    assert layout['list'][0] >= 0 and layout['list'][1] >= 0
                    assert layout['list'][0]+layout['list'][2] <= width
                    assert layout['list'][1]+layout['list'][3] <= height
                    if layout['panel']:
                        a,b=layout['list'],layout['panel']
                        assert a[0]+a[2] <= b[0] or a[1]+a[3] <= b[1]
                        assert b[0]+b[2] <= width and b[1]+b[3] <= height
                    assert len(screen.scope['items']) == count
                    for i,item in enumerate(screen.scope['items']):
                        assert item.action.value == i
                    last_button = renpy.get_widget('_rpt_hint_choice','rpt_hint_choice_%d' % (count-1))
                    last_button.focus()
                    assert store._rpt_hint_session['focus'] == count-1
                    assert u'신뢰도 +2' in _rpt_hint_detail(count-1)
                    _engine.display.screen.updated_screens.clear()
                    screen.update()
                    screen.visit_all(lambda d: d.per_interact())
                    _engine.display.render.process_redraws()
                    final_render = renpy.render(screen,width,height,0.0,0.0)
                    assert str(count-1) in _rpt_hint_detail(store._rpt_hint_session['focus'])
                    if layout['content_height'] > layout['list'][3]:
                        assert store._rpt_hint_session['list_adjustment'].value > 0
                    import pygame_sdl2 as pygame
                    pygame.image.save(final_render.pygame_surface(True),config.basedir+'/layout-%dx%d.png' % (width,height))
                    checks.append(dict(width=width,height=height,choices=count,panel=bool(layout['panel']),content_height=layout['content_height']))
                    renpy.hide_screen('_rpt_hint_choice')
                    return 'preserved'
                assert _rpt_hint_menu(previous)(items) == 'preserved'
                assert store._rpt_hint_session is None
            finally:
                store._rpt_hint_location = old_location
        path = config.basedir + '/engine-result.json'
        with open(path,'w') as f:json.dump(checks,f)
        print('RPT_HEADLESS_LAYOUT_OK')
        return False
    _engine.arguments.register_command('rpt-layout-check',_rpt_validation_command,uses_display=False)

label start:
    return
'''


for version,py in (('8.5.3','py3'),('7.8.7','py2')):
    sdk=ROOT/'build/renpy-validation'/('renpy-'+version+'-sdk')
    project=ROOT/'build/renpy-validation'/('hint-render-'+version)
    fixture=project/'staging'
    (fixture/'game').mkdir(parents=True,exist_ok=True)
    install_reference(project,{'language':'korean'},refresh_choices=False)
    write_support(fixture,{'language':'korean'},{'answers':[],'routes':[]},fixture)
    (fixture/'game/script.rpy').write_text(CHECK,encoding='utf-8')
    env=dict(os.environ,SDL_VIDEODRIVER='dummy',SDL_AUDIODRIVER='dummy',RENPY_RENDERER='sw',RENPY_SCALE_FACTOR='1',PYTHONDONTWRITEBYTECODE='1')
    command=[str(sdk/'lib'/(py+'-windows-x86_64')/'python.exe'),'-B',str(sdk/'renpy.py'),str(fixture),'rpt-layout-check']
    result=subprocess.run(command,cwd=fixture,env=env,capture_output=True,timeout=60,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    output=(result.stdout+result.stderr).decode('utf-8','replace')
    (fixture/'validation-output.txt').write_text(output,encoding='utf-8')
    print(version, 'exit',result.returncode,output[-7000:],flush=True)
    if result.returncode or 'RPT_HEADLESS_LAYOUT_OK' not in output:raise SystemExit(1)
    print((fixture/'engine-result.json').read_text(encoding='utf-8'),flush=True)
