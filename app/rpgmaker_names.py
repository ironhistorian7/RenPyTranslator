"""Share confirmed actor spellings with proven RPG Maker name-label slots only."""
from collections import defaultdict
import json
from pathlib import Path
import re


def _slot(row):
    location=row.get('location',{})
    if location.get('fmt')!='json':return None
    from gameloc_vendor import load
    load()
    from gameloc.treepath import parse_path
    try:parts=parse_path(location.get('ptr',''))
    except ValueError:return None
    path=Path(str(row.get('file',location.get('file',''))).replace('\\','/'))
    if (path.parent.name.lower()=='data' and path.name.lower()=='actors.json'
            and len(parts)==2 and isinstance(parts[0],int) and parts[1]=='name'):
        return 'actor'
    if row.get('usage')!='speaker_label' or len(parts)<2 or parts[-2]!='parameters':return None
    code=row.get('rpg_command_code')
    if (code==101 and parts[-1]==4) or (code==320 and parts[-1]==1):return 'speaker'
    if code is None and parts[-1] in (1,4):return 'missing_evidence'
    return None


def _sources(row):
    return {value for value in (row.get('source'),row.get('base_source'),*row.get('source_aliases',[]))
            if isinstance(value,str) and value.strip()}


def _overrides(values):
    if not isinstance(values,dict):raise ValueError('names.json overrides must map names to plain text')
    for source,target in values.items():
        if (not isinstance(source,str) or not isinstance(target,str) or not target.strip()
                or any(char in target for char in '{}[]\r\n')):
            raise ValueError('names.json overrides must map names to plain text')
    return values


def align_names(rows,known,overrides=None):
    """Return an in-memory overlay; no source, ID, cache or dialogue is changed."""
    from label_policy import expansion_errors
    overrides=_overrides({} if overrides is None else overrides)
    candidates=defaultdict(lambda:defaultdict(set));all_sources=set();conflicted_sources=set()
    slots={row['id']:_slot(row) for row in rows}
    report=dict(actor_names=0,authoritative_actor_names=0,updated_ids=[],
                ambiguous_sources=[],untranslated_actor_ids=[],override_conflict_actor_ids=[],
                missing_command_evidence_ids=[],canonical={},cache_modified=False)
    for row in rows:
        uid=row['id']
        if slots[uid]!='actor':continue
        report['actor_names']+=1
        sources=_sources(row);all_sources.update(sources)
        item=known.get(uid,{})
        target=item.get('text')
        usable=(item.get('status') not in ('source_fallback','ui_original')
                and isinstance(target,str) and re.search('[가-힣]',target)
                and not re.search(r'[\u3041-\u3096\u30a1-\u30fa\uff66-\uff9d{}\[\]\\\r\n]',target)
                and not expansion_errors(dict(row,usage='speaker_label'),target))
        explicit={overrides[source] for source in sources if source in overrides}
        if len(explicit)==1:target=next(iter(explicit));usable=True
        elif len(explicit)>1:
            report['override_conflict_actor_ids'].append(uid);conflicted_sources.update(sources);continue
        if not usable:
            report['untranslated_actor_ids'].append(uid);continue
        report['authoritative_actor_names']+=1
        for source in sources:candidates[source][target].add(uid)
    # Manual spellings apply only to names that an actor record actually defines.
    for source,target in overrides.items():
        if source in all_sources and source not in conflicted_sources:candidates[source]={target:set()}
    for source in conflicted_sources:candidates.pop(source,None)
    canonical={source:next(iter(values)) for source,values in candidates.items() if len(values)==1}
    report['canonical']=canonical
    report['ambiguous_sources']=sorted(source for source,values in candidates.items() if len(values)>1)
    effective=dict(known)
    for row in rows:
        uid=row['id'];slot=slots[uid]
        if slot=='missing_evidence':report['missing_command_evidence_ids'].append(uid)
        if slot not in ('actor','speaker'):continue
        target=canonical.get(row['source'])
        if target is None:continue
        previous=known.get(uid)
        if previous and previous.get('text')==target and previous.get('status') not in ('source_fallback','ui_original'):
            continue
        effective[uid]=dict(previous or {},id=uid,source=row['source'],text=target,
                            status='name_alignment',error=None)
        report['updated_ids'].append(uid)
    return effective,report


def apply_names(project,cfg,rows,known):
    """Read optional existing names.json overrides; the translation cache is input."""
    path=Path(project)/'names.json'
    overrides=json.loads(path.read_text(encoding='utf-8-sig')).get('overrides',{}) if path.is_file() else {}
    return align_names(rows,known,overrides)
