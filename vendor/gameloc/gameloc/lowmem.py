# SPDX-License-Identifier: GPL-3.0-or-later
"""덩이(``data.unity3d``)를 **메모리에 통째로 올리지 않고** 엽니다.

## 왜 이게 필요한가

UnityPy 는 덩이를 열 때 압축을 푼 조각을 전부 ``b"".join`` 으로 이어 붙이고
(푼 크기의 두 배), 그다음 파일마다 ``reader.read(size)`` 로 한 번 더 복사합니다.
2.4GB 짜리 덩이가 ``stats.died: 1`` 로 죽던 까닭입니다 (``bundle.py`` 참고).

## 무엇을 하나

UnityPy 의 읽기(``read_fs``)는 **그대로 둡니다.** 두 곳만 바꿉니다.

1. ``decompress_data`` — 푼 블록을 **임시 파일에 바로 쓰고** 빈 값을 돌려줍니다.
   그러면 ``b"".join`` 이 빈 값들을 이어 붙이니 메모리에는 블록 하나만 있습니다.
2. ``read_files`` — 파일(노드)을 **복사하지 않고** 임시 파일의 ``mmap`` 뷰로
   넘깁니다. 실제로 읽는 오브젝트의 바이트만 운영체제가 디스크에서 올립니다.

UnityPy 의 안쪽을 새로 짜지 않으니, UnityPy 가 바뀌어 어긋나면 이 파일의
시험(``tests/test_lowmem.py``)이 먼저 깨집니다 — 그게 이 시험의 값입니다.

## 쓰는 법

    with lowmem.enabled():
        env = UnityPy.load(str(path), path=str(path.parent))

**읽기 전용**입니다. 이 경로로 연 덩이를 다시 저장(``save``)하지는 못합니다.
"""

from __future__ import annotations

import contextlib
import mmap
import tempfile


class _Slices:
    """``BundleFile.read_files`` 가 노드를 자를 때 쓰는 최소한의 읽기 흉내.

    ``read_files`` 는 ``reader.Position`` 을 정하고 ``reader.read(size)`` 로
    노드 바이트를 받습니다. 여기서는 **복사 없이** 뷰를 잘라 줍니다.
    """

    def __init__(self, view: memoryview, base_offset: int):
        self.view = view
        self.BaseOffset = base_offset
        self.Position = 0

    def read(self, size: int = -1) -> memoryview:
        end = len(self.view) if size is None or size < 0 else self.Position + size
        return self.view[self.Position:end]


def _make_class(base, tmp_dir: str | None):
    class LowMemBundle(base):
        _spool = None
        _map = None

        def decompress_data(self, compressed_data, uncompressed_size, flags,
                            index=None):
            # UnityPy 는 블록 목록(작음)을 풀 때는 3개, 데이터 블록을 풀 때는
            # 블록 번호까지 4개를 넘깁니다. 번호가 있는 쪽만 디스크로 보냅니다.
            data = super().decompress_data(
                compressed_data, uncompressed_size, flags,
                0 if index is None else index)
            if index is None:
                return data
            if self._spool is None:
                self._spool = tempfile.TemporaryFile(dir=tmp_dir)
            self._spool.write(data)
            return b""

        def read_files(self, reader, files):
            spool = self._spool
            if spool is None:
                return super().read_files(reader, files)
            spool.flush()
            if spool.tell() == 0:
                return super().read_files(reader, files)
            self._map = mmap.mmap(spool.fileno(), 0, access=mmap.ACCESS_READ)
            return super().read_files(
                _Slices(memoryview(self._map), reader.BaseOffset), files)

    LowMemBundle.__name__ = base.__name__
    return LowMemBundle


@contextlib.contextmanager
def enabled(tmp_dir: str | None = None):
    """이 안에서 ``UnityPy.load`` 로 여는 덩이는 디스크로 풀어 읽습니다.

    ``tmp_dir`` 은 임시 파일을 둘 폴더입니다(기본은 시스템 임시 폴더). 푼 크기만큼
    빈 공간이 필요합니다 — 미리 재는 일은 ``bundle.weigh`` 가 합니다.
    """
    import UnityPy.files as files

    original = files.BundleFile
    files.BundleFile = _make_class(original, tmp_dir)
    try:
        yield
    finally:
        files.BundleFile = original
