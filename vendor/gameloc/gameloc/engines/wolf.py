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

"""울프RPG 에디터(WOLF RPG Editor) 게임에서 대사를 뽑고 되돌려 넣습니다.

## 생김새

::

    Game.exe
    Data.wolf                 ← 게임 자료 전부를 암호로 묶은 덩이 (없을 수도 있음)
    Data/                     ← 덩이가 없으면 게임이 여기를 읽습니다
      BasicData/CommonEvent.dat, DataBase.dat, CDataBase.dat, SysDatabase.dat, Game.dat …
      MapData/*.mps

``Data.wolf`` 가 있으면 게임은 그것만 봅니다. 덩이를 다시 암호로 묶어 쓰는 대신
**풀어서 ``Data/`` 폴더에 놓고 덩이는 백업으로 옮깁니다** — 그러면 게임이 폴더를
읽습니다(RJ01575079 에서 확인). 번역한 파일은 그 폴더에 씁니다.

되돌리기는 다른 엔진과 같습니다. 백업(덩이)을 제자리로 돌리고, 풀어 놓았던 파일은
'새로 넣은 파일' 목록에 적어 두었다가 지웁니다.

## 무엇을 뽑나

- 공통 이벤트·맵 이벤트의 **문장 표시**, **선택지**, **글자 그림**
- **문자열 변수에 넣는 글자**(메뉴 문구·전투 문장을 이렇게 만드는 게임이 많습니다)
- 데이터베이스의 **글자 칸 값**(아이템·스킬 이름, 설명)
- 창 제목

게임이 **글자 그대로 찾는 값**(이름으로 부르는 공통 이벤트, 라벨, 글자 비교 조건,
데이터베이스를 이름으로 여는 명령)은 뽑지 않습니다. 번역하면 찾던 것을 못 찾습니다.
"""

from __future__ import annotations

import re
import shutil
import struct
from pathlib import Path

from .. import sjiskorean, wolfarc, wolftext
from ..model import Location

ARCHIVE = "Data.wolf"
BACKUP_DIR = "__gameloc_backup__"
TEXT_DIRS = ("BasicData", "MapData")
DB_NAMES = ("DataBase", "CDataBase", "SysDatabase")
SCENE_ARCHIVE_MAX = 256 * 1024 * 1024      # 장면 글 묶음은 작습니다(수 MB). 이보다 크면 그림·소리 묶음으로 봅니다

# 일본어(가나·한자)
_JP = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uff66-\uff9f]")
_KANA = re.compile(r"[\u3040-\u30ff\uff66-\uff9f]")
_COUNT_TAG = re.compile(r"[+\uff0b][0-9\uff10-\uff19]+(?:,.*)?$")          # '\u2026+1' \ub85c \ub05d\ub098\ub294 \ud69f\uc218 \uc774\ub984\ud45c(DB \uc774\ub984\uacfc \uc9dd)
# 울프 제어문자: \c[1] \cself[2] \f[20] \font[1] \E \n \r[…] <C> 등
_CODE = re.compile(r"\\[A-Za-z]+\d*(?:\[[^\]]*\])?|\\.|<[A-Za-z]>")
# 파일 경로 모양. 역슬래시는 울프 제어문자(\c[1])에 늘 쓰이므로 **제어문자를 뗀 뒤**
# 봅니다 — 안 그러면 색이 들어간 대사가 전부 경로로 잘못 걸립니다.
_FILEISH = re.compile(r"^[^\s]*/[^\s]*$|\.(?:png|jpe?g|bmp|gif|ogg|mp3|wav|mid|txt|csv|dat|"
                      r"wolf|ttf|otf)$", re.I)


# ---------------------------------------------------------------- 경로 경고

def path_warning(root: Path) -> str:
    """게임 폴더 경로에 아스키가 아닌 글자(한글·일본어 등)가 있으면 경고문을, 없으면 빈 글.

    옛 울프(2.x · 3.0x, Shift-JIS)는 경로를 일본어 코드 페이지로 바꿔 파일을 여는데, 그러면 경로의
    한글·일본어가 깨져 ``Read Error: Cannot locate basic info data …/Game.dat`` 가 뜨며 안 켜집니다.
    **번역 전 원본도 똑같이 안 켜지는** 엔진 자체의 한계입니다(RJ01516302 에서 확인: 영문 경로에서는 켜지고,
    한글·일본어가 든 경로에서는 번역 전·후 모두 같은 오류). 폴더 연결(junction)로 영문 이름 경로를 만들어
    그 경로로 실행하면 파일을 옮기지 않고도 켜집니다."""
    text = str(root)
    if text.isascii():
        return ""
    return ("⚠  게임 폴더 경로에 한글·일본어 같은 글자가 있습니다. 구버전 울프(Shift-JIS) 게임은 이런 경로에서 "
            "'Read Error: Cannot locate basic info data' 를 띄우며 안 켜질 수 있습니다(번역과 상관없는 엔진의 한계).\n"
            "   → 영문 경로로 실행하세요. 파일을 옮기지 않아도 됩니다: 명령 프롬프트에서\n"
            f'      mklink /J C:\\Games\\GameName "{text}"\n'
            "      를 한 번 실행하고 C:\\Games\\GameName\\Game.exe 로 켜면 됩니다(관리자 권한 필요 없음).")


