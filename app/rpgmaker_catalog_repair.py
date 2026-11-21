"""Restore display locations dropped by legacy global identifier filtering.

The caller owns metadata persistence and cache commits. These helpers only read
the prepared snapshot and mutate supplied catalog/project objects in memory.
"""
from collections import defaultdict, deque
import hashlib
import json
from pathlib import Path

from gameloc_vendor import load


def _file(value):
    return str(value).replace('\\','/')


def _address(location, pointer=None):
    from gameloc.treepath import format_path, parse_path
    value=location.ptr if pointer is None else pointer
    try:value=format_path(parse_path(value))
    except ValueError:pass
    return _file(location.file),value


def _row(entry, snapshot, documents, line):
    from gameloc.treepath import parse_path, get_by_path, format_path
    location=entry.locations[0];parts=parse_path(location.ptr)
    row={'id':entry.id,'source':entry.text,'kind':'dialogue' if location.span else 'string',
         'usage':'dialogue' if location.span else 'text','file':location.file,'block':'labels',
         'speaker':'unknown','source_file':location.file,'source_line':line,
         'location':location.to_dict()}
    if 'parameters' in parts:
        pos=parts.index('parameters');head=parts[:pos];relative=_file(location.file)
        if relative not in documents:documents[relative]=json.loads((snapshot/relative).read_text(encoding='utf-8-sig'))
        command=get_by_path(documents[relative],format_path(head));code=command['code']
        commands=get_by_path(documents[relative],format_path(head[:-1]));index=head[-1]
        markers=[i for i,c in enumerate(commands[:index+1]) if c.get('code') in (101,102,105,111,117,118,119,402,403,411,412)]
        boundaries=[i for i in markers if commands[i].get('code') not in (101,105)]
        row['block']=format_path(head[:-1])+':'+str(boundaries[-1] if boundaries else 0)+':'+str(command.get('indent',0))
        headers=[i for i in markers if commands[i].get('code')==101]
        if headers:
            parameters=commands[headers[-1]].get('parameters',[])
            if len(parameters)>4:row['speaker']=parameters[4] or 'unknown'
        if code==102:row['usage']='choice'
        elif code in (320,324) or code==101 and parts[-1]==4:row['usage']='speaker_label'
        row['rpg_command_code']=code
    elif Path(location.file).name=='Actors.json' and parts[-1]=='name':
        row['usage']='speaker_label'
    return row


def recover_display_entries(project, cfg, rows, upstream, *, only_values=None):
    """Append missing proven display entries, preserving every existing ID.

    Call before the ordinary source-locale binding refresh. No cache is read or
    written and no model is invoked. The returned IDs identify only new rows.
    ``only_values`` optionally limits a targeted repair by exact original text.
    """
    load()
    from gameloc.engines import rpgmaker
    from gameloc.model import Entry
    project=Path(project);snapshot=project/'data/rpg-source'
    base=rpgmaker.is_rpgmaker(snapshot)
    report={'added':0,'added_ids':[],'files':[],'existing_ids_preserved':True,
            'cache_modified':False,'scope':'standard_display_identifier_collisions'}
    if base is None:return report
    reserved=set(upstream.identifiers)
    if only_values is not None:reserved.intersection_update(only_values)
    if not reserved:return report
    manifest=json.loads((project/'data/source-manifest.json').read_text(encoding='utf-8-sig'))
    allowed={_file(name) for name in manifest}
    occupied={_address(loc,ptr) for entry in upstream.entries for loc in entry.locations
              for ptr in [loc.ptr,*(loc.span or [])]}
    occupied.update((_file(row['file']),row.get('location',{}).get('ptr','')) for row in rows)
    ids={row['id'] for row in rows}|{entry.id for entry in upstream.entries}
    candidates=rpgmaker.extract_entries(base,snapshot)
    ranks={_address(location):index for index,(_text,location) in enumerate(candidates)}
    documents={};new_by_file=defaultdict(list);new_entries=[]
    last_line=max((row.get('source_line',0) for row in rows if isinstance(row.get('source_line'),int)),default=0)
    for text,location in candidates:
        if text not in reserved or _file(location.file) not in allowed:continue
        addresses={_address(location,pointer) for pointer in [location.ptr,*(location.span or [])]}
        if occupied & addresses or not rpgmaker.is_display_location(snapshot,location,documents):continue
        uid=hashlib.sha256((location.file+'\0'+location.ptr+'\0'+text).encode()).hexdigest()[:16]
        if uid in ids:continue
        entry=Entry(id=uid,text=text,locations=[location])
        row=_row(entry,snapshot,documents,last_line+len(new_entries)+1)
        new_entries.append(entry);new_by_file[_file(location.file)].append((ranks[_address(location)],row))
        report['added_ids'].append(uid);occupied.update(addresses);ids.add(uid)
    if not new_entries:return report
    # Insert into each original file's extraction order without reordering old
    # rows or changing their source-line identity.
    pending={name:deque(items) for name,items in new_by_file.items()}
    last={_file(row['file']):index for index,row in enumerate(rows)}
    merged=[]
    for index,row in enumerate(rows):
        name=_file(row['file']);queue=pending.get(name)
        rank=ranks.get((name,row.get('location',{}).get('ptr','')))
        while queue and rank is not None and queue[0][0]<rank:merged.append(queue.popleft()[1])
        merged.append(row)
        if index==last[name] and queue:
            merged.extend(value for _rank,value in queue);queue.clear()
    for queue in pending.values():merged.extend(value for _rank,value in queue)
    rows[:]=merged;upstream.entries.extend(new_entries)
    report.update(added=len(new_entries),files=sorted(new_by_file))
    return report


def reusable_display_records(rows, known, added_ids):
    """Reuse an unambiguous validated spelling after normal locale binding.

    Successful existing records take precedence. Conflicting translations and
    speaker-name/non-name collisions remain for the caller's missing-item path.
    """
    wanted=set(added_ids);by_source=defaultdict(list)
    for row in rows:
        item=known.get(row['id'])
        if (item is None or row['id'] in wanted or item.get('_rpg_residual_language')
                or item.get('status') in ('source_fallback','ui_original')
                or not isinstance(item.get('text'),str) or item['text']==row['source']):continue
        by_source[row['source']].append((row,item))
    records=[]
    for row in rows:
        if row['id'] not in wanted or row['id'] in known:continue
        candidates=[(other,item) for other,item in by_source.get(row['source'],[])
                    if (other.get('usage')=='speaker_label')==(row.get('usage')=='speaker_label')]
        if len({item['text'] for _other,item in candidates})!=1:continue
        other,item=candidates[0]
        records.append(dict(item,id=row['id'],source=row['source'],recovery='display_location',reused_from=other['id']))
    return records
