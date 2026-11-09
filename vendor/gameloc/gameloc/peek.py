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

"""유니티 빌드 안에 **무엇이 들어 있는지** 그대로 적어주는 진단기.

추출기가 아무것도 못 찾을 때, 원인은 셋 중 하나입니다.

1. 대사가 우리가 안 보는 자리에 있다 (다른 파일, 다른 오브젝트 종류)
2. UnityPy 가 이 유니티 버전을 못 읽는다
3. 대사가 파일이 아니라 다른 데 있다 (Addressables, 암호화, 서버)

셋은 대응이 전혀 다른데 겉으로는 똑같이 '0개' 로 보입니다. 그래서 짐작하지
말고, 파일 안을 열어 **있는 그대로** 적어놓고 그걸 보고 판단합니다.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from pathlib import Path

from . import typetree, unityraw
from .detect import detect

PREVIEW = 240
SAMPLES = 6
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿]")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _clean(s: str, limit: int = PREVIEW) -> str:
    s = _CTRL.sub("·", s or "")
    s = s.replace("\n", "⏎").replace("\t", "→")
    return s[:limit] + ("…" if len(s) > limit else "")


def _strings_in(node, out: list, depth: int = 0) -> None:
    if depth > 12 or len(out) > 400:
        return
    if isinstance(node, str):
        if node.strip():
            out.append(node)
    elif isinstance(node, dict):
        for v in node.values():
            _strings_in(v, out, depth + 1)
    elif isinstance(node, (list, tuple)):
        for v in node:
            _strings_in(v, out, depth + 1)


def report(root: Path, *, include_scenes: bool = True,
           max_files: int = 12) -> str:
    """진단 보고서. **커뮤니티에 올리라고 안내하는 파일**이라, 내보내기
    전에 집 경로와 계정 이름을 지웁니다 (:mod:`gameloc.privacy`)."""
    from . import privacy

    return privacy.scrub(_report(root, include_scenes=include_scenes,
                                 max_files=max_files))


def _report(root: Path, *, include_scenes: bool = True,
            max_files: int = 12) -> str:
    lines: list[str] = []
    add = lines.append

    info = detect(root)
    add("=" * 70)
    add("gameloc 진단 보고서")
    add("=" * 70)
    add(f"폴더   : {info.root}")
    add(f"엔진   : {info.summary()}")
    add("")

    if info.engine == "renpy":
        _renpy_report(info, add)
        return "\n".join(lines)

    if info.engine != "unity":
        add("이 보고서는 유니티와 Ren'Py 게임만 볼 수 있습니다.")
        return "\n".join(lines)

    try:
        import UnityPy
        add(f"UnityPy: {getattr(UnityPy, '__version__', '?')}")
    except ImportError:
        add("UnityPy 가 설치되어 있지 않습니다.  pip install UnityPy")
        return "\n".join(lines)

    add(f"타입정보 되살리기: "
        + ("가능 — " + typetree.describe(info.root)
           if typetree.generator(info.root, info.unity_version) is not None
           else ("패키지 없음 (pip install " + typetree.PACKAGE + ")"
                 if not typetree.available()
                 else "실패 — " + typetree.describe(info.root))))
    add("")

    targets = list(info.asset_files)
    if not include_scenes:
        targets = [p for p in targets if not p.name.lower().startswith("level")]
    add(f"검사할 파일 {len(targets)}개")
    add("")

    for path in targets[:max_files]:
        add("-" * 70)
        size = path.stat().st_size
        add(f"■ {path.relative_to(info.root)}   ({size / 1048576:.1f} MB)")
        try:
            env = UnityPy.load(str(path), path=str(path.parent))
            typetree.attach(env, info.root, info.unity_version)
            objs = list(env.objects)
        except Exception as exc:  # noqa: BLE001
            add(f"   ! 열지 못했습니다: {type(exc).__name__}: {exc}")
            add("   → UnityPy 가 이 유니티 버전을 못 읽는 것일 수 있습니다.")
            continue

        kinds = Counter(o.type.name for o in objs)
        add(f"   오브젝트 {len(objs)}개: "
            + ", ".join(f"{k}×{v}" for k, v in kinds.most_common(10)))

        _dump_textassets(objs, add)
        _dump_monobehaviours(objs, add)
        font_report(objs, add)
        _blob_report(objs, add)

    if len(targets) > max_files:
        add(f"… 외 {len(targets) - max_files}개 파일 생략")

    add("")
    lines.extend(dll_report(info.root))

    # 게임 폴더 안, *_Data 밖에 놓인 데이터 파일도 봅니다
    lines.extend(_outside_report(info.root))

    add("")
    add("=" * 70)
    add("이 파일을 그대로 보내주시면 어디에 대사가 있는지 판단할 수 있습니다.")
    return "\n".join(lines)


OUTSIDE_SKIP = {".dll", ".exe", ".pdb", ".config", ".so", ".dylib"}


def _outside_report(root: Path) -> list[str]:
    """*_Data 밖에 놓인 파일. 자체 포맷 시나리오가 여기 있는 경우가 있습니다."""
    lines: list[str] = []
    add = lines.append
    data_dirs = {p.name for p in root.glob("*_Data")}
    found = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        parts = p.relative_to(root).parts
        if parts[0] in data_dirs or parts[0] in ("__gameloc_backup__",
                                                 "MonoBleedingEdge", "D3D12"):
            continue
        if p.suffix.lower() in OUTSIDE_SKIP:
            continue
        found.append(p)
    add("=" * 70)
    add(f"■ *_Data 밖의 파일 {len(found)}개")
    for p in found[:25]:
        size = p.stat().st_size
        head = ""
        try:
            raw = p.read_bytes()[:400]
            txt = raw.decode("utf-8", errors="ignore")
            jp = len(_CJK.findall(txt))
            head = f"  일본어 {jp}자  {_clean(txt, 90)}"
        except OSError:
            pass
        add(f"   · {p.relative_to(root)}  {size:,}바이트{head}")
    return lines


def _dump_textassets(objs, add) -> None:
    tas = [o for o in objs if o.type.name == "TextAsset"]
    if not tas:
        return
    add(f"   ── TextAsset {len(tas)}개 ──")
    shown = 0
    for o in tas:
        try:
            d = o.read()
            name = getattr(d, "m_Name", "") or ""
            body = getattr(d, "m_Script", "") or ""
        except Exception as exc:  # noqa: BLE001
            add(f"      ! 읽기 실패: {type(exc).__name__}: {exc}")
            continue
        if isinstance(body, (bytes, bytearray)):
            try:
                body = bytes(body).decode("utf-8")
            except UnicodeDecodeError:
                add(f"      · {name}  (바이너리 {len(body)}바이트 — 암호화 가능성)")
                continue
        jp = len(_CJK.findall(body))
        add(f"      · {name}  {len(body)}자, 일본어 {jp}자")
        if shown < 8:
            add(f"          {_clean(body)}")
            shown += 1


def script_class(obj) -> str:
    """이 MonoBehaviour 가 어느 C# 클래스인가.

    타입 정보를 못 읽어도 이건 알 수 있습니다. MonoBehaviour 의 **공통 머리
    부분**(m_GameObject, m_Enabled, m_Script, m_Name)은 유니티가 아는 구조라
    언제나 읽히고, 거기 담긴 참조를 따라가면 클래스 이름이 나옵니다.
    무엇을 못 읽고 있는지 아는 게 원인 파악의 전부입니다.
    """
    try:
        head = obj.parse_monobehaviour_head()
        script = head.m_Script.deref_parse_as_object()
    except Exception:                                   # noqa: BLE001
        return "?"
    ns = getattr(script, "m_Namespace", "") or ""
    cls = getattr(script, "m_ClassName", "") or "?"
    asm = getattr(script, "m_AssemblyName", "") or ""
    full = f"{ns}.{cls}" if ns else cls
    return f"{full}  ({asm})" if asm else full


def _dump_monobehaviours(objs, add) -> None:
    mbs = [o for o in objs if o.type.name == "MonoBehaviour"]
    if not mbs:
        return
    ok = blind = 0
    best: list[tuple[int, str, list[str]]] = []
    blind_classes: Counter = Counter()
    blind_reasons: Counter = Counter()
    blind_ids: set = set()
    all_classes: Counter = Counter()
    for o in mbs:
        try:
            tree = o.read_typetree()
        except Exception as exc:  # noqa: BLE001
            blind += 1
            blind_classes[script_class(o)] += 1
            blind_reasons[_clean(str(exc), 110)] += 1
            blind_ids.add(o.path_id)
            continue
        ok += 1
        if not isinstance(tree, dict):
            continue
        found: list[str] = []
        _strings_in(tree, found)
        jp = [s for s in found if _CJK.search(s)]
        if jp:
            best.append((len(jp), str(tree.get("m_Name") or "?"), jp))

    add(f"   ── MonoBehaviour {len(mbs)}개 (타입정보 읽음 {ok}, 못 읽음 {blind}) ──")
    if blind and not ok:
        add("      → 타입 정보를 하나도 못 읽었습니다. IL2CPP 이거나 "
            "UnityPy 가 이 버전을 아직 못 다루는 경우입니다.")
    best.sort(reverse=True)
    for n, name, jp in best[:6]:
        add(f"      · {name}  — 일본어 문자열 {n}개")
        for s in jp[:SAMPLES]:
            add(f"          {_clean(s, 120)}")
    if not best and ok:
        add("      · 일본어가 든 MonoBehaviour 가 없습니다.")

    if blind_classes:
        add(f"      ── 못 읽은 {blind}개는 어떤 클래스인가 ──")
        for cls, n in blind_classes.most_common(12):
            add(f"         · {cls}  ×{n}")
        add("      ── 왜 못 읽었나 ──")
        for why, n in blind_reasons.most_common(5):
            add(f"         · {why}  ×{n}")
        _raw_scan_report(mbs, add, blind_ids)

    # 어떤 스크립트가 많이 쓰였는지 — 대사를 든 클래스를 찾는 실마리입니다
    for o in mbs[:1200]:
        all_classes[script_class(o)] += 1
    if all_classes:
        add("      ── 많이 쓰인 클래스 ──")
        for cls, n in all_classes.most_common(12):
            add(f"         · {cls}  ×{n}")


# ---------------------------------------------------------------- 코드 안의 문자열

# 유니티·마이크로소프트가 넣어준 라이브러리. 게임 대사가 있을 리 없습니다.
STOCK_DLL = re.compile(
    r"^(System|Microsoft|Mono|Unity|netstandard|mscorlib|Newtonsoft|"
    r"Cinemachine|TextMeshPro|DOTween|Sirenix|Spine|Rewired|I18N|"
    r"Accessibility|WindowsBase|nunit|JetBrains)", re.I)

_KANA = re.compile(r"[぀-ゟ゠-ヿ]")
_JP_RUN = re.compile(r"[぀-ヿ一-鿿ａ-ｚＡ-Ｚ０-９！-｠　、-〟ー・]{3,}"
                     r"[ -~぀-ヿ一-鿿ａ-ｚＡ-Ｚ０-９！-｠　、-〟ー・]*")


def _looks_japanese(s: str) -> bool:
    """진짜 일본어인가, 아니면 ASCII 를 UTF-16 으로 잘못 읽은 쓰레기인가.

    ``桔獩瀠潲牧浡`` 같은 건 "This program"(ASCII) 을 2바이트씩 묶어 읽은
    결과입니다. 한자만 잔뜩이고 **가나가 하나도 없습니다.** 진짜 일본어
    대사에는 가나가 반드시 섞입니다. 그걸로 가릅니다.
    """
    kana = len(_KANA.findall(s))
    if kana == 0:
        return False
    return kana / max(1, len(s)) >= 0.15


def _dll_strings(raw: bytes) -> list[str]:
    """.NET 어셈블리에 박힌 문자열은 UTF-16 입니다. 일본어가 든 것만 꺼냅니다."""
    # 2바이트 경계에 맞춰 읽어야 합니다. 어긋나면 ASCII 가 한자로 보입니다.
    try:
        text = raw[: len(raw) // 2 * 2].decode("utf-16-le", errors="ignore")
    except Exception:                                   # noqa: BLE001
        return []
    seen, out = set(), []
    for m in _JP_RUN.finditer(text):
        s = m.group(0).strip()
        if len(s) < 3 or s in seen or not _looks_japanese(s):
            continue
        seen.add(s)
        out.append(s)
        if len(out) > 4000:
            break
    return out


def dll_report(root: Path) -> list[str]:
    """게임 코드 안에 대사가 박혀 있는지."""
    lines: list[str] = []
    add = lines.append
    data = next((p for p in sorted(Path(root).glob("*_Data")) if p.is_dir()), None)
    managed = (data or Path(root)) / "Managed"
    if not managed.is_dir():
        return lines

    dlls = [p for p in sorted(managed.glob("*.dll"))
            if not STOCK_DLL.match(p.stem)]
    add("=" * 70)
    add(f"■ 게임 코드 안 (Managed/, 게임 자체 dll {len(dlls)}개)")
    if not dlls:
        add("   게임이 직접 만든 dll 이 없습니다.")
        return lines

    hits = []
    for p in dlls:
        try:
            found = _dll_strings(p.read_bytes())
        except OSError:
            continue
        if found:
            hits.append((len(found), p.name, found))
    hits.sort(reverse=True)

    if not hits:
        add("   일본어 문자열이 없습니다. 대사는 코드 밖에 있습니다.")
        return lines

    for n, name, found in hits[:5]:
        add(f"   · {name} — 일본어 문자열 {n}개")
        for s in found[:10]:
            add(f"       {_clean(s, 100)}")
    return lines


def _blob_report(objs, add) -> None:
    """큰 바이트 덩어리를 든 오브젝트 — 압축·암호화된 시나리오일 수 있습니다."""
    big: list[tuple[int, str, str]] = []
    for o in objs:
        if o.type.name != "MonoBehaviour":
            continue
        try:
            tree = o.read_typetree()
        except Exception:                               # noqa: BLE001
            continue
        if not isinstance(tree, dict):
            continue
        for key, val in tree.items():
            if isinstance(val, (bytes, bytearray)) and len(val) > 2048:
                big.append((len(val), str(tree.get("m_Name") or "?"), key))
            elif (isinstance(val, list) and len(val) > 2048
                  and val and isinstance(val[0], int)):
                big.append((len(val), str(tree.get("m_Name") or "?"), key))
    if not big:
        return
    big.sort(reverse=True)
    add(f"   ── 큰 바이트 덩어리 {len(big)}개 (압축·암호화된 대사일 수 있음) ──")
    for size, name, key in big[:6]:
        add(f"      · {name}.{key}  {size:,}바이트")


# ------------------------------------------------ 타입 정보 없이 문자열 꺼내기
# 실제 번역에 쓰는 것과 **같은 코드**를 씁니다. 진단에서 보인 문자열이
# 번역 목록에도 그대로 올라와야 하니까요.
raw_strings = unityraw.scan
_plausible = unityraw.plausible


def _raw_scan_report(objs, add, blind_ids: set) -> None:
    """타입 정보를 못 읽은 오브젝트를 **바이트 단위로** 훑어봅니다."""
    if not blind_ids:
        return
    found: list[tuple[int, str, list[str]]] = []
    for o in objs:
        if o.path_id not in blind_ids:
            continue
        try:
            data = o.get_raw_data()
        except Exception:                               # noqa: BLE001
            continue
        strings = [s for _off, s in raw_strings(data)]
        jp = [s for s in strings if _CJK.search(s)]
        if jp:
            found.append((len(jp), str(o.path_id), jp))

    add("      ── 못 읽은 것들을 바이트 단위로 훑어본 결과 ──")
    if not found:
        add("         일본어가 없습니다. 대사는 여기가 아닙니다.")
        return
    found.sort(reverse=True)
    total = sum(n for n, _i, _jp in found)
    add(f"         일본어 문자열 {total}개를 찾았습니다! (오브젝트 {len(found)}개)")
    for n, pid, jp in found[:5]:
        add(f"         · path_id {pid} — {n}개")
        for s in jp[:8]:
            add(f"             {_clean(s, 100)}")


# ---------------------------------------------------------------- 폰트

# 한글이 □□□ 로 나오는 이유는 번역이 안 들어가서가 아닙니다. 글자는 이미
# 들어갔고, **폰트에 그 글자 모양이 없어서** 네모로 그려집니다.
# 그러니 폰트가 어떤 글자를 가지고 있는지부터 봐야 합니다.
HANGUL_RANGES = [(0xAC00, 0xD7A3), (0x1100, 0x11FF), (0x3130, 0x318F)]
KANA_RANGE = (0x3040, 0x30FF)


def _covers(codes: set, lo: int, hi: int) -> int:
    return sum(1 for c in codes if lo <= c <= hi)


def _unicodes(node, out: set, depth: int = 0) -> None:
    """문자표에서 유니코드 번호만 그러모읍니다."""
    if depth > 8 or len(out) > 200000:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("m_Unicode", "unicode") and isinstance(v, int):
                out.add(v)
            else:
                _unicodes(v, out, depth + 1)
    elif isinstance(node, (list, tuple)):
        for v in node:
            _unicodes(v, out, depth + 1)


ATLAS_MODE = {0: "Static(고정)", 1: "Dynamic(실행 중 생성)", 2: "DynamicOS(시스템 폰트)"}


def font_report(objs, add) -> None:
    fonts = [o for o in objs if o.type.name == "Font"]
    tmps = []
    for o in objs:
        if o.type.name != "MonoBehaviour":
            continue
        cls = script_class(o)
        if "FontAsset" in cls or "TMP_Font" in cls:
            tmps.append((cls, o))
    if not fonts and not tmps:
        return

    add(f"   ── 폰트 {len(fonts) + len(tmps)}개 ──")

    for o in fonts:
        try:
            d = o.read()
            name = getattr(d, "m_Name", "?")
        except Exception:                                # noqa: BLE001
            name = "?"
        add(f"      · [유니티 기본] {name}")

    for cls, o in tmps:
        try:
            tree = o.read_typetree()
        except Exception as exc:                         # noqa: BLE001
            add(f"      · [TMP] 읽지 못함: {_clean(str(exc), 60)}")
            continue
        name = tree.get("m_Name") or "?"
        mode = tree.get("m_AtlasPopulationMode")
        face = tree.get("m_FaceInfo") or {}
        family = face.get("m_FamilyName") or ""
        w = tree.get("m_AtlasWidth") or 0
        h = tree.get("m_AtlasHeight") or 0
        codes: set = set()
        _unicodes(tree.get("m_CharacterTable") or tree.get("m_glyphInfoList"), codes)

        ko = sum(_covers(codes, lo, hi) for lo, hi in HANGUL_RANGES)
        jp = _covers(codes, *KANA_RANGE)
        add(f"      · [TMP] {name}  ({family})")
        add(f"          글자 {len(codes)}자 · 한글 {ko}자 · 가나 {jp}자")
        add(f"          아틀라스 {w}x{h} · 방식 "
            f"{ATLAS_MODE.get(mode, mode)}")
        fb = tree.get("m_FallbackFontAssetTable") or []
        add(f"          대체 폰트 {len(fb)}개")
        if ko == 0:
            add("          → 한글이 없습니다. 이 폰트로는 □□□ 로 나옵니다.")


# ---------------------------------------------------------------- Ren'Py

def _renpy_report(info, add) -> None:
    """Ren'Py 게임 속을 들여다봅니다.

    유니티와 달리 여기서 궁금한 것은 셋뿐입니다 — 원문이 어디에 들어 있는지,
    번역 파일이 제대로 놓였는지, 그리고 글꼴에 한글이 있는지.
    """
    import json

    from . import fontlist
    from .engines import renpy

    game = info.rp_game
    if game is None:
        add("game 폴더를 찾지 못했습니다.")
        return
    add(f"game 폴더: {game}")
    add("")

    # ── 원문이 어디에 있나
    kinds = Counter()
    per_file: list[tuple[str, str, int]] = []
    for label, kind, data in renpy._sources(game):
        if kind == "rpy":
            texts = [t for _f, t in renpy.rpy_texts(
                data.decode("utf-8", errors="replace")) if renpy._worth(t)]
        else:
            texts = [t for t in renpy.rpyc_texts(data) if renpy._worth(t)]
        kinds[kind] += 1
        per_file.append((label, kind, len(texts)))

    total = sum(n for _l, _k, n in per_file)
    add(f"■ 원문 — 파일 {len(per_file)}개에서 문장 {total}개")
    add(f"   .rpy(소스) {kinds['rpy']}개 · .rpyc(컴파일본) {kinds['rpyc']}개 · "
        f".rpa(묶음) {len(list(game.rglob('*.rpa')))}개")
    for label, kind, n in sorted(per_file, key=lambda x: -x[2])[:15]:
        add(f"   · {label}  [{kind}]  {n}개")
    if len(per_file) > 15:
        add(f"   … 외 {len(per_file) - 15}개")
    if total == 0:
        add("   ! 한 문장도 못 찾았습니다. .rpa 묶음이 다른 형식이거나 "
            "대사가 스크립트 밖에 있을 수 있습니다.")
    add("")

    # ── 번역이 들어가 있나
    add("■ 번역 파일")
    pack = game / renpy.PACK_JSON
    hook = game / renpy.HOOK_RPY
    if pack.is_file():
        try:
            mapping = json.loads(pack.read_text("utf-8"))
            add(f"   ✓ {renpy.PACK_JSON}  — 문장 {len(mapping)}개")
            for k, v in list(mapping.items())[:3]:
                add(f"      {_clean(k, 30)} → {_clean(v, 30)}")
        except Exception as exc:                        # noqa: BLE001
            add(f"   ! {renpy.PACK_JSON} 을 읽지 못했습니다: {exc}")
    else:
        add(f"   ✕ {renpy.PACK_JSON} 없음 — 아직 번역을 넣지 않았습니다")
    add(f"   {'✓' if hook.is_file() else '✕'} {renpy.HOOK_RPY}")
    for stale in renpy.stale_compiled(game):
        add(f"   ! {stale.name} 이 남아 있습니다 — 옛 번역이 되살아날 수 있습니다")
    add("")

    # ── 글꼴
    add("■ 글꼴")
    files = renpy.font_files(game)
    if not files:
        add("   게임이 들고 온 글꼴 파일은 없습니다 "
            "— Ren'Py 기본 글꼴(DejaVuSans)을 씁니다. 한글이 없습니다.")
    for p in files[:20]:
        try:
            got = fontlist.read_font(p)
        except Exception:                               # noqa: BLE001
            got = None
        mark = "한글 있음" if (got and got.hangul) else "한글 없음"
        name = got.name if got else p.stem
        add(f"   · {p.relative_to(game)}  ({name}) — {mark}")
    if len(files) > 20:
        add(f"   … 외 {len(files) - 20}개")

    names = renpy.font_names(game)
    add(f"   스크립트가 부르는 글꼴 이름 {len(names)}개: "
        + ", ".join(names[:8]) + (" …" if len(names) > 8 else ""))

    ours = [p for p in game.glob(renpy.FONT_BASE + ".*")
            if p.suffix.lower() in renpy.FONT_SUFFIXES]
    if ours:
        add(f"   ✓ 한글 글꼴이 들어가 있습니다: {ours[0].name}")
        add(f"   {'✓' if (game / renpy.FONT_RPY).is_file() else '✕'} "
            f"{renpy.FONT_RPY}")
    else:
        add("   ✕ 아직 한글 글꼴을 넣지 않았습니다 "
            "— 글자가 □□□ 로 나오면 '한글 폰트 넣기' 를 누르세요.")
    add("")
    add("=" * 70)
