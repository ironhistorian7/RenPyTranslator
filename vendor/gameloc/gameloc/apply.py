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

"""번역을 게임 파일에 되돌려 넣습니다.

모든 파일은 처음 고치기 직전에 백업하므로 ``restore`` 로 출고 상태로
되돌릴 수 있습니다.
"""

from __future__ import annotations

import io
import json
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
import re
from pathlib import Path
from typing import Callable

from . import catalog, containers, heuristics, prefixed, typetree, unityraw
from .model import Location, Project
from .treepath import get_by_path, set_by_path

BACKUP_DIR = "__gameloc_backup__"
Progress = Callable[[str], None]


def _noop(_: str) -> None:
    pass


@dataclass
class ApplyReport:
    lang: str
    files_written: int = 0
    strings_written: int = 0
    skipped_untranslated: int = 0
    failures: list[str] = field(default_factory=list)
    risky: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    written: list[str] = field(default_factory=list)   # 실제로 쓴 파일(게임 폴더 기준) — fixups 장부용

    def summary(self) -> str:
        """무엇이 들어갔고 **무엇이 안 들어갔는지** 빠짐없이 적습니다.

        '번역이 적용 안 된다' 는 신고가 가장 많은데, 대부분은 어딘가에서
        조용히 걸러진 것입니다. 걸러진 까닭을 안 보여 주면 사용자는 원인을
        찾을 길이 없습니다.
        """
        head = (f"[{self.lang}] 파일 {self.files_written}개, "
                f"문자열 {self.strings_written}개 적용")
        why = []
        if self.skipped_untranslated:
            why.append(f"번역문이 비어 있어 건너뜀 {self.skipped_untranslated}건")
        if self.risky:
            why.append(f"이름으로 보여 건너뜀 {len(self.risky)}건")
        if self.failures:
            why.append(f"실패 {len(self.failures)}건")
        if why:
            head += " | " + ", ".join(why)
        if not self.strings_written:
            head += "\n  → 한 건도 안 들어갔습니다. 위의 까닭을 보세요."
        return head

    def report(self) -> list[str]:
        r"""사람이 읽는 결과. **맨 위에 결론**, 그 아래 종류별로 한 번씩.

        예전에는 이렇게 나왔습니다.

            ✓ …\level7 (100)
            ! …\level8: RuntimeError: …\level8: path_id 61104 의 구조가 …
            ✓ …\level9 (180)
            [ko] 파일 17개, 문자열 2855개 적용 | … 실패 2건
              ! …\level12: RuntimeError: …  (같은 설명 통째로 반복)

        좋은 소식과 나쁜 소식이 한 줄씩 번갈아 나오고, 같은 말이 두 번
        나오고, ``RuntimeError`` 와 ``path_id 61104`` 은 사용자에게 아무
        뜻이 없고, **"그래서 어쩌라고" 가 없습니다.**
        """
        out: list[str] = []
        if self.strings_written:
            out.append(f"끝났습니다 — 파일 {self.files_written}개에 "
                       f"{self.strings_written:,}개를 넣었습니다.")
        else:
            out.append("한 건도 넣지 못했습니다.")

        for kind, names in self._by_kind().items():
            uniq = sorted(set(names))
            shown = ", ".join(uniq[:6])
            if len(uniq) > 6:
                shown += f" 외 {len(uniq) - 6}개"
            out += ["",
                    f"⚠  {len(uniq)}군데는 손대지 않았습니다",
                    f"   {kind}",
                    f"   {shown}"]
            so = _SO_WHAT.get(kind)
            if so:
                out.append(f"   → {so}")

        if self.skipped_untranslated:
            out += ["",
                    f"ℹ  {self.skipped_untranslated:,}개는 번역칸이 비어 있어 "
                    "건너뛰었습니다",
                    "   → 번역을 채우고 [게임에 적용] 을 다시 누르면 들어갑니다"]

        if self.risky:
            out += ["",
                    f"ℹ  {len(self.risky)}개는 대사가 아니라 '이름' 으로 보여 "
                    "넣지 않았습니다",
                    "   → 일부러 둔 것입니다. 모델·모션·파일 이름을 바꾸면 "
                    "게임이 죽습니다"]

        for note in self.notes:
            out += ["", note]

        if not self.strings_written and len(out) == 1:
            out.append("   → 번역을 채웠는지, 게임 폴더가 맞는지 보세요.")
        return out

    def _by_kind(self) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for line in self.failures:
            rel, _, rest = line.partition(": ")
            groups.setdefault(_kind_of(rest or line), []).append(_short(rel))
        return groups

    def plain(self) -> list[str]:
        r"""실패를 **사람 말로, 같은 것끼리 묶어서**.

        전에는 이렇게 나왔습니다.

            ! ...\level12: RuntimeError: ...\level12: path_id 7546 의 구조가
              어긋나 건너뜁니다 (문자열 개수가 35개 → 34개로 어긋났습니다).
              이 오브젝트는 바이트를 짐작해서 읽은 것이라 …
            ! ...\level8: RuntimeError: ...\level8: … (같은 설명 통째로 반복)

        파일 이름이 두 번 겹치고, ``RuntimeError`` 는 개발자 말이고,
        ``path_id 7546`` 은 사용자에게 아무 뜻이 없고, 긴 설명이 건마다
        되풀이됩니다. 이제 종류별로 한 번만 설명하고 파일 이름을 모읍니다.
        """
        if not self.failures:
            return []
        groups = self._by_kind()
        out = []
        for kind, names in groups.items():
            uniq = sorted(set(names))
            shown = ", ".join(uniq[:6])
            if len(uniq) > 6:
                shown += f" 외 {len(uniq) - 6}개"
            out.append(f"{kind} — {shown}")
        return out


