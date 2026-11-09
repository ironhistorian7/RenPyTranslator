"""머리말이 붙은 유니티 덩이(번들).

어떤 게임은 ``UnityFS`` 앞에 짧은 머리말(GUID 같은 글자와 0)을 붙여 둡니다.
암호화가 아니라 시작 위치만 밀린 것이라, 그 앞을 건너뛰고 열고 쓸 때
다시 붙이면 됩니다. 머리말 없는 보통 파일은 ``offset()`` 이 0 이라 아무것도
달라지지 않습니다.
"""
from __future__ import annotations

from pathlib import Path

_MAGIC = b"UnityFS"
_PLAIN = (b"UnityFS", b"UnityWeb", b"UnityRaw", b"UnityArchive")
_WINDOW = 256


def offset_of(head: bytes) -> int:
    """앞부분 바이트에서 ``UnityFS`` 가 시작하는 자리. 머리말이 없으면 0."""
    if head.startswith(_PLAIN):
        return 0
    i = head.find(_MAGIC, 1, _WINDOW)
    return i if i > 0 else 0


def offset(path: Path) -> int:
    try:
        with open(path, "rb") as fh:
            return offset_of(fh.read(_WINDOW + len(_MAGIC)))
    except OSError:
        return 0
