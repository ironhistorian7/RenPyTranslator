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

"""TyranoScript(ティラノスクリプト) 게임에서 대사를 뽑고 되돌려 넣습니다.

## 생김새

내용물이 전부 평문입니다. 대사는 ``data/scenario/*.ks`` 에 이렇게 들어 있습니다:

    ;── 주석
    *start                                  ← 라벨
    [bg storage="room.jpg" time=1000]       ← 태그
    #ユリ                                    ← 말하는 사람
    おはよう。[l][r]今日はいい天気だね。[p]      ← 대사 (태그가 중간에 섞임)
    [glink text="はい" target="*yes"]         ← 선택지
    @jump storage="next.ks"                 ← 줄 전체가 태그인 형태

## 왜 전용 추출기가 필요한가

엔진을 모를 때 쓰는 '평범한 텍스트 파일 읽기' 로는 두 가지가 어긋납니다.

1. ``.ks`` 를 안 읽습니다. 대신 ``data/others/plugin/*/readme.txt`` 같은
   **개발자용 설명서**를 읽어서 그걸 번역해 버립니다. 화면에는 안 나오는데
   목록만 잔뜩 채웁니다.
2. 읽는다 해도 한 줄을 통째로 번역하면 ``[l][r]`` 같은 태그가 번역기에
   씹혀서 게임이 대사를 못 그립니다.

그래서 줄을 **글자 조각과 태그 조각으로 쪼개서**, 글자만 번역하고 태그는
있던 자리에 그대로 둡니다.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..model import Location

# ---------------------------------------------------------------- 판별

SCENARIO_DIR = "scenario"
# 개발자용 문서·플러그인 소스가 들어 있는 곳. 화면에 안 나옵니다.
SKIP_PARTS = {"others", "system", "__gameloc_backup__"}


def is_tyrano(root: Path) -> Path | None:
    """TyranoScript 게임이면 ``data`` 를 담은 폴더를, 아니면 ``None``."""
    root = Path(root)
    if not root.is_dir():
        return None
    bases = [root] + [p for p in sorted(root.iterdir()) if p.is_dir()]
    for base in bases:
        data = base / "data"
        if not data.is_dir():
            continue
        engine = (base / "tyrano").is_dir() or (base / "index.html").is_file()
        content = (data / SCENARIO_DIR).is_dir() or any(data.glob("*.ks"))
        if engine and content:
            return base
    return None


def version(base: Path) -> str:
    """``tyrano/tyrano.js`` 머리에 적힌 버전 (없으면 빈 문자열)."""
    for cand in (base / "tyrano" / "tyrano.js", base / "tyrano" / "libs.js"):
        try:
            head = cand.read_text("utf-8", errors="ignore")[:4000]
        except OSError:
            continue
        m = re.search(r"[Vv]ersion\s*[:=]\s*[\"']?v?(\d+\.\d+[\w.]*)", head)
        if m:
            return m.group(1)
    return ""


def scenario_files(base: Path, *, include_system: bool = False) -> list[Path]:
    """번역 대상이 되는 ``.ks`` 들. 플러그인·시스템 폴더는 기본으로 뺍니다.

    ``system`` 에는 보통 개발자용 문서와 플러그인 소스가 들어 있어 화면에
    안 나옵니다. **그런데 게임에 따라 진짜 대사를 거기 두기도 합니다.**
    RPG Maker 에서 똑같은 일을 겪었습니다 — 플러그인 js 안에 줄거리 297줄이
    박혀 있었는데 우리가 안 읽고 있었습니다. 그래서 켤 수 있게 둡니다.
    """
    data = base / "data"
    out = []
    skip = SKIP_PARTS - {"system"} if include_system else SKIP_PARTS
    for p in sorted(data.rglob("*.ks")):
        rel = p.relative_to(data).parts
        if any(part in skip for part in rel[:-1]):
            continue
        out.append(p)
    return out


# ---------------------------------------------------------------- 줄 해석

# 태그 안에 따옴표로 감싼 ``]`` 가 들어갈 수 있습니다:
#     [glink text="はい[ハート]" target="*yes"]
# 단순히 첫 ``]`` 에서 끊으면 뒤쪽 ``" target="*yes"]`` 이 대사로 잘못 잡히고,
# 거기에 번역이 들어가면 태그가 통째로 망가집니다.
TAG = re.compile(r"""\[(?:[^\]"'\n]|"[^"\n]*"|'[^'\n]*')*\]""")
ATTR = re.compile(r"""([A-Za-z_][\w-]*)\s*=\s*("([^"]*)"|'([^']*)'|([^\s\]]+))""")

# 언제나 화면 문구인 속성
DISPLAY_ATTRS = {"text", "title", "body", "caption", "alt", "message"}
# ``chara_new``/``chara_mod``/``chara_face`` 의 ``name=`` 은 화면 문구가
# 아니라 **캐릭터를 가리키는 내부 식별자**입니다 (예: ``name="izumi"``).
# chara_new 에서 등록한 식별자를 뒤의 chara_mod/chara_face 가 그대로 찾아
# 씁니다 — 번역하면 (같은 글자를 매번 똑같이 옮기더라도) 등록/조회 시점의
# 문자열이 서로 달라질 위험이 있고, 실제로 이 값이 번역되어 대사창이
# 멈추는 신고가 있었습니다. 그래서 ``name=`` 은 이 태그들에서 절대
# 건드리지 않습니다. 화면 표시용 이름은 공식 문서에 나온 대로 ``jname=``
# 이 따로 있어 그쪽만 번역합니다.
CHARA_TAGS = {"chara_new", "chara_mod", "chara_face"}
# ``[title name="..."]`` 은 캐릭터 식별자와 무관한, 창 제목을 정하는
# 시스템 태그라 ``name=`` 이 곧 화면(창 제목) 문구입니다.
TITLE_TAGS = {"title"}


def tag_name(tag_body: str) -> str:
    """``[chara_new ...]`` 도 ``@chara_new ...`` 도 ``chara_new`` 로."""
    m = re.match(r"[\s\[@]*([A-Za-z_][\w-]*)", tag_body)
    return m.group(1).lower() if m else ""


def is_display_attr(tag: str, attr: str) -> bool:
    a = attr.lower()
    if a in DISPLAY_ATTRS:
        return True
    t = tag_name(tag)
    if a == "jname" and t in CHARA_TAGS:
        return True
    if a == "name" and t in TITLE_TAGS:
        return True
    return False


# ``.ks`` 안에는 대사만 있는 게 아닙니다. 자바스크립트와 HTML 을 그대로
# 끼워 넣을 수 있고, 타이틀 화면 메뉴는 대개 이 안에 들어 있습니다.
#
#     [iscript]
#     tf.menu = ["開始", "再開"];        ← 코드입니다. 번역하면 메뉴가 사라집니다
#     [endscript]
#
#     [html]<img src="logo.png" alt="ロゴ">[endhtml]   ← 태그 속성을 건드리면 그림이 깨집니다
#
# 그래서 이 블록 안은 통째로 건너뜁니다.
BLOCK_OPEN = re.compile(r"\[\s*(iscript|html|script|style|jscript)\b", re.I)
BLOCK_CLOSE = re.compile(r"\[\s*(endscript|endhtml|endstyle|/script|/html|/style)\b",
                         re.I)


def code_lines(body: str) -> set[int]:
    """``[iscript]`` · ``[html]`` 같은 블록 안에 있는 줄 번호."""
    out: set[int] = set()
    depth = 0
    for ln, line in enumerate(body.split("\n")):
        opened = bool(BLOCK_OPEN.search(line))
        closed = bool(BLOCK_CLOSE.search(line))
        if depth or opened:
            out.add(ln)
        if opened and not closed:
            depth += 1
        elif closed and depth:
            depth -= 1
    return out


def parse_line(line: str) -> list[tuple[str, str]]:
    """한 줄을 조각으로. 종류는 comment / label / name / tag / text."""
    stripped = line.strip()
    if not stripped:
        return []
    if stripped.startswith(";"):
        return [("comment", line)]
    if stripped.startswith("*"):
        return [("label", line)]
    if stripped.startswith("@"):
        return [("tag", line)]
    if stripped.startswith("#"):
        return [("name", line)]

    out: list[tuple[str, str]] = []
    pos = 0
    for m in TAG.finditer(line):
        if m.start() > pos:
            out.append(("text", line[pos:m.start()]))
        out.append(("tag", m.group(0)))
        pos = m.end()
    if pos < len(line):
        out.append(("text", line[pos:]))
    return out


_TAG_ONLY = re.compile(r"^[\s　]*$")


def worth(text: str) -> bool:
    s = (text or "").strip()
    if not s or _TAG_ONLY.match(s):
        return False
    return any(c.isalnum() for c in s)


def iter_texts(body: str):
    """``(ptr, 종류, 원문)`` 을 파일 순서대로.

    ptr 은 ``줄번호.조각번호`` 또는 ``줄번호.조각번호@속성`` 입니다.
    """
    skip = code_lines(body)
    for ln, line in enumerate(body.split("\n")):
        if ln in skip:
            continue
        for si, (kind, chunk) in enumerate(parse_line(line)):
            if kind == "text":
                if worth(chunk):
                    yield f"{ln}.{si}", "대사", chunk.strip()
            elif kind == "name":
                who = chunk.strip()[1:].strip()
                if worth(who):
                    yield f"{ln}.{si}", "이름", who
            elif kind == "tag":
                for m in ATTR.finditer(chunk):
                    attr = m.group(1)
                    val = m.group(3) or m.group(4) or m.group(5) or ""
                    if is_display_attr(chunk, attr) and worth(val):
                        yield f"{ln}.{si}@{attr}", "항목", val


def patch(body: str, edits: dict[str, str]) -> str:
    """``iter_texts`` 가 준 ptr 자리에 번역을 넣고 나머지는 그대로 둡니다."""
    lines = body.split("\n")
    by_line: dict[int, dict[str, str]] = {}
    for ptr, new in edits.items():
        head, _, attr = ptr.partition("@")
        ln_s, _, si_s = head.partition(".")
        try:
            ln, si = int(ln_s), int(si_s)
        except ValueError:
            continue
        by_line.setdefault(ln, {})[f"{si}@{attr}" if attr else str(si)] = new

    skip = code_lines(body)
    for ln, per_seg in by_line.items():
        if not 0 <= ln < len(lines) or ln in skip:
            continue
        original = lines[ln]
        parts = parse_line(original)
        if not parts:
            continue
        rebuilt = []
        for si, (kind, chunk) in enumerate(parts):
            new = per_seg.get(str(si))
            if new is not None and kind in ("text", "name"):
                new = escape_text(new)
            if kind == "text" and new is not None:
                # 앞뒤 공백은 원문 그대로 유지합니다 (대사 사이 간격이 바뀌면
                # 화면에서 티가 납니다).
                lead = chunk[:len(chunk) - len(chunk.lstrip())]
                tail = chunk[len(chunk.rstrip()):]
                chunk = lead + new + tail
            elif kind == "name" and new is not None:
                lead = chunk[:len(chunk) - len(chunk.lstrip())]
                chunk = lead + "#" + new
            elif kind == "tag":
                chunk = _patch_tag(chunk, per_seg, si)
            rebuilt.append(chunk)
        new_line = "".join(rebuilt)
        if _same_shape(original, new_line):
            lines[ln] = new_line
    return "\n".join(lines)


def escape_text(s: str) -> str:
    """번역문 속 ``[`` 는 태그 시작으로 읽힙니다. TyranoScript 식으로 막습니다.

    ``[[`` 가 화면에서 ``[`` 한 글자로 나옵니다. 이걸 안 하면 번역문 안의
    대괄호가 없는 태그로 해석돼 그 줄부터 화면이 어긋납니다.
    """
    return (s or "").replace("[", "[[")


def _same_shape(before: str, after: str) -> bool:
    """줄의 뼈대가 그대로인가.

    번역문에 ``[`` 나 ``]`` 가 섞여 들어오면 태그가 하나 늘거나 줄어들 수
    있습니다. 그러면 그 줄부터 게임이 이상해집니다. 뼈대가 달라졌으면
    **그 줄은 아예 안 바꾸고** 원문을 그대로 둡니다 — 한 줄 번역이 빠지는
    편이 화면이 깨지는 것보다 낫습니다.
    """
    def shape(s: str) -> list[str]:
        s = s.replace("[[", "")          # 글자로 보여줄 대괄호는 태그가 아닙니다
        return [tag_name(m.group(0)) for m in TAG.finditer(s)]

    return shape(before) == shape(after) and before.count("\t") == after.count("\t")


def _patch_tag(chunk: str, per_seg: dict[str, str], si: int) -> str:
    wanted = {k.split("@", 1)[1]: v for k, v in per_seg.items()
              if k.startswith(f"{si}@")}
    if not wanted:
        return chunk

    def repl(m):
        attr = m.group(1)
        if attr not in wanted:
            return m.group(0)
        new = wanted[attr].replace('"', "&quot;")
        return f'{attr}="{new}"'

    return ATTR.sub(repl, chunk)


# ---------------------------------------------------------------- 뽑기

def extract_entries(base: Path, root: Path, *,
                    include_system: bool = False) -> list:
    out: list[tuple[str, Location]] = []
    for path in scenario_files(base, include_system=include_system):
        try:
            body = path.read_text("utf-8", errors="replace")
        except OSError:
            continue
        rel = str(path.relative_to(root))
        for ptr, kind, text in iter_texts(body):
            out.append((text, Location(file=rel, kind="tyrano", fmt=kind,
                                       ptr=ptr)))
    return out
