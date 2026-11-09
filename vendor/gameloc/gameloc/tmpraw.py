# SPDX-License-Identifier: GPL-3.0-or-later
"""TextMeshPro 폰트의 '앞머리' 만 바이트로 직접 읽고 고칩니다.

## 왜 이런 게 필요한가

IL2CPP 로 빌드한 게임은 폰트(MonoBehaviour)의 **구조 설명서가 들어 있지
않습니다.** 그래서 보통 방법으로는 폰트 안을 들여다볼 수 없습니다.

그런데 TextMeshPro 폰트는 **앞부분 순서가 정해져 있습니다.**

    m_GameObject · m_Enabled · m_Script · m_Name
    hashCode · material · materialHashCode        ← TMP_Asset 이 물려준 것
    m_Version            "1.1.0"
    m_SourceFontFileGUID "09f5f6f2c59fdf2449740899f95946b9"
    m_SourceFontFile     원본 글꼴 파일을 가리킴 (없으면 0)
    m_AtlasPopulationMode  0=Static 1=Dynamic 2=DynamicOS

여기까지만 읽으면 됩니다. 뒤쪽(글자표·그림판)은 손대지 않습니다.

## 무엇을 고치는가

    m_SourceFontFile     0  →  한글이 든 글꼴 오브젝트
    m_AtlasPopulationMode 0 →  1 (Dynamic)

Static 폰트는 글자 그림이 통째로 구워져 있어서 없는 글자를 못 만듭니다.
Dynamic 으로 돌리고 원본 글꼴을 물려주면, 게임이 돌면서 필요한 글자를
그때그때 만들어냅니다. **16바이트만 바뀌고 파일 길이는 그대로입니다.**

## 안전장치

앞머리를 읽어서 m_Version 이 '숫자.숫자.숫자' 이고 GUID 가 32자리 16진수이고
방식이 0~2 안에 있을 때만 폰트로 인정합니다. 하나라도 어긋나면 그 오브젝트는
아예 건드리지 않습니다. 엉뚱한 것을 폰트로 잘못 보고 망가뜨리는 일을 막습니다.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass

STATIC, DYNAMIC, DYNAMIC_OS = 0, 1, 2

_VERSION = re.compile(r"\A[0-9]{1,4}(\.[0-9]{1,4}){1,3}\Z")
_GUID = re.compile(r"\A[0-9a-fA-F]{32}\Z")

# m_GameObject(12) + m_Enabled(4) + m_Script(12)
_NAME_AT = 28
# hashCode(4) + material PPtr(12) + materialHashCode(4)
_AFTER_NAME = 20

MAX_NAME = 240


@dataclass(frozen=True)
class Head:
    """폰트 앞머리에서 읽어낸 것."""

    name: str
    version: str
    guid: str
    src_file: int
    src_path: int
    mode: int
    off_src: int          # m_SourceFontFile 이 놓인 자리
    off_mode: int         # m_AtlasPopulationMode 가 놓인 자리

    @property
    def has_source(self) -> bool:
        return self.src_path != 0

    @property
    def is_static(self) -> bool:
        return self.mode == STATIC

    def needs_help(self) -> bool:
        """원본 글꼴이 없는 Static 폰트 — 없는 글자를 만들 길이 없습니다."""
        return self.is_static and not self.has_source


def _string(raw: bytes, i: int) -> tuple[str, int]:
    (n,) = struct.unpack_from("<i", raw, i)
    i += 4
    if n < 0 or n > MAX_NAME or i + n > len(raw):
        raise ValueError("문자열 길이가 이상합니다")
    s = raw[i:i + n].decode("utf-8")
    i += n
    return s, (i + 3) & ~3


def read_head(raw: bytes) -> Head | None:
    """TextMeshPro 폰트면 앞머리를 돌려주고, 아니면 ``None``."""
    if len(raw) < _NAME_AT + 64:
        return None
    try:
        name, i = _string(raw, _NAME_AT)
        i += _AFTER_NAME
        version, i = _string(raw, i)
        if not _VERSION.match(version):
            return None
        guid, i = _string(raw, i)
        if guid and not _GUID.match(guid):
            return None
        off_src = i
        (src_file,) = struct.unpack_from("<i", raw, i)
        (src_path,) = struct.unpack_from("<q", raw, i + 4)
        i += 12
        off_mode = i
        (mode,) = struct.unpack_from("<i", raw, i)
    except (ValueError, struct.error, UnicodeDecodeError):
        return None
    if mode not in (STATIC, DYNAMIC, DYNAMIC_OS):
        return None
    if src_file < 0 or src_file > 4096:
        return None
    if not name:
        return None
    return Head(name=name, version=version, guid=guid, src_file=src_file,
                src_path=src_path, mode=mode, off_src=off_src,
                off_mode=off_mode)


def retarget(raw: bytes, head: Head, font_path_id: int) -> bytes:
    """원본 글꼴을 물려주고 Dynamic 으로 돌립니다. 길이는 안 변합니다."""
    if font_path_id == 0:
        raise ValueError("가리킬 글꼴이 없습니다")
    out = bytearray(raw)
    struct.pack_into("<i", out, head.off_src, 0)                 # 같은 파일 안
    struct.pack_into("<q", out, head.off_src + 4, font_path_id)
    struct.pack_into("<i", out, head.off_mode, DYNAMIC)
    new = bytes(out)
    if len(new) != len(raw):
        raise ValueError("길이가 변했습니다")
    check = read_head(new)
    if check is None:
        raise ValueError("고친 뒤 다시 읽지 못했습니다")
    if (check.src_path, check.mode, check.name) != (font_path_id, DYNAMIC, head.name):
        raise ValueError("고친 값이 들어가지 않았습니다")
    return new


# ---------------------------------------------------------------------------
# 이미 글자가 채워진 Dynamic 폰트 비우기
#
# 실제 사례(RJ406835, Unity 2022.3 · IL2CPP): 일본어 폰트 ``UDDIGIKYOKASHON-R SDF``
# 는 Dynamic 인데 글자 6,269개가 이미 구워져 있었습니다(빌드 때 비우지 않는 설정).
# 그 폰트의 원본 글꼴만 한글 글꼴로 갈아치우면, 새 글꼴의 글리프 번호가 **옛 글꼴이
# 남긴 글자표와 겹쳐** 한글 한 글자가 엉뚱한 한자로 그려집니다(액→醸 처럼).
# 글자표를 비워 '막 만든 Dynamic 폰트' 로 돌리면 정상으로 나옵니다(게임에서 확인).
#
# 앞머리(위의 read_head) 다음의 배치 — TMP_FontAsset 1.1.0 / Unity 2022:
#   m_AtlasPopulationMode(4) 에 이어
#   faceIndex(4) · 문자열 둘 · 4바이트 값 18개(FaceInfo)
#   m_GlyphTable      개수 N + N×52
#   m_CharacterTable  개수 M + M×16
#   m_AtlasTextures   개수 A + A×12 (PPtr)
#   그림판 번호·여러 장·빌드 때 비움 (4바이트 셋)
#   m_UsedGlyphRects  개수 U + U×16
#   m_FreeGlyphRects  개수 F + F×16
# 모든 자리를 **크기가 서로 맞아떨어지는지** 확인하고, 하나라도 어긋나면 손대지
# 않습니다(None). 다른 버전의 TMP 는 배치가 달라 여기서 걸러집니다.
# ---------------------------------------------------------------------------

_FACE_WORDS = 18
GLYPH_SIZE = 52
CHAR_SIZE = 16
RECT_SIZE = 16
PPTR_SIZE = 12
_MAX_COUNT = 400_000


@dataclass(frozen=True)
class Tables:
    off_glyphs: int       # m_GlyphTable 개수가 놓인 자리
    glyphs: int
    chars: int
    off_atlas: int        # m_AtlasTextures 개수가 놓인 자리
    atlases: int
    atlas_path_id: int    # 첫 그림판(Texture2D)의 path_id (같은 파일 안일 때)
    off_used: int
    used: int
    end: int              # m_FreeGlyphRects 가 끝나는 자리

    @property
    def populated(self) -> bool:
        return self.glyphs > 0 or self.chars > 0 or self.used > 0


def read_tables(raw: bytes, head: Head) -> Tables | None:
    """글자표 자리를 찾습니다. 크기가 안 맞으면 ``None`` (그 폰트는 건드리지 않음)."""
    try:
        i = head.off_mode + 8                     # 방식(4) + faceIndex(4)
        for _ in range(2):                        # family · style
            n, = struct.unpack_from("<i", raw, i)
            if n < 0 or n > MAX_NAME:
                return None
            i = (i + 4 + n + 3) & ~3
        i += _FACE_WORDS * 4
        off_glyphs = i
        n_glyphs, = struct.unpack_from("<i", raw, i)
        if not 0 <= n_glyphs <= _MAX_COUNT:
            return None
        i += 4 + n_glyphs * GLYPH_SIZE
        n_chars, = struct.unpack_from("<i", raw, i)
        if not 0 <= n_chars <= _MAX_COUNT:
            return None
        i += 4 + n_chars * CHAR_SIZE
        off_atlas = i
        n_atlas, = struct.unpack_from("<i", raw, i)
        if not 0 <= n_atlas <= 32:
            return None
        atlas_pid = 0
        if n_atlas:
            file_id, atlas_pid = struct.unpack_from("<iq", raw, i + 4)
            if file_id != 0 or atlas_pid <= 0:
                return None
        i += 4 + n_atlas * PPTR_SIZE + 12         # PPtr 들 + 셋(4바이트)
        off_used = i
        n_used, = struct.unpack_from("<i", raw, i)
        if not 0 <= n_used <= n_glyphs:
            return None
        i += 4 + n_used * RECT_SIZE
        n_free, = struct.unpack_from("<i", raw, i)
        if not 0 <= n_free <= _MAX_COUNT:
            return None
        end = i + 4 + n_free * RECT_SIZE
        if end > len(raw):
            return None
        # 사각형 몇 개를 열어 보아 값이 상식 안에 있는지 본다
        for k in range(min(n_used, 3)):
            x, y, w, h = struct.unpack_from("<4i", raw, off_used + 4 + k * RECT_SIZE)
            if min(x, y, w, h) < 0 or max(x, y, w, h) > 65536:
                return None
    except struct.error:
        return None
    return Tables(off_glyphs=off_glyphs, glyphs=n_glyphs, chars=n_chars,
                  off_atlas=off_atlas, atlases=n_atlas, atlas_path_id=atlas_pid,
                  off_used=off_used, used=n_used, end=end)


def clear_tables(raw: bytes, t: Tables, atlas_w: int, atlas_h: int) -> bytes:
    """글리프·글자·사용한 칸을 비우고, 빈 칸은 그림판 전체 하나로 되돌립니다.

    그림판 그림 자체는 그대로 둡니다(새 글자가 그려지며 덮입니다). 뒤쪽 자료는
    바이트 그대로 잇습니다.
    """
    if atlas_w <= 0 or atlas_h <= 0 or atlas_w > 65536 or atlas_h > 65536:
        raise ValueError("그림판 크기가 이상합니다")
    zero = struct.pack("<i", 0)
    free = struct.pack("<i4i", 1, 0, 0, atlas_w - 1, atlas_h - 1)
    new = (raw[:t.off_glyphs] + zero + zero
           + raw[t.off_atlas:t.off_used] + zero
           + free + raw[t.end:])
    head = read_head(new)
    if head is None:
        raise ValueError("고친 뒤 다시 읽지 못했습니다")
    again = read_tables(new, head)
    if again is None or again.populated or again.atlas_path_id != t.atlas_path_id:
        raise ValueError("글자표를 비운 결과가 맞지 않습니다")
    return new
