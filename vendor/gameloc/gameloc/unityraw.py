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

"""타입 정보 없이, 유니티 오브젝트의 **바이트에서 직접** 문자열을 다루기.

## 왜 필요한가

유니티는 배포 빌드에 게임이 직접 만든 클래스의 구조를 안 넣습니다. 대개는
게임 DLL 로 되살릴 수 있는데(``typetree.py``), 그마저 실패하는 클래스가 있습니다.
Naninovel 의 ``Script`` 가 그런 경우고, **하필 거기에 대사가 전부 들어 있습니다.**

    MonoBehaviour 2710개 (타입정보 읽음 2679, 못 읽음 31)
    · Naninovel.Script  ×5
        パパ、ただいまぁ。お部屋入るねぇ。

## 어떻게 하나

구조는 몰라도 **문자열이 저장되는 규칙**은 압니다. 유니티는 언제나

    길이(4바이트 little-endian) + UTF-8 본문 + 4바이트 경계까지 0 채움

으로 씁니다. 그래서 바이트를 훑으며 이 모양을 찾으면 문자열과 **정확한 위치**를
얻습니다. 되돌려 넣을 때는 그 자리를 새 문자열로 갈아끼우면 됩니다. 길이가
달라져도 상관없습니다 — 유니티 문자열은 스스로 길이를 들고 다니고, 배열은
바이트 수가 아니라 **개수**로 세기 때문입니다.

## 무엇을 건드리고 무엇을 안 건드리나

구조를 모르는 채로 고치는 일이라 **일본어가 든 문자열만** 대상으로 삼습니다.
영어로 된 것은 대개 에셋 경로·라벨·명령 이름이고, 그걸 바꾸면 게임이 대상을
못 찾습니다. 대사만 건드리는 게 훨씬 안전합니다.
"""

from __future__ import annotations

import re
import unicodedata

MIN_STR = 2        # 바이트 기준 — 1바이트 문자열은 쓰레기일 확률이 큽니다
MAX_STR = 8192

_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿]")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def plausible(text: str) -> bool:
    """진짜 문자열인가, 우연히 그렇게 읽힌 바이트인가."""
    # 글자 수로 자르면 안 됩니다. "짧" 같은 한 글자 번역이 통째로 버려지고,
    # 그러면 적용 뒤 검증이 실패해 멀쩡한 번역이 안 들어갑니다.
    if not text or _CTRL.search(text):
        return False
    for c in text:
        if unicodedata.category(c) in ("Cn", "Co", "Cs"):
            return False
    return any(c.isalnum() or _CJK.match(c) for c in text)


def scan(data: bytes, *, limit: int = 20000) -> list[tuple[int, str]]:
    """``[(오프셋, 문자열)]``. 오프셋은 길이 숫자가 시작하는 자리입니다."""
    out: list[tuple[int, str]] = []
    n = len(data)
    i = 0
    while i + 4 <= n and len(out) < limit:
        size = int.from_bytes(data[i:i + 4], "little")
        if MIN_STR <= size <= MAX_STR and i + 4 + size <= n:
            try:
                text = data[i + 4:i + 4 + size].decode("utf-8")
            except UnicodeDecodeError:
                i += 4
                continue
            if plausible(text):
                out.append((i, text))
                i += 4 + size + (-size % 4)
                continue
        i += 4
    return out


# 영어 문장으로 볼 만한 모양. 구조를 모르는 채로 고치는 자리라 아주 깐깐하게
# 봅니다 — 여기서 명령 이름이나 에셋 경로를 잘못 집으면 게임이 조용히 망가집니다.
_LATIN_BAD = re.compile(r"""[/\\]|\.\w{2,4}$|_|\{|\}|<|>|\$|@|\||^\s*#""")
_WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")


def sentence_like(s: str) -> bool:
    """영어 **문장**인가. 라벨·명령·경로는 전부 떨어뜨립니다.

    통과 조건은 사람이 읽는 문장의 특징입니다 — 낱말이 여럿이고, 마침표나
    물음표로 끝나거나 충분히 길고, 경로·중괄호·밑줄 같은 기계 냄새가 없어야
    합니다. ``Move``, ``ui/main_menu``, ``{0} HP`` 같은 것은 전부 탈락합니다.
    """
    s = (s or "").strip()
    if not (8 <= len(s) <= 2000):
        return False
    if _CJK.search(s):
        return False
    if _LATIN_BAD.search(s):
        return False
    words = _WORD.findall(s)
    if len(words) < 3:
        return False
    if sum(len(w) for w in words) < len(s) * 0.5:   # 기호가 절반 넘으면 탈락
        return False
    if all(w.isupper() for w in words):             # ALL CAPS 라벨
        return False
    lowered = sum(1 for w in words if w[0].islower() or w.islower())
    ends = s.rstrip()[-1:] in ".!?…\"'"
    return bool(ends or lowered >= 2)


def latin_sentences(data: bytes) -> list[tuple[int, str]]:
    return [(off, s) for off, s in scan(data) if sentence_like(s)]


def translatable(data: bytes, *,
                 include_latin: bool = False) -> list[tuple[int, str]]:
    """번역 대상으로 삼을 것만.

    기본은 **일본어·중국어가 든 문자열만** 입니다. 영어 원문을 번역하려는
    경우(영어·중국어·일본어를 함께 지원하는 게임이 그렇습니다)에는
    ``include_latin`` 을 켜서 영어 문장까지 가져옵니다.
    """
    out = []
    for off, s in scan(data):
        if _CJK.search(s) or (include_latin and sentence_like(s)):
            out.append((off, s))
    return out


