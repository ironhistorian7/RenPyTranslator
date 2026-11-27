"""Explicit final review of failed translations, independent of name review."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

from job_events import emit, set_phase
from name_review_control import _cancelled, _desktop_reply


def _unity_manual_issue(entry, text):
    from unity_text_formats import validate_replacement
    if text == entry['source'] or validate_replacement(entry['source'], text, kind=entry.get('unity_format'), manual=True):
        return None
    location = entry.get('location') or entry['id']
    if not text.strip() or len(text.splitlines()) > 1 or text != text.strip() or '\0' in text:
        return f'{location} · 앞뒤 공백 없이 한 줄로 입력하세요.'
    return f'{location} · 원문의 변수, 태그와 구분자를 유지하세요.'


def build_review(rows, known, cfg=None):
    from japanese_output_policy import enabled as japanese_enabled
    entries = []
    unity = bool(cfg and cfg.get('engine') == 'unity')
    for row in rows:
        item = known.get(row['id'], {})
        entry = {'id': row['id'], 'source': row['source'], 'current': item.get('text', row['source'])}
        if unity:
            entry['unity_format'] = row.get('unity_format')
            if row.get('source_file') and row.get('source_line'):
                entry['location'] = f"{row['source_file']} · {row['source_line']}행"
        if item.get('status') == 'ui_original':
            continue
        if unity and item.get('_unity_numeric_review') and item.get('status') != 'manual_translation':
            location = entry.get('location') or entry['id']
            entry['issue'] = f'{location} · 원문과 번역문의 숫자가 다릅니다. 확인하세요.'
        elif item.get('status') == 'manual_translation':
            if not unity or not (issue := _unity_manual_issue(entry, entry['current'])):
                continue
            entry['issue'] = issue
        elif (item.get('status') not in ('source_fallback', 'partial_translation') and not item.get('_rpg_residual_language')
              and not (japanese_enabled(cfg) and (item.get('_japanese_residual_language') or item.get('_japanese_ruby_pending')))):
            from label_policy import unchanged_choice_errors
            if item.get('status')=='literal' or not unchanged_choice_errors(row,item.get('text'),cfg):continue
        entries.append(entry)
    return {'fingerprint': uuid.uuid4().hex, 'entries': entries}


def _remove_failures(project, handled):
    failed = Path(project) / 'data' / 'failed-items.json'
    if not handled or not failed.is_file():
        return
    existing = json.loads(failed.read_text(encoding='utf-8-sig'))
    if not isinstance(existing, list):
        raise ValueError('실패 항목 목록 형식이 올바르지 않습니다.')
    remaining = [item for item in existing if not isinstance(item, dict)
                 or not isinstance(item.get('row'), dict) or item['row'].get('id') not in handled]
    if len(remaining) == len(existing):
        return
    temp = failed.with_name(failed.name + '.manual.tmp')
    temp.write_text(json.dumps(remaining, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, failed)


def apply_review(project, cfg, review, reply):
    """Persist explicitly edited rows; skipped rows retain their original fallback."""
    from translation import fingerprint
    if not isinstance(reply, dict) or reply.get('fingerprint') != review['fingerprint'] or not isinstance(reply.get('entries'), list):
        raise ValueError('검토 목록을 다시 확인하세요.')
    expected = {entry['id']: entry for entry in review['entries']}
    values = {}
    for entry in reply['entries']:
        if (not isinstance(entry, dict) or not isinstance(entry.get('id'), str) or entry.get('id') not in expected or entry['id'] in values
                or not isinstance(entry.get('apply'), bool) or not isinstance(entry.get('text'), str)):
            raise ValueError('검토 항목을 다시 확인하세요.')
        values[entry['id']] = entry
    if set(values) != set(expected):
        raise ValueError('검토 항목이 변경되었습니다.')
    if cfg.get('engine') == 'unity':
        for uid,item in values.items():
            if item['apply'] and (issue := _unity_manual_issue(expected[uid],item['text'])):
                raise ValueError(issue)
    rows = [{'id': uid, 'source': expected[uid]['source'], 'text': item['text'],
             'model': cfg['model'], 'fingerprint': fingerprint(cfg), 'status': 'manual_translation'}
            for uid, item in values.items() if item['apply']]
    return _save_manual_rows(project, rows)


def _save_manual_rows(project, rows):
    if not rows:
        return rows
    from atomic_files import replace_cache_file
    cache_path = Path(project) / 'data' / 'translations.jsonl'
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=cache_path.parent, prefix='.translation-review-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            if cache_path.is_file() and cache_path.stat().st_size:
                with cache_path.open('rb') as previous:
                    previous.seek(-1, os.SEEK_END)
                    separator = previous.read(1) not in (b'\n', b'\r')
                    previous.seek(0)
                    shutil.copyfileobj(previous, stream)
                if separator:
                    stream.write(b'\n')
            for row in rows:
                stream.write((json.dumps(row, ensure_ascii=False) + '\n').encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
        replace_cache_file(temporary, cache_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    _remove_failures(project, {row['id'] for row in rows})
    from job_events import resolve_item_warnings
    resolve_item_warnings([row['id'] for row in rows])
    return rows


def _console_reply(review):
    if not sys.stdin.isatty():
        raise ValueError('미번역 항목 확인이 필요합니다. GUI 또는 대화형 PowerShell에서 실행하세요.')
    entries = []
    for entry in review['entries']:
        if entry.get('issue'): print(entry['issue'], flush=True)
        print('원문: ' + entry['source'] + '\n현재: ' + entry['current'], flush=True)
        print('내용을 그대로 입력하세요. /skip 유지, /cancel 취소, /literal 명령어 그대로 입력, /json 줄바꿈 입력.', flush=True)
        while True:
            answer = input('> ')
            if answer == '/cancel': raise KeyboardInterrupt
            if answer == '/skip':
                entries.append({'id': entry['id'], 'apply': False, 'text': ''})
                break
            if answer == '/literal':
                value = input('내용> ')
            elif answer == '/json':
                try:
                    value = json.loads(input('JSON 문자열> '))
                except ValueError:
                    print('JSON 문자열을 입력하세요.', flush=True)
                    continue
                if not isinstance(value, str):
                    print('JSON 문자열을 입력하세요.', flush=True)
                    continue
            else:
                value = answer
            entries.append({'id': entry['id'], 'apply': True, 'text': value})
            break
    return {'fingerprint': review['fingerprint'], 'entries': entries}


def run(project, cfg, rows=None, known=None, *, review_sentences=False):
    from translation import read_catalog, cache
    rows = read_catalog(project) if rows is None else rows
    known = cache(project, cfg) if known is None else known
    # Reconcile after an interrupted write: the append-only cache is authoritative.
    _remove_failures(project, {row['id'] for row in rows
                               if known.get(row['id'], {}).get('status') == 'manual_translation'})
    path = os.environ.get('RPT_TRANSLATION_REVIEW_FILE')
    edited = []
    review = build_review(rows, known, cfg)
    if path and (review['entries'] or review_sentences) and os.environ.get('RPT_DESKTOP_EVENTS') != '1':
        raise ValueError('번역 확인창의 연결 상태를 확인하세요.')
    if review['entries']:
        set_phase('translation.manual_review', '미번역 항목 확인')
        edited = _wait_for_reply(review, path, lambda payload: apply_review(project, cfg, review, payload))
    if not path or not review_sentences:
        return edited

    from sentence_review import build, selected_rows
    if edited:
        known = {**known, **{item['id']: item for item in edited}}
    sentences, groups = build(project, cfg, rows, known)
    if groups:
        set_phase('translation.sentence_review', '문장 번역 검토')
        selected = _wait_for_reply(sentences, path, lambda payload: selected_rows(cfg, sentences, groups, payload))
        edited.extend(_save_manual_rows(project, selected))
    return edited


def _wait_for_reply(review, path, accept):
    while True:
        _cancelled()
        if path:
            emit('translation_review', review=review)
            reply = _desktop_reply(review, Path(path))
        else:
            reply = _console_reply(review)
        try: return accept(reply)
        except ValueError as exc:
            review = dict(review, error=str(exc))
            if not path: print(str(exc), flush=True)
