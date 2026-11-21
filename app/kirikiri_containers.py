"""Read-only ISO9660/Joliet and XP3 access; no game code is executed."""
from __future__ import annotations

import os
import re
import struct
import threading
import zlib
from dataclasses import dataclass
from pathlib import Path


class ContainerError(ValueError):
    pass


def _path(value):
    value = str(value).replace('\\', '/')
    parts = value.split('/')
    if not value or value.startswith('/') or any(p in ('', '.', '..') for p in parts) or ':' in value or '\0' in value:
        raise ContainerError('Unsafe container path')
    return '/'.join(parts)


def _range(offset, size, total):
    if not isinstance(offset, int) or not isinstance(size, int) or offset < 0 or size < 0 or offset > total or size > total - offset:
        raise ContainerError('Range outside file')


@dataclass(frozen=True)
class Entry:
    path: str
    size: int
    offset: int = 0


class _Source:
    def read(self, path):
        entry = self.entries[_path(path)]
        return self.read_range(path, 0, entry.size)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class DirectorySource(_Source):
    def __init__(self, path):
        self.path = Path(path).resolve(strict=True)
        self.entries = {}
        for root, dirs, files in os.walk(self.path, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(root) / d).is_symlink()]
            for name in files:
                p = Path(root) / name
                if p.is_symlink():
                    continue
                rel = _path(p.relative_to(self.path).as_posix())
                self.entries[rel] = Entry(rel, p.stat().st_size)

    def read_range(self, path, offset, size):
        path = _path(path)
        entry = self.entries[path]
        _range(offset, size, entry.size)
        target = (self.path / path).resolve(strict=True)
        if not target.is_relative_to(self.path):
            raise ContainerError('Path escaped source')
        with target.open('rb') as f:
            f.seek(offset)
            data = f.read(size)
        if len(data) != size:
            raise ContainerError('Truncated source file')
        return data

    def close(self):
        pass