def patch_and_map(data: bytes,
                  edits: dict[int, str]) -> tuple[bytes, dict[int, int]]:
    """``{오프셋: 새 문자열}`` 을 적용한 바이트와, **새 위치 표**.

    위치 표가 핵심입니다. 고친 뒤 다시 훑어서 확인하면 '못 찾았다' 가
    '안 들어갔다' 인지 '훑기가 못 짚었다' 인지 구분이 안 됩니다. 쓴 자리를
    정확히 기억해 두면 그 자리를 직접 읽어 대조할 수 있습니다.
    """
    out = bytearray()
    moved: dict[int, int] = {}
    pos = 0
    for off in sorted(edits):
        if not 0 <= off <= len(data) - 4 or off < pos:
            raise ValueError(f"잘못된 오프셋 {off}")
        size = int.from_bytes(data[off:off + 4], "little")
        end = off + 4 + size + (-size % 4)
        if end > len(data):
            raise ValueError(f"오프셋 {off} 의 문자열이 범위를 넘습니다")
        body = edits[off].encode("utf-8")
        out += data[pos:off]
        moved[off] = len(out)
        out += len(body).to_bytes(4, "little") + body + b"\x00" * (-len(body) % 4)
        pos = end
    out += data[pos:]
    return bytes(out), moved


def _block(text: str) -> int:
    """길이 숫자 + 본문 + 4바이트 채움 까지 몇 바이트인가."""
    n = len(text.encode("utf-8"))
    return 4 + n + (-n % 4)


def layout_ok(before: bytes, after: bytes,
              edits: dict[int, str]) -> str | None:
    """고친 바이트가 **자리마다 그대로인지** 확인합니다. 어긋나면 그 까닭을.

    바이트 훑기는 짐작입니다. 문자열이 아닌 자리를 문자열로 잘못 짚어 거기에
    글을 쓰면 그 뒤가 전부 어긋나고, 게임은 켜지지만 그 표를 통째로 못 읽는
    가장 알아채기 어려운 고장이 납니다. 그래서 확인이 필요합니다.

    ## 다시 훑어서 세면 안 됩니다 — 그렇게 했다가 멀쩡한 번역을 버렸습니다

    처음에는 고친 바이트를 같은 규칙으로 **다시 훑어 개수를 비교**했습니다.
    그런데 훑기는 '이게 진짜 글자인가' 를 짐작으로 거릅니다. 그래서

        「くくく、どうかな。」   →   「……」

    처럼 **번역문에 글자가 하나도 없고 기호만 남으면** 훑기가 그걸 못 찾고,
    개수가 하나 줄었다는 이유로 **그 오브젝트의 멀쩡한 번역 24개가 통째로
    거부**되었습니다. 실제로 겪은 일입니다.

    ## 그래서 자리로 확인합니다

    우리는 **어디에 무엇을 썼는지 정확히 압니다.** 앞에서부터 밀린 만큼을
    더해 가며 그 자리를 직접 읽어 대조하면, 짐작이 끼어들 여지가 없습니다.
    문자열이 아닌 자리를 잘못 짚었다면 그 뒤가 밀려 바로 어긋납니다.
    """
    delta = 0
    for i, (off, text) in enumerate(scan(before, limit=10 ** 9), 1):
        want = edits.get(off, text)
        got = read_at(after, off + delta)
        if got != want:
            return (f"{i}번째 자리가 어긋났습니다 "
                    f"({want[:20]!r} 여야 하는데 {str(got)[:20]!r})")
        if off in edits:
            delta += _block(want) - _block(text)

    # 길이도 정확히 맞아야 합니다. 뒤에 뭔가 붙거나 잘렸다면 여기서 걸립니다.
    if len(after) != len(before) + delta:
        return (f"전체 길이가 안 맞습니다 "
                f"({len(before) + delta}바이트여야 하는데 {len(after)}바이트)")
    return None


def patch(data: bytes, edits: dict[int, str]) -> bytes:
    return patch_and_map(data, edits)[0]


def read_at(data: bytes, off: int) -> str | None:
    """그 자리에 적힌 문자열을 **규칙대로** 읽습니다 (훑기 아님)."""
    if not 0 <= off <= len(data) - 4:
        return None
    size = int.from_bytes(data[off:off + 4], "little")
    if size < 0 or off + 4 + size > len(data):
        return None
    try:
        return data[off + 4:off + 4 + size].decode("utf-8")
    except UnicodeDecodeError:
        return None


def missing(data: bytes, edits: dict[int, str],
            moved: dict[int, int]) -> list[str]:
    """쓴 자리를 직접 읽어, 안 들어간 번역을 돌려줍니다."""
    bad = []
    for off, new in edits.items():
        where = moved.get(off)
        if where is None or read_at(data, where) != new:
            bad.append(new)
    return bad


def verify(data: bytes, edits: dict[int, str],
           moved: dict[int, int] | None = None) -> bool:
    """번역이 전부 들어갔는가."""
    if moved is not None:
        return not missing(data, edits, moved)
    got = {s for _off, s in scan(data)}
    return all(new in got for new in edits.values())
