"""게임 안의 그림을 모아 **글자가 든 그림 후보**를 고르는 길(그림 번역 1단계).

글자 번역으로는 못 바꾸는 글자가 있습니다 — 그림 파일에 박힌 상태창 라벨, 타이틀 메뉴, 버튼 같은 것들입니다.
이것들은 한글로 다시 그려서 끼워 넣어야 하는데(`wolf.image_overrides`), 게임 하나에 그림이 수천 장이라
**어느 그림에 글자가 있는지**부터 골라야 합니다. 이 모듈이 하는 일:

1. 게임 안의 그림(울프 묶음 ``.wolf`` 안 + 풀린 폴더 + 다른 엔진의 그림 파일)을 모두 찾아 크기를 잽니다.
2. 묶음(폴더)별로 "글자가 있을 법한가" 를 매깁니다(CG·서 있는 그림·지도 조각 등은 보통 아닙니다).
3. 후보를 번호 붙은 **미리보기 표**(한 장에 30개)로 만듭니다. 사람이 훑거나, ChatGPT 에 올려
   "일본어 글자가 든 번호만 알려 달라" 고 하면 됩니다.
4. 골라낸 번호를 ``selected/`` 폴더로 모읍니다(다음 단계: 번역 묶음 만들기).

게임 파일은 읽기만 합니다. 결과는 ``<게임폴더>_loc/image_work/`` 아래에 둡니다.
"""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import io
import json
import re
import shutil
import struct
from pathlib import Path

from . import wolfarc

WORK_DIR = "image_work"
INDEX = "index.json"
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp")
BACKUP_DIR = "__gameloc_backup__"
BIG_ARCHIVE = 100 * 1024 * 1024        # 이보다 큰 묶음은 소리·큰 그림이라 기본으로 건너뜁니다
MAX_W, MAX_H = 2000, 1100              # 이보다 큰 그림은 화면 UI 가 아닌 CG 로 봅니다(상태창 같은 세로로 긴 판도 들어옵니다)
MIN_SIDE = 8                           # 이보다 작은 그림(점·구분선)은 글자가 못 듭니다
PER_SHEET = 30
COLS = 6
CELL_W, CELL_H = 260, 170

# 그림 묶음 이름으로 보는 "글자가 있을 법한가". 이름 일부(소문자)가 들어 있으면 해당 판정.
_UNLIKELY = ("cg", "tachi", "chip", "tile", "enemy", "face", "bgm", "voice", "bg", "back",
             "effect", "anim", "walk", "sprite", "character", "chara", "battler", "tileset")
_LIKELY = ("picture", "system", "basicdata", "ui", "menu", "title", "window", "button",
           "hud", "gui", "font", "text", "logo", "manpu", "pictures")


class ScanError(Exception):
    pass


@dataclasses.dataclass
class Item:
    n: int                  # 번호(미리보기 표에 적힌 것)
    rel: str                # 게임 폴더 기준 경로 (``Data/Picture/hud.png``)
    group: str              # 묶음/폴더 이름 (``Picture``)
    width: int
    height: int
    size: int               # 바이트
    dup_of: int = 0         # 내용이 같은 앞 번호(있으면 번역도 한 번이면 됩니다)
    sheet: int = 0
    selected: bool = False

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class Group:
    name: str
    count: int = 0
    small: int = 0          # MAX_W×MAX_H 이하 개수
    suggested: bool = False
    skipped: str = ""       # 건너뛴 까닭(큰 묶음 등)
    source: str = ""        # 묶음 파일 경로(풀린 폴더면 폴더)


@dataclasses.dataclass
class ScanResult:
    root: Path
    items: list[Item]
    groups: list[Group]
    errors: list[str]

    @property
    def work(self) -> Path:
        return work_dir(self.root)


def work_dir(root: Path) -> Path:
    root = Path(root)
    return root.parent / f"{root.name}_loc" / WORK_DIR


