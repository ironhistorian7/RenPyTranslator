# SPDX-License-Identifier: GPL-3.0-or-later
"""번역 서버가 '너무 빨리 부른다' 고 막을 때 어떻게 할 것인가.

## 무슨 일이 벌어지는가

    HTTP error 429 : Too Many Requests

번역 서버가 잠깐 문을 닫은 것입니다. 프로그램이 고장 난 게 아닙니다.
게임 한 개에 문장이 수천 개라 짧은 시간에 수천 번을 부르니, 서버가
"잠깐 쉬어라" 하는 것입니다.

## 전에는 왜 통째로 실패했는가

1. 세 번만 다시 해 보고 포기했습니다. 간격도 1.5초·3초뿐이었습니다.
   서버가 막는 시간은 보통 그보다 훨씬 깁니다.
2. 한 갈래가 막혀도 **나머지 갈래는 계속 두드렸습니다.** 막힌 문을 더
   세게 두드린 셈이라 막힌 시간이 오히려 길어집니다.
3. 그래서 뒤에 남은 문장들이 줄줄이 같은 실패를 겪고, 번역이 통째로
   날아갔습니다.

## 지금은

**브레이크 하나를 모두가 나눠 씁니다.** 한 갈래가 막히면 나머지도 같이
섭니다. 그리고 다시 갈 때는 전보다 천천히 갑니다.

    첫 번째 막힘 → 다 같이 멈춤 → 5초 뒤 재개, 요청 간격 1초
    또 막힘      → 15초 뒤 재개, 간격 2초
    또 막힘      → 40초, 간격 4초 …

잘 풀리면 간격을 조금씩 줄여 원래 속도로 돌아옵니다. 서버가
``Retry-After`` 로 "몇 초 뒤에 오라" 고 알려 주면 그 말을 따릅니다.
"""

from __future__ import annotations

import threading
import time

# 막힐 때마다 이만큼 기다립니다(초). 마지막 값을 넘으면 계속 그 값입니다.
WAITS = (5, 15, 40, 90, 180)
# 재개한 뒤 요청과 요청 사이에 두는 틈(초).
GAP_FIRST = 1.0
GAP_MAX = 8.0
# 이만큼 잇달아 성공하면 틈을 줄입니다.
EASE_AFTER = 12

# 서버가 '너무 많다' 또는 '지금 벅차다' 고 말하는 신호들.
BUSY_CODES = (429, 500, 502, 503, 504, 529)


# ---------------------------------------------------------------- 속도 고르기
#
# 왜 고르게 하는가 — **IP 사정이 사람마다 다릅니다.** 공유기·회사망·VPN 을
# 쓰면 남이 이미 써 버린 몫까지 같이 걸립니다. 우리가 하나로 정해 주면
# 어떤 사람에게는 늘 막히고, 어떤 사람에게는 공연히 느립니다.
#
# **틈(gap)이 진짜 손잡이입니다.** 틈은 모든 갈래가 함께 쓰는 시계라
# 전체 속도를 그대로 정합니다. 갈래 수는 틈이 0 일 때만 뜻이 있습니다.

SPEEDS = {
    "safe": {
        "label": "안전하게",
        "workers": 1, "gap": 1.0,
        "why": "한 갈래로 초당 한 번만. 가장 덜 막히지만 오래 걸립니다.",
    },
    "normal": {
        "label": "보통",
        "workers": 3, "gap": 0.2,
        "why": "살살 시작해서, 잘 풀리면 틈을 없앱니다. 대부분 이걸로 됩니다.",
    },
    "fast": {
        "label": "빠르게",
        "workers": 5, "gap": 0.0,
        "why": "처음부터 전속력. 막히기 시작하면 오히려 더 느려질 수 있습니다.",
    },
}
DEFAULT_SPEED = "normal"

# '빠르게' 로 이만큼 잇달아 막히면 스스로 한 단계 내려갑니다. 사용자가
# 고른 것을 뒤집는 일이라, 반드시 **화면에 알리고** 합니다.
GIVE_UP_FAST = 3

# 한 번 부르고 대답이 올 때까지 대략 이만큼(초). 짐작을 내놓기 위한 값일
# 뿐이라 정확할 필요는 없습니다.
ROUND_TRIP = 0.7


def speed_of(name: str) -> dict:
    return SPEEDS.get((name or "").strip().lower(), SPEEDS[DEFAULT_SPEED])


