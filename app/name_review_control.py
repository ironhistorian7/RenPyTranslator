"""One explicit name checkpoint between automatic preparation and dialogue."""
import json
import os
from pathlib import Path
import sys
import time

from job_events import emit,set_phase


def prepare_rpg(project,cfg):
    """Propose only declared name fields, once; dialogue and its cache stay untouched."""
    from name_review import build_review
    current=build_review(project,cfg)
    if not current['entries'] or current.get('confirmed'):return
    from translation import read_catalog,cache,translate_batch,BatchTranslationError
    from engine import save_json
    from hy_backend import runtime
    from rpgmaker_names import _slot,validation_config
    from rpgmaker_protocol import previous_output
    rows=read_catalog(project);known=cache(project,dict(cfg,_skip_name_review=True))
    path=project/'data/name-proposals.json'
    saved=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    entries={entry['key']:entry for entry in current['entries']}
    pending=[]
    for row in rows:
        if _slot(row) not in ('actor','speaker'):continue
        uid=row['id'];old=saved.get(uid,{})
        if 'rpg:'+uid not in entries:continue
        if old.get('source')==row['source'] and old.get('model')==cfg['model']:continue
        item=known.get(uid,{})
        # Keep already generated proposals, including their imperfect readings,
        # visible to the reviewer instead of generating them a second time.
        if item and item.get('status') not in ('source_fallback','ui_original'):
            continue
        pending.append(row)
    if not pending:return
    set_phase('names.automatic','이름 자동 처리',len(pending))
    from job_events import progress
    active_cfg=dict(cfg,_name_preparation=True)
    active_cfg=validation_config(active_cfg,rows,known)
    with runtime(active_cfg) as active:
        size=max(1,min(8,active.get('batch_size',8)))
        for offset in range(0,len(pending),size):
            _cancelled();batch=pending[offset:offset+size]
            inputs=[]
            for row in batch:
                entry=entries.get('rpg:'+row['id'],{})
                inputs.append(dict(row,source=entry.get('reading') or row['source']))
            try:
                outputs,_=translate_batch(inputs,{},active)
                failures=[]
            except BatchTranslationError as exc:
                outputs=exc.output;failures=exc.failures
            output={item['id']:item.get('text') for item in outputs}
            failed={item['row']['id']:item.get('error','') for item in failures}
            for row in batch:
                uid=row['id'];error=failed.get(uid)
                proposal=output.get(uid) or (previous_output(error) if error else None)
                saved[uid]=dict(source=row['source'],text=proposal,model=cfg['model'],error=error)
            save_json(path,saved)
            progress('names.automatic',min(offset+len(batch),len(pending)),len(pending))



def pending_kirikiri(project, cfg, current=None):
    """Return one representative for each name still needing a proposal."""
    from name_review import build_review, _kirikiri_slot, _valid_kirikiri
    from translation import read_catalog, cache
    project = Path(project)
    current = current if current is not None else build_review(project, cfg)
    if not current['entries'] or current.get('confirmed'): return []
    entries = {entry['key'][len('kirikiri:'):]: entry for entry in current['entries']
               if entry['key'].startswith('kirikiri:')}
    rows = read_catalog(project)
    known = cache(project, dict(cfg, _skip_name_review=True))
    path = project/'data/name-proposals.json'
    saved = json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}
    pending = []
    for row in rows:
        uid = str(row['id'])
        if uid not in entries or not _kirikiri_slot(row): continue
        entry = entries[uid]
        if entry.get('retained_decision') is not None or _valid_kirikiri(entry.get('manual_override')): continue
        # A proposal or cached translation from any member of this exact
        # source group can be shown without another model request.
        reusable = False
        for member_id in entry['members']:
            proposal = saved.get(member_id, {})
            item = known.get(member_id, {})
            if proposal.get('source') == row['source'] and proposal.get('model') == cfg.get('model'):
                reusable = True; break
            if (item.get('source') == row['source'] and item.get('status') not in ('source_fallback', 'ui_original')
                    and _valid_kirikiri(item.get('text'))):
                reusable = True; break
        if reusable: continue
        pending.append(row)
    return pending


