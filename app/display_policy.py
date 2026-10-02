"""Install reference separation without loading a model or installing fonts."""
import json
from app_paths import resource
from engine import save_json


def install(project,cfg,refresh_choices=True):
    game=project/'staging/game';game.mkdir(parents=True,exist_ok=True)
    helper=resource('size_runtime.py').read_text(encoding='utf-8')+'\n'+resource('reference_runtime.py').read_text(encoding='utf-8')
    code='# Original-language reference isolation.\ninit 1200 python:\n'+''.join('    '+s+'\n' for s in helper.splitlines())
    (game/'zz_rpt_reference.rpy').write_text(code,encoding='utf-8')
    from name_translation import install_runtime
    install_runtime(project,cfg)
    path=game/'tl'/cfg['language']/'_rpt_presentation.json'
    if path.exists() and refresh_choices:
        from translation import read_catalog,display_metadata
        from replacements import effective_cache
        from display_text import compose
        rows=read_catalog(project);known=effective_cache(project,cfg,apply_ui=False)
        metadata=display_metadata(project,dict(cfg,_reference_guard=True),rows)
        data=json.loads(path.read_text(encoding='utf-8'))
        for row in rows:
            if row['kind']=='string' and row['source'] in data.get('choices',{}) and row['id'] in known:
                data['choices'][row['source']]=compose(dict(row,kind='dialogue'),known[row['id']],metadata)
        save_json(path,data)