def _short(rel: str) -> str:
    """파일 이름만. 윈도우에서 만든 목록을 다른 데서 읽을 수도 있어
    두 가지 구분자를 모두 봅니다."""
    return rel.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or rel


# 실패 한 줄을 '무슨 일이 있었나' + '그래서 어쩌라고' 로. 뒤쪽이 없으면
# 사용자는 심각한 건지 무시해도 되는 건지 알 수가 없습니다. 실제로 신고
# 상당수가 "무슨 일이 일어났는지 몰라서" 였습니다.
_KINDS = (
    ("구조가 어긋나", "게임 파일 안이 예상과 달라 그 부분만 건너뛰었습니다",
     "게임은 정상으로 돕니다. 그 부분만 원문으로 보입니다"),
    ("오프셋", "글자가 놓인 자리가 밀려 있었습니다",
     "게임은 정상으로 돕니다. 그 부분만 원문으로 보입니다"),
    ("범위를 넘", "글자가 놓인 자리가 밀려 있었습니다",
     "게임은 정상으로 돕니다. 그 부분만 원문으로 보입니다"),
    ("안전하지 않아", "고치면 게임이 깨질 것 같아 그 파일은 두었습니다",
     "일부러 둔 것입니다. 그 파일은 원문으로 보입니다"),
    ("파일 없음", "게임 폴더에 그 파일이 없습니다",
     "게임을 지웠다 다시 깔았다면 [문장 뽑기] 부터 다시 하세요"),
    ("PermissionError", "파일이 잠겨 있습니다",
     "게임과 스팀을 끄고 [적용] 을 다시 누르세요"),
    ("MemoryError", "파일이 너무 커서 메모리가 모자랐습니다",
     "다른 프로그램을 끄고 다시 해 보세요"),
    ("타입 정보", "게임 내부 구조를 읽지 못했습니다",
     "그 파일은 통째로 원문으로 남습니다. [적용이 안 되었나요?] 를 눌러 보세요"),
)

_SO_WHAT = {say: so for _m, say, so in _KINDS}


def _kind_of(text: str) -> str:
    for mark, say, _so in _KINDS:
        if mark in text:
            return say
    # 개발자 말(RuntimeError: 등)은 떼어 냅니다.
    body = re.sub(r"^[A-Za-z_]*Error: ", "", text).strip()
    return body.split("(")[0].strip()[:60] or "알 수 없는 까닭"


