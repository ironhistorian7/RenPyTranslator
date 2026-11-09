# SPDX-License-Identifier: GPL-3.0-or-later
"""덩이 파일(``data.unity3d``)의 **무게를 먼저 재 봅니다.**

## 왜 이게 필요한가

유니티 6 부터는 게임을 통째로 한 덩이에 넣는 빌드가 흔합니다. 실제로 본
게임(Fungus 로 만든 Mono 빌드)은 ``data.unity3d`` 하나가 **2.4GB** 였습니다.
화면은 파일 이름만 띄운 채 몇십 분을 그대로 있었고, 사용자는 멈춘 줄 알았습니다.

멈춘 게 아니었습니다. UnityPy 가 덩이를 읽는 방식이 이렇습니다:

    blocksReader = EndianBinaryReader(
        b"".join(self.decompress_data(...) for blockInfo in m_BlocksInfo), ...)

**``b"".join`` 입니다.** 압축을 푼 조각을 전부 만들어 놓고, 그걸 다시 하나로
이어 붙입니다. 그래서 가장 부풀 때는 **푼 크기의 두 배**가 메모리에 있습니다.
2.4GB 짜리면 푼 크기가 3GB 안팎이고, 잠깐이지만 6GB가 필요합니다.

그리고 이 일이 **끝나기 전에는 오브젝트가 하나도 안 보입니다.** 그래서 안에서
진행 표시를 할 수가 없습니다. 어디까지 왔는지 물어볼 데가 없으니까요.

## 그래서 무엇을 하나

**들어 올리기 전에 무게를 잽니다.** 덩이의 머리와 블록 목록만 읽으면
(몇 킬로바이트면 됩니다) 푼 크기를 알 수 있습니다. 그걸 이 컴퓨터의 메모리와
견줘서, 되겠다 싶으면 걸릴 시간을 미리 알려 주고, 안 되겠다 싶으면 **몇십 분을
쓰고 죽는 대신 지금 말합니다.**

살짝 재 보는 것이 요점입니다. 여기서 압축을 풀면 재는 의미가 없습니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# ``b"".join`` 때문에 푼 크기의 두 배가 잠깐 필요합니다. 거기에 파이썬이
# 오브젝트를 만들 몫을 조금 더 봅니다.
BLOAT = 2.3

# 압축을 푸는 속도. 실측 대신 넉넉하게 잡은 어림수입니다 — 몇 분인지 몇십
# 분인지만 구분되면 됩니다.
MB_PER_SEC = 45.0

# 이 크기 이상의 덩이는 메모리 대신 **임시 파일로 풀어** 읽습니다(``lowmem.py``).
# 작은 덩이는 예전 방식 그대로 — 잘 되는 길은 건드리지 않습니다.
LOWMEM_FROM = 256 * 1024 * 1024

# 디스크로 풀어 읽을 때 잠깐이라도 있어야 하는 메모리. **어림수입니다** —
# 실제 게임으로 재서 고쳐야 합니다.
LOWMEM_RAM = 1024 ** 3


@dataclass
class Weight:
    """덩이 하나의 무게. ``uncompressed`` 가 0 이면 못 쟀다는 뜻입니다."""

    path: Path
    compressed: int = 0
    uncompressed: int = 0
    blocks: int = 0
    ram: int = 0
    bits: int = 64
    disk_free: int = 0
    why_unknown: str = ""

    @property
    def is_bundle(self) -> bool:
        return self.uncompressed > 0

    @property
    def low_memory(self) -> bool:
        """임시 파일로 풀어 읽을 덩이인가. 32비트는 큰 파일을 못 다룹니다."""
        return (self.is_bundle and self.bits == 64
                and self.uncompressed >= LOWMEM_FROM)

    @property
    def needs(self) -> int:
        """UnityPy 가 덩이를 **통째로** 올릴 때 잠깐이라도 있어야 하는 메모리.

        임시 파일로 푸는 길(``lowmem``)은 이 값을 쓰지 않습니다 — 그 길로 여는
        쪽만 ``blocking(low_memory=True)`` 로 따로 묻습니다. 폰트·되박기처럼
        아직 통째로 올리는 길은 이 값이 그대로 맞습니다.
        """
        return int(self.uncompressed * BLOAT)

    def _need(self, low_memory: bool) -> int:
        return LOWMEM_RAM if low_memory else self.needs

    def disk_short(self) -> str:
        """디스크로 풀 때 **빈 공간이 모자라면** 그 말. 넉넉하면 빈 문자열."""
        if not self.low_memory or not self.disk_free:
            return ""
        if self.disk_free < int(self.uncompressed * 1.05):
            return (f"임시로 {_gb(self.uncompressed)} 를 디스크에 풀어야 하는데 "
                    f"빈 공간이 {_gb(self.disk_free)} 뿐입니다. "
                    "임시 폴더가 있는 드라이브를 비워 주세요.")
        return ""

    @property
    def seconds(self) -> float:
        return self.uncompressed / (MB_PER_SEC * 1024 * 1024)

    # -- 사람에게 하는 말 ------------------------------------------------
    def headline(self) -> str:
        if not self.is_bundle:
            return ""
        return (f"{_gb(self.compressed)} 짜리 덩이 하나입니다 "
                f"(풀면 {_gb(self.uncompressed)}, 조각 {self.blocks:,}개). "
                f"여는 데만 {_mins(self.seconds)} 걸립니다.")

    def lowmem_note(self) -> str:
        """임시 파일로 풀어 읽을 때 사용자에게 미리 하는 말."""
        if not self.low_memory:
            return ""
        return ("메모리에 통째로 올리지 않고 임시 파일로 풀어 읽습니다 "
                f"(디스크 {_gb(self.uncompressed)} 를 잠깐 씁니다).")

    def blocking(self, low_memory: bool = False) -> str:
        """**열면 반드시 죽는** 경우만. 빠듯한 정도는 여기 안 들어옵니다.

        ``trouble()`` 은 "다른 프로그램을 닫아 두세요" 같은 잔소리까지
        포함합니다. 그걸 이유로 건너뛰면 될 일도 안 하게 됩니다.

        ``low_memory=True`` 는 **임시 파일로 푸는 길로 여는 쪽만** 씁니다
        (메모리 대신 디스크 빈 공간을 봅니다). 기본값은 예전 그대로입니다.
        """
        if not self.is_bundle:
            return ""
        if self.bits == 32:
            return ("32비트 파이썬은 2GB 를 넘겨 쓸 수 없어서 이 파일은 "
                    "열다가 반드시 죽습니다. 64비트 파이썬을 깔면 됩니다.")
        low = low_memory and self.low_memory
        if low and self.disk_short():
            return self.disk_short()
        need = self._need(low)
        if self.ram and need > self.ram:
            return (f"푸는 동안 잠깐 {_gb(need)} 가 필요한데 이 컴퓨터 "
                    f"메모리는 {_gb(self.ram)} 뿐입니다.")
        return ""

    def trouble(self, low_memory: bool = False) -> str:
        """못 할 것 같으면 **왜인지** 한 마디. 되겠으면 빈 문자열."""
        if not self.is_bundle:
            return ""
        if self.bits == 32:
            return ("32비트 파이썬은 2GB 를 넘겨 쓸 수 없어서 이 파일은 "
                    "열다가 반드시 죽습니다. 64비트 파이썬을 깔면 됩니다.")
        low = low_memory and self.low_memory
        if low and self.disk_short():
            return self.disk_short()
        need = self._need(low)
        if self.ram and need > self.ram:
            return (f"푸는 동안 잠깐 {_gb(need)} 가 필요한데 이 컴퓨터 "
                    f"메모리는 {_gb(self.ram)} 입니다. 열다가 죽거나, "
                    "디스크를 메모리 대신 쓰느라 몇 시간씩 걸립니다.")
        if self.ram and need > self.ram * 0.7:
            return (f"푸는 동안 잠깐 {_gb(need)} 가 필요합니다 "
                    f"(이 컴퓨터 {_gb(self.ram)}). 다른 프로그램을 닫아 두세요.")
        return ""


def _gb(n: int) -> str:
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f}GB"
    return f"{n / 1024 ** 2:,.0f}MB"


def _mins(sec: float) -> str:
    if sec < 90:
        return "1분 남짓"
    if sec < 3600:
        return f"{round(sec / 60)}분쯤"
    return f"{sec / 3600:.1f}시간쯤"


def _ram() -> int:
    try:
        import psutil
        return int(psutil.virtual_memory().total)
    except Exception:                                   # noqa: BLE001
        pass
    try:                                                # 리눅스
        import os
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except Exception:                                   # noqa: BLE001
        return 0


def _disk_free() -> int:
    """임시 파일이 놓일 드라이브의 빈 공간. 못 재면 0 (= 검사 안 함)."""
    try:
        import shutil
        import tempfile
        return int(shutil.disk_usage(tempfile.gettempdir()).free)
    except Exception:                                   # noqa: BLE001
        return 0


def _bits() -> int:
    import sys
    try:
        return 32 if sys.maxsize <= 2 ** 32 else 64
    except Exception:                                   # noqa: BLE001
        return 64


def weigh(path: Path) -> Weight:
    """덩이의 머리와 블록 목록만 읽어 무게를 잽니다. **압축은 안 풉니다.**

    덩이가 아니거나 못 읽으면 ``uncompressed`` 가 0 인 채로 돌려줍니다 —
    그 경우 부르는 쪽은 그냥 예전처럼 열면 됩니다.
    """
    path = Path(path)
    w = Weight(path=path, ram=_ram(), bits=_bits(), disk_free=_disk_free())
    try:
        w.compressed = path.stat().st_size
    except OSError:
        return w

    try:
        w.uncompressed, w.blocks = _read_block_info(path)
    except Exception as exc:                            # noqa: BLE001
        w.why_unknown = f"{type(exc).__name__}: {exc}"
        w.uncompressed = 0
    return w


def _read_block_info(path: Path) -> tuple[int, int]:
    """``(푼 크기, 조각 수)``. UnityPy 의 읽기를 **블록 목록까지만** 흉내냅니다.

    블록 목록 자체는 작습니다(보통 몇십 KB). 여기까지만 읽으면 2.4GB 짜리도
    한순간에 잽니다.
    """
    from UnityPy.enums import ArchiveFlags, ArchiveFlagsOld
    from UnityPy.streams import EndianBinaryReader

    with open(path, "rb") as fh:
        head = fh.read(1 << 16)
        if not head.startswith(b"UnityFS"):
            return 0, 0                     # 덩이가 아닙니다 (.assets 등)
        reader = EndianBinaryReader(head)
        signature = reader.read_string_to_null()
        version = reader.read_u_int()
        reader.read_string_to_null()        # unity version
        engine = reader.read_string_to_null()
        reader.read_long()                  # 전체 크기
        comp = reader.read_u_int()
        uncomp = reader.read_u_int()
        flags_value = reader.read_u_int()
        if signature != "UnityFS":
            reader.read_byte()

        ver = _version(engine)
        old = (ver < (2020,)
               or (ver[:1] == (2020,) and ver < (2020, 3, 34))
               or (ver[:1] == (2021,) and ver < (2021, 3, 2))
               or (ver[:1] == (2022,) and ver < (2022, 1, 1)))
        flags = (ArchiveFlagsOld if old else ArchiveFlags)(flags_value)
        if flags & flags.UsesAssetBundleEncryption:
            return 0, 0                     # 잠긴 덩이. 우리가 못 엽니다.
        if version >= 7 or (ver[:1] == (2019,) and ver >= (2019, 4, 15)):
            reader.align_stream(16)

        start = reader.Position
        if flags & ArchiveFlags.BlocksInfoAtTheEnd:
            fh.seek(-comp, 2)
            raw = fh.read(comp)
        else:
            if start + comp > len(head):
                fh.seek(start)
                raw = fh.read(comp)
            else:
                raw = head[start:start + comp]

        info = _unpack(raw, uncomp, flags)

    blocks = EndianBinaryReader(info)
    blocks.read_bytes(16)                   # hash
    n = blocks.read_int()
    total = 0
    for _ in range(n):
        total += blocks.read_u_int()        # uncompressedSize
        blocks.read_u_int()                 # compressedSize
        blocks.read_u_short()               # flags
    return total, n


def _unpack(raw: bytes, size: int, flags) -> bytes:
    """블록 목록만 풉니다. UnityPy 의 표를 그대로 씁니다 — 우리가 압축
    방식을 따로 알아야 할 이유가 없습니다."""
    from UnityPy.enums import ArchiveFlags, CompressionFlags
    from UnityPy.helpers import CompressionHelper

    how = CompressionFlags(flags & ArchiveFlags.CompressionTypeMask)
    fn = CompressionHelper.DECOMPRESSION_MAP.get(how)
    if fn is None:
        raise ValueError(f"모르는 압축 방식입니다: {how}")
    return fn(raw, size)


def _version(text: str) -> tuple:
    out = []
    num = ""
    for ch in text:
        if ch.isdigit():
            num += ch
        elif num:
            out.append(int(num))
            num = ""
            if len(out) >= 3:
                break
    if num:
        out.append(int(num))
    return tuple(out) or (0,)
