"""Recover unambiguous Unity model responses from requests or saved traces.

The caller must restrict this fallback to Unity HY requests and run the normal
numbered parser first. Returned text still requires the usual token, language,
and Unity structure validation; this helper never restores or edits text.
"""
import json
import re
from collections import defaultdict

from numbered_protocol import parse
from source_language import normalize


INPUT_FORMAT = 'unity_textasset_v2'


_AMBIGUOUS = re.compile(
    r'(?im)^\s*(?:\[[^\]\r\n]*\]|(?:\d+[.)]|[-*])\s+|'
    r'(?:translation|output|번역|출력|설명|참고)\s*[:：])|'
    r'```|</?target_id\b|\x00', re.I)


def parse_single_hy(content, expected_inputs, *, truncated=False):
    """Map one plain response to its sole expected ID, or return {}.

    A multiline response is accepted only if the supplied source itself has a
    real line break. Numbered, list-like, fenced, or explanatory envelopes are
    ambiguous and are left for the ordinary retry path.
    """
    if (truncated or not isinstance(content, str) or len(expected_inputs) != 1
            or not content.strip() or content.lstrip().startswith(('{', '}'))
            or _AMBIGUOUS.search(content)):
        return {}
    source = next(iter(expected_inputs.values()))
    if not isinstance(source, str):
        return {}
    return parse(content, expected_inputs, allow_headerless=True,
                 allow_multiline='\n' in source or '\r' in source)


def saved_attempts(project, rows, cfg):
    """Read prior drafts for these catalog IDs from their existing request log."""
    trace = project/'data/translation-requests.jsonl'
    try:
        stream = trace.open(encoding='utf-8-sig', errors='replace')
    except FileNotFoundError:
        return {}
    language = normalize(cfg.get('source_language'))
    single_requests = {}
    single_replies = {}
    drafts = defaultdict(list)
    with stream:
        for line in stream:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict) or record.get('source_language') != language:
                continue
            request_id = record.get('request_id')
            event = record.get('event')
            if event == 'request':
                items = record.get('items', ())
                profile = record.get('request_profile', {})
                if (len(items) == 1 and items[0]['id'] in rows
                        and profile.get('response_contract') == 'hy_numbered'
                        and profile.get('input_count') == 1):
                    single_requests[request_id] = (items[0]['id'], record.get('input_format'))
            elif event == 'response' and request_id in single_requests:
                uid, input_format = single_requests.pop(request_id)
                response = record.get('response', {})
                if response.get('done_reason') == 'stop':
                    single_replies[request_id] = (uid, response.get('message', {}).get('content'), input_format)
            elif event == 'validation':
                single = single_replies.pop(request_id, None)
                for failure in record.get('failures', ()):
                    uid = failure.get('id')
                    if uid not in rows:
                        continue
                    error = failure.get('error', '')
                    if '; output=' in error:
                        drafts[uid].append((error, failure.get('input_format')))
                    elif single and single[0] == uid and failure.get('error_code') == 'missing_response_item':
                        key = failure.get('expected_id', '0')
                        value = parse_single_hy(single[1], {key: rows[uid]['source']}).get(key)
                        if value:
                            drafts[uid].append(('Unity single response; output='+repr(value), single[2]))
    return {uid: tuple(dict.fromkeys(reversed(values))) for uid, values in drafts.items()}
