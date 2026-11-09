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

"""로컬 웹 UI.

파이썬 표준 라이브러리 http.server 로 127.0.0.1 에만 서버를 띄우고 기본 브라우저를
엽니다. 외부 패키지도, 인터넷 연결도 필요 없습니다.

    python -m gameloc.webui

느린 작업(추출·번역·적용)은 백그라운드 스레드에서 돌리고, 화면은 /api/job 을
폴링해서 진행 상황을 보여줍니다.
"""

from __future__ import annotations

import json
import os
import platform
import secrets
import subprocess
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import asar, profile, protect, translate as mt
from .apply import apply as apply_translations
from .reapply import reapply as apply_again
from .apply import restore as restore_backup
from .detect import detect
from .extract import extract as run_extract
from .extract import extract_dll_add
from .locate import find as locate_find
from . import __version__
from .model import Project
from .sheet import (merge_into_project, read_workbook, unfinished_message,
                    write_workbook)

WEB_DIR = Path(__file__).parent / "web"
PROJECT_JSON = "project.json"
SHEET_XLSX = "translation.xlsx"

_FAVICON = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    b'<rect width="32" height="32" rx="7" fill="#6ea8fe"/>'
    b'<text x="16" y="23" font-size="20" font-family="sans-serif" font-weight="bold"'
    b' text-anchor="middle" fill="#0d1117">\xea\xb0\x80</text></svg>'
)

# 브라우저에서 도는 도구의 함정: 사용자가 방문한 **다른 웹사이트**가 몰래
# 127.0.0.1 로 요청을 보낼 수 있습니다. 그러면 남의 웹페이지가 이 도구에게
# "게임 파일을 덮어써라" 를 시킬 수 있게 됩니다.
#
# 그래서 두 가지를 봅니다.
#   · 실행할 때마다 새로 만드는 비밀 토큰. 화면 HTML 에만 박아 넣으므로
#     다른 사이트는 알 수 없습니다.
#   · Host 헤더가 진짜 localhost 인지. DNS 를 127.0.0.1 로 돌려놓고
#     자기 도메인으로 접근하는 수법(DNS rebinding)을 막습니다.
TOKEN = secrets.token_urlsafe(24)
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}


class State:
    """서버가 들고 있는 모든 것. 한 번에 프로젝트 하나만 다룹니다."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.project: Project | None = None
        self.workdir: Path | None = None
        self.game: Path | None = None
        self.job: dict = {"running": False, "done": True, "log": [], "error": None,
                          "title": "", "result": None}
        self.dirty = False
        # 방금 한 일괄 바꾸기. 되돌리기 한 번을 위해 들고 있습니다.
        self.last_bulk: tuple[str, dict[str, str]] | None = None
        # 통으로 옮기기: (언어, 문장 id 순서, 덩이들). 밖에서 갖다 붙이지
        # 말고 여기 적어 둡니다 — 없는 이름을 쓰는 것과 같은 일입니다.
        self.handoff: tuple | None = None
        # 크롬 내장 번역: 보낸 문장(제어문자 가리개 포함) · 실패해 건너뛸 문장 · 끝낸 문장.
        self.chrome: dict = {"pending": {}, "skip": set(), "done": set(), "seq": 0}
        threading.Thread(target=self._autosave, daemon=True).start()

    def save(self) -> None:
        with self.lock:
            if self.project and self.workdir:
                self.project.save(self.workdir / PROJECT_JSON)
                self.dirty = False

    def _autosave(self) -> None:
        while True:
            time.sleep(3)
            try:
                if self.dirty:
                    self.save()
            except Exception:
                pass

    def start_job(self, title: str, fn) -> None:
        with self.lock:
            if self.job["running"]:
                raise RuntimeError("이미 다른 작업이 돌아가고 있습니다.")
            self.job = {"running": True, "done": False, "log": [], "error": None,
                        "title": title, "result": None,
                        # 경과 시간·마지막 소식 시각(멈춘 것과 일하는 것을 구별하는 데 씁니다)
                        # 과 실시간 번역 보기 목록.
                        "started": time.time(), "elapsed": 0, "last_log": time.time(),
                        "preview": [], "ok": 0, "bad": 0}

        def runner():
            try:
                result = fn(self.log)
                with self.lock:
                    self.job["result"] = result
            except Exception as e:
                with self.lock:
                    self.job["error"] = f"{type(e).__name__}: {e}"
                self.log("".join(traceback.format_exc().splitlines(True)[-4:]).strip())
            finally:
                with self.lock:
                    self.job["running"] = False
                    self.job["done"] = True
                    self.job["elapsed"] = time.time() - self.job.get("started", time.time())

        threading.Thread(target=runner, daemon=True).start()

    def log(self, msg: str) -> None:
        with self.lock:
            self.job["log"].append(str(msg))
            del self.job["log"][:-400]
            self.job["last_log"] = time.time()

    def preview(self, src: str, out: str, problem: str) -> None:
        """자동번역 중 문장 하나가 끝날 때마다. 화면이 최신 것부터 보여 줍니다."""
        with self.lock:
            job = self.job
            job["ok" if not problem else "bad"] += 1
            n = job["ok"] + job["bad"]
            job["preview"].append({"n": n, "s": src[:400], "o": out[:400],
                                   "p": problem[:200]})
            del job["preview"][:-300]


STATE = State()


# --------------------------------------------------------------------------
# 폴더 선택 대화상자 (OS 기본)
# --------------------------------------------------------------------------
def pick_folder() -> str | None:
    system = platform.system()
    try:
        if system == "Windows":
            ps = ("Add-Type -AssemblyName System.Windows.Forms;"
                  "$f=New-Object System.Windows.Forms.FolderBrowserDialog;"
                  "$f.Description='게임 폴더를 고르세요';"
                  "if($f.ShowDialog() -eq 'OK'){[Console]::Out.Write($f.SelectedPath)}")
            out = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps],
                                 capture_output=True, text=True, timeout=300)
            return out.stdout.strip() or None
        if system == "Darwin":
            out = subprocess.run(
                ["osascript", "-e", 'POSIX path of (choose folder with prompt "게임 폴더")'],
                capture_output=True, text=True, timeout=300)
            return out.stdout.strip() or None
        out = subprocess.run(["zenity", "--file-selection", "--directory"],
                             capture_output=True, text=True, timeout=300)
        return out.stdout.strip() or None
    except Exception:
        return None


def pick_file(title: str = "번역 패치 고르기") -> str | None:
    """번역 패치(.zip) 하나를 고르는 창. 못 띄우면 ``None``."""
    system = platform.system()
    try:
        if system == "Windows":
            ps = ("Add-Type -AssemblyName System.Windows.Forms;"
                  "$f=New-Object System.Windows.Forms.OpenFileDialog;"
                  f"$f.Title='{title}';"
                  "$f.Filter='번역 패치 (*.zip)|*.zip|모든 파일 (*.*)|*.*';"
                  "if($f.ShowDialog() -eq 'OK'){[Console]::Out.Write($f.FileName)}")
            out = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps],
                                 capture_output=True, text=True, timeout=300)
            return out.stdout.strip() or None
        if system == "Darwin":
            out = subprocess.run(
                ["osascript", "-e", f'POSIX path of (choose file with prompt "{title}")'],
                capture_output=True, text=True, timeout=300)
            return out.stdout.strip() or None
        out = subprocess.run(["zenity", "--file-selection"], capture_output=True,
                             text=True, timeout=300)
        return out.stdout.strip() or None
    except Exception:
        return None


def open_in_explorer(path: Path) -> None:
    try:
        system = platform.system()
        if system == "Windows":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif system == "Darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
def _engine_label(p: Project) -> str:
    if p.engine == "rpgmaker":
        return f"RPG Maker {p.unity_version}".strip()
    if p.engine == "unity":
        return f"Unity {p.unity_version}".strip()
    if p.engine == "renpy":
        return f"Ren'Py {p.unity_version}".strip()
    if p.engine == "tyrano":
        return f"TyranoScript {p.unity_version}".strip()
    if p.engine == "wolf":
        return "WOLF RPG 에디터"
    return p.engine


def api_state() -> dict:
    p = STATE.project
    d = {
        "game": str(STATE.game) if STATE.game else None,
        "workdir": str(STATE.workdir) if STATE.workdir else None,
        "hasProject": p is not None,
        "job": {k: v for k, v in STATE.job.items() if k != "result"},
    }
    if p:
        d.update({
            "engine": p.engine,
            "engineLabel": _engine_label(p),
            "langs": p.languages,
            "total": len(p.entries),
            "counts": {lg: sum(1 for e in p.entries if e.translations.get(lg))
                       for lg in p.languages},
            "stats": p.stats,
        })
    return d


def api_unpack(path: str) -> dict:
    """[Electron] app.asar 를 풀어 번역 가능한 상태로 만듭니다."""
    info = detect(Path(path))
    if not info.electron_asar:
        raise RuntimeError("이 폴더에서 app.asar 를 찾지 못했습니다.")
    n, dest = asar.open_up(info.electron_asar)
    after = detect(Path(path))
    return {"files": n, "dest": str(dest), **api_scan(path),
            "message": f"{n}개 파일을 풀었습니다. 안쪽 엔진: {after.summary()}"}


def api_repack(path: str) -> dict:
    return {"ok": asar.close_up(Path(path))}


def api_peek(path: str) -> dict:
    """[유니티·Ren’Py] 파일 안을 들여다본 보고서를 만들어 저장합니다. 읽기만 합니다."""
    from .peek import report

    game = Path(path).expanduser().resolve()
    text = report(game)
    dest = game.parent / f"{game.name}_gameloc_진단.txt"
    dest.write_text(text, "utf-8")
    return {"file": str(dest), "text": text}


def api_fonts() -> dict:
    """이 컴퓨터에 깔린 한글 글꼴 목록."""
    from .fontlist import scan

    fonts = scan()
    return {"fonts": [f.to_dict() for f in fonts]}


def api_font(body: dict) -> None:
    """[유니티·Ren’Py] □□□ 고치기. 백그라운드로 돌립니다."""
    game = Path(body["path"]).expanduser().resolve()
    ttf = (body.get("ttf") or "").strip()
    dry = bool(body.get("dry"))

    def job(log):
        from .font import fix
        rep = fix(game, ttf=Path(ttf) if ttf else None, dry_run=dry,
                  unfreeze=bool(body.get("unfreeze")),
                  os_font=bool(body.get("os_font")), progress=log)
        log("")
        log(rep.summary())
        for n in rep.notes:
            log("  · " + n)
        if rep.changed_files:
            log("")
            log("게임을 실행해 한글이 나오는지 확인하세요.")
            log("이상하면 [원래대로]를 누르면 됩니다.")
        return {"patched": rep.patched}

    STATE.start_job("폰트 고치는 중", job)


def api_renpy_pieces() -> dict:
    """Ren'Py 게임 안의 대본 후보 목록. 무엇이 왜 안 읽히는지까지."""
    from .engines import renpy

    game = STATE.game
    if not game:
        raise RuntimeError("게임 폴더를 먼저 고르세요.")
    found = renpy.is_renpy(Path(game))
    if found is None:
        raise RuntimeError("이 게임은 Ren'Py 가 아닙니다.")
    return {"pieces": [x.to_dict() for x in renpy.survey(found)]}