def prepare_kirikiri(project, cfg):
    """Translate one representative per display-name group without writing cache."""
    pending = pending_kirikiri(project, cfg)
    if not pending: return
    from translation import translate_batch, BatchTranslationError
    from engine import save_json
    from hy_backend import runtime
    from job_events import progress
    project = Path(project)
    path = project/'data/name-proposals.json'
    saved = json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}
    set_phase('names.automatic', '이름 자동 처리', len(pending))
    with runtime(dict(cfg, _name_preparation=True)) as active:
        size = max(1, min(8, active.get('batch_size', 8)))
        # A batch may contain only one syntax dialect.
        done = 0
        for dialect in sorted({row.get('dialect', 'kag') for row in pending}):
            subset = [row for row in pending if row.get('dialect', 'kag') == dialect]
            for offset in range(0, len(subset), size):
                _cancelled(); batch = subset[offset:offset + size]
                try:
                    outputs, _ = translate_batch(batch, {}, dict(active, _kirikiri_dialect=dialect))
                    failures = []
                except BatchTranslationError as exc:
                    outputs, failures = exc.output, exc.failures
                output = {str(item['id']): item.get('text') for item in outputs}
                failed = {str(item['row']['id']): item.get('error', '') for item in failures}
                for row in batch:
                    uid = str(row['id'])
                    saved[uid] = dict(source=row['source'], text=output.get(uid),
                                      model=cfg.get('model'), error=failed.get(uid))
                save_json(path, saved)
                done += len(batch); progress('names.automatic', done, len(pending))

def _cancelled():
    path=os.environ.get('RPT_CANCEL_FILE')
    if path and Path(path).exists():raise KeyboardInterrupt


def _desktop_reply(review,path):
    while True:
        _cancelled()
        if path.is_file():
            payload=json.loads(path.read_text(encoding='utf-8-sig'))
            path.unlink()
            return payload
        time.sleep(.2)


def _console_reply(review):
    if not sys.stdin.isatty():
        raise ValueError('번역 전에 이름 확인이 필요합니다. GUI 또는 대화형 PowerShell에서 실행하세요.')
    entries=[dict(entry) for entry in review['entries']]
    unresolved=[entry for entry in entries if entry['needs_review']]
    resolved=[entry for entry in entries if not entry['needs_review']]
    print('이름 확인: 직접 확인 %d개 · 자동 처리 %d개'%(len(unresolved),len(resolved)),flush=True)
    def edit(entry):
        print('%s | 참고: %s'%(entry['source'],entry.get('proposed') or entry['source']),flush=True)
        value=input('사용할 표기 [%s] (Enter: 유지, =: 원어, /취소): '%entry['value'])
        if value=='/취소':raise KeyboardInterrupt
        if value:entry['value']=entry['source'] if value=='=' else value
    for entry in unresolved:edit(entry)
    if resolved:
        answer=input('자동 처리된 이름 %d개도 확인할까요? [y/N]: '%len(resolved))
        if answer.lower() in ('y','yes','예'):
            for entry in resolved:edit(entry)
    if input('이 표기로 번역을 시작할까요? [y/N]: ').lower() not in ('y','yes','예'):
        raise KeyboardInterrupt
    return dict(review,entries=entries)


def run(project,cfg,force=False):
    """Do not return to the translation caller until the current list is approved."""
    from name_review import build_review,apply_review
    review=build_review(project,cfg)
    if not review['entries'] or (review.get('confirmed') and not force):return review
    set_phase('names.confirm','이름 확인')
    response_path=os.environ.get('RPT_NAME_REVIEW_FILE')
    if response_path and os.environ.get('RPT_DESKTOP_EVENTS')!='1':
        raise ValueError('이름 확인창의 연결 상태를 확인하세요.')
    while True:
        _cancelled()
        if response_path:
            emit('name_review',review=review)
            payload=_desktop_reply(review,Path(response_path))
        else:payload=_console_reply(review)
        try:
            saved=apply_review(project,cfg,payload)
        except ValueError as exc:
            # A failed edit stays in the same checkpoint; it never starts inference.
            edits={entry.get('key'):entry.get('value') for entry in payload.get('entries',[])
                   if isinstance(entry,dict)} if isinstance(payload,dict) else {}
            review=dict(review,error=str(exc),entries=[dict(entry,value=edits.get(entry['key'],entry['value']))
                                                      for entry in review['entries']])
            if not response_path:print(str(exc),flush=True)
            continue
        return saved