# ---------------------------------------------------------------- 그림 크기·판정


def image_size(data: bytes) -> tuple[int, int] | None:
    """PNG/BMP/JPEG 머리에서 가로·세로. 못 읽으면 ``None``."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"BM" and len(data) >= 26:
        w, h = struct.unpack("<ii", data[18:26])
        return abs(w), abs(h)
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            (seg,) = struct.unpack(">H", data[i + 2:i + 4])
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            i += 2 + seg
    return None


def suggest_group(name: str) -> bool:
    """묶음 이름만 보고 "글자가 있을 법한 묶음" 인지(대략)."""
    low = name.lower()
    if any(k in low for k in _UNLIKELY):
        return False
    return any(k in low for k in _LIKELY)


# ---------------------------------------------------------------- 모으기


def _wolf_archives(root: Path) -> list[tuple[str, Path]]:
    """``(묶음 이름, 파일)`` — **백업의 원본을 먼저** 봅니다(번역 후엔 풀려 있을 수 있어서)."""
    seen: dict[str, Path] = {}
    for base in (root / BACKUP_DIR, root):
        for p in sorted((base / "Data").glob("*.wolf")) if (base / "Data").is_dir() else []:
            seen.setdefault(p.stem.lower(), p)
    return sorted(((p.stem, p) for p in seen.values()), key=lambda t: t[0].lower())


def _loose_images(root: Path):
    """풀린 폴더/다른 엔진의 그림 파일. ``(상대 경로, 파일)``. 백업·번역 작업 폴더는 건너뜁니다."""
    skip = {BACKUP_DIR.lower(), "node_modules", ".git"}
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in IMAGE_EXTS:
            continue
        parts = p.relative_to(root).parts
        if any(x.lower() in skip for x in parts):
            continue
        yield "/".join(parts), p


def _group_of(rel: str) -> str:
    """상대 경로에서 묶음 이름: ``Data/Picture/x.png`` → ``Picture``, ``img/pictures/x.png`` → ``img/pictures``."""
    parts = rel.split("/")[:-1]
    if parts and parts[0].lower() == "data" and len(parts) > 1:
        return parts[1]
    return "/".join(parts[:2]) or "(루트)"


def scan(root: Path, *, groups: list[str] | None = None, include_big: bool = False,
         progress=None) -> ScanResult:
    """게임의 그림을 찾아 크기를 잽니다. 그림 내용은 ``work/candidates`` 로 꺼내지 않습니다(``build`` 가 합니다).

    크기까지 읽는 것은 **후보 묶음**(``groups`` 가 있으면 그것들, 없으면 이름으로 보아 글자가 있을 법한 묶음)뿐입니다.
    나머지 묶음은 그림 수만 세고 건너뜁니다(CG 수천 장을 푸느라 시간을 쓰지 않으려고).
    ``include_big`` 가 아니면 100 MB 넘는 묶음은 열지 않습니다(소리·큰 CG)."""
    say = progress or (lambda _m: None)
    root = Path(root)
    wanted = {g.lower() for g in groups} if groups else None

    def is_wanted(name: str) -> bool:
        return name.lower() in wanted if wanted is not None else suggest_group(name)

    if not root.is_dir():
        raise ScanError(f"폴더가 없습니다: {root}")
    items: list[Item] = []
    found: dict[str, Group] = {}
    errors: list[str] = []
    seen_rel: set[str] = set()

    def group_of(name: str, source: str = "") -> Group:
        g = found.get(name)
        if g is None:
            g = found[name] = Group(name, suggested=suggest_group(name), source=source)
        return g

    def add(rel: str, name: str, data_fn) -> None:
        """``data_fn`` 은 후보 묶음일 때만 부릅니다(그림을 푸는 것이 비싸서)."""
        low = rel.lower()
        if low in seen_rel:
            return
        seen_rel.add(low)
        g = group_of(name)
        g.count += 1
        if not is_wanted(name):
            return
        wh = image_size(data_fn())
        if wh is None:
            g.count -= 1
            return
        if wh[0] <= MAX_W and wh[1] <= MAX_H:
            g.small += 1
        items.append(Item(0, rel, name, wh[0], wh[1], 0))

    for name, path in _wolf_archives(root):
        g = group_of(name, str(path))
        big = path.stat().st_size > BIG_ARCHIVE
        if name.lower() in ("mapdata", "evtext", "bgm", "se") or name.lower().startswith("voice"):
            continue                          # 글자·소리 묶음
        if big and not include_big:
            g.skipped = f"{path.stat().st_size // (1024 * 1024)}MB — 큰 묶음이라 안 열었습니다"
            continue
        say(f"  · {path.name} 여는 중…")
        try:
            arc = wolfarc.WolfArchive(path)
        except wolfarc.WolfArcError as exc:
            errors.append(f"{path.name}: {exc}")
            g.skipped = f"못 읽음({exc})"
            continue
        for f in arc.files:
            if not f.path.lower().endswith(IMAGE_EXTS):
                continue
            try:
                add(f"Data/{name}/{f.path}", name, lambda f=f: arc.read(f))
            except Exception as exc:          # noqa: BLE001 — 한 장이 깨져도 나머지는 계속
                errors.append(f"{path.name}/{f.path}: {exc}")
        del arc

    for rel, p in _loose_images(root):
        try:
            add(rel, _group_of(rel), p.read_bytes)
        except OSError as exc:
            errors.append(f"{rel}: {exc}")

    for g in found.values():
        g.suggested = is_wanted(g.name)
        if not g.suggested and not g.skipped:
            g.skipped = "이름으로 보아 글자가 없을 묶음 — 크기는 안 쟀습니다 (-g 로 지정하면 봅니다)"
    items.sort(key=lambda i: (i.group.lower(), i.rel.lower()))
    out = sorted((g for g in found.values() if g.count or g.skipped), key=lambda g: g.name.lower())
    return ScanResult(root, items, out, errors)


def pick_candidates(res: ScanResult, *, max_w: int = MAX_W, max_h: int = MAX_H) -> list[Item]:
    """미리보기 표에 올릴 후보: 스캔한(후보 묶음의) 그림 중 너무 작거나(점·구분선) 큰(CG) 것을 뺀 것.
    번호는 1부터 매깁니다. 내용이 같은 그림은 ``build`` 가 ``dup_of`` 로 잇습니다."""
    out: list[Item] = []
    for it in res.items:
        if it.width > max_w or it.height > max_h or min(it.width, it.height) < MIN_SIDE:
            continue
        out.append(it)
    for i, it in enumerate(out, 1):
        it.n = i
    return out


# ---------------------------------------------------------------- 미리보기 표


def build(res: ScanResult, items: list[Item], *, progress=None, per_sheet: int = PER_SHEET) -> Path:
    """후보 그림을 ``candidates/`` 로 꺼내고, 번호 붙은 미리보기 표 ``sheets/sheet_NN.png`` 와
    ``index.json``·``index.csv``·``triage_prompt.txt`` 를 만듭니다. 만든 작업 폴더를 돌려줍니다."""
    from PIL import Image, ImageDraw, ImageFont

    say = progress or (lambda _m: None)
    work = res.work
    # 다시 만들어도 사람이 적어 둔 것(용어표 glossary.txt, 글자 바꾸기 명세 specs/)은 남기고 만들어지는 것만 지웁니다
    for name in ("candidates", "sheets", "selected", "boxes", "preview", "to_chatgpt"):
        if (work / name).exists():
            shutil.rmtree(work / name)
    for name in (INDEX, "index.csv", "triage_prompt.txt", "compare.png", "imported.json"):
        if (work / name).exists():
            (work / name).unlink()
    (work / "candidates").mkdir(parents=True, exist_ok=True)
    (work / "sheets").mkdir(parents=True, exist_ok=True)

    # 아래 둘은 묶음마다 한 번만 열도록 같은 묶음 그림을 모아 읽습니다.
    arcs: dict[str, wolfarc.WolfArchive | None] = {}
    by_hash: dict[str, int] = {}
    loaded: list[tuple[Item, bytes]] = []
    for it in items:
        data = None
        if it.rel.lower().startswith("data/"):
            stem = it.rel.split("/")[1]
            if stem.lower() not in arcs:
                path = next((p for n, p in _wolf_archives(res.root) if n.lower() == stem.lower()), None)
                try:
                    arcs[stem.lower()] = wolfarc.WolfArchive(path) if path else None
                except wolfarc.WolfArcError:
                    arcs[stem.lower()] = None
            arc = arcs[stem.lower()]
            if arc is not None:
                try:
                    data = arc.read(it.rel.split("/", 2)[2])
                except KeyError:
                    data = None
        if data is None:
            for base in (res.root / BACKUP_DIR, res.root):
                p = base / it.rel
                if p.is_file():
                    data = p.read_bytes()
                    break
        if data is None:
            res.errors.append(f"{it.rel}: 원본을 못 읽음")
            continue
        it.size = len(data)
        h = hashlib.sha1(data).hexdigest()
        if h in by_hash:
            it.dup_of = by_hash[h]
        else:
            by_hash[h] = it.n
        dest = work / "candidates" / it.rel.split("/", 1)[1] if it.rel.lower().startswith("data/") \
            else work / "candidates" / it.rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        loaded.append((it, data))
    arcs.clear()

    uniq = [(it, d) for it, d in loaded if not it.dup_of]
    try:
        font = ImageFont.load_default(size=18)
        small = ImageFont.load_default(size=12)
    except TypeError:                          # 아주 옛 Pillow
        font = small = ImageFont.load_default()

    def checker(w: int, h: int) -> "Image.Image":
        im = Image.new("RGB", (w, h), (70, 70, 70))
        d = ImageDraw.Draw(im)
        for y in range(0, h, 12):
            for x in range(0, w, 12):
                if (x // 12 + y // 12) % 2:
                    d.rectangle((x, y, x + 11, y + 11), fill=(98, 98, 98))
        return im

    sheets = 0
    for s in range(0, len(uniq), per_sheet):
        chunk = uniq[s:s + per_sheet]
        rows = (len(chunk) + COLS - 1) // COLS
        sheet = Image.new("RGB", (COLS * CELL_W, rows * CELL_H), (30, 30, 30))
        draw = ImageDraw.Draw(sheet)
        sheets += 1
        for k, (it, data) in enumerate(chunk):
            it.sheet = sheets
            x0, y0 = (k % COLS) * CELL_W, (k // COLS) * CELL_H
            cell = checker(CELL_W - 6, CELL_H - 26)
            try:
                im = Image.open(io.BytesIO(data))
                im.load()
                im = im.convert("RGBA")
                scale = min((CELL_W - 10) / im.width, (CELL_H - 30) / im.height, 4.0)
                if scale != 1:
                    im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))),
                                   Image.LANCZOS if scale < 1 else Image.NEAREST)
                cell.paste(im, ((cell.width - im.width) // 2, (cell.height - im.height) // 2), im)
            except Exception:                  # noqa: BLE001 — 못 여는 그림은 빈 칸으로
                pass
            sheet.paste(cell, (x0 + 3, y0 + 3))
            draw.rectangle((x0 + 3, y0 + CELL_H - 23, x0 + CELL_W - 4, y0 + CELL_H - 3), fill=(15, 15, 15))
            draw.text((x0 + 8, y0 + CELL_H - 23), f"{it.n}", fill=(255, 220, 80), font=font)
            name = it.rel.rsplit("/", 1)[-1]
            draw.text((x0 + 58, y0 + CELL_H - 19), f"{name[:26]}  {it.width}x{it.height}",
                      fill=(190, 190, 190), font=small)
        sheet.save(work / "sheets" / f"sheet_{sheets:02d}.png")
        say(f"  · 미리보기 표 {sheets}/{(len(uniq) + per_sheet - 1) // per_sheet}")
    for it, _d in loaded:                      # 같은 그림은 원본과 같은 표에 둡니다
        if it.dup_of:
            first = next((x for x, _ in loaded if x.n == it.dup_of), None)
            it.sheet = first.sheet if first else 0

    kept = [it for it, _d in loaded]
    (work / INDEX).write_text(json.dumps(
        {"game": res.root.name, "sheets": sheets, "items": [i.as_dict() for i in kept]},
        ensure_ascii=False, indent=1), encoding="utf-8")
    with open(work / "index.csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["번호", "표", "경로", "묶음", "가로", "세로", "같은그림"])
        for i in kept:
            w.writerow([i.n, i.sheet, i.rel, i.group, i.width, i.height, i.dup_of or ""])
    (work / "triage_prompt.txt").write_text(TRIAGE_PROMPT, encoding="utf-8")
    return work


TRIAGE_PROMPT = """\
올린 이미지는 게임에 들어 있는 그림들을 번호를 붙여 모아 놓은 '미리보기 표'입니다.
칸마다 왼쪽 아래 노란 숫자가 번호입니다. (어두운 격자무늬는 투명한 부분입니다)

