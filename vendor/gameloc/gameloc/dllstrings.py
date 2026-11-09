# SPDX-License-Identifier: GPL-3.0-or-later
"""게임 프로그램(.NET DLL) 안에 **글자 그대로 박힌 문구**를 읽고, 같은 자리에 번역을 덮어씁니다.

유니티 Mono 게임은 코드(``Assembly-CSharp.dll``)에서 ``button.text = "新しく始める"`` 처럼
문구를 직접 만들기도 합니다. 그런 글자는 씬·에셋 데이터에 없어서 번역표에 안 뜨고,
데이터의 글자를 번역해도 게임이 실행 중에 코드의 글자로 도로 덮어씁니다
(RJ01657800: 데이터에는 ``새로 시작하십시오`` 가 들어갔는데 화면은 일본어 그대로).

.NET 은 이런 글자를 ``#US`` (user strings) 라는 덩이에 UTF-16 으로 모아 둡니다.

    [길이(압축 정수)] [UTF-16 글자들] [끝 표지 1바이트]

## 왜 '같은 자리에, 같거나 짧게' 만 넣나

``#US`` 를 늘리면 뒤에 오는 덩이(``#Blob`` 등)와 메타데이터 표의 모든 위치가 밀립니다.
하나만 틀려도 DLL 이 통째로 안 열려 **게임이 못 켜집니다.** 그래서 덩이 크기는 그대로 두고
글자 수가 **같거나 적은** 번역만 넣습니다(모자라는 자리는 공백). 더 긴 번역은 넣지 않고
어느 것이 안 들어갔는지 알려 줍니다.

## 조심할 점

코드의 글자는 화면 문구이면서 **비교 대상**일 수도 있습니다(``if (name == "設定")``).
그런 것을 번역하면 게임 동작이 바뀝니다. 그래서 이 기능은 **선택해서 켤 때만** 씁니다.
"""

from __future__ import annotations

import dataclasses
import struct

_BSJB = struct.pack("<I", 0x424A5342)


@dataclasses.dataclass
class Heap:
    start: int          # 파일 안에서 #US 덩이가 시작하는 자리
    size: int


def find_user_strings(data: bytes) -> Heap | None:
    """메타데이터에서 ``#US`` 덩이의 자리를 찾습니다. 못 찾으면 ``None``."""
    root = data.find(_BSJB)
    while root >= 0:
        try:
            ver_len = struct.unpack_from("<I", data, root + 12)[0]
            if ver_len > 255:
                raise ValueError
            pos = root + 16 + ver_len
            n_streams = struct.unpack_from("<H", data, pos + 2)[0]
            if not 1 <= n_streams <= 16:
                raise ValueError
            pos += 4
            for _ in range(n_streams):
                off, size = struct.unpack_from("<II", data, pos)
                end = data.index(b"\x00", pos + 8)
                name = data[pos + 8:end].decode("ascii", "replace")
                pos = (end + 1 + 3) & ~3          # 이름은 4바이트 경계로 채웁니다
                if name == "#US":
                    if root + off + size <= len(data):
                        return Heap(root + off, size)
                    raise ValueError
        except (struct.error, ValueError):
            pass
        root = data.find(_BSJB, root + 1)
    return None


def _read_len(data: bytes, at: int) -> tuple[int, int]:
    """압축 정수 하나. (값, 차지한 바이트 수)"""
    b = data[at]
    if b < 0x80:
        return b, 1
    if (b & 0xC0) == 0x80:
        return ((b & 0x3F) << 8) | data[at + 1], 2
    return (((b & 0x1F) << 24) | (data[at + 1] << 16)
            | (data[at + 2] << 8) | data[at + 3]), 4


@dataclasses.dataclass
class UserString:
    offset: int         # #US 덩이 안에서의 자리(= ldstr 토큰 값)
    text: str
    chars: int          # 글자 수(들어갈 수 있는 최대 길이)


def read_strings(data: bytes, heap: Heap | None = None) -> list[UserString]:
    heap = heap or find_user_strings(data)
    if heap is None:
        return []
    out: list[UserString] = []
    i = 1                                     # 자리 0 은 빈 항목
    end = heap.size
    while i < end:
        try:
            length, used = _read_len(data, heap.start + i)
        except IndexError:
            break
        if length == 0:
            i += used
            continue
        body = heap.start + i + used
        if i + used + length > end:
            break
        nchars = (length - 1) // 2
        raw = data[body:body + nchars * 2]
        try:
            text = raw.decode("utf-16-le")
        except UnicodeDecodeError:
            i += used + length
            continue
        out.append(UserString(i, text, nchars))
        i += used + length
    return out


def _flag(text: str) -> int:
    """끝 표지 바이트. 아스키가 아닌 글자가 있으면 1."""
    return 1 if any(ord(c) > 0x7E or ord(c) < 0x09 for c in text) else 0


@dataclasses.dataclass
class PatchResult:
    data: bytes
    applied: dict[int, str]                 # 자리 → 실제로 넣은 글(공백 채움 포함)
    too_long: list[tuple[int, str, int]]    # (자리, 번역, 들어갈 수 있는 글자 수)
    missing: list[int]                      # 그 자리에 그 글이 없는 것


def patch(data: bytes, edits: dict[int, str]) -> PatchResult:
    """``edits``(자리 → 번역)를 같은 자리에 덮어쓴 새 바이트를 돌려줍니다.

    덩이 크기와 다른 모든 바이트는 그대로입니다. 다 쓴 뒤 **다시 읽어** 넣은 것이
    맞는지 확인하고, 어긋나면 예외를 냅니다(부르는 쪽이 그 파일을 건드리지 않습니다).
    """
    heap = find_user_strings(data)
    if heap is None:
        raise RuntimeError("이 DLL 에서 글자 덩이(#US)를 찾지 못했습니다")
    by_off = {s.offset: s for s in read_strings(data, heap)}
    buf = bytearray(data)
    applied: dict[int, str] = {}
    too_long: list[tuple[int, str, int]] = []
    missing: list[int] = []
    for off, new in sorted(edits.items()):
        cur = by_off.get(off)
        if cur is None:
            missing.append(off)
            continue
        if len(new.encode("utf-16-le")) // 2 > cur.chars:
            too_long.append((off, new, cur.chars))
            continue
        padded = new + " " * (cur.chars - len(new.encode("utf-16-le")) // 2)
        length, used = _read_len(data, heap.start + off)
        body = heap.start + off + used
        buf[body:body + cur.chars * 2] = padded.encode("utf-16-le")
        buf[body + cur.chars * 2] = _flag(padded)
        applied[off] = padded
    out = bytes(buf)

    # 검증: 크기 그대로, 넣은 자리만 바뀌었고, 다시 읽으면 넣은 글이 나온다
    if len(out) != len(data):
        raise RuntimeError("DLL 크기가 달라졌습니다")
    again = {s.offset: s.text for s in read_strings(out, find_user_strings(out))}
    for off, txt in applied.items():
        if again.get(off) != txt:
            raise RuntimeError(f"자리 {off} 에 넣은 글이 다시 읽히지 않습니다")
    for off, s in by_off.items():
        if off not in applied and again.get(off) != s.text:
            raise RuntimeError(f"건드리지 않은 자리 {off} 가 바뀌었습니다")
    return PatchResult(out, applied, too_long, missing)
