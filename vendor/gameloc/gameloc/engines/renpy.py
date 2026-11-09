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

"""Ren'Py 게임에서 대사를 뽑고, **원본을 건드리지 않고** 번역을 얹습니다.

## 왜 원본을 안 고치는가

RPG Maker 는 JSON 을 직접 고쳐야 했지만 Ren'Py 는 그럴 필요가 없습니다.
Ren'Py 에는 화면에 나갈 대사를 통째로 한 번 걸러주는 자리가 있습니다:

    config.say_menu_text_filter   — 모든 대사와 선택지
    config.replace_text           — 그 외 화면에 그려지는 모든 글자

그래서 번역문을 담은 json 하나와, 그걸 읽어 위 두 자리에 꽂아주는 짧은 .rpy
하나만 ``game/`` 에 **새로 넣으면** 됩니다. 원본 스크립트는 한 글자도 바뀌지
않습니다. 되돌리려면 넣은 파일 두 개를 지우면 끝이고, 번역이 틀렸거나
못 찾는 문장이 있어도 그냥 원문이 나올 뿐 게임이 죽지 않습니다.

## 어디서 원문을 읽는가

세 군데를 순서대로 봅니다.

1. ``game/**/*.rpy``     — 소스가 그대로 들어 있는 경우. 가장 정확합니다.
2. ``game/**/*.rpa``     — 묶음 파일. 안에서 .rpy / .rpyc 를 꺼냅니다.
3. ``game/**/*.rpyc``    — 소스를 지우고 컴파일본만 넣은 경우.

3번은 파이썬 pickle 로 굳어 있습니다. **실행하지 않고**(``pickletools``)
명령만 훑어서, ``'what'`` 이라는 키 바로 뒤에 오는 문자열 = 대사라는 규칙으로
꺼냅니다.
"""

from __future__ import annotations

import dataclasses
import io
import pickle
import pickletools
import re
import struct
import zlib
from pathlib import Path

from dataclasses import dataclass

from ..model import Location

# ---------------------------------------------------------------- 판별

def is_renpy(root: Path) -> Path | None:
    """Ren'Py 게임이면 ``game`` 폴더를, 아니면 ``None``."""
    root = Path(root)
    if not root.is_dir():
        return None
    # 게임 폴더를 통째로 압축 푼 경우 한 겹 더 들어가 있기도 합니다.
    bases = [root] + [p for p in sorted(root.iterdir()) if p.is_dir()]
    for base in bases:
        game = base / "game"
        if not game.is_dir():
            continue
        engine_here = (base / "renpy").is_dir() or (base / "lib").is_dir()
        content_here = any(game.glob("*.rpy")) or any(game.glob("*.rpyc")) \
            or any(game.glob("*.rpa"))
        if engine_here or content_here:
            return game
    return None


def version(game: Path) -> str:
    """``renpy/__init__.py`` 에 박힌 버전 문자열 (없으면 빈 문자열)."""
    init = game.parent / "renpy" / "__init__.py"
    try:
        head = init.read_text("utf-8", errors="ignore")[:8000]
    except OSError:
        return ""
    m = re.search(r"version_tuple\s*=\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", head)
    return ".".join(m.groups()) if m else ""


# ---------------------------------------------------------------- .rpa 묶음

# 목차를 되살리는 데 실제로 필요한 것만. 전부 값을 만드는 함수라 부작용이
# 없습니다. Ren'Py 는 파이썬 2 시절 형식(protocol 2)으로 목차를 굳혀서,
# 바이트열 하나에도 ``__builtin__.bytes`` 같은 지시가 들어갑니다.
_PICKLE_ALLOW = {
    ("__builtin__", "bytes"), ("builtins", "bytes"),
    ("__builtin__", "str"), ("builtins", "str"),
    ("__builtin__", "unicode"),
    ("_codecs", "encode"), ("codecs", "encode"),
}


class _SafeUnpickler(pickle.Unpickler):
    """목차만 읽습니다. 값을 만드는 함수 몇 개 말고는 전부 거부합니다."""

    def find_class(self, module, name):
        if (module, name) not in _PICKLE_ALLOW:
            raise pickle.UnpicklingError(f"허용하지 않는 객체: {module}.{name}")
        return getattr(__import__(module.replace("__builtin__", "builtins"),
                                  fromlist=[name]), name)


