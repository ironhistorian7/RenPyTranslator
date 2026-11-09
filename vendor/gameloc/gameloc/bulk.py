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

"""번역문 일괄 바꾸기 — **원문을 근거로** 바꿉니다.

## 무엇을 푸는가

자동번역이 ``coffee`` 를 죄다 ``호빵`` 으로 옮겨놨다고 합시다. 번역문에서
``호빵`` 을 전부 ``커피`` 로 바꾸면 될 것 같지만, 그러면 원문이 정말로
``あんまん``(호빵)이었던 줄까지 커피가 됩니다. 한 번 그렇게 뭉개면 어느 줄이
원래 호빵이었는지 되찾을 방법이 없습니다.

그래서 조건을 하나 더 겁니다.

    원문에 'coffee' 가 있고,  그 줄의 번역문에 '호빵' 이 있을 때만  →  '커피'

이러면 진짜 호빵은 원문에 ``coffee`` 가 없으니 건드려지지 않습니다.

## 반드시 미리 보여주고 바꿉니다

``plan()`` 은 아무것도 고치지 않고 "이 줄들이 이렇게 바뀝니다" 만 돌려줍니다.
사람이 그 목록을 보고 나서 ``apply()`` 를 부릅니다. 그리고 ``apply()`` 는
바꾸기 전 값을 전부 들고 돌아오므로, 마음에 안 들면 ``undo()`` 로 한 번에
되돌릴 수 있습니다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Change:
    """한 줄이 어떻게 바뀌는지."""

    id: str
    source: str
    before: str
    after: str
    hits: int = 0


@dataclass
class Plan:
    changes: list[Change] = field(default_factory=list)
    scanned: int = 0
    note: str = ""

    def __len__(self) -> int:
        return len(self.changes)

    def summary(self) -> str:
        if not self.changes:
            return self.note or "바뀔 줄이 없습니다."
        hits = sum(c.hits for c in self.changes)
        return f"{len(self.changes)}줄에서 {hits}군데를 바꿉니다."


def _needle(text: str, *, whole_word: bool, case_sensitive: bool):
    flags = 0 if case_sensitive else re.IGNORECASE
    pat = re.escape(text)
    if whole_word:
        # 한국어에는 낱말 경계(\b)가 잘 안 맞습니다. 알파벳·숫자일 때만 겁니다.
        if re.fullmatch(r"[\w\s]+", text) and re.search(r"[A-Za-z0-9]", text):
            pat = r"\b" + pat + r"\b"
    return re.compile(pat, flags)



# ---------------------------------------------------------------- 조사 맞추기

# 호빵(받침 O) → 커피(받침 X) 로 바꾸면 "호빵이" 가 "커피이" 가 됩니다.
# 한국어에서만 생기는 문제고, 규칙이 딱 떨어져서 기계가 고칠 수 있습니다.
_PAIRS = [("이", "가"), ("은", "는"), ("을", "를"), ("과", "와"), ("아", "야")]
_CLOSERS = ' \t\n.,!?…"\'()[]{}~·:;/'


def _has_batchim(ch: str) -> bool:
    return "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28 != 0


def _jong(ch: str) -> int:
    return (ord(ch) - 0xAC00) % 28 if "가" <= ch <= "힣" else -1


def fix_josa(word: str, tail: str) -> str:
    """새로 넣은 ``word`` 뒤에 붙은 조사를 받침에 맞게 고칩니다.

    조사가 아닐 수도 있는 자리는 건드리지 않습니다 — 조사 바로 뒤에 글자가
    이어지면(``커피이야기``) 그냥 둡니다.
    """
    if not word or not tail:
        return tail
    last = word[-1]
    if not ("가" <= last <= "힣"):
        return tail
    batchim = _has_batchim(last)

    # 으로 / 로 — ㄹ 받침은 '로' 를 씁니다 (서울로, 커피로, 집으로)
    for a, b, n in (("으로", "로", 2), ("로", "으로", 1)):
        if tail.startswith(a) and (len(tail) <= n or tail[n] in _CLOSERS):
            want_ro = (not batchim) or _jong(last) == 8
            if a == "으로" and want_ro:
                return "로" + tail[2:]
            if a == "로" and not want_ro:
                return "으로" + tail[1:]
            return tail

    if len(tail) > 1 and tail[1] not in _CLOSERS:
        return tail                       # 조사가 아니라 낱말의 일부
    for withb, without in _PAIRS:
        if tail[0] == withb and not batchim:
            return without + tail[1:]
        if tail[0] == without and batchim:
            return withb + tail[1:]
    return tail


def _sub(pattern, replace: str, text: str, *, josa: bool) -> tuple[str, int]:
    """``pattern`` 을 갈아끼우고, 필요하면 바로 뒤 조사를 손봅니다."""
    out, pos, n = [], 0, 0
    for m in pattern.finditer(text):
        out.append(text[pos:m.start()])
        out.append(replace)
        pos = m.end()
        n += 1
        if josa:
            fixed = fix_josa(replace, text[pos:pos + 2])
            if fixed != text[pos:pos + 2]:
                out.append(fixed)
                pos += len(text[pos:pos + 2])
    out.append(text[pos:])
    return "".join(out), n


def plan(entries, lang: str, *, source_has: str = "", find: str,
         replace: str, whole_word: bool = False,
         case_sensitive: bool = False, josa: bool = True) -> Plan:
    """무엇이 바뀔지만 계산합니다. **아무것도 고치지 않습니다.**

    ``source_has`` 를 비워두면 원문 조건 없이 번역문만 보고 바꿉니다. 편하지만
    위험한 쪽이라, 화면에서는 비워둔 채로 쓰지 말라고 알려줍니다.
    """
    p = Plan()
    if not find:
        p.note = "바꿀 말을 적어주세요."
        return p

    src_pat = _needle(source_has, whole_word=whole_word,
                      case_sensitive=case_sensitive) if source_has else None
    find_pat = _needle(find, whole_word=whole_word,
                       case_sensitive=case_sensitive)

    for e in entries:
        cur = e.translations.get(lang, "")
        if not cur:
            continue
        p.scanned += 1
        if src_pat is not None and not src_pat.search(e.text):
            continue
        new, n = _sub(find_pat, replace, cur, josa=josa)
        if n and new != cur:
            p.changes.append(Change(id=e.id, source=e.text, before=cur,
                                    after=new, hits=n))

    if not p.changes:
        p.note = ("조건에 맞는 줄이 없습니다. 원문과 번역문의 말이 "
                  "정확히 그렇게 적혀 있는지 확인해 보세요.")
    elif src_pat is None:
        p.note = ("원문 조건 없이 번역문만 보고 바꿉니다. 원래부터 그 말이었던 "
                  "줄까지 바뀔 수 있으니 아래 목록을 꼭 확인하세요.")
    return p


def apply(entries, lang: str, changes: list[Change], *,
          only: set[str] | None = None) -> dict[str, str]:
    """계획을 실제로 적용하고 **바꾸기 전 값**을 돌려줍니다 (되돌리기용)."""
    wanted = {c.id: c for c in changes if only is None or c.id in only}
    undo: dict[str, str] = {}
    for e in entries:
        c = wanted.get(e.id)
        if c is None:
            continue
        cur = e.translations.get(lang, "")
        if cur != c.before:       # 그 사이에 사람이 고쳤다면 건드리지 않습니다
            continue
        undo[e.id] = cur
        e.translations[lang] = c.after
        if lang in e.machine:     # 사람이 손본 것으로 칩니다
            e.machine.remove(lang)
    return undo


def undo(entries, lang: str, snapshot: dict[str, str]) -> int:
    """``apply()`` 가 돌려준 값으로 되돌립니다."""
    n = 0
    for e in entries:
        if e.id in snapshot:
            e.translations[lang] = snapshot[e.id]
            n += 1
    return n
