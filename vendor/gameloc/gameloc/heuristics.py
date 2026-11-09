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

"""번역할 만한 문자열인지 점수를 매깁니다 (유니티 쪽에서 주로 씁니다).

게임 빌드에는 사람이 읽을 텍스트가 아닌 문자열이 훨씬 많습니다 — 셰이더 키워드,
에셋 경로, GUID, 열거형 이름. 전부 쏟아내면 진짜 대사 300줄이 4만 줄에 묻힙니다.
"""

from __future__ import annotations

import re

LEVELS = {"loose": 0.20, "normal": 0.45, "strict": 0.70}

# 타입트리 안에서 사람이 읽을 텍스트가 절대 아닌 필드 이름
KEY_DENYLIST = {
    "m_Name", "m_Guid", "m_Tag", "m_TagString", "m_Layer", "m_LayerName",
    "m_SortingLayerName", "m_ShaderKeywords", "m_AssetBundleName",
    "m_AssetBundleVariant", "m_PathName", "m_ClassName", "m_Namespace",
    "m_AssemblyName", "assetPath", "path", "guid", "fileID", "typeName",

    # --- 유니티 입력 시스템 ------------------------------------------------
    # 여기 값들은 화면 문구처럼 생겼지만("Button", "Hold", "Move", "Attack")
    # 사실은 **입력을 이어주는 이름**입니다. 번역하면 클릭·키 입력이 어디로도
    # 연결되지 않아 **버튼이 안 눌립니다.** 게임은 멀쩡히 켜지고 화면도 정상이라
    # 원인을 찾기가 아주 어렵습니다.
    "m_ExpectedControlType", "m_Interactions", "m_Processors",
    "m_Path", "m_Groups", "m_Action", "m_BindingGroup", "m_ControlPath",
    "m_DevicePath", "m_Type", "m_Id", "m_Flags", "m_SingletonAction",
    "m_ActionMaps", "m_ControlSchemes", "m_DeviceRequirements",
    "m_BindingMask", "m_ProcessorsString", "m_InteractionsString",

    # .NET 타입 이름. 어느 게임에서든 화면에 안 나옵니다.
    "m_TypeString", "m_TypeName", "m_ObjectType",

    # --- 유니티 UI 가 **이름으로 찾아 부르는** 것들 ------------------------
    # 진짜 게임(Freedom)에서 이것들이 번역돼 게임 파일에 그대로 들어갔습니다.
    #
    #   m_OnClick…m_TargetAssemblyTypeName
    #       "EnvironmentSettings, Assembly-CSharp" → "환경 설정, 어셈블리-CSharp"
    #   m_AnimationTriggers.m_NormalTrigger  "Normal" → "정상"
    #
    # 앞의 것은 버튼을 누르면 **어느 클래스의 어느 메서드를 부를지** 적은
    # 글자이고, 뒤의 것은 애니메이터가 상태를 찾는 이름표입니다. 번역하면
    # 못 찾아서 버튼이 안 눌리거나 반응 애니메이션이 안 돕니다. 유니티가 정한
    # 필드 이름이라 어느 게임에서든 화면 문구가 아닙니다.
    "m_TargetAssemblyTypeName", "m_ObjectArgumentAssemblyTypeName",
    "m_MethodName", "m_AnimationTriggers",
    # TextMeshPro 글꼴의 이름표("Liberation Sans", "Regular")
    "m_FaceInfo",

    # --- TextMeshPro 글꼴 설정 --------------------------------------------
    # 진짜 게임에서 이런 것이 **문장 하나로** 목록에 올라왔습니다.
    #
    #   NotoSansSC SDF › m_CreationSettings.characterSequence
    #   "啊阿埃挨哎唉哀皑癌蔼矮艾碍…"   (한자 7,000자)
    #
    # 대사가 아니라 **글꼴에 구워 넣을 글자 목록**입니다. 한자가 잔뜩이라
    # CJK 점수가 만점으로 나와 그대로 통과했습니다.
    #
    # 번역하면 글꼴 설정이 망가지고, 그 전에 번역기 요금부터 터집니다 —
    # 한 줄이 7,000자니까요.
    "m_CreationSettings", "characterSequence", "referencedFontAssetGUID",
    "referencedTextAssetGUID", "fontSourcePath", "sourceFontFileGUID",
    "m_fontInfo", "m_CharacterTable", "m_GlyphTable", "m_UsedGlyphRects",
    "m_FreeGlyphRects", "m_KerningTable", "m_FontFeatureTable",
}

