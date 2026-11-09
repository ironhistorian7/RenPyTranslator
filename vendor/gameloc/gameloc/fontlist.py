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

"""컴퓨터에 깔린 글꼴을 훑어 **한글이 들어 있는 것만** 골라냅니다.

파일 이름만 보고 고르면 안 됩니다. ``malgun.ttf`` 가 맑은 고딕인 건 알겠지만
``H2GTRM.TTF`` 나 ``NanumSquareR.otf`` 는 열어봐야 압니다. 그래서 글꼴 파일을
직접 읽습니다 — 이름표(name 표)에서 사람이 읽을 이름을, 글자표(cmap 표)에서
한글이 있는지를 봅니다.

바깥 라이브러리는 안 씁니다. TrueType/OpenType 은 표 몇 개만 보면 되고,
그 정도는 직접 읽는 게 의존성을 하나 더 다는 것보다 낫습니다.
"""

from __future__ import annotations

import platform
import struct
from dataclasses import dataclass
from pathlib import Path

# 이 글자들이 있으면 한글을 그릴 수 있다고 봅니다.
PROBE = (0xAC00, 0xD55C, 0xB098)          # 가, 한, 나
SUFFIXES = {".ttf", ".otf", ".ttc", ".otc"}
MAX_FILES = 900                            # 폰트 폴더가 큰 컴퓨터를 위한 상한


@dataclass
class FontInfo:
    path: str
    name: str
    hangul: bool = False

    def to_dict(self) -> dict:
        return {"path": self.path, "name": self.name, "hangul": self.hangul}


def font_dirs() -> list[Path]:
    system = platform.system()
    if system == "Windows":
        return [Path(r"C:\Windows\Fonts"),
                Path.home() / "AppData/Local/Microsoft/Windows/Fonts"]
    if system == "Darwin":
        return [Path("/System/Library/Fonts"), Path("/Library/Fonts"),
                Path.home() / "Library/Fonts"]
    return [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"),
            Path.home() / ".fonts", Path.home() / ".local/share/fonts"]


# ---------------------------------------------------------------- sfnt 읽기

def _tables(data: bytes, offset: int = 0) -> dict[bytes, tuple[int, int]]:
    """``{표 이름: (시작, 길이)}``."""
    if offset + 12 > len(data):
        return {}
    num = struct.unpack_from(">H", data, offset + 4)[0]
    out = {}
    for i in range(num):
        rec = offset + 12 + i * 16
        if rec + 16 > len(data):
            break
        tag, _sum, off, length = struct.unpack_from(">4sIII", data, rec)
        out[tag] = (off, length)
    return out


def _font_offsets(data: bytes) -> list[int]:
    """.ttc 는 글꼴 여러 개가 한 파일에 들어 있습니다."""
    if data[:4] == b"ttcf":
        n = struct.unpack_from(">I", data, 8)[0]
        return list(struct.unpack_from(f">{min(n, 20)}I", data, 12))
    return [0]


def _name(data: bytes, tables: dict) -> str:
    loc = tables.get(b"name")
    if not loc:
        return ""
    off, _len = loc
    if off + 6 > len(data):
        return ""
    count, str_off = struct.unpack_from(">HH", data, off + 2)
    best = ""
    for i in range(count):
        rec = off + 6 + i * 12
        if rec + 12 > len(data):
            break
        pid, eid, _lid, nid, length, noff = struct.unpack_from(">6H", data, rec)
        if nid not in (1, 4):                     # 1=가족 이름, 4=전체 이름
            continue
        start = off + str_off + noff
        raw = data[start:start + length]
        try:
            text = (raw.decode("utf-16-be") if pid == 3 or (pid == 0)
                    else raw.decode("latin-1"))
        except Exception:                          # noqa: BLE001
            continue
        text = text.strip("\x00 ").strip()
        if text and (not best or nid == 1):
            best = text
    return best


def _cmap_has(data: bytes, tables: dict, codes=PROBE) -> bool:
    loc = tables.get(b"cmap")
    if not loc:
        return False
    off, _len = loc
    if off + 4 > len(data):
        return False
    n = struct.unpack_from(">H", data, off + 2)[0]
    subs = []
    for i in range(n):
        rec = off + 4 + i * 8
        if rec + 8 > len(data):
            break
        pid, eid, sub = struct.unpack_from(">HHI", data, rec)
        subs.append((pid, eid, off + sub))
    # 유니코드 전체를 담는 표를 먼저 봅니다.
    subs.sort(key=lambda x: 0 if (x[0], x[1]) in ((3, 10), (0, 4), (0, 6)) else 1)
    for _pid, _eid, sub in subs:
        if sub + 2 > len(data):
            continue
        fmt = struct.unpack_from(">H", data, sub)[0]
        if fmt == 4 and _fmt4_has(data, sub, codes):
            return True
        if fmt == 12 and _fmt12_has(data, sub, codes):
            return True
    return False


def _fmt4_has(data: bytes, sub: int, codes) -> bool:
    try:
        seg2 = struct.unpack_from(">H", data, sub + 6)[0]
    except struct.error:
        return False
    seg = seg2 // 2
    ends = sub + 14
    starts = ends + seg2 + 2
    if starts + seg2 > len(data):
        return False
    for c in codes:
        for i in range(seg):
            end = struct.unpack_from(">H", data, ends + i * 2)[0]
            if c > end:
                continue
            start = struct.unpack_from(">H", data, starts + i * 2)[0]
            if start <= c:
                return True
            break
    return False


def _fmt12_has(data: bytes, sub: int, codes) -> bool:
    try:
        ngroups = struct.unpack_from(">I", data, sub + 12)[0]
    except struct.error:
        return False
    base = sub + 16
    if base + ngroups * 12 > len(data) or ngroups > 200000:
        return False
    for c in codes:
        lo, hi = 0, ngroups - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            s, e, _g = struct.unpack_from(">III", data, base + mid * 12)
            if c < s:
                hi = mid - 1
            elif c > e:
                lo = mid + 1
            else:
                return True
    return False


def read_font(path: Path) -> FontInfo | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    for off in _font_offsets(data):
        tables = _tables(data, off)
        if not tables:
            continue
        info = FontInfo(path=str(path), name=_name(data, tables) or path.stem)
        info.hangul = _cmap_has(data, tables)
        if info.hangul:
            return info
    tables = _tables(data, _font_offsets(data)[0])
    return FontInfo(path=str(path), name=_name(data, tables) or path.stem)


def scan(only_hangul: bool = True, limit: int = MAX_FILES) -> list[FontInfo]:
    """설치된 글꼴 목록. 이름순, 한글 되는 것만."""
    out: list[FontInfo] = []
    seen: set[str] = set()
    checked = 0
    for folder in font_dirs():
        if not folder.is_dir():
            continue
        for p in sorted(folder.rglob("*")):
            if checked >= limit:
                break
            if not p.is_file() or p.suffix.lower() not in SUFFIXES:
                continue
            checked += 1
            info = read_font(p)
            if info is None or (only_hangul and not info.hangul):
                continue
            key = info.name.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(info)
    out.sort(key=lambda f: f.name.lower())
    return out