def apply(
    proj: Project,
    lang: str,
    *,
    dry_run: bool = False,
    game_root: Path | None = None,
    progress: Progress = _noop,
    reserved_names: set[str] | None = None,
) -> ApplyReport:
    root = _resolve_root(proj, game_root)
    rep = ApplyReport(lang=lang)

    # Ren'Py 는 원본을 고치지 않습니다 — 사전 파일을 얹는 방식이라 경로가 통째로
    # 다릅니다.
    if proj.engine == "renpy":
        return _apply_renpy(proj, lang, root, rep, dry_run=dry_run,
                            progress=progress)

    # A caller rendering from an original snapshot can share its identifiers
    # with validation without scanning previously generated output plugins.
    reserved = set(proj.identifiers) | (
        _reserved_names(root, proj) if reserved_names is None else reserved_names)
    _CATALOG_UNKNOWN.clear()

    plan: dict[str, dict] = defaultdict(lambda: defaultdict(dict))
    meta: dict[tuple[str, object], Location] = {}

    for e in proj.entries:
        new = e.translations.get(lang, "")
        if not new or new == e.text:
            rep.skipped_untranslated += 1
            continue
        # 원문이 '무언가를 가리키는 이름' 이면 번역해서 넣으면 안 됩니다.
        # 모델·모션 이름을 바꾸면 플러그인이 대상을 못 찾아 게임이 죽습니다.
        if e.text in reserved:
            rep.risky.append(f"{e.text} → {new}  (게임이 이름으로 참조하는 문자열)")
            continue
        if heuristics.looks_like_identifier(e.text):
            rep.risky.append(f"{e.text} → {new}  (이름 모양)")
            continue
        # 덱 코드(``GD004=3,GD005=3,…``) 같은 코드 목록. 번역기가 글자 하나만
        # 건드려도 게임이 그 덱을 못 엽니다. 예전에 뽑은 목록에도 걸리게 여기서도 봅니다.
        if heuristics.looks_like_kv_code(e.text):
            rep.risky.append(f"{e.text[:40]} → …  (코드 목록)")
            continue
        # ``Apps.Masters.StatusMaster, Assembly-CSharp`` 같은 클래스 이름. 번역하면 게임이
        # 자기 자료 클래스를 못 찾아 검은 화면이 됩니다. 예전에 뽑은 목록도 여기서 막습니다.
        if heuristics.looks_like_dotnet(e.text):
            rep.risky.append(f"{e.text[:40]} → …  (클래스 이름)")
            continue
        for loc in e.locations:
            # 예전 버전으로 뽑아 둔 목록에는 입력 시스템 값 같은 것이 섞여
            # 있을 수 있습니다. 다시 뽑지 않아도 막히도록 여기서도 봅니다.
            if heuristics.is_denied_key(loc.ptr):
                rep.risky.append(f"{e.text} → {new}  ({loc.ptr.rsplit('.', 1)[-1]})")
                continue
            if loc.fmt == "typetree" and heuristics.is_ref_field(loc.ptr, e.text):
                rep.risky.append(f"{e.text} → {new}  ({loc.ptr.rsplit('.', 1)[-1]})")
                continue
            for ptr, part in _distribute(new, loc).items():
                plan[loc.file][_obj_key(loc)][ptr] = part
            meta[(loc.file, _obj_key(loc))] = loc

    # 울프는 파일마다 따로가 아니라 한꺼번에 — 묶음(Data.wolf)을 풀어 두는 일이
    # 게임 전체에 한 번이라서입니다.
    if proj.engine == "wolf":
        from .engines import wolf
        wolf.apply(proj, lang, root, rep, dry_run=dry_run, progress=progress,
                   edits_by_file={rel: per_obj[None] for rel, per_obj in plan.items()})
        _record_written(root, rep, dry_run)
        return rep

    for rel, per_obj in sorted(plan.items()):
        target = root / rel
        if not target.exists():
            rep.failures.append(f"{rel}: 파일 없음")
            continue
        try:
            n = _patch_file(target, root, per_obj, meta, rel, dry_run,
                            skipped=rep.risky,
                            enum_guard=proj.stats.get("extract_source") != "en")
        except Exception as exc:  # noqa: BLE001
            # _patch_file 이 이미 파일 이름을 붙여 놓는 경우가 있습니다.
            # 그대로 또 붙이면 이름이 두 번 겹쳐 찍힙니다.
            body = str(exc)
            if not body.startswith(rel):
                body = f"{type(exc).__name__}: {body}"
            rep.failures.append(f"{rel}: {body}")
            # 진행 중에는 나쁜 소식을 흘리지 않습니다. 끝에 종류별로 묶어
            # 한 번만 설명합니다 — 예전에는 같은 말이 두 번 나왔습니다.
            continue
        if n:
            rep.files_written += 1
            rep.strings_written += n
            rep.written.append(rel)
            progress(f"  ✓ {rel} ({n})")
    _record_written(root, rep, dry_run)
    if _CATALOG_UNKNOWN:
        rep.notes.append(
            f"ℹ  번들 {len(_CATALOG_UNKNOWN)}개를 고쳤지만 카탈로그(catalog.bin) 형식을 "
            "몰라 크기는 맞추지 못했습니다\n"
            "   → 유니티 6 게임은 대부분 이대로 잘 돕니다. 게임이 시작하다 멈추면 "
            "[원래대로] 를 누르고 알려 주세요")
    saved = _stale_save(root)
    if saved and rep.strings_written:
        rep.notes.append(
            f"ℹ  게임 폴더에 아주 작은 세이브 파일이 있습니다 ({saved})\n"
            "   → 예전에 실패한 실행이 남긴 빈 세이브일 수 있습니다. 덱·메뉴가 비어 "
            "있거나 열리지 않으면 그 파일을 다른 곳으로 옮긴 뒤 게임을 켜 보세요")
    return rep


def _record_written(root: Path, rep: ApplyReport, dry_run: bool) -> None:
    """방금 쓴 파일을 장부에 적습니다(다음 [다시 적용] 이 '그 뒤에 누가 고쳤나' 를 보려고).
    장부를 못 적어도 적용 자체는 성공입니다."""
    if dry_run or not rep.written:
        return
    try:
        from . import fixups
        fixups.record_written(root, rep.written)
    except Exception:                                   # noqa: BLE001
        pass


def _stale_save(root: Path) -> str:
    """게임 폴더 바로 아래의 아주 작은 ``save`` 파일 이름(있을 때만).

    번역이 잘못 들어간 채 한 번 실행한 게임은 **빈 세이브** 를 남기고, 그 뒤로는
    덱 목록이 비어 메뉴가 안 열립니다(``GetMainDeck`` 인덱스 오류). 지우면 고쳐지는데
    사용자는 원인을 알 길이 없습니다. 진짜 진행 기록은 훨씬 큽니다.
    """
    try:
        f = Path(root) / "save"
        if f.is_file() and f.stat().st_size <= 512:
            return f"save · {f.stat().st_size}바이트"
    except OSError:
        pass
    return ""


def _unity_version(root: Path) -> str:
    try:
        from .detect import detect
        return detect(root).unity_version
    except Exception:                     # noqa: BLE001
        return ""