_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CAMEL = re.compile(r"^[a-z]+(?:[A-Z][a-z0-9]*)+$")
_PASCAL_TOKEN = re.compile(r"^(?:[A-Z][a-z0-9]*){2,}$")
_HEXISH = re.compile(r"^[0-9a-fA-F\-]{8,}$")
_ID_CODE = re.compile(r"^[A-Za-z_]{1,6}[-_]?\d{1,6}$")
# 밑줄로 이어붙인 이름은 문장이 아니라 에셋·모델·모션 이름입니다.
# 일본어로 되어 있어도 마찬가지입니다:  海の家_波_木の揺れ
# 밑줄만 봅니다. 하이픈은 진짜 문구에도 흔합니다("Auto-Save", "Re-try").
_SNAKEY = re.compile(r"^\S*_\S*$")
_SENTENCE_MARK = re.compile(r"[\u3002\u3001\uff01\uff1f\u2026.!?,]")
_NUMERIC = re.compile(r"^[\d\s.,:;+\-*/%()\[\]#]+$")
_URL = re.compile(r"^\w+://|^www\.", re.I)
_PATHY = re.compile(r"[/\\]")
_EXT = re.compile(r"\.(png|jpg|jpeg|tga|wav|ogg|mp3|mp4|asset|prefab|unity|mat|"
                  r"anim|controller|fbx|shader|dll|txt|json|csv|bytes|ttf|otf)$", re.I)
_FORMAT_ONLY = re.compile(r"^[\s{}\[\]<>%$#@|_+=~^&*\\/,.:;!?'\"`-]*$")
_TAG_ONLY = re.compile(r"^\s*<[^>]+>\s*$")

# 구조가 통째로 딸려온 줄. 줄 단위로 쪼갠 설정 파일에서 나옵니다:
#     "バージョン": {          "items": [          },
# 이걸 번역하면 게임이 값을 못 찾습니다. 대사에는 이런 모양이 없습니다.
_STRUCTURE = re.compile(
    r"""^\s*(?:
          [\]\}\[\{,;]+                       # 괄호·쉼표만
        | ["'][^"']*["']\s*:\s*[\[\{]?\s*,?   # "키": 또는 "키": {
        | ["'][^"']*["']\s*:\s*.*[\[\{]\s*,?  # "키": ... {
        | ["'][^"']*["']\s*:\s*["'][^"']*["']\s*,?   # "키": "값",
        | ["'][^"']*["']\s*:\s*[\d.eE+-]+\s*,?       # "키": 12,
        | ["'][^"']*["']\s*:\s*(?:true|false|null)\s*,?
        | <[/!?][^>]*>                        # </닫는태그>
    )\s*$""",
    re.X,
)


def looks_like_identifier(text: str) -> bool:
    """대사가 아니라 '무언가를 가리키는 이름' 으로 보이는가.

    번역하면 안 되는 것들입니다 — 모델 이름, 모션 이름, 스위치 이름, 파일 키.
    판단 근거는 언어가 아니라 **모양**입니다:
      · 밑줄로 토막이 이어져 있고
      · 공백이 없고
      · 문장 부호가 없다
    """
    s = (text or "").strip()
    if not s or len(s) > 80:
        return False
    if " " in s or "\n" in s or "\t" in s:
        return False
    if _SENTENCE_MARK.search(s):
        return False
    return bool(_SNAKEY.match(s))


# ── .NET 배관 ────────────────────────────────────────────────────────────
#
# **번역하면 게임이 깨집니다.** 유니티가 이 글자로 클래스와 메서드를 찾기
# 때문입니다. 한국어로 바꿔 놓으면 못 찾고, 그 이벤트가 통째로 안 돕니다.
#
# 실제 게임(Fungus 로 만든 Mono 빌드)에서 뽑은 15,000개 안에 이런 것이
# 잔뜩 섞여 있었습니다. UnityEvent 가 "어느 클래스의 어느 메서드를
# 부를까" 를 글자로 적어 두기 때문입니다:
#
#   KaiwaHojo, Assembly-CSharp, Version=0.0.0.0, Culture=neutral, PublicKeyToken=null
#   System.Int32, mscorlib, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089
#   ShakeCoroutine (Single, Single, Int32): Void
#
# 사람이 봐도 대사가 아닌 게 뻔한데, 우리 규칙은 **공백이 있다**는 이유로
# 전부 통과시켰습니다. 5,635번 쓰인 줄이 번역 목록 위쪽에 앉아 있었습니다.

