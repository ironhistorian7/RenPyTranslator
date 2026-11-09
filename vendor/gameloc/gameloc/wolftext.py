# SPDX-License-Identifier: GPL-3.0-or-later
"""울프RPG 에디터 데이터 파일 안의 글자를 찾고, 그 자리만 바꿔 다시 씁니다.

대상 파일(``Data/BasicData``·``Data/MapData``):

- ``CommonEvent.dat`` — 공통 이벤트. 대사·선택지 대부분이 여기 있습니다.
- ``DataBase.dat`` / ``CDataBase.dat`` / ``SysDatabase.dat`` (+ 같은 이름의 ``.project``)
  — 아이템·스킬 이름과 설명, 메뉴 문구. 글자 값은 ``.dat`` 쪽에, 칸 이름(편집기용)은
  ``.project`` 쪽에 있습니다.
- ``*.mps`` — 맵. 맵 이벤트의 대사.
- ``Game.dat`` — 창 제목.

## 왜 '그 자리만' 바꾸나

울프 파일은 처음부터 끝까지 ``[u32 길이][글자][0]`` 과 숫자가 차례로 놓인 모양이고,
파일 안 어디에도 "몇 번째 바이트로 가라" 는 절대 위치가 없습니다(``Game.dat`` 의 크기
칸 하나만 예외라 따로 맞춥니다). 그래서 구조를 한 번 끝까지 읽어 **글자 칸의 위치만**
기억해 두고, 그 칸만 새 길이로 갈아 끼우면 나머지 바이트는 원본 그대로입니다. 모르는
칸을 다시 만들어 쓰다 틀릴 일이 없습니다.

3.5 부터는 본문을 LZ4 로 한 번 더 묶습니다(``CommonEvent.dat`` 버전 0x93/0xCC,
데이터베이스 0xC4, 맵 0x65 이상). 풀어서 바꾸고 다시 묶습니다.

구조는 Sinflower 의 UberWolf(MIT, https://github.com/Sinflower/UberWolf) 의 WolfRPG
파서를 보고 옮겼습니다. 고지는 THIRD-PARTY-NOTICES.txt.
"""

from __future__ import annotations

import dataclasses
import re
import struct

# ------------------------------------------------------------------ LZ4 (블록)


