"""그림 번역 2단계: ChatGPT 에 보낼 파일 만들기 → 받아 온 그림을 게임 크기로 맞춰 겹쳐 쓰기 폴더에 넣기.

1단계(``imagescan``)로 고른 그림(``image_work/selected``)을 ChatGPT(웹, API 아님)에 한 장씩 올려 한글 그림을
받아 오면, 여기서 **자동으로** 다음을 합니다(손으로 하면 장마다 실수가 납니다).

* 보낼 때: 작은 그림은 크게 키우고, 투명한 부분은 회색으로 깔아 글자가 잘 보이게 합니다.
* 받아 올 때: ChatGPT 는 여백을 흰색으로 덧붙이고 비율을 조금 바꾸므로, 여백을 자르고 원래 크기로 맞춥니다.
  투명도는 원본의 것을 되살립니다(배경이 투명한 글자 그림은 배경색을 지워 만듭니다).
* 결과는 ``<게임폴더>_loc/images/<Data 아래 경로>`` 에 놓입니다 — 적용할 때 게임에 끼워 넣어지는 바로 그 자리입니다.
* ``compare.png`` 에 원본과 새 그림을 나란히 보여 줘서, 어긋난 것을 눈으로 골라낼 수 있게 합니다.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

from . import imagescan

SEND_DIR = "to_chatgpt"
BG = (128, 128, 128)              # 보낼 때 투명한 곳에 깔 색(밝은 글자·어두운 글자 모두 보입니다)
TARGET = 800                      # 보낼 그림의 긴 변 목표(작은 그림은 키웁니다)
MAX_SCALE = 8
PROMPT_FILE = "prompt.txt"
GLOSSARY = "glossary.txt"          # 줄마다 `日本語 → 한국어` (직접 적어 두면 지시문에 들어갑니다)


def _scale(w: int, h: int) -> int:
    return max(1, min(MAX_SCALE, round(TARGET / max(w, h))))


def _load_selected(root: Path) -> tuple[Path, list[imagescan.Item]]:
    work, idx = imagescan.load_index(root)
    items = [imagescan.Item(**d) for d in idx["items"]]
    return work, [i for i in items if i.selected and not i.dup_of]


def _cand_path(work: Path, rel: str) -> Path:
    return work / "candidates" / (rel.split("/", 1)[1] if rel.lower().startswith("data/") else rel)


def _safe(name: str) -> str:
    return re.sub(r"[^\w.\-]", "_", name)


def export(root: Path, *, note: str = "") -> Path:
    """고른 그림을 ``image_work/to_chatgpt`` 에 번호_이름.png 로 놓고 지시문 ``prompt.txt`` 를 씁니다."""
    from PIL import Image

    work, items = _load_selected(root)
    if not items:
        raise imagescan.ScanError("고른 그림이 없습니다 — 먼저 images-pick 으로 번호를 고르세요")
    out = work / SEND_DIR
    if out.exists():
        for p in out.glob("*"):
            p.unlink()
    out.mkdir(parents=True, exist_ok=True)
    lines = []
    for it in items:
        im = Image.open(_cand_path(work, it.rel)).convert("RGBA")
        k = _scale(im.width, im.height)
        if k > 1:
            im = im.resize((im.width * k, im.height * k), Image.NEAREST)
        flat = Image.new("RGBA", im.size, BG + (255,))
        flat.alpha_composite(im)
        stem = _safe(it.rel.rsplit("/", 1)[-1].rsplit(".", 1)[0])
        name = f"{it.n:03d}_{stem}.png"
        flat.convert("RGB").save(out / name)
        lines.append(f"- {name}  (원래 크기 {it.width}x{it.height}, {k}배 확대본)")
    gl = ""
    g = work / GLOSSARY
    if g.is_file():
        gl = "\n번역 표(이 말은 반드시 이대로)\n" + g.read_text(encoding="utf-8-sig").strip() + "\n"
    (out / PROMPT_FILE).write_text(PROMPT.format(files="\n".join(lines), glossary=gl, note=(note.strip() + "\n") if note.strip() else ""),
                                   encoding="utf-8")
    return out


PROMPT = """\
[아래 지시문을 ChatGPT 에 붙여 넣고, 그림 파일을 **한 번에 한 장씩** 첨부하세요. (같은 대화에서 이어서 해도 됩니다)]