def _resolve_root(proj: Project, game_root: Path | None) -> Path:
    """위치 경로가 실제로 맞아떨어지는 폴더를 고릅니다.

    Electron 게임은 사용자가 바깥 폴더를 넣지만 내용물은 ``resources/app/``
    안에 있습니다. 넘어온 폴더에서 파일이 안 보이면 프로젝트에 적힌 폴더로
    갑니다 — 안 그러면 '파일 없음' 만 잔뜩 뜨고 아무것도 안 바뀝니다.
    """
    given = Path(game_root) if game_root else None
    recorded = Path(proj.game_root) if proj.game_root else None
    if given is None:
        return recorded or Path(".")
    sample = next((l.file for e in proj.entries for l in e.locations), None)
    if sample is None or (given / sample).exists():
        return given
    if recorded is not None and (recorded / sample).exists():
        return recorded
    return given


ADDED_LIST = "__added__.txt"


def _safe_mapping(proj: Project, lang: str, root: Path,
                  rep: ApplyReport) -> dict[str, str]:
    """덧씌우기가 쓸 사전. **직접 고치는 길과 똑같은 관문**을 지납니다.

    덧씌우기라고 아무 글자나 바꾸면 안 됩니다. 모델·모션 이름을 바꾸면
    플러그인이 대상을 못 찾아 게임이 죽는 것은 이 길에서도 같습니다.
    """
    reserved = set(proj.identifiers) | _reserved_names(root, proj)
    out: dict[str, str] = {}
    for e in proj.entries:
        new = e.translations.get(lang, "")
        if not new or new == e.text:
            rep.skipped_untranslated += 1
            continue
        if e.text in reserved:
            rep.risky.append(f"{e.text} → {new}  (게임이 이름으로 참조하는 문자열)")
            continue
        if heuristics.looks_like_identifier(e.text):
            rep.risky.append(f"{e.text} → {new}  (이름 모양)")
            continue
        out[e.text] = new
    return out


def apply_overlay(proj: Project, lang: str, *, game_root: Path | None = None,
                  dry_run: bool = False,
                  progress: Progress = _noop) -> ApplyReport:
    """원본을 안 건드리고 사전 + 후크를 얹습니다 (RPG Maker · 티라노).

    Ren'Py 는 원래부터 이 방식이라 :func:`apply` 가 그리로 보냅니다.
    유니티는 이 길이 없습니다 — 모드 로더가 필요한데, 그게 싫어서 만든
    도구입니다. 대신 [마지막 수단] 이 그 자리를 맡습니다.
    """
    from . import overlay

    root = _resolve_root(proj, game_root)
    rep = ApplyReport(lang=lang)
    if proj.engine == "renpy":
        return _apply_renpy(proj, lang, root, rep, dry_run=dry_run,
                            progress=progress)
    if proj.engine not in ("rpgmaker", "tyrano"):
        rep.failures.append(
            f"{proj.engine} 게임에는 덧씌우기 길이 없습니다")
        return rep

    mapping = _safe_mapping(proj, lang, root, rep)
    rep.strings_written = len(mapping)
    if dry_run or not mapping:
        return rep

    if proj.engine == "rpgmaker":
        hook, host = overlay.rm_targets(root)
        body = overlay.rm_plugin_source(mapping)
        wired = overlay.rm_register(host.read_text("utf-8", errors="replace"))
    else:
        hook, host = overlay.ty_targets(root)
        body = overlay.ty_hook_source(mapping)
        wired = overlay.ty_register(host.read_text("utf-8", errors="replace"))

    # 후크는 **새로 넣는** 파일이라 지우면 그만입니다. 목록 파일(plugins.js ·
    # index.html)은 원본을 고치는 것이니 반드시 백업부터 합니다.
    _backup(host, root)
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(body, "utf-8")
    host.write_text(wired, "utf-8")
    rep.files_written = 2
    progress(f"  ✓ {hook.relative_to(root)}")
    progress(f"  ✓ {host.relative_to(root)}")
    _remember_added(root, [hook])
    return rep


def _apply_renpy(proj: Project, lang: str, root: Path, rep: ApplyReport, *,
                 dry_run: bool, progress: Progress) -> ApplyReport:
    """``game/`` 에 사전 json 과 짧은 후크 .rpy 를 새로 넣습니다.

    원본 스크립트는 열지도 않습니다. 되돌리기는 넣은 파일을 지우는 것으로
    끝나고, 사전에 없는 문장은 그냥 원문이 나옵니다.
    """
    import json

    from .engines import renpy

    game = renpy.is_renpy(root)
    if game is None:
        rep.failures.append("Ren'Py 의 game 폴더를 찾지 못했습니다")
        return rep

    mapping: dict[str, str] = {}
    for e in proj.entries:
        new = e.translations.get(lang, "")
        if not new or new == e.text:
            rep.skipped_untranslated += 1
            continue
        mapping[e.text] = new

    rep.strings_written = len(mapping)
    if dry_run or not mapping:
        return rep

    pack = game / renpy.PACK_JSON
    hook = game / renpy.HOOK_RPY
    pack.write_text(json.dumps(mapping, ensure_ascii=False, indent=0), "utf-8")
    hook.write_text(renpy.hook_source(), "utf-8")
    rep.files_written = 2
    progress(f"  ✓ {pack.relative_to(root)}")
    progress(f"  ✓ {hook.relative_to(root)}")

    # 전에 넣은 후크의 컴파일본이 남아 있으면 옛 번역이 되살아납니다.
    for stale in renpy.stale_compiled(game):
        try:
            stale.unlink()
        except OSError:
            rep.failures.append(f"{stale.name} 를 지우지 못했습니다")

    _remember_added(root, [pack, hook])
    return rep


