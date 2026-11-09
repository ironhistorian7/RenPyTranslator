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

"""'이 문장은 왜 번역이 안 됐지?' 에 답하는 진단기.

**그냥 문자열 검색으로는 못 찾습니다.** 화면에서 한 덩어리로 보이는 대사가
파일에서는 이렇게 생겼기 때문입니다:

    "仕事を手伝いに行かないと。"
    "\\C[18]厨房\\C[0]で彼女が待ってるはずだ。"

줄마다 명령이 따로고, 색 지정 같은 제어문자가 문장 한가운데 박혀 있습니다.
그래서 양쪽에서 제어문자와 공백을 걷어낸 뒤 대조하고, 파일 안의 문자열을
문서 순서대로 이어붙인 것과도 대조해 '여러 줄에 쪼개진' 경우까지 잡습니다.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from . import protect
from .detect import detect

TEXTY = {".json", ".js", ".txt", ".csv", ".tsv", ".xml", ".html", ".css",
         ".yaml", ".yml", ".ini", ".rpy"}
BINARY_HINT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".ogg", ".m4a",
               ".wav", ".mp3", ".mp4", ".ttf", ".otf", ".woff", ".woff2",
               ".efkefc", ".rpgmvp", ".rpgmvo", ".rpgmvm", ".dat", ".pak"}
MAX_BYTES = 32 * 1024 * 1024
MIN_QUERY = 2

_STRIP = re.compile(r"[\s　]+")


def normalize(s: str) -> str:
    """대조용으로 문장을 '화면에 보이는 글자' 만 남깁니다."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = protect.CODE_RE.sub("", s)      # \C[3] \V[1] %1 <tag> …
    return _STRIP.sub("", s)            # 줄바꿈·공백 전부


@dataclass
class Hit:
    file: str
    kind: str
    covered: bool
    reason: str
    where: str = ""
    raw: str = ""
    how: str = ""


@dataclass
class FindReport:
    query: str
    hits: list[Hit] = field(default_factory=list)
    scanned: int = 0
    in_project: bool = False
    project_text: str = ""
    translated: bool = False
    skipped_binary: int = 0
    engine: str = ""

    def verdict(self) -> str:
        if self.in_project:
            return ("이 문장은 이미 목록에 있습니다. "
                    + ("번역도 채워져 있습니다 — 게임에 [적용]을 안 하셨을 수 있습니다."
                       if self.translated else "번역만 아직 안 채운 상태입니다."))
        if not self.hits:
            return ("텍스트 파일에서 찾지 못했습니다. 이미지에 그려진 글자이거나, "
                    "암호화된 파일 안에 있을 수 있습니다. 문장 일부만 넣어 다시 찾아보세요.")
        if any(h.covered for h in self.hits):
            return "파일에는 있습니다. 아래 위치와 이유를 보세요."
        return "gameloc 이 아직 다루지 않는 위치에 있습니다."


def _classify(rel: str, engine: str) -> tuple[str, bool, str]:
    r = rel.replace("\\", "/").lower()
    if engine == "rpgmaker":
        if "data/" in r:
            if r.endswith("mapinfos.json"):
                return ("맵 목록", False, "화면에 안 나오는 내부 목록이라 건너뜁니다.")
            return ("게임 데이터", True,
                    "기본으로 가져오는 위치입니다. 목록에 없다면 화이트리스트 밖 필드"
                    "(메모칸·주석·스크립트)이거나, 게임이 이름으로 참조하는 문자열일 수 있습니다.")
        if r.endswith("js/plugins.js"):
            return ("플러그인 설정", True,
                    "화면 문구가 분명한 키(text·message·label 류)만 가져옵니다.")
        if "js/plugins/" in r:
            return ("플러그인 소스", True,
                    "'플러그인 js 파일까지' 옵션을 켜고 다시 뽑으면 잡힙니다.")
        if re.search(r"js/(rmmz|rpg)_", r):
            return ("엔진 코어", False,
                    "RPG Maker 엔진 자체에 박힌 문구입니다(가나 자판, 決定 버튼 등). "
                    "코어를 고치는 일이라 건드리지 않습니다.")
        if r.endswith(".css") or "/css/" in r:
            return ("스타일", False, "화면 문구가 아니라 서식입니다.")
    if engine == "renpy":
        if r.endswith(".rpy") and "/tl/" not in r:
            return ("Ren'Py 스크립트", True,
                    "기본으로 가져오는 위치입니다. 대사·선택지·_() 문구만 뽑으므로 "
                    "화면(screen) 안에 직접 박아 넣은 글자는 빠질 수 있습니다.")
        if "/tl/" in r:
            return ("기존 번역", False,
                    "게임에 이미 들어 있던 번역 파일입니다. 건드리지 않습니다.")
        if r.endswith(".rpyc"):
            return ("컴파일본", True, "소스가 없을 때만 여기서 대사를 꺼냅니다.")
    if r.endswith(".html"):
        return ("HTML", False, "게임 껍데기 파일입니다.")
    return ("기타 텍스트", False, "gameloc 이 다루지 않는 파일입니다.")


