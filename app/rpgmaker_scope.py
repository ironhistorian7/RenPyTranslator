"""Static conversation scopes for RPG Maker only; never execute event commands."""
import hashlib
import json
from pathlib import Path

from engine import save_json

VERSION = 2
# Built-in housekeeping, pictures, audio, waits and simple variable conditions.
# Unknown/plugin/script commands and story interactions are not display helpers.
HELPER_CODES = frozenset((0,108,408,111,411,412,117,121,122,123,
                         221,222,223,224,225,230,231,232,233,234,235,236,
                         241,242,243,244,245,246,249,250,251))
BOUNDARIES = frozenset((102,103,104,111,112,113,115,118,119,201,202,203,214,
                       301,302,303,355,356,357,402,403,404,411,412,413))


class Helpers:
    def __init__(self, events):
        self.events = {e['id']:e for e in events if e}
        self.result = {}

    def check(self, uid, visiting=()):
        if uid in self.result:return self.result[uid]['helper']
        if uid in visiting or uid not in self.events:return False
        if len(visiting)>=32:return False
        reason='built-in display/state helper; returns without story interaction'
        allowed=True
        for command in self.events[uid].get('list',[]):
            code=command.get('code');args=command.get('parameters',[])
            if code not in HELPER_CODES:
                allowed=False;reason='story or unclassified command '+str(code);break
            if (code==111 and args and args[0]==12) or (code==122 and len(args)>3 and args[3]==4):
                allowed=False;reason='script expression';break
            if code==117 and (not args or not self.check(args[0],visiting+(uid,))):
                allowed=False;reason='nested call is not a known returning display helper';break
        self.result[uid]={'helper':allowed,'reason':reason}
        return allowed

    def barrier(self, command):
        code=command.get('code')
        if code==117:
            args=command.get('parameters',[])
            return not args or not self.check(args[0])
        return code in BOUNDARIES


def refresh(project,cfg):
    """Update metadata only. IDs, source text, locations and translations stay intact."""
    from gameloc_vendor import load
    load()
    from gameloc.treepath import parse_path,get_by_path,format_path
    project=Path(project);catalog=project/'data/catalog.json'
    raw=catalog.read_bytes();digest=hashlib.sha256(raw).hexdigest()
    auditfile=project/'data/rpg-context-scopes.json'
    if auditfile.exists():
        old=json.loads(auditfile.read_text(encoding='utf-8'))
        if old.get('version')==VERSION and old.get('catalog_sha256')==digest:return old
    snapshot=project/'data/rpg-source'
    base=(snapshot/str(cfg.get('rpg_base','.'))).resolve()
    if not base.is_relative_to(snapshot.resolve()):raise ValueError('Invalid RPG Maker data root')
    common=base/'data/CommonEvents.json'
    helpers=Helpers(json.loads(common.read_text(encoding='utf-8-sig')) if common.is_file() else [])
    rows=json.loads(raw);docs={};lists={};changed=0
    for row in rows:
        if 'location' not in row:continue
        parts=parse_path(row['location']['ptr'])
        if 'parameters' not in parts:continue
        pos=parts.index('parameters');head=parts[:pos]
        if not head or not isinstance(head[-1],int):continue
        rel=row['file'];path=(snapshot/rel).resolve()
        if not path.is_relative_to(snapshot.resolve()):raise ValueError('Invalid RPG Maker metadata path')
        if rel not in docs:docs[rel]=json.loads(path.read_text(encoding='utf-8-sig'))
        listptr=format_path(head[:-1]);key=(rel,listptr)
        if key not in lists:
            commands=get_by_path(docs[rel],listptr);scopes=[];boundary=0
            for i,command in enumerate(commands):
                if helpers.barrier(command):boundary=i
                scopes.append(boundary)
            lists[key]=(commands,scopes)
        commands,scopes=lists[key];index=head[-1]
        block=listptr+':'+str(scopes[index])+':'+str(commands[index].get('indent',0))
        if row.get('block')!=block:row['block']=block;changed+=1
    if changed:save_json(catalog,rows)
    audit={'version':VERSION,'rows':len(rows),'changed_blocks':changed,
           'ids_and_sources_preserved':True,'translation_cache_deleted':False,
           'catalog_sha256':hashlib.sha256(catalog.read_bytes()).hexdigest(),
           'common_events':{str(k):v for k,v in sorted(helpers.result.items())}}
    save_json(auditfile,audit)
    print(f'RPG Maker conversation scopes: {changed} metadata updates; IDs and translation cache retained',flush=True)
    return audit
