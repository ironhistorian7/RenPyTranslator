"""Whole generated patch on official SDKs, with invented text only; no story/model."""
from pathlib import Path
import json
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'app'))
from engine import interpreter_args, save_json
from packaging import install_support
from story_hints import write_support

CHECK = r'''
init -1 python:
    config.screen_width = 1280
    config.screen_height = 720
    config.save_directory = None
    config.developer = False
    config.sound = False
    gui.textbox_height = 200

style say_window is window
style say_dialogue is default
style gui_text is default
style choice_button is button

init 1500 python:
    import renpy as _engine
    def _rpt_validation_command():
        import json
        _preferences.language = 'korean'
        _rpt_language_fonts()
        assert preferences.font_transform == 'rpt_korean'
        group = _rpt_transform('DejaVuSans.ttf')
        segments = list(group.segment(u'ABC 가나다'))
        assert any(font.endswith('NanumSquareNeo-Regular.ttf') for font, text in segments if u'가' in text)
        assert any(font == 'DejaVuSans.ttf' for font, text in segments if u'A' in text)
        # Actually exercise both historical Text method signatures.
        before = config.replace_text
        config.replace_text = lambda s: s.replace('India', u'인디아')
        try:
            text = _engine.text.text.Text('')
            tokens = [(renpy.TEXT_TEXT, 'India'), (renpy.TEXT_TAG, 'rpt_ref'),
                      (renpy.TEXT_TEXT, 'India'), (renpy.TEXT_TAG, '/rpt_ref')]
            assert text.apply_custom_tags(tokens) == [(renpy.TEXT_TEXT, u'인디아'), (renpy.TEXT_TEXT, 'India')]
        finally:
            config.replace_text = before
        checks = []
        for style_name, base_size in (('say_dialogue',40), ('centered_text',32), ('choice_button_text',28)):
            for spelling in ('new', 'legacy', 'wrong'):
                reference = ('{rpt_ref=0.55}{cps=0}English reference{/cps}{/rpt_ref}' if spelling == 'new' else
                             '{size=' + ('*0.55' if spelling == 'legacy' else '99') + '}{cps=0}{rpt_ref}English reference{/rpt_ref}{/cps}{/size}')
                caption = u'한글 본문\n' + reference
                displayable = _engine.text.text.Text(caption, style=style_name, font='DejaVuSans.ttf', size=base_size, slow=False)
                displayable.update()
                layout = _engine.text.text.Layout(displayable, 600, 10000, {}, size_only=True, drawable_res=False)
                parts = [(seg.size, part) for line in layout.paragraphs for seg, part in line if hasattr(seg, 'size')]
                assert any(size == int(base_size * .55) and 'English' in part for size, part in parts), repr(parts)
                assert any(size == base_size and u'한글' in part for size, part in parts), repr(parts)
                assert layout.size[0] > 0 and layout.size[1] > 0
                checks.append(dict(style=style_name, spelling=spelling, base=base_size, reference=int(base_size*.55), layout=list(layout.size)))
        assert rpt_josa(u'민수', u'은/는') == u'는'
        assert rpt_josa(u'서연', u'은/는') == u'은'
        assert callable(_rpt_hint_screen_layout)
        assert renpy.has_screen('_rpt_hint_choice')
        store._rpt_layout_scale = 1.2
        _rpt_apply_layout(True)
        assert gui.textbox_height == 240
        _rpt_apply_layout(False)
        assert gui.textbox_height == 200
        _preferences.language = None
        _rpt_language_fonts()
        assert _rpt_transform('DejaVuSans.ttf') == 'DejaVuSans.ttf'
        with open(config.basedir + '/engine-result.json', 'w') as f:
            json.dump(dict(version=_engine.version, check='actual text layout and glyph generation', text_layouts=checks, rendered=False), f)
        print('RPT_PATCH_COMPAT_OK')
        return False
    _engine.arguments.register_command('rpt-patch-check', _rpt_validation_command)

label start:
    $ raise Exception('Story must never execute')
'''

for version in ('7.3.5', '7.4.11', '7.5.3', '7.6.0', '7.8.7', '8.0.3', '8.1.0', '8.5.3'):
    sdk = ROOT / 'build/renpy-validation' / ('renpy-' + version + '-sdk')
    project = ROOT / 'build/renpy-validation' / ('patch-' + version)
    stage = project / 'staging'
    game = stage / 'game'
    game.mkdir(parents=True, exist_ok=True)
    (game / 'script.rpy').write_text(CHECK, encoding='utf-8')
    save_json(project / 'data/catalog.json', [])
    cfg = dict(language='korean', model='unused', _reference_guard=True)
    install_support(project, cfg)
    write_support(project, cfg, {'answers': [], 'routes': []}, stage)
    interpreters = sorted((sdk / 'lib').glob('*windows*/python.exe'), key=lambda p: 'x86_64' not in str(p))
    env = dict(os.environ, SDL_VIDEODRIVER='dummy', SDL_AUDIODRIVER='dummy', RENPY_RENDERER='sw',
               RENPY_PATH_TO_SAVES=str(project / 'saves'), PYTHONDONTWRITEBYTECODE='1')
    command = interpreter_args(interpreters[0]) + [str(sdk / 'renpy.py'), str(stage), 'rpt-patch-check']
    result = subprocess.run(command, cwd=stage, env=env, capture_output=True, timeout=45,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    output = (result.stdout + result.stderr).decode('utf-8', 'replace')
    (project / 'validation-output.txt').write_text(output, encoding='utf-8')
    print(version, 'exit', result.returncode, output[-7000:], flush=True)
    if result.returncode or 'RPT_PATCH_COMPAT_OK' not in output:
        raise SystemExit(1)
