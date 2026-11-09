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

"""RPG Maker MV / MZ.

화면에 나오는 것은 전부 ``data/`` 아래 평문 JSON 이라 바이너리를 풀 일이 없습니다.
문제는 반대입니다 — 파일이 구조로 가득해서, 문자열을 통째로 쓸어담으면 스위치 이름·
파일 이름·편집자 메모가 수천 개 딸려옵니다. 그래서 화이트리스트로 갑니다.

순진한 덤프가 못 하는 것 하나: RPG Maker 는 메시지창 한 줄을 ``code: 401`` 명령
하나로 저장합니다. 그대로 번역하면 일본어 줄바꿈 위치에서 잘린 조각을 번역하게
됩니다. 연속된 401 은 한 문단으로 합쳐서 보여주고, 되돌릴 때 **같은 개수의 명령에**
다시 나눕니다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .. import containers, heuristics, jsonnest
from ..model import Location
from ..treepath import format_path

# --- 이벤트 명령 코드 -------------------------------------------------------
TEXT_LINE = 401        # 문장 표시, 한 줄
SCROLL_LINE = 405      # 스크롤 문장, 한 줄
CHOICES = 102          # 선택지 -> parameters[0] 이 문자열 배열
CHANGE_NAME = 320      # -> parameters[1]
CHANGE_NICKNAME = 324  # -> parameters[1]
CHANGE_PROFILE = 325   # -> parameters[1]
PLUGIN_CMD_MV = 356    # MV: parameters[0] 에 명령 한 줄
PLUGIN_CMD_MZ = 357    # MZ: parameters[3] 이 인자 객체 {"text": "...", ...}

MERGEABLE = {TEXT_LINE, SCROLL_LINE}
PARAM1_CODES = {CHANGE_NAME, CHANGE_NICKNAME, CHANGE_PROFILE}

# --- 데이터베이스 필드 ------------------------------------------------------
DB_FIELDS = {
    "Actors.json": ["name", "nickname", "profile"],
    "Classes.json": ["name"],
    "Skills.json": ["name", "description", "message1", "message2"],
    "Items.json": ["name", "description"],
    "Weapons.json": ["name", "description"],
    "Armors.json": ["name", "description"],
    "Enemies.json": ["name"],
    "States.json": ["name", "message1", "message2", "message3", "message4"],
}

SYSTEM_SCALARS = ["gameTitle", "currencyUnit"]
SYSTEM_LISTS = ["armorTypes", "elements", "equipTypes", "skillTypes", "weaponTypes"]
SYSTEM_TERMS = ["basic", "commands", "params"]

PLUGIN_SKIP_KEYS = {
    "name", "status", "description", "filename", "file", "se", "bgm", "bgs",
    "image", "img", "picture", "font", "fontface", "switch", "variable",
    "keycode", "key", "code", "script", "eval", "formula", "class", "symbol",
    "model", "modelname", "motion", "anim", "animation", "sprite", "pose",
    "expression", "emotion", "character", "charactername", "target", "type",
    "mode", "tag", "state", "layer", "effect", "preset",
}
PLUGIN_ARG_SKIP = PLUGIN_SKIP_KEYS | {
    "switchid", "variableid", "id", "index", "x", "y", "width", "height",
    "wait", "duration", "me", "faceindex", "color", "colorindex", "align",
    "speed", "volume", "pitch", "pan", "actorid", "eventid", "mapid",
    "commonevent",
}

# ==========================================================================
# 플러그인 파라미터: 화이트리스트
# ==========================================================================
# 플러그인 파라미터 키는 플러그인 수만큼 있습니다. "이건 이름이니 빼자" 를
# 하나씩 쫓아다니는 방식으로는 계속 새는 게 증명됐습니다 (모델·애니메이션·
# 타이틀 메뉴 항목이 차례로 게임을 죽였습니다).
#
# 그래서 뒤집었습니다. **화면에 나오는 문구라고 확신할 수 있는 키만** 번역하고
# 나머지는 전부 '이름' 으로 봅니다. 못 잡은 문구는 일본어로 남을 뿐이지만,
# 잘못 번역한 이름은 게임을 죽입니다. 어느 쪽으로 틀릴지는 분명합니다.
DISPLAY_MARKERS = (
    "text", "message", "msg", "desc", "label", "caption", "title", "help",
    "prompt", "hint", "tip", "word", "line", "dialog", "comment", "notice",
    "confirm", "warning", "info", "content", "body", "summary", "subtitle",
    "テキスト", "メッセージ", "説明",
)


def key_of(ptr: str) -> str:
    """경로에서 의미 있는 마지막 키 이름. ``models![0]`` 의 키는 ``models``."""
    parts = [p for p in re.split(r"[.\[\]!]+", ptr) if p and not p.isdigit()]
    return parts[-1].lower() if parts else ""


def key_matches(ptr: str, table: set) -> bool:
    """키 이름이 표에 있는가. 복수형도 같은 것으로 봅니다(models → model)."""
    k = key_of(ptr)
    if k in table:
        return True
    return len(k) > 3 and k.endswith("s") and k[:-1] in table


def is_display_key(ptr: str) -> bool:
    """이 키의 값이 '화면에 나오는 문구' 라고 볼 수 있는가."""
    if key_matches(ptr, PLUGIN_SKIP_KEYS) or key_matches(ptr, PLUGIN_ARG_SKIP):
        return False
    k = key_of(ptr)
    return any(marker in k for marker in DISPLAY_MARKERS)


# --------------------------------------------------------------------------
def is_rpgmaker(root: Path) -> Path | None:
    """``data/`` 와 ``js/`` 를 담고 있는 폴더, 없으면 None."""
    for base in (root, root / "www"):
        if (base / "data" / "System.json").is_file() and (base / "js").is_dir():
            return base
    return None


def variant(base: Path) -> str:
    js = base / "js"
    if (js / "rmmz_core.js").exists():
        return "MZ"
    if (js / "rpg_core.js").exists():
        return "MV"
    return "MV/MZ"


def plugins_file(base: Path) -> Path | None:
    p = base / "js" / "plugins.js"
    return p if p.is_file() else None


def plugin_scripts(base: Path) -> list[Path]:
    d = base / "js" / "plugins"
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.js") if p.stat().st_size < 4 * 1024 * 1024)


def data_files(base: Path) -> list[Path]:
    d = base / "data"
    return sorted(p for p in d.glob("*.json") if p.name != "MapInfos.json")


# RPG Maker 가 만들어 주는 파일 이름들. 이 구조는 우리가 정확히 압니다.
_STANDARD = {
    "System.json", "MapInfos.json", "Animations.json", "Tilesets.json",
    "Troops.json", "CommonEvents.json", "Actors.json", "Classes.json",
    "Skills.json", "Items.json", "Weapons.json", "Armors.json",
    "Enemies.json", "States.json",
}
_MAP_FILE = re.compile(r"^Map\d+\.json$", re.I)
_CUSTOM_SUFFIX = {".json", ".csv", ".tsv", ".txt"}


def custom_data_files(base: Path) -> list[Path]:
    """``data/`` 안에 있지만 **RPG Maker 것이 아닌** 파일들.

    플러그인을 쓰는 게임은 대사를 자기가 만든 파일에 담습니다. 실제로 본
    예가 이렇습니다:

        data/ScenarioText.csv       220KB   대사 1454줄
        data/MiniScenarioText.csv   189KB   대사 1181줄
        data/TraceConversations.json

    이런 파일은 우리가 구조를 모릅니다. 그래도 JSON·CSV 라면 **어느 칸에
    글자가 들어 있는지**는 알 수 있으므로, 일반 파일과 똑같이 다룹니다.
    이걸 안 하면 메뉴·아이템만 번역되고 **줄거리는 통째로 원문**으로 남는데,
    화면에는 '1215개 적용' 이라고 찍혀서 무엇이 빠졌는지 알 수가 없습니다.
    """
    d = base / "data"
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.iterdir()):
        if not p.is_file():
            continue
        if p.suffix.lower() not in _CUSTOM_SUFFIX and not _extensionless_text(p):
            continue
        if p.name in _STANDARD or _MAP_FILE.match(p.name):
            continue
        out.append(p)
    return out


def _extensionless_text(p: Path) -> bool:
    """확장자 없는 글자 파일인가. 배포판에서 ``cardDb.csv`` 의 확장자를 뗀 게임이 있습니다
    (플러그인이 ``data/cardDb`` 로 읽습니다). 이진 파일은 걸러냅니다."""
    if p.suffix:
        return False
    try:
        if p.stat().st_size > 32 * 1024 * 1024:
            return False
        head = p.read_bytes()[:4096]
        import codecs
        codecs.getincrementaldecoder("utf-8")().decode(head, final=False)
    except (OSError, UnicodeDecodeError):
        return False
    return b"\x00" not in head


# ==========================================================================
# 참조 이름 색인
# ==========================================================================
# 게임 안에서 "이름으로 찾는" 것들이 있습니다. 애니메이션, 타일셋, 이벤트,
# 플러그인이 등록한 모델·모션. 화면에 안 나오지만 파일에는 평범한 일본어로
# 적혀 있어서 모양만으로는 대사와 구별되지 않습니다:
#
#     Animation not found: 웨이브        ← ウェーブ 를 번역해버린 결과
#
# 그래서 모양이 아니라 **쓰임새** 로 가립니다.
NAME_ONLY_FILES = {
    "Animations.json", "Tilesets.json", "Troops.json",
    "CommonEvents.json", "MapInfos.json",
}


def _all_commands(node):
    """문서 어디에 있든 이벤트 명령을 전부 훑습니다."""
    if isinstance(node, dict):
        if "code" in node and "parameters" in node:
            yield node
        for v in node.values():
            yield from _all_commands(v)
    elif isinstance(node, list):
        for v in node:
            yield from _all_commands(v)


# 스크립트 안에 따옴표로 적힌 문자열은 **화면 문구가 아니라 값** 입니다.
#
#     $gameVariables.setValue(255, '達成')
#     if ($gameVariables.value(255) === '達成') { ... }
#
# 이런 게임은 진행 상태를 일본어 낱말로 저장하고 그걸 비교합니다. 저 낱말을
# 번역해 넣으면 **게임이 죽지는 않는데 이벤트가 안 넘어갑니다.** 세이브에 든
# 옛 값과 비교가 안 맞기 때문입니다. 죽지 않아서 원인을 찾기가 더 어렵습니다.
SCRIPT_CODES = {355, 655}          # 스크립트
COND_BRANCH = 111                  # 조건 분기 (0번 인자가 12면 스크립트)
CONTROL_VAR = 122                  # 변수 조작 (3번 인자가 4면 스크립트)

_LITERAL = re.compile(r"""(['"`])((?:\\.|(?!\1).)*)\1""")


def script_texts(cmd: dict) -> list[str]:
    """이벤트 명령 하나에서 '스크립트로 취급되는' 문자열들."""
    code = cmd.get("code")
    params = cmd.get("parameters") or []
    out = []
    if code in SCRIPT_CODES and params and isinstance(params[0], str):
        out.append(params[0])
    elif code == COND_BRANCH and len(params) > 1 and params[0] == 12:
        if isinstance(params[1], str):
            out.append(params[1])
    elif code == CONTROL_VAR and len(params) > 4 and params[3] == 4:
        if isinstance(params[4], str):
            out.append(params[4])
    return out


def script_literals(text: str) -> set[str]:
    """스크립트 한 토막에서 따옴표 안의 문자열을 꺼냅니다."""
    from .. import jslex

    out = set()
    for lit in jslex.literals(text or ""):
        val = lit.text
        if val.strip():
            out.add(val)
    return out


def _add_str(names: set[str], v) -> None:
    if isinstance(v, str) and v.strip():
        names.add(v)


def collect_identifiers_certain(base: Path) -> set[str]:
    """**확실한** 참조용 이름만 모읍니다 — Animations.json 등의 이름, 스위치·변수,
    이벤트 이름, 스크립트가 비교하는 값. 화면에 안 나오는 게 분명하므로 언제나
    제외해도 안전합니다. 끄는 설정이 없습니다.
    """
    names: set[str] = set()
    for path in (base / "data").glob("*.json"):
        try:
            doc = json.loads(path.read_text("utf-8"))
        except Exception:
            continue
        if path.name in NAME_ONLY_FILES and isinstance(doc, list):
            for row in doc:
                if isinstance(row, dict):
                    _add_str(names, row.get("name"))
        if path.name == "System.json" and isinstance(doc, dict):
            for key in ("switches", "variables"):
                for v in doc.get(key) or []:
                    _add_str(names, v)
        if path.name.startswith("Map") and isinstance(doc, dict):
            for ev in doc.get("events") or []:
                if isinstance(ev, dict):
                    _add_str(names, ev.get("name"))

        # 스크립트·조건분기 안의 따옴표 문자열 = 게임이 비교하는 값
        for cmd in _all_commands(doc):
            for chunk in script_texts(cmd):
                for lit in script_literals(chunk):
                    _add_str(names, lit)
    return names


def collect_identifiers_guess(base: Path, *, include_plugin_values: bool = True,
                              include_js_literals: bool = True) -> set[str]:
    """**추정** 참조용 이름 — 플러그인 설정·인자 중 화면 문구로 볼 수 없는 값과,
    플러그인 소스에 박힌 일본어 리터럴. '플러그인 설정 전부' 를 켠 사용자는
    이쪽을 빼고 볼 수 있습니다.

    이 값들은 **모양이 같을 뿐** 다른 곳(System.json 항목·데이터베이스 필드)의
    진짜 화면 문구와 같은 낱말을 쓸 수 있습니다 — 실제로 메뉴 플러그인이
    "ステータス" 를 내부 키 값으로 쓰면서 System.json 의 메뉴 명령
    "ステータス" 와 글자가 겹친 사례가 있었습니다. 그래서 호출하는 쪽이
    :func:`known_display_texts` 로 확실한 문구를 먼저 빼고 써야 합니다.
    """
    from .. import jslex

    names: set[str] = set()

    # 플러그인 **소스** 안에 박힌 일본어 문자열도 게임이 비교하는 값입니다.
    #
    #     if (this._lastChoice === 'はい') { ... }
    #     const MODE = { '日中': 0, '夕方': 1 };
    #
    # 이런 낱말을 data 쪽에서 번역해버리면 짝이 안 맞아 그 부분만 조용히
    # 멈춥니다. 죽지 않아서 가장 찾기 어려운 고장입니다.
    # ('플러그인 js 파일까지' 를 켠 사용자는 이걸 직접 번역하겠다는 뜻이므로
    #  그 경우엔 막지 않습니다.)
    # 비교·열쇠로 쓰는 낱말은(``js_literal_is_key``) 옵트인 여부와 상관없이
    # 언제나 보호합니다 — RJ01670083 에서 "共通" 이 카드 조회 열쇠였는데
    # 번역되어 조회가 깨진 사례가 있었습니다. 대사처럼 보이는 긴 문장만
    # ``include_js_literals=False`` (= '플러그인 js 파일까지' 를 켠 사용자)
    # 일 때 여기서 빼서, 그쪽은 화면 문구로 따로 뽑혀 번역될 수 있게 둡니다.
    for js in sorted((base / "js" / "plugins").glob("*.js")):
        try:
            body = js.read_text("utf-8", errors="replace")
        except OSError:
            continue
        for lit in jslex.literals(body):
            if lit.subst:
                continue        # `${...}` 안은 코드입니다
            val = lit.text
            if not _CJK.search(val):    # 일본어가 든 것만. 영어는 대사와 안 겹칩니다
                continue
            if include_js_literals or js_literal_is_key(body, lit):
                _add_str(names, val)

    if not include_plugin_values:
        return names

    plug = plugins_file(base)
    if plug is not None:
        try:
            for ptr, val in containers.explode(
                    plug.read_text("utf-8", errors="replace"), "rmplugins", plug.name):
                # 화면 문구로 확신할 수 없는 플러그인 값은 전부 '이름' 취급.
                if not is_display_key(ptr):
                    _add_str(names, val)
        except Exception:
            pass

    for path in (base / "data").glob("*.json"):
        try:
            doc = json.loads(path.read_text("utf-8"))
        except Exception:
            continue
        for cmd in _all_commands(doc):
            if cmd.get("code") == PLUGIN_CMD_MZ:
                params = cmd.get("parameters") or []
                if len(params) > 3 and isinstance(params[3], dict):
                    for sub, val in jsonnest.walk(params[3]):
                        if sub and not is_display_key(sub):
                            _add_str(names, val)
    return names


def known_display_texts(base: Path) -> set[str]:
    """System.json 항목·데이터베이스 필드처럼 **틀림없이 화면에 나오는** 문구.

    :func:`collect_identifiers_guess` 가 내놓은 값이 이 집합과 겹치면, 추정이
    아니라 확실한 문구 쪽을 믿습니다 — 같은 낱말을 플러그인이 다른 용도로도
    쓴다고 해서 System.json 의 메뉴 명령까지 통째로 사라지면 안 됩니다.
    """
    texts: set[str] = set()
    for _t, _l in _system(_safe_json(base / "data" / "System.json"), ""):
        texts.add(_t)
    for name, fields in DB_FIELDS.items():
        for _t, _l in _database(_safe_json(base / "data" / name), "", fields, True):
            texts.add(_t)
    return texts


def _safe_json(path: Path):
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return {} if path.name == "System.json" else []


def collect_identifiers(base: Path, *, include_plugin_values: bool = True,
                        include_js_literals: bool = True) -> set[str]:
    """번역하면 안 되는 '참조용 이름' 을 모읍니다.

    두 종류가 섞여 있습니다.
      · **확실한 것** — Animations.json 등의 이름, 스위치·변수, 이벤트 이름.
        화면에 안 나오는 게 분명하므로 언제나 제외합니다.
      · **추정** — 플러그인 설정·인자 중 화면 문구로 볼 수 없는 값.
        '플러그인 설정 전부' 를 켠 사용자는 이쪽을 빼고 볼 수 있습니다.

    (하위 호환용 — 새 코드는 :func:`collect_identifiers_certain` /
    :func:`collect_identifiers_guess` / :func:`known_display_texts` 를 따로
    쓰세요. ``extract.py`` 는 그렇게 해서 추정 식별자가 확실한 화면 문구를
    가리지 않게 합니다.)
    """
    return (collect_identifiers_certain(base)
            | collect_identifiers_guess(base, include_plugin_values=include_plugin_values,
                                        include_js_literals=include_js_literals))


# ==========================================================================
# 추출
# ==========================================================================
def extract_plugins(base: Path, root: Path, threshold: float,
                    all_params: bool = False) -> list:
    """js/plugins.js 의 플러그인 파라미터 중 화면 문구."""
    path = plugins_file(base)
    if path is None:
        return []
    text = path.read_text("utf-8", errors="replace")
    rel = str(path.relative_to(root)).replace("\\", "/")
    out = []
    try:
        cells = list(containers.explode(text, "rmplugins", path.name))
    except Exception:
        return []
    for ptr, cell in cells:
        if not ptr.startswith("[") or ".parameters" not in ptr:
            continue
        if not (is_display_key(ptr) or all_params):
            continue
        if heuristics.score(cell) < threshold:
            continue
        out.append((cell, Location(file=rel, kind="loose", fmt="rmplugins", ptr=ptr,
                                   asset=_plugin_name(text, ptr) or "플러그인")))
    return out


def _plugin_name(text: str, ptr: str) -> str:
    try:
        doc = containers._plugins_doc(text)
        return str(doc[int(ptr[1:ptr.index("]")])].get("name") or "")
    except Exception:
        return ""


# 널리 쓰이는 **편집·변환 도구** 플러그인. 안의 일본어는 화면 문구가 아니라 명령어·항목 이름표라
# (``直接指定`` · ``オフセットX``) 번역하면 그 도구가 못 알아봅니다. 사례: RJ01670083.
_JS_TOOL_PLUGINS = re.compile(r"^(?:Text2Frame|Keke_)", re.I)

_JS_STR = re.compile(r"(['\"`])((?:\\.|(?!\1)[^\\\n])*)\1")
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿]")


_SENTENCE_MARK = re.compile(r"[。！？…、]|\\[A-Za-z]+\d*\[|%\d|\\n")


def js_literal_is_key(src: str, lit) -> bool:
    """이 JS 글자가 **화면 문구가 아니라 코드가 쓰는 이름표**로 보이는가.

    번역하면 게임이 못 찾습니다. 실제 사례(RJ01670083):

        parameters['スキップキー']                      → 플러그인 파라미터 열쇠
        heroine === "共通"                              → 카드 표의 값과 비교
        ["共通", "セレナ", "ノーラ", "リリシア"]        → 값 목록

    ① 비교·case·색인 접근에 쓰인 것, ② 짧은 글자만 든 배열의 원소, ③ 문장 부호·제어문자 없는
    8자 이하 낱말은 뺍니다. 색 코드가 든 긴 카드 설명 같은 문장은 그대로 가져옵니다.
    """
    before = src[max(0, lit.start - 30):lit.start].rstrip()
    after = src[lit.end:lit.end + 30].lstrip()
    if re.search(r"(?:===|!==|==|!=|\bcase)$", before) or re.match(r"(?:===|!==|==|!=)", after):
        return True
    if before.endswith("[") and after.startswith("]"):
        return True                                   # obj['열쇠']
    val = lit.text
    short = len(val) <= 12 and not _SENTENCE_MARK.search(val)
    if short and before[-1:] in ("[", ",") and after[:1] in (",", "]"):
        return True                                   # 낱말 목록의 원소
    return len(val) <= 8 and not _SENTENCE_MARK.search(val)


def extract_plugin_scripts(base: Path, root: Path) -> list:
    """플러그인 js 파일 안에 하드코딩된 일본어 문자열.

    JS 소스를 건드리는 일이라 기본으로는 끄고, CJK 가 든 리터럴만 가져옵니다.
    """
    from .. import jslex

    out = []
    for path in plugin_scripts(base):
        if _JS_TOOL_PLUGINS.match(path.name):
            continue
        try:
            text = path.read_text("utf-8", errors="replace")
        except Exception:
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        seen: set[str] = set()
        for lit in jslex.literals(text):
            if lit.subst:
                continue        # `${...}` 안은 코드입니다
            val = lit.text
            if not _CJK.search(val) or len(val) > 400:
                continue
            if "/" in val and "." in val.rsplit("/", 1)[-1]:
                continue        # 경로처럼 생긴 건 제외
            if js_literal_is_key(text, lit):
                continue        # 코드가 값으로 비교하거나 열쇠로 쓰는 글자
            if val in seen:
                continue
            seen.add(val)
            out.append((val, Location(file=rel, kind="jsliteral", fmt="jsliteral",
                                      ptr=val, asset=path.name)))
    return out


def count_js_dialogue(base: Path, *, min_len: int = 8) -> int:
    """플러그인 js 안에 **대사로 보이는** 문장이 몇 개나 있는가.

    가져오지는 않고 세기만 합니다. '플러그인 js 파일까지' 를 안 켠 사용자에게
    "여기 대사가 있다" 고 알려 주기 위한 것입니다. 짧은 낱말(등장인물 이름,
    방향 표시 같은 것)은 빼고 문장만 셉니다 — 겁주지 않으려고요.
    """
    from .. import jslex

    seen: set[str] = set()
    for path in plugin_scripts(base):
        if _JS_TOOL_PLUGINS.match(path.name):
            continue
        try:
            text = path.read_text("utf-8", errors="replace")
        except OSError:
            continue
        for lit in jslex.literals(text):
            if lit.subst:
                continue        # `${...}` 가 든 것은 코드입니다
            val = lit.text
            if len(val) < min_len or len(val) > 400:
                continue
            if not _CJK.search(val):
                continue
            if "/" in val and "." in val.rsplit("/", 1)[-1]:
                continue
            seen.add(val)
    return len(seen)


def extract_entries(base: Path, root: Path, *, include_notes: bool = False,
                    threshold: float = 0.45) -> list:
    out: list = []
    for path in data_files(base):
        rel = str(path.relative_to(root)).replace("\\", "/")
        try:
            doc = json.loads(path.read_text("utf-8"))
        except Exception:
            continue
        name = path.name
        if name == "System.json":
            out += _system(doc, rel)
        elif name in DB_FIELDS:
            out += _database(doc, rel, DB_FIELDS[name], include_notes)
        out += _event_lists(doc, rel, threshold=threshold)
    return out


def _loc(rel: str, ptr: str, asset: str = "", span: list[str] | None = None) -> Location:
    return Location(file=rel, kind="loose", fmt="json", ptr=ptr, asset=asset, span=span)


def _system(doc: dict, rel: str) -> list:
    out = []
    for key in SYSTEM_SCALARS:
        v = doc.get(key)
        if isinstance(v, str) and v.strip():
            out.append((v, _loc(rel, key, "시스템")))
    for key in SYSTEM_LISTS:
        for i, v in enumerate(doc.get(key) or []):
            if isinstance(v, str) and v.strip():
                out.append((v, _loc(rel, f"{key}[{i}]", "시스템")))
    terms = doc.get("terms") or {}
    for key in SYSTEM_TERMS:
        for i, v in enumerate(terms.get(key) or []):
            if isinstance(v, str) and v.strip():
                out.append((v, _loc(rel, f"terms.{key}[{i}]", "용어")))
    for k, v in (terms.get("messages") or {}).items():
        if isinstance(v, str) and v.strip():
            out.append((v, _loc(rel, f"terms.messages.{k}", "메시지")))
    return out


def _database(doc: list, rel: str, fields: list[str], include_notes: bool) -> list:
    out = []
    keys = fields + (["note"] if include_notes else [])
    for i, row in enumerate(doc or []):
        if not isinstance(row, dict):
            continue
        label = row.get("name") or f"#{i}"
        for f in keys:
            v = row.get(f)
            if isinstance(v, str) and v.strip():
                out.append((v, _loc(rel, f"[{i}].{f}", str(label))))
    return out


def _is_command_list(node) -> bool:
    return (isinstance(node, list) and len(node) > 0
            and all(isinstance(x, dict) and "code" in x and "parameters" in x
                    for x in node))


def _event_lists(node, rel: str, prefix: list | None = None, label: str = "",
                 threshold: float = 0.45) -> list:
    prefix = prefix or []
    out = []
    if isinstance(node, dict):
        label = node.get("displayName") or node.get("name") or label
        dn = node.get("displayName")
        if isinstance(dn, str) and dn.strip():
            out.append((dn, _loc(rel, format_path(prefix + ["displayName"]), "맵 이름")))
        for k, v in node.items():
            out += _event_lists(v, rel, prefix + [k], label, threshold)
    elif isinstance(node, list):
        if _is_command_list(node):
            out += _commands(node, rel, prefix, label, threshold)
        else:
            for i, v in enumerate(node):
                out += _event_lists(v, rel, prefix + [i], label, threshold)
    return out


def _commands(cmds: list, rel: str, prefix: list, label: str,
              threshold: float = 0.45) -> list:
    out = []
    i = 0
    while i < len(cmds):
        c = cmds[i]
        code = c.get("code")
        params = c.get("parameters") or []

        if code in MERGEABLE:
            span_ptrs, lines = [], []
            j = i
            while j < len(cmds) and cmds[j].get("code") == code:
                p = cmds[j].get("parameters") or []
                if not p or not isinstance(p[0], str):
                    break
                span_ptrs.append(format_path(prefix + [j, "parameters", 0]))
                lines.append(p[0])
                j += 1
            if span_ptrs and any(l.strip() for l in lines):
                out.append(("\n".join(lines),
                            _loc(rel, span_ptrs[0], label, span=span_ptrs)))
            i = max(j, i + 1)
            continue

        if code == CHOICES and params and isinstance(params[0], list):
            for k, choice in enumerate(params[0]):
                if isinstance(choice, str) and choice.strip():
                    out.append((choice, _loc(
                        rel, format_path(prefix + [i, "parameters", 0, k]),
                        f"{label} 선택지" if label else "선택지")))
        elif code in PARAM1_CODES and len(params) > 1 and isinstance(params[1], str):
            if params[1].strip():
                out.append((params[1], _loc(
                    rel, format_path(prefix + [i, "parameters", 1]), label)))
        elif code == PLUGIN_CMD_MZ and len(params) > 3 and isinstance(params[3], dict):
            # MZ 플러그인 커맨드: parameters[3] 이 인자 객체.
            # 인자 값이 또 JSON 문자열일 수 있어 jsonnest 로 겹겹이 벗깁니다.
            head = format_path(prefix + [i, "parameters", 3])
            plug = str(params[1] or params[0] or "플러그인")
            for sub, val in jsonnest.walk(params[3]):
                if not sub or not is_display_key(sub):
                    continue
                if heuristics.score(val) < threshold:
                    continue
                out.append((val, _loc(rel, f"{head}.{sub}",
                                      f"{label} · {plug}" if label else plug)))
        elif code == PLUGIN_CMD_MV and params and isinstance(params[0], str):
            # MV 플러그인 커맨드는 명령 한 줄이라 통째로만 안전하게 다룹니다.
            if _CJK.search(params[0]):
                out.append((params[0], _loc(
                    rel, format_path(prefix + [i, "parameters", 0]),
                    f"{label} · 플러그인 명령" if label else "플러그인 명령")))
        i += 1
    return out
