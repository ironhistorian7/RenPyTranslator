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

"""Electron 의 ``app.asar`` 아카이브.

Electron 게임은 내용물을 전부 ``resources/app.asar`` 하나에 묶어 둡니다.
겉에서 보이는 건 크로미움 런타임 파일뿐이라 번역할 게 하나도 없어 보입니다.

형식은 단순합니다. 앞에 JSON 목차가 있고 뒤에 파일들이 이어 붙어 있습니다:

    uint32 4          (다음 값의 크기)
    uint32 headerSize (목차 블록 크기)  → 파일 본문은 8+headerSize 부터
    uint32 payloadSize
    uint32 jsonSize
    <JSON 목차>
    <파일 내용들>

**되묶지 않습니다.** Electron 은 ``resources/app.asar`` 가 없으면
``resources/app/`` 폴더를 대신 읽습니다. 그래서 풀어놓고 asar 를 옆으로
치워두면 평범한 파일이 됩니다 — 번역 도구가 다루기 훨씬 안전하고,
되돌리기도 이름만 바꾸면 끝입니다.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

MAGIC_FIRST = 4          # 첫 uint32 는 언제나 4


@dataclass
class Entry:
    path: str            # 아카이브 안 경로 (a/b/c.json)
    size: int
    offset: int          # 파일 본문 시작점 기준 오프셋
    unpacked: bool = False


def is_asar(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(16)
        if len(head) < 16:
            return False
        first, header_size, payload, json_size = struct.unpack("<IIII", head)
        return first == MAGIC_FIRST and 0 < json_size <= payload <= header_size
    except OSError:
        return False


def read_header(path: Path) -> tuple[dict, int]:
    """(목차 dict, 파일 본문이 시작하는 절대 오프셋)."""
    with open(path, "rb") as f:
        head = f.read(16)
        first, header_size, payload, json_size = struct.unpack("<IIII", head)
        if first != MAGIC_FIRST:
            raise ValueError("asar 파일이 아닙니다")
        raw = f.read(json_size)
    return json.loads(raw.decode("utf-8")), 8 + header_size


def _walk(node: dict, prefix: str = "") -> Iterator[Entry]:
    for name, info in (node.get("files") or {}).items():
        full = f"{prefix}/{name}" if prefix else name
        if "files" in info:
            yield from _walk(info, full)
        else:
            yield Entry(path=full,
                        size=int(info.get("size", 0)),
                        offset=int(info.get("offset", 0)),
                        unpacked=bool(info.get("unpacked")))


def list_files(path: Path) -> list[Entry]:
    header, _ = read_header(path)
    return list(_walk(header))


def unpack(path: Path, dest: Path) -> int:
    """아카이브를 폴더로 풉니다. 푼 파일 개수를 돌려줍니다."""
    header, base = read_header(path)
    dest = Path(dest)
    data = path.read_bytes()
    n = 0
    for e in _walk(header):
        target = dest / e.path
        target.parent.mkdir(parents=True, exist_ok=True)
        if e.unpacked:
            # .unpacked 폴더에 따로 들어 있는 파일 (보통 네이티브 모듈)
            src = path.parent / (path.name + ".unpacked") / e.path
            if src.is_file():
                target.write_bytes(src.read_bytes())
                n += 1
            continue
        start = base + e.offset
        target.write_bytes(data[start:start + e.size])
        n += 1
    return n


OFF_SUFFIX = ".gameloc-off"


def open_up(asar_path: Path) -> tuple[int, Path]:
    """app.asar 를 옆 폴더로 풀고, asar 는 이름을 바꿔 비활성화합니다.

    Electron 은 ``resources/app.asar`` 가 없으면 ``resources/app/`` 을 읽습니다.
    되돌리기는 폴더를 지우고 이름만 되돌리면 끝입니다(``close_up``).
    """
    asar_path = Path(asar_path)
    dest = asar_path.parent / "app"
    if dest.exists():
        raise FileExistsError(f"{dest} 가 이미 있습니다. 먼저 되돌리세요.")
    n = unpack(asar_path, dest)
    asar_path.rename(asar_path.with_name(asar_path.name + OFF_SUFFIX))
    return n, dest


def close_up(root: Path) -> bool:
    """푼 폴더를 지우고 원래 app.asar 를 되살립니다."""
    import shutil
    res = Path(root) / "resources"
    off = res / ("app.asar" + OFF_SUFFIX)
    if not off.is_file():
        return False
    if (res / "app").is_dir():
        shutil.rmtree(res / "app")
    off.rename(res / "app.asar")
    return True