def _remember_added(root: Path, paths: list[Path]) -> None:
    """되돌리기가 지워야 할, **새로 넣은** 파일 목록."""
    listing = root / BACKUP_DIR / ADDED_LIST
    listing.parent.mkdir(parents=True, exist_ok=True)
    known = set()
    if listing.exists():
        known = {ln.strip() for ln in listing.read_text("utf-8").splitlines()
                 if ln.strip()}
    known |= {str(p.relative_to(root)).replace("\\", "/") for p in paths}
    listing.write_text("\n".join(sorted(known)) + "\n", "utf-8")


def _reserved_names(root: Path, proj: Project) -> set[str]:
    """게임 폴더를 지금 다시 읽어 '이름으로 참조되는 문자열' 을 모읍니다."""
    if proj.engine != "rpgmaker":
        return set()
    try:
        from .engines import rpgmaker
        base = rpgmaker.is_rpgmaker(root)
        # 확실한 것만. 플러그인 추정치는 프로젝트에 저장된 목록을 따릅니다
        # (사용자가 '전부 번역' 을 골랐다면 그 선택을 존중해야 하므로).
        if base is None:
            return set()
        # 'js 파일까지 번역' 을 켜고 뽑은 목록이면 js 안의 문자열은 번역 대상이니
        # 여기서 막으면 안 됩니다.
        wants_js = bool(proj.stats.get("rm_js_literals"))
        return rpgmaker.collect_identifiers(
            base, include_plugin_values=False, include_js_literals=not wants_js)
    except Exception:
        return set()


def _distribute(new: str, loc: Location) -> dict[str, str]:
    """번역한 문단을 원래 있던 칸들에 나눠 넣습니다.

    RPG Maker 는 메시지 한 줄을 명령 하나로 저장합니다. 명령을 더하거나 빼면
    이벤트의 모든 인덱스가 밀리므로 **절대 개수를 바꾸지 않습니다** — 남는 줄은
    마지막 칸에 몰아넣고, 모자라면 빈 문자열로 둡니다.
    """
    if not loc.span:
        return {loc.ptr: new}
    n = len(loc.span)
    lines = new.split("\n")
    if len(lines) > n:
        lines = lines[: n - 1] + ["\n".join(lines[n - 1:])] if n > 1 else ["\n".join(lines)]
    lines += [""] * (n - len(lines))
    return dict(zip(loc.span, lines))


def _obj_key(loc: Location):
    """오브젝트를 가리키는 열쇠.

    직렬화 파일이 여러 개 든 번들(data.unity3d)에서는 ``path_id`` 가 파일마다 따로
    매겨져 번호만으로는 다른 오브젝트를 집습니다. ``serial`` 이 있으면 그 이름까지 같이
    씁니다. 없으면(대부분의 게임) 예전처럼 ``path_id`` 하나입니다.
    """
    return (loc.serial, loc.path_id) if loc.serial else loc.path_id


def _index_objects(env, keys) -> dict:
    """``keys`` (path_id 또는 (serial, path_id)) 로 오브젝트를 찾는 표."""
    by_pid = {o.path_id: o for o in env.objects}
    if any(isinstance(k, tuple) for k in keys):
        by_pid.update({(getattr(o.assets_file, "name", ""), o.path_id): o
                       for o in env.objects})
    return by_pid


def _patch_file(target: Path, root: Path, per_obj: dict, meta: dict,
                rel: str, dry_run: bool, skipped: list | None = None,
                enum_guard: bool = True) -> int:
    sample = next(iter(meta[(rel, k)] for k in per_obj))
    if sample.kind == "tyrano":
        return _patch_tyrano(target, root, per_obj[None], dry_run)
    if sample.kind == "jsliteral":
        return _patch_js(target, root, per_obj[None], dry_run)
    if sample.kind == "loose":
        return _patch_loose(target, root, per_obj[None], sample, dry_run)
    if sample.kind == "dll":
        return _patch_dll(target, root, per_obj[None], dry_run, skipped)
    return _patch_unity(target, root, per_obj, meta, rel, dry_run, skipped,
                        enum_guard)