def rpa_index(path: Path) -> dict[str, tuple[int, int, bytes]]:
    """``{내부경로: (오프셋, 길이, 앞에 붙일 바이트)}``."""
    with open(path, "rb") as fh:
        header = fh.readline()
        if header.startswith(b"RPA-3.0"):
            parts = header.split()
            offset, key = int(parts[1], 16), int(parts[2], 16)
        elif header.startswith(b"RPA-2.0"):
            offset, key = int(header.split()[1], 16), 0
        else:
            raise ValueError("RPA-2.0/3.0 묶음이 아닙니다")
        fh.seek(offset)
        raw = zlib.decompress(fh.read())

    index = _SafeUnpickler(io.BytesIO(raw)).load()
    out: dict[str, tuple[int, int, bytes]] = {}
    for name, slots in index.items():
        if not slots:
            continue
        first = list(slots[0])
        off, length = int(first[0]) ^ key, int(first[1]) ^ key
        prefix = first[2] if len(first) > 2 else b""
        if isinstance(prefix, str):
            prefix = prefix.encode("utf-8", "surrogateescape")
        out[str(name)] = (off, length, prefix or b"")
    return out


def rpa_read(path: Path, entry: tuple[int, int, bytes]) -> bytes:
    off, length, prefix = entry
    with open(path, "rb") as fh:
        fh.seek(off)
        body = fh.read(max(0, length - len(prefix)))
    return prefix + body


# ---------------------------------------------------------------- .rpyc

RPYC2_MAGIC = b"RENPY RPC2"


def rpyc_payload(data: bytes) -> bytes | None:
    """.rpyc 안의 pickle 스트림(압축 해제본)."""
    if data.startswith(RPYC2_MAGIC):
        pos = len(RPYC2_MAGIC)
        while pos + 12 <= len(data):
            slot, offset, length = struct.unpack("<III", data[pos:pos + 12])
            pos += 12
            if slot == 0:
                break
            if slot == 1:
                try:
                    return zlib.decompress(data[offset:offset + length])
                except zlib.error:
                    return None
        return None
    try:                                  # 아주 옛날 형식은 통째로 압축
        return zlib.decompress(data)
    except zlib.error:
        return None


_STR_OPS = {"UNICODE", "BINUNICODE", "SHORT_BINUNICODE", "BINUNICODE8",
            "STRING", "BINSTRING", "SHORT_BINSTRING"}
_PUT_OPS = {"PUT", "BINPUT", "LONG_BINPUT"}
_GET_OPS = {"GET", "BINGET", "LONG_BINGET"}


def _as_text(arg) -> str | None:
    if isinstance(arg, str):
        return arg
    if isinstance(arg, (bytes, bytearray)):
        try:
            return bytes(arg).decode("utf-8")
        except UnicodeDecodeError:
            return None
    return None


def _pickle_tokens(payload: bytes) -> list[str | None]:
    """pickle 을 **실행하지 않고** 스택에 올라가는 값을 순서대로 늘어놓습니다.

    문자열이면 그 문자열, 아니면 ``None``.

    memo 를 따라가는 게 핵심입니다. 같은 문자열이 두 번째부터는 통째로 다시
    저장되지 않고 ``BINGET 3`` 처럼 번호로만 나옵니다. 대사 노드의 ``'what'``
    키가 딱 그 경우라, memo 를 안 따라가면 첫 줄만 잡히고 끝납니다.
    """
    tokens: list[str | None] = []
    memo: dict[object, str | None] = {}
    last: str | None = None
    try:
        for op, arg, _pos in pickletools.genops(payload):
            name = op.name
            if name in _PUT_OPS:
                memo[arg] = last
            elif name == "MEMOIZE":
                memo[len(memo)] = last
            elif name in _GET_OPS:
                last = memo.get(arg)
                tokens.append(last)
            elif len(op.stack_after) > len(op.stack_before):
                last = _as_text(arg) if name in _STR_OPS else None
                tokens.append(last)
    except Exception:                     # noqa: BLE001 - 깨진 파일은 그냥 포기
        pass
    return tokens


# 대사 노드의 상태 dict 에서 이 키 **바로 뒤**에 오는 문자열이 화면에 나갑니다.
SAY_KEYS = {"what"}
_JUNKY = re.compile(r"^[\w./\\-]+\.(rpy|rpyc|py|png|jpg|webp|ogg|mp3|wav|ttf)$", re.I)