def api_renpy_unpack(body: dict) -> dict:
    """묶음(.rpa)을 작업 폴더에 풀어 놓습니다. 게임 폴더는 안 건드립니다."""
    from .engines import renpy

    game = STATE.game
    if not game:
        raise RuntimeError("게임 폴더를 먼저 고르세요.")
    found = renpy.is_renpy(Path(game))
    if found is None:
        raise RuntimeError("이 게임은 Ren'Py 가 아닙니다.")
    which = [str(x) for x in (body.get("which") or [])]
    if not which:
        raise RuntimeError("풀 묶음을 고르세요.")

    def job(log):
        out = (STATE.workdir or Path(game)) / "rpa"
        n = 0
        for rel in which:
            arc = found / rel
            if not arc.is_file():
                log(f"  · 없습니다: {rel}")
                continue
            log(f"{rel} 을 풉니다…")
            try:
                got = renpy.unpack_rpa(arc, out / Path(rel).stem, progress=log)
            except Exception as exc:                    # noqa: BLE001
                log(f"  · 못 풀었습니다: {renpy._rpa_why(arc, exc)}")
                continue
            n += len(got)
        log("")
        log(f"모두 {n}개를 풀었습니다.")
        log(f"자리: {out}")
        log("안을 열어 보시고, 쓸 만하면 [문장 뽑기] 를 다시 누르세요.")
        return {"files": n, "dest": str(out)}

    STATE.start_job("묶음 풀기", job)
    return {"ok": True}


def api_scan(path: str) -> dict:
    info = detect(Path(path))
    try:
        prof = profile.classify(info).to_dict()
    except Exception:                                   # noqa: BLE001
        prof = None          # 유형 판별이 실패해도 스캔 자체는 그대로 돌아가야 합니다
    return {"summary": info.summary(), "engine": info.engine, "profile": prof,
            "packed": info.engine == "electron-packed",
            "supported": info.engine in ("unity", "rpgmaker", "renpy", "tyrano", "wolf"),
            "safeMode": info.engine == "renpy",
            "files": [str(f.relative_to(info.root)) for f in info.asset_files[:40]],
            "fileCount": len(info.asset_files),
            # 엔진을 모를 때는 이쪽이 유일한 단서라 목록을 같이 보냅니다
            "looseFiles": [str(f.relative_to(info.root))
                           for f in info.loose_files[:40]],
            "looseCount": len(info.loose_files)}


def _carry_over(proj: Project, workdir: Path, log) -> None:
    """이미 번역해 둔 게 있으면 이어받습니다."""
    old = workdir / PROJECT_JSON
    if not old.exists():
        return
    try:
        from . import rm_defaults
        prev_entries = {e.text: e for e in Project.load(old).entries}
        carried = 0
        for e in proj.entries:
            old_e = prev_entries.get(e.text)
            if old_e is not None and old_e.translations:
                for lang, tr in old_e.translations.items():
                    # 사전으로 미리 채운 기본 용어를 예전 **기계번역**이 덮어쓰지 않게 합니다.
                    if e.translations.get(lang) and (lang in old_e.machine or e.text in rm_defaults.KO):
                        continue
                    e.translations[lang] = tr
                    if lang in old_e.machine and lang not in e.machine:
                        e.machine.append(lang)
                carried += 1
        if carried:
            log(f"이전 번역 {carried}건을 이어받았습니다.")
    except Exception:
        pass


def api_extract(body: dict) -> None:
    game = Path(body["path"]).expanduser().resolve()
    langs = [x.strip() for x in (body.get("langs") or "ko").split(",") if x.strip()]
    workdir = game.parent / f"{game.name}_loc"

    def job(log):
        workdir.mkdir(parents=True, exist_ok=True)
        proj = run_extract(
            game,
            filter_level=body.get("filter", "normal"),
            dedup=not body.get("noDedup", False),
            languages=langs,
            include_scenes=not bool(body.get("noScenes")),
            source_lang=body.get("srcLang", "all"),
            include_notes=bool(body.get("notes")),
            include_js=bool(body.get("js")),
            include_dll=bool(body.get("dll")),
            all_plugin_params=bool(body.get("allParams")),
            include_system=bool(body.get("system")),
            pick=body.get("pick") or None,
            progress=log,
        )
        _carry_over(proj, workdir, log)
        proj.save(workdir / PROJECT_JSON)
        with STATE.lock:
            STATE.project, STATE.workdir, STATE.game = proj, workdir, Path(proj.game_root)
        log(f"고유 문자열 {len(proj.entries)}개")
        return {"total": len(proj.entries)}

    STATE.start_job("문자열 뽑는 중", job)