def _patch_dll(target: Path, root: Path, edits: dict, dry_run: bool,
               skipped: list | None = None) -> int:
    """게임 프로그램(DLL) 안의 글자를 **같은 자리에 같거나 짧게** 덮어씁니다.

    자세한 까닭은 ``dllstrings`` 머리말에. 자리가 모자라 못 넣은 번역은 조용히 넘기지 않고
    ``skipped`` 에 남겨 결과 화면에 보입니다. 바꾼 뒤 다시 읽어 확인하고, 어긋나면 그 DLL 은
    손대지 않습니다.
    """
    from . import dllstrings

    data = target.read_bytes()
    res = dllstrings.patch(data, {int(k): v for k, v in edits.items()})
    if skipped is not None:
        for _off, new, room in res.too_long:
            skipped.append(f"{new[:30]}  (코드 글자 자리 부족 — {room}글자까지만 들어갑니다)")
    if not res.applied:
        return 0
    if not dry_run:
        _backup(target, root)
        target.write_bytes(res.data)
    return len(res.applied)


def _patch_js(target: Path, root: Path, edits: dict, dry_run: bool) -> int:
    """js 소스 안의 문자열 리터럴을 바꿉니다.

    주석·정규식·템플릿을 가려내는 토크나이저로 **문자열만** 찾아 바꾸고,
    바꾼 뒤 다시 읽어 문자열 개수와 내용이 그대로인지 확인합니다. 한 군데라도
    어긋나면 그 파일은 아예 손대지 않습니다 — 게임이 켜자마자
    ``SyntaxError`` 로 죽는 것보다 번역이 덜 들어가는 편이 낫습니다.
    """
    from . import jslex

    text = target.read_text("utf-8")
    try:
        out, n = jslex.replace(text, edits)
    except jslex.Unsafe as exc:
        raise RuntimeError(f"안전하지 않아 건너뜁니다 ({exc})") from None
    if out == text:
        return 0
    if not dry_run:
        _backup(target, root)
        target.write_text(out, "utf-8")
    return n


def _patch_tyrano(target: Path, root: Path, edits: dict, dry_run: bool) -> int:
    """``.ks`` 시나리오. 태그는 건드리지 않고 글자 조각만 갈아끼웁니다."""
    from .engines import tyrano

    text = target.read_text("utf-8")
    out = tyrano.patch(text, edits)
    if out == text:
        return 0
    if not dry_run:
        _backup(target, root)
        target.write_text(out, "utf-8")
    return len(edits)


def _patch_loose(target: Path, root: Path, edits: dict, loc: Location,
                 dry_run: bool) -> int:
    text = target.read_text("utf-8")
    out = containers.implode(text, loc.fmt, edits, target.name)
    if out == text:
        return 0
    if not dry_run:
        _backup(target, root)
        target.write_text(out, "utf-8")
    return len(edits)


def _load_unity(source, base_dir: Path):
    """UnityPy 환경을 **게임 폴더 기준**으로 엽니다.

    유니티 파일들은 서로를 참조합니다 — ``resources.assets`` 안의 스크립트가
    어떤 클래스인지 알려면 ``globalgamemanagers.assets`` 를 같이 봐야 합니다.
    UnityPy 는 그 짝을 ``환경의 path`` 에서 찾는데, 바이트를 직접 넘기면 path 가
    **현재 작업 폴더(=번역기가 설치된 곳)** 로 잡힙니다. 그러면 게임 파일을
    영영 못 찾습니다:

        FileNotFoundError: File globalgamemanagers.assets not found in
        (번역기가 설치된 폴더)

    그래서 언제나 파일이 놓인 폴더를 명시해서 엽니다.
    """
    import UnityPy

    return UnityPy.load(source, path=str(base_dir))


