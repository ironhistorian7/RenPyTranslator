"""그림 속 일본어 글자를 **직접 지우고 한글로 다시 쓰기**(ChatGPT 없이, 컴퓨터 안에서).

ChatGPT 는 큰 그림 한 장의 글자 한두 개는 잘 바꾸지만, 작은 라벨이 수십 개 든 설정 화면·상태창 판은 틀리기 쉽습니다.
이런 그림은 글자 자리가 정해져 있고(판 위의 라벨) 배경이 단순해서, 글자만 지우고 같은 자리에 한글을 얹으면 됩니다.

쓰는 순서
1. ``find_boxes`` — 그림에서 글자 덩어리 상자를 찾아 번호를 매기고, 번호가 적힌 확인용 그림을 만듭니다.
2. 번호별로 한글 문구를 적은 명세(``spec``)를 씁니다. 예 ``{"3": "기본 정보", "5": {"text": "현재 성욕", "align": "left"}}``
3. ``paint`` — 지정한 글자 상자만 지우고(주변 배경을 이어 채움) 한글을 같은 자리·같은 색으로 씁니다.
   항상 **원본**에서 다시 그리므로 명세를 고쳐 몇 번이고 다시 돌려도 됩니다.

글자 찾기 방식(``mode``)
* ``light`` — 어두운 판 위의 밝은(채도 낮은) 글자.  ``dark`` — 밝은 판 위의 어두운 글자.
* ``alpha`` — 투명 배경 위의 글자(테두리 포함). 지운 자리는 투명해집니다.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path


@dataclasses.dataclass
class Params:
    mode: str = "light"          # light | dark | alpha
    thresh: int = 120            # light: 이 밝기 이상 / dark: 이 밝기 이하 / alpha: 이 투명도 이상(기본 40)
    sat: int = 70                # 이보다 채도가 높으면(테두리 금색 등) 글자로 보지 않음. alpha 모드는 무시
    gap: int = 5                 # 가로로 이 간격 이내의 글자는 한 덩어리
    vgap: int = 2                # 세로로 이 간격 이내는 한 덩어리
    min_area: int = 4            # 이보다 작은 점은 버림
    region: tuple | None = None  # (x0, y0, x1, y1) 또는 그런 상자 여러 개의 목록 안에서만 찾기

    @classmethod
    def from_dict(cls, d: dict | None) -> "Params":
        d = dict(d or {})
        if "region" in d and d["region"] is not None:
            d["region"] = tuple(tuple(r) if isinstance(r, (list, tuple)) else r for r in d["region"])
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclasses.dataclass
class Box:
    n: int
    x0: int
    y0: int
    x1: int           # 끝은 포함
    y1: int
    pixels: int

    @property
    def w(self) -> int:
        return self.x1 - self.x0 + 1

    @property
    def h(self) -> int:
        return self.y1 - self.y0 + 1


def _mask(im, p: Params) -> bytearray:
    w, h = im.size
    px = im.load()
    out = bytearray(w * h)
    reg = p.region
    rects = [(0, 0, w, h)] if not reg else ([tuple(reg)] if not isinstance(reg[0], (list, tuple)) else [tuple(r) for r in reg])
    th = p.thresh if p.mode != "alpha" else (p.thresh if p.thresh != 120 else 40)
    for x0, y0, x1, y1 in rects:
        x1, y1 = min(x1, w), min(y1, h)
        for y in range(max(0, y0), y1):
            row = y * w
            for x in range(max(0, x0), x1):
                r, g, b, a = px[x, y]
                if p.mode == "alpha":
                    if a >= th:
                        out[row + x] = 1
                    continue
                if a < 128:
                    continue
                luma = (r * 299 + g * 587 + b * 114) // 1000
                if max(r, g, b) - min(r, g, b) > p.sat:
                    continue
                if (p.mode == "light" and luma >= th) or (p.mode == "dark" and luma <= th):
                    out[row + x] = 1
    return out


def find_boxes(im, p: Params | None = None) -> list[Box]:
    """글자 덩어리 상자들(왼쪽 위에서 오른쪽 아래 순서, 번호 1부터)."""
    p = p or Params()
    im = im.convert("RGBA")
    w, h = im.size
    mask = _mask(im, p)
    dil = bytearray(w * h)
    pts = [i for i in range(w * h) if mask[i]]
    for i in pts:
        y, x = divmod(i, w)
        for yy in range(max(0, y - p.vgap), min(h, y + p.vgap + 1)):
            base = yy * w
            for xx in range(max(0, x - p.gap), min(w, x + p.gap + 1)):
                dil[base + xx] = 1
    seen = bytearray(w * h)
    boxes: list[Box] = []
    for i in pts:
        if seen[i] or not dil[i]:
            continue
        stack = [i]
        seen[i] = 1
        x0 = y0 = 1 << 30
        x1 = y1 = -1
        cnt = 0
        while stack:
            j = stack.pop()
            y, x = divmod(j, w)
            if mask[j]:
                cnt += 1
                x0, x1 = min(x0, x), max(x1, x)
                y0, y1 = min(y0, y), max(y1, y)
            for yy in (y - 1, y, y + 1):
                if 0 <= yy < h:
                    for xx in (x - 1, x, x + 1):
                        if 0 <= xx < w:
                            k = yy * w + xx
                            if dil[k] and not seen[k]:
                                seen[k] = 1
                                stack.append(k)
        if cnt >= p.min_area:
            boxes.append(Box(0, x0, y0, x1, y1, cnt))
    # 위에서 아래로, 같은 줄(세로 겹침)이면 왼쪽에서 오른쪽
    boxes.sort(key=lambda b: (b.y0 + b.y1) // 2)
    lines: list[list[Box]] = []
    for b in boxes:
        for ln in lines:
            ref = ln[0]
            if abs((b.y0 + b.y1) / 2 - (ref.y0 + ref.y1) / 2) <= max(3, min(b.h, ref.h) * 0.6):
                ln.append(b)
                break
        else:
            lines.append([b])
    out: list[Box] = []
    for ln in lines:
        out += sorted(ln, key=lambda b: b.x0)
    for n, b in enumerate(out, 1):
        b.n = n
    return out


def annotate(im, boxes: list[Box], scale: int | None = None):
    """번호가 적힌 확인용 그림(확대해서 상자와 번호를 그립니다)."""
    from PIL import Image, ImageDraw, ImageFont

    base = im.convert("RGBA")
    k = scale or max(1, min(4, 1200 // max(base.width, 1)))
    bg = Image.new("RGBA", base.size, (90, 90, 90, 255))
    bg.alpha_composite(base)
    big = bg.resize((base.width * k, base.height * k), Image.NEAREST).convert("RGB")
    d = ImageDraw.Draw(big)
    try:
        font = ImageFont.load_default(size=11)
    except TypeError:
        font = ImageFont.load_default()
    for b in boxes:
        d.rectangle((b.x0 * k - 1, b.y0 * k - 1, (b.x1 + 1) * k, (b.y1 + 1) * k), outline=(255, 60, 60))
        label = str(b.n)
        tw = d.textlength(label, font=font)
        d.rectangle((b.x0 * k - 1, b.y0 * k - 12, b.x0 * k + tw + 2, b.y0 * k - 1), fill=(255, 60, 60))
        d.text((b.x0 * k + 1, b.y0 * k - 12), label, fill=(255, 255, 255), font=font)
    return big


# ---------------------------------------------------------------- 지우고 쓰기


def _bg_side(px, x: int, y: int, step: int, w: int, mode: str):
    """글자 구간 옆의 배경색: 가까운 3픽셀 중 글자와 반대로 어두운(밝은 글자일 때)/밝은 것을 고릅니다."""
    cand = [px[xx, y] for xx in (x, x + step, x + 2 * step) if 0 <= xx < w]
    if not cand:
        return None

    def luma(c):
        return c[0] * 299 + c[1] * 587 + c[2] * 114

    return min(cand, key=luma) if mode != "dark" else max(cand, key=luma)


def _erase(im, mask: bytearray, box: Box, mode: str, sat_max: int = 90, pad: int = 3) -> None:
    """상자 안의 글자 픽셀(둘레 흐릿한 테두리까지)을 지웁니다.

    같은 줄의 왼쪽·오른쪽 배경색을 이어 채웁니다.
    ``alpha`` 모드는 그냥 투명하게 만듭니다."""
    w, h = im.size
    px = im.load()
    x0, y0, x1, y1 = box.x0 - pad, box.y0 - 1, box.x1 + pad, box.y1 + 1
    if mode == "alpha":
        for y in range(max(0, y0), min(h, y1 + 1)):
            for x in range(max(0, x0), min(w, x1 + 1)):
                if px[x, y][3]:
                    px[x, y] = (0, 0, 0, 0)
        return

    # 글자 둘레 흐릿한 테두리(안티앨리어싱)까지 지우려고 가로 3·세로 2칸까지 부풀립니다
    target: set[tuple[int, int]] = set()
    for y in range(max(0, y0), min(h, y1 + 1)):
        for x in range(max(0, x0), min(w, x1 + 1)):
            if any(mask[yy * w + xx] for yy in range(max(0, y - 1), min(h, y + 2))
                   for xx in range(max(0, x - 3), min(w, x + 4))):
                r, g, b = px[x, y][:3]
                if mask[y * w + x] or max(r, g, b) - min(r, g, b) <= (sat_max + 25 if mode == "dark" else (255 if sat_max >= 200 else min(sat_max, 55))):   # 금색 테두리선 같은 것은 건드리지 않음
                    target.add((x, y))
    # 같은 줄에서 연속한 글자 구간마다, 구간 바로 왼쪽·오른쪽 배경색을 이어 채웁니다(세로 이웃은 쓰지 않아 테두리선이 번지지 않음)
    rows: dict[int, list[int]] = {}
    for (x, y) in target:
        rows.setdefault(y, []).append(x)
    for y, xs in rows.items():
        xs.sort()
        runs, start, prev = [], xs[0], xs[0]
        for x in xs[1:]:
            if x != prev + 1:
                runs.append((start, prev))
                start = x
            prev = x
        runs.append((start, prev))
        for a, b in runs:
            L = _bg_side(px, a - 1, y, -1, w, mode)
            R = _bg_side(px, b + 1, y, 1, w, mode)
            L = L or R
            R = R or L
            if L is None:
                continue
            n = b - a + 2
            for i, x in enumerate(range(a, b + 1), 1):
                t = i / n
                px[x, y] = tuple(int(L[k] * (1 - t) + R[k] * t) for k in range(3)) + (px[x, y][3],)

def _text_color(im, mask: bytearray, box: Box, mode: str):
    w = im.size[0]
    px = im.load()
    cols = []
    for y in range(box.y0, box.y1 + 1):
        for x in range(box.x0, box.x1 + 1):
            if mask[y * w + x]:
                cols.append(px[x, y][:3])
    if not cols:
        return (255, 255, 255)
    cols.sort(key=lambda c: (c[0] * 299 + c[1] * 587 + c[2] * 114))
    pick = cols[-max(1, len(cols) // 3):] if mode in ("light", "alpha") else cols[:max(1, len(cols) // 3)]
    return tuple(sorted(c[k] for c in pick)[len(pick) // 2] for k in range(3))


def _font(size: int, bold: bool):
    from PIL import ImageFont

    from . import sjiskorean

    cands = []
    if bold:
        for d in sjiskorean.font_dirs():
            for name in ("malgunbd.ttf", "NanumGothicBold.ttf", "NanumBarunGothicBold.ttf"):
                if (d / name).is_file():
                    cands.append(d / name)
    cands.append(sjiskorean.find_base_font())
    for p in cands:
        try:
            return ImageFont.truetype(str(p), size)
        except OSError:
            continue
    raise FileNotFoundError("한글 글꼴을 찾지 못했습니다")


SPEC_DIR = "specs"


def spec_files(work: Path) -> list[Path]:
    return sorted((Path(work) / SPEC_DIR).glob("*.json"))


def paint_all(root: Path, names: list[str] | None = None, progress=None) -> list[tuple[str, Path]]:
    """``image_work/specs/*.json`` 의 명세대로 그린 그림을 ``<게임>_loc/images/…`` 에 씁니다(미리보기는 ``preview/``).

    그림 원본은 ``image_work/candidates/`` 에서 읽으므로 몇 번을 돌려도 같습니다."""
    import json

    from PIL import Image

    from . import imagescan

    say = progress or (lambda _m: None)
    work = imagescan.work_dir(root)
    dest_root = Path(root).parent / f"{Path(root).name}_loc" / "images"
    done = []
    for f in spec_files(work):
        if names and f.stem not in names:
            continue
        spec = json.loads(f.read_text(encoding="utf-8-sig"))
        src = work / "candidates" / spec["image"]
        if not src.is_file():
            raise FileNotFoundError(f"{f.name}: 원본 그림이 없습니다({spec['image']}) — 먼저 images 로 모으세요")
        out = paint(src, spec)
        dest = dest_root / spec["image"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        out.save(dest)
        prev = work / "preview" / f"{f.stem}.png"
        prev.parent.mkdir(parents=True, exist_ok=True)
        k = int(spec.get("preview", 2))
        bg = Image.new("RGBA", out.size, (90, 90, 90, 255))
        bg.alpha_composite(out)
        bg.resize((out.width * k, out.height * k), Image.NEAREST).convert("RGB").save(prev)
        n = sum(len(ps.get("labels", {})) for ps in (spec.get("passes") or [spec]))
        say(f"  · {spec['image']}  ({n}곳)")
        done.append((spec["image"], dest))
    return done


def paint(src, spec: dict):
    """``src``(그림 파일 또는 PIL 그림)의 글자 상자들을 명세대로 한글로 바꾼 그림을 돌려줍니다.

    ``spec``: ``{"mask": {...Params...}, "labels": {"번호": "한글" | {"text","align","color","size","max_w","bold","dy","dx","stroke"}}}``
    상자 번호는 ``find_boxes`` 가 붙인 번호입니다(``"6+7"`` 은 두 상자를 한 자리로 합칩니다). 명세에 없는 상자는 건드리지
    않습니다. 글자 찾기 설정이 서로 다른 곳이 한 그림에 섞여 있으면 ``{"passes": [{"mask":…, "labels":…}, …]}`` 로 나눠 씁니다."""
    from PIL import Image, ImageDraw

    im = Image.open(src).convert("RGBA") if not hasattr(src, "load") else src.convert("RGBA")
    passes = spec.get("passes") or [{"mask": spec.get("mask"), "labels": spec.get("labels", {})}]
    out = im.copy()
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for ps in passes:
        p = Params.from_dict(ps.get("mask"))
        by = {b.n: b for b in find_boxes(im, p)}
        mask = _mask(im, p)
        for key, val in ps.get("labels", {}).items():
            parts = []
            for k in str(key).split("+"):            # "6+7" = 떨어진 글자 '決　定' 같은 것을 한 자리로
                b = by.get(int(k))
                if b is None:
                    raise KeyError(f"글자 상자 {k} 번이 없습니다(상자는 {len(by)}개)")
                parts.append(b)
            b = parts[0] if len(parts) == 1 else Box(
                parts[0].n, min(q.x0 for q in parts), min(q.y0 for q in parts),
                max(q.x1 for q in parts), max(q.y1 for q in parts), sum(q.pixels for q in parts))
            _draw_label(im, out, draw, mask, p, b, {"text": val} if isinstance(val, str) else dict(val))
    out.alpha_composite(layer)
    return out


def _draw_label(im, out, draw, mask, p: Params, b: Box, opt: dict) -> None:
    text = opt["text"]
    _erase(out, mask, b, p.mode, p.sat)
    color = tuple(opt["color"]) if opt.get("color") else _text_color(im, mask, b, p.mode)
    bold = opt.get("bold", True)
    avail = opt.get("max_w") or int(b.w * opt.get("grow", 1.15))
    size = opt.get("size") or max(8, int(round(b.h * 1.0)))
    font = _font(size, bold)
    stroke = int(opt.get("stroke", 0))
    while size > 8 and not opt.get("size") and draw.textlength(text, font=font) > avail:
        size -= 1
        font = _font(size, bold)
    tw = draw.textlength(text, font=font)
    asc, desc = font.getmetrics()
    align = opt.get("align", "left")
    x = b.x0 if align == "left" else (b.x1 + 1 - tw if align == "right" else b.x0 + (b.w - tw) / 2)
    y = b.y0 + (b.h - (asc + desc)) / 2 + opt.get("dy", 0) + 0.5
    x += opt.get("dx", 0)
    kw = {"stroke_width": stroke, "stroke_fill": tuple(opt.get("stroke_color", (0, 0, 0)))} if stroke else {}
    draw.text((x, y), text, font=font, fill=color + (255,), **kw)