# SPDX-License-Identifier: GPL-3.0-or-later
"""[다시 적용] 이 **번역표 밖의 수정**을 지우지 않게 하는 장부.

## 무엇이 문제였나

[게임에 적용] 은 (백업이 있으면) 게임을 원래대로 되돌린 뒤 번역표(project.json)에 있는 것만
다시 씁니다. 그런데 게임 파일에는 번역표에 **없는** 수정이 섞일 수 있습니다.

- ``한글 폰트 넣기`` — 번역 문자열이 아니라 폰트 자산을 고칩니다. 번역표에 적히지 않습니다.
- 손으로 파일을 직접 고친 것(``m_Text`` 몇 개 등) — 번역표를 거치지 않았습니다.

그래서 다시 적용할 때마다 이 수정들이 **소리 없이** 사라졌습니다(RJ01700968 에서 두 번).
버튼 하나를 누른 것뿐인데 이미 고친 화면이 도로 □□□ 가 되고 원문이 돌아옵니다.

## 어떻게 고쳤나 — 세 가지

1. **다시 걸 수 있는 수정은 기억해서 다시 겁니다.** 폰트 넣기는 쓴 옵션을 게임 폴더의
   ``__gameloc_fixups__.json`` 에 적어 두고, 다시 적용한 뒤 같은 옵션으로 다시 돌립니다.
   (번역표가 아니라 **게임 폴더**에 적는 까닭: 문장을 새로 뽑아 번역표를 갈아엎어도 살아남고,
   [원래대로] 로 게임이 원본이 되면 함께 지워지기 때문입니다.)
2. **기억 못 하는 수정은 지우기 전에 따로 보관하고 알려 줍니다.** 적용이 끝날 때 쓴 파일의
   (크기, 수정 시각)을 ``__gameloc_applied__.json`` 에 적어 둡니다. 다음에 다시 적용할 때
   되돌리기 전에 그 값과 다르면 "그 뒤에 누가 고친 파일" 이라는 뜻입니다. 그 파일을
   ``__gameloc_drift__/<시각>/`` 로 복사해 두고 결과에 알립니다. 아무것도 조용히 사라지지 않습니다.
3. 위 둘은 **첫 적용에는 아무 일도 하지 않습니다**(백업이 없으면 건너뜁니다).
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

FIXUPS_FILE = "__gameloc_fixups__.json"
APPLIED_FILE = "__gameloc_applied__.json"
DRIFT_DIR = "__gameloc_drift__"
MAX_KEEP = 1024 ** 3          # 이보다 큰 파일은 복사해 두지 않고 알리기만 합니다


def home(root: Path) -> Path:
    """장부를 두는 폴더 = 백업이 실제로 놓인 폴더(전자 게임은 resources/app 안)."""
    from .apply import _backup_root

    root = Path(root)
    return _backup_root(root) or root


def _load(path: Path) -> dict:
    try:
        d = json.loads(path.read_text("utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")


def _stat(p: Path) -> list[int] | None:
    try:
        st = p.stat()
    except OSError:
        return None
    return [st.st_size, st.st_mtime_ns]


# ------------------------------------------------ 1. 다시 걸 수 있는 수정 (폰트)

def font_runs(root: Path) -> list[dict]:
    return list(_load(home(root) / FIXUPS_FILE).get("font", []))


def record_font(root: Path, opts: dict, files: list[str]) -> None:
    """폰트 넣기를 마쳤을 때 부릅니다. 같은 옵션이면 덮고, 다르면 뒤에 덧붙입니다."""
    h = home(root)
    data = _load(h / FIXUPS_FILE)
    runs = [r for r in data.get("font", []) if r.get("opts") != opts]
    runs.append({"opts": opts, "files": sorted(files),
                 "at": datetime.now().isoformat(timespec="seconds")})
    data["font"] = runs
    _save(h / FIXUPS_FILE, data)
    record_written(root, files)         # 방금 고친 파일의 '지금 모습'도 장부에 맞춥니다


def fixup_files(root: Path) -> set[str]:
    return {f for r in font_runs(root) for f in r.get("files", [])}


def clear(root: Path) -> None:
    """[원래대로] 를 눌렀을 때. 게임이 원본이 되었으니 기억할 것이 없습니다."""
    h = home(root)
    for name in (FIXUPS_FILE, APPLIED_FILE):
        try:
            (h / name).unlink()
        except OSError:
            pass


def replay(root: Path, progress=lambda m: None) -> list[str]:
    """기억해 둔 수정을 다시 겁니다. 결과 화면에 붙일 안내문을 돌려줍니다."""
    runs = font_runs(root)
    if not runs:
        return []
    from .font import fix

    notes: list[str] = []
    for run in runs:
        o = run.get("opts", {})
        progress("이전에 넣어 둔 한글 폰트를 다시 넣습니다.")
        try:
            rep = fix(Path(root), ttf=Path(o["ttf"]) if o.get("ttf") else None,
                      unfreeze=bool(o.get("unfreeze")), os_font=bool(o.get("os_font")),
                      progress=progress)
        except Exception as exc:                          # noqa: BLE001
            notes.append(f"⚠  한글 폰트를 다시 넣지 못했습니다 ({type(exc).__name__}: {exc})\n"
                         "   → [한글 폰트 넣기] 를 한 번 눌러 주세요. 번역은 들어갔습니다")
            continue
        if not rep.patched:
            notes.append("⚠  한글 폰트를 다시 넣었지만 고친 곳이 없었습니다\n"
                         "   → 글자가 □□□ 로 나오면 [한글 폰트 넣기] 를 눌러 주세요")
        else:
            notes.append(f"ℹ  한글 폰트를 다시 넣었습니다 ({len(rep.patched)}곳)")
    return notes


# ------------------------------------------------ 2. 쓴 파일 장부 (모르는 수정 감지)

def record_written(root: Path, rels: list[str]) -> None:
    """적용·폰트 넣기가 방금 쓴 파일의 (크기, 수정 시각) 을 적어 둡니다."""
    if not rels:
        return
    h = home(root)
    if not (h / "__gameloc_backup__").is_dir():
        return                          # 백업이 없으면 되돌릴 일도 없습니다
    data = _load(h / APPLIED_FILE)
    files = data.setdefault("files", {})
    for rel in rels:
        st = _stat(h / rel)
        if st is not None:
            files[rel.replace("\\", "/")] = st
    _save(h / APPLIED_FILE, data)


def drifted(root: Path) -> list[str]:
    """마지막에 적어 둔 뒤 **다른 손이 고친** 파일. 기억해 둔 수정이 만든 것은 뺍니다."""
    h = home(root)
    files = _load(h / APPLIED_FILE).get("files", {})
    known = {f.replace("\\", "/") for f in fixup_files(root)}
    out = []
    for rel, was in files.items():
        if rel in known:
            continue
        now = _stat(h / rel)
        if now is not None and now != was:
            out.append(rel)
    return sorted(out)


def keep_drifted(root: Path, rels: list[str]) -> tuple[Path | None, list[str]]:
    """되돌리기 전에 그 파일들을 따로 복사해 둡니다. (보관 폴더, 너무 커서 못 둔 것)"""
    if not rels:
        return None, []
    h = home(root)
    dest = h / DRIFT_DIR / datetime.now().strftime("%Y%m%d_%H%M%S")
    skipped: list[str] = []
    for rel in rels:
        src = h / rel
        try:
            if src.stat().st_size > MAX_KEEP:
                skipped.append(rel)
                continue
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / rel)
        except OSError:
            skipped.append(rel)
    return dest, skipped