def _patch_unity(target: Path, root: Path, per_obj: dict, meta: dict,
                 rel: str, dry_run: bool, skipped: list | None = None,
                 enum_guard: bool = True) -> int:
    skip = prefixed.offset(target)
    head = b""
    if skip:
        with open(target, "rb") as fh:
            head = fh.read(skip)
            env = _load_unity(io.BytesIO(fh.read()), target.parent)
    else:
        env = _load_unity(str(target), target.parent)
    # 뽑을 때 타입 정보를 되살려 읽었다면, 되돌려 넣을 때도 똑같이 해야
    # 같은 오브젝트를 열 수 있습니다.
    typetree.attach(env, root, _unity_version(root))
    by_pid = _index_objects(env, per_obj)
    written = 0

    for path_id, edits in per_obj.items():
        obj = by_pid.get(path_id)
        if obj is None:
            raise KeyError(f"object {path_id} 를 찾을 수 없습니다")
        loc = meta[(rel, path_id)]

        if loc.kind == "unityraw":
            # 타입 정보를 못 읽는 오브젝트. 바이트의 그 자리만 갈아끼웁니다.
            data = obj.get_raw_data()
            by_off = {int(ptr): val for ptr, val in edits.items()}
            patched, moved = unityraw.patch_and_map(data, by_off)
            why = unityraw.layout_ok(data, patched, by_off)
            if why is not None:
                raise RuntimeError(
                    f"{rel}: path_id {path_id} 의 구조가 어긋나 건너뜁니다 "
                    f"({why}). 이 오브젝트는 바이트를 짐작해서 읽은 것이라, "
                    f"짐작이 틀리면 표 전체를 못 읽게 됩니다")
            bad = unityraw.missing(patched, by_off, moved)
            if bad:
                sample = " / ".join(b[:20] for b in bad[:3])
                raise RuntimeError(
                    f"{rel}: path_id {path_id} 에서 번역 {len(bad)}건이 "
                    f"들어가지 않았습니다 ({sample})")
            obj.set_raw_data(patched)
            written += len(edits)
            continue

        tree = obj.read_typetree()

        if loc.fmt == "typetree":
            # 예전에 뽑아 둔 목록에 **이름표**(Fungus 의 description, Live2D 의
            # ParameterIds …)가 섞여 있어도 여기서 막습니다. 다시 뽑지 않아도
            # 보호되어야 하고, 이름표를 번역해 넣으면 게임이 못 찾아 깨집니다.
            # ``edits`` 에서 빼야 뒤의 검증(_verify_unity)도 같은 목록을 봅니다.
            denials = heuristics.shape_denials(tree)
            # 표 모양 데이터의 이름표 열(id · cardType …)도 같은 방식으로 막습니다.
            # 예전 버전으로 뽑아 번역해 둔 목록에도 걸려야 합니다 — 이걸 번역해 넣으면
            # 게임이 시작하다 죽어 **무한 로딩** 이 됩니다.
            enums = (heuristics.enum_columns(tree) if enum_guard
                     else frozenset())
            bad = [p for p in edits
                   if heuristics.denied_by_shape(p, denials)
                   or heuristics.is_id_field(p)
                   or heuristics.denied_enum(p, enums)]
            if bad:
                for ptr in bad:
                    val = edits.pop(ptr)
                    if skipped is not None:
                        skipped.append(f"{str(val)[:40]}  ({ptr})")
                if not edits:
                    continue
            for ptr, val in edits.items():
                set_by_path(tree, ptr, val)
        else:
            blob = tree.get("m_Script")
            if isinstance(blob, (bytes, bytearray)):
                blob = blob.decode("utf-8")
            tree["m_Script"] = containers.implode(blob, loc.fmt, edits, loc.asset)
        written += len(edits)
        obj.save_typetree(tree)

    if written and not dry_run:
        data = _serialize(env)
        _verify_unity(data, per_obj, meta, rel, root)
        # 카탈로그가 이 번들을 다룰 수 있는지 **쓰기 전에** 봅니다. 쓴 뒤에 실패하면
        # 번들은 이미 바뀌었는데 카탈로그는 옛 크기 그대로 남습니다.
        _check_catalog(target, root)
        _backup(target, root)
        target.write_bytes(head + data)
        _retune_catalog(target, root)
    return written


# 이번 적용에서 '카탈로그 형식을 몰라 크기를 못 고친' 번들 수. apply() 가 비우고 읽습니다.
_CATALOG_UNKNOWN: list[str] = []


def _check_catalog(target: Path, root: Path) -> None:
    if not catalog.under_aa(target):
        return
    cat = catalog.is_addressables(root)
    if cat is None:
        return
    if catalog.status(cat, target.name) == "missing":
        raise KeyError(f"카탈로그에서 {target.name} 을 찾지 못했습니다")


def _retune_catalog(target: Path, root: Path) -> None:
    """Addressables 번들을 고쳤으면 **장부도 고칩니다.**

    ``StreamingAssets/aa/`` 게임은 번들마다 크기와 검사값을 catalog.bin 에
    따로 적어 둡니다. 번역을 넣으면 크기가 달라지는데 장부는 옛 크기를 들고
    있어서, 유니티가 그 번들을 못 쓰는 파일로 보고 거부합니다 — **게임이
    아예 안 켜집니다.**

    파일 자체는 멀쩡해서 우리 ``_verify_unity`` 를 그냥 통과합니다.
    어긋난 것이 파일이 아니라 **파일 밖의 장부**라서요. 진짜 게임에서
    번들을 지운 것과 번역을 넣은 것이 증상이 같아 잡았습니다.
    """
    if not catalog.under_aa(target):
        return
    cat = catalog.is_addressables(root)
    if cat is None:
        return
    if catalog.status(cat, target.name) == "unknown":
        _CATALOG_UNKNOWN.append(target.name)
        return
    _backup(cat, root)
    catalog.retune(cat, {target.name: target.stat().st_size})


