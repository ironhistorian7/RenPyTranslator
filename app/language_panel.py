"""Install/export the small language panel without opening scripts or translations."""
import json
import os
import shutil
import zipfile
from app_paths import ROOT,resource
from engine import save_json

FILE='zz_rpt_language.rpy'
FONT='NanumSquareNeo-Regular.ttf'
LICENSE='NanumSquareNeo-OFL.txt'


def options(project,cfg,corner=None,margin=None):
    from output_paths import preferences
    opts=preferences(project)
    if corner is not None:
        if corner not in ('left','right'):raise ValueError('Panel corner must be left or right')
        opts['language_corner']=corner
    if margin is not None:
        margin=int(margin)
        if not 0<=margin<=300:raise ValueError('Panel margin must be 0..300')
        opts['language_margin']=margin
    if corner is not None or margin is not None:save_json(project/'data/tool-options.json',opts)
    return dict(cfg,**{k:v for k,v in opts.items() if k.startswith('language_')})


def install(project,cfg):
    game=project/'staging/game';game.mkdir(parents=True,exist_ok=True)
    lang=cfg['language'];fontpath='tl/'+lang+'/fonts/'+FONT
    files=[game/FILE]
    for name in (FONT,LICENSE):
        target=game/'tl'/lang/'fonts'/name
        if not target.exists():
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(ROOT/'vendor/fonts'/name,target)
        files.append(target)
    code=resource('language_panel.rpy').read_text(encoding='utf-8')
    for marker,value in [('LANGUAGE_VALUE',lang),('CORNER_VALUE',cfg.get('language_corner','right')),
                         ('MARGIN_VALUE',cfg.get('language_margin',12)),('FONT_VALUE',fontpath)]:
        code=code.replace(marker,json.dumps(value,ensure_ascii=False))
    if not files[0].exists() or files[0].read_text(encoding='utf-8')!=code:
        files[0].write_text(code,encoding='utf-8')
    return files


def export(project,cfg):
    from output_paths import destination
    files=install(project,cfg)
    output,label=destination(project,cfg);output.mkdir(parents=True,exist_ok=True)
    stage=project/'staging'
    target=output/(label+'-language-panel.zip')
    note='언어 패널 적용: 이 압축의 game 폴더를 게임의 game 폴더와 합치세요.\n하단 구석 화살표를 누르면 한국어 / 원문을 선택할 수 있습니다.\n기존 번역 파일은 변경하지 않습니다. 한국어 번역 패치가 먼저 적용되어 있어야 합니다.\n'
    with zipfile.ZipFile(target.with_suffix('.zip.tmp'),'w',zipfile.ZIP_DEFLATED) as z:
        for path in files:
            rel=path.relative_to(stage);dest=output/rel
            dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest)
            z.write(path,rel.as_posix())
        z.writestr('언어 패널 적용방법.txt',note.encode('utf-8-sig'))
    target.with_suffix('.zip.tmp').replace(target)
    (output/'언어 패널 적용방법.txt').write_text(note,encoding='utf-8-sig')
    save_json(output/'project-link.json',{'project':os.path.relpath(project,output)})
    print('Language panel only: no translation, model or game execution',flush=True)
    print('Output folder: '+str(output),flush=True)
    print(target,flush=True)
