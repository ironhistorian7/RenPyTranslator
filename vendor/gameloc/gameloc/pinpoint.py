# SPDX-License-Identifier: GPL-3.0-or-later
"""파일을 직접 지목했을 때, **읽을 수 있는 것인지 즉시 알려 줍니다**.

## 왜

"이 파일에 대사가 있어요" 하고 아는 사용자가 있습니다. 그러면 진단을
돌릴 것 없이 그 파일을 바로 보면 됩니다. 문제는 **읽을 수 있는지 없는지**
알려 주지 않으면 사용자가 계속 헛수고를 한다는 것입니다.

셋 중 하나로 답합니다.

    글자 파일          — 됩니다 (json · js · csv · txt …)
    길이 붙은 이진     — 됩니다 (유니티처럼 길이+글자로 담긴 것)
    못 읽음            — 안 됩니다 (암호화되었거나 모르는 형식)

## 쓸 때 지킬 것

구조를 모르는 파일에 **쓸 때**는 유니티에서 쓰는 검증을 그대로 씁니다 —
고친 뒤 다시 읽어 문자열 개수와 차례가 어긋나면 아예 안 씁니다
(:func:`gameloc.unityraw.layout_ok`). 짐작으로 읽은 것에 짐작으로 쓰면
파일이 통째로 망가지기 때문입니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

TEXT_SUFFIX = {".json", ".js", ".txt", ".csv", ".tsv", ".xml", ".html",
               ".css", ".yaml", ".yml", ".ini", ".rpy", ".ks", ".tjs"}
MAX_BYTES = 256 * 1024 * 1024
SAMPLE = 8


@dataclass
class Look:
    path: str = ""
    kind: str = ""            # "text" | "raw" | "unreadable"
    can_read: bool = False
    can_write: bool = False
    strings: int = 0
    sample: list[str] = field(default_factory=list)
    why: str = ""

    def verdict(self) -> str:
        if self.kind == "text":
            return f"글자 파일입니다. 그대로 읽고 쓸 수 있습니다. ({self.why})"
        if self.kind == "raw":
            return (f"길이가 앞에 붙은 이진 파일입니다. 문자열 "
                    f"{self.strings:,}개를 찾았습니다. 읽고 쓸 수 있습니다.")
        return f"이 파일은 못 읽습니다. {self.why}"


def _looks_encrypted(head: bytes) -> bool:
    """앞부분이 고르게 흩어져 있으면 압축·암호화된 것입니다."""
    if len(head) < 512:
        return False
    seen = len(set(head))
    return seen > 240 and head.count(0) < len(head) // 64


def inspect(path: Path) -> Look:
    path = Path(path)
    look = Look(path=path.name)
    if not path.is_file():
        look.kind, look.why = "unreadable", "파일이 없습니다."
        return look
    try:
        size = path.stat().st_size
    except OSError as exc:
        look.kind, look.why = "unreadable", f"열 수 없습니다: {exc}"
        return look
    if size > MAX_BYTES:
        look.kind, look.why = "unreadable", "너무 큽니다."
        return look

    # ① 글자 파일인가
    if path.suffix.lower() in TEXT_SUFFIX:
        try:
            text = path.read_text("utf-8")
        except (OSError, UnicodeDecodeError):
            text = ""
        if text:
            look.kind, look.can_read, look.can_write = "text", True, True
            look.why = f"{len(text.splitlines()):,}줄"
            look.sample = [ln.strip() for ln in text.splitlines()
                           if ln.strip()][:SAMPLE]
            return look

    try:
        data = path.read_bytes()
    except (OSError, MemoryError) as exc:
        look.kind, look.why = "unreadable", f"읽다 실패했습니다: {exc}"
        return look

    # ② 길이가 앞에 붙은 이진인가
    from . import unityraw

    try:
        found = list(unityraw.scan(data, limit=10 ** 9))
    except Exception:                                   # noqa: BLE001
        found = []
    if found:
        look.kind, look.can_read, look.can_write = "raw", True, True
        look.strings = len(found)
        look.sample = [t for _o, t in found if t.strip()][:SAMPLE]
        return look

    # ③ 그 밖
    look.kind = "unreadable"
    look.why = ("압축되었거나 암호화된 것 같습니다."
                if _looks_encrypted(data[:4096])
                else "gameloc 이 아는 담는 방식이 아닙니다.")
    return look
