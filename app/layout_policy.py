"""Independent textbox sizing; update only this tool's generated support files."""
import re
from app_paths import resource


def install(project,cfg):
    game=project/'staging/game';game.mkdir(parents=True,exist_ok=True)
    old=game/'zz_rpt_korean.rpy'
    if old.exists():
        text=old.read_text(encoding='utf-8')
        # Remove the exact legacy blocks emitted by this tool, not game styles.
        text=re.sub(r'^    _rpt_textbox_(?:before|height) = .*\n','',text,flags=re.M)
        text=re.sub(r'^    if _rpt_textbox_(?:before|height) is not None:\n        gui\.textbox_height = _rpt_textbox_(?:before|height)\n','',text,flags=re.M)
        text=re.sub(r'^translate \w+ style say_window:\n    ysize _rpt_textbox_height\n','',text,flags=re.M)
        if text!=old.read_text(encoding='utf-8'):old.write_text(text,encoding='utf-8')
    scale=cfg.get('textbox_scale')
    if scale is not None and not .25<=float(scale)<=4:raise ValueError('Textbox scale must be 0.25..4, or default')
    code='''# Generated textbox option; default retains the game's own style and position.
init 1050 python:
    _rpt_layout_scale = SCALE
HELPER

translate LANGUAGE python:
    _rpt_apply_layout(True)

translate None python:
    _rpt_apply_layout(False)
'''.replace('SCALE',repr(float(scale)) if scale is not None else 'None').replace('LANGUAGE',cfg['language'])
    helper=resource('layout_runtime.py').read_text(encoding='utf-8')
    code=code.replace('HELPER','\n'.join('    '+line for line in helper.splitlines()))
    (game/'zz_rpt_layout.rpy').write_text(code,encoding='utf-8')
    print('Textbox height: '+('game default' if scale is None else str(scale)+'x original'),flush=True)
