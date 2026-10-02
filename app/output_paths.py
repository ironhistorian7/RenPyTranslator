"""User-visible output names and portable per-project settings."""
import json
from pathlib import Path
import re
from app_paths import ROOT
from engine import save_json


def safe_name(value):
    value=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',value).strip().rstrip('. ')
    return value or 'Game'


def preferences(project):
    path=project/'data/tool-options.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def apply_options(project,cfg,output=None,suffix=None,textbox=None):
    opts=preferences(project)
    if output is not None:
        opts['output_root']=str(output)
    if suffix is not None:
        if re.search(r'[<>:"/\\|?*\x00-\x1f]',suffix):raise ValueError('Suffix must not contain path characters')
        opts['suffix']=suffix
    if textbox is not None:
        opts['textbox_scale']=None if str(textbox).lower()=='default' else float(textbox)
        if opts['textbox_scale'] is not None and not .25<=opts['textbox_scale']<=4:
            raise ValueError('Textbox scale must be between 0.25 and 4, or default')
    if any(v is not None for v in (output,suffix,textbox)):save_json(project/'data/tool-options.json',opts)
    return dict(cfg,**opts,_portable_output=True)


def destination(project,cfg):
    # Keep lower-level library callers/backward-compatible tests on their explicit workspace.
    if not cfg.get('_portable_output'):return project/'output',project.name+'-'+cfg['language']+'-local-draft'
    root=Path(cfg.get('output_root') or 'project')
    root_key=str(root)
    if not root.is_absolute():root=ROOT/root
    root=root.resolve()
    name=safe_name(Path(cfg.get('source') or project.name).name)
    if 'source' not in cfg:name=re.sub(r'-[a-f0-9]{10,12}$','',name)
    suffix=cfg.get('suffix','-kr')
    if suffix and name.endswith(suffix):name=name[:-len(suffix)]
    if cfg.get('comparison_label'):name += '-'+safe_name(cfg['comparison_label'])
    owner=project/'data/output-location.json'
    previous=json.loads(owner.read_text(encoding='utf-8')) if owner.exists() else {}
    base=name;number=1
    while True:
        label=safe_name(base+suffix);target=root/label
        if not target.exists() or previous.get('name')==label and previous.get('root') in (root_key,str(root)):break
        number+=1;base=name+'-'+str(number)
    for protected in (project/'staging',Path(cfg['source']) if cfg.get('source') else project/'staging'):
        resolved=protected.resolve()
        if target==resolved or target.is_relative_to(resolved) or resolved.is_relative_to(target):
            raise ValueError('Output must be separate from the source game and staging folder')
    # Store a relative root where possible so moving the portable folder retains names.
    save_json(owner,{'root':root_key,'name':label})
    return target,label