# 어셈블리로 한정한 타입 이름. ``Version=`` 과 ``Culture=`` 가 함께 오면
# 사람 글일 수가 없습니다.
_ASSEMBLY = re.compile(
    r"(?:Version\s*=\s*\d+\.\d+\.\d+\.\d+|PublicKeyToken\s*=|Culture\s*=\s*neutral)")

# 메서드 생김새: ``Name (Type, Type): Return``
_SIGNATURE = re.compile(
    r"^[A-Za-z_][\w.<>`]*\s*\([^)]*\)\s*:\s*[A-Za-z_][\w.<>\[\]]*$")

# ``System.Collections.Generic.List`` 처럼 점으로 이어진 순수 타입 이름.
_DOTTED_TYPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$")

# ``Apps.Masters.StatusMaster, Assembly-CSharp`` — 어셈블리 이름만 붙은 **짧은 형태**.
# Odin Serializer 같은 직렬화 도구가 데이터 파일 안에 클래스 이름을 이렇게 적어 둡니다.
# ``Version=`` 이 없어 위의 ``_ASSEMBLY`` 에 안 걸렸고, 번역기가 이것을 소문자로 바꾸고
# ``Assembly-CSharp`` 를 ``어셈블리-csharp`` 로 번역해서, 게임이 자기 자료 클래스를 못 찾아
# **검은 화면** 이 됐습니다(RJ01657800, 유니티 6).
_ASSEMBLY_SHORT = re.compile(
    r"^[A-Za-z_][\w.+`<>\[\]]*\s*,\s*"
    r"(?:Assembly-CSharp(?:-firstpass)?|Assembly-UnityScript|mscorlib|netstandard|"
    r"UnityEngine(?:\.\w+)*|Unity\.[\w.]+|System(?:\.\w+)*)$", re.I)

_RUNTIME_ROOTS = ("system.", "unityengine.", "unityeditor.", "mscorlib",
                  "assembly-csharp", "netstandard", "microsoft.", "mono.",
                  "newtonsoft.", "tmpro.", "fungus.")


# 글꼴에 구울 글자 목록은 **띄어쓰기도 문장부호도 없이** 글자만 수천 개
# 이어집니다. 필드 이름을 못 볼 때(raw 경로)를 위한 두 번째 그물입니다.
GLYPHS_MIN = 400


def looks_like_glyph_set(text: str) -> bool:
    """대사가 아니라 '글꼴에 넣을 글자 목록' 인가.

    사람이 쓴 글에는 **같은 글자가 되풀이됩니다.** 글자 목록은 그렇지 않아요 —
    한 글자씩 한 번만 나옵니다. 그걸로 가릅니다.
    """
    s = (text or "").strip()
    if len(s) < GLYPHS_MIN:
        return False
    body = s.replace("\n", "")
    if len(set(body)) < len(body) * 0.9:
        return False                        # 되풀이가 많으면 사람 글입니다
    # 사람 글이라면 이만큼 길면 띄어쓰기나 문장부호가 반드시 섞입니다.
    breathing = sum(c.isspace() or c in "。、．，!?！？…" for c in body)
    return breathing < len(body) * 0.01


def looks_like_dotnet(text: str) -> bool:
    """유니티가 클래스·메서드를 찾는 데 쓰는 글자인가.

    **번역하면 게임이 깨집니다.** 대사가 아니라 배관입니다.
    """
    s = (text or "").strip()
    if not s or len(s) > 400:
        return False
    if _CJK.search(s):
        return False            # 일본어가 섞였으면 배관이 아닙니다
    if _ASSEMBLY.search(s) or _ASSEMBLY_SHORT.match(s):
        return True
    if _SIGNATURE.match(s):
        return True
    low = s.lower()
    if _DOTTED_TYPE.match(s) and any(low.startswith(r) for r in _RUNTIME_ROOTS):
        return True
    return False