def guess_seconds(sentences: int, batch: int, speed: str) -> float:
    """이 속도로 대략 몇 초 걸릴지. **막히지 않았을 때**의 값입니다.

    막히면 한 번에 5~180초씩 더 붙습니다 — 그래서 '빠르게' 가 실제로는
    더 느려질 수 있고, 그 사실을 화면에서 미리 보여 주려고 씁니다.
    """
    if sentences <= 0 or batch <= 0:
        return 0.0
    calls = -(-sentences // batch)          # 올림
    s = speed_of(speed)
    by_threads = s["workers"] / ROUND_TRIP
    rate = by_threads if s["gap"] <= 0 else min(by_threads, 1.0 / s["gap"])
    return calls / rate


def server_body(exc: Exception) -> str:
    """HTTP 오류 응답의 본문(서버가 한 말). 한 번만 읽고 예외에 붙여 둡니다."""
    cached = getattr(exc, "_gl_body", None)
    if cached is not None:
        return cached
    body = ""
    reader = getattr(exc, "read", None)
    if callable(reader):
        try:
            body = reader().decode("utf-8", "replace")
        except Exception:                               # noqa: BLE001
            body = ""
    try:
        exc._gl_body = body                             # type: ignore[attr-defined]
    except Exception:                                   # noqa: BLE001
        pass
    return body


def server_message(exc: Exception) -> str:
    """서버 본문에서 사람이 읽을 한 줄(``error.message``). 없으면 빈 문자열."""
    body = server_body(exc).strip()
    if not body:
        return ""
    try:
        import json
        data = json.loads(body)
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            return str(err.get("message") or err.get("status") or "")[:300]
        if isinstance(err, str):
            return err[:300]
    except Exception:                                   # noqa: BLE001
        pass
    return body[:300]


# OpenAI 는 '요청이 너무 잦다' 와 '잔액(한도)이 없다' 를 **똑같이 429** 로 돌려줍니다.
# 앞의 것은 기다리면 풀리지만 뒤의 것은 영영 안 풀립니다 — 예전에는 둘 다
# "서버가 속도를 늦추라고 합니다" 로 보여서, 잔액이 없는 사용자가 이유도 모른 채
# 기다리기만 했습니다.
#
# 표지는 일부러 **좁게** 잡았습니다. Gemini 는 분당 한도에 걸려도 "You exceeded your
# current quota … billing details" 라고 하고 ``RESOURCE_EXHAUSTED`` 로 돌려주는데,
# 그건 기다리면 풀립니다. 넓게 잡으면 Gemini 의 '잠시 쉬어 갑니다' 가 깨집니다.
# 그래서 OpenAI 가 잔액 없음에만 붙이는 코드(``insufficient_quota``)만 봅니다.
_QUOTA_MARKS = ("insufficient_quota",)

# DeepL 은 아예 **전용 코드**를 씁니다 — 456 은 "Quota Exceeded"(그 달 글자
# 한도 소진) 하나뿐이라 본문을 볼 필요도 없습니다. 실제 제보(고궁사):
# 이걸 몰라서 매 묶음마다 3번씩 다시 시도하다 "HTTP Error 456: Unrecoverable
# Error" 만 반복해 게임 하나 번역이 멈춘 것처럼 보였습니다.
_DEEPL_QUOTA_CODE = 456


def is_quota(exc: Exception) -> bool:
    """기다려도 안 풀리는 '잔액·한도 없음' 인가."""
    code = getattr(exc, "code", None)
    if code == _DEEPL_QUOTA_CODE:
        return True
    if code not in (402, 429):
        return False
    low = server_body(exc).lower()
    return any(m in low for m in _QUOTA_MARKS)


def busy_for(exc: Exception) -> float | None:
    """이 오류가 '잠시 쉬어라' 인가. 맞으면 서버가 말한 초, 아니면 ``None``.

    ``0`` 도 참값입니다 — '막혔지만 몇 초인지는 안 알려줬다' 는 뜻이라
    반드시 ``is None`` 으로 견줘야 합니다.
    """
    code = getattr(exc, "code", None)
    if code not in BUSY_CODES:
        # 키가 틀렸거나 문장이 이상한 것은 기다린다고 나아지지 않습니다.
        return None
    if is_quota(exc):
        # 잔액이 없는 것은 기다린다고 나아지지 않습니다.
        return None
    hdrs = getattr(exc, "headers", None)
    if hdrs is not None:
        try:
            raw = hdrs.get("Retry-After")
        except Exception:                               # noqa: BLE001
            raw = None
        if raw:
            try:
                return max(0.0, min(600.0, float(str(raw).strip())))
            except ValueError:
                pass
    return 0.0


def is_busy(exc: Exception) -> bool:
    return busy_for(exc) is not None


class Brake:
    """모든 갈래가 함께 쓰는 브레이크.

    ``scale`` 은 기다리는 시간을 통째로 줄입니다 — 멈춤도, 요청 사이의
    틈도 함께. 검사할 때 0 으로 두면 실제로 안 기다리고도 같은 흐름을
    확인할 수 있습니다.
    """

    def __init__(self, scale: float = 1.0, gap: float = 0.0) -> None:
        """``gap`` 은 **처음에만** 두는 틈입니다. 바닥이 아닙니다.

        비공식 경로는 첫 타석부터 두들기면 막히는 일이 있어 살살
        시작합니다. 그런데 그 틈을 계속 물고 있으면 안 됩니다 —
        0.35초를 고정하면 초당 세 번이 한계라, 10만 문장짜리 게임이
        2.8시간에서 9.7시간이 됩니다. 실제로 그렇게 만들 뻔했습니다.

        그래서 잘 풀리면 **틈을 0 까지 줄여** 원래 속도로 돌아갑니다.
        서버가 괜찮다는데 우리가 굳이 기다릴 이유가 없습니다.
        """
        self.scale = scale
        self.floor = 0.0
        self.start = max(0.0, gap)
        self.lock = threading.Lock()
        self.until = 0.0          # 이 시각까지는 아무도 안 부릅니다
        self.gap = self.start     # 요청 사이에 두는 틈
        self.next_at = 0.0        # 다음 요청을 보내도 되는 시각
        self.hits = 0             # 막힌 횟수
        self.streak = 0           # 잇단 성공 횟수
        self.slept = 0.0          # 통틀어 쉰 시간

    # ── 부르기 전에 ──────────────────────────────────────────────────
    def wait(self, should_stop=lambda: False) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                due = max(self.until, self.next_at)
                if now >= due:
                    self.next_at = now + self.gap * self.scale
                    return
                nap = min(due - now, 0.25)
            if should_stop():
                return
            time.sleep(nap)
            self.slept += nap

    # ── 막혔을 때 ────────────────────────────────────────────────────
    def hit(self, said: float = 0.0) -> float:
        """다 같이 멈춥니다. 얼마나 쉬는지 돌려줍니다."""
        with self.lock:
            i = min(self.hits, len(WAITS) - 1)
            secs = max(said, WAITS[i]) * self.scale
            self.hits += 1
            self.streak = 0
            self.gap = min(GAP_MAX,
                           max(GAP_FIRST, self.start)
                           if self.gap <= self.start else self.gap * 2)
            self.until = time.monotonic() + secs
            self.next_at = self.until
            return secs

    # ── 잘 됐을 때 ───────────────────────────────────────────────────
    def eased(self) -> None:
        with self.lock:
            if self.gap <= 0:
                return
            self.streak += 1
            if self.streak >= EASE_AFTER:
                self.streak = 0
                # 잘 풀리면 끝까지 놓아 줍니다. 0.2초 밑으로 내려가면
                # 그냥 0 으로 — 있으나 마나 한 틈을 붙들 이유가 없습니다.
                self.gap = max(self.floor,
                               0.0 if self.gap <= 0.2 else self.gap / 2)

    # ── 스스로 한 단계 내려가기 ──────────────────────────────────────
    def slow_down(self, gap: float) -> bool:
        """다시는 이 틈 아래로 안 내려갑니다. 이미 그러고 있으면 ``False``.

        ``floor`` 는 평소 0 입니다 — 잘 풀리면 틈을 끝까지 놓아 주려고요.
        여기서만 바닥을 올립니다. '빠르게' 가 이 컴퓨터에서는 안 통한다는
        것이 드러났을 때입니다.
        """
        with self.lock:
            if self.floor >= gap:
                return False
            self.floor = gap
            self.gap = max(self.gap, gap)
            return True

    # ── 사람에게 알리기 ──────────────────────────────────────────────
    def advice(self, provider_label: str = "") -> str:
        if not self.hits:
            return ""
        s = (f"번역 서버가 {self.hits}번 '너무 빨리 부른다' 며 막았습니다. "
             "그때마다 쉬었다 이어서 했습니다.")
        if "구글" in provider_label:
            s += (" 구글 무료 번역은 문장이 많으면 자주 막힙니다. "
                  "DeepL 무료 키를 넣으면 훨씬 안정적입니다.")
        return s
