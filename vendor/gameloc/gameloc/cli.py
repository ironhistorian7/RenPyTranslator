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

"""Gameloc — 게임 폴더를 넣으면 번역할 문자열을 뽑아주고,
채워 넣은 번역을 다시 게임에 되돌려 넣는 도구."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from . import __version__
from .apply import apply as apply_translations
from .apply import restore as restore_backup
from .detect import detect
from .extract import extract as run_extract
from .heuristics import LEVELS
from .model import Project
from .sheet import (merge_into_project, read_workbook, unfinished_message,
                    write_workbook)

PROJECT_JSON = "project.json"
SHEET_XLSX = "translation.xlsx"


def _say(msg: str) -> None:
    click.echo(msg)


def _load_project(workdir: Path) -> Project:
    pj = workdir / PROJECT_JSON
    if not pj.exists():
        raise click.ClickException(
            f"{pj} 가 없습니다. 먼저 `python -m gameloc.cli extract` 를 실행하세요.")
    return Project.load(pj)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__)
def main() -> None:
    """게임 폴더 → 번역 → 다시 게임으로."""


@main.command()
@click.option("--port", default=8777, help="쓸 포트")
@click.option("--no-browser", is_flag=True, help="브라우저를 자동으로 열지 않음")
def gui(port: int, no_browser: bool) -> None:
    """브라우저 번역기를 엽니다 (권장)."""
    from .webui import serve
    serve(port, not no_browser)


@main.command()
@click.argument("game_folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
def scan(game_folder: Path) -> None:
    """폴더를 분석만 하고 무엇이 들어있는지 알려줍니다."""
    info = detect(game_folder)
    _say(info.summary())
    try:
        from . import profile
        _say("")
        for line in profile.classify(info).lines():
            _say(line)
        _say("")
    except Exception:                                   # noqa: BLE001
        pass                 # 유형 판별이 실패해도 스캔은 그대로 돌아갑니다
    if info.data_dir:
        _say(f"데이터 폴더: {info.data_dir}")
    for p in info.asset_files[:15]:
        _say(f"  · {p.relative_to(info.root)}")
    if len(info.asset_files) > 15:
        _say(f"  … 외 {len(info.asset_files) - 15}개")


@main.command()
@click.argument("game_folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("-o", "--out", type=click.Path(path_type=Path),
              help="보고서를 저장할 파일 (기본: 게임폴더 옆 gameloc_진단.txt)")
@click.option("--no-scenes", is_flag=True, help="씬 파일은 건너뜁니다 (빠름)")
def peek(game_folder: Path, out: Path | None, no_scenes: bool) -> None:
    """[유니티] 파일 안에 무엇이 들어 있는지 그대로 적어줍니다.

    번역할 문장을 못 찾을 때, 원인이 '자리를 모른다' 인지 '못 읽는다' 인지
    가려내는 용도입니다. 게임 파일은 읽기만 하고 아무것도 바꾸지 않습니다.
    """
    from .peek import report

    text = report(game_folder, include_scenes=not no_scenes)
    dest = out or game_folder.parent / f"{game_folder.name}_gameloc_진단.txt"
    dest.write_text(text, "utf-8")
    _say(text[:2000])
    _say("")
    _say(f"전체 보고서: {dest}")


@main.command(name="fonts")
def fonts_cmd() -> None:
    """이 컴퓨터에 깔린 한글 글꼴을 보여줍니다."""
    from .fontlist import scan

    found = scan()
    if not found:
        _say("한글이 든 글꼴을 찾지 못했습니다.")
        return
    _say(f"한글 글꼴 {len(found)}개:")
    for f in found:
        _say(f"  · {f.name}")
        _say(f"      {f.path}")


@main.command(name="font")
@click.argument("game_folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--ttf", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="쓸 한글 글꼴 파일. 없으면 컴퓨터에서 알아서 찾습니다.")
@click.option("--dry-run", is_flag=True, help="바꾸지 않고 무엇을 할지만 봅니다")
def font_cmd(game_folder: Path, ttf: Path | None, dry_run: bool) -> None:
    """[유니티] 한글이 □□□ 로 나오는 것을 고칩니다."""
    from .font import fix

    rep = fix(game_folder, ttf=ttf, dry_run=dry_run, progress=_say)
    _say("")
    _say(rep.summary())
    for n in rep.notes:
        _say("  · " + n)
    if rep.changed_files:
        _say("되돌리려면: python -m gameloc.cli restore <게임폴더>")


@main.command()
@click.argument("game_folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
def unpack(game_folder: Path) -> None:
    """[Electron] resources/app.asar 를 풀어 번역할 수 있게 만듭니다."""
    from . import asar
    info = detect(game_folder)
    if info.electron_app and not info.electron_asar:
        _say("이미 풀려 있습니다.")
        return
    if not info.electron_asar:
        raise click.ClickException("이 폴더에서 app.asar 를 찾지 못했습니다.")
    n, dest = asar.open_up(info.electron_asar)
    _say(f"{n}개 파일을 풀었습니다 → {dest}")
    _say("원본 app.asar 는 app.asar.gameloc-off 로 이름만 바꿔 두었습니다.")
    after = detect(game_folder)
    _say("")
    _say("안쪽 엔진: " + after.summary())


@main.command()
@click.argument("game_folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
def repack(game_folder: Path) -> None:
    """[Electron] 푼 폴더를 지우고 원래 app.asar 로 되돌립니다."""
    from . import asar
    if asar.close_up(game_folder):
        _say("원래대로 되돌렸습니다.")
    else:
        _say("되돌릴 것이 없습니다 (app.asar.gameloc-off 가 없습니다).")


@main.command()
@click.argument("game_folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.argument("text")
def find(game_folder: Path, text: str) -> None:
    """번역이 안 된 문장이 어느 파일에 있는지 찾아줍니다."""
    from .locate import find as locate_find
    rep = locate_find(game_folder, text)
    _say(rep.verdict())
    _say(f"(텍스트 파일 {rep.scanned}개 확인)")
    for h in rep.hits:
        _say("")
        _say(f"  {h.file}  [{h.kind}]  {'가져옴' if h.covered else '안 가져옴'}")
        _say(f"    위치: {h.where}   ({h.how})")
        _say(f"    원문: {h.raw}")
        _say(f"    {h.reason}")


@main.command()
@click.argument("game_folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("-o", "--out", "workdir", type=click.Path(path_type=Path), default=None,
              help="작업 폴더 (기본: ./<게임이름>_loc)")
@click.option("-l", "--lang", "langs", default="ko",
              help="만들 번역 열, 쉼표로 구분 (예: ko,ja)")
@click.option("-f", "--filter", "filter_level",
              type=click.Choice(list(LEVELS)), default="normal",
              help="[유니티] 문자열 선별 강도")
@click.option("--no-dedup", is_flag=True, help="같은 문장을 합치지 않고 등장 위치마다 한 줄씩")
@click.option("--no-scenes", is_flag=True,
              help="[유니티] level* 씬 파일을 건너뜁니다 (빠르지만 대사가 통째로 빠질 수 있습니다)")
@click.option("--src-lang", "src_lang",
              type=click.Choice(["all", "en", "ja", "zh", "ko"]), default="all",
              help="[유니티] 뽑을 원문 언어. 영어·일본어·중국어를 함께 담은 "
                   "게임에서 한 언어만 골라 뽑습니다")
@click.option("--notes", is_flag=True, help="[RPG Maker] 데이터베이스 메모(note) 칸도 포함")
@click.option("--js", "use_js", is_flag=True, help="[RPG Maker] 플러그인 js 파일 안의 문구까지")
@click.option("--all-plugin-params", "all_params", is_flag=True,
              help="[RPG Maker] 플러그인 파라미터를 전부 (위험: 게임이 죽을 수 있음)")
@click.option("--dll", "use_dll", is_flag=True,
              help="[유니티 Mono] 게임 코드(DLL)에 글자 그대로 박힌 문구까지 (실험 · 자리가 모자라면 "
                   "그 번역은 못 넣습니다)")
@click.option("--max-rows", type=int, default=None, help="엑셀에 쓸 최대 행 수")
def extract(game_folder: Path, workdir: Path | None, langs: str,
            filter_level: str, no_dedup: bool, no_scenes: bool, src_lang: str,
            notes: bool,
            use_js: bool, all_params: bool, use_dll: bool, max_rows: int | None) -> None:
    """게임 폴더에서 문자열을 뽑아 엑셀을 만듭니다."""
    workdir = workdir or Path.cwd() / f"{game_folder.resolve().name}_loc"
    workdir.mkdir(parents=True, exist_ok=True)

    proj = run_extract(
        game_folder,
        filter_level=filter_level,
        dedup=not no_dedup,
        languages=[l.strip() for l in langs.split(",") if l.strip()],
        include_scenes=not no_scenes,
        source_lang=src_lang,
        include_notes=notes,
        include_js=use_js,
        include_dll=use_dll,
        all_plugin_params=all_params,
        progress=_say,
    )
    proj.save(workdir / PROJECT_JSON)
    xlsx = write_workbook(proj, workdir / SHEET_XLSX, max_rows=max_rows)

    _say("")
    _say(f"고유 문자열 {len(proj.entries)}개 (총 등장 {proj.stats['raw_strings']}회)")
    _say(f"엑셀: {xlsx}")
    _say(f"번역을 채운 뒤:  python -m gameloc.cli apply {workdir}")
    if proj.stats.get("monobehaviour_no_typetree"):
        _say(f"참고: 타입트리가 없어 건너뛴 오브젝트 "
             f"{proj.stats['monobehaviour_no_typetree']}개 (IL2CPP 빌드에서 흔합니다)")


@main.command()
@click.argument("workdir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("-l", "--lang", default=None, help="적용할 언어 열 (기본: 첫 번째 언어)")
@click.option("--game", type=click.Path(exists=True, file_okay=False, path_type=Path),
              default=None, help="게임 폴더를 다른 위치로 지정")
@click.option("-n", "--dry-run", is_flag=True, help="쓰지 않고 결과만 보기")
def apply(workdir: Path, lang: str | None, game: Path | None, dry_run: bool) -> None:
    """엑셀에 채운 번역을 게임 파일에 넣습니다."""
    proj = _load_project(workdir)
    xlsx = workdir / SHEET_XLSX
    found: list[str] = []
    problems: list[str] = []
    n = 0
    if xlsx.exists():
        edits, found = read_workbook(xlsx, problems)
        n = merge_into_project(proj, edits)
        proj.save(workdir / PROJECT_JSON)

    lang = lang or (found[0] if found else proj.languages[0])
    total = sum(1 for e in proj.entries if e.translations.get(lang))
    _say(f"엑셀 읽음 — 언어 열: {', '.join(found) or '없음'} | "
         f"'{lang}' 번역 {total}건 (이번에 새로 바뀐 것 {n}건)")
    if problems:
        _say(f"  ! {unfinished_message(problems)}")

    rep = apply_translations(proj, lang, dry_run=dry_run, game_root=game, progress=_say)
    _say("")
    if dry_run:
        _say("[미리보기] 파일은 아직 안 건드렸습니다.")
    for line in rep.report():
        _say(line)
    for f in rep.failures[:20]:
        _say(f"  ! {f}")
    if rep.risky:
        _say("")
        _say("대사가 아니라 '이름'으로 보여 넣지 않은 항목:")
        for r in rep.risky[:20]:
            _say(f"  · {r}")
    if not dry_run and rep.files_written:
        _say("원본은 <게임폴더>/__gameloc_backup__ 에 있습니다. "
             "되돌리려면: python -m gameloc.cli restore <게임폴더>")


@main.command()
@click.argument("workdir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("-l", "--lang", "langs", default=None, help="엑셀에 둘 언어 열 (예: ko,ja,en)")
@click.option("--max-rows", type=int, default=None)
@click.option("--google-formulas", is_flag=True,
              help="비어 있는 번역 칸에 구글 시트 번역 수식을 채웁니다 "
                   "(구글 드라이브에서 열면 바로 번역됩니다)")
def sheet(workdir: Path, langs: str | None, max_rows: int | None,
          google_formulas: bool) -> None:
    """엑셀을 다시 만듭니다. 언어 열을 바꾸거나 추가할 때 씁니다."""
    proj = _load_project(workdir)
    xlsx = workdir / SHEET_XLSX
    if xlsx.exists():
        problems: list[str] = []
        edits, _ = read_workbook(xlsx, problems)
        merge_into_project(proj, edits)
        if problems:
            _say(f"  ! {unfinished_message(problems)}")
    if langs:
        proj.languages = [l.strip() for l in langs.split(",") if l.strip()]
    proj.save(workdir / PROJECT_JSON)
    write_workbook(proj, xlsx, max_rows=max_rows, google_formulas=google_formulas)
    _say(f"엑셀 갱신: {xlsx} (언어 열: {', '.join(proj.languages)})")
    if google_formulas:
        _say("구글 시트용 수식을 채웠습니다. 사용법은 엑셀 안의 [정보] 시트에 있습니다.")


@main.command()
@click.argument("game_folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
def restore(game_folder: Path) -> None:
    """번역 적용 전 원본으로 되돌립니다."""
    n = restore_backup(game_folder, progress=_say)
    _say(f"{n}개 파일 복원")


@main.command(name="patch-save")
@click.argument("game_folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("-o", "--out", type=click.Path(dir_okay=False, path_type=Path), default=None,
              help="저장할 파일 (기본: 게임 폴더 옆 <이름>.gameloc-patch.zip)")
def patch_save(game_folder: Path, out: Path | None) -> None:
    """적용된 번역을 패치 파일 하나로 저장합니다(같은 게임 원본에 바로 넣을 수 있습니다)."""
    from . import patchpack

    try:
        patchpack.export_patch(game_folder, out, progress=_say)
    except patchpack.PatchError as exc:
        raise click.ClickException(str(exc))
    _say("※ " + patchpack.NOTE)


@main.command(name="patch-folder")
@click.argument("game_folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("-o", "--out", type=click.Path(file_okay=False, path_type=Path), default=None,
              help="내보낼 폴더 (기본: 게임 폴더 옆 <이름>_번역파일)")
def patch_folder(game_folder: Path, out: Path | None) -> None:
    """적용된 번역을, 같은 게임 폴더에 그냥 복사해 덮어쓸 폴더로 내보냅니다(gameloc 없이 쓰는 길)."""
    from . import patchpack

    try:
        patchpack.export_overlay(game_folder, out, progress=_say)
    except patchpack.PatchError as exc:
        raise click.ClickException(str(exc))
    _say("※ " + patchpack.NOTE)


@main.command(name="images")
@click.argument("game_folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("-g", "--group", "groups", multiple=True,
              help="올릴 묶음(폴더) 이름. 여러 번 쓸 수 있습니다. 없으면 글자가 있을 법한 묶음만")
@click.option("--big", is_flag=True, help="100 MB 넘는 묶음도 엽니다(느리고 메모리를 많이 씁니다)")
@click.option("--list", "list_only", is_flag=True, help="묶음별 그림 수만 보여 주고 끝냅니다")
def images(game_folder: Path, groups: tuple[str, ...], big: bool, list_only: bool) -> None:
    """게임 안의 그림을 모아, 글자가 든 그림을 고르기 위한 번호 붙은 미리보기 표를 만듭니다."""
    from . import imagescan

    try:
        res = imagescan.scan(game_folder, groups=list(groups) or None, include_big=big, progress=_say)
        _say(f"그림 {len(res.items)}장, 묶음 {len(res.groups)}개")
        for g in res.groups:
            mark = "★" if g.suggested else " "
            note = f"  [{g.skipped}]" if g.skipped else ""
            _say(f"  {mark} {g.name:<22} {g.count:>6}장 (작은 것 {g.small}){note}")
        if list_only:
            return
        cand = imagescan.pick_candidates(res)
        if not cand:
            raise click.ClickException("후보가 없습니다 — -g 로 묶음을 지정해 보세요")
        work = imagescan.build(res, cand, progress=_say)
    except imagescan.ScanError as exc:
        raise click.ClickException(str(exc))
    for e in res.errors[:10]:
        _say("  ! " + e)
    _say(f"후보 {len(cand)}장 → 미리보기 표: {work / 'sheets'}")
    _say(f"표를 ChatGPT 에 올릴 때 쓸 문구: {work / 'triage_prompt.txt'}")
    _say("글자가 든 번호를 알아낸 뒤:  gameloc images-pick <게임폴더> \"3, 7, 12-15\"")


@main.command(name="images-pick")
@click.argument("game_folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.argument("numbers")
def images_pick(game_folder: Path, numbers: str) -> None:
    """미리보기 표에서 고른 번호의 그림을 ``selected`` 폴더로 모읍니다. 예: "3, 7, 12-15"."""
    from . import imagescan

    nums = imagescan.parse_numbers(numbers)
    if not nums:
        raise click.ClickException("번호를 읽지 못했습니다")
    try:
        chosen = imagescan.select(game_folder, nums)
    except imagescan.ScanError as exc:
        raise click.ClickException(str(exc))
    _say(f"{len(chosen)}장을 {imagescan.work_dir(game_folder) / 'selected'} 에 모았습니다")


@main.command(name="images-export")
@click.argument("game_folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--note", default="", help="지시문 끝에 덧붙일 말(예: 화풍·용어 지시)")
def images_export(game_folder: Path, note: str) -> None:
    """고른 그림을 ChatGPT 에 올릴 파일(to_chatgpt 폴더)과 지시문으로 만듭니다."""
    from . import imagescan, imagetrans

    try:
        out = imagetrans.export(game_folder, note=note)
    except imagescan.ScanError as exc:
        raise click.ClickException(str(exc))
    n = len(list(out.glob("*.png")))
    _say(f"{n}장 → {out}")
    _say(f"지시문: {out / imagetrans.PROMPT_FILE}  (그림은 한 번에 한 장씩 올리세요)")
    _say("용어를 고정하려면 image_work\\glossary.txt 에 `日本語 → 한국어` 를 줄마다 적고 다시 실행하세요")
    _say("ChatGPT 가 준 그림을 한 폴더에 모은 뒤:  gameloc images-import <게임폴더> <그 폴더>")


@main.command(name="images-boxes")
@click.argument("game_folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.argument("image")
@click.option("--mask", default="{}", help='글자 찾기 설정(JSON). 예 {"mode":"alpha"} {"thresh":150,"gap":8}')
@click.option("--scale", type=int, default=None, help="확인용 그림 확대 배율")
def images_boxes(game_folder: Path, image: str, mask: str, scale: int | None) -> None:
    """그림에서 글자 덩어리 상자를 찾아 번호를 붙인 확인용 그림을 만듭니다(그림 글자 직접 바꾸기 1단계)."""
    import json

    from PIL import Image

    from . import imagepaint, imagescan

    work = imagescan.work_dir(game_folder)
    src = work / "candidates" / image
    if not src.is_file():
        raise click.ClickException(f"모아 둔 그림에 없습니다: {image} (먼저 gameloc images)")
    try:
        params = imagepaint.Params.from_dict(json.loads(mask))
    except (ValueError, TypeError) as exc:
        raise click.ClickException(f"--mask 를 읽지 못했습니다: {exc}")
    im = Image.open(src).convert("RGBA")
    boxes = imagepaint.find_boxes(im, params)
    out = work / "boxes" / image.replace("/", "_")
    out.parent.mkdir(parents=True, exist_ok=True)
    imagepaint.annotate(im, boxes, scale).save(out)
    _say(f"글자 상자 {len(boxes)}개 → {out}")
    _say("번호별 한글을 image_work\\specs\\<이름>.json 에 적은 뒤:  gameloc images-paint <게임폴더>")


@main.command(name="images-paint")
@click.argument("game_folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.argument("names", nargs=-1)
def images_paint(game_folder: Path, names: tuple[str, ...]) -> None:
    """명세(specs)대로 그림 속 글자를 지우고 한글로 써서 ``<게임>_loc/images`` 에 넣습니다."""
    from . import imagepaint

    try:
        done = imagepaint.paint_all(game_folder, list(names) or None, progress=_say)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        raise click.ClickException(str(exc))
    _say(f"{len(done)}장 완료. 미리보기는 image_work\\preview 에 있습니다. 게임에 넣으려면 [게임에 적용]")


@main.command(name="images-import")
@click.argument("game_folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.argument("results", type=click.Path(exists=True, file_okay=False, path_type=Path))
def images_import(game_folder: Path, results: Path) -> None:
    """ChatGPT 가 만든 그림을 게임 크기에 맞춰 ``<게임>_loc/images`` 에 넣습니다(적용할 때 게임에 들어갑니다)."""
    from . import imagescan, imagetrans

    try:
        res = imagetrans.import_results(game_folder, results, progress=_say)
    except imagescan.ScanError as exc:
        raise click.ClickException(str(exc))
    _say(f"{len(res['done'])}장 넣음. 원본과 비교: {imagescan.work_dir(game_folder) / 'compare.png'}")
    if res["unmatched"]:
        _say("짝을 못 찾은 파일: " + ", ".join(res["unmatched"]) + "  (파일 이름 앞에 번호_ 를 붙이세요)")
    if res["missing"]:
        _say("아직 안 온 번호: " + ", ".join(str(n) for n in res["missing"]))
    _say("게임에 넣으려면 gameloc 에서 [게임에 적용] 을 다시 실행하세요")


@main.command(name="patch-apply")
@click.argument("game_folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.argument("patch", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--dry-run", is_flag=True, help="검사만 하고 아무것도 안 바꿉니다")
def patch_apply(game_folder: Path, patch: Path, dry_run: bool) -> None:
    """저장해 둔 번역 패치를 게임 폴더에 넣습니다(같은 게임 버전인지 먼저 확인합니다)."""
    from . import patchpack

    try:
        rep = patchpack.apply_patch(game_folder, patch, dry_run=dry_run, progress=_say)
    except patchpack.PatchError as exc:
        raise click.ClickException(str(exc))
    _say(rep.summary())


if __name__ == "__main__":
    sys.exit(main())