def score(text: str) -> float:
    """``text`` 가 사람이 읽을, 화면에 나오는 문구일 확신도."""
    if text is None:
        return 0.0
    s = text.strip()
    if not s:
        return 0.0

    if _FORMAT_ONLY.match(s) or _TAG_ONLY.match(s):
        return 0.0
    # 언어와 무관하게, 파일 구조가 딸려온 줄은 대사가 아닙니다.
    # CJK 검사보다 **먼저** 와야 합니다 — 키 이름이 일본어인 경우가 많습니다.
    if _STRUCTURE.match(s):
        return 0.0
    if _URL.search(s) or _EXT.search(s):
        return 0.0
    if _HEXISH.match(s) and not _CJK.search(s):
        return 0.0
    if _NUMERIC.match(s):
        return 0.0
    if _PATHY.search(s) and " " not in s and not _CJK.search(s):
        return 0.0

    # .NET 배관은 공백이 있어서 식별자 검사를 빠져나갑니다. 먼저 잡습니다.
    if looks_like_dotnet(s):
        return 0.0

    # 덱 코드 같은 ``이름=값,이름=값`` 목록. 일본어가 섞여 있어도 대사가 아닙니다.
    if looks_like_kv_code(s):
        return 0.0

    # 글꼴에 구울 글자 목록. CJK 가 잔뜩이라 점수는 만점으로 나오는데
    # 대사가 아닙니다. CJK 검사보다 **먼저** 와야 합니다.
    if looks_like_glyph_set(s):
        return 0.0

    # 식별자 모양이면 언어와 무관하게 거부.
    # 이 검사는 CJK 검사보다 **먼저** 와야 합니다. 게임의 에셋·모델 이름은
    # 일본어인 경우가 많고(예: 海の家_波_木の揺れ), 그걸 번역하면 플러그인이
    # 대상을 못 찾아 게임이 죽습니다. 대사가 밑줄로 이어진 경우는 없습니다.
    if looks_like_identifier(s):
        return 0.15

    cjk = len(_CJK.findall(s))
    if cjk:
        return min(1.0, 0.75 + 0.05 * min(cjk, 5))

    if len(s) < 2:
        return 0.0

    words = s.split()
    if sum(c.isalpha() for c in s) == 0:
        return 0.0

    if len(words) == 1:
        w = s
        if _ID_CODE.match(w):
            return 0.05            # d001, npc12 — 행 키
        if _CAMEL.match(w) or _PASCAL_TOKEN.match(w):
            return 0.10
        if "_" in w and _IDENT.match(w):
            return 0.05
        if _IDENT.match(w):
            if w.islower():
                # name, desc, speaker — 대개 열 이름. loose 에서만 잡힙니다.
                return 0.35
            return 0.50 if 3 <= len(w) <= 20 else 0.25
        return 0.30

    val = 0.55
    if len(words) >= 3:
        val += 0.15
    if re.search(r"[.!?\u2026]", s):
        val += 0.10
    if s[:1].isupper() and any(c.islower() for c in s):
        val += 0.10
    if len(s) > 40:
        val += 0.05
    if all(_IDENT.match(w) and w.isupper() for w in words):
        val -= 0.35
    return max(0.0, min(1.0, val))


# ── 유니티 Localization 전용 ──────────────────────────────────────────────
#
# 여기 규칙은 **그 표를 알아본 오브젝트에만** 적용합니다. 전역 목록에 섞으면
# 안 됩니다. 한 게임 때문에 만든 규칙이 다른 게임의 멀쩡한 대사를 조용히
# 걸러내기 시작하고, 그렇게 쌓인 규칙 서른 개는 아무도 못 풉니다.
#
# 이 시스템은 이름표(키)와 번역문을 따로 들고 다닙니다.
#
#     SharedTableData.m_Entries[i].m_Key      "MainMenu_EnterBar"  <- 이름표
#     StringTable.m_TableData[i].m_Localized  "ENTER THE BAR"      <- 번역문
#
# 게임은 이름표로 번역문을 찾습니다. 이름표를 번역하면 짝이 끊겨 표 전체가
# 안 나옵니다 —  No translation found for 'Velvet Room' in Menu
LOCALIZATION_FIELDS = {
    "m_Key", "m_KeyId", "m_TableCollectionName",
    "m_TableCollectionNameGuidString", "m_LocaleId", "m_Code",
    "m_LocaleName", "m_CustomFormatterName", "m_SharedTableDataGuid",
    "m_KeyGenerator",
}

# 이 필드들이 함께 보이면 유니티 Localization 표입니다.
_LOCALIZATION_MARKS = (
    {"m_TableData", "m_SharedData"},        # StringTable
    {"m_Entries", "m_TableCollectionName"},  # SharedTableData
)


def is_localization_table(tree) -> bool:
    """이 오브젝트가 유니티 Localization 의 표인가."""
    if not isinstance(tree, dict):
        return False
    keys = set(tree)
    return any(mark <= keys for mark in _LOCALIZATION_MARKS)


