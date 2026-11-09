# SPDX-License-Identifier: GPL-3.0-or-later
"""적용 결과를 **번역 패치 파일 하나**로 저장하고, 같은 게임의 원본에 한 번에 넣습니다.

## 왜 필요한가

번역을 실험할 때마다 "원본 복사 → 뽑기 → 번역 → 적용" 을 처음부터 되풀이하면 몇 분씩 걸립니다.
이미 한 번 잘 적용된 결과가 있다면, 그 결과 **파일들 자체**를 묶어 두었다가 새 원본 복사본에
그대로 얹으면 몇 초면 같은 번역된 게임이 됩니다.

## 무엇이 들어가나

[게임에 적용] 은 건드리는 파일을 먼저 ``__gameloc_backup__`` 에 원본째 복사해 둡니다. 그래서
"백업에 있는 원본" 과 "지금 게임 폴더의 파일" 을 비교하면 적용이 바꾼 것이 정확히 나옵니다.

- ``replace`` — 고쳐진 파일. **새 모습 전체**를 넣습니다(유니티 에셋은 글자 길이에 따라 뒤가 전부
  밀려서 '다른 부분만' 잘라 담을 수 없습니다).
- ``add`` — 새로 생긴 파일(Ren'Py 번역 사전, 울프에서 푼 파일들).
- ``remove`` — 없어진 파일(울프의 ``Data.wolf`` 는 백업으로 옮겨집니다).
- 한글 폰트 넣기를 기억해 둔 장부(``fixups``) — 넣은 뒤에도 [다시 적용] 이 폰트를 지우지 않게.

## 안전장치

1. **같은 원본인지 먼저 확인합니다.** 파일마다 '패치를 만들 때의 원본' 의 지문(SHA-256)을 같이 적어
   두고, 넣기 전에 지금 게임의 같은 파일이 그것과 일치하는지 봅니다. 하나라도 다르면(게임 버전이
   다름) **아무것도 안 바꾸고** 어느 파일이 다른지 알려 줍니다. 억지로 넣는 길은 두지 않았습니다.
2. **넣기 전에 원본을 백업합니다.** 그래서 [원래대로] 로 되돌아갑니다.
3. 이미 번역이 적용된 게임이면 먼저 원래대로 되돌리고 넣습니다(실험 반복용).
4. 하나라도 실패하면 **원래대로 되돌린 뒤** 알립니다. 반쯤 넣은 상태로 두지 않습니다.
5. 패치 안의 경로가 게임 폴더 밖을 가리키거나(``..``), 실행 파일(.exe·.dll 새로 추가 등)을
   새로 심으려 하면 거부합니다.

## 조심할 점

패치에는 **게임 파일의 일부가 그대로 들어 있습니다.** 본인이 산 게임으로 실험하는 용도입니다.
남에게 나눠 주면 안 됩니다(번역표만 나누려면 엑셀·project.json 을 쓰세요). 또 남이 만든 패치는
게임 폴더의 파일을 바꾸므로 믿을 수 있는 것만 쓰세요.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from . import fixups
from .apply import ADDED_LIST, BACKUP_DIR, _backup, _backup_root, _remember_added, restore

FORMAT = 1
PATCH_JSON = "patch.json"
SUFFIX = ".gameloc-patch.zip"
CHUNK = 8 * 1024 * 1024
RISKY_NEW = {".exe", ".dll", ".bat", ".cmd", ".ps1", ".scr", ".msi", ".com", ".lnk", ".vbs", ".jar"}
NOTE = ("이 패치에는 게임 파일의 일부가 들어 있습니다. 본인이 산 게임의 실험용으로만 쓰고 "
        "남에게 나눠 주지 마세요.")


class PatchError(Exception):
    """사용자에게 그대로 보여 줄 수 있는 말로 쓴 실패."""


def _noop(_: str) -> None:
    pass


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _same(a: Path, b: Path) -> bool:
    if a.stat().st_size != b.stat().st_size:
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            x, y = fa.read(CHUNK), fb.read(CHUNK)
            if x != y:
                return False
            if not x:
                return True


def _internal(rel: str) -> bool:
    return rel.split("/", 1)[0].startswith("__gameloc_")


def _game_base(root: Path) -> Path:
    """패치의 경로가 기준으로 삼는 폴더 = 백업이 놓이는 폴더."""
    found = _backup_root(root)
    if found is not None:
        return found
    from .detect import detect

    return Path(detect(root).root)


# ------------------------------------------------------------------ 만들기

@dataclasses.dataclass
class PatchEntry:
    rel: str
    action: str                 # replace | add | remove
    base_sha: str = ""
    new_sha: str = ""
    size: int = 0


def _changes(root: Path) -> tuple[Path, list[PatchEntry]]:
    """백업과 지금 게임을 견줘 적용이 바꾼 파일 목록을 냅니다. 아무것도 안 바꿉니다."""
    root = Path(root)
    base = _backup_root(root)
    if base is None:
        raise PatchError("이 게임은 아직 번역을 적용한 적이 없어 패치로 만들 것이 없습니다. "
                         "먼저 [게임에 적용] 을 하세요.")
    bk = base / BACKUP_DIR
    entries: list[PatchEntry] = []

    for src in sorted(bk.rglob("*")):
        if not src.is_file() or src.name == ADDED_LIST:
            continue
        rel = src.relative_to(bk).as_posix()
        if _internal(rel):
            continue
        live = base / rel
        if not live.is_file():
            entries.append(PatchEntry(rel, "remove", _sha(src)))
        elif not _same(src, live):
            entries.append(PatchEntry(rel, "replace", _sha(src), _sha(live), live.stat().st_size))
    listing = bk / ADDED_LIST
    if listing.is_file():
        for line in listing.read_text("utf-8").splitlines():
            rel = line.strip()
            if not rel or _internal(rel):
                continue
            live = base / rel
            if live.is_file():
                entries.append(PatchEntry(rel, "add", "", _sha(live), live.stat().st_size))
    if not entries:
        raise PatchError("원본과 달라진 파일이 없습니다. 번역이 실제로 들어갔는지 확인하세요.")
    return base, entries


def export_patch(root: Path, out: Path | None = None, *, progress=_noop) -> Path:
    """적용된 게임의 변경분을 패치 파일로 저장합니다. 게임 폴더는 건드리지 않습니다."""
    root = Path(root)
    base, entries = _changes(root)

    from . import __version__
    ledger = fixups._load(base / fixups.FIXUPS_FILE)
    meta = {
        "format": FORMAT, "gameloc": __version__, "note": NOTE,
        "created": datetime.now().isoformat(timespec="seconds"),
        "game": root.name, "engine": _engine_of(root),
        "files": [dataclasses.asdict(e) for e in entries],
        "fixups": ledger or None,
    }
    out = Path(out) if out else root.parent / (root.name + SUFFIX)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6,
                             allowZip64=True) as zf:
            zf.writestr(PATCH_JSON, json.dumps(meta, ensure_ascii=False, indent=1))
            for e in entries:
                if e.action == "remove":
                    continue
                progress(f"  + {e.rel} ({_mb(e.size)})")
                zf.write(base / e.rel, "files/" + e.rel)
        os.replace(tmp, out)
    finally:
        if tmp.exists():
            tmp.unlink()
    n = {a: sum(1 for e in entries if e.action == a) for a in ("replace", "add", "remove")}
    progress(f"패치를 만들었습니다: 바뀐 파일 {n['replace']}개, 새 파일 {n['add']}개, "
             f"없어진 파일 {n['remove']}개 · {_mb(out.stat().st_size)}")
    return out


OVERLAY_SUFFIX = "_번역파일"
FINISH_BAT = "finish_translation.bat"      # 이름은 영문: 한글 이름은 명령창 코드페이지에 따라 못 찾을 수 있음
OVERLAY_README = "읽어보세요.txt"


def export_overlay(root: Path, out_dir: Path | None = None, *, progress=_noop) -> Path:
    """적용된 게임의 변경분을 **게임 폴더에 그대로 덮어쓸 폴더**로 내보냅니다.

    gameloc 없이 쓰는 길입니다. 이 폴더 안의 내용을 같은 게임의 원본 폴더에 복사해 덮어쓰면
    번역된 게임이 됩니다. 안전 확인·백업·되돌리기는 없습니다(그건 [번역 패치 넣기] 의 몫).
    원본이 사라지는 파일(울프의 Data.wolf)은 덮어쓰기로 지울 수 없어서, 이름을 바꾸는
    ``번역마무리.bat`` 를 함께 넣습니다.
    """
    root = Path(root)
    base, entries = _changes(root)
    out_dir = Path(out_dir) if out_dir else root.parent / (root.name + OVERLAY_SUFFIX)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise PatchError(f"내보낼 폴더가 이미 있고 비어 있지 않습니다: {out_dir}\n"
                         "지우거나 다른 이름을 고르세요(남은 옛 파일이 섞이지 않게 합니다).")
    out_dir.mkdir(parents=True, exist_ok=True)
    removed: list[str] = []
    for e in entries:
        if e.action == "remove":
            removed.append(e.rel)
            continue
        progress(f"  + {e.rel} ({_mb(e.size)})")
        dst = out_dir / e.rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(base / e.rel, dst)
    n = {a: sum(1 for e in entries if e.action == a) for a in ("replace", "add", "remove")}
    steps = ["1. 이 폴더 안의 모든 파일·폴더를 같은 게임의 원본 폴더에 복사해 덮어씁니다 "
             "(\"같은 이름의 파일을 바꾸시겠습니까\" 에서 [모두 바꾸기])."]
    if removed:
        lines = ["@echo off", "chcp 65001 >nul", "cd /d \"%~dp0\""]
        for rel in removed:
            win = rel.replace("/", "\\")
            lines.append(f'if exist "{win}" ren "{win}" "{Path(rel).name}.orig"')
        lines += ["echo Done. You can start the game now.", "pause"]
        (out_dir / FINISH_BAT).write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))
        steps.append(f"2. 게임 폴더에서 {FINISH_BAT} 를 한 번 실행합니다 "
                     f"(원본 {', '.join(removed)} 의 이름을 .orig 로 바꿔 둡니다. 안 하면 게임이 "
                     "옛 파일을 먼저 읽어 번역이 안 나옵니다).")
    steps.append(f"{len(steps) + 1}. 게임을 실행합니다.")
    readme = ["번역 파일 덮어쓰기용 폴더", "",
              f"원래 게임: {root.name}", ""] + steps + [
              "", "※ 원본을 따로 보관한 사본에 쓰세요. 덮어쓰면 원본 파일은 사라집니다"
              "(원래대로 되돌려 주는 장치가 없습니다).",
              "※ " + NOTE]
    (out_dir / OVERLAY_README).write_bytes(("\r\n".join(readme) + "\r\n").encode("utf-8"))
    progress(f"덮어쓰기용 폴더를 만들었습니다: 바뀐 파일 {n['replace']}개, 새 파일 {n['add']}개"
             + (f", 이름을 바꿀 파일 {n['remove']}개" if n["remove"] else ""))
    return out_dir


def _engine_of(root: Path) -> str:
    from .detect import detect

    try:
        return detect(root).engine
    except Exception:                                   # noqa: BLE001
        return ""


def _mb(n: int) -> str:
    return f"{n / 1024 ** 3:.1f}GB" if n >= 1024 ** 3 else f"{n / 1024 ** 2:,.1f}MB"


# ------------------------------------------------------------------ 넣기

@dataclasses.dataclass
class PatchReport:
    replaced: int = 0
    added: int = 0
    removed: int = 0
    already: int = 0                  # 이미 패치가 만든 모습인 파일
    restored_first: bool = False
    dry_run: bool = False

    def summary(self) -> str:
        head = "[미리보기] 아직 아무것도 안 바꿨습니다. " if self.dry_run else "패치를 넣었습니다. "
        s = (f"{head}바꾼 파일 {self.replaced}개, 새 파일 {self.added}개, "
             f"없앤 파일 {self.removed}개")
        if self.already:
            s += f", 이미 같은 파일 {self.already}개"
        return s


def _check_rel(rel: str) -> None:
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts or rel.startswith(("/", "\\")) or ":" in rel:
        raise PatchError(f"패치 안에 게임 폴더 밖을 가리키는 경로가 있습니다: {rel}")
    if _internal(rel):
        raise PatchError(f"패치가 gameloc 의 내부 파일을 건드리려 합니다: {rel}")


def read_meta(patch: Path) -> dict:
    try:
        with zipfile.ZipFile(patch) as zf:
            meta = json.loads(zf.read(PATCH_JSON).decode("utf-8"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        raise PatchError("번역 패치 파일이 아니거나 깨져 있습니다.") from None
    if meta.get("format") != FORMAT:
        raise PatchError(f"이 패치는 형식 {meta.get('format')} 입니다. "
                         "이 gameloc 은 형식 1 만 읽습니다(더 새 gameloc 으로 만든 패치일 수 있습니다).")
    return meta


def apply_patch(root: Path, patch: Path, *, dry_run: bool = False,
                progress=_noop) -> PatchReport:
    """패치를 게임 폴더에 넣습니다. 먼저 전부 검사하고, 문제가 없을 때만 씁니다."""
    root, patch = Path(root), Path(patch)
    meta = read_meta(patch)
    entries = [PatchEntry(**{k: e.get(k, d) for k, d in
                             (("rel", ""), ("action", ""), ("base_sha", ""),
                              ("new_sha", ""), ("size", 0))}) for e in meta.get("files", [])]
    if not entries:
        raise PatchError("패치에 파일이 하나도 없습니다.")
    for e in entries:
        _check_rel(e.rel)
        if e.action not in ("replace", "add", "remove"):
            raise PatchError(f"알 수 없는 동작입니다: {e.action} ({e.rel})")
        if e.action == "add" and Path(e.rel).suffix.lower() in RISKY_NEW:
            raise PatchError(f"패치가 실행 파일을 새로 심으려 합니다: {e.rel}")

    engine = _engine_of(root)
    if meta.get("engine") and engine and meta["engine"] != engine:
        raise PatchError(f"다른 종류의 게임용 패치입니다 (패치: {meta['engine']}, "
                         f"이 폴더: {engine}).")

    report = PatchReport(dry_run=dry_run)
    base = _game_base(root)
    has_backup = _backup_root(root) is not None

    # 이미 번역이 적용된 게임이면 원본으로 되돌린 뒤 넣습니다. 미리보기는 되돌리지 않고
    # **백업에 있는 원본** 을 기준으로 검사합니다.
    if has_backup and not dry_run:
        # 되돌리기는 **백업에 있는 옛 파일로 덮어씁니다.** 마지막 적용 뒤에 다른 곳에서 바뀐 파일
        # (게임 업데이트·손수정)이 있으면 그것이 소리 없이 사라지므로, 있으면 멈추고 알립니다.
        drift = fixups.drifted(root)
        if drift:
            shown = "\n  · ".join(drift[:8]) + (f"\n  … 외 {len(drift) - 8}개" if len(drift) > 8 else "")
            raise PatchError("패치를 넣지 않았습니다. 이 폴더에는 마지막 적용 뒤에 다른 곳에서 바뀐 "
                             "파일이 있어, 자동으로 원래대로 되돌리면 그 변경이 사라집니다. "
                             "게임 폴더는 그대로입니다. 그 파일들을 확인하고 필요하면 따로 보관한 뒤 "
                             "[원래대로] 를 누르고 다시 시도하세요:\n  · " + shown)
        progress("이미 번역이 적용된 폴더입니다. 원래대로 되돌린 뒤 패치를 넣습니다.")
        restore(root, progress=lambda _m: None)
        report.restored_first = True
        has_backup = False

    def original(rel: str) -> Path:
        cand = base / BACKUP_DIR / rel
        return cand if cand.is_file() else base / rel

    # ── 1. 전부 검사 (아무것도 안 쓴 채로) ─────────────────────────────
    problems: list[str] = []
    todo: list[PatchEntry] = []
    for e in entries:
        tgt = base / e.rel
        if e.action == "add":
            if tgt.is_file():
                if _sha(tgt) == e.new_sha:
                    report.already += 1
                    continue
                problems.append(f"{e.rel}: 원본에 없던 파일인데 이미 다른 내용으로 있습니다")
                continue
            todo.append(e)
            continue
        src = original(e.rel)
        if not src.is_file():
            if e.action == "remove" and not tgt.exists() and (base / BACKUP_DIR / e.rel).is_file():
                report.already += 1
                continue
            problems.append(f"{e.rel}: 이 게임에 그 파일이 없습니다")
            continue
        got = _sha(src)
        if e.action == "replace" and tgt.is_file() and got == e.new_sha and not has_backup:
            report.already += 1
            continue
        if got != e.base_sha:
            problems.append(f"{e.rel}: 원본이 패치를 만든 게임과 다릅니다 (게임 버전이 다른 것 같습니다)")
            continue
        todo.append(e)
    if problems:
        shown = "\n  · ".join(problems[:12])
        more = f"\n  … 외 {len(problems) - 12}개" if len(problems) > 12 else ""
        raise PatchError("패치를 넣지 않았습니다 — 이 게임과 맞지 않습니다. "
                         "게임 파일은 그대로입니다" +
                         (" (이미 번역돼 있던 것은 원래대로 되돌렸습니다)" if report.restored_first else "")
                         + f":\n  · {shown}{more}")

    for e in todo:
        if e.action == "replace":
            report.replaced += 1
        elif e.action == "add":
            report.added += 1
        else:
            report.removed += 1
    if dry_run:
        return report

    # ── 2. 쓰기: 실패하면 되돌립니다 ───────────────────────────────────
    written: list[str] = []
    added_paths: list[Path] = []
    try:
        with zipfile.ZipFile(patch) as zf:
            for e in todo:
                tgt = base / e.rel
                if e.action == "remove":
                    _backup(tgt, base)
                    tgt.unlink()
                    progress(f"  ✕ {e.rel} (백업으로 옮김)")
                    continue
                if e.action == "replace":
                    _backup(tgt, base)
                tgt.parent.mkdir(parents=True, exist_ok=True)
                tmp = tgt.with_name(tgt.name + ".gameloc-tmp")
                h = hashlib.sha256()
                with zf.open("files/" + e.rel) as src, open(tmp, "wb") as dst:
                    while chunk := src.read(CHUNK):
                        h.update(chunk)
                        dst.write(chunk)
                if h.hexdigest() != e.new_sha:
                    tmp.unlink()
                    raise PatchError(f"{e.rel}: 패치 안의 파일이 깨져 있습니다")
                os.replace(tmp, tgt)
                written.append(e.rel)
                if e.action == "add":
                    added_paths.append(tgt)
                progress(f"  ✓ {e.rel}")
        if added_paths:
            _remember_added(base, added_paths)
        ledger = meta.get("fixups")
        if ledger:
            fixups._save(base / fixups.FIXUPS_FILE, ledger)
        fixups.record_written(base, written)
    except Exception as exc:                            # noqa: BLE001
        progress("패치를 넣다 실패해 원래대로 되돌립니다.")
        try:
            restore(root, progress=lambda _m: None)
        finally:
            for tmp in base.rglob("*.gameloc-tmp"):
                tmp.unlink()
        if isinstance(exc, PatchError):
            raise
        raise PatchError(f"패치를 넣다 실패했습니다: {type(exc).__name__}: {exc} "
                         "(게임은 원래대로 되돌렸습니다)") from exc
    return report