할 일: 그림 안에 **일본어(한자·히라가나·가타카나) 글자가 그려져 있는 칸의 번호만** 골라 알려 주세요.
- 캐릭터 그림·배경·무늬·효과처럼 글자가 없는 칸은 빼세요.
- 숫자나 영어 알파벳만 있는 칸도 빼세요.
- 글자가 있는지 애매하면 포함하고 '?' 를 붙이세요.

답은 이런 모양으로만 주세요(설명 없이):
번호: 3, 7, 12, 15?, 21
"""


# ---------------------------------------------------------------- 고른 번호 모으기


def parse_numbers(text: str) -> list[int]:
    """``"3, 7, 12-15, 21?"`` → ``[3, 7, 12, 13, 14, 15, 21]``. 숫자가 아닌 글은 무시합니다."""
    out: list[int] = []
    for a, b in re.findall(r"(\d+)\s*(?:[-~–]\s*(\d+))?", text):
        lo = int(a)
        hi = int(b) if b else lo
        if hi < lo or hi - lo > 5000:
            continue
        out.extend(range(lo, hi + 1))
    return sorted(set(out))


def load_index(root: Path) -> tuple[Path, dict]:
    work = work_dir(root)
    f = work / INDEX
    if not f.is_file():
        raise ScanError("먼저 그림 모으기(images)를 실행하세요 — 작업 폴더가 없습니다")
    return work, json.loads(f.read_text(encoding="utf-8"))


def select(root: Path, numbers: list[int]) -> list[Item]:
    """번호의 그림을 ``selected/`` 로 복사하고 ``index.json`` 에 표시합니다(같은 그림도 함께)."""
    work, idx = load_index(root)
    want = set(numbers)
    items = [Item(**d) for d in idx["items"]]
    sel_dir = work / "selected"
    if sel_dir.exists():
        shutil.rmtree(sel_dir)
    chosen: list[Item] = []
    for it in items:
        it.selected = it.n in want or (it.dup_of in want)
        if not it.selected:
            continue
        src = work / "candidates" / (it.rel.split("/", 1)[1] if it.rel.lower().startswith("data/") else it.rel)
        if src.is_file():
            dest = sel_dir / src.relative_to(work / "candidates")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            chosen.append(it)
    idx["items"] = [i.as_dict() for i in items]
    (work / INDEX).write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    return chosen