def api_extract_dll_add(body: dict) -> None:
    """이미 연 프로젝트에 **DLL 글자만 추가로** 뽑아 얹습니다.

    번역 화면(3단계)에서 바로 씁니다 — "문장 뽑기"를 다시 눌러 전체를 다시 만드는
    대신, 씬·에셋은 하나도 안 건드리고 DLL 만 훑어 새 항목만 더합니다. 같은 글이
    이미 있으면(다른 곳에서 이미 뽑힌 글과 원문이 같으면) 그 항목에 위치만
    보태고, 번역·검토 상태는 그대로 둡니다.
    """
    p, game = STATE.project, STATE.game
    if not p or not game:
        raise RuntimeError("먼저 문자열을 뽑으세요.")

    def job(log):
        added = extract_dll_add(game, filter_level=body.get("filter", "normal"),
                                progress=log)
        new_entries = 0
        new_locations = 0
        with STATE.lock:
            by_id = {e.id: e for e in p.entries}
            for ne in added:
                cur = by_id.get(ne.id)
                if cur is None:
                    p.entries.append(ne)
                    by_id[ne.id] = ne
                    new_entries += 1
                else:
                    for loc in ne.locations:
                        if loc not in cur.locations:
                            cur.locations.append(loc)
                            new_locations += 1
            p.save(STATE.workdir / PROJECT_JSON)
        log(f"새 항목 {new_entries}개, 기존 항목에 자리 추가 {new_locations}곳")
        return {"newEntries": new_entries, "newLocations": new_locations,
                "total": len(p.entries)}

    STATE.start_job("DLL 글자 추가로 뽑는 중", job)


def api_open(path: str, must_exist: bool = True) -> dict:
    game = Path(path).expanduser().resolve()
    workdir = game.parent / f"{game.name}_loc"
    pj = workdir / PROJECT_JSON
    if not pj.exists():
        if must_exist:
            raise FileNotFoundError("이 게임 폴더로 만든 작업 폴더가 없습니다.")
        return {"exists": False}
    proj = Project.load(pj)
    with STATE.lock:
        STATE.project, STATE.workdir, STATE.game = proj, workdir, Path(proj.game_root)
    return {"exists": True, **api_state()}


def api_files(lang: str) -> list[dict]:
    """파일별 총/미번역 개수. 맵 하나씩 끝내는 방식으로 작업하려는 사람용."""
    p = STATE.project
    if not p:
        return []
    total: dict[str, int] = {}
    todo: dict[str, int] = {}
    for e in p.entries:
        done = bool(e.translations.get(lang))
        for f in {l.file for l in e.locations}:
            total[f] = total.get(f, 0) + 1
            if not done:
                todo[f] = todo.get(f, 0) + 1
    return [{"file": f, "total": n, "todo": todo.get(f, 0)} for f, n in total.items()]


def api_entries(q: dict) -> dict:
    p = STATE.project
    if not p:
        return {"rows": [], "total": 0, "filtered": 0, "files": [], "done": 0}
    lang = (q.get("lang") or [p.languages[0]])[0]
    mode = (q.get("mode") or ["all"])[0]
    term = (q.get("q") or [""])[0].strip().lower()
    only_file = (q.get("file") or [""])[0]
    offset = int((q.get("offset") or ["0"])[0])
    limit = min(int((q.get("limit") or ["200"])[0]), 2000)

    rows = []
    for e in p.entries:
        tr = e.translations.get(lang, "")
        if mode == "todo" and tr:
            continue
        if mode == "done" and not tr:
            continue
        if mode == "mt" and lang not in e.machine:
            continue
        if term and term not in e.text.lower() and term not in tr.lower():
            continue
        if only_file and not any(l.file == only_file for l in e.locations):
            continue
        rows.append(e)

    def _max_chars(e):
        # DLL 안에 박힌 글자는 자리가 고정입니다 — 그 자리들 중 가장 좁은 것을
        # 넘으면 어디서도 못 들어갑니다. dll 위치가 없으면(대부분) None.
        caps = [l.max_chars for l in e.locations
                if l.kind == "dll" and l.max_chars is not None]
        return min(caps) if caps else None

    page = [{
        "id": e.id,
        "text": e.text,
        "tr": e.translations.get(lang, ""),
        "count": e.count,
        "where": e.locations[0].label() if e.locations else "",
        "lines": len(e.locations[0].span) if (e.locations and e.locations[0].span) else 1,
        "mt": lang in e.machine,
        "maxChars": _max_chars(e),
    } for e in rows[offset:offset + limit]]

    return {"rows": page, "total": len(p.entries), "filtered": len(rows),
            "offset": offset, "files": api_files(lang), "langs": p.languages,
            # 진행률까지 함께 돌려줍니다. 목록을 새로 그릴 때마다 /api/state 를
            # 또 부르면 느려지고, 그 사이 값이 어긋납니다.
            "done": sum(1 for e in p.entries if e.translations.get(lang))}


def api_save_entry(body: dict) -> dict:
    p = STATE.project
    if not p:
        raise RuntimeError("먼저 문자열을 뽑으세요.")
    lang, eid, text = body["lang"], body["id"], body.get("text", "")
    for e in p.entries:
        if e.id == eid:
            if text:
                e.translations[lang] = text
            else:
                e.translations.pop(lang, None)
            if lang in e.machine:      # 사람이 확인했으므로 검토 대기 해제
                e.machine.remove(lang)
            STATE.dirty = True
            return {"ok": True,
                    "done": sum(1 for x in p.entries if x.translations.get(lang))}
    raise KeyError("그런 항목이 없습니다.")


def _bulk_plan(body: dict):
    from . import bulk

    p = STATE.project
    if not p:
        raise RuntimeError("먼저 문자열을 뽑으세요.")
    return p, bulk.plan(
        p.entries, body.get("lang", "ko"),
        source_has=(body.get("source") or "").strip(),
        find=(body.get("find") or "").strip(),
        replace=body.get("replace") or "",
        whole_word=bool(body.get("wholeWord")),
        case_sensitive=bool(body.get("caseSensitive")),
        josa=body.get("josa", True) is not False,
    )


def api_bulk_preview(body: dict) -> dict:
    """무엇이 바뀔지만 보여줍니다. 아무것도 안 고칩니다."""
    _p, plan = _bulk_plan(body)
    return {
        "count": len(plan.changes),
        "hits": sum(c.hits for c in plan.changes),
        "summary": plan.summary(),
        "note": plan.note,
        "samples": [{"id": c.id, "source": c.source,
                     "before": c.before, "after": c.after}
                    for c in plan.changes[:50]],
    }


def api_bulk_apply(body: dict) -> dict:
    from . import bulk

    p, plan = _bulk_plan(body)
    lang = body.get("lang", "ko")
    only = body.get("only")
    snapshot = bulk.apply(p.entries, lang, plan.changes,
                          only=set(only) if only else None)
    if snapshot:
        STATE.last_bulk = (lang, snapshot)
        STATE.dirty = True
        STATE.save()
    return {"changed": len(snapshot),
            "summary": f"{len(snapshot)}줄을 바꿨습니다.",
            "done": sum(1 for e in p.entries if e.translations.get(lang))}


def api_bulk_undo() -> dict:
    from . import bulk

    p = STATE.project
    if not p or not STATE.last_bulk:
        return {"restored": 0, "summary": "되돌릴 일괄 바꾸기가 없습니다."}
    lang, snapshot = STATE.last_bulk
    n = bulk.undo(p.entries, lang, snapshot)
    STATE.last_bulk = None
    STATE.dirty = True
    STATE.save()
    return {"restored": n, "summary": f"{n}줄을 되돌렸습니다.",
            "done": sum(1 for e in p.entries if e.translations.get(lang))}


def api_add_lang(lang: str) -> dict:
    p = STATE.project
    if not p:
        raise RuntimeError("먼저 문자열을 뽑으세요.")
    if lang and lang not in p.languages:
        p.languages.append(lang)
        STATE.dirty = True
    return api_state()


def api_providers() -> dict:
    cfg = mt.load_settings()
    return {"providers": mt.provider_list(),
            "saved": {k: bool(v) for k, v in (cfg.get("keys") or {}).items()},
            "last": cfg.get("last", {})}