def denies_in_localization(path: str) -> bool:
    """Localization 표 **안에서만** 막을 필드인가."""
    for part in path.split("."):
        if part.split("[")[0] in LOCALIZATION_FIELDS:
            return True
    return False


# 이 필드에 든 값은 **틀림없이 화면에 나오는 글자**입니다. 게임이 그렇게
# 설계했다고 스스로 밝히고 있는 자리라, 짐작으로 거를 이유가 없습니다.
#
#   StringTable.m_TableData[i].m_Localized   유니티 Localization 의 번역문
#
# 여기를 짐작에 맡기면 'EXIT', 'SETTINGS' 같은 대문자 라벨이 "이건 라벨이지
# 문장이 아니다" 로 걸러져 메뉴가 절반만 번역됩니다.
KEY_ALLOWLIST = {
    "m_Localized", "m_LocalizedString", "m_Text", "m_TranslatedValue",
}


def is_display_key(path: str) -> bool:
    """짐작 없이 그대로 가져와도 되는, 확실한 화면 문구 필드인가."""
    tail = path.rsplit(".", 1)[-1].split("[")[0]
    return tail in KEY_ALLOWLIST


# ── 오브젝트 **모양**으로 알아보는 이름표 ────────────────────────────────
#
# ``description`` · ``Name`` · ``Type`` 같은 이름은 어느 게임에서는 화면 문구입니다.
# 그래서 전역 목록(KEY_DENYLIST)에 넣으면 다른 게임의 멀쩡한 글을 조용히 걸러
# 냅니다. 여기 규칙은 **그 모양을 한 오브젝트 안에서만** 겁니다 — Localization
# 규칙(``is_localization_table``)과 같은 방식입니다.
#
# 진짜 게임(Freedom, Fungus 로 만든 비주얼노벨)에서 대사 옆에 이런 것들이
# 15,741개 중 절반 넘게 섞여 있었습니다.
#
#   Fungus 명령   itemId + indentLevel
#       storyText      = 대사              <- 번역
#       description    = 편집기용 메모      <- 7,294개, 화면에 안 나옴
#       targetMethod   = 부를 메서드 이름  <- 번역하면 이벤트가 안 돕니다
#   Fungus 블록   blockName + commandList
#       blockName      = 다른 곳에서 이름으로 부르는 블록 이름
#   Live2D(Cubism) 모션   ParameterIds + ParameterCurves
#       ParameterIds   = 모델 파라미터 이름 ("Paramhaa")
#   Live2D(Cubism) 표정   Type + Parameters + FadeInTime
#       Type           = "Live2D Expression"
#   ``Name`` 과 ``DisplayName`` 이 **짝으로** 있는 조각
#       Name 은 찾는 키, 화면에 보이는 것은 DisplayName
#
# 값에 일본어가 있고 없고는 보지 않습니다. 이 모양이면 이름표입니다.
IDENTIFIER_SHAPES: tuple[tuple[frozenset, frozenset], ...] = (
    (frozenset({"itemId", "indentLevel"}), frozenset({
        "description", "commentText", "commenterName", "targetMethod",
        "targetMethodText", "targetComponentText", "targetComponentAssemblyName",
        "targetComponentFullname", "returnValueVariableKey", "returnValueType"})),
    (frozenset({"blockName", "commandList"}), frozenset({"blockName"})),
    (frozenset({"ParameterIds", "ParameterCurves"}),
     frozenset({"ParameterIds", "MotionName"})),
    (frozenset({"Type", "Parameters", "FadeInTime"}),
     frozenset({"Type", "Parameters"})),
    (frozenset({"Name", "DisplayName"}), frozenset({"Name"})),

    # ── 진짜 게임(Freedom)에서 게임을 실제로 실행해 보고서야 드러난 것들 ────────────
    # 등장인물 변수 이름(Momo·Roze·Mikan …)이 번역돼 들어가자 게임 로그에
    # ``Variable Momo not found`` 가 줄줄이 찍혔습니다. 변수를 이름으로 찾기 때문입니다.
    #
    #   Fungus 변수 (Integer/String/Boolean … Variable)   scope + key + value
    #       key      = 변수 이름         <- 번역하면 스크립트가 변수를 못 찾습니다
    #       value    = 값(문자열 변수면 화면에 나올 수 있음)  <- 그대로 둡니다
    (frozenset({"scope", "key", "value"}), frozenset({"key"})),
    #   Fungus MessageReceived                          parentBlock + message
    #       message  = SendFungusMessage 가 보내는 이름표
    (frozenset({"parentBlock", "suppressBlockAutoSelect", "message"}),
     frozenset({"message"})),
    #   이 게임의 의상 자료(CostumeData): 의상·파라미터 이름이 전부 식별자입니다.
    (frozenset({"costumeName", "baseOutfit", "outfitValues"}), frozenset({
        "costumeName", "top", "bottom", "underwear", "bra", "hat", "socks", "shoes",
        "make", "baseOutfit", "outfitValues"})),
    #   이 게임의 모션 사운드 트리거: triggers[].label 은 트리거 이름입니다.
    (frozenset({"animator", "triggers", "showDebugUI"}), frozenset({"triggers"})),
    #   이 게임의 모브 한마디(MobOneLineTalk): lines 는 Localization 표(Mob)의 키입니다.
    (frozenset({"lines", "speechBubblePrefab", "displayTime"}), frozenset({"lines"})),
)

