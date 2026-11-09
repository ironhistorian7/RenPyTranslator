# SPDX-License-Identifier: GPL-3.0-or-later
"""게임 **유형 판별** — 번역이 될지, 무엇을 조심해야 할지를 뽑기 전에 알려 줍니다.

같은 '유니티' 라도 어떤 것은 되고 어떤 것은 안 됩니다. 엔진만 보면 그 차이를 모르고,
차이는 **번역을 넣고 게임을 켠 뒤에야** 드러났습니다(무한 로딩, 글자 □□□, 대사 그대로).
그래서 뽑기 전에 파일 몇 개만 읽어 유형을 가립니다.

읽기만 합니다. 게임 파일은 절대 바꾸지 않고, 큰 파일을 통째로 열지도 않습니다
(번들은 머리 8바이트만 봅니다). 이 결과로 **번역 동작을 바꾸지는 않습니다** — 사용자에게
"이 유형이라 이런 점이 되고 이런 점은 안 된다" 를 미리 알려 주는 용도입니다.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from . import catalog, prefixed
from .detect import GameInfo

# 유니티 번들·에셋 파일의 머리글. 이게 아니면 암호화되었거나 다른 형식입니다.
_UNITY_MAGIC = (b"UnityFS", b"UnityWeb", b"UnityRaw", b"UnityArchive")
_BIG = 20 * 1024 * 1024            # 이보다 큰 파일만 '암호화 의심' 표본으로 봅니다


@dataclasses.dataclass
class Profile:
    family: str                     # 엔진 계열
    code: str                       # 짧은 유형 이름 (예: Unity/Mono/Addressables)
    label: str                      # 사람이 읽는 한 줄
    verdict: str                    # 가능 · 일부 · 어려움 · 먼저 풀기 · 모름
    traits: list[str] = dataclasses.field(default_factory=list)
    notes: list[str] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    def lines(self) -> list[str]:
        out = [f"유형: {self.label}", f"예상: {self.verdict}"]
        out += [f"  · {t}" for t in self.traits]
        out += [f"  ⚠ {n}" for n in self.notes]
        return out


def _head(path: Path, n: int = 16) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read(n)
    except OSError:
        return b""


def _unity_major(version: str) -> int:
    try:
        return int(version.split(".")[0])
    except (ValueError, IndexError):
        return 0


def _encrypted_candidates(data_dir: Path) -> tuple[int, int]:
    """(머리글이 유니티가 아닌 큰 파일 수, 살펴본 큰 파일 수). ``StreamingAssets`` 만 봅니다."""
    sa = data_dir / "StreamingAssets"
    if not sa.is_dir():
        return 0, 0
    bad = seen = 0
    try:
        for p in sa.rglob("*"):
            if seen >= 60:
                break
            if not p.is_file() or p.suffix.lower() in (".json", ".hash", ".bin", ".txt",
                                                        ".xml", ".csv"):
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size < _BIG:
                continue
            seen += 1
            head = _head(p, 300)
            if not head.startswith(_UNITY_MAGIC) and not prefixed.offset_of(head):
                bad += 1
    except OSError:
        pass
    return bad, seen


def _unity(info: GameInfo) -> Profile:
    data = info.data_dir or info.root
    major = _unity_major(info.unity_version)
    backend = info.scripting_backend or ("IL2CPP" if (info.root / "GameAssembly.dll").exists()
                                         else "")
    traits: list[str] = []
    notes: list[str] = []
    verdict = "가능"
    parts = ["Unity" + (f" {info.unity_version}" if info.unity_version else "")]

    if backend:
        parts.append(backend)
    if backend == "IL2CPP":
        verdict = "일부"
        traits.append("IL2CPP 빌드 — 게임 코드가 기계어라 데이터 구조를 못 읽는 스크립트가 있습니다")
        notes.append("글자는 바이트에서 직접 뽑습니다. 구조를 못 읽는 오브젝트의 문장은 빠질 수 있습니다")
    elif backend == "Mono":
        traits.append("Mono 빌드 — 게임 코드에서 데이터 구조를 되살려 읽을 수 있습니다")

    if major >= 6000:
        traits.append("유니티 6 계열")
        notes.append("유니티 6 은 시험해 본 게임이 적습니다. 적용 뒤 게임이 시작하는지 꼭 확인하세요")

    cat = catalog.is_addressables(info.root)
    if cat is not None:
        try:
            bundles = sum(1 for p in cat.parent.rglob("*.bundle"))
        except OSError:
            bundles = 0
        parts.append("Addressables")
        traits.append(f"Addressables 번들 {bundles}개 (대사가 번들에 들어 있는 유형)")
        try:
            known = bool(catalog.slots(cat.read_bytes()))
        except OSError:
            known = False
        if known:
            traits.append("카탈로그(catalog.bin) 형식을 압니다 — 번들 크기를 맞춰 넣습니다")
        else:
            traits.append("카탈로그(catalog.bin) 형식을 모릅니다 — 번들 크기를 맞추지 못합니다")
            notes.append("고친 번들이 그대로 읽히는지는 게임을 켜 봐야 압니다 "
                         "(유니티 6 게임 한 곳에서는 문제없이 읽혔습니다)")
    if (data / "data.unity3d").exists():
        parts.append("data.unity3d")
        traits.append("data.unity3d 한 덩어리 — 여는 데 몇 분 걸릴 수 있습니다")

    bad, seen = _encrypted_candidates(data)
    if seen and bad:
        verdict = "일부"
        traits.append(f"큰 파일 {seen}개 중 {bad}개는 유니티 형식이 아닙니다 (암호화 의심)")
        notes.append("암호화된 파일 안의 문장은 읽을 수 없어 번역되지 않을 수 있습니다")
        parts.append("암호화 의심")

    return Profile("Unity", "/".join(parts[1:]) or "Unity", " · ".join(parts), verdict,
                   traits, notes)


def classify(info: GameInfo) -> Profile:
    """``detect()`` 결과로 유형을 가립니다."""
    eng = info.engine
    if eng == "unity":
        return _unity(info)
    if eng == "rpgmaker":
        var = info.rm_variant or ""
        return Profile("RPG Maker", f"RPGMaker/{var}", f"RPG Maker {var}".strip(), "가능",
                       ["데이터가 JSON 이라 구조가 정해져 있습니다"],
                       ["플러그인이 이름으로 부르는 값은 번역하지 않고 두어야 합니다 "
                        "(자동으로 걸러냅니다)"])
    if eng == "renpy":
        return Profile("Ren'Py", "Renpy", f"Ren'Py {info.unity_version}".strip(), "가능",
                       ["원본 파일을 고치지 않고 번역 사전을 얹는 방식입니다"], [])
    if eng == "tyrano":
        return Profile("TyranoScript", "Tyrano", f"TyranoScript {info.unity_version}".strip(),
                       "가능", ["시나리오(.ks) 파일에서 대사를 뽑습니다"], [])
    if eng == "wolf":
        packed = any(p.suffix.lower() == ".wolf" for p in info.asset_files)
        traits = ["공통 이벤트·데이터베이스·맵에서 대사와 이름을 뽑습니다"]
        notes = []
        if packed:
            traits.append("자료가 .wolf 묶음으로 되어 있어, 적용할 때 글자가 든 묶음"
                          "(Data.wolf, 또는 BasicData·MapData)만 Data 폴더로 풀고 그 묶음 "
                          "파일은 백업 폴더로 옮깁니다 ([원래대로] 로 되돌립니다)")
            notes.append("묶음을 푸는 것은 제작자가 막아 둔 일입니다. 직접 산 게임을 "
                         "혼자 번역해 하는 데만 쓰세요. 푼 파일이나 번역 파일을 남에게 "
                         "나눠 주면 안 되며, 책임은 쓰는 사람에게 있습니다")
        from .engines.wolf import path_warning
        warn = path_warning(info.root)
        if warn:
            notes.append(" ".join(warn.replace("⚠", "", 1).split()))   # 화면에서는 한 문단으로
        return Profile("WOLF RPG", "Wolf", "WOLF RPG 에디터", "가능", traits, notes)
    if eng == "electron-packed":
        return Profile("Electron", "Electron/asar", "Electron (app.asar 로 묶임)", "먼저 풀기",
                       ["내용물이 app.asar 한 덩어리 안에 있습니다"],
                       ["먼저 풀어야 안쪽 엔진을 알 수 있습니다"])
    return Profile("알 수 없음", "unknown", "아직 모르는 유형", "모름",
                   [f"읽을 수 있는 텍스트 파일 {len(info.loose_files)}개"],
                   ["엔진을 못 알아봤습니다. 폴더가 맞는지 확인하세요"])