첨부한 이미지는 일본어 게임의 화면 그림(버튼·라벨·상태창 등)입니다. 이미지 안의 일본어 글자를 한국어로 바꿔 주세요.

지켜야 할 것
1. 글자 말고는 아무것도 바꾸지 마세요. 배경, 테두리, 아이콘, 칸의 모양·위치·색은 그대로 두세요.
2. 이미지 크기와 가로세로 비율을 바꾸지 마세요. 자르거나 여백을 더하지 마세요.
3. 글자의 위치(가운데/왼쪽 정렬), 크기, 굵기, 색, 테두리·그림자를 원본과 최대한 같게 하세요. 글자가 원래 자리를 넘치지 않게 하세요(길면 글자를 조금 줄이세요).
4. 한글 철자를 정확히 쓰세요. 뜻은 게임 화면에 맞는 짧고 자연스러운 말로 옮기세요.
5. 숫자·영어·기호(G, ml, ?, / 등)는 그대로 두세요. 글자가 없는 그림이면 그대로 돌려주세요.
6. 회색 배경(128,128,128)은 투명한 곳을 표시한 것입니다. 그대로 두세요.
{glossary}{note}
결과는 PNG 이미지 한 장으로만 주세요.

보낼 파일
{files}
"""


# ---------------------------------------------------------------- 받아 오기


def _near(a, b, tol: int) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a[:3], b[:3]))


def crop_margin(im, sent_corners_white: bool):
    """ChatGPT 가 덧붙인 **흰 여백**을 자릅니다. 원본 모서리가 원래 흰색이면 자르지 않습니다."""
    from PIL import Image, ImageChops

    rgb = im.convert("RGB")
    c = [rgb.getpixel(p) for p in ((0, 0), (rgb.width - 1, 0), (0, rgb.height - 1), (rgb.width - 1, rgb.height - 1))]
    if sent_corners_white or not all(_near(p, (255, 255, 255), 14) for p in c):
        return im
    diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, (255, 255, 255))).convert("L").point(
        lambda v: 255 if v > 24 else 0)
    box = diff.getbbox()
    return im.crop(box) if box else im


def _key_alpha(rgb, bg=BG):
    """배경색(회색)과 얼마나 다른지로 투명도를 만듭니다(투명 배경의 글자 그림용)."""
    from PIL import Image, ImageChops

    d = ImageChops.difference(rgb, Image.new("RGB", rgb.size, bg))
    r, g, b = d.split()
    m = ImageChops.lighter(ImageChops.lighter(r, g), b)
    return m.point(lambda v: 0 if v < 10 else min(255, (v - 10) * 6))


def fit(new_bytes: bytes, original_path: Path) -> tuple["Image.Image", str]:
    """받은 그림을 원본 크기·투명도로 맞춥니다. ``(그림, 메모)`` — 메모가 있으면 눈으로 확인하세요."""
    from PIL import Image

    orig = Image.open(original_path).convert("RGBA")
    got = Image.open(io.BytesIO(new_bytes))
    got.load()
    memo = []
    has_alpha = got.mode in ("RGBA", "LA") and got.getchannel("A").getextrema()[0] < 250
    flat0 = Image.new("RGBA", orig.size, BG + (255,))
    flat0.alpha_composite(orig)
    w, h = orig.size
    corners_white = all(_near(flat0.getpixel(p)[:3], (255, 255, 255), 14)
                        for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)))
    rgba = got.convert("RGBA")
    if not has_alpha:
        rgba = crop_margin(rgba, corners_white)
    ratio_old, ratio_new = w / h, rgba.width / rgba.height
    if abs(ratio_new / ratio_old - 1) > 0.12:
        memo.append(f"가로세로 비율이 많이 다름({ratio_new:.2f} ↔ {ratio_old:.2f})")
    rgba = rgba.resize((w, h), Image.LANCZOS)
    if has_alpha:
        return rgba, "; ".join(memo)
    # 투명도: 원본이 거의 불투명이면 원본 것을, 아니면(글자만 있는 그림) 배경색을 지워 만듭니다
    a = orig.getchannel("A")
    opaque = a.point(lambda v: 255 if v > 8 else 0).histogram()[255] / (w * h)
    if opaque >= 0.9 or opaque == 0:
        rgba.putalpha(a)
    else:
        rgba.putalpha(_key_alpha(rgba.convert("RGB")))
        memo.append("배경색을 지워 투명하게 만듦 — 글자 가장자리 확인")
    return rgba, "; ".join(memo)


def _match(name: str, items: list[imagescan.Item]):
    m = re.match(r"\s*(\d{1,4})[_\-. ]", name)
    if m:
        n = int(m.group(1))
        for it in items:
            if it.n == n:
                return it
    stem = re.sub(r"[^\w]", "", name.rsplit(".", 1)[0]).lower()
    for it in items:
        s = re.sub(r"[^\w]", "", it.rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]).lower()
        if s and s in stem:
            return it
    return None


def import_results(root: Path, folder: Path, *, progress=None) -> dict:
    """``folder`` 의 그림을 맞춰서 ``<게임>_loc/images/…`` 에 넣고 ``compare.png`` 를 만듭니다.

    돌려주는 값: ``{"done": [(번호, 경로, 메모)], "unmatched": [파일 이름], "missing": [번호]}``"""
    say = progress or (lambda _m: None)
    work, items = _load_selected(root)
    dest_root = Path(root).parent / f"{Path(root).name}_loc" / "images"
    done, unmatched, seen = [], [], set()
    rows = []
    for p in sorted(Path(folder).glob("*")):
        if p.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".bmp") or p.name == PROMPT_FILE:
            continue
        it = _match(p.name, items)
        if it is None:
            unmatched.append(p.name)
            continue
        src = _cand_path(work, it.rel)
        try:
            img, memo = fit(p.read_bytes(), src)
        except Exception as exc:                 # noqa: BLE001 — 한 장이 깨져도 나머지는 계속
            unmatched.append(f"{p.name} ({exc})")
            continue
        rel = it.rel.split("/", 1)[1] if it.rel.lower().startswith("data/") else it.rel
        dest = dest_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.suffix.lower() in (".jpg", ".jpeg"):
            img.convert("RGB").save(dest, quality=95)
        else:
            img.save(dest, "BMP" if dest.suffix.lower() == ".bmp" else "PNG")
        seen.add(it.n)
        done.append((it.n, it.rel, memo))
        rows.append((it, src, img, memo))
        say(f"  · {it.n:03d} {it.rel}" + (f"  ※ {memo}" if memo else ""))
    if rows:
        _compare(work / "compare.png", rows)
    (work / "imported.json").write_text(json.dumps(
        [{"n": n, "rel": r, "memo": m} for n, r, m in done], ensure_ascii=False, indent=1), encoding="utf-8")
    return {"done": done, "unmatched": unmatched,
            "missing": [i.n for i in items if i.n not in seen]}


def _compare(path: Path, rows) -> None:
    from PIL import Image, ImageDraw, ImageFont

    font = None
    for cand in (r"C:\Windows\Fonts\malgun.ttf", "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
                 "/System/Library/Fonts/AppleSDGothicNeo.ttc"):
        try:
            font = ImageFont.truetype(cand, 16)
            break
        except OSError:
            continue
    if font is None:                          # 한글 글꼴이 없으면 메모 대신 영어 표시만
        try:
            font = ImageFont.load_default(size=16)
        except TypeError:
            font = ImageFont.load_default()
    cw, ch = 520, 150
    sheet = Image.new("RGB", (cw * 2 + 70, ch * len(rows)), (30, 30, 30))
    d = ImageDraw.Draw(sheet)

    def paste(im, x, y):
        s = min((cw - 10) / im.width, (ch - 10) / im.height, 4.0)
        im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))),
                       Image.LANCZOS if s < 1 else Image.NEAREST)
        bg = Image.new("RGB", (cw, ch - 4), (90, 90, 90))
        bg.paste(im, ((cw - im.width) // 2, (ch - 4 - im.height) // 2), im)
        sheet.paste(bg, (x, y))

    for k, (it, src, img, memo) in enumerate(rows):
        y = k * ch
        d.text((6, y + 6), f"{it.n}", fill=(255, 220, 80), font=font)
        paste(Image.open(src).convert("RGBA"), 60, y + 2)
        paste(img, 60 + cw + 4, y + 2)
        if memo:
            d.text((64, y + ch - 20), "※ " + memo[:90], fill=(255, 120, 120), font=font)
    sheet.save(path)
