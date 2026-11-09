# SPDX-License-Identifier: GPL-3.0-or-later
"""Shift-JIS 로만 글자를 담는 옛 게임(울프 2.x 등)에서 **한글을 보이게 하는 방법**.

## 문제

이 게임들은 글자를 Shift-JIS(CP932) 바이트로 저장하고 그대로 읽습니다. CP932 에는 한글이 없어서
번역문을 담을 수가 없습니다. 실행 파일을 고쳐 코드 페이지를 바꾸는 길은 글자 나누는 계산(두 바이트
글자의 앞 바이트 판정 등)이 엔진 곳곳에 박혀 있어 위험합니다.

## 방법: 글자 코드는 그대로, 글꼴만 바꿔 끼우기

1. 번역문에 쓰인 **한글 글자마다** CP932 로 담을 수 있는 **일본 한자 하나를 짝지어 줍니다**
   (``각 → 鑪`` 처럼). 게임 글자는 계속 올바른 Shift-JIS 입니다 — 엔진은 아무것도 모릅니다.
2. 짝지은 한자의 글꼴 모양을 **그 한글의 모양으로 바꾼 글꼴**을 만들어 게임 폴더에 넣고, 게임이
   그 글꼴을 쓰게 합니다. 그러면 화면에는 한글이 나옵니다.

짝으로 쓸 한자는 **게임에 남는 일본어 글에 안 쓰이는 것**만 고릅니다(그래야 안 번역된 일본어 글이
엉뚱한 한글로 보이지 않습니다). 또 두 번째 바이트가 아스키 기호와 겹치는 한자(``\\`` ``[`` ``|`` 등)는
빼서 제어문자(``\\c[2]``) 해석을 건드리지 않습니다.

## 글꼴 만들기 (순수 파이썬)

한글을 가진 TrueType 글꼴(기본: 이 컴퓨터의 나눔고딕 같은 열린 글꼴)의 ``cmap`` 만 고칩니다 — 글자 모양
(``glyf``)은 그대로 두고 "한자 코드 → 그 한글의 모양 번호" 연결만 더합니다. 이름은 새로 지어
(``GamelocKo``) 원래 글꼴과 섞이지 않게 하고, 일본어 코드 페이지를 지원한다고 표시해 엔진이
일본어 글자 집합으로 부를 때 다른 글꼴로 바꿔치지 않게 합니다.
"""

from __future__ import annotations

import struct
from pathlib import Path

FAMILY = "GamelocKo"
HANGUL_FIRST, HANGUL_LAST = 0xAC00, 0xD7A3


def is_hangul(ch: str) -> bool:
    return HANGUL_FIRST <= ord(ch) <= HANGUL_LAST


# ------------------------------------------------------------------ 짝 정하기

def kanji_pool() -> list[str]:
    """짝으로 쓸 수 있는 일본 한자 후보(JIS 제2수준부터). 두 번째 바이트가 0x80 이상인 것만."""
    out_l2, out_l1 = [], []
    for lead in list(range(0x88, 0xA0)) + list(range(0xE0, 0xEB)):
        for trail in range(0x80, 0xFD):
            try:
                ch = bytes((lead, trail)).decode("cp932")
            except UnicodeDecodeError:
                continue
            if len(ch) == 1 and 0x4E00 <= ord(ch) <= 0x9FFF:
                (out_l2 if lead >= 0x98 else out_l1).append(ch)
    return out_l2 + out_l1                    # 덜 쓰이는 제2수준을 먼저