def api_autotranslate(body: dict) -> None:
    p = STATE.project
    if not p:
        raise RuntimeError("먼저 문자열을 뽑으세요.")
    pid = body.get("provider", "google")
    lang = body.get("lang") or p.languages[0]
    scope = body.get("scope", "todo")
    src = body.get("src", "auto")
    key = (body.get("key") or "").strip()

    cfg = mt.load_settings()
    keys = cfg.setdefault("keys", {})
    if key:
        if body.get("remember"):
            keys[pid] = key
    else:
        key = keys.get(pid, "")
    cfg["last"] = {"provider": pid, "src": src, "speed": body.get("speed", ""),
                   "base": body.get("base", ""), "model": body.get("model", ""),
                   "glossary": body.get("glossary", ""),
                   "prompt": body.get("prompt", "")}
    mt.save_settings(cfg)

    # 여러 곳에 나눠 보내기. 비어 있으면 예전처럼 한 곳만 씁니다.
    from . import relay as rl

    legs: list = []
    for row in (body.get("legs") or []):
        lid = str(row.get("provider") or "").strip()
        if not lid:
            continue
        legs.append(rl.Leg(lid, str(row.get("key") or "") or keys.get(lid, ""),
                           str(row.get("model") or ""),
                           str(row.get("base") or "")))
    mode = str(body.get("mode") or "relay")

    provider = None if legs else mt.make_provider(
        pid, key, body.get("model", ""), str(body.get("base") or ""))
    glossary = protect.parse_glossary(body.get("glossary", ""))

    if scope == "all":
        targets = list(p.entries)
    elif scope == "ids":
        want = set(body.get("ids") or [])
        targets = [e for e in p.entries if e.id in want]
    else:
        targets = [e for e in p.entries if not e.translations.get(lang)]

    limit = int(body.get("limit") or 0)
    if limit > 0:
        targets = targets[:limit]

    def job(log):
        if legs:
            how = "동시에 보내기" if mode == "spread" else "이어달리기"
            log(f"{how} — {len(legs)}곳  ·  대상 {len(targets)}개  ·  {src} → {lang}")
        else:
            log(f"엔진: {provider.label}  ·  대상 {len(targets)}개  ·  {src} → {lang}")
        if glossary:
            log(f"용어집 {len(glossary)}개 적용")
        prompt = str(body.get("prompt") or "").strip()
        if prompt and not legs:
            log("추가 지시문 적용 (말로 시키는 번역기만 알아듣습니다)")
        common = dict(lang=lang, src=src, glossary=glossary,
                      engine=(p.engine if p else ""),
                      speed=str(body.get("speed") or ""), progress=log,
                      should_stop=lambda: STATE.job.get("cancel", False))
        mt.set_item_hook(STATE.preview)
        try:
            if legs:
                # 여러 곳에 나눠 보내기(이어달리기/동시에)는 아직 추가 지시문을
                # 안 받습니다 — relay.py 쪽 신호가 하나 더 필요해서 다음에.
                rep = rl.run(targets, legs, mode=mode, **common)
            else:
                rep = mt.translate_entries(
                    targets, provider, src=src, dst=lang, glossary=glossary,
                    lang_col=lang, engine=(p.engine if p else ""),
                    speed=str(body.get("speed") or ""),
                    extra_instruction=prompt, progress=log,
                    should_stop=lambda: STATE.job.get("cancel", False))
        finally:
            mt.set_item_hook(None)
        # 묶어 보내기가 통했는지는 사람이 알 길이 없습니다. 시험 중인
        # 기능이라 결과를 남겨 두어야 판단할 수 있습니다.
        if provider is not None and getattr(provider, "batched", None) is False:
            log("  · 이 서버가 묶어 보내기를 안 받아 하나씩 보냈습니다 "
                "(느리지만 결과는 같습니다).")
        STATE.dirty = True
        STATE.save()
        log("")
        log(rep.summary())
        # 어느 곳이 얼마나 했는지. 한도가 어디서 끝났는지 알아야
        # 다음에 계획을 짤 수 있습니다.
        for line in getattr(rep, "plain", list)():
            log(line)
        for pr in rep.problems[:20]:
            log("  ! " + pr)
        if rep.failed:
            log("실패한 항목은 원문 그대로 두었습니다. 손으로 채우세요.")
        return {"done": rep.done, "failed": rep.failed, "skipped": rep.skipped}

    STATE.start_job("자동 번역", job)


# ── 크롬 내장 번역 ─────────────────────────────────────────────────────────
#
# 크롬(138 이상)에는 웹페이지가 직접 부를 수 있는 **기기 안 번역기**(Translator API)가
# 있습니다. 키도 결제도 속도 제한도 없습니다. 번역은 브라우저가 하고, 이쪽 서버는
# 문장을 골라 주고(제어문자 가리기) 결과를 받아 검사해서 넣기만 합니다. 서버가 인터넷
# 번역 서비스를 부르는 것이 아니라서 ``translate_entries`` 를 거치지 않습니다.

def _chrome_state() -> dict:
    return STATE.chrome


def api_chrome_next(body: dict) -> dict:
    p = STATE.project
    if not p:
        raise RuntimeError("먼저 문자열을 뽑으세요.")
    lang = body.get("lang") or p.languages[0]
    scope = body.get("scope", "todo")
    size = max(1, min(int(body.get("size") or 40), 100))
    cap = int(body.get("cap") or 0)               # 이번에 더 받을 수 있는 최대 개수(0 = 제한 없음)
    if scope == "ids":
        want = set(body.get("ids") or [])
        targets = [e for e in p.entries if e.id in want]
    elif scope == "all":
        targets = list(p.entries)
    else:
        targets = [e for e in p.entries if not e.translations.get(lang)]
    with STATE.lock:
        st = _chrome_state()
        if body.get("reset"):
            st["pending"].clear(); st["skip"].clear(); st["done"].clear()
        uniq: list[str] = []
        seen: set[str] = set()
        for e in targets:
            t = e.text
            if t in seen or t in st["skip"] or t in st["done"]:
                continue
            seen.add(t)
            uniq.append(t)
        left = len(uniq)
        take = uniq[:min(size, cap) if cap > 0 else size]
        items = []
        for t in take:
            masked, codes = protect.mask(t, engine=p.engine)
            st["seq"] += 1
            k = str(st["seq"])
            st["pending"][k] = (t, codes)
            items.append({"k": k, "t": masked})
    return {"items": items, "left": left}


def api_chrome_put(body: dict) -> dict:
    p = STATE.project
    if not p:
        raise RuntimeError("먼저 문자열을 뽑으세요.")
    lang = body.get("lang") or p.languages[0]
    scope = body.get("scope", "todo")
    glossary = protect.parse_glossary(body.get("glossary", ""))
    ok = bad = 0
    shown: list[dict] = []
    with STATE.lock:
        st = _chrome_state()
        index: dict[str, list] = {}
        for e in p.entries:
            index.setdefault(e.text, []).append(e)
        for it in body.get("items") or []:
            t, codes = st["pending"].pop(str(it.get("k")), (None, None))
            if t is None:
                continue
            out = str(it.get("o") or "")
            problem = str(it.get("err") or "")
            final = ""
            if not problem and not out.strip():
                problem = "번역 결과가 비어 있습니다"
            if not problem:
                final, problem = protect.roundtrip(t, out, codes)
                problem = problem or ""
            if not problem:
                final = protect.apply_glossary(final, glossary)
                if final.strip() == t.strip():
                    problem = "원문과 같아 넣지 않았습니다"
            if problem:
                st["skip"].add(t)
                bad += 1
                shown.append({"s": t[:400], "o": "", "p": problem[:200]})
                continue
            for e in index.get(t, []):
                if scope != "all" and e.translations.get(lang):
                    continue
                e.translations[lang] = final
                if lang not in e.machine:
                    e.machine.append(lang)
            st["done"].add(t)
            ok += 1
            shown.append({"s": t[:400], "o": final[:400], "p": ""})
        STATE.dirty = True
    return {"ok": ok, "bad": bad, "shown": shown}


# ── 반자동 번역: 통으로 뽑아 붙여넣고 통으로 되받기 ─────────────────────

def _handoff_project():
    """이 화면이 쓰는 프로젝트.

    처음에 ``STATE.need()`` 라고 썼는데 **그런 함수는 없습니다.** 제가
    지어냈고, 시험이 ``raising=False`` 로 그 이름을 만들어 붙여서 없는
    것을 있다고 믿고 통과했습니다. 화면에서만 터졌습니다.
    """
    p = STATE.project
    if not p:
        raise RuntimeError("먼저 문자열을 뽑으세요.")
    return p


