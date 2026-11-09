# SPDX-License-Identifier: GPL-3.0-or-later
"""[적용] 을 **몇 번을 눌러도 같은 결과**가 되게 합니다.

## 무엇이 문제였나

적용은 문자열이 **몇 번째 바이트에 있는지**로 기억했습니다. 그런데 한 번
넣고 나면 그 자리가 전부 밀립니다. 두 번째 적용은 처음에 기억해 둔 옛
자리를 그대로 쓰니 엉뚱한 곳을 가리킵니다.

    [ko] 파일 2개, 문자열 5개 적용 | 실패 17건
      ! level1:  오프셋 356 의 문자열이 범위를 넘습니다
      ! level10: 오프셋 272 의 문자열이 범위를 넘습니다
      ! …  (17개 파일 전부)

실수로 두 번 누르는 것만의 문제가 아닙니다. 이게 늘 하는 일입니다.

    뽑기 → 자동 번역 → 적용 → 게임에서 어색한 데 발견
    → 표에서 고친다 → 다시 적용        ← 여기서 깨집니다

## 어떻게 고쳤나

**자리를 안 쓰고 글로 찾습니다.**

    ① 백업이 있으면 먼저 원래대로 되돌린다
    ② 지금 게임 폴더에서 **다시 뽑는다**   ← 자리는 항상 새것
    ③ 뽑힌 원문을 번역표에서 찾아 짝지운다
    ④ 원래 쓰던 적용기에 넘긴다

밀릴 자리를 저장하지 않으니 밀릴 일이 없습니다.

처음 누르는 것이면 ①②③ 을 건너뛰고 곧장 넣습니다 — 되돌릴 것도,
밀린 자리도 없기 때문입니다. 빠른 길은 빠른 채로 둡니다.

## 되돌렸다 다시 쓰는 사이에 사라지던 것들 (fixups 참고)

되돌리기는 **번역표에 없는 수정**까지 같이 지웁니다(한글 폰트 넣기, 손으로 고친 파일).
그래서 ① 앞뒤로 두 가지를 더 합니다.

- 되돌리기 **전에**, 마지막 적용 뒤 누가 고친 파일이 있으면 따로 복사해 두고 알립니다.
- 적용이 끝난 **뒤에**, 기억해 둔 폰트 넣기를 같은 옵션으로 다시 돌립니다.

**미리보기(dry_run)는 게임 파일을 건드리지 않습니다.** 예전에는 되돌리기가 먼저 실제로
돌아서, 화면에 "게임은 아직 그대로입니다" 라고 쓰고는 번역을 지우고 있었습니다.
"""

from __future__ import annotations

from pathlib import Path


def _noop(_: str) -> None:
    pass


def reapply(proj, lang: str, game_root: Path, *, dry_run: bool = False,
            progress=_noop):
    """``ApplyReport`` 를 돌려줍니다. 부르는 쪽은 달라질 게 없습니다."""
    from . import apply as ap
    from . import extract as ex
    from . import fixups

    root = Path(game_root)
    if ap._backup_root(root) is None:
        return ap.apply(proj, lang, dry_run=dry_run, game_root=root,
                        progress=progress)

    # 번역표를 먼저 챙깁니다. 다시 뽑으면 proj 의 자리 정보는 버리지만
    # **사람이 채운 번역문**은 그대로 살려야 합니다.
    table = {e.text: e.translations[lang]
             for e in proj.entries
             if (e.translations.get(lang) or "").strip()
             and e.translations[lang] != e.text}
    if not table:
        return ap.apply(proj, lang, dry_run=dry_run, game_root=root,
                        progress=progress)

    if dry_run:
        # 미리보기는 파일을 안 건드립니다. 이미 적용된 게임이라 지금 파일 기준으로 계산합니다.
        progress("이미 한 번 넣은 폴더입니다. 미리보기는 게임 파일을 건드리지 않고 "
                 "지금 파일 기준으로 계산합니다.")
        fresh = _fresh(ex, root, proj, progress)
        _fill(fresh, table, lang, progress)
        rep = ap.apply(fresh, lang, dry_run=True, game_root=root, progress=progress)
        rep.notes.append("ℹ  이미 적용된 게임의 미리보기라 개수는 대략입니다. "
                         "실제 적용 결과는 [게임에 적용] 뒤에 정확합니다.")
        return rep

    # 지우기 전에 **번역표 밖에서 고쳐진 파일**을 따로 보관합니다.
    drift = fixups.drifted(root)
    kept = None
    skipped: list[str] = []
    if drift:
        kept, skipped = fixups.keep_drifted(root, drift)
        progress(f"※ 마지막 적용 뒤에 다른 곳에서 고쳐진 파일 {len(drift)}개가 있습니다 "
                 "— 다시 적용하면 그 수정은 원본으로 돌아갑니다.")
        for rel in drift[:8]:
            progress(f"    {rel}")
        if kept is not None:
            progress(f"    (지우기 전에 복사해 뒀습니다: {kept})")

    progress("이미 한 번 넣은 폴더입니다. 되돌리고 처음부터 다시 넣습니다.")
    n = ap.restore(root, progress=lambda _m: None, keep_state=True)
    if n:
        progress(f"  · 원본 {n}개 파일로 되돌렸습니다.")

    fresh = _fresh(ex, root, proj, progress)
    _fill(fresh, table, lang, progress)
    rep = ap.apply(fresh, lang, dry_run=False, game_root=root, progress=progress)

    # 기억해 둔 수정(한글 폰트)을 다시 겁니다.
    rep.notes += fixups.replay(root, progress)

    if drift:
        shown = ", ".join(drift[:6]) + (f" 외 {len(drift) - 6}개" if len(drift) > 6 else "")
        msg = (f"⚠  마지막 적용 뒤 다른 곳에서 고쳐진 파일 {len(drift)}개는 원본으로 "
               f"되돌아갔습니다: {shown}")
        if kept is not None:
            msg += f"\n   → 지우기 전 모습을 복사해 뒀습니다: {kept}"
        if skipped:
            msg += f"\n   → 너무 커서 복사하지 못한 것: {', '.join(skipped)}"
        msg += ("\n   → 그 수정이 필요하면 번역표에서 고쳐 넣거나(문장이면 표에서 직접 수정), "
                "복사본에서 꺼내 쓰세요")
        rep.notes.append(msg)
    return rep


def _fresh(ex, root: Path, proj, progress):
    """처음 뽑을 때 게임 코드(DLL) 글자까지 골랐다면 다시 뽑을 때도 같이 뽑습니다.
    안 그러면 그 번역이 두 번째 적용부터 조용히 빠집니다.
    RPG Maker 는 플러그인 js 글자를 처음에 골랐는지가 ``rm_js_literals`` 에 남아 있습니다."""
    return ex.extract(root, source_lang=proj.source_lang or "all",
                      include_dll=bool(proj.stats.get("extract_dll")),
                      include_js="rm_js_literals" in proj.stats,
                      progress=progress)


def _fill(fresh, table: dict, lang: str, progress) -> None:
    hit = 0
    for e in fresh.entries:
        new = table.get(e.text)
        if new:
            e.translations[lang] = new
            hit += 1
    progress(f"  · 번역표와 맞은 문장 {hit:,}개 / {len(table):,}개")