def rpyc_texts(data: bytes) -> list[str]:
    """컴파일본에서 대사만. ``'what'`` 키 바로 뒤에 오는 값이 화면에 나갑니다."""
    payload = rpyc_payload(data)
    if not payload:
        return []
    tokens = _pickle_tokens(payload)
    out, seen = [], set()
    for i, tok in enumerate(tokens[:-1]):
        if tok in SAY_KEYS:
            nxt = tokens[i + 1]
            if nxt and nxt not in SAY_KEYS and nxt not in seen:
                seen.add(nxt)
                out.append(nxt)
    return out


# ---------------------------------------------------------------- .rpy 소스

# 대사:   e "안녕"      /   "안녕"      /   e "안녕" (what_color="#f00")
# 앞의 낱말(화자)은 있을 수도 없을 수도 있습니다.
_STR = r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\''
_SAY = re.compile(rf'^\s*(?:(?:[A-Za-z_]\w*(?:\.\w+)?)\s+)?({_STR})\s*(?:\(|$|#)')
_MENU_ITEM = re.compile(rf'^\s+({_STR})\s*(?:if\s+.+)?:\s*(?:#.*)?$')
_UNDERSCORE = re.compile(rf'\b_\(\s*({_STR})\s*[,)]')
_OLD_NEW = re.compile(rf'^\s*(old|new)\s+({_STR})\s*$')

_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "'": "'", "\\": "\\", " ": " "}


def unquote(lit: str) -> str:
    """Ren'Py 문자열 리터럴 → 화면에 실제로 넘어가는 문자열."""
    body = lit[1:-1]
    out, i = [], 0
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            out.append(_ESCAPES.get(nxt, nxt))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _strip_comment(line: str) -> str:
    """따옴표 밖의 ``#`` 부터 잘라냅니다."""
    quote = None
    for i, c in enumerate(line):
        if quote:
            if c == "\\":
                continue
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "#":
            return line[:i]
    return line


def rpy_texts(text: str) -> list[tuple[str, str]]:
    """``[(종류, 원문)]``. 종류는 say / menu / string."""
    out: list[tuple[str, str]] = []
    in_menu_block = False
    menu_indent = 0

    for raw_line in text.split("\n"):
        line = _strip_comment(raw_line)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())

        m = _OLD_NEW.match(line)
        if m:                             # 이미 있는 번역 블록은 건드리지 않습니다
            continue

        if re.match(r"^\s*menu\b.*:\s*$", line):
            in_menu_block, menu_indent = True, indent
            continue
        if in_menu_block and line.strip() and indent <= menu_indent:
            in_menu_block = False

        if in_menu_block:
            m = _MENU_ITEM.match(line)
            if m:
                out.append(("menu", unquote(m.group(1))))
                continue

        for m in _UNDERSCORE.finditer(line):
            out.append(("string", unquote(m.group(1))))

        m = _SAY.match(line)
        if m:
            out.append(("say", unquote(m.group(1))))
    return out


# ---------------------------------------------------------------- 모으기

SKIP_DIRS = {"tl", "cache", "saves", "__gameloc_backup__"}


OUR_FILES = {"zzz_gameloc.rpy", "zzz_gameloc.rpyc",
             "zzz_gameloc_font.rpy", "zzz_gameloc_font.rpyc"}


