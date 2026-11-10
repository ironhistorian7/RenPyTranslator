"""Bind an explicitly selected source locale without changing saved entry IDs."""
import hashlib
import json
from pathlib import Path
import re

from engine import save_json
from rpgmaker_language import discover_tables, prepare_sources


def text_usage(row):
    """Only proven message roles and standard database labels are label-shaped."""
    usage=row.get('usage')
    if row.get('kind')=='dialogue' or usage=='dialogue':return 'dialogue'
    if usage in ('choice','speaker_label'):return usage
    path=Path(str(row.get('file','')).replace('\\','/'))
    if path.parent.name.lower()!='data':return 'text'
    filename=path.name.lower()
    database={'actors.json','armors.json','classes.json','enemies.json','items.json',
              'skills.json','states.json','weapons.json'}
    if filename not in database|{'system.json'} and not re.fullmatch(r'map\d+\.json',filename):
        return 'text'
    from gameloc_vendor import load
    load()
    from gameloc.treepath import parse_path
    try:parts=parse_path(row.get('location',{}).get('ptr',''))
    except ValueError:return 'text'
    if filename=='system.json':
        if parts in (['gameTitle'],['currencyUnit']):return 'ui_label'
        if len(parts)==2 and parts[0] in ('armorTypes','elements','equipTypes','skillTypes','weaponTypes') and isinstance(parts[1],int):
            return 'ui_label'
        if len(parts)==3 and parts[:2] in (['terms','commands'],['terms','basic'],['terms','params']):
            return 'ui_label'
    elif filename in database and len(parts)==2 and isinstance(parts[0],int) and parts[1]=='name':
        return 'speaker_label' if filename=='actors.json' else 'ui_label'
    elif re.fullmatch(r'map\d+\.json',filename) and parts==['displayName']:
        return 'ui_label'
    return 'text'


def refresh_usages(rows,snapshot=None):
    """Correct catalog purpose metadata only; source, locations and IDs are intact."""
    changed=0;documents={}
    for row in rows:
        usage=text_usage(row)
        if row.get('usage')!=usage:row['usage']=usage;changed+=1
        if usage!='speaker_label' or 'rpg_command_code' in row or snapshot is None:continue
        from gameloc_vendor import load
        load()
        from gameloc.treepath import parse_path,get_by_path,format_path
        relative=Path(str(row.get('file','')).replace('\\','/'))
        if relative.is_absolute() or '..' in relative.parts:continue
        try:
            parts=parse_path(row.get('location',{}).get('ptr',''))
            if 'parameters' not in parts:continue
            if relative not in documents:
                documents[relative]=json.loads((Path(snapshot)/relative).read_text(encoding='utf-8-sig'))
            command=get_by_path(documents[relative],format_path(parts[:parts.index('parameters')]))
            if isinstance(command,dict) and isinstance(command.get('code'),int):
                row['rpg_command_code']=command['code'];changed+=1
        except (OSError,ValueError,KeyError,IndexError,TypeError):continue
    return changed


def copy_declared_tables(source, snapshot, base, manifest):
    """Copy only declared missing language tables into the existing text snapshot."""
    source=Path(source).resolve();snapshot=Path(snapshot)
    game=(source/base).resolve()
    if not game.is_relative_to(source):raise ValueError('Invalid RPG Maker language data root')
    copied=0
    for table in discover_tables(game,issues=[]):
        original=(game/table['file']).resolve()
        if not original.is_relative_to(game) or not original.is_file():continue
        relative=original.relative_to(source).as_posix()
        target=snapshot/relative
        if target.exists():continue
        raw=original.read_bytes()
        if raw.startswith(b'\xef\xbb\xbf'):raw=raw[3:]
        target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        manifest[relative]=hashlib.sha256(raw).hexdigest();copied+=1
    return copied


def bind_rows(rows, mapping):
    changed=0;bound=0
    for row in rows:
        before=dict(row);base=row.get('base_source',row['source'])
        selected=mapping.resolve(base)
        if selected:
            row['base_source']=base;row['source']=selected['text']
            row['source_aliases']=mapping.runtime_aliases(base)
            row['source_mapping']={'language':selected['language'],'provenance':selected['provenance']}
            bound+=1
        elif 'base_source' in row:
            row['source']=base;row.pop('source_mapping',None)
        changed+=row!=before
    return {'changed':changed,'bound_entries':bound,'total_entries':len(rows)}


def refresh(project, cfg, *, allow_original=False):
    project=Path(project);snapshot=project/'data/rpg-source'
    catalog=project/'data/catalog.json';manifestfile=project/'data/source-manifest.json'
    manifest=json.loads(manifestfile.read_text(encoding='utf-8'))
    copied=0
    if allow_original:
        copied=copy_declared_tables(cfg['source'],snapshot,Path(cfg.get('rpg_base','.')),manifest)
        if copied:save_json(manifestfile,manifest)
    mapping=prepare_sources(snapshot,cfg['source_language'],base=cfg.get('rpg_base','.'),files=manifest.keys())
    rows=json.loads(catalog.read_text(encoding='utf-8'))
    result=bind_rows(rows,mapping)
    usage_updates=refresh_usages(rows,snapshot)
    if result['changed'] or usage_updates:save_json(catalog,rows)
    report=dict(mapping.report(),**result,copied_tables=copied,entry_ids_preserved=True,
                translation_cache_modified=False,usage_updates=usage_updates)
    save_json(project/'data/rpg-source-language.json',report)
    if result['bound_entries']:
        print('Source language tables: '+str(result['bound_entries'])+' entries linked',flush=True)
    if report.get('issues'):
        print('Source language table details: data/rpg-source-language.json',flush=True)
    return report