class ISOSource(_Source):
    BLOCK = 2048

    def __init__(self, path):
        self.path = Path(path)
        self._file = self.path.open('rb')
        self._lock = threading.RLock()
        self._size = self.path.stat().st_size
        self.entries = {}
        try:
            primary = joliet = None
            for sector in range(16, min(self._size // self.BLOCK, 256)):
                d = self._raw(sector * self.BLOCK, self.BLOCK)
                if d[1:6] != b'CD001':
                    continue
                if d[0] == 1:
                    primary = d
                elif d[0] == 2 and d[88:91] in (b'%/@', b'%/C', b'%/E'):
                    joliet = d
                elif d[0] == 255:
                    break
            descriptor = joliet or primary
            if descriptor is None:
                raise ContainerError('Unsupported ISO: ISO9660/Joliet volume not found (UDF-only is unsupported)')
            self.joliet = joliet is not None
            self._walk(descriptor[156:190], '', set())
        except Exception:
            self.close()
            raise

    def _raw(self, offset, size):
        _range(offset, size, self._size)
        with self._lock:
            self._file.seek(offset)
            data = self._file.read(size)
        if len(data) != size:
            raise ContainerError('Truncated ISO')
        return data

    def _walk(self, record, prefix, seen):
        if len(record) < 34:
            raise ContainerError('Invalid ISO directory record')
        extent, size = struct.unpack_from('<I', record, 2)[0], struct.unpack_from('<I', record, 10)[0]
        start = extent * self.BLOCK
        _range(start, size, self._size)
        key = (start, size)
        if key in seen:
            raise ContainerError('ISO directory cycle')
        seen = seen | {key}
        data = self._raw(start, size)
        pos = 0
        while pos < size:
            length = data[pos]
            if not length:
                pos = (pos // self.BLOCK + 1) * self.BLOCK
                continue
            rec = data[pos:pos + length]
            if length < 34 or len(rec) != length or 33 + rec[32] > length:
                raise ContainerError('Invalid ISO directory record')
            pos += length
            raw = rec[33:33 + rec[32]]
            if raw in (b'\0', b'\1'):
                continue
            try:
                name = raw.decode('utf-16-be' if self.joliet else 'ascii')
            except UnicodeError as e:
                raise ContainerError('Invalid ISO filename') from e
            name = re.sub(r';\d+$', '', name).rstrip('.')
            path = _path(prefix + name)
            if rec[25] & 0x80:
                raise ContainerError('Multi-extent ISO files are unsupported')
            if rec[25] & 2:
                self._walk(rec, path + '/', seen)
            else:
                off = struct.unpack_from('<I', rec, 2)[0] * self.BLOCK
                n = struct.unpack_from('<I', rec, 10)[0]
                _range(off, n, self._size)
                if path in self.entries:
                    raise ContainerError('Duplicate ISO path')
                self.entries[path] = Entry(path, n, off)

    def read_range(self, path, offset, size):
        entry = self.entries[_path(path)]
        _range(offset, size, entry.size)
        return self._raw(entry.offset + offset, size)

    def close(self):
        self._file.close()


def open_source(path):
    path = Path(path)
    if path.is_file():
        return ISOSource(path)
    if not path.is_dir():
        raise FileNotFoundError(path)
    isos = [p for p in path.iterdir() if p.is_file() and p.suffix.lower() == '.iso']
    if len(isos) > 1:
        raise ContainerError('Multiple ISO files: select one explicitly')
    if len(isos) == 1:
        return ISOSource(isos[0])
    return DirectorySource(path)


def discover_game_roots(source):
    roots = {}
    for path in source.entries:
        folder, _, name = path.rpartition('/')
        low = name.lower()
        flags = roots.setdefault(folder, set())
        if low.endswith('.xp3'):
            flags.add('xp3')
        if low.endswith('.exe') or low in ('krkr.dll', 'kirikiri.dll'):
            flags.add('runtime')
    return sorted(root for root, flags in roots.items() if flags == {'xp3', 'runtime'})


XP3_MAGIC = b'XP3\r\n \n\x1a\x8bg\x01'


@dataclass(frozen=True)
class XP3Entry:
    path: str
    size: int
    segments: tuple
    adler32: int | None
    protected: bool


class XP3Archive(_Source):
    def __init__(self, source, path):
        self.source, self.path = source, _path(path)
        self._size = source.entries[self.path].size
        self.entries = {}
        self.notices = []
        if self._read(0, 11) != XP3_MAGIC:
            raise ContainerError('Invalid XP3 signature')
        offset = struct.unpack('<Q', self._read(11, 8))[0]
        seen = set()
        while True:
            if offset in seen:
                raise ContainerError('XP3 index cycle')
            seen.add(offset)
            flag = self._read(offset, 1)[0]
            if flag & 0x7e or (flag & 7) not in (0, 1):
                raise ContainerError('Unsupported XP3 index encoding')
            packed = struct.unpack('<Q', self._read(offset + 1, 8))[0]
            cursor = offset + 9
            if flag & 1:
                unpacked = struct.unpack('<Q', self._read(cursor, 8))[0]
                cursor += 8
            else:
                unpacked = packed
            data = self._read(cursor, packed)
            if flag & 1:
                try:
                    dec = zlib.decompressobj()
                    data = dec.decompress(data, unpacked + 1)
                    if len(data) != unpacked or not dec.eof or dec.unused_data or dec.unconsumed_tail:
                        raise ContainerError('Invalid XP3 compressed index length')
                except zlib.error as e:
                    raise ContainerError('Invalid XP3 compressed index') from e
            self._parse_index(data)
            cursor += packed
            if not flag & 0x80:
                break
            offset = struct.unpack('<Q', self._read(cursor, 8))[0]
        if any(e.protected for e in self.entries.values()):
            self.notices.append('XP3 protected flag is set; content encryption cannot be determined from this flag alone.')

    def _read(self, offset, size):
        _range(offset, size, self._size)
        return self.source.read_range(self.path, offset, size)

    @staticmethod
    def _chunks(data):
        pos = 0
        while pos < len(data):
            if len(data) - pos < 12:
                raise ContainerError('Truncated XP3 chunk')
            tag, size = struct.unpack_from('<4sQ', data, pos)
            pos += 12
            _range(pos, size, len(data))
            yield tag, data[pos:pos + size]
            pos += size

    def _parse_index(self, data):
        for tag, value in self._chunks(data):
            if tag != b'File':
                continue
            chunks = {}
            for subtag, subvalue in self._chunks(value):
                if subtag in chunks:
                    raise ContainerError('Duplicate XP3 file chunk')
                chunks[subtag] = subvalue
            info, segm = chunks.get(b'info', b''), chunks.get(b'segm', b'')
            if len(info) < 22 or len(segm) % 28:
                raise ContainerError('Invalid XP3 file entry')
            flags, size, packed, nchars = struct.unpack_from('<IQQH', info)
            if 22 + nchars * 2 > len(info):
                raise ContainerError('Invalid XP3 filename')
            try:
                path = _path(info[22:22 + nchars * 2].decode('utf-16le'))
            except UnicodeError as e:
                raise ContainerError('Invalid XP3 filename') from e
            segments = []
            for pos in range(0, len(segm), 28):
                method, offset, original, compressed = struct.unpack_from('<IQQQ', segm, pos)
                if method not in (0, 1):
                    raise ContainerError('Unsupported or encrypted XP3 segment')
                _range(offset, compressed, self._size)
                if method == 0 and compressed != original:
                    raise ContainerError('Invalid XP3 segment size')
                segments.append((method, offset, original, compressed))
            adlr = chunks.get(b'adlr')
            if adlr is not None and len(adlr) != 4:
                raise ContainerError('Invalid XP3 checksum')
            self.entries[path] = XP3Entry(path, size, tuple(segments), struct.unpack('<I', adlr)[0] if adlr else None, bool(flags & 0x80000000))

    def read(self, path):
        entry = self.entries[_path(path)]
        data = self.read_range(path, 0, entry.size)
        if entry.adler32 is not None and zlib.adler32(data) != entry.adler32:
            raise ContainerError('XP3 checksum mismatch; unsupported, encrypted, or corrupt entry')
        return data

    def read_range(self, path, offset, size):
        entry = self.entries[_path(path)]
        _range(offset, size, entry.size)
        if sum(s[2] for s in entry.segments) != entry.size:
            raise ContainerError('XP3 segment size mismatch; unsupported or encrypted entry')
        out, position = bytearray(), 0
        for method, start, original, packed in entry.segments:
            lo, hi = max(offset, position), min(offset + size, position + original)
            if lo < hi:
                if method == 0:
                    out.extend(self._read(start + lo - position, hi - lo))
                else:
                    try:
                        dec = zlib.decompressobj()
                        raw = dec.decompress(self._read(start, packed), original + 1)
                        if len(raw) != original or not dec.eof or dec.unused_data or dec.unconsumed_tail:
                            raise ContainerError('Invalid XP3 compressed segment length')
                    except zlib.error as e:
                        raise ContainerError('Unsupported, encrypted, or corrupt XP3 segment') from e
                    out.extend(raw[lo - position:hi - position])
            position += original
        return bytes(out)

    def close(self):
        pass


def _chunk(tag, data):
    return tag + struct.pack('<Q', len(data)) + data


def write_xp3(path, members, compress=True):
    """Create a new patch archive exclusively; members maps safe paths to bytes."""
    prepared = [(_path(name), bytes(data)) for name, data in members.items()]
    if len({name.casefold() for name, _ in prepared}) != len(prepared):
        raise ContainerError('Duplicate XP3 member path')
    with open(path, 'xb') as f:
        try:
            f.write(XP3_MAGIC + b'\0' * 8)
            chunks = []
            for name, data in prepared:
                encoded = name.encode('utf-16le')
                if len(encoded) // 2 > 65535:
                    raise ContainerError('XP3 filename too long')
                payload = zlib.compress(data) if compress else data
                method = int(compress and len(payload) < len(data))
                if not method:
                    payload = data
                offset = f.tell()
                f.write(payload)
                info = struct.pack('<IQQH', 0, len(data), len(payload), len(encoded) // 2) + encoded
                segm = struct.pack('<IQQQ', method, offset, len(data), len(payload))
                chunks.append(_chunk(b'File', _chunk(b'info', info) + _chunk(b'segm', segm) + _chunk(b'adlr', struct.pack('<I', zlib.adler32(data)))))
            index = b''.join(chunks)
            index_offset = f.tell()
            compressed = zlib.compress(index)
            f.write(b'\x01' + struct.pack('<QQ', len(compressed), len(index)) + compressed)
            f.seek(11)
            f.write(struct.pack('<Q', index_offset))
        except Exception:
            f.close()
            Path(path).unlink(missing_ok=True)
            raise


def choose_patch_name(names):
    """Continue the contiguous standard patch chain without activating a gap."""
    existing = {str(n).replace('\\', '/').rsplit('/', 1)[-1].casefold() for n in names}
    nums = set()
    for name in existing:
        m = re.fullmatch(r'patch(\d*)\.xp3', name)
        if m:
            suffix = m.group(1)
            if suffix and (suffix.startswith('0') or int(suffix) < 2):
                continue
            nums.add(int(suffix) if suffix else 1)
    if not nums:
        return 'patch.xp3'
    highest = max(nums)
    if nums != set(range(1, highest + 1)):
        raise ContainerError('Patch archive numbering has a gap; cannot safely add a patch')
    candidate = f'patch{highest + 1}.xp3'
    if candidate.casefold() in existing:
        raise ContainerError('Patch archive already exists')
    return candidate