_FIRST_SEGMENT = re.compile(r"[.\[]")


def shape_denials(tree) -> frozenset:
    """이 오브젝트 모양이 이름표로 지목하는 **맨 앞 필드 이름들**.

    오브젝트 하나에 한 번만 계산해서 글자마다 ``denied_by_shape`` 에 넘깁니다.
    """
    if not isinstance(tree, dict) or not tree:
        return frozenset()
    keys = tree.keys()
    out: set[str] = set()
    for needed, denied in IDENTIFIER_SHAPES:
        if needed <= keys:
            out |= denied
    return frozenset(out)


def denied_by_shape(path: str, denials: frozenset) -> bool:
    """``path`` 가 (``shape_denials`` 가 지목한) 이름표 필드 안인가."""
    return bool(denials) and _FIRST_SEGMENT.split(path, 1)[0] in denials


# ── 텍스트 에셋 안의 프로그램 코드·라이선스 문서 ──────────────────────────
#
# 진짜 게임(Freedom)의 data.unity3d 에는 대사가 아닌 텍스트 에셋이 섞여 있었습니다.
#
#   fungus · inspect · junglestory   Lua 프로그램 코드     ("local function …", "return M")
#   COPYING · LICENSE                 라이선스 문서        ("Creative Commons Legal Code")
#
# 그대로 번역 목록에 오르면 번역해서 코드를 망가뜨리게 됩니다. **줄 단위 글**(``lines``)
# 로만 읽힌 텍스트 에셋에만 씁니다 — JSON·CSV 같은 구조가 있는 에셋과, 대사가 줄마다 든
# 다른 게임의 글은 이 문에 걸리지 않습니다(코드 줄이 30% 넘어야 하고 여덟 줄 이상).
_LICENSE_NAMES = ("license", "licence", "copying", "third-party-notices",
                  "thirdpartynotices", "third_party_notices")
_CODE_LINE = re.compile(
    r"^\s*(?:--|//|local\s|function\b|end\b|return\b|require\b|elseif\b|else\b"
    r"|if\b.*\bthen\b|for\b.*\bdo\b|while\b.*\bdo\b|[{}]\s*,?\s*$)")


def is_license_asset(name: str) -> bool:
    """이름으로 알아보는 라이선스 문서(``LICENSE``, ``COPYING`` …)."""
    base = (name or "").strip().lower().rsplit(".", 1)[0]
    return any(base == n or base.startswith(n + " ") or base.endswith(" " + n)
               for n in _LICENSE_NAMES)


_SVG_HEAD = re.compile(r"<svg[\s>]", re.I)


def is_svg(text: str) -> bool:
    """SVG 도형 파일인가. 글이 아니라 그림입니다(줄마다 ``viewBox=…`` 같은 것이 뽑힙니다).

    실제 사례: 어떤 게임의 글꼴 그림 자산 1,231개가 '문장' 으로 뽑혀 표를 어지럽혔습니다.
    파일 앞머리에 ``<svg`` 가 있을 때만 그렇게 봅니다.
    """
    return bool(_SVG_HEAD.search((text or "")[:4096]))


