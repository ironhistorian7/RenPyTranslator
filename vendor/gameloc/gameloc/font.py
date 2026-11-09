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

"""한글이 □□□ 로 나오는 것을 고칩니다.

## 무엇이 문제인가

번역은 이미 게임 안에 들어가 있습니다. 그런데 게임이 쓰는 폰트에 한글 **모양**이
없으면 그리지 못하고 네모를 찍습니다.

    · [TMP] timemachine-wa SDF   글자 7124자 · 한글 0자 · 아틀라스 8192x8192 · Static

Static(고정) 폰트는 글자 그림이 통째로 구워져 있어서, 없는 글자를 만들어낼
방법이 없습니다. 그림판을 새로 구워야 하는데 그건 유니티 에디터가 필요합니다.

## 그런데 빠져나갈 구멍이 있습니다

TextMeshPro 에는 두 가지 장치가 있습니다.

1. **Dynamic(실행 중 생성) 폰트** — 글자 그림을 미리 굽지 않고, 게임이 돌 때
   원본 폰트 파일에서 그때그때 만들어냅니다. 원본만 바꿔치면 어떤 글자든 나옵니다.
2. **대체 폰트(fallback)** — 어떤 글자를 못 그리면 다음 폰트에게 넘깁니다.

이 게임에는 마침 Dynamic 폰트가 들어 있습니다. 그래서:

    ① Dynamic 폰트가 쓰는 **원본 글꼴 파일**을 한글 글꼴로 바꾸고
    ② 한글이 없는 폰트들의 **대체 폰트 목록**에 그 Dynamic 폰트를 넣는다

이러면 일본어·영어는 원래 폰트가 그대로 그리고, 한글만 대체 폰트가 그립니다.
원본 그림판은 손대지 않습니다.

## 글꼴 파일은 어디서

**사용자 컴퓨터에 이미 깔려 있는 한글 글꼴을 씁니다.** 맑은 고딕 같은 것들요.
글꼴을 프로그램에 넣어 배포하면 저작권 문제가 생기지만, 각자 컴퓨터에 있는
것을 쓰는 건 그런 문제가 없습니다.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass, field
from pathlib import Path

from . import bundle, catalog

HANGUL = (0xAC00, 0xD7A3)

# 사람들 컴퓨터에 대체로 깔려 있는 한글 글꼴. 위에 있는 것부터 찾습니다.
CANDIDATES = {
    "Windows": [
        r"C:\Windows\Fonts\malgun.ttf",          # 맑은 고딕
        r"C:\Windows\Fonts\malgunsl.ttf",
        r"C:\Windows\Fonts\NanumGothic.ttf",
        r"C:\Windows\Fonts\gulim.ttc",
        r"C:\Windows\Fonts\batang.ttc",
    ],
    "Darwin": [
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/Library/Fonts/NanumGothic.ttf",
    ],
    "Linux": [
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
}


def find_system_font() -> Path | None:
    """이 컴퓨터에 깔려 있는 한글 글꼴 하나."""
    for p in CANDIDATES.get(platform.system(), []):
        path = Path(p)
        if path.is_file():
            return path
    # 못 찾으면 폰트 폴더를 통째로 뒤집니다
    roots = [Path(r"C:\Windows\Fonts"), Path("/usr/share/fonts"),
             Path("/Library/Fonts"), Path.home() / ".fonts"]
    for root in roots:
        if not root.is_dir():
            continue
        for pat in ("*malgun*", "*Nanum*", "*NotoSansCJK*", "*NotoSansKR*",
                    "*Gothic*"):
            hit = next(iter(sorted(root.rglob(pat))), None)
            if hit and hit.suffix.lower() in (".ttf", ".otf", ".ttc"):
                return hit
    return None


# ---------------------------------------------------------------- 폰트 찾기

def is_font_asset(class_name: str) -> bool:
    return "FontAsset" in class_name or "TMP_Font" in class_name


def unicodes_of(tree: dict) -> set[int]:
    out: set[int] = set()
    for row in tree.get("m_CharacterTable") or []:
        if isinstance(row, dict):
            u = row.get("m_Unicode")
            if isinstance(u, int):
                out.add(u)
    return out


def has_hangul(tree: dict) -> bool:
    return any(HANGUL[0] <= u <= HANGUL[1] for u in unicodes_of(tree))


def is_dynamic(tree: dict) -> bool:
    return tree.get("m_AtlasPopulationMode") in (1, 2)


@dataclass
class FontPlan:
    """무엇을 어떻게 고칠지."""

    donor_path_id: int | None = None      # 대체 폰트로 쓸 Dynamic 폰트
    donor_name: str = ""
    source_font_id: int | None = None     # 그 폰트가 읽는 원본 글꼴 파일
    source_font_name: str = ""
    targets: list[tuple[int, str]] = field(default_factory=list)  # 고칠 폰트들
    font_like: int = 0                    # 폰트로 보이지만 못 읽은 것 수
    font_real: int = 0                    # 그 중 앞머리가 진짜 폰트 모양인 것
    # 옛날식 Font 오브젝트. TextMeshPro 구조를 못 읽을 때의 우회로입니다.
    legacy: list[tuple[int, str]] = field(default_factory=list)
    tmp_readable: bool = False            # TextMeshPro 구조를 하나라도 읽었나
    notes: list[str] = field(default_factory=list)

    def ok(self) -> bool:
        return (self.donor_path_id is not None
                and self.source_font_id is not None
                and bool(self.targets))

    def legacy_only(self) -> bool:
        """제대로 된 길이 **막혔고**, 옛날식 Font 로 시도해 볼 수 있는가.

        '읽었는데 고칠 게 없다' 와 '아예 못 읽었다' 는 다릅니다. 앞쪽은
        할 일이 없는 것이니 우회로를 쓰면 안 됩니다.
        """
        return not self.tmp_readable and bool(self.legacy)


def _pptr_id(value) -> int | None:
    if isinstance(value, dict):
        pid = value.get("m_PathID")
        if isinstance(pid, int) and pid != 0:
            return pid
    return None


def plan(objs) -> FontPlan:
    """열려 있는 오브젝트들을 보고 계획을 세웁니다. 아무것도 안 고칩니다."""
    from .peek import script_class
    from . import tmpraw

    p = FontPlan()
    assets: list[tuple[int, str, dict]] = []
    for o in objs:
        # 옛날식 Font 는 유니티 **기본 클래스**라 IL2CPP 게임에서도 읽힙니다.
        # TextMeshPro 구조를 못 읽을 때 기댈 곳이 여기뿐입니다.
        if o.type.name == "Font":
            try:
                name = str(o.read_typetree().get("m_Name") or "?")
            except Exception:                          # noqa: BLE001
                name = "?"
            p.legacy.append((o.path_id, name))
            continue
        if o.type.name != "MonoBehaviour":
            continue
        # 이름만 보고 폰트라 우기면 없는 폰트를 있다고 말하게 됩니다.
        # 바이트 앞머리가 실제 TextMeshPro 폰트 모양일 때만 인정합니다.
        try:
            head = tmpraw.read_head(o.get_raw_data())
        except Exception:                              # noqa: BLE001
            head = None
        if head is None and not is_font_asset(script_class(o)):
            continue
        if head is not None:
            p.font_real += 1
        p.font_like += 1
        try:
            tree = o.read_typetree()
        except Exception:                              # noqa: BLE001
            continue
        if isinstance(tree, dict):
            assets.append((o.path_id, str(tree.get("m_Name") or "?"), tree))

    p.tmp_readable = bool(assets)
    if not assets:
        if p.font_real:
            p.notes.append(
                f"TextMeshPro 폰트 {p.font_real}개가 보이는데 구조를 읽지 못했습니다. "
                "게임 DLL 에서 타입 정보를 되살리지 못한 경우입니다.")
        elif p.font_like:
            p.notes.append(
                f"폰트 비슷한 것 {p.font_like}개가 있지만 TextMeshPro 폰트는 "
                "아닙니다. 건드리지 않습니다.")
        else:
            p.notes.append("이 파일에는 TextMeshPro 폰트가 없습니다.")
        if p.legacy:
            names = ", ".join(n for _i, n in p.legacy[:3])
            p.notes.append(
                f"대신 유니티 기본 글꼴 {len(p.legacy)}개({names})가 있습니다. "
                "여기에 한글 글꼴을 넣어 보겠습니다.")
        return p

    # ① 대체 폰트로 쓸 Dynamic 폰트. 글자가 적은 쪽이 아틀라스에 여유가 많습니다.
    dynamics = [(pid, name, t) for pid, name, t in assets if is_dynamic(t)]
    dynamics.sort(key=lambda x: len(unicodes_of(x[2])))
    for pid, name, tree in dynamics:
        src = _pptr_id(tree.get("m_SourceFontFile"))
        if src is not None:
            p.donor_path_id, p.donor_name = pid, name
            p.source_font_id = src
            break
    if p.donor_path_id is None:
        p.notes.append("실행 중에 글자를 만들어내는(Dynamic) 폰트가 없습니다. "
                       "이 방법으로는 고칠 수 없습니다.")
        return p

    # ② 한글이 없는 폰트 전부가 대상. 대체 폰트 자신은 뺍니다.
    for pid, name, tree in assets:
        if pid == p.donor_path_id or has_hangul(tree):
            continue
        p.targets.append((pid, name))
    if not p.targets:
        p.notes.append("이미 한글이 있는 폰트뿐입니다. 고칠 게 없습니다.")
    return p


# ---------------------------------------------------------------- 고치기

@dataclass
class FontReport:
    changed_files: list[str] = field(default_factory=list)
    donor: str = ""
    ttf: str = ""
    patched: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    retargeted: list[str] = field(default_factory=list)   # 굳어 있어서 풀어 준 폰트
    legacy_route: bool = False           # 유니티 기본 글꼴로 우회했나
    source_font_name: str = ""
    engine: str = "unity"
    existing_donor: bool = False         # 게임에 이미 있던 한글 폰트를 대체 폰트로 걸었나
    fallback_count: int = 0              # 그렇게 대체 폰트를 건 폰트 수

    def summary(self) -> str:
        if self.engine == "renpy":
            if not self.changed_files:
                return "폰트를 고치지 못했습니다. " + (self.notes[0] if self.notes else "")
            return (f"한글 글꼴({Path(self.ttf).name})을 game 폴더에 넣고, "
                    f"게임이 쓰던 글꼴 {len(self.patched)}개를 그것으로 바꿔치도록 "
                    "했습니다. 원본 글꼴 파일은 그대로 있습니다.")
        if not self.patched:
            return "폰트를 고치지 못했습니다. " + (self.notes[0] if self.notes else "")
        if self.existing_donor:
            head = (f"게임에 이미 있는 한글 폰트 '{self.donor}' 를 폰트 {self.fallback_count}개의 "
                    "대체 폰트로 걸었습니다. 글꼴 파일은 새로 넣지 않았습니다.")
            if self.legacy_route:
                head += (f" 그 밖에 유니티 기본 글꼴 {len(self.patched) - self.fallback_count}개는 "
                         f"한글 글꼴({Path(self.ttf).name})로 바꿨습니다.")
            return head
        if self.legacy_route:
            head = (f"유니티 기본 글꼴 {len(self.patched)}개를 "
                    f"한글 글꼴({Path(self.ttf).name})로 바꿨습니다.")
            if self.retargeted:
                head += f" 굳어 있던 폰트 {len(self.retargeted)}개도 풀었습니다."
            return head
        return (f"'{self.donor}' 를 한글 글꼴({Path(self.ttf).name})로 바꾸고, "
                f"폰트 {len(self.patched)}개에 대체 폰트로 걸었습니다.")


def _set_font_data(tree: dict, data: bytes, name: str) -> bool:
    """유니티 Font 오브젝트 안의 글꼴 파일 자체를 갈아끼웁니다."""
    if "m_FontData" not in tree:
        return False
    tree["m_FontData"] = data
    if isinstance(tree.get("m_FontNames"), list):
        tree["m_FontNames"] = [name]
    return True


# 이모지·아이콘 모음은 글자 폰트가 아닙니다. 한글 원본을 물려도 얻을 게 없고
# 그림판 형식이 달라 오히려 어긋날 수 있어 건드리지 않습니다.
def _donor_font_id(objs, patched_ids: set[int]) -> int:
    """한글을 넣어 줄 원본 글꼴을 고릅니다.

    ``patched_ids`` 는 **방금 한글 글꼴로 갈아친** 글꼴들입니다. 그 중 어느
    것을 가리켜도 안에 든 것은 똑같은 한글 글꼴이라, 아무거나 써도 됩니다.
    TextMeshPro 는 가리키는 대로 불러올 뿐이고, 게임이 그것을 다른 데서
    쓰고 있는지와는 상관이 없습니다.

    그래도 **이미 Dynamic 폰트가 물고 있는** 것이 있으면 그것을 먼저 씁니다.
    유니티가 실제로 그 글꼴을 불러낸 적이 있다는 뜻이라 조금 더 안전합니다.
    """
    from . import tmpraw

    for o in objs:
        if o.type.name != "MonoBehaviour":
            continue
        try:
            head = tmpraw.read_head(o.get_raw_data())
        except Exception:                               # noqa: BLE001
            continue
        if head is None or head.mode != tmpraw.DYNAMIC:
            continue
        if head.src_file == 0 and head.src_path in patched_ids:
            return head.src_path
    return min(patched_ids) if patched_ids else 0


_NOT_TEXT = ("emoji", "icon", "symbol", "sprite", "dropcap")
# 반대로 **이것처럼 보여야만** 손댑니다. TextMeshPro 폰트는 거의 예외 없이
# 이름에 ``SDF`` 나 ``Font`` 가 들어갑니다. 이 관문이 없으면 스프라이트
# 모음(``DropCap Numbers`` 같은 것)까지 폰트로 알고 고쳐 버립니다 —
# 실제로 그렇게 해서 게임이 글자를 하나도 안 그리고 꺼졌습니다.
_LOOKS_TEXT = ("sdf", "font")


def dynamic_ready(objs) -> int:
    """이미 '실행 중에 그리기' 로 되어 있고 원본 글꼴을 물고 있는 폰트 수.

    이런 폰트가 하나라도 있으면, 그 원본 글꼴 안을 한글 글꼴로 갈아치우는
    것만으로 한글이 나옵니다. **굳은 폰트를 억지로 풀 이유가 없습니다.**
    """
    from . import tmpraw

    n = 0
    for o in objs:
        if o.type.name != "MonoBehaviour":
            continue
        try:
            head = tmpraw.read_head(o.get_raw_data())
        except Exception:                               # noqa: BLE001
            continue
        if head is not None and head.mode == tmpraw.DYNAMIC and head.src_path:
            n += 1
    return n


def _retarget_static(objs, rep, donor_id: int, progress) -> int:
    """원본 글꼴이 없는 Static 폰트를 Dynamic 으로 돌리고 한글 원본을 물립니다.

    IL2CPP 게임은 폰트 구조를 못 읽습니다. 그래도 TextMeshPro 폰트의 **앞머리**
    순서는 정해져 있어서, 거기 있는 두 값만 바이트로 직접 고칠 수 있습니다.
    자세한 것은 :mod:`gameloc.tmpraw` 를 보세요. 길이가 안 변하는 수정이라
    뒤쪽 자료를 밀어내지 않습니다.
    """
    from . import tmpraw

    n = 0
    for o in objs:
        if o.type.name != "MonoBehaviour":
            continue
        try:
            raw = o.get_raw_data()
        except Exception:                               # noqa: BLE001
            continue
        head = tmpraw.read_head(raw)
        if head is None or not head.needs_help():
            continue
        low = head.name.lower()
        if any(w in low for w in _NOT_TEXT) or \
                not any(w in low for w in _LOOKS_TEXT):
            progress(f"  · 건너뜀(글자 폰트가 아님): {head.name}")
            continue
        try:
            o.set_raw_data(tmpraw.retarget(raw, head, donor_id))
        except Exception as exc:                        # noqa: BLE001
            rep.notes.append(f"{head.name} 을 못 고쳤습니다: {exc}")
            continue
        rep.retargeted.append(head.name)
        n += 1
        progress(f"  · 굳은 폰트를 풀었습니다: {head.name} → 한글 원본 물림")
    return n


def _tex_size(obj) -> tuple[int, int] | None:
    if obj is None or obj.type.name != "Texture2D":
        return None
    try:
        tree = obj.read_typetree()
    except Exception:                                   # noqa: BLE001
        return None
    w, h = tree.get("m_Width"), tree.get("m_Height")
    if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
        return w, h
    return None


def _clear_stale_glyphs(objs, patched_ids: set[int], rep, progress) -> int:
    """글자가 이미 채워진 Dynamic 폰트의 글자표를 비웁니다.

    원본 글꼴을 방금 한글 글꼴로 갈아친 Dynamic 폰트만 대상입니다. 이미 구워진
    글자표가 옛 글꼴의 글리프 번호로 되어 있어서, 새 글꼴과 번호가 겹치면 한글이
    엉뚱한 한자로 그려집니다(RJ406835 에서 확인). 배치를 확신할 수 없는 폰트는
    건드리지 않습니다(:func:`gameloc.tmpraw.read_tables` 가 ``None``).
    """
    from . import tmpraw

    by_id = {o.path_id: o for o in objs}
    n = 0
    for o in objs:
        if o.type.name != "MonoBehaviour":
            continue
        try:
            raw = o.get_raw_data()
        except Exception:                               # noqa: BLE001
            continue
        head = tmpraw.read_head(raw)
        if head is None or head.mode != tmpraw.DYNAMIC:
            continue
        if head.src_file != 0 or head.src_path not in patched_ids:
            continue
        tables = tmpraw.read_tables(raw, head)
        if tables is None or not tables.populated:
            continue
        size = _tex_size(by_id.get(tables.atlas_path_id))
        if size is None:
            continue
        try:
            o.set_raw_data(tmpraw.clear_tables(raw, tables, *size))
        except Exception as exc:                        # noqa: BLE001
            rep.notes.append(f"{head.name} 의 글자표를 못 비웠습니다: {exc}")
            continue
        n += 1
        progress(f"  · 낡은 글자표를 비웠습니다: {head.name} "
                 f"(글리프 {tables.glyphs:,}개)")
    if n:
        rep.notes.append(
            f"이미 글자가 구워져 있던 '실행 중에 그리기' 폰트 {n}개의 글자표를 "
            "비웠습니다. 옛 글꼴의 글자 번호가 남아 한글이 엉뚱한 글자로 "
            "보이는 것을 막습니다. 게임이 돌면서 필요한 글자를 새로 그립니다.")
    return n


def _make_readable(objs, names: list[str], progress) -> int:
    """푼 폰트의 그림판을 '읽고 쓸 수 있음' 으로 바꿉니다.

    TextMeshPro 가 실행 중에 글자를 그려 넣으려면 그림판을 읽을 수 있어야
    합니다. 빌드된 게임은 대체로 잠겨 있습니다. 잘 안 되면 그냥 넘어갑니다.
    """
    want = {f"{n} Atlas" for n in names}
    n = 0
    for o in objs:
        if o.type.name != "Texture2D":
            continue
        try:
            tree = o.read_typetree()
        except Exception:                               # noqa: BLE001
            continue
        if str(tree.get("m_Name") or "") not in want:
            continue
        if tree.get("m_IsReadable"):
            continue
        tree["m_IsReadable"] = 1
        try:
            o.save_typetree(tree)
        except Exception:                               # noqa: BLE001
            continue
        n += 1
        progress(f"  · 그림판을 열었습니다: {tree.get('m_Name')}")
    return n


def _fix_legacy(env, objs, p, ttf: Path, rep, *, dry_run: bool,
                unfreeze: bool = False, progress):
    """TextMeshPro 구조를 못 읽을 때의 우회로.

    IL2CPP 게임에서는 TextMeshPro 폰트가 MonoBehaviour 라 구조를 못 읽습니다.
    그러면 대체 폰트를 걸 방법이 없습니다. 그런데 유니티 **기본 클래스**인
    옛날식 ``Font`` 는 언제나 읽힙니다.

    TextMeshPro 의 기본 대체 폰트(``LiberationSans SDF - Fallback``)는
    Dynamic 이고 이 ``LiberationSans`` 를 원본으로 씁니다. 그래서 그 안의
    글꼴 파일만 한글 글꼴로 바꿔 두면, 없는 글자를 실행 중에 만들어냅니다.

    **장담은 못 합니다.** 그 대체 폰트가 실제로 걸려 있는지 확인할 방법이
    없기 때문입니다(구조를 못 읽으니까요). 기본 설정이면 되고, 개발자가
    대체 폰트 목록을 비웠으면 안 됩니다. 안 되면 되돌리기로 원상복구됩니다.
    """
    by_id = {o.path_id: o for o in objs}
    data = ttf.read_bytes()
    patched_ids: set[int] = set()
    for pid, name in p.legacy:
        obj = by_id.get(pid)
        if obj is None:
            continue
        try:
            tree = obj.read_typetree()
        except Exception:                               # noqa: BLE001
            continue
        if not _set_font_data(tree, data, ttf.stem):
            continue
        obj.save_typetree(tree)
        patched_ids.add(pid)
        rep.patched.append(name)
        progress(f"  · 유니티 기본 글꼴 교체: {name} ← {ttf.name}")

    if not rep.patched:
        rep.notes.append("유니티 기본 글꼴에도 글꼴 데이터가 없었습니다.")
        return rep, None
    rep.legacy_route = True
    _clear_stale_glyphs(objs, patched_ids, rep, progress)

    # 굳어 있는(Static) 폰트를 풀어 줍니다. 기증자는 **이미 게임이 쓰고 있는**
    # 것을 고릅니다. 실제로 물려 있는 게 확인된 글꼴이라야 믿을 만합니다.
    # 굳은 폰트 풀기는 **위험합니다.** 실제 게임(Liar's Bar)에서 두 번
    # 확인했습니다: 풀면 게임이 켜지다 꺼지고, 안 풀면 멀쩡히 돕니다.
    # 이름 관문으로 폰트 아닌 것을 걸러도 결과는 같았습니다 — 진짜 폰트인
    # LiberationSans SDF · variex_regular SDF 만 풀어도 꺼졌습니다.
    #
    # 그래서 **이미 되는 폰트가 있으면 손대지 않습니다.** 그 경우 굳은
    # 폰트를 쓰는 화면만 □□□ 로 남지만, 게임이 아예 안 켜지는 것보다
    # 낫습니다. 되는 게 하나도 없을 때만 마지막 수로 풉니다.
    ready = dynamic_ready(objs)
    donor = _donor_font_id(objs, patched_ids) if (unfreeze or not ready) else 0
    if ready and unfreeze:
        rep.notes.append(
            "굳은 폰트도 풀라고 하셔서 풉니다. 게임이 안 켜지면 "
            "[원래대로] 를 누르고 이 설정을 끄세요.")
    elif ready:
        rep.notes.append(
            f"이 파일의 폰트 {ready}개는 이미 '실행 중에 그리기' 라, 방금 갈아친 "
            "글꼴을 그대로 씁니다. 굳어 있는 폰트는 그대로 두었습니다 — 그것까지 "
            "풀면 게임이 안 켜집니다. 그 폰트를 쓰는 화면이 있으면 거기만 "
            "□□□ 로 남습니다.")
    if donor:
        if _retarget_static(objs, rep, donor, progress):
            _make_readable(objs, rep.retargeted, progress)
            rep.notes.append(
                f"글자 그림이 통째로 구워져 있던 폰트 {len(rep.retargeted)}개를 "
                "'실행 중에 만들기' 로 돌리고 한글 글꼴을 물렸습니다. "
                "원래 있던 글자는 그대로 나오고, 없던 한글만 새로 그려집니다.")
    else:
        rep.notes.append(
            "한글을 넣어 줄 원본 글꼴이 없어, 굳은 폰트는 그대로 두었습니다.")

    rep.notes.append(
        "안 나오면 [원래대로 되돌리기] 를 누르세요. 파일은 그대로 있습니다.")
    if dry_run:
        return rep, None
    from .apply import _serialize
    return rep, _serialize(env)


# ---------------------------------------------------------------- 게임에 이미 있는 한글 폰트
#
# **진짜 게임(Freedom)에서 나온 길입니다.** 화면에 쓰는 글꼴은 일본어 7,129자만 구워진
# Static 폰트(``Corporate-Logo-Rounded-Bold-ver3 SDF``)라 한글이 □□□ 로 나왔습니다.
# 그런데 게임은 **한글이 전부 구워진 폰트를 이미 갖고 있었습니다** — 한국어 언어팩용
# ``korea SDF`` (한글 11,172자). 같은 직렬화 파일 안에 있어서, 일본어 폰트의 대체 폰트
# 표에 그 폰트 한 줄만 걸면 됩니다.
#
#   · 새 글꼴 파일을 게임에 넣지 않습니다 (크기·저작권 부담이 없습니다)
#   · 실행 중에 글자를 새로 그리지 않습니다 (Static 그대로 — 예전에 게임을 죽인 길이 아닙니다)
#   · 폰트 오브젝트를 저장했다 다시 읽어도 바이트가 그대로입니다 (확인했습니다)
#
# 한글이 구워진 폰트와 한글이 없는 폰트가 **같은 직렬화 파일에** 있을 때만 씁니다.
# 대체 폰트 표의 포인터가 그 파일 안의 번호로 적히기 때문입니다.

HANGUL_DONOR_MIN = 2000        # 이만큼 구워져 있어야 대체 폰트 자격이 있습니다
SHELL_MAX_CHARS = 50           # 이보다 글자가 적으면 껍데기(빈 폴백)라 손대지 않습니다


def _hangul_count(tree: dict) -> int:
    return sum(1 for u in unicodes_of(tree) if HANGUL[0] <= u <= HANGUL[1])


def _serial_of(o) -> str:
    return getattr(getattr(o, "assets_file", None), "name", "") or ""


def _tmp_fonts_by_file(objs) -> dict:
    """``{직렬화 파일 이름: [(오브젝트, 이름, 트리)]}`` — 읽을 수 있는 TextMeshPro 폰트만."""
    from .peek import script_class

    out: dict = {}
    for o in objs:
        if o.type.name != "MonoBehaviour":
            continue
        try:
            if not is_font_asset(script_class(o)):
                continue
            tree = o.read_typetree()
        except Exception:                                   # noqa: BLE001
            continue
        if isinstance(tree, dict) and "m_CharacterTable" in tree \
                and isinstance(tree.get("m_FallbackFontAssetTable"), list):
            out.setdefault(_serial_of(o), []).append(
                (o, str(tree.get("m_Name") or "?"), tree))
    return out


def _add_hangul_fallback(objs, rep, progress) -> list[tuple[str, int, int]]:
    """한글 없는 폰트마다, **같은 파일의** 한글 폰트를 대체 폰트로 겁니다.

    바꾼 것의 ``(직렬화 파일, 폰트 path_id, 대체 폰트 path_id)`` 목록을 돌려줍니다.
    """
    done: list[tuple[str, int, int]] = []
    for serial, fonts in _tmp_fonts_by_file(objs).items():
        donors = [(o, n, t) for o, n, t in fonts if _hangul_count(t) >= HANGUL_DONOR_MIN]
        if not donors:
            continue
        donors.sort(key=lambda x: (-_hangul_count(x[2]), x[0].path_id))
        d_obj, d_name, _d_tree = donors[0]
        ptr = {"m_FileID": 0, "m_PathID": d_obj.path_id}
        for o, name, tree in fonts:
            low = name.lower()
            if o is d_obj or _hangul_count(tree) or len(unicodes_of(tree)) < SHELL_MAX_CHARS:
                continue
            if any(w in low for w in _NOT_TEXT) or not any(w in low for w in _LOOKS_TEXT):
                continue
            table = tree["m_FallbackFontAssetTable"]
            if any(_pptr_id(x) == d_obj.path_id for x in table):
                continue
            table.append(dict(ptr))
            try:
                o.save_typetree(tree)
            except Exception as exc:                        # noqa: BLE001
                rep.notes.append(f"{name} 에 대체 폰트를 걸지 못했습니다: {exc}")
                continue
            rep.patched.append(name)
            rep.donor = d_name
            done.append((serial, o.path_id, d_obj.path_id))
            progress(f"  · 대체 폰트 연결: {name} → {d_name}")
    return done


def _verify_fallback(data: bytes, base_dir: Path, done, game_root: Path,
                     unity_version: str) -> None:
    """저장한 바이트를 다시 읽어 대체 폰트가 정말 걸렸는지 확인합니다. 안 맞으면 예외."""
    import io

    import UnityPy

    from . import typetree

    env = UnityPy.load(io.BytesIO(data), path=str(base_dir))
    typetree.attach(env, game_root, unity_version)
    by = {(_serial_of(o), o.path_id): o for o in env.objects}
    for serial, pid, donor in done:
        o = by.get((serial, pid))
        if o is None:
            raise RuntimeError(f"다시 읽으니 폰트 {pid} 가 사라졌습니다")
        table = o.read_typetree().get("m_FallbackFontAssetTable") or []
        if not any(_pptr_id(x) == donor for x in table):
            raise RuntimeError(f"폰트 {pid} 에 대체 폰트가 저장되지 않았습니다")


def _ids_collide(objs) -> bool:
    """여러 직렬화 파일에 같은 path_id 가 있는가(path_id 만으로는 오브젝트를 못 집는다)."""
    seen: dict[int, str] = {}
    for o in objs:
        s = _serial_of(o)
        prev = seen.setdefault(o.path_id, s)
        if prev != s:
            return True
    return False


def apply_to_file(target: Path, base_dir: Path, ttf: Path, *,
                  game_root: Path | None = None, unity_version: str = "",
                  dry_run: bool = False, unfreeze: bool = False,
                  os_font: bool = False,
                  progress=lambda m: None):
    """파일 하나를 고칩니다. ``(FontReport, 새 바이트 또는 None)``.

    ``os_font`` 는 굳은 글꼴을 **컴퓨터 글꼴로 그리기**(DynamicOS) 로
    바꿉니다. 구워진 글자는 그대로 두고 없는 글자만 채우는 길이라,
    원본 글꼴 참조가 없는 게임에서 유일하게 통합니다.
    """
    import UnityPy

    from . import typetree

    rep = FontReport(ttf=str(ttf))
    env = UnityPy.load(str(target), path=str(base_dir))
    # 폰트도 MonoBehaviour 입니다. 배포 빌드에는 그 구조가 안 들어 있어서
    # 게임 DLL 에서 되살리지 않으면 **폰트가 아예 안 보입니다.**
    typetree.attach(env, game_root or base_dir, unity_version)
    objs = list(env.objects)

    # os_font 는 **다른 길로 못 고친 것에만 쓰는 마지막 수단**입니다. 실제
    # 사례: os_font 를 켜면 이 자리에서 먼저 반환해 버려서, 도너·대체
    # 폰트로 멀쩡히 고쳐지던 화면까지 건너뛰고 DynamicOS 로만 시도했는데,
    # DynamicOS 전환이 이 컴퓨터의 유니티 런타임에서 새 글자를 하나도 못
    # 그리는 경우가 있어(이름을 정확히 맞춰도!) **이전엔 되던 곳까지**
    # □□□ 로 남았습니다. 그래서 이제 항상 아래 안전한 길부터 시도하고,
    # 그걸로 못 고친 폰트만 골라 os_font 를 얹습니다.
    fixed_ids: set[int] = set()
    changed = False
    verify_done = None

    # ① 게임에 이미 한글이 구워진 폰트가 있으면 그것을 대체 폰트로 겁니다. 글꼴을 새로
    #    넣지 않으니 가장 가볍고, 있으면 아래 옛 길보다 먼저입니다.
    done = _add_hangul_fallback(objs, rep, progress)
    if done:
        rep.existing_donor = True
        rep.fallback_count = len(done)
        changed = True
        verify_done = done
        for _serial, pid, donor_pid in done:
            fixed_ids.add(pid)
            fixed_ids.add(donor_pid)
    else:
        # ② 아래 옛 길들은 오브젝트를 path_id 하나로 찾습니다. 직렬화 파일이 여러 개고
        #    번호가 겹치면 **엉뚱한 오브젝트를 고칠 수 있어** 건너뜁니다(번호가 안
        #    겹치면 예전 그대로).
        if _ids_collide(objs):
            rep.notes.append("이 번들에는 같은 번호의 오브젝트가 여러 직렬화 파일에 있어, "
                             "번호로 폰트를 찾는 방식은 건너뜁니다.")
        else:
            p = plan(objs)
            rep.notes = list(p.notes)

            if p.legacy_only():
                # 레거시(IL2CPP, 구조 못 읽음) 경로는 안에서 unfreeze 까지 다
                # 처리하는 별개의 완결된 길이라, os_font 를 더 얹지 않습니다.
                return _fix_legacy(env, objs, p, ttf, rep, dry_run=dry_run,
                                   unfreeze=unfreeze, progress=progress)
            if p.ok():
                by_id = {o.path_id: o for o in objs}
                rep.donor = p.donor_name

                # ① 원본 글꼴 파일을 한글 글꼴로
                src = by_id.get(p.source_font_id)
                if src is None:
                    rep.notes.append(f"원본 글꼴 오브젝트({p.source_font_id})를 못 찾았습니다.")
                else:
                    try:
                        stree = src.read_typetree()
                    except Exception as exc:                # noqa: BLE001
                        rep.notes.append(f"원본 글꼴을 읽지 못했습니다: {exc}")
                        stree = None
                    if stree is not None and not _set_font_data(
                            stree, ttf.read_bytes(), ttf.stem):
                        rep.notes.append("이 글꼴 오브젝트에는 글꼴 데이터가 들어 있지 않습니다.")
                    elif stree is not None:
                        src.save_typetree(stree)
                        rep.source_font_name = str(stree.get("m_Name") or "")
                        progress(f"  · 원본 글꼴 교체: {stree.get('m_Name')} ← {ttf.name}")
                        fixed_ids.add(p.source_font_id)
                        changed = True

                        # ② 한글 없는 폰트들에 대체 폰트로 걸기
                        ptr = {"m_FileID": 0, "m_PathID": p.donor_path_id}
                        for pid, name in p.targets:
                            obj = by_id.get(pid)
                            if obj is None:
                                continue
                            try:
                                tree = obj.read_typetree()
                            except Exception:                # noqa: BLE001
                                continue
                            table = tree.get("m_FallbackFontAssetTable")
                            if not isinstance(table, list):
                                continue
                            fixed_ids.add(pid)
                            if any(_pptr_id(x) == p.donor_path_id for x in table):
                                continue
                            table.append(dict(ptr))
                            tree["m_FallbackFontAssetTable"] = table
                            obj.save_typetree(tree)
                            rep.patched.append(name)
                            progress(f"  · 대체 폰트 연결: {name} → {p.donor_name}")

    # ③ 위 길로 못 고친(진짜 되살릴 원본도 도너도 없는) 폰트만 마지막으로 os_font.
    if os_font:
        got = _thaw_all_to_os(objs, ttf, rep, progress=progress, skip_ids=fixed_ids)
        if got:
            changed = True

    # ④ 레거시 UnityEngine.UI.Text 가 쓰는 옛날식 Font. TextMeshPro 와는 완전히
    #    별개 시스템이지만, TMP 의 "원본 글꼴"(``m_SourceFontFile``)도 이
    #    같은 옛날식 ``Font`` 클래스라서 ``fixed_ids`` 로 이미 손댄 것과
    #    번호가 겹칠 수 있습니다 — 그런 건 이미 한글 글꼴로 바뀐 것이니
    #    또 건드리지 않게 건너뜁니다(위에서 뭘 했든 나머지는 항상 봅니다).
    legacy_ui_changed = _fix_legacy_ui_text(objs, ttf, rep, progress, skip_ids=fixed_ids)
    if legacy_ui_changed:
        changed = True

    if not changed or dry_run:
        return rep, None
    from .apply import _serialize
    data = _serialize(env)
    if verify_done:
        _verify_fallback(data, base_dir, verify_done, game_root or base_dir, unity_version)
    return rep, data




def _gb(n: int) -> str:
    return f"{n / 1024 ** 3:.1f}GB" if n >= 1024 ** 3 else f"{n / 1024 ** 2:,.0f}MB"


def fix(root: Path, *, ttf: Path | None = None, dry_run: bool = False,
        unfreeze: bool = False, os_font: bool = False,
        progress=lambda m: None) -> FontReport:
    """게임 폴더의 폰트를 고칩니다. 원본은 백업합니다.

    ``unfreeze`` 는 **위험한 마지막 수**입니다. 글자 그림이 통째로 구워진
    폰트를 '실행 중에 그리기' 로 돌립니다. 그 폰트를 쓰는 화면의 □□□ 가
    풀릴 수도 있지만, 실제 게임에서 두 번 다 게임이 안 켜졌습니다.
    그래서 기본은 꺼 두고, 사람이 알고 켤 때만 합니다.
    """
    from .apply import _backup
    from .detect import detect

    info = detect(root)
    if info.engine not in ("unity", "renpy"):
        rep = FontReport()
        rep.notes.append("유니티와 Ren'Py 게임에서만 됩니다.")
        return rep

    font = Path(ttf) if ttf else find_system_font()
    if font is None or not Path(font).is_file():
        rep = FontReport(engine=info.engine)
        rep.notes.append("이 컴퓨터에서 한글 글꼴을 찾지 못했습니다. "
                         "글꼴 파일(.ttf/.otf)을 직접 골라주세요.")
        return rep
    font = Path(font)
    progress(f"쓸 글꼴: {font}")

    if info.engine == "renpy":
        rep = _fix_renpy(info, font, auto=ttf is None,
                         dry_run=dry_run, progress=progress)
        _remember(info.root, rep, ttf, unfreeze, os_font, dry_run)
        return rep

    merged = FontReport(ttf=str(font))
    for i, path in enumerate(info.asset_files, 1):
        # **너무 무거운 덩이는 손대지 않습니다.**
        #
        # 여기는 문장 뽑기와 달리 **본체에서** 돕니다. 2.4GB 짜리
        # data.unity3d 를 열다 메모리가 모자라면 그 파일만 잃는 게 아니라
        # 프로그램이 통째로 멈춥니다 — 사용자에게는 무한로딩으로 보입니다.
        # 진짜 게임에서 [마지막 수단] 이 여기서 굳었습니다.
        heavy = bundle.weigh(path)
        if heavy.is_bundle:
            hard = heavy.blocking()
            if hard:
                progress(f"  · {path.name} 은 건너뜁니다 — {hard}")
                merged.notes.append(
                    f"{path.name}({_gb(heavy.compressed)})는 이 컴퓨터에서 열기 "
                    "무거워 글꼴을 안 고쳤습니다. 그 파일 안 글꼴만 □□□ 로 "
                    "남을 수 있습니다.")
                continue
            progress(f"  [{i}/{len(info.asset_files)}] {path.name} — "
                     f"{heavy.headline()}")
        else:
            progress(f"  [{i}/{len(info.asset_files)}] {path.name}")
        try:
            rep, data = apply_to_file(path, path.parent, font,
                                      game_root=info.root,
                                      unity_version=info.unity_version,
                                      dry_run=dry_run, unfreeze=unfreeze,
                                      os_font=os_font, progress=progress)
        except Exception as exc:                        # noqa: BLE001
            merged.notes.append(f"{path.name}: {type(exc).__name__}: {exc}")
            continue
        if rep.patched:
            if rep.donor:
                merged.donor = rep.donor
            merged.legacy_route = merged.legacy_route or rep.legacy_route
            if rep.existing_donor:
                merged.existing_donor = True
                merged.fallback_count += rep.fallback_count
            merged.retargeted += rep.retargeted
            merged.patched += rep.patched
            if data is not None:
                _backup(path, info.root)
                path.write_bytes(data)
                # **장부도 같이 고쳐야 합니다.**
                #
                # Addressables 번들은 크기가 catalog.bin 에 따로 적혀 있어서,
                # 파일만 바꾸면 유니티가 그 번들을 거부합니다 — 게임이 아예
                # 안 켜집니다. 번역 넣을 때 겪은 것과 똑같은 사고입니다.
                # 글꼴 고치기도 같은 파일을 건드리므로 여기도 필요합니다.
                if catalog.under_aa(path):
                    cat = catalog.is_addressables(info.root)
                    if cat is not None:
                        _backup(cat, info.root)
                        catalog.retune(cat, {path.name: path.stat().st_size})
                        progress(f"    · 장부도 맞췄습니다 ({path.name})")
                merged.changed_files.append(str(path.relative_to(info.root)))
                progress(f"  ✓ {path.relative_to(info.root)}")
        merged.notes += rep.notes

    # 파일마다 같은 말이 반복되면 읽기만 어렵습니다.
    seen: set[str] = set()
    merged.notes = [n for n in merged.notes
                    if not (n in seen or seen.add(n))]
    _remember(info.root, merged, ttf, unfreeze, os_font, dry_run)
    return merged


def _remember(root: Path, rep: FontReport, ttf, unfreeze: bool, os_font: bool,
              dry_run: bool) -> None:
    """이 폰트 고치기를 게임 폴더의 장부에 적어 둡니다 — [게임에 적용] 을 다시 눌러 게임이
    원본으로 되돌려졌다 다시 쓰이는 사이에 폰트가 사라지지 않게 다시 걸기 위해서입니다
    (``fixups`` 머리말). 장부를 못 적어도 폰트 고치기 자체는 성공입니다."""
    if dry_run or not rep.changed_files:
        return
    try:
        from . import fixups
        fixups.record_font(Path(root), {"ttf": str(ttf) if ttf else None,
                                        "unfreeze": bool(unfreeze),
                                        "os_font": bool(os_font)},
                           list(rep.changed_files))
    except Exception:                                   # noqa: BLE001
        pass


# ---------------------------------------------------------------- Ren'Py

def _fix_renpy(info, font: Path, *, auto: bool = False, dry_run: bool = False,
               progress=lambda m: None) -> FontReport:
    """Ren'Py 는 유니티와 딴판이라 방법도 다릅니다.

    유니티는 글꼴이 게임 파일 **안에** 구워져 있어서 파일을 뜯어고쳐야 했지만,
    Ren'Py 는 ``game/`` 안의 글꼴 **파일**을 이름으로 불러다 씁니다. 그래서
    한글 글꼴을 하나 넣어 두고 "이 이름으로 부르면 저걸 줘라" 하고 일러두면
    끝입니다. ``config.font_replacement_map`` 이 바로 그 자리입니다.

    원본은 하나도 안 건드립니다. 파일 두 개가 새로 생길 뿐이고, 되돌리기는
    그 둘을 지우는 것으로 끝납니다.
    """
    from .apply import _remember_added
    from .engines import renpy

    rep = FontReport(ttf=str(font), engine="renpy")
    game = info.rp_game
    if game is None:
        rep.notes.append("Ren'Py 의 game 폴더를 찾지 못했습니다.")
        return rep

    # 글꼴 모음(.ttc)은 첫 번째 글꼴만 쓰이므로, 우리가 알아서 고른 경우라면
    # 낱개 글꼴이 있는지 한 번 더 봅니다. 사용자가 직접 고른 건 존중합니다.
    if auto and font.suffix.lower() == ".ttc":
        from .fontlist import scan
        try:
            alt = next((Path(f.path) for f in scan()
                        if Path(f.path).suffix.lower() in (".ttf", ".otf")), None)
        except Exception:                               # noqa: BLE001
            alt = None
        if alt is not None:
            progress(f"글꼴 모음 대신 낱개 글꼴을 씁니다: {alt.name}")
            font = alt
            rep.ttf = str(font)

    suffix = font.suffix.lower()
    if suffix not in renpy.FONT_SUFFIXES:
        rep.notes.append(f"{suffix} 는 Ren'Py 가 읽지 못하는 형식입니다. "
                         ".ttf 나 .otf 를 골라주세요.")
        return rep
    if suffix == ".ttc":
        rep.notes.append("글꼴 모음(.ttc)은 Ren'Py 가 첫 번째 글꼴만 씁니다. "
                         "원하는 모양이 아니면 .ttf 를 골라보세요.")

    names = renpy.font_names(game)
    rep.patched = names
    rep.donor = font.stem
    have = renpy.font_files(game)
    progress(f"게임이 부르는 글꼴 {len(names)}개 "
             f"(game 폴더 안 글꼴 파일 {len(have)}개)")

    if dry_run:
        return rep

    dest = game / (renpy.FONT_BASE + suffix)
    hook = game / renpy.FONT_RPY
    try:
        dest.write_bytes(font.read_bytes())
        hook.write_text(renpy.font_hook_source(dest.name, names), "utf-8")
    except OSError as exc:
        rep.notes.append(f"파일을 넣지 못했습니다: {exc}")
        return rep

    # 전에 넣은 후크의 컴파일본이 남아 있으면 옛 설정이 되살아납니다.
    stale = game / (renpy.FONT_RPY + "c")
    if stale.is_file():
        try:
            stale.unlink()
        except OSError:
            pass

    root = Path(info.root)
    try:
        _remember_added(root, [dest, hook])
    except Exception as exc:                            # noqa: BLE001
        rep.notes.append(f"되돌리기 목록에 적지 못했습니다: {exc}")

    for p in (dest, hook):
        rep.changed_files.append(str(p.relative_to(root)))
        progress(f"  ✓ {p.relative_to(root)}")
    return rep


# ── 굳은 글꼴을 '컴퓨터 글꼴로 그리기' 로 바꾸기 ─────────────────────────
#
# **진짜 게임에서 나온 길입니다.** 로그가 정확히 짚어 줬습니다.
#
#   The character \uAC8C was not found in the
#   [Corporate-Logo-Rounded-Bold-ver3 SDF] font asset
#   or any potential fallbacks.
#
# 그 글꼴을 열어 보니 이랬습니다.
#
#   m_AtlasPopulationMode  0        Static — 글자 그림이 8192×8192 에 구워짐
#   m_SourceFontFile       없음      되살릴 원본 참조가 아예 없음
#   m_CharacterTable       7,129자   전부 일본어
#   m_FallbackFontAssetTable  0개    대신 봐 줄 글꼴도 없음
#
# 한글이 구워져 있지도, 새로 그릴 원본이 있지도, 대신 볼 글꼴이 있지도
# 않습니다. 그래서 무슨 수를 써도 □□□ 입니다.
#
# ## Dynamic(1) 이 아니라 DynamicOS(2) 입니다
#
# 예전에 게임을 두 번 죽인 것은 **Dynamic(1)** 로 바꿨을 때입니다. 그건
# ``m_SourceFontFile`` 이 가리키는 원본 글꼴 에셋에서 글자를 그려 내는
# 방식인데, 이 글꼴에는 그게 없으니 죽는 게 당연했습니다.
#
# **DynamicOS(2)** 는 원본 에셋 대신 **컴퓨터에 깔린 글꼴 파일**에서
# 그립니다. 참조가 없어도 되는, 바로 이 상황을 위한 길입니다.
# 구워진 일본어 7,129자는 그대로 두고 **없는 글자만** 채웁니다.

# TMP 의 채우기 방식. 숫자는 TextMeshPro 가 정한 것입니다.
STATIC, DYNAMIC, DYNAMIC_OS = 0, 1, 2


def _os_font_name(ttf: Path) -> str:
    """TMP 가 컴퓨터에서 찾을 이름. 파일 이름이 아니라 **글꼴 이름**입니다.

    잘 아는 시스템 글꼴(맑은 고딕·굴림·바탕·나눔고딕 등)은 실제 게임에서
    확인된 영문 이름을 그대로 씁니다. 그 밖의 글꼴은 **파일 이름만 보고
    짐작하면 안 됩니다** — ``H2GTRE.TTF`` 나 ``NanumSquareR.otf`` 처럼
    파일 이름이 실제 글꼴 이름과 다른 경우가 흔합니다. 사용자가 [쓸 글꼴]
    목록에서 그런 글꼴을 고르면, TMP 가 그 이름의 시스템 글꼴을 못 찾아
    **이미 잘 나오던 글자까지** □□□ 로 깨지는 신고가 있었습니다(자동
    고르기는 아는 글꼴만 후보라 안 걸렸습니다). 그래서 모르는 글꼴은
    :mod:`fontlist` 처럼 파일을 직접 열어 이름표(name 표)에서 진짜
    이름을 읽습니다 — 적어도 파일 이름보다는 훨씬 더 실제 글꼴 이름에
    가깝습니다.
    """
    known = {
        "malgun": "Malgun Gothic", "malgunbd": "Malgun Gothic",
        "gulim": "Gulim", "batang": "Batang", "dotum": "Dotum",
        "notosanskr-regular": "Noto Sans KR", "notosanscjkkr-regular": "Noto Sans KR",
        "nanumgothic": "NanumGothic", "applegothic": "AppleGothic",
    }
    # **윈도우 경로를 리눅스에서 다루면 안 쪼개집니다.**
    #   Path(r"C:\Windows\Fonts\malgun.ttf").stem  →  "C:\Windows\Fonts\malgun"
    # 우리 시험은 리눅스에서 도는데 진짜 경로는 윈도우 것이라, 여기서
    # 어긋나면 TMP 가 못 찾는 이름을 넣게 됩니다.
    raw = str(ttf).replace("\\", "/").rsplit("/", 1)[-1]
    stem = raw.rsplit(".", 1)[0] if "." in raw else raw
    if stem.lower() in known:
        return known[stem.lower()]

    from . import fontlist

    try:
        info = fontlist.read_font(Path(ttf))
    except Exception:                                     # noqa: BLE001
        info = None
    if info and info.name:
        return info.name
    return stem


def thaw_to_os(tree: dict, ttf: Path) -> bool:
    """굳은 TMP 글꼴 하나를 '컴퓨터 글꼴로 그리기' 로 바꿉니다.

    바꾼 게 있으면 ``True``. 이미 그렇게 되어 있거나 TMP 글꼴이 아니면
    ``False`` — 손대지 않았다는 뜻입니다.
    """
    if not isinstance(tree, dict) or "m_AtlasPopulationMode" not in tree:
        return False
    if tree.get("m_AtlasPopulationMode") != STATIC:
        return False                    # 이미 실행 중에 그리는 글꼴입니다
    if not (tree.get("m_CharacterTable") or tree.get("m_GlyphTable")):
        return False                    # 글자가 없으면 글꼴이 아닙니다

    tree["m_AtlasPopulationMode"] = DYNAMIC_OS
    if "InternalDynamicOS" in tree:
        tree["InternalDynamicOS"] = 1
    # TMP 는 이 이름으로 컴퓨터 글꼴을 찾습니다. 경로가 아니라 이름입니다.
    name = _os_font_name(ttf)
    if "m_SourceFontFilePath" in tree:
        tree["m_SourceFontFilePath"] = str(ttf)
    cs = tree.get("m_CreationSettings")
    if isinstance(cs, dict) and "sourceFontFileName" in cs:
        cs["sourceFontFileName"] = name
    fi = tree.get("m_FaceInfo")
    if isinstance(fi, dict):
        # 이름을 바꾸면 TMP 가 **구워진 글자까지** 다시 그리려 듭니다.
        # 일본어는 이미 잘 나오고 있으니 건드리지 않습니다.
        pass
    # 여러 장짜리 이미지를 켜 둬야 새 글자를 담을 자리가 생깁니다.
    if "m_IsMultiAtlasTexturesEnabled" in tree:
        tree["m_IsMultiAtlasTexturesEnabled"] = 1
    return True


# ── 레거시 UnityEngine.UI.Text 가 쓰는 옛날식 Font ───────────────────────
#
# **진짜 게임(RJ01700968)에서 나온 길입니다.** 설정 화면 버튼(`그것을 끄지
# 마십시오`)의 글자 **데이터는 완전히 정상**이었는데 화면엔 `그것　끄` /
# `마십시오` 처럼 글자가 빠져 보였습니다. 그 텍스트 오브젝트를 열어보니
# TextMeshPro 가 아니라 옛날 방식인 ``UnityEngine.UI.Text``(``m_FontData``
# 안에 ``m_Font`` 를 가리킴) 였고, 그 ``Font`` 오브젝트는 게임 자체의 손글씨
# 느낌 장식 폰트(예: "setofont", 13MB 짜리 임베디드 TTF)를 그대로 쓰고
# 있었습니다 — 한글이 하나도 없는 폰트라, 없는 글자를 조용히 안 그린 겁니다.
#
# TMP 를 고쳐도 이 시스템은 전혀 안 건드려집니다 — 완전히 별개의 컴포넌트라
# ``m_AtlasPopulationMode`` 같은 TMP 개념이 없습니다. 그래서 항상 따로
# 확인합니다.
#
# ``Font`` 오브젝트는 옛날 유니티에도 ``m_FallbackFonts`` (대체 글꼴 표)가
# 있어서, TMP 처럼 "같은 파일 안에 이미 한글이 든 Font 가 있으면 그것을
# 대체 폰트로 건다" 를 먼저 시도합니다(원래 글꼴 모양이 그대로 유지됩니다).
# 그런 것이 없으면(실제로는 이 경우가 대부분입니다 — 이 게임에는 다른 Font
# 오브젝트가 아예 없었습니다) 마지막 수로 그 폰트 **자체**를 한글 글꼴로
# 바꿔칩니다. 그러면 그 폰트를 쓰는 **다른** 글자(주로 아직 번역 안 된
# 원문)도 모양이 바뀌지만, 아무것도 안 보이는 것보다는 낫고 한글은 확실히
# 나옵니다.

_HANGUL_PROBE = (0xAC00, 0xD55C, 0xB098)      # 가, 한, 나 — fontlist 와 같은 표본


def _legacy_has_hangul(data: bytes) -> bool:
    """임베디드 TTF/OTF 바이트 안에 한글 글리프가 있는가.

    :mod:`gameloc.fontlist` 가 컴퓨터에 깔린 글꼴 파일을 읽을 때 쓰는 것과
    똑같은 sfnt 표 읽기를 재사용합니다 — 새 의존성 없이, 이미 검증된 길로.
    """
    from . import fontlist

    try:
        for off in fontlist._font_offsets(data):
            tables = fontlist._tables(data, off)
            if tables and fontlist._cmap_has(data, tables, _HANGUL_PROBE):
                return True
    except Exception:                                       # noqa: BLE001
        pass
    return False


def _legacy_font_objs(objs):
    """``Font`` 타입 오브젝트만, 직렬화 파일별로 묶어서 돌려줍니다.

    ``m_FontData`` 가 비어 있으면(=글꼴 파일이 안 박혀 있고 이름만으로
    컴퓨터 글꼴을 찾는 식) 손댈 데이터가 없어 뺍니다.
    """
    out: dict[str, list] = {}
    for o in objs:
        if o.type.name != "Font":
            continue
        try:
            tree = o.read_typetree()
        except Exception:                                   # noqa: BLE001
            continue
        fd = tree.get("m_FontData")
        if isinstance(fd, (bytes, bytearray)):
            fd = bytes(fd)
        elif isinstance(fd, str):
            fd = fd.encode("latin-1", "ignore")            # 테스트용 가짜 환경
        else:
            continue
        if len(fd) < 100:
            continue
        out.setdefault(_serial_of(o), []).append(
            (o, str(tree.get("m_Name") or "?"), tree, fd))
    return out


def _fix_legacy_ui_text(objs, ttf: Path, rep, progress,
                        skip_ids=frozenset()) -> set[int]:
    """레거시 ``UnityEngine.UI.Text`` 가 쓰는 옛날식 ``Font`` 에 한글을 넣습니다.

    바뀐 ``Font`` 오브젝트의 path_id 모음을 돌려줍니다(비었으면 손댄 게
    없다는 뜻).
    """
    changed: set[int] = set()
    n_fallback = 0
    n_replaced = 0
    data = None
    for serial, fonts in _legacy_font_objs(objs).items():
        fonts = [f for f in fonts if f[0].path_id not in skip_ids]
        donors = [f for f in fonts if _legacy_has_hangul(f[3])]
        targets = [f for f in fonts if not _legacy_has_hangul(f[3])]
        if not targets:
            continue
        if donors:
            d_obj, d_name, _dt, _dd = donors[0]
            ptr = {"m_FileID": 0, "m_PathID": d_obj.path_id}
            for o, name, tree, _d in targets:
                table = tree.get("m_FallbackFonts")
                if not isinstance(table, list):
                    continue
                if any(_pptr_id(x) == d_obj.path_id for x in table):
                    continue
                table.append(dict(ptr))
                tree["m_FallbackFonts"] = table
                o.save_typetree(tree)
                changed.add(o.path_id)
                rep.patched.append(name)
                n_fallback += 1
                progress(f"  · (레거시) 대체 글꼴 연결: {name} → {d_name}")
        else:
            if data is None:
                data = ttf.read_bytes()
            for o, name, tree, _d in targets:
                if not _set_font_data(tree, data, ttf.stem):
                    continue
                o.save_typetree(tree)
                changed.add(o.path_id)
                rep.patched.append(name)
                n_replaced += 1
                progress(f"  · (레거시) 글꼴을 통째로 바꿨습니다: {name} ← {ttf.name}")
    if n_fallback:
        rep.notes.append(
            f"레거시(UnityEngine.UI.Text) 글꼴 {n_fallback}개에 대체 글꼴을 "
            "걸었습니다 — 원래 모양은 그대로입니다.")
    if n_replaced:
        rep.notes.append(
            f"레거시(UnityEngine.UI.Text) 글꼴 {n_replaced}개는 한글이 든 "
            f"글꼴이 게임 안에 없어 그 글꼴 자체를 한글 글꼴({ttf.name})로 "
            "바꿨습니다 — 그 글꼴을 쓰던 다른 글자(주로 아직 번역 안 된 "
            "원문)도 모양이 함께 바뀝니다.")
    return changed


def _thaw_all_to_os(objs, ttf: Path, rep, *, progress, skip_ids=frozenset()) -> int:
    """이 파일 안의 굳은 TMP 글꼴을 전부 '컴퓨터 글꼴로 그리기' 로.

    ``skip_ids`` 에 든 오브젝트는 건드리지 않습니다 — 이미 도너·대체 폰트로
    (더 안전하게) 고쳐진 폰트입니다. 실제로 DynamicOS 전환이 **이 컴퓨터의
    유니티 런타임에서 새 글자를 하나도 못 그리는** 경우가 있었습니다(이름이
    맞아도!) — 그런데 원래 먼저 시도하던 도너/대체 폰트 길은 os_font 를
    켜면 아예 건너뛰어서, 잘 고쳐지던 화면까지 □□□ 로 남는 사고가 났습니다.
    그래서 이제 os_font 는 다른 길이 못 고친 것에만 마지막으로 씁니다.
    """
    done = 0
    for o in objs:
        if o.type.name != "MonoBehaviour" or o.path_id in skip_ids:
            continue
        try:
            tree = o.read_typetree()
        except Exception:                               # noqa: BLE001
            continue
        name = str(tree.get("m_Name") or "")
        if not thaw_to_os(tree, ttf):
            continue
        o.save_typetree(tree)
        rep.patched.append(name)
        rep.retargeted.append(name)
        done += 1
        progress(f"  · 컴퓨터 글꼴로 그리기: {name} ← {_os_font_name(ttf)}")
    if not done:
        rep.notes.append("이 파일에는 굳은 TextMeshPro 글꼴이 없었습니다.")
    return done