def _sources(game: Path, pick: list[str] | None = None):
    """``(표시용 경로, 종류, 바이트)`` 를 순서대로."""
    def usable(p: Path) -> bool:
        rel = p.relative_to(game).parts
        if p.name in OUR_FILES:           # 우리가 넣은 파일은 원문이 아닙니다
            return False
        return not any(part in SKIP_DIRS for part in rel[:-1])

    # ``pick`` 은 사람이 화면에서 고른 목록입니다. 주면 그것만 읽습니다 —
    # 자동 규칙(소스가 있으면 컴파일본은 뺀다)도 여기서는 안 씁니다.
    # 사람이 일부러 골랐다면 그 판단이 우리 규칙보다 우선입니다.
    want = set(pick) if pick else None

    def chosen(label: str) -> bool:
        return want is None or label in want

    rpy = sorted(p for p in game.rglob("*.rpy") if usable(p))
    for p in rpy:
        label = str(p.relative_to(game))
        if chosen(label):
            yield label, "rpy", p.read_bytes()

    have = {p.stem for p in rpy}
    for p in sorted(game.rglob("*.rpyc")):
        if not usable(p):
            continue
        label = str(p.relative_to(game))
        if want is None and p.stem in have:
            continue
        if chosen(label):
            yield label, "rpyc", p.read_bytes()

    for arc in sorted(game.rglob("*.rpa")):
        if not chosen(str(arc.relative_to(game))):
            continue
        try:
            index = rpa_index(arc)
        except Exception:                 # noqa: BLE001
            continue
        label = arc.relative_to(game)
        inner_rpy = {n for n in index if n.endswith(".rpy")}
        for name in sorted(index):
            if name.endswith(".rpy"):
                kind = "rpy"
            elif name.endswith(".rpyc") and name[:-1] not in inner_rpy:
                kind = "rpyc"
            else:
                continue
            try:
                yield f"{label}:{name}", kind, rpa_read(arc, index[name])
            except Exception:             # noqa: BLE001
                continue


# ---------------------------------------------------------------- 손으로 고르기
#
# 왜 필요한가 — ``_sources()`` 는 못 읽는 묶음을 **조용히 건너뜁니다**
# (``except: continue``). 그래서 사용자는 "왜 이 게임만 문장이 적지?" 를
# 알 길이 없습니다. 실제로 "rpa 수동 추출을 넣어달라" 는 요청이 왔는데,
# 그건 자동이 실패했다는 뜻입니다. 그러니 **고르게 해 주기 전에 먼저
# 무엇이 안 읽혔는지 말해 줘야** 합니다.


@dataclass
class Piece:
    """게임 안에서 찾은 원문 후보 하나."""

    label: str                 # 사용자에게 보여 줄 이름 (game 기준 상대 경로)
    kind: str                  # "rpy" | "rpyc" | "rpa"
    ok: bool = True            # 읽을 수 있나
    why: str = ""              # 못 읽으면 까닭
    inner: int = 0             # rpa 안에 든 대본 수
    used: bool = True          # 지금 설정으로 실제로 읽히나
    skipped_for: str = ""      # 안 읽힌다면 그 까닭

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def survey(game: Path) -> list[Piece]:
    """무엇이 있고, 무엇이 읽히고, 무엇이 왜 빠지는지.

    아무것도 안 바꿉니다. 화면에 목록을 보여 주기 위한 것입니다.
    """
    game = Path(game)
    out: list[Piece] = []

    def usable(p: Path) -> bool:
        rel = p.relative_to(game).parts
        if p.name in OUR_FILES:
            return False
        return not any(part in SKIP_DIRS for part in rel[:-1])

    rpy = sorted(p for p in game.rglob("*.rpy") if usable(p))
    for p in rpy:
        out.append(Piece(str(p.relative_to(game)), "rpy"))

    have = {p.stem for p in rpy}
    for p in sorted(game.rglob("*.rpyc")):
        if not usable(p):
            continue
        # 소스가 있으면 컴파일본은 안 읽습니다. 같은 내용을 두 번 읽을
        # 뿐이고, 소스 쪽이 훨씬 정확하니까요. 그래도 **목록에는 보여
        # 줍니다** — 소스가 옛날 것이라 컴파일본만 최신인 경우가 있습니다.
        dup = p.stem in have
        out.append(Piece(str(p.relative_to(game)), "rpyc", used=not dup,
                         skipped_for="같은 이름의 .rpy 가 있습니다" if dup else ""))

    for arc in sorted(game.rglob("*.rpa")):
        label = str(arc.relative_to(game))
        try:
            index = rpa_index(arc)
        except Exception as exc:                        # noqa: BLE001
            out.append(Piece(label, "rpa", ok=False, used=False,
                             why=_rpa_why(arc, exc)))
            continue
        n = sum(1 for k in index if k.endswith((".rpy", ".rpyc")))
        out.append(Piece(label, "rpa", inner=n, used=n > 0,
                         skipped_for="" if n else "안에 대본이 없습니다"))
    return out


