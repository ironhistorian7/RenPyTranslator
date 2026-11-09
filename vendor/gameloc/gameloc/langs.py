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

"""문자열이 **무슨 언어인가**. 뽑을 원문 언어를 고르는 데 씁니다.

## 왜 필요한가

유니티 게임은 영어·일본어·중국어를 한 파일에 다 담아두는 경우가 흔합니다.
전부 뽑으면 같은 대사가 세 벌씩 나와서 목록이 세 배가 되고, 어느 줄이 어느
언어인지 뒤섞입니다. 영어를 한국어로 옮기려는 사람에게는 일본어·중국어 줄이
전부 방해물입니다.

## 어떻게 가르나

글자만 봅니다. 사전도 통계도 쓰지 않습니다 — 짧은 UI 문구에는 그런 게 잘
안 먹히고, 글자 종류만으로 충분히 갈립니다.

    가나(ひらがな·カタカナ)가 있으면      → 일본어
    한글이 있으면                          → 한국어
    한자만 있고 가나가 없으면              → 중국어 (일본어일 수도 있습니다)
    한자·가나·한글이 없고 알파벳이 있으면  → 영어(로마자)

한자만 있는 문자열은 **원리적으로** 중국어인지 일본어인지 구분할 수 없습니다
(`設定` 은 양쪽 다 맞습니다). 그래서 일본어나 중국어를 고르면 한자만 있는
문자열은 **양쪽 모두에 넣어** 줍니다. 빠뜨리는 것보다 몇 줄 더 나오는 편이
낫습니다.
"""

from __future__ import annotations

import re

KANA = re.compile(r"[぀-ヿｦ-ﾟ]")
HANGUL = re.compile(r"[가-힯ᄀ-ᇿ㄰-㆏]")
HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
LATIN = re.compile(r"[A-Za-z]")

# 사용자에게 보여줄 이름
NAMES = {
    "en": "영어",
    "ja": "일본어",
    "zh": "중국어",
    "ko": "한국어",
    "han": "한자만 (중국어·일본어 공용)",
    "other": "그 외 언어 (러시아어 등)",
    "sym": "숫자·기호",
}

CHOICES = ["all", "en", "ja", "zh", "ko"]


def guess(text: str) -> str:
    """``en`` / ``ja`` / ``zh`` / ``ko`` / ``han`` / ``other`` 중 하나."""
    s = text or ""
    if HANGUL.search(s):
        return "ko"
    if KANA.search(s):
        return "ja"
    if HAN.search(s):
        return "han"          # 한자만 — 중국어일 수도 일본어일 수도
    if LATIN.search(s):
        return "en"
    # 키릴·그리스·아랍·타이… 우리가 고를 수 있는 목록에 없는 문자 체계입니다.
    # 이것을 '알 수 없음' 으로 뭉뚱그리면 중국어를 골랐는데 러시아어가
    # 딸려 나옵니다 (실제로 그런 게임이 있었습니다).
    if any(ch.isalpha() for ch in s):
        return "other"
    return "sym"          # 숫자·기호뿐 — 어느 언어랄 것이 없습니다


def wanted_set(choice: str) -> set[str] | None:
    """고른 언어 하나 → 통과시킬 판정값들. ``all`` 이면 ``None``(전부)."""
    if not choice or choice == "all":
        return None
    if choice == "ja":
        return {"ja", "han"}
    if choice == "zh":
        return {"zh", "han"}
    if choice in ("en", "ko"):
        return {choice}
    return None


def matches(text: str, wanted: set[str] | None) -> bool:
    """``wanted`` 가 ``None`` 이면 전부 통과."""
    if wanted is None:
        return True
    kind = guess(text)
    if kind == "sym":
        # 숫자·기호만 있는 줄. 어느 언어라고 할 수 없으니 막지 않습니다.
        return True
    return kind in wanted


def census(texts) -> dict[str, int]:
    """뽑힌 것들이 무슨 언어였는지 세어 봅니다. 안내 문구에 씁니다."""
    out: dict[str, int] = {}
    for t in texts:
        k = guess(t)
        out[k] = out.get(k, 0) + 1
    return out
