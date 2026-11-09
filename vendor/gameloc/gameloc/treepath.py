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

"""중첩된 dict/list 안의 한 지점을 가리키는 경로 표기.

    a.b[0].c  ->  ["a", "b", 0, "c"]

유니티 타입트리, 파싱한 JSON, CSV 격자 안의 문자열 하나를 정확히 짚어
번역을 되돌려 넣기 위해 씁니다.
"""

from __future__ import annotations

import re
from typing import Any, Iterator

_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def parse_path(path: str) -> list:
    if not path:
        return []
    out: list = []
    pos = 0
    while pos < len(path):
        if path[pos] == ".":
            pos += 1
            continue
        m = _TOKEN.match(path, pos)
        if not m:
            raise ValueError(f"bad path {path!r} at {pos}")
        out.append(m.group(1) if m.group(1) is not None else int(m.group(2)))
        pos = m.end()
    return out


def format_path(parts: list) -> str:
    buf = ""
    for p in parts:
        if isinstance(p, int):
            buf += f"[{p}]"
        else:
            buf += f".{p}" if buf else str(p)
    return buf


def get_by_path(root: Any, path: str) -> Any:
    node = root
    for part in parse_path(path):
        node = node[part]
    return node


def set_by_path(root: Any, path: str, value: Any) -> None:
    parts = parse_path(path)
    if not parts:
        raise ValueError("cannot set the root itself")
    node = root
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value


def walk_strings(root: Any, _prefix: list | None = None) -> Iterator[tuple[str, str]]:
    """dict/list 트리의 모든 문자열 잎을 ``(경로, 값)`` 으로 냅니다."""
    prefix = _prefix or []
    if isinstance(root, str):
        yield format_path(prefix), root
    elif isinstance(root, dict):
        for k, v in root.items():
            yield from walk_strings(v, prefix + [k])
    elif isinstance(root, (list, tuple)):
        for i, v in enumerate(root):
            yield from walk_strings(v, prefix + [i])