def _json_strings(node, path: str = "", out: list | None = None) -> list[tuple[str, str]]:
    """모든 문자열을 **문서 순서대로**. 쪼개진 대사를 이어붙이려면 순서가 중요합니다."""
    out = [] if out is None else out
    if isinstance(node, str):
        out.append((path, node))
    elif isinstance(node, dict):
        for k, v in node.items():
            _json_strings(v, f"{path}.{k}" if path else str(k), out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _json_strings(v, f"{path}[{i}]", out)
    return out


def _search_pairs(pairs: list[tuple[str, str]], needle: str):
    """(경로, 원문, 일치방식). 낱개에서 먼저 찾고, 없으면 이어붙여 찾습니다."""
    for path, value in pairs:
        n = normalize(value)
        if n and needle in n:
            how = "정확히 일치" if needle == n else "일부 일치"
            if protect.CODE_RE.search(value):
                how += " (제어문자 무시)"
            return path, value, how

    joined, marks = "", []
    for path, value in pairs:
        marks.append((len(joined), path, value))
        joined += normalize(value)
    idx = joined.find(needle)
    if idx >= 0:
        touched = [(p, v) for start, p, v in marks
                   if start < idx + len(needle) and start + len(normalize(v)) > idx]
        if touched:
            paths = ", ".join(p for p, _ in touched[:4])
            raw = " ⏎ ".join(v for _, v in touched[:4])
            return paths, raw, f"{len(touched)}줄에 나뉘어 있음"
    return None


# 글자가 들어 있을 만한 이진 파일. 그림·소리·글꼴은 아무리 뒤져도 없습니다.
NEVER_TEXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tga",
              ".ogg", ".m4a", ".wav", ".mp3", ".mp4", ".webm", ".ttf",
              ".otf", ".woff", ".woff2", ".efkefc",
              ".rpgmvp", ".rpgmvo", ".rpgmvm"}
MAX_SCAN_BYTES = 256 * 1024 * 1024


def _worth_bytes(path: Path, suffix: str) -> bool:
    if suffix in NEVER_TEXT:
        return False
    try:
        size = path.stat().st_size
    except OSError:
        return False
    return 64 <= size <= MAX_SCAN_BYTES


def _find_in_bytes(path: Path, root: Path, needle: str, engine: str):
    """길이가 앞에 붙은 문자열 덩어리를 훑어 찾습니다.

    유니티 파일은 ``길이(4바이트) + UTF-8 + 채움`` 으로 글자를 담습니다.
    이 모양을 아는 훑기가 이미 :mod:`gameloc.unityraw` 에 있습니다.
    그래도 못 찾으면 파일 전체에서 그 글자의 바이트를 그냥 찾아봅니다 —
    다른 게임 엔진은 담는 방식이 다르기 때문입니다.
    """
    from . import unityraw

    try:
        data = path.read_bytes()
    except (OSError, MemoryError):
        return None

    where = raw = ""
    how = ""
    try:
        for off, text in unityraw.scan(data, limit=10 ** 9):
            if needle in normalize(text):
                where, raw, how = f"{off}바이트째", text, "이진 파일 안 문자열"
                break
    except Exception:                                   # noqa: BLE001
        pass

    if not raw:
        try:
            at = data.find(needle.encode("utf-8"))
        except UnicodeEncodeError:
            at = -1
        if at < 0:
            return None
        start = max(0, at - 40)
        chunk = data[start:at + 120].decode("utf-8", "ignore")
        where, raw, how = f"{at}바이트째", chunk, "이진 파일 안 (모양 미상)"

    rel = str(path.relative_to(root))
    kind, covered, reason = _classify_binary(rel, engine, how)
    return Hit(file=rel, kind=kind, covered=covered, reason=reason,
               where=where, raw=raw[:200], how=how)