# ---------------------------------------------------------------- 판별


def is_wolf(root: Path) -> Path | None:
    """울프 게임이면 게임 폴더를, 아니면 ``None``."""
    root = Path(root)
    if (root / ARCHIVE).is_file() or (root / BACKUP_DIR / ARCHIVE).is_file():
        return root
    data = root / "Data"
    if (data / "BasicData" / "Game.dat").is_file() or any(
            (data / f"{d}.wolf").is_file() for d in TEXT_DIRS):
        return root
    return None


def text_files_hint(root: Path) -> list[Path]:
    """판별 결과에 보여 줄 파일 목록(있는 것만)."""
    out = [p for p in (root / ARCHIVE, root / "Data" / "BasicData.wolf") if p.is_file()]
    for d in TEXT_DIRS:
        folder = root / "Data" / d
        if folder.is_dir():
            out += sorted(p for p in folder.iterdir()
                          if p.suffix.lower() in (".dat", ".mps"))
    return out


# ---------------------------------------------------------------- 원본 읽기


def _archives(root: Path) -> list[tuple[str, Path]]:
    """(게임 폴더 기준 앞머리, 덩이 파일). **백업에 있는 원본을 먼저** 봅니다."""
    rels = {ARCHIVE: "Data/"}
    for base in (root / BACKUP_DIR, root):
        for p in sorted((base / "Data").glob("*.wolf")):
            # 그림·소리 묶음(수백 MB)은 글자가 없으니 열지도 풀지도 않습니다
            if p.stem.lower() in {d.lower() for d in TEXT_DIRS}:
                rels.setdefault(f"Data/{p.name}", f"Data/{p.stem}/")
    out = []
    for rel, prefix in rels.items():
        for base in (root / BACKUP_DIR, root):
            if (base / rel).is_file():
                out.append((prefix, base / rel))
                break
    return out


