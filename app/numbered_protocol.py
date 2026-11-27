"""Strict shared parser for numbered translation responses."""
import re
import json
from collections import Counter

_HEADER = re.compile(r'(?m)^\[([0-9]+)(\]|>(?=[ \t\r\n]|$))[ \t]*')
_ALIASED_HEADER = re.compile(r'(?m)^\[(?:[^\W\d_][^\W_]*_)+([0-9]+)\][ \t]*')
_PROTOCOL_LIKE = re.compile(r'(?m)^\s*\[[^\]\r\n]*\]|</?target_id\b|```', re.I)


def response_instruction():
    """Instructions usable with existing [ID] source records."""
    return ('각 입력 항목마다 동일한 숫자 ID를 사용하여 [ID] 번역 형식으로 출력하세요. '
            '항목을 합치거나 ID를 바꾸지 말고, 설명이나 코드 블록 없이 번역만 출력하세요. '
            '번역에 필요한 줄바꿈과 원문의 표시를 유지하세요.')


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _clean(records, expected_inputs):
    # Preserve the established source-aware HY protocol wrapper behavior.
    from hy_backend import _unwrap_target_id, _INVALID_TARGET_ID
    result = {}
    for key, value in records.items():
        value = _unwrap_target_id(value, key, expected_inputs[key])
        if value is not _INVALID_TARGET_ID and value:
            result[key] = value
    return result


def parse(content, expected_inputs, *, allow_headerless=False, allow_multiline=False, truncated=False):
    """Return only unambiguous ID-to-text mappings.

    expected_inputs is a mapping of exact numeric string IDs to source text.
    A numeric line present in any source is ambiguous as a response delimiter;
    both the item before it and the item bearing that ID are rejected. Unknown
    headers similarly invalidate the preceding item. An expected, uniquely
    numbered [ID> line separates items but its own translation is retried.
    Output order follows the response. With truncated=True the last numbered record is always discarded;
    JSON and headerless responses are rejected as a whole on truncation.
    """
    expected = set(expected_inputs)
    if any(not isinstance(key, str) or not re.fullmatch(r'[0-9]+', key) for key in expected):
        raise ValueError('expected IDs must be numeric strings')
    if not isinstance(content, str):
        return {}
    value = content.strip()
    if value.startswith('{'):
        if truncated:
            return {}
        try:
            data = json.loads(value, object_pairs_hook=_unique_pairs,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError('invalid constant')))
        except (ValueError, TypeError):
            return {}
        if (not isinstance(data, dict) or not set(data).issubset(expected)
                or any(not isinstance(text, str) for text in data.values())):
            return {}
        return _clean({key: text.strip() for key, text in data.items()}, expected_inputs)
    headers = list(_HEADER.finditer(value))
    if not headers and len(expected)==1:
        aliases=list(_ALIASED_HEADER.finditer(value))
        if (len(aliases)==1 and aliases[0].start()==0 and aliases[0][1] in expected
                and not any(_ALIASED_HEADER.search(source) for source in expected_inputs.values())):
            alias=aliases[0]
            value='['+alias[1]+'] '+value[alias.end():]
            headers=list(_HEADER.finditer(value))
    if not headers:
        if (not truncated and allow_headerless and len(expected) == 1 and value
                and not re.search(r'\x00' if allow_multiline else r'[\r\n\x00]', value)
                and not _PROTOCOL_LIKE.search(value)
                and not re.match(r'\s*[{}]', value)
                and not re.match(r'\s*(?:translation|output|번역|출력)\s*:', value, re.I)):
            return _clean({next(iter(expected)): value}, expected_inputs)
        return {}
    source_headers = {m.group(1) for source in expected_inputs.values()
                      if isinstance(source, str) for m in _HEADER.finditer(source)}
    counts = Counter(m.group(1) for m in headers)
    invalid = {key for key, count in counts.items() if count != 1}
    result = {}
    for index, header in enumerate(headers):
        key = header.group(1)
        next_header = headers[index + 1] if index + 1 < len(headers) else None
        if header.group(2) != ']':
            invalid.add(key)
        # A possible literal source heading cannot establish either boundary.
        if key in source_headers:
            invalid.add(key)
            if index:
                invalid.add(headers[index - 1].group(1))
        if key not in expected and index:
            invalid.add(headers[index - 1].group(1))
        text = value[header.end():next_header.start() if next_header else len(value)].strip()
        if key in expected and text:
            result[key] = text
    if truncated:
        invalid.add(headers[-1].group(1))
    if headers[0].start():
        invalid.add(headers[0].group(1))
    return _clean({key: text for key, text in result.items() if key not in invalid}, expected_inputs)
