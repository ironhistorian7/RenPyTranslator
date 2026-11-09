# SPDX-License-Identifier: GPL-3.0-or-later
"""게임이 남긴 로그(유니티 ``Player.log``)를 읽어 **왜 안 켜지는지** 알려 줍니다.

번역을 넣은 뒤 게임이 멈추거나 메뉴가 안 열릴 때, 사용자가 보는 것은 무한 로딩뿐입니다.
원인은 로그에 적혀 있습니다. 실제로 겪은 것만 표지로 삼았습니다.

  · ``An item with the same key has already been added. Key: 악마``
        게임이 이름표로 쓰는 값이 번역되어 서로 같아졌다 (Succubus Reborn)
  · ``ArgumentOutOfRangeException … UserData.GetMainDeck``
        번역이 잘못 들어간 채 한 번 켜서 생긴 **빈 세이브**가 남았다
  · ``Variable Momo not found``
        비주얼노벨(Fungus)의 변수 이름이 번역되었다 (Freedom)
  · ``No translation found for '...' in ...``
        유니티 Localization 이 세이브에 적힌 언어를 못 찾는다

읽기만 합니다. 파일을 옮기거나 지우는 일은 여기서 하지 않습니다.
"""

from __future__ import annotations

import dataclasses
import os
import re
from pathlib import Path


@dataclasses.dataclass
class Finding:
    code: str            # 표지 이름
    say: str             # 사람이 읽는 원인
    do: str              # 권하는 다음 수 (다시 적용 · 세이브 옮기기 · 데이터 초기화 · 없음)
    times: int = 1


_TAIL = 2_000_000          # 로그 끝 2MB 만 읽습니다 (Player.log 는 커질 수 있습니다)


def find_player_log(data_dir: Path | None) -> Path | None:
    """이 게임의 ``Player.log``. 게임 폴더 이름이 든 것 중 가장 최근 것."""
    if data_dir is None:
        return None
    base = Path(os.environ.get("USERPROFILE", "~")).expanduser() / "AppData" / "LocalLow"
    if not base.is_dir():
        return None
    needle = Path(data_dir).name.encode("utf-8", "ignore")     # 예: Succubus Reborn_Data
    best: tuple[float, Path] | None = None
    try:
        for log in base.glob("*/*/Player.log"):
            try:
                head = log.read_bytes()[:40000]
                if needle and needle not in head:
                    continue
                m = log.stat().st_mtime
            except OSError:
                continue
            if best is None or m > best[0]:
                best = (m, log)
    except OSError:
        return None
    return best[1] if best else None


def read_tail(path: Path) -> str:
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return ""
    return raw[-_TAIL:].decode("utf-8", "replace")


_RULES = (
    ("dup_key",
     re.compile(r"An item with the same key has already been added\.? Key: ?(.+)"),
     "게임이 **이름표로 쓰는 값**('{0}' 같은 것)이 번역되면서 서로 같아졌습니다. "
     "게임이 시작하다 멈춥니다(무한 로딩).",
     "reapply"),
    ("empty_save",
     re.compile(r"ArgumentOutOfRangeException[\s\S]{0,300}?GetMainDeck|"
                r"ArgumentOutOfRangeException[\s\S]{0,200}?(?:UserData|SaveData)"),
     "저장 데이터가 **비어 있어** 목록(덱 등)을 못 엽니다. 번역이 잘못 들어간 채 한 번 "
     "켜서 생긴 빈 세이브일 수 있습니다.",
     "movesave"),
    ("variable_missing",
     re.compile(r"Variable (\S+) not found"),
     "게임이 **변수 이름**('{0}')을 못 찾습니다. 변수 이름이 번역되었을 가능성이 큽니다.",
     "reapply"),
    ("no_translation",
     re.compile(r"No translation found for '([^']*)'"),
     "유니티 Localization 이 세이브에 적힌 언어를 못 찾습니다. 게임 파일이 아니라 "
     "게임 세이브의 설정 문제입니다.",
     "resetdata"),
)


def diagnose(text: str) -> list[Finding]:
    """로그 글에서 알려진 원인을 찾습니다. 많이 나온 것부터."""
    out: list[Finding] = []
    for code, rx, say, do in _RULES:
        hits = rx.findall(text)
        if not hits:
            continue
        first = hits[0] if isinstance(hits[0], str) else hits[0][0]
        out.append(Finding(code, say.format(str(first).strip()[:40]), do, len(hits)))
    nulls = len(re.findall(r"NullReferenceException", text))
    if nulls >= 5 and not out:
        out.append(Finding(
            "null_refs",
            f"게임이 필요한 값을 못 찾는 오류가 {nulls}번 났습니다. 번역된 이름표가 원인일 수 있습니다.",
            "reapply", nulls))
    out.sort(key=lambda f: -f.times)
    return out