def _handoff_targets(lang: str, scope: str) -> list:
    """무엇을 내보낼까. 기본은 **아직 안 된 것만** 입니다 — 다 된 것을
    다시 붙여넣게 하면 15,000개가 40번이 아니라 80번이 됩니다."""
    p = _handoff_project()
    rows = [e for e in p.entries if (e.text or "").strip()]
    if scope != "all":
        rows = [e for e in rows if not (e.translations.get(lang) or "").strip()]
    return rows


def api_handoff_plan(body: dict) -> dict:
    from . import handoff

    lang = body.get("lang") or _handoff_project().languages[0]
    rows = _handoff_targets(lang, body.get("scope") or "todo")
    chunks = handoff.split([e.text for e in rows],
                           chars=int(body.get("chars") or handoff.CHARS))
    STATE.handoff = (lang, [e.id for e in rows], chunks)
    return {
        "total": len(rows),
        "chunks": [{"i": i, "label": c.label(), "first": c.first,
                    "count": len(c.lines)} for i, c in enumerate(chunks)],
        "note": (f"{len(rows):,}개를 {len(chunks)}번에 나눠 옮깁니다. "
                 "번역기에 붙여넣고, 결과를 그대로 다시 가져오세요."),
    }


def _handoff_ready():
    got = STATE.handoff
    if not got:
        raise RuntimeError("먼저 [내보낼 것 준비] 를 누르세요.")
    return got


def api_handoff_text(body: dict) -> dict:
    _lang, _ids, chunks = _handoff_ready()
    i = int(body.get("i") or 0)
    if not 0 <= i < len(chunks):
        raise RuntimeError("그 덩이가 없습니다.")
    return {"text": chunks[i].text(), "label": chunks[i].label()}


def api_handoff_preview(body: dict) -> dict:
    """**넣기 전에** 짝을 보여 줍니다. 이게 이 기능의 안전장치입니다."""
    from . import handoff

    _lang, _ids, chunks = _handoff_ready()
    i = int(body.get("i") or 0)
    if not 0 <= i < len(chunks):
        raise RuntimeError("그 덩이가 없습니다.")
    landing = handoff.land(chunks[i], body.get("pasted") or "")
    return {
        "report": landing.report(),
        "numbered": landing.numbered,
        "good": len(landing.good),
        "bad": len(landing.bad),
        "pairs": [{"n": p.n, "before": p.before, "after": p.after,
                   "ok": p.ok, "why": p.why} for p in landing.pairs],
    }


def api_handoff_apply(body: dict) -> dict:
    from . import handoff

    lang, ids, chunks = _handoff_ready()
    i = int(body.get("i") or 0)
    if not 0 <= i < len(chunks):
        raise RuntimeError("그 덩이가 없습니다.")
    landing = handoff.land(chunks[i], body.get("pasted") or "")

    p = _handoff_project()
    by_id = {e.id: e for e in p.entries}
    put = 0
    for pair in landing.good:
        pos = pair.n - 1                    # 번호는 1부터입니다
        if not 0 <= pos < len(ids):
            continue
        entry = by_id.get(ids[pos])
        if entry is None or entry.text != pair.before:
            continue                        # 그 사이에 바뀌었으면 안 건드립니다
        entry.translations[lang] = pair.after
        put += 1
    if put:
        STATE.dirty = True
        STATE.save()
    return {"put": put,
            "summary": f"{put}개를 넣었습니다." if put else "한 개도 못 넣었습니다.",
            "done": sum(1 for e in p.entries if e.translations.get(lang))}



def api_locale_fill(body: dict) -> dict:
    """비어 있는 언어 표를 우리 번역으로 채웁니다 (유니티 Localization).

    게임이 컴퓨터 언어를 보고 **한국어 표를 고르는데 그 표가 비어 있는**
    게임이 있습니다. 그때는 일본어 표를 고쳐 봐야 소용이 없습니다 — 게임이
    안 봅니다. 게임이 실제로 보는 표를 채워야 합니다.
    """
    from . import localefill

    p = _handoff_project()
    if not STATE.game:
        raise RuntimeError("먼저 게임 폴더를 넣으세요.")
    lang = body.get("lang") or p.languages[0]
    src = (body.get("src") or "ja").strip().lower()

    by_text = {}
    for e in p.entries:
        got = (e.translations.get(lang) or "").strip()
        if got:
            by_text.setdefault(e.text, got)

    rep = localefill.fill(
        Path(STATE.game), src, lang.lower(), by_text.get,
        unity_version=p.unity_version or "",
        dry_run=bool(body.get("dry_run")))
    return {"filled": rep.filled, "kept": rep.kept, "missing": rep.missing,
            "was": rep.was, "now": rep.now, "tables": rep.tables,
            "report": rep.plain()}


def api_locale_check() -> dict:
    """이 게임에 '비어 있는 언어 표' 문제가 있나."""
    from . import localefill

    if not STATE.game:
        return {"addressables": False, "locales": {}}
    found = localefill.locale_bundles(Path(STATE.game))
    return {"addressables": bool(found),
            "locales": {k: v.stat().st_size for k, v in found.items()}}


def api_find(body: dict) -> dict:
    root = Path(body.get("path") or (STATE.game or ""))
    if not root or not Path(root).is_dir():
        raise RuntimeError("게임 폴더를 먼저 고르세요.")
    rep = locate_find(Path(root), body.get("q", ""), STATE.project)
    return {"verdict": rep.verdict(), "scanned": rep.scanned,
            "inProject": rep.in_project, "translated": rep.translated,
            "projectText": rep.project_text,
            "hits": [{"file": h.file, "kind": h.kind, "covered": h.covered,
                      "reason": h.reason, "where": h.where, "raw": h.raw,
                      "how": h.how} for h in rep.hits]}


def api_apply(body: dict) -> None:
    p, game = STATE.project, STATE.game
    if not p:
        raise RuntimeError("먼저 문자열을 뽑으세요.")
    lang = body.get("lang") or p.languages[0]
    dry = bool(body.get("dry"))
    STATE.save()

    # 덧씌우기는 원본을 안 건드리는 두 번째 길입니다. A 가 실패했을 때만
    # 화면에 보입니다.
    how = str(body.get("how") or "direct")

    def job(log):
        if how == "overlay":
            from .apply import apply_overlay

            log("덧씌우기 — 게임 파일은 그대로 두고 사전을 얹습니다.")
            rep = apply_overlay(p, lang, game_root=game, dry_run=dry,
                                progress=log)
        else:
            rep = apply_again(p, lang, game, dry_run=dry, progress=log)
        log("")
        if dry:
            log("[미리보기] 파일은 아직 안 건드렸습니다.")
        # 맨 위에 결론, 같은 종류는 묶어서 한 번만, 경고마다 '그래서' 한 줄.
        for line in rep.report():
            log(line)
        return {"files": rep.files_written, "strings": rep.strings_written,
                "failures": rep.failures, "risky": rep.risky, "dry": dry}

    STATE.start_job("미리보기" if dry else "게임에 적용", job)


def api_patch_save() -> None:
    """적용된 게임의 변경분을 번역 패치 파일 하나로 저장합니다."""
    game, work = STATE.game, STATE.workdir
    if not game:
        raise RuntimeError("게임 폴더를 먼저 고르세요.")

    def job(log):
        from . import patchpack

        out = (Path(work) if work else Path(game).parent) / (Path(game).name + patchpack.SUFFIX)
        try:
            out = patchpack.export_patch(Path(game), out, progress=log)
        except patchpack.PatchError as exc:
            log(str(exc))
            raise RuntimeError("패치를 만들지 못했습니다 (까닭은 위 글)") from None
        log("")
        log(f"저장 위치: {out}")
        log("※ " + patchpack.NOTE)
        open_in_explorer(out.parent)
        return {"file": str(out)}

    STATE.start_job("번역 패치 만들기", job)


