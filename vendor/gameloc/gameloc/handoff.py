# SPDX-License-Identifier: GPL-3.0-or-later
"""반자동 번역 — **통으로 뽑아 붙여넣고, 통으로 되받기.**

## 왜

번역기 창에 손으로 붙여넣는 사람이 많습니다. 한도도 차단도 없고, 결과를
눈으로 보면서 할 수 있으니까요. 그런데 문장이 15,000개면 하나씩은 못 합니다.

그래서 **여러 개를 한 덩이로 묶어 주고, 번역된 덩이를 받아 제자리에
돌려놓습니다.** 자동도 아니고 수동도 아니라 반자동입니다.

## 번호를 붙이는 까닭 — 이게 이 기능의 전부입니다

    1. apple
    2. lemon
    3. orange

``apple/lemon/orange`` 처럼 구분자로 잇는 쪽이 짧고 편합니다. 그런데
**세 가지가 실제로 깨뜨립니다.**

  · 원문에 구분자가 들어 있습니다 — 일본 게임에 ``A/B`` 표기가 흔합니다
  · 번역기가 구분자를 바꿉니다 — 구글은 ``/`` 를 전각 ``／`` 로 바꿉니다
  · **하나가 어긋나면 그 뒤가 전부 밀립니다.** 500개 중 3번이 사라지면
    4번부터 497개가 한 칸씩 밀려 들어갑니다. 오류는 안 나고 조용히 다
    틀립니다 — 나중에 게임을 켜 봐야 압니다

번호는 이걸 다 막습니다. 줄이 합쳐지든 순서가 바뀌든 **번호를 보고 제자리를
찾고**, 못 찾은 것은 못 찾았다고 말할 수 있습니다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 구글 번역기 칸이 5,000자쯤에서 잘립니다. 붙여넣었는데 뒤가 조용히
# 사라지는 것이 제일 나쁘므로 넉넉히 잡습니다.
CHARS = 4000

# 한 덩이가 너무 크면 번역기가 문단을 통째로 재구성해 버립니다.
MOST = 200

# 번호 뒤에 오는 기호. **전각까지 받습니다** — 구글이 ``.`` 를 ``．`` 로,
# ``)`` 를 ``）`` 로 바꿔 돌려줍니다. 여기서 막히면 사람은 "왜 하나도 안
# 들어가지" 만 보게 됩니다.
_AFTER = ".)]:：。、．）］・-–—"
_NUM = re.compile(r"^[\s\u3000]*(\d+)[\s\u3000]*[" + re.escape(_AFTER)
                  + r"]?[\s\u3000]*(.*)$")


@dataclass
class Chunk:
    """붙여넣기 한 번 분량."""

    first: int                      # 이 덩이의 첫 번호 (1부터)
    lines: list[str] = field(default_factory=list)

    @property
    def last(self) -> int:
        return self.first + len(self.lines) - 1

    def text(self) -> str:
        return "\n".join(f"{self.first + i}. {t}"
                         for i, t in enumerate(self.lines))

    def label(self) -> str:
        return f"{self.first}~{self.last}번 ({len(self.lines)}개)"


def split(texts: list[str], *, chars: int = CHARS, most: int = MOST) -> list[Chunk]:
    """글자 수로 끊습니다. **개수로 끊으면 긴 대사에서 넘칩니다.**

    번호는 덩이가 바뀌어도 **이어집니다** — 1~200, 201~380 … 이렇게요.
    덩이마다 1번부터 다시 세면, 사용자가 순서를 섞어 붙여넣었을 때
    어느 덩이 것인지 알 길이 없습니다.
    """
    out: list[Chunk] = []
    cur = Chunk(first=1)
    size = 0
    for i, t in enumerate(texts, 1):
        one = len(t) + 6                    # 번호와 줄바꿈 몫
        if cur.lines and (size + one > chars or len(cur.lines) >= most):
            out.append(cur)
            cur = Chunk(first=i)
            size = 0
        cur.lines.append(t)
        size += one
    if cur.lines:
        out.append(cur)
    return out


@dataclass
class Pair:
    """돌아온 것 하나. 화면에 그대로 보여 줍니다."""

    n: int
    before: str
    after: str = ""
    why: str = ""                   # 비어 있으면 괜찮다는 뜻

    @property
    def ok(self) -> bool:
        return bool(self.after) and not self.why


@dataclass
class Landing:
    """되읽은 결과. **넣기 전에** 사람에게 보여 줍니다."""

    pairs: list[Pair] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)      # 번호가 남는 것
    numbered: bool = True

    @property
    def good(self) -> list[Pair]:
        return [p for p in self.pairs if p.ok]

    @property
    def bad(self) -> list[Pair]:
        return [p for p in self.pairs if not p.ok]

    def report(self) -> list[str]:
        out = [f"{len(self.good)}개를 제자리에 놓을 수 있습니다."]
        if not self.numbered:
            out.append("⚠  붙여넣은 글에 번호가 없습니다. 줄 순서만 보고 "
                       "맞췄으니 **꼭 확인하세요** — 한 줄만 어긋나도 "
                       "그 뒤가 전부 밀립니다.")
        if self.bad:
            out.append(f"⚠  {len(self.bad)}개는 짝을 못 찾았습니다. "
                       "그 줄만 원문으로 남습니다.")
        if self.extra:
            out.append(f"ℹ  {len(self.extra)}줄은 어느 것에도 안 붙었습니다 "
                       "— 번역기가 줄을 더 만든 것으로 보입니다.")
        return out


def land(chunk: Chunk, pasted: str) -> Landing:
    """번역기에서 받아온 글을 원래 자리에 맞춥니다.

    번호가 있으면 번호로 맞춥니다. 하나도 없으면 줄 순서로 맞추되
    **그 사실을 분명히 알립니다** — 조용히 밀려 들어가면 안 됩니다.
    """
    rows = [r for r in (pasted or "").splitlines() if r.strip()]
    got: dict[int, str] = {}
    loose: list[str] = []
    for row in rows:
        m = _NUM.match(row)
        if m and m.group(2).strip():
            got[int(m.group(1))] = m.group(2).strip()
        else:
            loose.append(row.strip())

    out = Landing(numbered=bool(got))
    if got:
        for i, before in enumerate(chunk.lines):
            n = chunk.first + i
            after = got.pop(n, "")
            out.pairs.append(Pair(n=n, before=before, after=after,
                                  why="" if after else "번호를 못 찾았습니다"))
        out.extra = [f"{n}. {t}" for n, t in sorted(got.items())] + loose
        return out

    # 번호가 통째로 없어진 경우. 줄 순서로 맞추는 수밖에 없습니다.
    for i, before in enumerate(chunk.lines):
        after = loose[i] if i < len(loose) else ""
        out.pairs.append(Pair(n=chunk.first + i, before=before, after=after,
                              why="" if after else "돌아온 줄이 모자랍니다"))
    out.extra = loose[len(chunk.lines):]
    return out