def _verify_unity(data: bytes, per_obj: dict, meta: dict, rel: str,
                  root: Path) -> None:
    """새로 만든 바이트를 **다시 읽어서** 번역이 진짜 들어갔는지 확인합니다.

    유니티 파일은 다시 쓰는 과정에서 조용히 망가질 수 있는데, 망가진 파일을
    게임 폴더에 덮어쓰면 그때부터 게임이 안 켜집니다. 그래서 쓰기 **전에**
    확인하고, 하나라도 안 맞으면 예외를 던져 원본을 그대로 둡니다.
    """
    env = _load_unity(io.BytesIO(data), (root / rel).parent)
    typetree.attach(env, root, _unity_version(root))
    by_pid = _index_objects(env, per_obj)

    for path_id, edits in per_obj.items():
        obj = by_pid.get(path_id)
        if obj is None:
            raise RuntimeError(f"{rel}: 다시 읽으니 object {path_id} 가 사라졌습니다")
        loc = meta[(rel, path_id)]
        if loc.kind == "unityraw":
            # 다시 저장된 뒤에는 자리가 밀려 있으므로 오프셋으로는 못 봅니다.
            # 대신 바이트 안에 그 글자가 들어 있는지만 봅니다 — 훑기가 못 짚어서
            # 멀쩡한 번역을 되돌리는 일이 없도록.
            blob = obj.get_raw_data()
            for val in edits.values():
                if val.encode("utf-8") not in blob:
                    raise RuntimeError(
                        f"{rel}: 번역문이 저장되지 않았습니다 (원본을 그대로 둡니다)")
            continue
        tree = obj.read_typetree()
        if loc.fmt == "typetree":
            for ptr, val in edits.items():
                got = get_by_path(tree, ptr)
                if got != val:
                    raise RuntimeError(
                        f"{rel}: {ptr} 가 저장되지 않았습니다 (원본을 그대로 둡니다)")
        else:
            blob = tree.get("m_Script")
            if isinstance(blob, (bytes, bytearray)):
                blob = blob.decode("utf-8", "replace")
            for val in edits.values():
                if val and val not in (blob or "") and not (
                        loc.fmt in ("json", "jsonlit")
                        and json.dumps(val, ensure_ascii=False)[1:-1] in (blob or "")):
                    raise RuntimeError(
                        f"{rel}: 번역문이 저장되지 않았습니다 (원본을 그대로 둡니다)")


def _serialize(env) -> bytes:
    last = None
    for packer in ("original", "lz4", "none", None):
        try:
            return env.file.save(packer=packer)
        except (NotImplementedError, TypeError, ValueError) as e:
            last = e
    raise RuntimeError(f"파일을 다시 쓸 수 없습니다: {last}")


def _backup(target: Path, root: Path) -> None:
    dest = root / BACKUP_DIR / target.relative_to(root)
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, dest)


def _backup_root(root: Path) -> Path | None:
    """백업이 실제로 놓인 폴더.

    Electron 게임은 내용물이 ``resources/app/`` 안에 있어서 백업도 그 안에
    생깁니다. 사용자는 바깥 폴더를 넣으므로 한 겹 더 들어가 찾아봅니다.
    """
    root = Path(root)
    if (root / BACKUP_DIR).is_dir():
        return root
    for cand in (root / "resources" / "app", root / "www"):
        if (cand / BACKUP_DIR).is_dir():
            return cand
    hits = sorted(root.glob(f"*/{BACKUP_DIR}")) + \
        sorted(root.glob(f"*/*/{BACKUP_DIR}"))
    return hits[0].parent if hits else None


def restore(root: Path, progress: Progress = _noop, *, keep_state: bool = False) -> int:
    """백업으로 되돌립니다. ``keep_state`` 는 [다시 적용] 이 되돌렸다 곧바로 다시 쓸 때만 켭니다 —
    사용자가 [원래대로] 를 누른 것이면 기억해 둔 수정(폰트 등)도 함께 지웁니다."""
    found = _backup_root(root)
    if found is None:
        progress("백업 폴더가 없습니다. 되돌릴 것이 없습니다.")
        return 0
    root = found
    backup = root / BACKUP_DIR
    if not backup.is_dir():
        progress("백업 폴더가 없습니다. 되돌릴 것이 없습니다.")
        return 0
    n = 0
    # 새로 넣었던 파일(Ren'Py 후크 등)은 되돌릴 원본이 없으니 지웁니다.
    listing = backup / ADDED_LIST
    if listing.is_file():
        emptied: set[Path] = set()
        lines = listing.read_text("utf-8").splitlines()
        for rel in lines:
            rel = rel.strip()
            if not rel:
                continue
            for target in (root / rel, root / (rel + "c")):
                if target.is_file():
                    target.unlink()
                    emptied.add(target.parent)
                    n += 1
                    # 울프는 묶음에서 푼 파일 수백 개가 여기 들어옵니다. 줄마다 찍지 않습니다.
                    if len(lines) <= 50:
                        progress(f"  ✕ {rel}")
        if len(lines) > 50:
            progress(f"  ✕ 새로 넣었던 파일 {n}개")
        listing.unlink()
        # 새로 넣은 파일만 들어 있던 폴더(울프의 풀어 둔 Data/…)는 빈 채로 남기지 않습니다.
        for folder in sorted(emptied, key=lambda p: len(p.parts), reverse=True):
            while folder != root and root in folder.parents:
                try:
                    folder.rmdir()                 # 비어 있을 때만 지워집니다
                except OSError:
                    break
                folder = folder.parent

    for src in sorted(backup.rglob("*")):
        if src.is_file() and src.name != ADDED_LIST:
            dest = root / src.relative_to(backup)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            progress(f"  ← {dest.relative_to(root)}")
            n += 1
    if not keep_state:
        try:
            from . import fixups
            fixups.clear(root)                # 게임이 원본이 되었으니 기억할 수정도 없습니다
        except Exception:                               # noqa: BLE001
            pass
    return n