def api_patch_overlay() -> None:
    """적용된 게임의 변경분을 게임 폴더에 그냥 복사해 덮어쓸 폴더로 내보냅니다."""
    game, work = STATE.game, STATE.workdir
    if not game:
        raise RuntimeError("게임 폴더를 먼저 고르세요.")

    def job(log):
        from . import patchpack

        out = (Path(work) if work else Path(game).parent) / (Path(game).name + patchpack.OVERLAY_SUFFIX)
        try:
            out = patchpack.export_overlay(Path(game), out, progress=log)
        except patchpack.PatchError as exc:
            log(str(exc))
            raise RuntimeError("폴더를 만들지 못했습니다 (까닭은 위 글)") from None
        log("")
        log(f"저장 위치: {out}")
        log("이 폴더 안의 내용을 같은 게임의 원본 폴더에 복사해 덮어쓰면 됩니다. 자세한 순서는 "
            f"폴더 안의 {patchpack.OVERLAY_README} 에 있습니다.")
        log("※ " + patchpack.NOTE)
        open_in_explorer(out)
        return {"folder": str(out)}

    STATE.start_job("덮어쓰기용 폴더 만들기", job)


def api_patch_apply(body: dict) -> None:
    """저장해 둔 번역 패치를 이 게임 폴더에 넣습니다."""
    game = Path(body["path"]).expanduser().resolve()
    patch = Path(body["patch"]).expanduser()
    dry = bool(body.get("dry"))

    def job(log):
        from . import patchpack

        try:
            rep = patchpack.apply_patch(game, patch, dry_run=dry, progress=log)
        except patchpack.PatchError as exc:
            log(str(exc))
            raise RuntimeError("패치를 넣지 못했습니다 (까닭은 위 글)") from None
        log("")
        log(rep.summary())
        if not dry:
            log("게임을 실행해서 확인하세요. 되돌리려면 [원래대로] 를 누르세요.")
        return {"replaced": rep.replaced, "added": rep.added, "removed": rep.removed}

    STATE.start_job("번역 패치 넣는 중", job)


def api_restore(body: dict | None = None) -> None:
    game = Path(body["path"]).expanduser().resolve() if body and body.get("path") else STATE.game
    if not game:
        raise RuntimeError("게임 폴더를 먼저 고르세요.")

    def job(log):
        # 마지막 수단으로 넣어 둔 것이 있으면 그것부터 걷어냅니다.
        # '원래대로' 를 눌렀는데 남의 DLL 이 남아 있으면 원래대로가
        # 아닙니다.
        from . import runtime

        gone = runtime.uninstall(game, progress=log) if runtime.installed(game) else 0
        n = restore_backup(game, progress=log)
        log(f"{n}개 파일 복원" + (f", 넣었던 것 {gone}개 제거" if gone else ""))
        return {"restored": n, "runtime_removed": gone}

    STATE.start_job("원래대로 되돌리는 중", job)




def api_stuck(body: dict) -> dict:
    """[적용이 안 되었나요?] — 증상을 고르면 다음 수를 알려 줍니다.

    사용자가 설계한 화면입니다. 적용이 **성공했는데도** 게임이 그대로인
    경우가 실제로 있었고(RJ01676161), 그때 사용자는 갈 곳이 없었습니다.
    """
    p = STATE.project
    what = str(body.get("what") or "")
    engine = p.engine if p else ""

    if what == "notshown":
        steps = ["번역은 파일에 들어갔는데 화면이 그대로인 경우입니다.",
                 "대개 게임이 그 글을 **다른 곳**에서 읽고 있습니다."]
        acts = [{"do": "find", "label": "그 문장이 어디 있는지 찾기",
                 "hint": "게임에 보이는 문장을 그대로 넣어 보세요"}]
        if engine == "rpgmaker":
            steps.append("이 게임은 RPG Maker 입니다. 플러그인 js 안에 대사를 "
                         "박아 두는 게임이 꽤 있습니다.")
            acts.append({"do": "reextract", "option": "js",
                         "label": "플러그인 js 파일까지 읽고 다시 뽑기"})
        if engine in ("rpgmaker", "tyrano"):
            # 파일을 바르게 고쳐 놨는데도 게임이 딴 데서 읽어 오는 경우가
            # 있습니다. 그럴 때는 고치는 대신 **그리는 순간 바꿔치기**를
            # 합니다. 원본을 안 건드리므로 되돌리기도 지우면 끝입니다.
            acts.append({"do": "overlay",
                         "label": "덧씌우기 — 원본을 그대로 두고 얹기",
                         "hint": "게임 파일은 안 건드립니다. 사전을 얹어 "
                                 "그리는 순간 바꿔치기합니다."})
        elif engine == "unity":
            acts.append({"do": "reextract", "option": "scenes",
                         "label": "씬 파일까지 읽고 다시 뽑기"})
            # 파일을 고쳐도 게임이 안 볼 때의 마지막 수단. RPG Maker ·
            # Ren'Py · 티라노는 게임 코드에 한 줄 끼워 넣어 뚫지만
            # 유니티는 그럴 자리가 없어 남의 것(BepInEx)을 빌립니다.
            acts.append({"do": "runtime",
                         "label": "마지막 수단 — 그리는 순간 바꿔치기",
                         "hint": "파일은 고쳤는데 게임이 안 볼 때. "
                                 "게임에 DLL 을 넣으므로 백신이 오탐할 수 "
                                 "있습니다."})
    elif what == "notfound":
        steps = ["뽑을 문장을 아예 못 찾은 경우입니다.",
                 "게임이 글을 담는 방식을 아직 모르는 것일 수 있습니다."]
        acts = [{"do": "find", "label": "그 문장이 어디 있는지 찾기"},
                {"do": "pinpoint", "label": "파일을 직접 지목하기",
                 "hint": "어느 파일인지 아신다면 그 파일을 골라 주세요"},
                {"do": "report", "label": "진단 보고서 만들기",
                 "hint": "계정 이름은 지워서 나옵니다. 그대로 보내셔도 됩니다"}]
    elif what == "boxes":
        steps = ["글자가 □□□ 로 나오는 것은 **번역 문제가 아닙니다.**",
                 "게임 글꼴에 한글 모양이 없어서 못 그리는 것입니다."]
        if engine in ("unity", "renpy"):
            acts = [{"do": "font", "label": "한글 폰트 넣기",
                     "hint": "컴퓨터에 이미 있는 한글 글꼴을 씁니다"}]
            if engine == "renpy":
                steps.append("Ren'Py 는 원본을 안 고치고 글꼴만 얹으므로 "
                             "되돌리기도 간단합니다.")
        else:
            # 폰트 바꾸기는 유니티·Ren'Py 만 됩니다. 할 수 없는 일을
            # 버튼으로 내밀면 눌러 보고 실패만 겪습니다.
            steps.append(
                "이 게임은 글꼴을 게임 파일 안에 굽지 않고 컴퓨터 글꼴을 "
                "가져다 씁니다. 그래서 gameloc 이 바꿀 폰트가 없습니다.")
            steps.append(
                "게임 폴더의 css 나 폰트 설정에서 글꼴 이름을 "
                "'Malgun Gothic' 같은 한글 글꼴로 바꾸면 나옵니다.")
            acts = [{"do": "find", "label": "그 문장이 어디 있는지 찾기"}]
    elif what == "hang":
        # 게임이 안 켜지거나 무한 로딩이거나 메뉴가 안 열리는 경우. 방법을 **순서대로**
        # 늘어놓고 하나씩 시도하게 합니다. 한 단계를 하고 게임을 켜 봐서 안 되면 다음.
        steps = ["게임이 안 켜지거나, 무한 로딩이거나, 메뉴가 안 열리는 경우입니다.",
                 "아래를 **위에서부터 한 단계씩** 해 보세요. 한 단계를 하고 게임을 켜 봐서 "
                 "여전히 안 되면 다음 단계로 넘어갑니다."]
        acts = [{"do": "diagnose", "label": "① 게임 로그로 원인 찾기",
                 "hint": "게임이 남긴 기록을 읽어 무엇이 문제인지 알려 드립니다. 아무것도 안 바꿉니다"},
                {"do": "reapply", "label": "② 이름표 보호를 걸어 다시 적용",
                 "hint": "원본으로 되돌린 뒤 번역을 다시 넣습니다. 게임이 이름으로 찾는 값은 "
                         "이번엔 번역하지 않고 둡니다"}]
        if STATE.game and (Path(STATE.game) / "save").is_file():
            acts.append({"do": "movesave", "label": "③ 세이브 파일 옮겨 두기",
                         "hint": "게임 폴더의 save 파일을 지우지 않고 이름만 바꿔 둡니다. "
                                 "번역이 잘못 들어간 채 켜서 생긴 빈 세이브가 원인일 때 풀립니다"})
        if engine == "unity":
            acts.append({"do": "runtime", "label": "④ 마지막 수단 — 그리는 순간 바꿔치기",
                         "hint": "파일은 고치지 않고 게임 실행 중에 바꿔치기합니다. "
                                 "게임에 DLL 을 넣으므로 백신이 오탐할 수 있습니다"})
        elif engine in ("rpgmaker", "tyrano"):
            acts.append({"do": "overlay", "label": "④ 덧씌우기 — 원본을 그대로 두고 얹기",
                         "hint": "게임 파일은 안 건드립니다"})
        acts.append({"do": "restore", "label": "⑤ 전부 원래대로 되돌리기",
                     "hint": "번역을 모두 지우고 게임 파일을 처음 상태로 돌립니다"})
    else:
        raise RuntimeError("무엇이 문제인지 골라 주세요.")
    return {"steps": steps, "actions": acts, "engine": engine}


