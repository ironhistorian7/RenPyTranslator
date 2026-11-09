# SPDX-License-Identifier: GPL-3.0-or-later
"""남에게 주는 파일에서 **누구의 컴퓨터인지** 지웁니다.

## 왜

진단 보고서 첫 줄이 이랬습니다.

    폴더   : C:\\Users\\홍길동\\Desktop\\20260815\\게임이름

그리고 파일 맨 아래에는 우리가 이렇게 적어 뒀습니다.

    이 파일을 그대로 보내주시면 어디에 대사가 있는지 판단할 수 있습니다.

시키는 대로 커뮤니티에 올리면 **자기 윈도우 계정 이름을 같이 올리게
됩니다.** 번역 꾸러미도 남에게 주는 것이니 똑같습니다.

## 무엇을 지우는가

집 안쪽만 지웁니다. 게임 폴더 이름은 남깁니다 — 그게 있어야 무슨
게임인지 알아보고 도울 수 있습니다.

    C:\\Users\\홍길동\\Desktop\\20260815\\게임\\Data  →  …\\게임\\Data
    /home/gildong/games/x                        →  …/games/x
    /Users/gildong/games/x                       →  …/games/x

계정 이름을 딴 데서 쓰는 경우도 있어(폴더 이름, 파일 이름) 그 낱말
자체를 통째로 찾아 지웁니다.
"""

from __future__ import annotations

import os
import re

HIDDEN = "…"

# C:\Users\누구  ·  /home/누구  ·  /Users/누구
_HOMES = [
    re.compile(r"(?i)[A-Z]:[\\/]+Users[\\/]+[^\\/\r\n\"']+"),
    re.compile(r"(?i)[A-Z]:[\\/]+Documents and Settings[\\/]+[^\\/\r\n\"']+"),
    re.compile(r"/home/[^/\r\n\"']+"),
    re.compile(r"/Users/[^/\r\n\"']+"),
]


# 흔해서 남의 글에 우연히 들어 있을 이름들. 이런 것까지 지우면
# 멀쩡한 글이 망가집니다.
COMMON = {"root", "user", "users", "admin", "administrator", "guest",
          "owner", "home", "pc", "test"}


def _me() -> list[str]:
    """이 컴퓨터의 계정 이름들. 짧거나 흔한 것은 뺍니다."""
    names = [(os.environ.get(k) or "").strip()
             for k in ("USERNAME", "USER", "LOGNAME")]
    try:
        names.append(os.path.basename(os.path.expanduser("~").rstrip("/\\")))
    except Exception:                                   # noqa: BLE001
        pass
    out = [n for n in names if len(n) >= 3 and n.lower() not in COMMON]
    return sorted(set(out), key=len, reverse=True)


def scrub(text: str) -> str:
    """글 안의 집 경로와 계정 이름을 지웁니다."""
    if not text:
        return text
    for pat in _HOMES:
        text = pat.sub(HIDDEN, text)
    for name in _me():
        # 낱말째로만 지웁니다. 'root' 를 통째로 찾으면 'gameroot' 의
        # 가운데가 뜯겨 멀쩡한 글이 망가집니다.
        text = re.sub(r"(?<![0-9A-Za-z])%s(?![0-9A-Za-z])" % re.escape(name),
                      HIDDEN, text)
    return text


def scrub_path(path) -> str:
    return scrub(str(path))