def _rpa_why(path: Path, exc: Exception) -> str:
    """못 읽은 까닭을 **사람 말로**. 개발자 말은 안 씁니다."""
    try:
        head = path.open("rb").readline()[:16]
    except OSError:
        return "파일을 열지 못했습니다"
    if head.startswith(b"RPA-"):
        return ("묶음 머리는 맞는데 목차를 못 읽었습니다. "
                "손댄 묶음이거나 우리가 모르는 판입니다")
    if head.startswith((b"PK", b"RPA-1.0")):
        return "우리가 모르는 묶음 형식입니다"
    return "Ren'Py 묶음이 아닌 것 같습니다"


def unpack_rpa(arc: Path, dest: Path, *, scripts_only: bool = True,
               progress=lambda _m: None) -> list[str]:
    """묶음을 폴더로 풀어 놓습니다. **게임 폴더는 안 건드립니다.**

    자동으로 못 읽을 때 사람이 직접 열어 보라고 두는 길입니다. 기본은
    대본만 꺼냅니다 — 그림·소리까지 풀면 몇 기가가 되고, 우리가 쓸 일도
    없습니다.
    """
    arc, dest = Path(arc), Path(dest)
    index = rpa_index(arc)
    dest.mkdir(parents=True, exist_ok=True)
    out: list[str] = []
    for name in sorted(index):
        if scripts_only and not name.endswith((".rpy", ".rpyc")):
            continue
        rel = name.replace("\\", "/").lstrip("/")
        if ".." in rel.split("/"):          # 묶음이 바깥으로 나가려는 것
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_bytes(rpa_read(arc, index[name]))
        except Exception as exc:                        # noqa: BLE001
            progress(f"  · {rel} 을 못 꺼냈습니다: {type(exc).__name__}")
            continue
        out.append(rel)
    progress(f"{len(out)}개를 풀었습니다 → {dest}")
    return out


def extract_entries(game: Path, root: Path, *, threshold: float = 0.0,
                    pick: list[str] | None = None) -> list:
    """``[(원문, Location)]``.

    번역문을 사전으로 얹는 방식이라 원문을 하나 잘못 집어도 게임에 아무 일이
    없습니다. 그래서 여기서는 걸러내기를 아주 느슨하게 합니다 — 못 찾는 것보다
    쓸데없는 게 몇 개 섞이는 편이 낫습니다.
    """
    out: list[tuple[str, Location]] = []
    for label, kind, data in _sources(game, pick):
        if kind == "rpy":
            try:
                pairs = rpy_texts(data.decode("utf-8", errors="replace"))
            except Exception:             # noqa: BLE001
                continue
        else:
            pairs = [("say", t) for t in rpyc_texts(data)]

        for fmt, text in pairs:
            if not _worth(text):
                continue
            out.append((text, Location(file=f"game/{label}", kind="renpy",
                                       fmt=fmt, ptr="")))
    return out


_TAG_ONLY = re.compile(r"^\s*(\{[^}]*\}|\[[^\]]*\]|\s)*$")


def _worth(text: str) -> bool:
    s = (text or "").strip()
    if not (1 <= len(s) <= 4000):
        return False
    if _TAG_ONLY.match(s):                # {p}, [name] 만 있는 것
        return False
    if _JUNKY.match(s):                   # 파일 이름
        return False
    return any(c.isalpha() for c in s)


# ---------------------------------------------------------------- 얹기

PACK_JSON = "gameloc_translation.json"
HOOK_RPY = "zzz_gameloc.rpy"

HOOK_SOURCE = '''\
# Gameloc 이 만든 파일입니다. 원본 스크립트는 건드리지 않았습니다.
# 번역을 끄려면 이 파일과 {json} 을 지우면 됩니다.
init 1789 python:

    def _gameloc_load():
        import json
        try:
            raw = renpy.file("{json}").read()
        except Exception:
            return {{}}
        if not isinstance(raw, str):
            raw = raw.decode("utf-8")
        try:
            return json.loads(raw)
        except Exception:
            return {{}}

    _gameloc_map = _gameloc_load()
    _gameloc_prev_say = config.say_menu_text_filter
    _gameloc_prev_text = config.replace_text

    def _gameloc_say_filter(s):
        if _gameloc_prev_say is not None:
            s = _gameloc_prev_say(s)
        return _gameloc_map.get(s, s)

    def _gameloc_text_filter(s):
        if _gameloc_prev_text is not None:
            s = _gameloc_prev_text(s)
        got = _gameloc_map.get(s)
        if got is not None:
            return got
        stripped = s.strip()
        if stripped != s:
            got = _gameloc_map.get(stripped)
            if got is not None:
                return s.replace(stripped, got)
        return s

    config.say_menu_text_filter = _gameloc_say_filter
    config.replace_text = _gameloc_text_filter
'''