def _classify_binary(rel: str, engine: str, how: str) -> tuple[str, bool, str]:
    r = rel.replace("\\", "/").lower()
    if engine == "unity":
        if "길이" in how or "문자열" in how:
            return ("유니티 파일 안", True,
                    "gameloc 이 읽는 방식으로 들어 있습니다. 목록에 없다면 "
                    "'씬 파일 건너뛰기' 가 켜져 있거나, 이름으로 보여 걸러졌을 "
                    "수 있습니다.")
        return ("유니티 파일 안 (모양 미상)", False,
                "글자는 있는데 gameloc 이 아는 담는 방식이 아닙니다. "
                "게임이 스스로 만든 형식일 수 있습니다.")
    if "/data/" in r:
        return ("이진 데이터 파일", False,
                "이 형식은 아직 못 읽습니다.")
    return ("이진 파일 안", False,
            "글자는 이 파일에 있습니다. gameloc 이 아직 이 형식을 안 읽습니다.")


def find(root: Path, query: str, project=None, *, limit: int = 30) -> FindReport:
    root = Path(root).expanduser().resolve()
    rep = FindReport(query=(query or "").strip())
    needle = normalize(rep.query)
    if len(needle) < MIN_QUERY:
        return rep

    info = detect(root)
    rep.engine = info.engine

    if project is not None:
        for e in project.entries:
            if needle in normalize(e.text):
                rep.in_project = True
                rep.project_text = e.text
                rep.translated = any(v for v in e.translations.values())
                break

    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__gameloc_backup__" in path.parts:
            continue
        suffix = path.suffix.lower()
        if suffix not in TEXTY:
            # 유니티는 **글자를 이진 파일 안에** 넣습니다. 여기서
            # 건너뛰면 사고가 가장 많은 곳에서 진단이 아예 안 돕니다.
            # 부품(unityraw 의 바이트 훑기)은 이미 있으니 붙입니다.
            if _worth_bytes(path, suffix):
                hit = _find_in_bytes(path, root, needle, rep.engine)
                if hit is not None:
                    rep.scanned += 1
                    rep.hits.append(hit)
                    if len(rep.hits) >= limit:
                        break
                    continue
                rep.scanned += 1
            elif suffix in BINARY_HINT:
                rep.skipped_binary += 1
            continue
        try:
            if path.stat().st_size > MAX_BYTES:
                continue
            text = path.read_text("utf-8", errors="ignore")
        except OSError:
            continue
        rep.scanned += 1

        found = None
        if suffix == ".json":
            try:
                found = _search_pairs(_json_strings(json.loads(text)), needle)
            except Exception:
                found = None
        if found is None:
            lines = [(f"{i + 1}행", ln)
                     for i, ln in enumerate(text.split("\n")) if ln.strip()]
            found = _search_pairs(lines, needle)
        if found is None:
            continue

        where, raw, how = found
        rel = str(path.relative_to(root))
        kind, covered, reason = _classify(rel, info.engine)
        rep.hits.append(Hit(file=rel, kind=kind, covered=covered, reason=reason,
                            where=where, raw=raw[:300], how=how))
        if len(rep.hits) >= limit:
            break
    return rep


# ---------------------------------------------------------------- 다시 뽑기

# 어떤 자리에서 찾았으면 **무슨 옵션을 켜고 다시 뽑아야** 하는가.
# 진단이 "여기 있다" 로 끝나 버리면 사용자는 그다음에 뭘 해야 할지
# 모릅니다. 여기까지 이어 줘야 진단이 쓸모가 있습니다 (백로그 4).
RETRY = {
    "플러그인 소스": ("js", "플러그인 js 파일 안까지 읽기"),
    "플러그인 설정": ("allParams", "플러그인 설정값을 가리지 않고 전부 읽기"),
    "게임 데이터": ("notes", "메모칸·주석까지 읽기"),
    "유니티 파일 안": ("scenes", "씬 파일까지 읽기"),
    "유니티 파일 안 (모양 미상)": ("scenes", "씬 파일까지 읽기"),
}


def suggest(rep: "FindReport") -> list[dict]:
    """진단 결과에서 **다음에 누를 것**을 뽑아냅니다.

    돌려주는 것: ``[{"option": "js", "label": "…", "where": "파일이름"}]``
    화면은 이것으로 [그럼 거기도 읽고 다시 뽑기] 버튼을 만듭니다.
    """
    if rep.in_project:
        return []
    out: dict[str, dict] = {}
    for hit in rep.hits:
        got = RETRY.get(hit.kind)
        if got is None:
            continue
        opt, label = got
        # 이미 잘 다루는 자리인데 목록에 없다면, 그건 옵션 문제입니다.
        out.setdefault(opt, {"option": opt, "label": label, "where": []})
        out[opt]["where"].append(_short_name(hit.file))
    for v in out.values():
        v["where"] = ", ".join(sorted(set(v["where"]))[:4])
    return list(out.values())


def _short_name(rel: str) -> str:
    return rel.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or rel
