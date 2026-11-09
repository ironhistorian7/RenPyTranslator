# SPDX-License-Identifier: GPL-3.0-or-later
"""**비어 있는 언어 표를 채웁니다** (유니티 Localization).

## 왜 필요한가

한국어를 넣다 만 게임이 있습니다. 실제로 본 게임은 이랬습니다.

    localization-string-tables-japanese(ja)   22,313 바이트   항목 445개
    localization-string-tables-korean(ko)      3,137 바이트   항목  20개  ← 껍데기

한국어 **로케일은 등록되어 있는데 내용이 없습니다.** 그런데 유니티는 컴퓨터
언어를 보고 로케일을 고릅니다. 그래서 한국어 윈도우에서 돌리면 게임이
한국어 표를 고르고, 그 표가 비어 있으니 화면이 이렇게 됩니다.

    No translation found for 'titlemenu…' in UIkankei

개발자는 일본어 컴퓨터에서 만들었을 테니 한 번도 못 봤을 겁니다.

## 왜 일본어 표를 고치는 것으로는 안 되는가

게임이 **한국어 표를 봅니다.** 일본어 표에 한국어를 넣어 봐야 쳐다보지도
않습니다. 그러니 게임이 실제로 보는 그 표를 채워야 합니다.

## 어떻게

두 표가 **같은 모양이고 같은 번호를 씁니다.** 다른 것은 글자 한 칸뿐입니다.

    NAME_ja   m_Id=157673582592   m_Localized="ペネロペ"
    NAME_ko   m_Id=157673582592   m_Localized="페넬로페"

가리키는 곳(``m_SharedData``)까지 똑같습니다. 그래서 일본어 표를 훑어
번호마다 우리 번역을 넣어 주면 끝입니다.

## 이미 들어 있는 것은 건드리지 않습니다

위 ``NAME_ko`` 의 "페넬로페" 는 **개발자가 직접 넣은 것**입니다. 기계번역으로
덮어쓰면 사람이 한 번역을 지우는 셈이라, 비어 있는 자리만 채웁니다.

## 크기가 바뀌므로 장부도 고쳐야 합니다

표가 3KB 에서 22KB 로 자랍니다. Addressables 는 번들 크기를 catalog.bin 에
따로 적어 두므로 그것도 같이 고쳐야 합니다 — 안 그러면 **게임이 아예 안
켜집니다**. ``catalog.retune`` 이 그 일을 합니다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import catalog, typetree

# localization-string-tables-korean(ko)_assets_all.bundle
_TABLES = re.compile(r"localization-string-tables-(.+?)\(([a-zA-Z-]+)\)_", re.I)


@dataclass
class Report:
    source: str = ""
    target: str = ""
    filled: int = 0                 # 새로 채운 항목
    kept: int = 0                   # 이미 있어서 그대로 둔 것
    missing: int = 0                # 번역이 없어 못 채운 것
    was: int = 0                    # 번들 크기 (전)
    now: int = 0                    # 번들 크기 (후)
    tables: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def plain(self) -> list[str]:
        if not self.filled and not self.kept:
            if self.missing:
                # **까닭을 잃으면 안 됩니다.** 여기서 그냥 "채울 것이
                # 없었습니다" 라고만 하면, 사용자는 번역을 다 해 놓고도
                # 왜 아무것도 안 들어갔는지 알 길이 없습니다.
                return [f"{self.missing:,}개를 채울 수 있었는데 우리 번역이 "
                        "비어 있어 하나도 못 넣었습니다.",
                        "  · 번역을 채우고 다시 눌러 주세요."] + self.notes
            return self.notes or ["채울 것이 없었습니다."]
        out = [f"비어 있던 {self.target} 표에 {self.filled:,}개를 넣었습니다 "
               f"({self.source} 표를 본떴습니다)."]
        if self.kept:
            out.append(f"  · 이미 들어 있던 {self.kept:,}개는 그대로 뒀습니다 "
                       "— 게임 제작자가 넣은 번역입니다")
        if self.missing:
            out.append(f"  · {self.missing:,}개는 우리 번역이 비어 있어 건너뛰었습니다")
        out.append(f"  · 번들 {self.was:,} → {self.now:,} 바이트 (장부도 같이 고쳤습니다)")
        return out + self.notes


def locale_bundles(root: Path) -> dict[str, Path]:
    """``{언어코드: 문자열표 번들}``. Addressables 게임이 아니면 빈 것."""
    cat = catalog.is_addressables(root)
    if cat is None:
        return {}
    out: dict[str, Path] = {}
    for f in sorted(cat.parent.rglob("*.bundle")):
        m = _TABLES.search(f.name)
        if m:
            out[m.group(2).lower()] = f
    return out


def _read(path: Path, gen) -> tuple:
    import UnityPy

    env = UnityPy.load(str(path))
    if gen is not None:
        env.typetree_generator = gen
    return env, [o for o in env.objects if o.type.name == "MonoBehaviour"]


def _stem(name: str) -> str:
    """``NAME_ja`` → ``NAME``. 언어 꼬리를 뗀 표 이름."""
    return name.rsplit("_", 1)[0] if "_" in name else name


def fill(root: Path, src_code: str, dst_code: str, translate,
         *, unity_version: str = "", dry_run: bool = False) -> Report:
    """``translate(원문) -> 번역문`` 을 받아 빈 표를 채웁니다.

    번역이 빈 문자열이면 그 항목은 **넣지 않습니다.** 빈 값을 넣으면 원문도
    아니고 번역도 아닌 빈 화면이 됩니다.
    """
    rep = Report(source=src_code, target=dst_code)
    found = locale_bundles(root)
    src_path, dst_path = found.get(src_code), found.get(dst_code)
    if src_path is None or dst_path is None:
        rep.notes.append(
            f"{src_code}·{dst_code} 두 언어의 문자열 표를 다 찾지는 못했습니다.")
        return rep

    gen = typetree.generator(Path(root), unity_version)
    if gen is None:
        rep.notes.append("게임 코드에서 타입 정보를 되살리지 못해 표를 못 읽습니다.")
        return rep

    _env, src_objs = _read(src_path, gen)
    by_table: dict[str, dict[int, str]] = {}
    for o in src_objs:
        try:
            t = o.read_typetree()
        except Exception:                               # noqa: BLE001
            continue
        rows = t.get("m_TableData")
        if rows is None:
            continue
        by_table[_stem(str(t.get("m_Name") or ""))] = {
            r["m_Id"]: r.get("m_Localized") or "" for r in rows}

    env, dst_objs = _read(dst_path, gen)
    rep.was = dst_path.stat().st_size
    touched = 0
    for o in dst_objs:
        try:
            t = o.read_typetree()
        except Exception:                               # noqa: BLE001
            continue
        rows = t.get("m_TableData")
        if rows is None:
            continue
        src_rows = by_table.get(_stem(str(t.get("m_Name") or "")))
        if not src_rows:
            continue
        have = {r["m_Id"] for r in rows}
        rep.kept += len(have)
        added = 0
        for mid, original in src_rows.items():
            if mid in have:
                continue
            ko = (translate(original) or "").strip()
            if not ko:
                rep.missing += 1
                continue
            rows.append({"m_Id": mid, "m_Localized": ko,
                         "m_Metadata": {"m_Items": []}})
            added += 1
        if not added:
            continue
        rows.sort(key=lambda r: r["m_Id"])
        o.save_typetree(t)
        rep.filled += added
        rep.tables.append(str(t.get("m_Name")))
        touched += 1

    if not touched or dry_run:
        rep.now = rep.was
        return rep

    data = _serialize(env)
    _verify(data, gen, rep)
    dst_path.write_bytes(data)
    rep.now = dst_path.stat().st_size

    cat = catalog.is_addressables(root)
    if cat is not None:
        catalog.retune(cat, {dst_path.name: rep.now})
    return rep


def _serialize(env) -> bytes:
    last = None
    for packer in ("original", "lz4", "none", None):
        try:
            return env.file.save(packer=packer)
        except (NotImplementedError, TypeError, ValueError) as e:
            last = e
    raise RuntimeError(f"번들을 다시 쓸 수 없습니다: {last}")


def _verify(data: bytes, gen, rep: Report) -> None:
    """**쓰기 전에** 다시 읽어 확인합니다. 망가진 것을 넣으면 게임이 안 켜집니다."""
    import io

    import UnityPy

    env = UnityPy.load(io.BytesIO(data))
    env.typetree_generator = gen
    total = 0
    for o in env.objects:
        if o.type.name != "MonoBehaviour":
            continue
        try:
            t = o.read_typetree()
        except Exception as exc:                        # noqa: BLE001
            raise RuntimeError(f"다시 읽지 못했습니다: {exc}") from exc
        total += len(t.get("m_TableData") or [])
    if total < rep.filled:
        raise RuntimeError(
            f"다시 읽으니 항목이 {total}개뿐입니다 (넣은 것 {rep.filled}개)")