def looks_like_code(text: str) -> bool:
    """줄 단위 글이 사실은 프로그램 코드인가. 여덟 줄 이상이고 코드 줄이 30% 넘을 때만."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if len(lines) < 8:
        return False
    code = sum(1 for ln in lines if _CODE_LINE.match(ln))
    return code * 10 >= len(lines) * 3


def is_denied_key(path: str) -> bool:
    """경로 **어느 마디라도** 알려진 비-텍스트 필드인가.

    마지막 마디만 보면 ``m_KeyGenerator.m_Something`` 처럼 한 겹 안쪽에 숨은
    값을 놓칩니다. 살림살이 밑에 딸린 것은 통째로 살림살이입니다.
    """
    for part in path.split("."):
        if part.split("[")[0] in KEY_DENYLIST:
            return True
    return False


# ── 이름표 열(列) — 표 모양 데이터에서 '이 칸은 이름표다' 를 알아봅니다 ──────────
#
# 실제 사례(Succubus Reborn, 유니티 6): 카드 게임의 자료 표(ScriptableObject)가
#
#     word[i]  { id: "Demon",  en: "Demon",  jp: "悪魔",  zh_Hans: "恶魔" }
#     card[i]  { cardAttribute: "Human", cardType: "Sucker", jp… }
#
# 처럼 생겼습니다. 화면에 나오는 것은 ``jp`` 이고, ``id``·``cardType`` 은 게임이
# **이름으로 찾는 칸**입니다. 표 전체를 번역했더니 ``Demon`` 과 ``Incubus`` 가 둘 다
# "악마" 가 되어 게임이 시작하다 ``An item with the same key has already been
# added`` 로 죽었고, 화면은 **무한 로딩** 이었습니다.
#
# 규칙(좁게 겁니다):
#   · 같은 표에 CJK 글이 든 칸이 있고            (= 일본어·중국어 원문 표)
#   · 어떤 칸의 값이 거의 전부 영문 낱말 하나이며   (Human · Sucker · Hypno …)
#   · 그런 행이 다섯 개 이상일 때
# 그 칸은 이름표입니다. 영어가 원문인 게임(CJK 가 없는 표)에는 걸리지 않고,
# 영어를 원문으로 골랐을 때는 부르는 쪽에서 아예 끕니다.
# 낱말 하나(``Human``), 또는 쉼표로 이은 낱말 목록(``Nature,Miracle`` · ``Seal7,Will,Milk2``).
# 빈칸으로 이은 것(``Add IP``)은 이름이 아니라 문구일 수 있어 여기 안 넣습니다.
_ENUM_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,29}(?:\s*,\s*[A-Za-z][A-Za-z0-9_]{0,29})*$")
ENUM_MIN_ROWS = 5
_INDEX = re.compile(r"\[\d+\]")
# 칸 이름이 이런 낱말로 끝나면 값이 하나뿐이어도 이름표로 봅니다 (specialCondition · faceState …)
_CODEISH_NAME = re.compile(
    r"(?:state|condition|type|kind|attr|attribute|tag|mode|flag|status|category|"
    r"target|option|class|group|cue|anim|face|param|key|id|trigger|action|command)s?$",
    re.I)

# 필드 이름이 곧 이름표인 것. ``id`` · ``key`` · ``creditID`` · ``item_id`` …
# 화면 문구가 이런 이름을 쓰는 일은 없습니다.
def is_id_field(path: str) -> bool:
    """경로의 **마지막 마디**가 ``id`` 같은 이름표 필드인가."""
    tail = _INDEX.sub("", path.rsplit(".", 1)[-1])
    if not tail:
        return False
    if tail.lower() in ("id", "ids", "key", "uid", "guid"):
        return True
    return bool(re.search(r"[a-z0-9](?:Id|ID)$|_id$", tail))


# 값이 영문 낱말 하나이고 **필드 이름이 '찾는 이름' 꼴**이면 이름표입니다.
# 실제 사례(RJ01657800): ``_gameSceneName: Evening`` · ``NextSceneName: Title`` (씬 이름),
# ``initialPoseKey: Stand`` · ``reactionKey: Face`` (조회 키). 번역하면 게임이 씬·포즈를 못 찾습니다.
# 값에 공백이 있거나 CJK 가 있으면(진짜 문구일 수 있어) 걸지 않습니다.
# 실제 사례(RJ01692614): ``_animationName: Idel`` · ``playerTag: Player`` · ``Performer: Maid`` ·
# 유니티 입력 모듈의 ``m_HorizontalAxis: Horizontal`` · ``m_SubmitButton: Submit`` (입력 이름).
_REF_NAME = re.compile(
    r"(?:scenename|key|keys|animationname|tag|performer|"
    r"horizontalaxis|verticalaxis|submitbutton|cancelbutton|"
    r"skinname|startinganimation|bonename|state|stringargument)$", re.I)
# 붙여 쓴 영문 낱말 여럿(``MeleeWeapon`` · ``SelectWeapon3``). 화면 문구가 아니라, 언어 표의
# 열쇠 이름을 그대로 적어 둔 UI 자리표시인 경우가 많습니다(번역하면 표에서 못 찾습니다).
_CAMEL_KEY = re.compile(r"^[A-Z][a-z0-9]*(?:[A-Z][a-z0-9]*)+$")
_LATIN_WORD = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def is_ref_field(path: str, value: str) -> bool:
    """필드 이름이 ``…SceneName`` · ``…Key`` 꼴이고 값이 영문 낱말 하나인가."""
    tail = _INDEX.sub("", path.rsplit(".", 1)[-1]).replace("_", "")
    if not tail or not _REF_NAME.search(tail):
        return False
    return bool(_LATIN_WORD.match((value or "").strip()))


def looks_like_table_key(text: str) -> bool:
    """붙여 쓴 영문 낱말 여럿(``MeleeWeapon``)인가. 언어 표가 있는 게임에서만 부릅니다."""
    return bool(_CAMEL_KEY.match((text or "").strip()))


def _column_values(rows: list) -> dict[str, list[str]]:
    cols: dict[str, list[str]] = {}
    for r in rows:
        for k, v in r.items():
            if isinstance(v, str):
                if v.strip():
                    cols.setdefault(k, []).append(v)
            elif isinstance(v, list) and v and all(isinstance(x, str) for x in v):
                vals = [x for x in v if x.strip()]
                if vals:
                    cols.setdefault(k + "[]", []).extend(vals)
    return cols


def enum_columns(tree) -> frozenset:
    """이 오브젝트 안에서 '이름표 열' 로 알아본 경로들 (``word[].id`` 꼴).

    ``[n]`` 을 ``[]`` 로 뭉갠 경로입니다. ``denied_enum`` 으로 견줍니다.
    """
    out: set[str] = set()

    def visit(node, prefix: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                visit(v, f"{prefix}.{k}" if prefix else str(k))
        elif isinstance(node, list):
            rows = [r for r in node if isinstance(r, dict)]
            if len(rows) >= ENUM_MIN_ROWS:
                cols = _column_values(rows)
                table_has_cjk = any(_CJK.search(x)
                                    for vals in cols.values() for x in vals)
                if table_has_cjk:
                    for name, vals in cols.items():
                        # 값이 몇 개 안 되는 칸(예: specialCondition 에 'Envy' 하나)은 이름이
                        # 코드처럼 생겼을 때만 봅니다. 아니면 'note: OK' 같은 것까지 걸립니다.
                        need = 1 if _CODEISH_NAME.search(name.rstrip("[]")) else ENUM_MIN_ROWS
                        if len(vals) < need:
                            continue
                        ok = sum(1 for x in vals if _ENUM_TOKEN.match(x))
                        if ok >= len(vals) * 0.95:
                            out.add(f"{prefix}[].{name}")
            for r in node:
                if isinstance(r, (dict, list)):
                    visit(r, f"{prefix}[]")

    visit(tree, "")
    return frozenset(out)


def denied_enum(path: str, columns: frozenset) -> bool:
    """``path`` 가 (``enum_columns`` 가 알아본) 이름표 열 안인가."""
    return bool(columns) and _INDEX.sub("[]", path) in columns


# 쉼표로 이은 ``이름=값`` 목록. 덱 코드가 이런 모양입니다:
#     GD004=3,GD005=3,GD007=2,…,p=SC001,n=情熱
# 일본어가 섞여 있어 CJK 점수는 만점으로 나오지만 대사가 아닙니다. 번역기가
# ``SC001`` 을 ``SC 001`` 로 바꾸는 순간 덱이 안 열립니다.
_KV_PAIR = re.compile(r"^[A-Za-z_][\w.\-]*=[^,=]*$")


_LOG_TAG = re.compile(r"^\s*\[[A-Za-z][\w:. /\-]{0,40}\]")


def looks_like_log_line(text: str) -> bool:
    """``[PsdImage] …`` 처럼 대괄호 영문 꼬리표로 시작하는 개발자용 로그인가."""
    return bool(_LOG_TAG.match(text or ""))


def looks_like_kv_code(text: str) -> bool:
    """대사가 아니라 ``key=value,key=value,…`` 코드 목록인가 (쌍이 셋 이상)."""
    s = (text or "").strip()
    if len(s) < 8 or "\n" in s or "," not in s:
        return False
    parts = s.split(",")
    if len(parts) < 3:
        return False
    return all(_KV_PAIR.match(p.strip()) for p in parts)