class Mapping:
    """한글 글자 → 짝 한자. ``reserved`` 는 게임에 남는 글에 쓰인 글자(짝으로 안 씀)."""

    def __init__(self, texts: list[str], reserved: set[str]) -> None:
        used = sorted({ch for t in texts for ch in t if is_hangul(ch)})
        pool = [c for c in kanji_pool() if c not in reserved]
        if len(used) > len(pool):
            raise ValueError(f"한글 {len(used)}자를 짝지을 한자가 모자랍니다 ({len(pool)}자 남음)")
        self.pairs: dict[str, str] = dict(zip(used, pool))

    def substitute(self, text: str) -> str:
        """한글을 짝 한자로 바꾼 글. CP932 로 담을 수 없는 나머지 글자는 ``?`` 로."""
        out = []
        for ch in text:
            sub = self.pairs.get(ch)
            if sub is not None:
                out.append(sub)
                continue
            try:
                ch.encode("cp932")
                out.append(ch)
            except UnicodeEncodeError:
                out.append("?")
        return "".join(out)

    def encode(self, text: str) -> bytes:
        return self.substitute(text).encode("cp932")


# ------------------------------------------------------------------ TTF 읽고 쓰기

def _u16(b: bytes, o: int) -> int:
    return struct.unpack_from(">H", b, o)[0]


