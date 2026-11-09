# SPDX-License-Identifier: GPL-3.0-or-later
"""Addressables 카탈로그 — 번들을 고쳤으면 **여기도 고쳐야** 합니다.

## 왜 필요한가

``StreamingAssets/aa/`` 로 만든 게임(Addressables)은 번들 파일마다 **크기와
검사값을 catalog.bin 에 따로 적어 둡니다.** 우리가 번역을 넣으면 글자 길이가
달라져 번들 크기가 바뀌는데, 카탈로그는 옛 크기를 그대로 들고 있습니다.

그러면 유니티는 그 번들을 **못 쓰는 파일로 보고 거부합니다.** 파일이 아예
없는 것과 똑같은 결과입니다 — **게임이 안 켜집니다.**

진짜 게임에서 이렇게 확인했습니다. 번들 이름을 ``.off`` 로 바꿔 없앤 것과,
번역을 적용한 것이 **증상이 완전히 같았습니다.**

## 왜 우리 검사를 빠져나갔나

우리는 다시 쓴 파일을 **다시 읽어서** 번역이 들어갔는지 확인합니다. 그런데
파일 자체는 멀쩡합니다 — UnityPy 로는 잘 읽힙니다. 어긋난 것은 파일이
아니라 **파일 밖의 장부**라서, 파일만 보는 검사로는 영영 못 잡습니다.

## 생김새

번들 하나마다 이런 정수 네 개가 나란히 있습니다.

    [ CRC ][ 크기 ][ 2422 ][ 2487 ]
                     └─ 이 두 값은 모든 번들이 똑같이 갖고 있어 표식이 됩니다

어느 번들 것인지는 **이름 문자열 바로 뒤에 오는 첫 짝**으로 가립니다.
실제 게임의 번들 15개에서 크기가 전부 맞는 것을 확인했습니다.

## 검사값은 0 으로 둡니다

유니티는 CRC 가 0 이면 **검사를 건너뜁니다.** 우리가 유니티와 똑같은
방식으로 CRC 를 다시 계산하는 것보다 이쪽이 훨씬 안전합니다 — 계산이
한 비트만 달라도 게임이 안 켜지는데, 그건 우리가 확인할 길이 없습니다.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path

NAME = "catalog.bin"
AA = "aa"                       # StreamingAssets 아래 Addressables 폴더 이름

# 모든 번들 항목이 공통으로 갖고 있는 두 정수. 이것으로 자리를 찾습니다.
_MARK = struct.pack("<ii", 2422, 2487)

_BUNDLE = re.compile(rb"[ -~]{8,200}\.bundle")


@dataclass
class Slot:
    """카탈로그 안에서 한 번들이 차지하는 자리."""

    crc_at: int
    size_at: int
    size: int
    crc: int


def slots(raw: bytes) -> list[Slot]:
    """카탈로그에 적힌 번들 자리들. 파일 순서대로."""
    out: list[Slot] = []
    at = raw.find(_MARK)
    while at >= 0:
        if at >= 8:
            crc, size = struct.unpack_from("<ii", raw, at - 8)
            out.append(Slot(crc_at=at - 8, size_at=at - 4, size=size, crc=crc))
        at = raw.find(_MARK, at + 1)
    return out


def slot_for(raw: bytes, bundle_name: str) -> Slot | None:
    """``bundle_name`` 의 자리. 못 찾으면 ``None``.

    **이름 뒤에 오는 첫 자리**가 그 번들 것입니다. 크기로 찾으면 안 됩니다 —
    크기가 같은 번들이 실제로 둘 있었습니다(2,225 바이트짜리 둘).
    """
    stem = bundle_name.rsplit(".bundle", 1)[0]
    # 카탈로그에는 이름 뒤에 해시가 덧붙기도 합니다. 앞부분으로 찾습니다.
    needle = stem.encode("utf-8", "ignore")
    where = raw.find(needle)
    if where < 0:
        return None
    for s in slots(raw):
        if s.crc_at > where:
            return s
    return None


def status(catalog: Path, bundle_name: str) -> str:
    """이 카탈로그가 ``bundle_name`` 을 어떻게 다루는가.

    * ``"ok"``      — 크기 자리를 찾았습니다. ``retune`` 이 고칠 수 있습니다.
    * ``"unknown"`` — 카탈로그 **형식을 모릅니다**(크기 자리가 하나도 안 보임).
      유니티 6 의 Addressables 2.x ``catalog.bin`` 이 그렇습니다. 이 형식에서는
      번들 크기를 우리가 못 고치지만, 실제 게임(Succubus Reborn)은 고친 번들을
      멀쩡히 읽었습니다.
    * ``"missing"`` — 형식은 아는데 이 번들만 없습니다. 진짜 어긋남입니다.

    번들을 **쓰기 전에** 부르세요. 예전에는 쓴 뒤에 카탈로그를 고치다 실패해서,
    "손대지 않았습니다" 라고 하면서 번들은 이미 바뀌어 있었습니다.
    """
    raw = Path(catalog).read_bytes()
    if not slots(raw):
        return "unknown"
    return "ok" if slot_for(raw, bundle_name) is not None else "missing"


def names(raw: bytes) -> list[str]:
    """카탈로그가 알고 있는 번들 이름들 (중복 포함)."""
    return [m.group(0).decode("utf-8", "replace") for m in _BUNDLE.finditer(raw)]


def is_addressables(path: Path) -> Path | None:
    """이 게임이 Addressables 를 쓰나. 쓰면 ``catalog.bin`` 의 자리를."""
    for data in sorted(Path(path).glob("*_Data")) or [Path(path)]:
        cat = data / "StreamingAssets" / AA / NAME
        if cat.is_file():
            return cat
    return None


def under_aa(target: Path) -> bool:
    """이 파일이 Addressables 번들인가."""
    p = Path(target)
    return p.suffix.lower() == ".bundle" and AA in {x.lower() for x in p.parts}


@dataclass
class Fixed:
    name: str
    was: int
    now: int
    crc_cleared: bool = True


def retune(catalog: Path, sizes: dict[str, int]) -> list[Fixed]:
    """``{번들 파일 이름: 새 크기}`` 를 카탈로그에 반영합니다.

    검사값은 0 으로 둬서 유니티가 검사를 건너뛰게 합니다. 고친 것만
    돌려주고, 못 찾은 이름은 조용히 넘기지 않고 예외를 냅니다 — 조용히
    넘어가면 게임이 안 켜지는 이유를 아무도 못 찾습니다.
    """
    cat = Path(catalog)
    raw = bytearray(cat.read_bytes())
    done: list[Fixed] = []
    if not slots(bytes(raw)):
        # 형식을 모르는 카탈로그(유니티 6 의 Addressables 2.x 등). 이름을 못 찾는 것과
        # 다릅니다 — 크기 자리 자체가 안 보이니 고칠 수도, 틀렸다고 할 수도 없습니다.
        return done
    for name, new in sizes.items():
        s = slot_for(bytes(raw), name)
        if s is None:
            raise KeyError(f"카탈로그에서 {name} 을 찾지 못했습니다")
        if s.size == new and s.crc == 0:
            continue
        struct.pack_into("<i", raw, s.size_at, new)
        struct.pack_into("<i", raw, s.crc_at, 0)
        done.append(Fixed(name=name, was=s.size, now=new))
    if done:
        cat.write_bytes(bytes(raw))
    return done


def check(catalog: Path) -> list[str]:
    """카탈로그에 적힌 크기와 **실제 파일 크기**가 맞는지.

    안 맞는 것이 있으면 그 게임은 안 켜집니다. 사람 말로 돌려줍니다.
    """
    cat = Path(catalog)
    raw = cat.read_bytes()
    folder = cat.parent
    out: list[str] = []
    for f in sorted(folder.rglob("*.bundle")):
        s = slot_for(raw, f.name)
        if s is None:
            continue
        real = f.stat().st_size
        if s.size != real:
            out.append(f"{f.name}: 장부 {s.size:,} · 실제 {real:,}")
    return out
