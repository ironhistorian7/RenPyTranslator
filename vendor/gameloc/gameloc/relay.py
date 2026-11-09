# SPDX-License-Identifier: GPL-3.0-or-later
"""한 곳에 다 때려박지 않고, 여러 곳에 나눠 보내기.

## 왜 필요한가

무료 한도는 **서비스마다 따로** 걸립니다. 그래서 나눠 보내면 그냥 더해집니다.

    DeepL 50만 자 + 구글 50만 자 + Gemini + NIM …

10만 문장짜리 게임은 250만 자쯤 되는데, 한 곳으로는 어디를 써도 모자라거나
돈이 듭니다. 여러 곳을 이어 붙이면 공짜로 넘길 수 있습니다.

## 두 가지 방식

**이어달리기** — 고른 순서대로 씁니다. 앞이 한도가 끝나거나 계속 막히면
다음으로 넘어갑니다. **대부분을 한 엔진이 맡으니 말투가 안 섞입니다.**

**동시에 보내기** — 여러 곳에 한꺼번에 보냅니다. 엔진 수만큼 빨라지지만,
말투가 조금씩 달라집니다.

## 말투가 섞이는 문제를 줄이는 법

문장을 하나씩 번갈아 뿌리면 **한 대화 안에서** 사람 말투가 바뀝니다.

    DeepL   "그래, 알겠어."
    Gemini  "예, 알겠습니다."      ← 같은 인물, 다음 줄

그래서 **파일 단위로** 나눕니다. 한 장면은 한 엔진이 통째로 맡으니 대화
안에서는 일관되고, 장면이 바뀔 때만 미세하게 달라집니다. 고유명사는
용어집으로 못 박을 수 있지만 **말투는 못 박습니다** — 그래서 나누는 단위가
중요합니다.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import translate as mt

# 한 엔진이 이만큼 잇달아 막히면 "이 곳은 지금 안 되는구나" 로 보고 다음으로
# 넘어갑니다. 한도가 끝난 것인지 잠깐 막힌 것인지는 서버가 잘 안 알려 줍니다.
GIVE_UP_AFTER = 6


@dataclass
class Leg:
    """나눠 보낼 곳 하나."""

    pid: str
    key: str = ""
    model: str = ""
    base: str = ""

    def make(self):
        return mt.make_provider(self.pid, self.key, self.model, self.base)


@dataclass
class Stage:
    """한 곳이 실제로 무엇을 했나. 화면에 그대로 보여 줍니다."""

    label: str
    done: int = 0
    failed: int = 0
    note: str = ""


@dataclass
class PlanReport:
    total: int = 0
    done: int = 0
    skipped: int = 0
    failed: int = 0
    throttled: int = 0
    stages: list[Stage] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def summary(self) -> str:
        s = f"자동 번역 {self.done}건 완료"
        if self.failed:
            s += f", 남은 것 {self.failed}건"
        if self.skipped:
            s += f", 건너뜀 {self.skipped}건"
        return s

    def plain(self) -> list[str]:
        """어느 곳이 얼마나 했는지. **이게 이 기능의 값입니다** —
        한도가 어디서 끝났는지 사람이 알아야 다음에 계획을 짭니다."""
        return [f"  · {s.label}: {s.done:,}건"
                + (f" · {s.note}" if s.note else "")
                for s in self.stages]


def _absorb(out: PlanReport, rep, label: str) -> Stage:
    st = Stage(label=label, done=rep.done, failed=rep.failed)
    out.done += rep.done
    out.skipped += rep.skipped
    out.throttled += getattr(rep, "throttled", 0)
    for p in rep.problems:
        if len(out.problems) < 40:
            out.problems.append(f"[{label}] {p}")
    out.stages.append(st)
    return st


def _todo(entries: list, lang: str) -> list:
    """아직 번역이 안 채워진 것들."""
    return [e for e in entries if not (e.translations.get(lang) or "").strip()]


# ---------------------------------------------------------------- 이어달리기

def relay(entries: list, legs: list[Leg], *, lang: str = "ko",
          src: str = "auto", glossary=None, engine: str = "",
          speed: str = "", progress=lambda _m: None,
          should_stop=lambda: False) -> PlanReport:
    """고른 순서대로. 앞이 못 하면 남은 것만 다음으로 넘깁니다.

    **남은 것만** 넘기는 것이 핵심입니다. 앞 엔진이 잘 해 놓은 것을 뒤에서
    다시 번역하면 말투가 섞이고 시간도 두 배로 듭니다.
    """
    out = PlanReport(total=len(entries))
    left = _todo(entries, lang)
    for i, leg in enumerate(legs, 1):
        if not left or should_stop():
            break
        try:
            provider = leg.make()
        except Exception as exc:                        # noqa: BLE001
            progress(f"[{i}/{len(legs)}] {leg.pid} 은 못 씁니다: {exc}")
            out.stages.append(Stage(label=leg.pid, note=str(exc)[:60]))
            continue

        progress("")
        progress(f"[{i}/{len(legs)}] {provider.label} — 남은 {len(left):,}건")
        rep = mt.translate_entries(
            left, provider, src=src, dst=lang, glossary=glossary,
            lang_col=lang, engine=engine, speed=speed,
            progress=progress, should_stop=should_stop)
        st = _absorb(out, rep, provider.label)

        before = len(left)
        left = _todo(left, lang)
        if left and len(left) == before and i < len(legs):
            # 한 건도 못 했습니다. 한도가 끝났거나 키가 틀렸거나.
            st.note = "한 건도 못 했습니다 — 다음 곳으로 넘어갑니다"
            progress(f"  · {provider.label} 가 한 건도 못 했습니다. 다음으로 넘어갑니다.")
        elif left:
            st.note = f"{len(left):,}건을 다음으로 넘깁니다"

    out.failed = len(left)
    return out


# ---------------------------------------------------------------- 동시에

def _split(entries: list, n: int) -> list[list]:
    """**파일 단위로** 나눕니다. 한 장면은 한 곳이 통째로 맡습니다.

    문장을 하나씩 번갈아 뿌리면 한 대화 안에서 말투가 바뀝니다.
    """
    if n <= 1:
        return [list(entries)]
    groups: dict[str, list] = {}
    for e in entries:
        locs = getattr(e, "locations", None) or []
        key = getattr(locs[0], "file", "") if locs else ""
        groups.setdefault(key, []).append(e)

    # 큰 파일부터 번갈아 담아야 몫이 고르게 갈립니다.
    buckets: list[list] = [[] for _ in range(n)]
    for _key, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        buckets.sort(key=len)
        buckets[0] += items
    return buckets


def spread(entries: list, legs: list[Leg], *, lang: str = "ko",
           src: str = "auto", glossary=None, engine: str = "",
           speed: str = "", progress=lambda _m: None,
           should_stop=lambda: False) -> PlanReport:
    """여러 곳에 한꺼번에. 빠른 대신 장면마다 말투가 조금씩 다릅니다."""
    out = PlanReport(total=len(entries))
    left = _todo(entries, lang)

    ready: list[tuple[Leg, object]] = []
    for leg in legs:
        try:
            ready.append((leg, leg.make()))
        except Exception as exc:                        # noqa: BLE001
            progress(f"{leg.pid} 은 못 씁니다: {exc}")
            out.stages.append(Stage(label=leg.pid, note=str(exc)[:60]))
    if not ready:
        out.failed = len(left)
        return out

    parts = _split(left, len(ready))
    progress(f"{len(ready)}곳에 나눠 보냅니다 — "
             + ", ".join(f"{p.label} {len(part):,}건"
                         for (_l, p), part in zip(ready, parts)))
    progress("  · 파일 단위로 나눕니다. 한 장면은 한 곳이 통째로 맡습니다.")

    lock = threading.Lock()
    reps: list = [None] * len(ready)

    def work(i: int) -> None:
        leg, provider = ready[i]
        try:
            reps[i] = mt.translate_entries(
                parts[i], provider, src=src, dst=lang, glossary=glossary,
                lang_col=lang, engine=engine, speed=speed,
                progress=lambda m: progress(f"  [{provider.label}] {m}"),
                should_stop=should_stop)
        except Exception as exc:                        # noqa: BLE001
            with lock:
                progress(f"  [{provider.label}] 멈췄습니다: {exc}")

    with ThreadPoolExecutor(max_workers=len(ready)) as pool:
        list(pool.map(work, range(len(ready))))

    for (_leg, provider), rep in zip(ready, reps):
        if rep is None:
            out.stages.append(Stage(label=provider.label, note="멈췄습니다"))
            continue
        _absorb(out, rep, provider.label)

    out.failed = len(_todo(left, lang))
    return out


def run(entries: list, legs: list[Leg], *, mode: str = "relay", **kw) -> PlanReport:
    """``mode`` 는 ``relay``(이어달리기) 또는 ``spread``(동시에)."""
    fn = spread if mode == "spread" else relay
    return fn(entries, legs, **kw)