def _checksum(data: bytes) -> int:
    pad = data + b"\0" * (-len(data) % 4)
    return sum(struct.unpack(">%dI" % (len(pad) // 4), pad)) & 0xFFFFFFFF


class Ttf:
    def __init__(self, data: bytes) -> None:
        if data[:4] not in (b"\x00\x01\x00\x00", b"true", b"OTTO"):
            raise ValueError("TrueType 글꼴이 아닙니다(.ttc 모음이나 다른 형식)")
        if data[:4] == b"OTTO":
            raise ValueError("OpenType(CFF) 글꼴은 아직 못 다룹니다")
        self.sfnt = data[:4]
        n = _u16(data, 4)
        self.tables: dict[str, bytes] = {}
        for i in range(n):
            tag, _cs, off, ln = struct.unpack_from(">4sIII", data, 12 + 16 * i)
            self.tables[tag.decode("latin-1")] = data[off:off + ln]

    @classmethod
    def load(cls, path: Path) -> "Ttf":
        return cls(Path(path).read_bytes())

    # --- cmap
    def cmap(self) -> dict[int, int]:
        """유니코드 → 글자 모양 번호 (BMP + 보조 평면)."""
        t = self.tables["cmap"]
        n = _u16(t, 2)
        best: tuple[int, int] | None = None
        for i in range(n):
            pid, eid, off = struct.unpack_from(">HHI", t, 4 + 8 * i)
            if (pid, eid) in ((3, 10), (0, 4), (0, 6)):
                best = (off, 12)
                break
            if (pid, eid) in ((3, 1), (0, 3)) and best is None:
                best = (off, 4)
        if best is None:
            raise ValueError("유니코드 cmap 이 없습니다")
        return self._read_sub(t, best[0])

    @staticmethod
    def _read_sub(t: bytes, off: int) -> dict[int, int]:
        fmt = _u16(t, off)
        out: dict[int, int] = {}
        if fmt == 4:
            segx2 = _u16(t, off + 6)
            seg = segx2 // 2
            ends = struct.unpack_from(">%dH" % seg, t, off + 14)
            starts = struct.unpack_from(">%dH" % seg, t, off + 16 + segx2)
            deltas = struct.unpack_from(">%dh" % seg, t, off + 16 + 2 * segx2)
            ro_at = off + 16 + 3 * segx2
            ros = struct.unpack_from(">%dH" % seg, t, ro_at)
            for i in range(seg):
                for c in range(starts[i], ends[i] + 1):
                    if c == 0xFFFF:
                        continue
                    if ros[i] == 0:
                        g = (c + deltas[i]) & 0xFFFF
                    else:
                        p = ro_at + 2 * i + ros[i] + 2 * (c - starts[i])
                        g = _u16(t, p) if p + 2 <= len(t) else 0
                        if g:
                            g = (g + deltas[i]) & 0xFFFF
                    if g:
                        out[c] = g
        elif fmt == 12:
            ng = struct.unpack_from(">I", t, off + 12)[0]
            for i in range(ng):
                s, e, g = struct.unpack_from(">III", t, off + 16 + 12 * i)
                for c in range(s, e + 1):
                    out[c] = g + (c - s)
        else:
            raise ValueError(f"cmap 형식 {fmt} 은 못 읽습니다")
        return out

    @staticmethod
    def _build_fmt4(m: dict[int, int]) -> bytes:
        codes = sorted(c for c in m if c < 0xFFFF)
        segs: list[tuple[int, int, int]] = []          # (start, end, delta)
        for c in codes:
            d = (m[c] - c) & 0xFFFF
            if segs and segs[-1][1] == c - 1 and segs[-1][2] == d:
                segs[-1] = (segs[-1][0], c, d)
            else:
                segs.append((c, c, d))
        segs.append((0xFFFF, 0xFFFF, 1))
        n = len(segs)
        sr = 2 * (1 << (n.bit_length() - 1))
        body = struct.pack(">HHHH", n * 2, sr, (n.bit_length() - 1), n * 2 - sr)
        body += b"".join(struct.pack(">H", s[1]) for s in segs) + b"\0\0"
        body += b"".join(struct.pack(">H", s[0]) for s in segs)
        body += b"".join(struct.pack(">H", s[2]) for s in segs)
        body += b"\0\0" * n
        length = 6 + len(body)
        if length > 0xFFFF:
            raise ValueError("cmap 이 너무 커서 형식 4 로 담을 수 없습니다")
        return struct.pack(">HHH", 4, length, 0) + body

    @staticmethod
    def _build_fmt12(m: dict[int, int]) -> bytes:
        groups: list[tuple[int, int, int]] = []
        for c in sorted(m):
            if groups and groups[-1][1] == c - 1 and groups[-1][2] + (c - groups[-1][0]) == m[c]:
                groups[-1] = (groups[-1][0], c, groups[-1][2])
            else:
                groups.append((c, c, m[c]))
        body = b"".join(struct.pack(">III", s, e, g) for s, e, g in groups)
        return struct.pack(">HHIII", 12, 0, 16 + len(body), 0, len(groups)) + body

    def set_cmap(self, m: dict[int, int]) -> None:
        subs = [(3, 1, self._build_fmt4(m))]
        if any(c > 0xFFFF for c in m):
            subs.append((3, 10, self._build_fmt12(m)))
        head = struct.pack(">HH", 0, len(subs))
        off = 4 + 8 * len(subs)
        recs, blobs = b"", b""
        for pid, eid, blob in subs:
            recs += struct.pack(">HHI", pid, eid, off + len(blobs))
            blobs += blob
        self.tables["cmap"] = head + recs + blobs

    # --- 이름
    def set_names(self, family: str) -> None:
        full = family
        ps = family.replace(" ", "")
        names = {1: family, 2: "Regular", 3: f"{ps};Gameloc", 4: full, 5: "Version 1.0", 6: ps}
        recs, strs = [], b""
        entries = []
        for nid, s in names.items():
            entries.append((1, 0, 0, nid, s.encode("mac_roman")))
            entries.append((3, 1, 0x409, nid, s.encode("utf-16-be")))
        entries.sort(key=lambda e: (e[0], e[1], e[2], e[3]))
        for pid, eid, lid, nid, raw in entries:
            recs.append(struct.pack(">HHHHHH", pid, eid, lid, nid, len(raw), len(strs)))
            strs += raw
        head = struct.pack(">HHH", 0, len(entries), 6 + 12 * len(entries))
        self.tables["name"] = head + b"".join(recs) + strs

    def claim_japanese(self) -> None:
        """OS/2 에 '일본어 코드 페이지(932)' 지원 표시. 엔진이 일본어 글자 집합으로 글꼴을 부를 때
        글꼴 매퍼가 다른 글꼴로 바꿔치지 않게 합니다."""
        t = bytearray(self.tables.get("OS/2", b""))
        if len(t) >= 86 and _u16(bytes(t), 0) >= 1:
            cp1 = struct.unpack_from(">I", t, 78)[0] | (1 << 17) | (1 << 19) | 1
            struct.pack_into(">I", t, 78, cp1)
            self.tables["OS/2"] = bytes(t)

    # --- 저장
    def to_bytes(self) -> bytes:
        tables = {k: v for k, v in self.tables.items() if k != "DSIG"}   # 고친 뒤엔 서명이 틀립니다
        tables["head"] = tables["head"][:8] + b"\0\0\0\0" + tables["head"][12:]
        tags = sorted(tables)
        n = len(tags)
        sr = 16 * (1 << (n.bit_length() - 1))
        out = bytearray(self.sfnt + struct.pack(">HHHH", n, sr, n.bit_length() - 1, n * 16 - sr))
        offset = 12 + 16 * n
        body = b""
        for tag in tags:
            data = tables[tag]
            out += struct.pack(">4sIII", tag.encode("latin-1"), _checksum(data), offset + len(body), len(data))
            body += data + b"\0" * (-len(data) % 4)
        font = bytearray(bytes(out) + body)
        adj = (0xB1B0AFBA - _checksum(bytes(font))) & 0xFFFFFFFF
        head_off = None
        for i, tag in enumerate(tags):
            if tag == "head":
                head_off = struct.unpack_from(">I", font, 12 + 16 * i + 8)[0]
        struct.pack_into(">I", font, head_off + 8, adj)
        return bytes(font)


PREFERRED_FONTS = ("nanumgothic.ttf", "nanumbarungothic.ttf", "notosanskr-vf.ttf",
                   "notosanskr-regular.ttf", "malgun.ttf")


def font_dirs() -> list[Path]:
    import os

    dirs = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    dirs += [Path.home() / ".fonts", Path("/usr/share/fonts"), Path("/Library/Fonts")]
    return [d for d in dirs if d.is_dir()]


def find_base_font() -> Path:
    """한글 모양을 빌려 올 TrueType 글꼴. 수정·재배포가 허용되는 열린 글꼴(나눔고딕·노토)을 먼저 찾습니다."""
    found: dict[str, Path] = {}
    for d in font_dirs():
        try:
            for p in d.rglob("*"):
                if p.suffix.lower() == ".ttf":
                    found.setdefault(p.name.lower(), p)
        except OSError:
            continue
    for name in PREFERRED_FONTS:
        if name in found:
            return found[name]
    raise FileNotFoundError("한글 모양을 빌려 올 TrueType(.ttf) 글꼴을 이 컴퓨터에서 찾지 못했습니다 "
                            "(나눔고딕·노토 산스 KR 등을 설치하세요)")


def build_font(base: Path, mapping: Mapping, family: str = FAMILY) -> bytes:
    """``base`` 글꼴에서 '짝 한자 → 그 한글의 모양' 연결을 더한 글꼴을 만듭니다."""
    ttf = Ttf.load(base)
    cmap = ttf.cmap()
    missing = [h for h in mapping.pairs if ord(h) not in cmap]
    if missing:
        raise ValueError(f"고른 글꼴에 한글 {len(missing)}자의 모양이 없습니다 (예: {''.join(missing[:5])})")
    glyph_of = {h: cmap[ord(h)] for h in mapping.pairs}
    # 게임 글자는 짝 한자 코드로만 들어오므로 한글 음절 칸(1만 1천여 개)은 대응표에서 뺍니다.
    # 형식 4 대응표는 크기 한도(65,535바이트)가 있어서 이래야 들어갑니다. 글자 모양 자체는 그대로입니다.
    cmap = {c: g for c, g in cmap.items()
            if not (HANGUL_FIRST <= c <= HANGUL_LAST) and c <= 0xFFFF}
    for h, k in mapping.pairs.items():
        cmap[ord(k)] = glyph_of[h]
    ttf.set_cmap(cmap)
    ttf.set_names(family)
    ttf.claim_japanese()
    return ttf.to_bytes()
