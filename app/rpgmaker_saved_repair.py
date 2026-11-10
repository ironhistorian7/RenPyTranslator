"""Reuse indexed valid model outputs before scheduling RPG Maker translation."""
import json


def saved_records(project,cfg,rows,known):
    """Return append-only recovery records and an updated index, without writing."""
    index=project/'data/failed-items.json'
    if not index.is_file():return [],None
    from failed_repair import saved_output
    from translation import fingerprint
    failures=json.loads(index.read_text(encoding='utf-8'))
    recovered=[];remaining=[]
    for failure in failures:
        indexed=failure.get('row',{});uid=indexed.get('id');row=rows.get(uid)
        if row is None:
            remaining.append(failure);continue
        aliases={row['source'],row.get('base_source',row['source']),*row.get('source_aliases',[])}
        if indexed.get('source') not in aliases:
            remaining.append(failure);continue
        previous=known.get(uid)
        if previous and previous.get('status') not in ('source_fallback','ui_original') and previous.get('text')!=row['source']:
            continue  # Already validated successful cache entries take priority.
        error=failure.get('error') or (previous or {}).get('error','')
        text=saved_output(row['source'],error,cfg,row.get('usage'),
                          input_format=failure.get('input_format',(previous or {}).get('input_format')))
        if text is None:
            remaining.append(failure);continue
        recovered.append(dict(previous or {},id=uid,source=row['source'],text=text,
                              model=(previous or {}).get('model',cfg['model']),
                              fingerprint=fingerprint(cfg),status='failure_recovered',error=None,
                              recovery='saved_output'))
    return recovered,remaining if len(remaining)!=len(failures) else None
