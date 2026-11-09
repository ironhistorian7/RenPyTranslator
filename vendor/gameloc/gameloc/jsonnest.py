# Gameloc — 게임 폴더를 넣으면 번역해 주는 도구
# Copyright (C) 2026  하진
#
# 이 프로그램은 자유 소프트웨어입니다. 자유 소프트웨어 재단이 펴낸 GNU 일반
# 공중 사용 허가서 3판 또는 그 이후 판의 조건에 따라 재배포하거나 고칠 수
# 있습니다.
#
# 이 프로그램은 쓸모가 있기를 바라며 배포하지만 어떠한 보증도 하지 않습니다.
# 자세한 내용은 GNU 일반 공중 사용 허가서를 보세요.
# 함께 받은 LICENSE 파일에 전문이 있습니다. <https://www.gnu.org/licenses/>

"""JSON 안에 JSON 문자열이 들어 있는 구조를 다룹니다.

RPG Maker MZ 플러그인 파라미터가 이렇게 생겼습니다. 파라미터 값은 문자열인데,
그 문자열을 다시 JSON 으로 파싱하면 배열이 나오고, 그 원소가 또 JSON 문자열입니다.

경로에 ``!`` 를 넣어 "여기서 문자열을 JSON 으로 한 겹 벗긴다" 를 표시합니다.

    parameters.list![0]!text

쓰기는 벗긴 순서의 역순으로 다시 감싸므로, 손대지 않은 부분의 인코딩이 유지됩니다.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterator

_TOKEN = re.compile(r"(!)|([^.\[\]!]+)|\[(\d+)\]")

UNWRAP = "!"


def parse_path(path: str) -> list:
    out: list = []
    pos = 0
    while pos < len(path):
        if path[pos] == ".":
            pos += 1
            continue
        m = _TOKEN.match(path, pos)
        if not m:
            raise ValueError(f"bad path {path!r} at {pos}")
        if m.group(1):
            out.append(UNWRAP)
        elif m.group(2) is not None:
            out.append(m.group(2))
        else:
            out.append(int(m.group(3)))
        pos = m.end()
    return out


def format_path(parts: list) -> str:
    buf = ""
    for p in parts:
        if p == UNWRAP:
            buf += "!"
        elif isinstance(p, int):
            buf += f"[{p}]"
        else:
            buf += f".{p}" if buf and not buf.endswith("!") else str(p)
    return buf


def _as_json(value: Any) -> Any | None:
    """문자열이 JSON 오브젝트/배열이면 파싱해서 돌려줍니다. 아니면 None."""
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s or s[0] not in "[{":
        return None
    try:
        parsed = json.loads(s)
    except Exception:
        return None
    return parsed if isinstance(parsed, (list, dict)) else None


def walk(node: Any, _prefix: list | None = None, _depth: int = 0) -> Iterator[tuple[str, str]]:
    """모든 문자열 잎을 (경로, 값) 으로. JSON 문자열은 벗기고 들어갑니다."""
    prefix = _prefix or []
    if _depth > 8:
        return
    if isinstance(node, str):
        inner = _as_json(node)
        if inner is not None:
            yield from walk(inner, prefix + [UNWRAP], _depth + 1)
        else:
            yield format_path(prefix), node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from walk(v, prefix + [k], _depth)
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            yield from walk(v, prefix + [i], _depth)


def get(root: Any, path: str) -> Any:
    node = root
    for part in parse_path(path):
        node = json.loads(node) if part == UNWRAP else node[part]
    return node


def set_(root: Any, path: str, value: str) -> Any:
    """경로에 값을 넣고 (필요하면 다시 감싸서) 루트를 돌려줍니다."""
    return _set(root, parse_path(path), value)


def _set(node: Any, parts: list, value: str) -> Any:
    if not parts:
        return value
    head, rest = parts[0], parts[1:]
    if head == UNWRAP:
        inner = json.loads(node)
        updated = _set(inner, rest, value)
        # 공백 없는 compact 가 RPG Maker 관행입니다.
        return json.dumps(updated, ensure_ascii=False, separators=(",", ":"))
    node[head] = _set(node[head], rest, value)
    return node