def lz4_decompress(src: bytes, size: int) -> bytes:
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        tok = src[i]
        i += 1
        lit = tok >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        out += src[i:i + lit]
        i += lit
        if i >= n:
            break
        off = src[i] | (src[i + 1] << 8)
        i += 2
        ml = tok & 15
        if ml == 15:
            while True:
                b = src[i]
                i += 1
                ml += b
                if b != 255:
                    break
        ml += 4
        start = len(out) - off
        if off <= 0 or start < 0:
            raise ValueError("LZ4: 잘못된 거리")
        if off >= ml:
            out += out[start:start + ml]
        else:
            chunk = bytes(out[start:])
            out += (chunk * (ml // off + 1))[:ml]
    if len(out) != size:
        raise ValueError(f"LZ4: 풀린 크기가 다릅니다 ({len(out)} != {size})")
    return bytes(out)


def _lz4_len(out: bytearray, rest: int) -> None:
    while rest >= 255:
        out.append(255)
        rest -= 255
    out.append(rest)


def lz4_compress(src: bytes) -> bytes:
    """표준 LZ4 블록. 빠르지 않아도 되니 단순한 탐욕 방식입니다."""
    n = len(src)
    out = bytearray()
    table: dict[bytes, int] = {}
    anchor = i = 0
    mflimit = n - 12                      # 마지막 일치는 끝에서 12바이트 앞에서 시작
    while i < mflimit:
        key = src[i:i + 4]
        cand = table.get(key)
        table[key] = i
        if cand is None or i - cand > 0xFFFF:
            i += 1
            continue
        limit = n - 5 - i                 # 마지막 5바이트는 늘 글자 그대로
        ml = 4
        while ml < limit:
            step = min(64, limit - ml)
            if src[cand + ml:cand + ml + step] == src[i + ml:i + ml + step]:
                ml += step
                continue
            while ml < limit and src[cand + ml] == src[i + ml]:
                ml += 1
            break
        lit = i - anchor
        m = ml - 4
        out.append((min(lit, 15) << 4) | min(m, 15))
        if lit >= 15:
            _lz4_len(out, lit - 15)
        out += src[anchor:i]
        out += struct.pack("<H", i - cand)
        if m >= 15:
            _lz4_len(out, m - 15)
        i += ml
        anchor = i
    lit = n - anchor
    out.append(min(lit, 15) << 4)
    if lit >= 15:
        _lz4_len(out, lit - 15)
    out += src[anchor:]
    return bytes(out)


# ------------------------------------------------------------------ 읽기


class WolfFormatError(Exception):
    pass


@dataclasses.dataclass
class Slot:
    at: int             # 풀린 스트림에서 u32 길이 칸의 위치
    size: int           # 길이 칸 4바이트 + 글자 바이트
    text: str
    ptr: str            # 구조로 정한 자리 이름 (``ev12/c34/s0``)
    kind: str           # message / choice / picture / setstring / db / title / other
    ctx: str = ""       # 사람이 볼 설명 (이벤트 이름, DB 칸 이름)
    cid: int = 0        # 이벤트 명령 번호 (명령 안의 글자일 때)


class _R:
    def __init__(self, buf: bytes, pos: int, utf8: bool) -> None:
        self.b, self.p, self.utf8 = buf, pos, utf8

    def u8(self) -> int:
        v = self.b[self.p]
        self.p += 1
        return v

    def u32(self) -> int:
        v = struct.unpack_from("<I", self.b, self.p)[0]
        self.p += 4
        return v

    def skip(self, n: int) -> None:
        if n < 0 or self.p + n > len(self.b):
            raise WolfFormatError(f"범위를 넘었습니다 (@{self.p}+{n})")
        self.p += n

    def expect(self, v: int, what: str) -> None:
        got = self.u8()
        if got != v:
            raise WolfFormatError(f"{what}: 0x{v:02X} 자리에 0x{got:02X} (@{self.p - 1})")

    def s(self) -> tuple[str, int, int]:
        at = self.p
        n = self.u32()
        if n == 0 or self.p + n > len(self.b):
            raise WolfFormatError(f"글자 길이가 이상합니다 ({n} @{at})")
        raw = self.b[self.p:self.p + n]
        self.p += n
        if raw[-1:] == b"\0":
            raw = raw[:-1]
        text = raw.decode("utf-8" if self.utf8 else "cp932", "replace")
        return text, at, 4 + n


def encode(text: str, utf8: bool, encoder=None) -> bytes:
    """``[u32 길이][글자][0]``. SJIS 파일에 못 담는 글자면 ``UnicodeEncodeError``.

    ``encoder`` 가 있으면 그것이 글을 바이트로 바꿉니다(한글을 짝 한자로 바꿔 담는 SJIS 파일용,
    ``sjiskorean`` 참고)."""
    body = (encoder(text) if encoder else text.encode("utf-8" if utf8 else "cp932")) + b"\0"
    return struct.pack("<I", len(body)) + body


# 명령 번호 (UberWolf Command.hpp)
MESSAGE, CHOICES, COMMENT, DEBUG_MSG = 101, 102, 103, 106
STRING_COND, SET_STRING, PICTURE, MOVE = 112, 122, 150, 201
DATABASE, EVENT_BY_NAME, CALL_BY_ID = 250, 300, 210


def _command(r: _R, v35: bool, base: str, ci: int, ctx: str,
             slots: list[Slot], refs: set[str]) -> None:
    argc = (r.u8() - 1) & 0xFF
    cid = r.u32()
    args = [r.u32() for _ in range(argc)]
    r.u8()                                          # 들여쓰기
    strs = [r.s() for _ in range(r.u8())]
    term = r.u8()
    if term == 1 or (term == 0 and cid == MOVE):
        r.skip(5)
        r.u8()
        for _ in range(r.u32()):
            r.u8()
            r.skip(4 * r.u8())
            r.expect(1, "이동 경로")
            r.expect(0, "이동 경로")
    elif term != 0:
        raise WolfFormatError(f"명령 끝 표지가 {term} (@{r.p - 1})")
    if v35:
        r.skip(r.u8())

    for k, (text, at, size) in enumerate(strs):
        ptr = f"{base}/c{ci}/s{k}"
        if cid == MESSAGE and k == 0:
            kind = "message"
        elif cid == CHOICES:
            kind = "choice"
        elif cid == PICTURE and k == 0 and args and ((args[0] >> 4) & 7) == 2:
            kind = "picture"
        elif cid == SET_STRING and k == 0:
            kind = "setstring"
        elif (cid == EVENT_BY_NAME and k >= 1) or cid == CALL_BY_ID:
            # 다른 공통 이벤트를 부르면서 넘기는 **글자 인자**(전투 메시지, 알림, 확인창 문구, 간판 이름…).
            # 이름으로 부르는 쪽의 첫 글자(부르는 이벤트 이름)는 아래에서 열쇠로 모읍니다.
            kind = "callarg"
        elif cid == DATABASE and k == 0 and text and len(strs) >= 4 and strs[3][0]:
            # DB 칸에 **글을 써 넣는** 명령(`[써 넣을 글, DB 이름, 자료 이름, 칸 이름]`). 도움말·항목 설명처럼
            # 화면 글을 DB 에 담아 두는 게임이 있습니다. 칸 이름이 비어 있으면 검색·비교라 열쇠입니다.
            slots.append(Slot(at, size, text, ptr, "dbwrite", f"{ctx} › {strs[1][0]}", cid))
            continue
        else:
            kind = "other"
            # 게임이 **글자 그대로 비교·검색하는** 값. 같은 글자를 다른 데서 번역하면
            # 비교가 어긋나므로 모아 두었다가 뽑을 때 뺍니다.
            if cid in (STRING_COND, DATABASE, EVENT_BY_NAME):
                refs.add(text)
        slots.append(Slot(at, size, text, ptr, kind, ctx, cid))


def _parse_common_events(r: _R, v35: bool, slots: list[Slot], refs: set[str]) -> None:
    for _ in range(r.u32()):
        r.expect(0x8E, "공통 이벤트 머리")
        eid = r.u32()
        r.u32()
        r.skip(7)
        name, _at, _sz = r.s()
        refs.add(name)                               # 이름으로 부르는 공통 이벤트
        for ci in range(r.u32()):
            _command(r, v35, f"ev{eid}", ci, name, slots, refs)
        r.s()
        r.s()                                        # 설명(편집기용)
        r.expect(0x8F, "공통 이벤트 자료")
        for _ in range(r.u32()):
            r.s()
        r.skip(r.u32())
        for _ in range(r.u32()):
            for _ in range(r.u32()):
                r.s()
        for _ in range(r.u32()):
            r.skip(4 * r.u32())
        r.skip(0x1D)
        for _ in range(100):
            r.s()
        r.expect(0x91, "공통 이벤트 끝")
        r.s()
        tail = r.u8()
        if tail == 0x92:
            r.s()
            r.u32()
            r.expect(0x92, "공통 이벤트 끝")
        elif tail != 0x91:
            raise WolfFormatError(f"공통 이벤트 끝 표지가 0x{tail:02X}")
    if r.u8() < 0x89:
        raise WolfFormatError("공통 이벤트 파일 끝 표지가 이상합니다")


def _parse_map(r: _R, version: int, slots: list[Slot], refs: set[str]) -> None:
    v35 = version >= 0x67
    r.s()
    r.u32()
    w, h = r.u32(), r.u32()
    n_events = r.u32()
    layers = 3
    if v35:
        r.u32()
        layers = r.u32()
    if r.utf8 and struct.unpack_from("<i", r.b, r.p)[0] == -1:
        r.skip(4)
    else:
        r.skip(w * h * layers * 4)
    seen = 0
    while (ind := r.u8()) == 0x6F:
        if r.b[r.p:r.p + 4] != b"\x39\x30\0\0":
            raise WolfFormatError("맵 이벤트 머리가 이상합니다")
        r.skip(4)
        eid = r.u32()
        ename, _at, _sz = r.s()
        r.u32()
        r.u32()
        n_pages = r.u32()
        r.skip(4)
        pages = 0
        while (pind := r.u8()) == 0x79:
            r.u32()
            r.s()                                    # 그림 파일
            r.skip(4 + 37 + 4 + 2)
            for _ in range(r.u32()):
                r.u8()
                r.skip(4 * r.u8())
                r.expect(1, "이동 경로")
                r.expect(0, "이동 경로")
            for ci in range(r.u32()):
                _command(r, v35, f"e{eid}/p{pages}", ci, ename, slots, refs)
            features = r.u32()
            r.skip(3)
            if features > 3:
                r.skip(1)
            r.expect(0x7A, "맵 페이지 끝")
            pages += 1
        if pind != 0x70 or pages != n_pages:
            raise WolfFormatError("맵 이벤트 페이지 수가 맞지 않습니다")
        seen += 1
    if ind != 0x66 or seen != n_events:
        raise WolfFormatError("맵 이벤트 수가 맞지 않습니다")


@dataclasses.dataclass
class DbProject:
    """``.project`` 에서 읽은 이름들(편집기용). 글자 값의 뜻을 알려 주는 데만 씁니다."""
    types: list[tuple[str, list[str], list[str]]]    # (타입 이름, 칸 이름들, 자료 이름들)


def parse_project(data: bytes, utf8: bool) -> DbProject:
    r = _R(data, 0, utf8)
    types = []
    for _ in range(r.u32()):
        tname = r.s()[0]
        fields = [r.s()[0] for _ in range(r.u32())]
        datas = [r.s()[0] for _ in range(r.u32())]
        r.s()
        r.skip(r.u32())
        for _ in range(r.u32()):
            r.s()
        for _ in range(r.u32()):
            for _ in range(r.u32()):
                r.s()
        for _ in range(r.u32()):
            r.skip(4 * r.u32())
        r.skip(4 * r.u32())
        types.append((tname, fields, datas))
    if r.p != len(data):
        raise WolfFormatError(".project 뒤에 모르는 자료가 남았습니다")
    return DbProject(types)


def _parse_db(r: _R, version: int, proj: DbProject | None,
              slots: list[Slot]) -> None:
    n_types = r.u32()
    if proj is not None and len(proj.types) != n_types:
        proj = None                                  # 짝이 안 맞으면 이름 없이
    for ti in range(n_types):
        if r.b[r.p:r.p + 4] != b"\xFE\xFF\xFF\xFF":
            raise WolfFormatError("데이터베이스 타입 구분자가 이상합니다")
        r.skip(4)
        unk = r.u32()
        n_fields = r.u32()
        if unk == 0x1D4C0:
            r.s()
        infos = [r.u32() for _ in range(n_fields)]
        str_fields = [i for i, v in enumerate(infos) if v >= 0x7D0]
        n_int = n_fields - len(str_fields)
        # 글자 칸은 인덱스 순서대로 저장됩니다
        str_fields.sort(key=lambda i: infos[i] - 0x7D0)
        tname, fnames, dnames = (proj.types[ti] if proj else ("", [], []))
        for di in range(r.u32()):
            r.skip(4 * n_int)
            dname = dnames[di] if di < len(dnames) else ""
            for k in range(len(str_fields)):
                text, at, size = r.s()
                fi = str_fields[k]
                fname = fnames[fi] if fi < len(fnames) else ""
                ctx = " › ".join(x for x in (tname, dname, fname) if x)
                slots.append(Slot(at, size, text, f"t{ti}/d{di}/f{fi}", "db", ctx))
    if r.u8() != version:
        raise WolfFormatError("데이터베이스 끝 표지가 이상합니다")


# ------------------------------------------------------------------ 파일 단위


CE_MAGIC = b"W\0\0OL\0FC\0"
DB_MAGIC = b"W\0\0OL\0FM\0"
MAP_MAGIC = b"\0" * 10 + b"WOLFM\0\0\0\0\0"


def _magic_ok(got: bytes, want: bytes, utf8_idx: int) -> bool | None:
    """맞으면 UTF-8 여부, 안 맞으면 ``None``."""
    if got == want:
        return False
    alt = bytearray(want)
    alt[utf8_idx] = 0x55
    return True if got == bytes(alt) else None


@dataclasses.dataclass
class WolfText:
    """한 파일을 읽은 결과. ``rebuild`` 로 바꾼 파일을 만듭니다."""
    kind: str                   # common / db / map / game
    utf8: bool
    stream: bytes               # 머리 + (풀린) 본문
    head_len: int               # LZ4 로 묶인 경우 머리 길이, 아니면 0
    slots: list[Slot]
    refs: set[str]
    game_dat: tuple[int, int] | None = None     # Game.dat 크기 칸 위치, 원래 파일 크기
    encoder: object = None                      # 글 → 바이트 (기본: 파일 인코딩 그대로)

    def rebuild(self, edits: dict[str, str]) -> tuple[bytes, int]:
        """``edits`` (ptr → 새 글자) 를 넣은 파일 바이트와 넣은 개수."""
        by_ptr = {s.ptr: s for s in self.slots}
        todo = sorted(((by_ptr[p], t) for p, t in edits.items() if p in by_ptr),
                      key=lambda x: x[0].at)
        parts, last, n = [], 0, 0
        for s, new in todo:
            if new == s.text:
                continue
            parts += [self.stream[last:s.at], encode(new, self.utf8, self.encoder)]
            last = s.at + s.size
            n += 1
        parts.append(self.stream[last:])
        out = b"".join(parts)
        if self.game_dat is not None and n:
            out = _fix_game_dat(out, self)
        if self.head_len:
            body = out[self.head_len:]
            packed = lz4_compress(body)
            out = out[:self.head_len] + struct.pack("<II", len(body), len(packed)) + packed
        return out, n


def parse(name: str, data: bytes, project: bytes | None = None) -> WolfText:
    """파일 이름으로 형식을 고릅니다. 모르는 파일·깨진 파일이면 ``WolfFormatError``."""
    try:
        return _parse(name, data, project)
    except (struct.error, IndexError, UnicodeDecodeError) as exc:
        raise WolfFormatError(f"{name}: 파일이 예상과 다릅니다 ({exc})") from None


def _parse(name: str, data: bytes, project: bytes | None) -> WolfText:
    low = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if low.endswith(".txt"):
        return _load_txt(data)
    if low == "commonevent.dat":
        return _load_common(data)
    if low == "game.dat":
        return _load_game(data)
    if low.endswith(".mps"):
        return _load_map(data)
    if low.endswith(".dat") and low.replace(".dat", "") in ("database", "cdatabase", "sysdatabase"):
        return _load_db(data, project)
    raise WolfFormatError(f"{name}: 글자를 읽을 줄 모르는 파일")


# ------------------------------------------------------------------ 장면 글 파일 (.txt)
#
# 게임에 따라 이벤트가 ``Data/Evtext/a01_01h.txt`` 같은 **글 파일을 읽어** 장면 대사를 보여 줍니다
# (RJ01724124: 656개, 일본어 11만 줄). 이 파일들은 울프 데이터 파일이 아니라 제작자가 정한 대본 문법이라
# 구조가 게임마다 다르지만, 줄 종류는 대체로 이렇습니다.
#
#   //설명 · /명령,인자…   ← 건드리지 않음 (/evcg, /胸+1 …)
#   セリス：2,8           ← 표정·자세 지시. 캐릭터 이름이 열쇠라서 건드리지 않음
#   セリス：              ← 화자 줄(보이는 이름)
#   본문 줄들            ← 한 대사창. 빈 줄이나 다른 종류의 줄에서 끝남
#
# 파일 글자 코드는 Shift-JIS 인 채로 쓰이는데, **울프 3.5 UTF-8 판은 UTF-8 로 써도 알아서 읽습니다**
# (RJ01724124 에서 한글이 든 UTF-8 파일로 장면을 돌려 확인). 그래서 바꾼 파일은 UTF-8 로 씁니다.
# 한 번 풀어 둔 칸의 번호(ptr)는 번호 순서입니다 — 번역하면 일본어가 사라져 '일본어가 든 칸만' 세는 방식은
# 다시 읽을 때 번호가 밀리므로, 일본어 여부와 상관없이 모든 글 덩이에 번호를 매깁니다.

SCENE, SPEAKER = "scene", "speaker"
_T_BLANK, _T_CMD, _T_HEAD, _T_DIRECTIVE, _T_SPEAKER, _T_TEXT = range(6)
_RE_SPEAKER = re.compile(r"^([^\s：/@＠][^：/@＠]{0,30})：$")      # 번역한 이름은 '가게 주인' 처럼 띄어쓰기가 있을 수 있음
_RE_DIRECTIVE = re.compile(r"^[^\s：]{1,16}：\d+(?:,\d+)*$")
_RE_HEAD = re.compile(r"^[@＠]\d+$")


def _txt_kind(line: str) -> tuple[int, str]:
    s = line.strip()
    if not s:
        return _T_BLANK, ""
    if s.startswith("/"):
        return _T_CMD, ""
    if _RE_HEAD.match(s):
        return _T_HEAD, ""
    if _RE_DIRECTIVE.match(s):
        return _T_DIRECTIVE, ""
    m = _RE_SPEAKER.match(s)
    if m:
        return _T_SPEAKER, m.group(1)
    return _T_TEXT, ""


@dataclasses.dataclass
class TxtText(WolfText):
    """장면 글 파일. 줄 단위로 읽고, 바꾼 줄만 다시 써서 UTF-8 로 냅니다."""
    lines: list[str] = dataclasses.field(default_factory=list)
    nl: str = "\r\n"
    bom: bool = False

    def rebuild(self, edits: dict[str, str]) -> tuple[bytes, int]:
        by_ptr = {s.ptr: s for s in self.slots}
        todo = [(by_ptr[p], t) for p, t in edits.items() if p in by_ptr and t != by_ptr[p].text]
        lines, n = list(self.lines), 0
        for s, new in sorted(todo, key=lambda x: -x[0].at):
            if s.kind == SPEAKER:
                name = " ".join(new.replace("：", "").split())
                if not name or len(name) > 30 or name[0] in "/@＠":       # 화자 줄로 다시 읽히지 않을 이름은 두지 않음
                    continue
                lines[s.at:s.at + s.size] = [name + "："]
            else:
                body = [ln.rstrip() for ln in new.replace("\r\n", "\n").replace("\r", "\n").split("\n")
                        if ln.strip()]
                if not body:
                    continue
                lines[s.at:s.at + s.size] = body
            n += 1
        if not n:
            return self.stream, 0
        out = self.nl.join(lines).encode("utf-8")
        return (b"\xef\xbb\xbf" + out if self.bom else out), n


def _txt_decode(data: bytes) -> tuple[str, bool]:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8"), True
    try:
        return data.decode("utf-8"), False
    except UnicodeDecodeError:
        return data.decode("cp932"), False


def _load_txt(data: bytes) -> WolfText:
    text, bom = _txt_decode(data)
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl)
    slots: list[Slot] = []
    i = 0
    while i < len(lines):
        kind, name = _txt_kind(lines[i])
        if kind == _T_SPEAKER:
            slots.append(Slot(i, 1, name, f"t{len(slots)}", SPEAKER))
            i += 1
        elif kind == _T_TEXT:
            j = i
            while j < len(lines) and _txt_kind(lines[j])[0] == _T_TEXT:
                j += 1
            who = slots[-1].text + "：" if slots and slots[-1].kind == SPEAKER \
                and slots[-1].at + 1 == i else ""
            slots.append(Slot(i, j - i, "\n".join(lines[i:j]), f"t{len(slots)}", SCENE, who))
            i = j
        else:
            i += 1
    return TxtText("txt", True, data, 0, slots, set(), lines=lines, nl=nl, bom=bom)


def _unpack(data: bytes, head_len: int) -> bytes:
    dec, enc = struct.unpack_from("<II", data, head_len)
    body = data[head_len + 8:head_len + 8 + enc]
    if head_len + 8 + enc != len(data):
        raise WolfFormatError("LZ4 묶음 크기가 파일과 맞지 않습니다")
    return data[:head_len] + lz4_decompress(body, dec)


def _load_common(data: bytes) -> WolfText:
    if data[:1] != b"\0":
        raise WolfFormatError("CommonEvent.dat: 암호가 걸려 있습니다(2.x 방식)")
    utf8 = _magic_ok(data[1:10], CE_MAGIC, 5)
    if utf8 is None:
        raise WolfFormatError("CommonEvent.dat 가 아닙니다")
    ver = data[10]
    v35 = ver in (0x93, 0xCC)
    stream = _unpack(data, 11) if v35 else data
    slots: list[Slot] = []
    refs: set[str] = set()
    r = _R(stream, 11, utf8)
    _parse_common_events(r, v35, slots, refs)
    if r.p != len(stream):
        raise WolfFormatError("CommonEvent.dat 뒤에 모르는 자료가 남았습니다")
    return WolfText("common", utf8, stream, 11 if v35 else 0, slots, refs)


def _load_db(data: bytes, project: bytes | None) -> WolfText:
    if data[:1] != b"\0":
        raise WolfFormatError("데이터베이스에 암호가 걸려 있습니다(2.x 방식)")
    utf8 = _magic_ok(data[1:10], DB_MAGIC, 5)
    if utf8 is None:
        raise WolfFormatError("데이터베이스 파일이 아닙니다")
    ver = data[10]
    packed = ver == 0xC4
    stream = _unpack(data, 11) if packed else data
    proj = None
    if project:
        try:
            proj = parse_project(project, utf8)
        except (WolfFormatError, struct.error, IndexError):
            proj = None
    slots: list[Slot] = []
    r = _R(stream, 11, utf8)
    _parse_db(r, ver, proj, slots)
    if r.p != len(stream):
        raise WolfFormatError("데이터베이스 뒤에 모르는 자료가 남았습니다")
    return WolfText("db", utf8, stream, 11 if packed else 0, slots, set())


def _load_map(data: bytes) -> WolfText:
    utf8 = _magic_ok(data[:20], MAP_MAGIC, 16)
    if utf8 is None:
        raise WolfFormatError("맵 파일이 아닙니다")
    ver = struct.unpack_from("<I", data, 20)[0]
    packed = ver >= 0x65
    stream = _unpack(data, 25) if packed else data
    slots: list[Slot] = []
    refs: set[str] = set()
    r = _R(stream, 25, utf8)
    _parse_map(r, ver, slots, refs)
    if r.p != len(stream):
        raise WolfFormatError("맵 뒤에 모르는 자료가 남았습니다")
    return WolfText("map", utf8, stream, 25 if packed else 0, slots, refs)


GAME_MAGIC = b"W\0\0OL\0FM\0"


def _load_game(data: bytes) -> WolfText:
    if data[:1] != b"\0":
        raise WolfFormatError("Game.dat: 암호가 걸려 있습니다")
    utf8 = _magic_ok(data[1:10], GAME_MAGIC, 8)
    if utf8 is None:
        raise WolfFormatError("Game.dat 가 아닙니다")
    r = _R(data, 10, utf8)
    r.skip(r.u32())
    count = r.u32()
    slots: list[Slot] = []
    t, at, size = r.s()
    slots.append(Slot(at, size, t, "title", "title", "창 제목"))
    magic = r.s()[0]
    if magic != "0000-0000":
        raise WolfFormatError("Game.dat 의 표식 글자가 다릅니다")
    r.skip(r.u32())
    # 글꼴 4개(본문 + 보조 3) + 기본 캐릭터 그림. 글꼴 이름은 번역 대상이 아니지만 **한글 글꼴로
    # 바꿔 끼울 때** 쓰므로 자리를 기억해 둡니다(``kind="font"`` 는 뽑기에서 빠집니다).
    for i, name in enumerate(("font", "subfont1", "subfont2", "subfont3", "pc_graphic")):
        t, at, size = r.s()
        if i < 4:
            slots.append(Slot(at, size, t, name, "font", "글꼴 이름"))
    if count >= 9:
        t, at, size = r.s()
        slots.append(Slot(at, size, t, "title_plus", "other", "제목 덧붙임"))
    if count > 9:
        r.s()
        r.s()
        for name in ("startup_msg", "title_msg"):
            t, at, size = r.s()
            slots.append(Slot(at, size, t, name, "other", name))
    if count > 13:
        r.s()
    size_at = r.p
    return WolfText("game", utf8, data, 0, slots, set(),
                    game_dat=(size_at, len(data)))


def _fix_game_dat(out: bytes, wt: WolfText) -> bytes:
    """``Game.dat`` 의 크기 칸과 뒤따르는 두 위치 칸을 늘어난 만큼 맞춥니다.

    UberWolf 가 하는 그대로입니다: 크기 칸 = 새 크기, 위치 칸 = 원래 값 + 차이.
    크기 칸이 원래 파일 크기와 관계가 안 보이면(모르는 판) 고치지 않고 예외를 냅니다.
    """
    size_at, old_len = wt.game_dat
    shift = len(out) - old_len
    old_size = struct.unpack_from("<I", wt.stream, size_at)[0]
    # 원래 크기 칸은 "앞 0 한 바이트를 뺀 파일 크기 + 암호 머리 9" 입니다.
    base = old_size - old_len
    if not -16 <= base <= 16:
        raise WolfFormatError("Game.dat 크기 칸을 알아보지 못했습니다")
    new_at = size_at + shift
    b = bytearray(out)
    struct.pack_into("<I", b, new_at, old_size + shift)
    p = new_at + 8
    words = struct.unpack_from("<I", b, p)[0]
    p += 4 + words * 2
    for _ in range(2):
        v = struct.unpack_from("<I", b, p)[0]
        struct.pack_into("<I", b, p, (v + shift) & 0xFFFFFFFF)
        p += 4
    return bytes(b)