def api_diagnose() -> dict:
    """게임 로그를 읽어 원인을 알려 줍니다. 읽기만 합니다."""
    from . import logdiag

    game = STATE.game
    if not game:
        raise RuntimeError("게임 폴더를 먼저 고르세요.")
    info = detect(Path(game))
    log = logdiag.find_player_log(info.data_dir)
    if log is None:
        return {"log": "", "findings": [],
                "say": "게임 로그(Player.log)를 찾지 못했습니다. 게임을 한 번 켜 본 뒤 다시 눌러 주세요."}
    found = logdiag.diagnose(logdiag.read_tail(log))
    return {"log": str(log),
            "findings": [{"code": f.code, "say": f.say, "do": f.do, "times": f.times}
                         for f in found],
            "say": "" if found else "알려진 원인을 로그에서 찾지 못했습니다. 다음 단계로 넘어가세요."}


def api_movesave() -> dict:
    """게임 폴더의 ``save`` 파일을 **지우지 않고** 이름만 바꿉니다(되돌릴 수 있음)."""
    game = STATE.game
    if not game:
        raise RuntimeError("게임 폴더를 먼저 고르세요.")
    f = Path(game) / "save"
    if not f.is_file():
        return {"moved": "", "say": "옮길 save 파일이 없습니다."}
    size = f.stat().st_size
    dst = f.with_name("save.moved_" + time.strftime("%Y%m%d_%H%M%S"))
    f.rename(dst)
    return {"moved": dst.name, "size": size,
            "say": f"save 파일({size:,}바이트)을 {dst.name} 으로 옮겨 두었습니다. "
                   "게임을 켜 보세요. 문제가 없으면 그대로 쓰시고, 옛 세이브가 필요하면 "
                   "이름을 save 로 되돌리세요."}


def api_pinpoint(body: dict) -> dict:
    """파일 하나를 지목했을 때 읽을 수 있는지 즉시 답합니다."""
    from . import pinpoint as pin

    f = Path(str(body.get("path") or "")).expanduser()
    look = pin.inspect(f)
    return {"kind": look.kind, "verdict": look.verdict(),
            "strings": look.strings, "sample": look.sample[:6],
            "canRead": look.can_read, "canWrite": look.can_write}

def api_runtime_plan() -> dict:
    """유니티 마지막 수단이 이 게임에 쓸 수 있는지, 무엇이 필요한지."""
    from . import runtime
    from .detect import detect

    game = STATE.game
    if not game:
        raise RuntimeError("게임 폴더를 먼저 고르세요.")
    need = runtime.plan(detect(game))
    guessed = _guessed()
    return {"ok": need.ok(), "backend": need.backend, "bits": need.bits,
            "unity": need.unity, "bepinex": need.bepinex_hint,
            "xunity": need.xunity_hint, "where": need.where,
            "releases": runtime.XUNITY_RELEASES,
            "installed": runtime.installed(game),
            "folders": [str(f) for f in runtime.guess_folders()],
            "from": guessed[0], "why": guessed[1],
            "bundled": all(runtime.bundled_for(need)),
            "notes": need.notes}


def _guessed():
    """번역표 원문을 보고 원문 언어를 짐작합니다. ``(코드, 근거)``."""
    from . import runtime

    p = STATE.project
    if not p:
        return "", ""
    said = (getattr(p, "source_lang", "") or "").strip()
    if said and said.lower() not in ("auto", "all"):
        return said, "프로젝트에 정해 두신 값입니다"
    return runtime.guess_lang(e.text for e in p.entries)



def api_runtime_install(body: dict) -> dict:
    """받아 둔 압축 두 개를 넣고 번역표를 깔아 줍니다."""
    from . import runtime

    p, game = STATE.project, STATE.game
    if not p or not game:
        raise RuntimeError("먼저 문장을 뽑고 번역을 채우세요.")
    from .detect import detect

    # 사람에게 파일 경로를 타이핑하게 하면 안 됩니다. 폴더를 주면
    # 우리가 찾습니다 — 실제로 폴더를 넣어 '파일을 못 찾았다' 만 보게
    # 됐습니다.
    need = runtime.plan(detect(game))
    if not need.ok():
        raise RuntimeError("\n".join(need.notes))

    # 우리가 지고 다니는 것이 이 게임에 맞으면 그걸 씁니다. 사람에게
    # 받아 오라고 시키지 않습니다.
    bep, xu = runtime.bundled_for(need)
    notes: list[str] = []
    where = Path(str(body.get("folder") or "")).expanduser()
    if bep and xu:
        notes.append("같이 들어 있는 것을 씁니다 — 따로 받지 않아도 됩니다.")
    else:
        if not str(body.get("folder") or "").strip():
            raise FileNotFoundError(
                "이 게임에 맞는 짝은 같이 들어 있지 않습니다.\n"
                "받아 두신 폴더를 알려주세요.")
        if not where.exists():
            raise FileNotFoundError(f"그런 폴더가 없습니다: {where}")
        bep, xu, notes = runtime.find_zips(where, need)
    if bep is None or xu is None:
        found = [f.name for f in runtime._zips(where)]
        tail = ["", "그 폴더에 있는 압축:"] + ["    " + f for f in found] \
            if found else ["", "그 폴더에는 압축 파일이 없습니다."]
        raise FileNotFoundError("\n".join(notes + tail))
    lang = body.get("lang") or p.languages[0]
    # 원문 언어는 **사람이 정합니다.** 우리는 짐작만 보여 주고, 고쳐서
    # 주면 그대로 씁니다. 이걸 틀리면 플러그인이 원문을 거들떠보지도
    # 않아 번역표를 다 깔고도 한 줄이 안 바뀝니다.
    src_lang = str(body.get("from") or "").strip() or p.source_lang

    def job(log):
        log(f"BepInEx : {bep.name}")
        log(f"XUnity  : {xu.name}")
        for n in notes:
            log("  · " + n)
        return runtime.install(game, bep, xu, p, lang,
                               src_lang=src_lang,
                               with_font=body.get("font", True) is not False,
                               ttf=str(body.get("ttf") or ""),
                               progress=log)

    STATE.start_job("마지막 수단 넣기", job)
    return {"ok": True}


def api_runtime_remove() -> dict:
    from . import runtime

    game = STATE.game
    if not game:
        raise RuntimeError("게임 폴더를 먼저 고르세요.")

    def job(log):
        return {"removed": runtime.uninstall(game, progress=log)}

    STATE.start_job("마지막 수단 지우기", job)
    return {"ok": True}

def api_export(body: dict | None = None) -> dict:
    p, w = STATE.project, STATE.workdir
    if not p or not w:
        raise RuntimeError("먼저 문자열을 뽑으세요.")
    STATE.save()
    formulas = bool((body or {}).get("googleFormulas"))
    path = write_workbook(p, w / SHEET_XLSX, google_formulas=formulas)
    return {"path": str(path), "googleFormulas": formulas}


