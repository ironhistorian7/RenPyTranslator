"""Share confirmed actor spellings with proven RPG Maker name-label slots only."""
from collections import defaultdict
from functools import lru_cache
import json
from pathlib import Path
import re


def _slot(row):
    location=row.get('location',{})
    if location.get('fmt')!='json':return None
    path=Path(str(row.get('file',location.get('file',''))).replace('\\','/'))
    actor=path.parent.name.lower()=='data' and path.name.lower()=='actors.json'
    if not actor and row.get('usage')!='speaker_label':return None
    from gameloc_vendor import load
    load()
    from gameloc.treepath import parse_path
    try:parts=parse_path(location.get('ptr',''))
    except ValueError:return None
    if (actor
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


def _plain_name(value):
    return (isinstance(value,str) and bool(value.strip())
            and any(char.isalpha() for char in value)
            and not any(char in value for char in '{}[]\\\r\n'))


def validation_config(cfg,rows,known=None):
    """Share schema-confirmed names, including originals, across every language check."""
    if cfg.get('engine')!='rpgmaker':return cfg
    from label_policy import expansion_errors
    known=known or {};groups=[]
    for row in rows:
        if _slot(row) not in ('actor','speaker'):continue
        forms={value.strip() for value in _sources(row) if _plain_name(value)}
        saved=known.get(row['id'],{});target=saved.get('text')
        if (_plain_name(target) and saved.get('status') not in ('source_fallback','ui_original')
                and not expansion_errors(dict(row,usage='speaker_label'),target)):
            forms.add(target.strip())
        if forms:groups.append(tuple(sorted(forms)))
    # A small direct batch may not contain the Actor rows loaded by its caller.
    groups.extend(tuple(group) for group in cfg.get('_rpg_name_groups',()))
    return dict(cfg,_rpg_name_groups=tuple(sorted(set(groups))))


def _edge(char):
    if re.fullmatch(r'[\u3400-\u9fff\uf900-\ufaff]',char):return r'[\u3400-\u9fff\uf900-\ufaff]'
    if re.fullmatch(r'[\u30a1-\u30fa\uff66-\uff9dー]',char):return r'[\u30a1-\u30fa\uff66-\uff9dー]'
    if re.fullmatch(r'[\u3041-\u3096]',char):return r'[\u3041-\u3096]'
    return r'[A-Za-zÀ-ÖØ-öø-ÿ0-9_]'


@lru_cache(maxsize=512)
def _pattern(names):
    parts=[]
    for name in sorted(names,key=lambda value:(-len(value),value)):
        # Source Japanese honorifics establish a name boundary, but remain
        # visible to language validation when the model leaves them untranslated.
        honorific=r'さん|ちゃん|くん|さま|様|君|先生|先輩|殿'
        if re.fullmatch(r'[\u3400-\u9fff\uf900-\ufaff]',name[-1]):
            # A Han-to-hiragana transition can be a verb stem, not a name.
            # Permit noun particles/honorifics, not arbitrary inflection tails.
            right=r'(?:(?![\u3400-\u9fff\uf900-\ufaff\u3041-\u3096])|(?='+honorific+r'|が|は|を|に|へ|と|も|で|の|や|だ|から|まで|より|だけ|たち|ら))'
        else:right=r'(?:(?!'+_edge(name[-1])+r')|(?='+honorific+r'))'
        parts.append(r'(?<!'+_edge(name[0])+')'+re.escape(name)+right)
    return re.compile('|'.join(parts) if parts else r'(?!)',re.I)


@lru_cache(maxsize=128)
def _index(groups):
    by_form=defaultdict(set)
    for group in groups:
        for form in group:by_form[form.casefold()].update(group)
    return _pattern(tuple(sorted({form for group in groups for form in group}))),by_form


def name_only(source,cfg):
    groups=tuple(tuple(group) for group in (cfg or {}).get('_rpg_name_groups',()))
    pattern,_=_index(groups)
    return bool(pattern.fullmatch(source.strip()))


def mask_names(source,text,cfg):
    """Replace only confirmed name spans by spaces, keeping all text offsets."""
    groups=tuple(tuple(group) for group in (cfg or {}).get('_rpg_name_groups',()))
    pattern,index=_index(groups)
    if pattern.fullmatch(source.strip()):return ' '*len(text)
    names=set()
    for match in pattern.finditer(source):names.update(index.get(match[0].casefold(),()))
    if not names:return text
    return _pattern(tuple(sorted(names))).sub(lambda match:' '*len(match[0]),text)


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
                and _plain_name(target)
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
    effective,report=align_names(rows,known,overrides)
    from name_review import overlay,configuration
    effective,review=overlay(project,rows,effective,engine='rpgmaker')
    cfg.update(configuration(project,cfg,rows))
    report['name_review']=review
    return effective,report