def hook_source() -> str:
    return HOOK_SOURCE.format(json=PACK_JSON)


def written_files(game: Path) -> list[Path]:
    return [game / PACK_JSON, game / HOOK_RPY]


def stale_compiled(game: Path) -> list[Path]:
    """전에 넣었던 후크의 컴파일본. 남아 있으면 옛날 번역이 되살아납니다."""
    return [p for p in (game / (HOOK_RPY + "c"),) if p.exists()]


# ---------------------------------------------------------------- 한글 글꼴

FONT_RPY = "zzz_gameloc_font.rpy"
FONT_BASE = "gameloc_korean"          # 확장자는 골라온 글꼴을 따라갑니다
FONT_SUFFIXES = (".ttf", ".otf", ".ttc")

# 스크립트 안에 적힌 글꼴 파일 이름. "gui/font/DejaVuSans.ttf" 같은 모양입니다.
_FONT_REF = re.compile(r"[\w][\w./\\ +-]{0,120}?\.(?:ttf|otf|ttc)",
                       re.IGNORECASE)

# Ren'Py 가 아무것도 안 정했을 때 쓰는 기본 글꼴. 한글이 없습니다.
BUILTIN_FONTS = ["DejaVuSans.ttf", "DejaVuSans-Bold.ttf"]


def font_files(game: Path) -> list[Path]:
    """``game/`` 안에 실제로 들어 있는 글꼴 파일."""
    out = [p for p in sorted(game.rglob("*"))
           if p.is_file() and p.suffix.lower() in FONT_SUFFIXES]
    return [p for p in out if p.stem != FONT_BASE]


def font_names(game: Path) -> list[str]:
    """이 게임이 부르는 글꼴 이름 전부.

    Ren'Py 는 글꼴을 ``game/`` 기준 경로 **문자열**로 부릅니다. 그래서
    바꿔치기하려면 그 문자열들을 알아야 합니다. 세 군데서 모읍니다 —
    실제로 놓여 있는 파일, ``.rpy`` 소스에 적힌 이름, 컴파일본 안의 문자열.
    """
    found: set[str] = set(BUILTIN_FONTS)

    for p in font_files(game):
        found.add(str(p.relative_to(game)).replace("\\", "/"))

    for _label, kind, data in _sources(game):
        if kind == "rpy":
            text = data.decode("utf-8", errors="replace")
            found.update(m.group(0) for m in _FONT_REF.finditer(text))
        else:
            for tok in _pickle_tokens(rpyc_payload(data) or b""):
                if tok and _FONT_REF.fullmatch(tok):
                    found.add(tok)

    # 슬래시 방향이 다른 표기도 같이 넣어 둡니다 — 어느 쪽으로 적혔든 잡히게.
    found = {n for n in found if FONT_BASE not in n}
    for name in list(found):
        if "\\" in name:
            found.add(name.replace("\\", "/"))
        elif "/" in name:
            found.add(name.replace("/", "\\"))
    return sorted(found)


FONT_HOOK_SOURCE = '''\
# Gameloc 이 만든 파일입니다. 원본 스크립트는 건드리지 않았습니다.
# 한글이 □□□ 로 나오는 것을 고칩니다. 되돌리려면 이 파일과
# {font} 를 지우면 됩니다.
init 1790 python:

    _gameloc_font = "{font}"
    _gameloc_from = {names}

    for _gl_name in _gameloc_from:
        for _gl_bold in (False, True):
            for _gl_italic in (False, True):
                config.font_replacement_map[(_gl_name, _gl_bold, _gl_italic)] = \\
                    (_gameloc_font, _gl_bold, _gl_italic)

    # 위 목록에서 빠진 글꼴이 있어도 기본 글꼴은 한글이 되게 해 둡니다.
    style.default.font = _gameloc_font
'''


def font_hook_source(font_file: str, names: list[str]) -> str:
    listing = "[\n" + "".join(
        f"        {name!r},\n" for name in names) + "    ]"
    return FONT_HOOK_SOURCE.format(font=font_file, names=listing)