class Source:
    """번역 전 **원본** 바이트를 줍니다(덩이 안 → 백업 → 게임 폴더 순).

    묶음은 처음 쓸 때 엽니다(전체를 메모리에 올리므로 안 쓰는 것은 열지 않습니다).
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.errors: list[str] = []
        self._paths = _archives(self.root)
        self._open: dict[str, wolfarc.WolfArchive | None] = {}
        self.scene_dirs: dict[str, str] = {}      # 소문자 이름 → Data 안의 실제 이름 (장면 글 폴더)

    def add_scene_dir(self, name: str) -> bool:
        """이벤트가 글 파일을 읽어 오는 ``Data/<name>/`` 를 등록합니다(묶음 ``.wolf`` 든 풀린 폴더든).

        없으면 ``False``. 큰 묶음(그림·소리)은 글 파일 폴더가 아니므로 등록하지 않습니다."""
        low = name.lower()
        if low in self.scene_dirs or low in {d.lower() for d in TEXT_DIRS}:
            return low in self.scene_dirs
        for base in (self.root / BACKUP_DIR, self.root):
            data = base / "Data"
            for p in sorted(data.glob("*.wolf")) if data.is_dir() else []:
                if p.stem.lower() == low and p.stat().st_size <= SCENE_ARCHIVE_MAX:
                    prefix = f"Data/{p.stem}/"
                    if all(pre.lower() != prefix.lower() for pre, _ in self._paths):
                        self._paths.append((prefix, p))
                    self.scene_dirs[low] = p.stem
                    return True
            folder = data / name
            if folder.is_dir() and any(folder.rglob("*.txt")):
                self.scene_dirs[low] = folder.name
                return True
        return False

    def scene_files(self) -> list[str]:
        """등록한 장면 글 폴더 안의 ``.txt`` (``Data/Evtext/a01_01h.txt`` 꼴)."""
        found: dict[str, str] = {}
        for low, d in self.scene_dirs.items():
            prefix = f"Data/{d}/"
            for pre, arc in self.arcs:
                if pre.lower() == prefix.lower():
                    for f in arc.files:
                        if f.path.lower().endswith(".txt"):
                            rel = prefix + f.path.replace("\\", "/")
                            found.setdefault(rel.lower(), rel)
            for base in (self.root / BACKUP_DIR, self.root):
                folder = base / "Data" / d
                if folder.is_dir():
                    for p in folder.rglob("*.txt"):
                        rel = prefix + p.relative_to(folder).as_posix()
                        found.setdefault(rel.lower(), rel)
        return sorted(found.values())

    @property
    def arcs(self) -> list[tuple[str, wolfarc.WolfArchive]]:
        out = []
        for prefix, path in self._paths:
            arc = self._get(prefix, path)
            if arc is not None:
                out.append((prefix, arc))
        return out

    def _get(self, prefix: str, path: Path):
        if prefix not in self._open:
            try:
                self._open[prefix] = wolfarc.WolfArchive(path)
            except wolfarc.WolfArcError as exc:
                self._open[prefix] = None
                self.errors.append(f"{path.name}: {exc}")
        return self._open[prefix]

    def _in_archive(self, rel: str):
        low = rel.replace("\\", "/").lower()
        for prefix, path in self._paths:
            if low.startswith(prefix.lower()):
                arc = self._get(prefix, path)
                if arc is None:
                    continue
                try:
                    return arc, arc.find(rel[len(prefix):])
                except KeyError:
                    pass
        return None

    def text_files(self) -> list[str]:
        names: set[str] = set()
        for prefix, arc in self.arcs:
            for f in arc.files:
                names.add(prefix + f.path)
        for d in TEXT_DIRS:
            for base in (self.root / BACKUP_DIR, self.root):
                folder = base / "Data" / d
                if folder.is_dir():
                    for p in folder.iterdir():
                        names.add(f"Data/{d}/{p.name}")
        low = {n.lower(): n for n in names}
        out = []
        for n in sorted(low.values()):
            parts = n.split("/")
            name = parts[-1].lower()
            if len(parts) != 3 or parts[1] not in TEXT_DIRS:
                continue
            if name == "commonevent.dat" or name == "game.dat" or name.endswith(".mps") or \
                    name in {f"{d.lower()}.dat" for d in DB_NAMES}:
                out.append(n)
        return out

    def read(self, rel: str) -> bytes | None:
        hit = self._in_archive(rel)
        if hit is not None:
            arc, f = hit
            return arc.read(f, strip_guard=True)
        for base in (self.root / BACKUP_DIR, self.root):
            p = base / rel
            if p.is_file():
                data = p.read_bytes()
                if data.startswith(wolfarc.GUARD):
                    data = data[len(wolfarc.GUARD):]
                return data
        return None

    def parse(self, rel: str) -> wolftext.WolfText:
        data = self.read(rel)
        if data is None:
            raise FileNotFoundError(rel)
        project = None
        if rel.lower().endswith(".dat") and rel.rsplit("/", 1)[-1][:-4] in DB_NAMES:
            project = self.read(rel[:-4] + ".project")
        return wolftext.parse(rel, data, project)


# ---------------------------------------------------------------- 뽑기


# ---------------------------------------------------------------- 줄 맞추기
#
# 울프의 대화창은 **자동 줄바꿈이 없습니다.** 제작자가 줄마다 직접 끊어 놓았고(``\r\n``), 번역기는
# 그 줄바꿈을 공백으로 합쳐 버려 한 줄이 창 밖으로 넘칩니다. 그래서 적용할 때 원문이 쓰던 줄 폭
# (가장 긴 줄)에 맞춰 번역문을 다시 끊습니다. 번역기에는 줄을 합친 채 문장으로 번역하게 두는 편이
# 품질이 좋아서 번역 단계에서는 줄바꿈을 막지 않습니다.

_CTRL = re.compile(r"(\\[A-Za-z]+\d*\[[^\]]*\]|\\[A-Za-z!.^|<>${}\\])")
DEFAULT_WIDTH = 52          # 줄 하나가 원문 한 줄뿐일 때의 기본 폭(전각 26자)
MIN_WIDTH = 20


def _units(text: str) -> int:
    """화면에서 차지하는 폭(전각 2, 반각 1). 제어문자는 폭 0, ``\\s[n]``(변수 글자)은 짐작으로 8."""
    total = 0
    for part in _CTRL.split(text):
        if part.startswith("\\"):
            total += 8 if part.startswith("\\s[") else (2 if part.startswith("\\i[") else 0)
        else:
            total += sum(1 if ord(c) < 0x80 or 0xFF61 <= ord(c) <= 0xFF9F else 2 for c in part)
    return total


def _wrap_line(line: str, limit: int) -> list[str]:
    if _units(line) <= limit:
        return [line]
    out: list[str] = []
    cur = ""
    for word in line.split(" "):
        cand = word if not cur else cur + " " + word
        if _units(cand) <= limit:
            cur = cand
            continue
        if cur:
            out.append(cur)
        cur = ""
        while _units(word) > limit:                    # 한 낱말이 한 줄보다 길면 억지로 끊습니다
            cut = len(word)
            while cut > 1 and _units(word[:cut]) > limit:
                cut -= 1
            out.append(word[:cut])
            word = word[cut:]
        cur = word
    if cur:
        out.append(cur)
    return out


_HEAD = re.compile(r"^([@＠]\d+)(\r?\n)")
_HEAD_IN_NEW = re.compile(r"^[@＠]\d+\s*")


def fit_lines(kind: str, original: str, new: str) -> str:
    """번역문을 원문의 줄 폭에 맞춰 다시 끊습니다. 줄바꿈이 필요 없는 것(이름·선택지)은 그대로.

    이 게임의 대화는 첫 줄이 ``@1`` 처럼 **게임이 읽는 표시**이고 본문은 그 다음 줄부터입니다. 번역기는
    그 줄바꿈을 공백으로 합쳐 버리므로(``@1 본문``) 첫 줄을 원문 그대로 되살리고 본문만 맞춥니다."""
    if kind == wolftext.SPEAKER:
        return " ".join(new.split())
    if kind == wolftext.SCENE:
        return fit_scene(original, new)
    if kind == "callarg":
        return fit_csv(original, new)
    m = _HEAD.match(original) if kind == "message" else None
    if m:
        body = _HEAD_IN_NEW.sub("", new, count=1) if _HEAD_IN_NEW.match(new) else new
        return m.group(1) + m.group(2) + fit_lines(kind, original[m.end():], body)
    if kind in ("choice", "title", "font", "picture") and "\n" not in original:
        return new
    multi = "\n" in original
    if not multi and not (kind == "message" and _units(new) > DEFAULT_WIDTH):
        return new
    nl = "\r\n" if "\r\n" in original else "\n"
    if multi:
        widest = max(_units(ln) for ln in original.replace("\r\n", "\n").split("\n"))
        limit = max(MIN_WIDTH, widest)
    else:
        limit = DEFAULT_WIDTH
    lines: list[str] = []
    for ln in new.replace("\r\n", "\n").split("\n"):
        lines += _wrap_line(ln, limit)
    return nl.join(lines)


SCENE_MAX = 72          # 장면 대사창 한 줄의 폭(전각 36자). 창은 이보다 넓지만 여유를 둡니다


def _scene_safe(line: str) -> str:
    """번역한 줄이 대본 문법(명령·화자·지시)으로 읽히지 않게 합니다."""
    kind = wolftext._txt_kind(line)[0]
    if kind == wolftext._T_TEXT:
        return line
    if kind == wolftext._T_CMD:
        return line.replace("/", "／", 1)
    if kind in (wolftext._T_SPEAKER, wolftext._T_DIRECTIVE):
        return line.replace("：", ":", 1)
    return "＠" + line.lstrip("@＠") if line.strip().startswith(("@", "＠")) else line


def fit_csv(original: str, new: str) -> str:
    """``セーブ,ロード,タイトルへ`` 처럼 쉼표로 이은 선택지 목록은 게임이 쉼표로 나눕니다. 번역기가 쉼표를
    바꾸거나 띄어 쓰면 못 나누므로, 원문이 띄어쓰기 없는 쉼표 목록일 때는 쉼표만 ``,`` 로 맞춥니다."""
    if "," not in original or any(c.isspace() for c in original.strip()):
        return new
    parts = [p.strip() for p in re.split(r"[,，、､]", new) if p.strip()]
    return ",".join(parts) if len(parts) >= 2 else new


def fit_scene(original: str, new: str) -> str:
    """장면 대사 번역을 원문의 줄 수에 맞춰 다시 끊습니다(대사창은 보통 3줄까지).

    번역기가 줄바꿈을 지킨 경우(줄 수가 같고 폭이 넘치지 않음)는 그대로 둡니다. 아니면 한 문단으로 합쳐
    원문 가장 긴 줄의 폭에서 시작해, 줄 수가 원문보다 많으면 폭을 조금씩 넓혀 가며(``SCENE_MAX`` 까지) 맞춥니다."""
    orig = [x for x in original.split("\n") if x.strip()]
    want = max(1, len(orig))
    got = [x.strip() for x in new.replace("\r\n", "\n").replace("\r", "\n").split("\n") if x.strip()]
    if not got:
        return new
    if len(got) <= want and all(_units(x) <= SCENE_MAX for x in got):
        return "\n".join(_scene_safe(x) for x in got)
    flat = " ".join(got)
    limit = max(MIN_WIDTH, max((_units(x) for x in orig), default=DEFAULT_WIDTH))
    lines = _wrap_line(flat, limit)
    while len(lines) > want and limit < SCENE_MAX:
        limit += 2
        lines = _wrap_line(flat, limit)
    return "\n".join(_scene_safe(x.rstrip()) for x in lines if x.strip())


def scene_dirs_from(parsed) -> list[str]:
    """이벤트 글 중 ``Data/<폴더>/…`` 로 시작하는 것에서 폴더 이름을 모읍니다(예: ``Data/Evtext/\\cself[5]h.txt``).

    이벤트가 그 폴더의 글 파일을 읽어 대사를 보여 주는 게임이라는 뜻입니다. 이런 증거가 없는 폴더의
    ``.txt`` 는 건드리지 않습니다(설정·메모 파일일 수 있습니다)."""
    names: dict[str, str] = {}
    for _rel, wt in parsed:
        if wt.kind != "common":
            continue
        for s in wt.slots:
            m = re.match(r"^Data/([^/\\]+)/", s.text)
            if m and m.group(1).lower() not in {d.lower() for d in TEXT_DIRS}:
                names.setdefault(m.group(1).lower(), m.group(1))
    return sorted(names.values())


def _worth(text: str) -> bool:
    """제어문자를 빼고도 사람이 읽을 글자가 남는가."""
    rest = _CODE.sub("", text)
    return any(c.isalpha() for c in rest)


def cdb_field_names(src: "Source") -> set[str]:
    """가변DB(CDataBase)의 **칸 이름들**. 게임은 이 이름을 글자 그대로 불러 값을 읽고 씁니다.

    데이터베이스 칸 안에 같은 글자가 들어 있으면 그것은 보여 주는 글이 아니라 '어느 칸을 쓸지 알려 주는
    열쇠'입니다(RJ01724124: ``Hカウントタグ`` 표의 ``cdb項目名`` 칸에 ``失禁量`` 이 있고, 게임이 그 이름으로
    가변DB 의 칸을 찾음). 번역하면 못 찾아 ``DB operation: The item number is negative! => -1`` 오류가 납니다.
    읽지 못하면 빈 집합(= 지금까지와 같은 동작)을 돌려줍니다."""
    return _project_names(src, ("cdatabase.dat",), fields=True)


def db_data_names(src: "Source") -> set[str]:
    """모든 데이터베이스의 **자료 이름들**(``立絵画像`` 의 ``セリス`` 처럼). 게임이 글자로 찾는 열쇠입니다.

    장면 글의 화자 이름이 이것과 같으면 게임이 그 이름으로 서 있는 그림 따위를 찾으므로 번역하지 않습니다."""
    return _project_names(src, tuple(f"{d.lower()}.dat" for d in DB_NAMES), datas=True)


def _project_names(src: "Source", dats: tuple[str, ...], *, fields: bool = False,
                   datas: bool = False) -> set[str]:
    names: set[str] = set()
    for rel in src.text_files():
        if rel.rsplit("/", 1)[-1].lower() not in dats:
            continue
        try:
            data, raw = src.read(rel), src.read(rel[:-4] + ".project")
            if data is None or raw is None:
                continue
            utf8 = wolftext._magic_ok(data[1:10], wolftext.DB_MAGIC, 5)
            if utf8 is None:
                continue
            for _type, flds, dts in wolftext.parse_project(raw, utf8).types:
                if fields:
                    names |= {f.strip() for f in flds if f.strip()}
                if datas:
                    names |= {d.strip() for d in dts if d.strip()}
        except (wolftext.WolfFormatError, ValueError, IndexError, OSError, struct.error):
            continue
    return names


def text_buffers(parsed) -> set[str]:
    """DB 안에서 **화면에 보일 긴 글을 담아 두는** 타입 이름들(``dbwrite`` 로 긴 글이 3번 이상 써 넣어지는 곳).

    그런 곳에 써 넣는 값은 다른 데서 열쇠로 쓰이는 낱말(``性欲`` 같은 능력치 이름)과 같아도 화면 글자입니다."""
    count: dict[str, int] = {}
    for _rel, wt in parsed:
        for s in wt.slots:
            if s.kind == "dbwrite" and ("\n" in s.text or len(s.text) >= 20):
                t = s.ctx.rsplit(" › ", 1)[-1]
                count[t] = count.get(t, 0) + 1
    return {t for t, n in count.items() if n >= 3}


def _keep(slot: wolftext.Slot, refs: set[str], utf8: bool = True,
          keys: "frozenset[str] | set[str]" = frozenset(),
          dkeys: "frozenset[str] | set[str]" = frozenset(),
          buffers: "frozenset[str] | set[str]" = frozenset()) -> bool:
    t = slot.text
    if not t.strip() or not _worth(t):
        return False
    if slot.kind in ("message", "choice", "picture"):
        return True
    if slot.kind == "title":
        return utf8            # Shift-JIS 게임의 창 제목은 시스템 글꼴로 그려져 한글 짝 글자를 못 씁니다
    if slot.kind in ("setstring", "db"):
        # 값 칸에는 파일 이름·내부 키도 섞여 있습니다. 일본어가 든 것만, 경로 모양은 뺍니다.
        if t in refs or t.strip() in keys or _FILEISH.search(_CODE.sub("", t).strip()):
            return False
        return bool(_JP.search(t))
    if slot.kind == "dbwrite":
        # DB 에 써 넣는 화면 글(도움말 등). 열쇠로 쓰이는 값(다른 곳의 비교·검색 글자, DB 자료 이름)은 뺍니다.
        s = _CODE.sub("", t).strip()
        if _FILEISH.search(s) or not _JP.search(t) or _COUNT_TAG.search(s) or len(s) < 2:
            return False
        if slot.ctx.rsplit(" › ", 1)[-1] in buffers:
            return True
        return not (t in refs or t.strip() in keys or t.strip() in dkeys)
    if slot.kind == "callarg":
        # 공통 이벤트에 넘기는 글자 인자. 보이는 문구(전투 메시지·알림·간판)도 있지만 이름표(``/絶頂+1``)나
        # 글자 모음(``呪殺怒忌…``)도 있어서, 일본어가 있고 열쇠로 쓰이지 않는 것만 뽑습니다.
        s = _CODE.sub("", t).strip()
        if t in refs or t.strip() in keys or s.startswith("/") or _FILEISH.search(s) or not _JP.search(t):
            return False
        if _COUNT_TAG.search(s):                            # ``魔物出産+1`` 같은 횟수 이름표
            return False
        return bool(_KANA.search(t)) or len(s) < 8      # 가나 없이 긴 한자 덩어리는 글자 모음
    return False


def extract_entries(root: Path, progress=None) -> tuple[list, dict]:
    src = Source(root)
    say = progress or (lambda _m: None)
    for err in src.errors:
        say(f"  ! {err}")
    raw: list[tuple[str, Location]] = []
    stats: dict[str, int] = {}
    parsed = []
    refs: set[str] = set()
    for rel in src.text_files():
        try:
            wt = src.parse(rel)
        except (wolftext.WolfFormatError, ValueError, IndexError, FileNotFoundError) as exc:
            say(f"  ! {rel}: {exc}")
            stats["wolf_errors"] = stats.get("wolf_errors", 0) + 1
            continue
        parsed.append((rel, wt))
        refs |= wt.refs
    keys = cdb_field_names(src)
    dkeys = db_data_names(src)
    buffers = text_buffers(parsed)
    for rel, wt in parsed:
        got = 0
        for s in wt.slots:
            if _keep(s, refs, wt.utf8, keys, dkeys, buffers):
                raw.append((s.text, Location(file=rel, kind="wolf", fmt=s.kind,
                                             ptr=s.ptr, asset=s.ctx)))
                stats[f"wolf_{s.kind}"] = stats.get(f"wolf_{s.kind}", 0) + 1
                got += 1
        if got:
            say(f"  · {rel.rsplit('/', 1)[-1]}: {got}개")
    stats["wolf_files"] = len(parsed)
    stats["wolf_packed"] = int(bool(src.arcs))
    _extract_scenes(src, parsed, refs, raw, stats, say)
    return raw, stats


def _extract_scenes(src: "Source", parsed, refs: set[str], raw: list, stats: dict, say) -> None:
    """이벤트가 읽어 오는 장면 글 파일(``Data/Evtext/*.txt`` 등)에서 대사와 화자 이름을 뽑습니다.

    UTF-8 로 만든 울프(3.5)에서만 합니다 — 바꾼 파일을 UTF-8 로 쓰는데, 옛 Shift-JIS 울프는 그것을 못 읽습니다."""
    if not parsed or not all(wt.utf8 for _rel, wt in parsed):
        return
    dirs = scene_dirs_from(parsed)
    found = [d for d in dirs if src.add_scene_dir(d)]
    if not found:
        return
    # 게임이 글자 그대로 비교하는 화자 이름(예: 말풍선 전용 인물)은 번역하지 않습니다
    # 또 게임이 그 이름으로 서 있는 그림 따위를 찾는 이름(데이터베이스 자료 이름)도 번역하지 않습니다
    fixed = {r.strip().rstrip("：") for r in refs} | {r.strip() for r in refs} | db_data_names(src)
    per_dir: dict[str, int] = {}
    for rel in src.scene_files():
        try:
            wt = src.parse(rel)
        except (wolftext.WolfFormatError, ValueError, IndexError, FileNotFoundError,
                UnicodeDecodeError) as exc:
            say(f"  ! {rel}: {exc}")
            stats["wolf_errors"] = stats.get("wolf_errors", 0) + 1
            continue
        got = 0
        for s in wt.slots:
            if s.kind == wolftext.SCENE:
                ok = bool(_JP.search(s.text)) and _worth(s.text)
            elif s.kind == wolftext.SPEAKER:
                ok = bool(_JP.search(s.text)) and s.text.strip() not in fixed
            else:
                ok = False
            if ok:
                raw.append((s.text, Location(file=rel, kind="wolf", fmt=s.kind, ptr=s.ptr, asset=s.ctx)))
                stats[f"wolf_{s.kind}"] = stats.get(f"wolf_{s.kind}", 0) + 1
                got += 1
        d = rel.split("/")[1]
        per_dir[d] = per_dir.get(d, 0) + got
    stats["wolf_scene_files"] = len(src.scene_files())
    for d, n in per_dir.items():
        say(f"  · Data/{d}/ 장면 글 파일: {n}개")


# ---------------------------------------------------------------- 넣기


def _backup(target: Path, root: Path) -> None:
    dest = root / BACKUP_DIR / target.relative_to(root)
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, dest)


def unpack_archives(root: Path, progress=None, extra=()) -> list[Path]:
    """게임 폴더에 있는 ``.wolf`` 덩이를 ``Data/`` 로 풀고 덩이는 백업으로 옮깁니다.

    ``extra`` 는 글 파일이 든 덩이(장면 글 폴더, 예: ``Evtext``) 이름들입니다.

    이미 폴더에 있는 파일은 덮지 않습니다(그 게임이 원래 폴더로 둔 파일). 새로 만든
    파일 목록을 돌려줍니다 — 되돌리기가 지울 것들입니다.
    """
    say = progress or (lambda _m: None)
    root = Path(root)
    # 글자가 든 묶음(Data.wolf 통짜, 또는 BasicData·MapData 처럼 폴더별 묶음)만 풉니다.
    # 그림·소리 묶음은 그대로 둬도 게임이 씁니다.
    live = [(ARCHIVE, "Data/")] + [
        (f"Data/{p.name}", f"Data/{p.stem}/") for p in sorted((root / "Data").glob("*.wolf"))
        if p.stem.lower() in {d.lower() for d in (*TEXT_DIRS, *extra)}]
    added: list[Path] = []
    for rel, prefix in live:
        path = root / rel
        if not path.is_file():
            continue
        arc = wolfarc.WolfArchive(path)
        say(f"  · {path.name} 푸는 중… ({len(arc.files)}개)")
        for f in arc.files:
            dest = root / (prefix + f.path)
            if dest.exists():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(arc.read(f, strip_guard=True))
            added.append(dest)
        del arc
        keep = root / BACKUP_DIR / rel
        keep.parent.mkdir(parents=True, exist_ok=True)
        if keep.exists():
            path.unlink()                    # 백업에 원본이 이미 있습니다
        else:
            path.replace(keep)               # 같은 드라이브라 옮기기만 합니다(복사 안 함)
        say(f"  · {path.name} → 백업 폴더로 옮김 (게임이 이제 Data 폴더를 읽습니다)")
    return added


def apply(proj, lang: str, root: Path, rep, *, dry_run: bool, progress, edits_by_file) -> None:
    """``edits_by_file`` = {파일: {ptr: 번역}}. 결과는 ``rep`` 에 적습니다."""
    from ..apply import _remember_added

    root = Path(root)
    src = Source(root)
    for err in src.errors:
        rep.failures.append(f"{err.split(':')[0]}: 안전하지 않아 건너뜁니다 ({err})")

    # 번역표에 장면 글 파일(Data/Evtext/…txt)이 있으면 그 폴더를 읽을 수 있게 등록합니다
    scene_dirs = sorted({rel.split("/")[1] for rel in edits_by_file
                         if rel.lower().endswith(".txt") and rel.count("/") >= 2 and rel.startswith("Data/")})
    for d in scene_dirs:
        src.add_scene_dir(d)

    parsed: dict[str, tuple[wolftext.WolfText, dict]] = {}
    for rel, edits in sorted(edits_by_file.items()):
        try:
            parsed[rel] = (src.parse(rel), dict(edits))
        except FileNotFoundError:
            rep.failures.append(f"{rel}: 파일 없음")
        except (wolftext.WolfFormatError, ValueError, IndexError) as exc:
            rep.failures.append(f"{rel}: 구조가 어긋나 건너뜁니다 ({exc})")

    # 가변DB 칸 이름과 같은 글자가 든 데이터베이스 칸은 번역하지 않습니다. 게임이 그 글자로 칸을 찾으므로
    # 번역하면 못 찾습니다. 예전에 번역해 둔 표에 들어 있어도 여기서 걸러 원문 그대로 둡니다.
    keys = cdb_field_names(src)
    if keys:
        skipped = 0
        for _rel, (wt, edits) in parsed.items():
            for s in wt.slots:
                if s.ptr in edits and s.kind in ("setstring", "db") and s.text.strip() in keys:
                    del edits[s.ptr]
                    skipped += 1
        if skipped:
            rep.notes.append(f"ℹ  게임이 글자 그대로 칸을 찾는 데이터베이스 값 {skipped}개는 "
                             "번역하지 않고 원문으로 두었습니다(번역하면 게임이 오류를 냅니다).")

    # 대화창은 자동 줄바꿈이 없으므로 번역문을 원문의 줄 폭에 맞춰 다시 끊습니다.
    for _rel, (wt, edits) in parsed.items():
        by_ptr = {s.ptr: s for s in wt.slots}
        for ptr, new in list(edits.items()):
            s = by_ptr.get(ptr)
            if s is not None and s.kind != "font":
                edits[ptr] = fit_lines(s.kind, s.text, new)

    # 장면 글 파일은 UTF-8 로 써야 한글이 들어갑니다. 옛 Shift-JIS 울프는 그것을 못 읽으므로 건드리지 않습니다.
    scene_rels = [r for r, (wt, _e) in parsed.items() if wt.kind == "txt"]
    if scene_rels and any(not wt.utf8 for wt, _e in parsed.values() if wt.kind != "txt"):
        for r in scene_rels:
            del parsed[r]
        rep.notes.append("ℹ  이 게임은 글자를 Shift-JIS 로만 읽는 옛 울프라, 장면 글 파일(.txt)은 번역하지 않고 "
                         "원문으로 두었습니다.")

    # ── Shift-JIS 게임: 한글을 짝 한자로 담고, 그 한자를 한글로 그리는 글꼴을 만듭니다 ──────
    mapping = font_bytes = None
    font_edits = 0
    if any(not wt.utf8 for wt, _ in parsed.values()):
        try:
            mapping, font_bytes, base_font = _korean_setup(src, parsed, progress)
        except (ValueError, OSError) as exc:
            rep.failures.append(f"한글 글꼴을 만들지 못했습니다: {exc}")
            return
        gd_rel = "Data/BasicData/Game.dat"
        if gd_rel not in parsed:
            try:
                parsed[gd_rel] = (src.parse(gd_rel), {})
            except (FileNotFoundError, wolftext.WolfFormatError, ValueError, IndexError) as exc:
                rep.failures.append(f"{gd_rel}: 글꼴 이름을 못 바꿔 건너뜁니다 ({exc})")
                return
        gd_wt, gd_edits = parsed[gd_rel]
        for s in gd_wt.slots:
            if s.kind == "title":
                gd_edits.pop(s.ptr, None)          # 창 제목은 번역하지 않습니다
            elif s.kind == "font":
                gd_edits[s.ptr] = sjiskorean.FAMILY
                font_edits += 1
        warn = path_warning(root)
        if warn:
            rep.notes.append(warn)
        rep.notes.append(
            "ℹ  이 게임은 글자를 Shift-JIS 로만 저장하는 옛 방식이라 한글을 그대로 담을 수 없습니다.\n"
            f"   → 한글 한 글자마다 쓰이지 않는 일본 한자를 짝지어 담고, 그 한자를 한글 모양으로 그리는 "
            f"글꼴({sjiskorean.FAMILY}.ttf, {base_font.name} 의 글자 모양 사용)을 게임 폴더에 넣었습니다.\n"
            "   → 게임이 이 글꼴을 쓰므로 게임 폴더를 옮길 때 같이 가져가세요. [원래대로] 가 지웁니다.")

    outputs: list[tuple[str, bytes, int]] = []
    for rel, (wt, edits) in sorted(parsed.items()):
        is_game_dat = wt.kind == "game"
        try:
            if mapping is not None and not wt.utf8:
                wt.encoder = mapping.encode
                expect = {p: mapping.substitute(t) for p, t in edits.items()}
            else:
                expect = dict(edits)
            data, n = wt.rebuild(edits)
            again = wolftext.parse(rel, data, src.read(rel[:-4] + ".project")
                                   if wt.kind == "db" else None)
            for s in again.slots:
                if s.ptr in expect and s.text != expect[s.ptr]:
                    raise wolftext.WolfFormatError(f"{s.ptr} 가 다시 읽히지 않습니다")
        except (wolftext.WolfFormatError, ValueError, IndexError, UnicodeEncodeError) as exc:
            rep.failures.append(f"{rel}: 구조가 어긋나 건너뜁니다 ({exc})")
            continue
        if n:
            outputs.append((rel, data, n - (font_edits if is_game_dat and mapping is not None else 0)))
    src = None                                   # 덩이(수백 MB)를 메모리에서 내립니다
    overrides = image_overrides(root)
    if dry_run or not (outputs or overrides):
        for rel, _d, n in outputs:
            if n:
                rep.files_written += 1
                rep.strings_written += n
        if overrides:
            rep.notes.append(f"ℹ  한글로 바꾼 그림 {len(overrides)}개를 적용할 때 끼워 넣습니다.")
        return
    img_dirs = sorted({rel.split("/")[1] for rel in overrides})
    added = unpack_archives(root, progress, extra=[*scene_dirs, *img_dirs])
    if font_bytes is not None:
        # 엔진이 게임 폴더(맨 위와 Data)의 글꼴 파일을 스스로 읽어 씁니다.
        for folder in (root, root / "Data"):
            ttf = folder / f"{sjiskorean.FAMILY}.ttf"
            ttf.write_bytes(font_bytes)
            added.append(ttf)
            rep.written.append(ttf.relative_to(root).as_posix())
            progress(f"  ✓ {ttf.relative_to(root).as_posix()} (한글 글꼴)")
    if added:
        _remember_added(root, added)
    added_set = {p.resolve() for p in added}
    for rel, data, n in outputs:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.resolve() not in added_set and not _was_added(root, rel):
            _backup(target, root)
        target.write_bytes(data)
        rep.written.append(rel)
        if n:
            rep.files_written += 1
            rep.strings_written += n
        progress(f"  ✓ {rel} ({n})")
    fresh: list[Path] = []
    for rel, picture in overrides.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            fresh.append(target)
        elif target.resolve() not in added_set and not _was_added(root, rel):
            _backup(target, root)
        target.write_bytes(picture.read_bytes())
        rep.written.append(rel)
        rep.files_written += 1
        progress(f"  ✓ {rel} (한글 그림)")
    if fresh:
        _remember_added(root, fresh)


IMAGE_DIR = "images"


def image_overrides(root: Path) -> dict[str, Path]:
    """한글로 바꿔 둔 **그림 파일**들. ``<게임폴더>_loc/images/`` 안에 게임의 ``Data`` 폴더와 같은 경로로 둡니다.

    예: ``…_loc/images/Picture/hud.png`` → 게임의 ``Data/Picture/hud.png``. 그림 속에 박힌 글자(상태창 라벨 등)는 글자
    번역으로 못 바꾸므로, 한글로 다시 그린 그림을 적용할 때 끼워 넣습니다. 묶음(``Picture.wolf`` 등)은 풀어 두고
    원본 묶음은 백업으로 옮기며, [원래대로] 가 모두 되돌립니다. ``{상대 경로(Data/…): 파일}``"""
    root = Path(root)
    folder = root.parent / f"{root.name}_loc" / IMAGE_DIR
    out: dict[str, Path] = {}
    if not folder.is_dir():
        return out
    for p in sorted(folder.rglob("*")):
        if not p.is_file():
            continue
        parts = p.relative_to(folder).parts
        if len(parts) < 2 or ".." in parts or p.suffix.lower() not in (".png", ".jpg", ".jpeg", ".bmp"):
            continue                     # Data 바로 아래 파일이나 그림 아닌 것은 건드리지 않습니다
        out["Data/" + "/".join(parts)] = p
    return out


def _korean_setup(src: "Source", parsed: dict, progress):
    """Shift-JIS 게임용 한글 짝짓기와 글꼴. ``(Mapping, 글꼴 바이트, 빌린 글꼴 경로)``."""
    texts = [t for _wt, edits in parsed.values() for t in edits.values()]
    reserved: set[str] = set()
    for rel in src.text_files():
        wt = parsed[rel][0] if rel in parsed else src.parse(rel)
        reserved |= {ch for s in wt.slots for ch in s.text}
    mapping = sjiskorean.Mapping(texts, reserved)
    base = sjiskorean.find_base_font()
    progress(f"  · 한글 {len(mapping.pairs)}자를 짝지어 글꼴을 만듭니다 (글자 모양: {base.name})")
    return mapping, sjiskorean.build_font(base, mapping), base


def _was_added(root: Path, rel: str) -> bool:
    from ..apply import ADDED_LIST
    listing = root / BACKUP_DIR / ADDED_LIST
    if not listing.is_file():
        return False
    want = rel.replace("\\", "/")
    return any(ln.strip() == want for ln in listing.read_text("utf-8").splitlines())