def api_import() -> dict:
    p, w = STATE.project, STATE.workdir
    if not p or not w:
        raise RuntimeError("먼저 문자열을 뽑으세요.")
    xlsx = w / SHEET_XLSX
    if not xlsx.exists():
        raise FileNotFoundError("translation.xlsx 가 작업 폴더에 없습니다. 먼저 내보내세요.")
    problems: list[str] = []
    edits, langs = read_workbook(xlsx, problems)
    n = merge_into_project(p, edits)
    STATE.save()
    return {"changed": n, "langs": langs, "unfinished": len(problems),
            "message": unfinished_message(problems)}


# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "gameloc"

    def log_message(self, *a):   # 콘솔 조용히
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _guard(self) -> bool:
        """이 요청을 받아도 되는가."""
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            return False
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip()
        return host in ALLOWED_HOSTS or host == ""

    def _auth(self) -> bool:
        """API 는 토큰이 있어야 합니다. 화면 자체(GET /)는 필요 없습니다."""
        return secrets.compare_digest(self.headers.get("X-Gameloc-Token") or "", TOKEN)

    def do_GET(self):
        if not self._guard():
            return self._json({"error": "forbidden"}, 403)
        u = urlparse(self.path)
        try:
            if u.path in ("/", "/index.html"):
                html = (WEB_DIR / "index.html").read_text("utf-8")
                return self._send(200, html.replace("__GAMELOC_TOKEN__", TOKEN)
                                   .replace("__GAMELOC_VERSION__", __version__)
                                  .encode("utf-8"), "text/html; charset=utf-8")
            if u.path == "/favicon.ico":
                return self._send(200, _FAVICON, "image/svg+xml")
            if u.path.startswith("/api/") and not self._auth():
                return self._json({"error": "forbidden"}, 403)
            if u.path == "/api/state":
                return self._json(api_state())
            if u.path == "/api/job":
                with STATE.lock:
                    out = dict(STATE.job)
                    # 실시간 번역 보기: 화면이 이미 받은 번호(since) 뒤의 것만 보냅니다.
                    try:
                        since = int((parse_qs(u.query).get("since") or ["0"])[0])
                    except ValueError:
                        since = 0
                    out["preview"] = [r for r in out.get("preview", []) if r["n"] > since]
                    out["elapsed"] = (time.time() - out["started"]
                                      if out.get("started") and out.get("running")
                                      else out.get("elapsed", 0))
                    return self._json(out)
            if u.path == "/api/fonts":
                return self._json(api_fonts())
            if u.path == "/api/renpy/pieces":
                return self._json(api_renpy_pieces())
            if u.path == "/api/entries":
                return self._json(api_entries(parse_qs(u.query)))
            return self._json({"error": "not found"}, 404)
        except Exception as e:
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_POST(self):
        if not self._guard():
            return self._json({"error": "forbidden"}, 403)
        u = urlparse(self.path)
        if not self._auth():
            return self._json({"error": "forbidden"}, 403)
        try:
            body = self._body()
            if u.path == "/api/pick":
                return self._json({"path": pick_folder()})
            if u.path == "/api/scan":
                return self._json(api_scan(body["path"]))
            if u.path == "/api/font":
                api_font(body)
                return self._json({"started": True})
            if u.path == "/api/renpy/unpack":
                return self._json(api_renpy_unpack(body))
            if u.path == "/api/peek":
                return self._json(api_peek(body["path"]))
            if u.path == "/api/unpack":
                return self._json(api_unpack(body["path"]))
            if u.path == "/api/repack":
                return self._json(api_repack(body["path"]))
            if u.path == "/api/extract":
                api_extract(body)
                return self._json({"started": True})
            if u.path == "/api/extract/dll_add":
                api_extract_dll_add(body)
                return self._json({"started": True})
            if u.path == "/api/open":
                return self._json(api_open(body["path"], bool(body.get("strict", True))))
            if u.path == "/api/bulk/preview":
                return self._json(api_bulk_preview(body))
            if u.path == "/api/bulk/apply":
                return self._json(api_bulk_apply(body))
            if u.path == "/api/locale/fill":
                return self._json(api_locale_fill(body))
            if u.path == "/api/locale/check":
                return self._json(api_locale_check())
            if u.path == "/api/handoff/plan":
                return self._json(api_handoff_plan(body))
            if u.path == "/api/handoff/text":
                return self._json(api_handoff_text(body))
            if u.path == "/api/handoff/preview":
                return self._json(api_handoff_preview(body))
            if u.path == "/api/handoff/apply":
                return self._json(api_handoff_apply(body))
            if u.path == "/api/bulk/undo":
                return self._json(api_bulk_undo())
            if u.path == "/api/save":
                return self._json(api_save_entry(body))
            if u.path == "/api/lang":
                return self._json(api_add_lang(body.get("lang", "").strip()))
            if u.path == "/api/find":
                return self._json(api_find(body))
            if u.path == "/api/providers":
                return self._json(api_providers())
            if u.path == "/api/autotranslate":
                api_autotranslate(body)
                return self._json({"started": True})
            if u.path == "/api/cancel":
                with STATE.lock:
                    STATE.job["cancel"] = True
                return self._json({"ok": True})
            if u.path == "/api/diagnose":
                return self._json(api_diagnose())
            if u.path == "/api/movesave":
                return self._json(api_movesave())
            if u.path == "/api/chrome/next":
                return self._json(api_chrome_next(body))
            if u.path == "/api/chrome/put":
                return self._json(api_chrome_put(body))
            if u.path == "/api/runtime/plan":
                return self._json(api_runtime_plan())
            if u.path == "/api/runtime/install":
                return self._json(api_runtime_install(body))
            if u.path == "/api/runtime/remove":
                return self._json(api_runtime_remove())
            if u.path == "/api/stuck":
                return self._json(api_stuck(body))
            if u.path == "/api/pinpoint":
                return self._json(api_pinpoint(body))
            if u.path == "/api/apply":
                api_apply(body)
                return self._json({"started": True})
            if u.path == "/api/restore":
                api_restore(body)
                return self._json({"started": True})
            if u.path == "/api/pickfile":
                return self._json({"path": pick_file()})
            if u.path == "/api/patch/save":
                api_patch_save()
                return self._json({"started": True})
            if u.path == "/api/patch/apply":
                api_patch_apply(body)
                return self._json({"started": True})
            if u.path == "/api/patch/overlay":
                api_patch_overlay()
                return self._json({"started": True})
            if u.path == "/api/export":
                return self._json(api_export(body))
            if u.path == "/api/import":
                return self._json(api_import())
            if u.path == "/api/flush":
                STATE.save()
                return self._json({"ok": True})
            if u.path == "/api/reveal":
                target = STATE.workdir if body.get("what") == "work" else STATE.game
                if target:
                    open_in_explorer(Path(target))
                return self._json({"ok": True})
            return self._json({"error": "not found"}, 404)
        except Exception as e:
            return self._json({"error": f"{type(e).__name__}: {e}"}, 400)


def serve(port: int = 8777, open_browser: bool = True) -> None:
    requested = port
    httpd = None
    for p in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            port = p
            break
        except OSError:
            continue
    if httpd is None:
        raise SystemExit("빈 포트를 찾지 못했습니다.")

    url = f"http://127.0.0.1:{port}/"
    print("=" * 46)
    print("  Gameloc 이 열렸습니다")
    print(f"  버전 {__version__}")
    print(f"  {url}")
    if port != requested:
        # 이미 떠 있는 예전 창이 원래 포트를 잡고 있습니다. 브라우저에 남아
        # 있는 옛 탭을 계속 쓰면 **옛 버전 코드가 돕니다.** 화면 왼쪽 위
        # 버전 배지로 확인할 수 있게 알려줍니다.
        print("")
        print(f"  [!] {requested}번 포트를 이미 쓰고 있습니다.")
        print("      전에 켜둔 번역기 창이 남아 있는 것 같습니다.")
        print("      그 창을 닫고, 브라우저의 옛 탭도 닫으세요.")
        print("      옛 탭을 계속 쓰면 예전 버전이 돌아갑니다.")
        print("")
    print("  창을 닫으려면 이 검은 창을 닫으세요.")
    print("=" * 46)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        STATE.save()


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    serve(a.port, not a.no_browser)
