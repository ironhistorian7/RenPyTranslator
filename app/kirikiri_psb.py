"""PSB v2 reader/writer. Format reference: storycraft/emote-psb-rs (MIT),
https://github.com/storycraft/emote-psb-rs. No code or game content is executed.
"""
from dataclasses import dataclass, field
import struct


class PsbError(ValueError):
    pass


class PsbInt(int):
    def __new__(cls, value, width):
        obj = int.__new__(cls, value)
        obj.width = width
        return obj


class PsbFloat(float):
    def __new__(cls, value, kind):
        obj = float.__new__(cls, value)
        obj.kind = kind
        return obj


@dataclass(frozen=True)
class PsbResource:
    index: int
    width: int = 1


@dataclass
class PsbDocument:
    root: object
    names: list = field(default_factory=list)
    names_blob: bytes = b''
    resources: list = field(default_factory=list)
    header_length: int = 0


def _uint(data, pos, n):
    if n < 0 or n > 8 or pos < 0 or pos + n > len(data):
        raise PsbError('truncated PSB integer')
    return int.from_bytes(data[pos:pos+n], 'little')


def _array(data, pos):
    if pos >= len(data) or not 0x0d <= data[pos] <= 0x14:
        raise PsbError('invalid PSB array')
    n = data[pos] - 0x0c
    count = _uint(data, pos + 1, n)
    pos += 1 + n
    if pos >= len(data) or not 0x0d <= data[pos] <= 0x14:
        raise PsbError('invalid PSB array width')
    width = data[pos] - 0x0c
    pos += 1
    if count > (len(data) - pos) // width:
        raise PsbError('truncated PSB array')
    return [_uint(data, pos + i * width, width) for i in range(count)], pos + count * width


def _pack_array(values):
    def width(n): return max(1, (n.bit_length() + 7) // 8)
    n = width(len(values)); w = width(max(values, default=0))
    if n > 8 or w > 8: raise PsbError('array value too large')
    return bytes([12+n]) + len(values).to_bytes(n, 'little') + bytes([12+w]) + b''.join(v.to_bytes(w, 'little') for v in values)


def loads(data):
    data = bytes(data)
    if data[:4] in (b'mdf\x00', b'MDF\x00'):
        raise PsbError('MDF compression is unsupported')
    if len(data) < 40 or data[:4] != b'PSB\x00': raise PsbError('invalid PSB header')
    version, flags, hlen, no, so, sd, ro, rl, rd, entry = struct.unpack_from('<HH8I', data, 4)
    if version != 2 or flags != 0: raise PsbError('only unencrypted PSB version 2 is supported')
    if any(x < 40 or x > len(data) for x in (no, so, sd, ro, rl, rd, entry)):
        raise PsbError('invalid PSB offset')
    offsets, p = _array(data, no); tree, p = _array(data, p); indexes, p = _array(data, p)
    names_blob = data[no:p]
    names = []
    try:
        for index in indexes:
            node = tree[index]; chars = bytearray(); seen = set()
            while node:
                if node in seen: raise PsbError('cyclic name tree')
                seen.add(node); parent = tree[node]; char = node - offsets[parent]
                if not 0 <= char <= 255: raise PsbError('invalid name tree')
                chars.append(char); node = parent
            names.append(bytes(reversed(chars)).decode('utf-8'))
        string_offsets, _ = _array(data, so)
        strings = []
        for offset in string_offsets:
            start = sd + offset
            if start >= len(data): raise PsbError('invalid string offset')
            end = data.find(b'\0', start)
            if end < 0: raise PsbError('unterminated string')
            strings.append(data[start:end].decode('utf-8'))
        res_offsets, _ = _array(data, ro); lengths, _ = _array(data, rl)
        if len(res_offsets) != len(lengths): raise PsbError('resource table length mismatch')
        resources = []
        for offset, size in zip(res_offsets, lengths):
            if rd + offset + size > len(data): raise PsbError('invalid resource bounds')
            resources.append(data[rd+offset:rd+offset+size])
    except (IndexError, UnicodeDecodeError) as exc:
        raise PsbError('invalid PSB table') from exc

    active = set()
    def read(pos, depth=0):
        if depth > 256 or pos in active or pos >= len(data): raise PsbError('invalid PSB value nesting')
        active.add(pos)
        original_pos = pos
        try:
            tag = data[pos]; pos += 1
            if tag == 1: return None
            if tag in (2, 3): return tag == 3
            if 4 <= tag <= 12:
                n = tag - 4; raw = _uint(data, pos, n)
                if n and raw & (1 << (n*8-1)): raw -= 1 << (n*8)
                return PsbInt(raw, n)
            if 0x15 <= tag <= 0x18:
                idx = _uint(data, pos, tag-0x14)
                if idx >= len(strings): raise PsbError('invalid string reference')
                return strings[idx]
            if 0x19 <= tag <= 0x1c:
                idx = _uint(data, pos, tag-0x18)
                if idx >= len(resources): raise PsbError('invalid resource reference')
                return PsbResource(idx, tag-0x18)
            if tag == 0x1d: return PsbFloat(0.0, tag)
            if tag in (0x1e, 0x1f):
                n = 4 if tag == 0x1e else 8
                if pos+n > len(data): raise PsbError('truncated float')
                return PsbFloat(struct.unpack_from('<f' if n == 4 else '<d', data, pos)[0], tag)
            if tag == 0x20:
                off, start = _array(data, pos)
                return [read(start + x, depth+1) for x in off]
            if tag == 0x21:
                keys, p = _array(data, pos); off, start = _array(data, p)
                if len(keys) != len(off) or any(k >= len(names) for k in keys): raise PsbError('invalid object keys')
                if len({names[k] for k in keys}) != len(keys): raise PsbError('duplicate object keys')
                return {names[k]: read(start+x, depth+1) for k,x in zip(keys, off)}
            raise PsbError('unsupported PSB value type 0x%02x' % tag)
        finally:
            active.remove(original_pos)
    return PsbDocument(read(entry), names, names_blob, resources, hlen)


def dumps(doc):
    if not doc.names_blob: raise PsbError('original name table is required')
    name_ids = {name: i for i, name in enumerate(doc.names)}
    strings = []; string_ids = {}
    def ref(tag, value, width=1):
        width = max(width, max(1, (value.bit_length()+7)//8))
        if width > 4: raise PsbError('reference too large')
        return bytes([tag+width]) + value.to_bytes(width, 'little')
    def encode(value, depth=0):
        if depth > 256: raise PsbError('value nesting too deep')
        if value is None: return b'\x01'
        if isinstance(value, bool): return bytes([3 if value else 2])
        if isinstance(value, int):
            n = getattr(value, 'width', None)
            if n is None:
                n = 0 if value == 0 else next((i for i in range(1,9) if -(1 << (i*8-1)) <= value < (1 << (i*8-1))), 9)
            if n > 8 or (n == 0 and value != 0): raise PsbError('invalid integer width')
            try: return bytes([4+n]) + int(value).to_bytes(n, 'little', signed=True)
            except OverflowError as exc: raise PsbError('integer does not fit original width') from exc
        if isinstance(value, float):
            kind = getattr(value, 'kind', 0x1f)
            if kind == 0x1d and value == 0: return b'\x1d'
            if kind == 0x1e: return b'\x1e' + struct.pack('<f', value)
            return b'\x1f' + struct.pack('<d', value)
        if isinstance(value, str):
            if '\0' in value: raise PsbError('PSB strings cannot contain NUL')
            if value not in string_ids:
                string_ids[value] = len(strings); strings.append(value)
            return ref(0x14, string_ids[value])
        if isinstance(value, PsbResource):
            if not 0 <= value.index < len(doc.resources): raise PsbError('invalid resource reference')
            return ref(0x18, value.index, value.width)
        if isinstance(value, (list, dict)):
            keys = []
            if isinstance(value, dict):
                try: keys = [name_ids[k] for k in value]
                except KeyError as exc: raise PsbError('new PSB object keys are unsupported') from exc
                values = value.values()
            else: values = value
            chunks = [encode(v, depth+1) for v in values]
            off = []; size = 0
            for chunk in chunks: off.append(size); size += len(chunk)
            return bytes([0x21 if isinstance(value, dict) else 0x20]) + (_pack_array(keys) if isinstance(value, dict) else b'') + _pack_array(off) + b''.join(chunks)
        raise PsbError('unsupported Python value type')
    root = encode(doc.root)
    string_data = bytearray(); string_offsets = []
    for value in strings:
        string_offsets.append(len(string_data)); string_data.extend(value.encode('utf-8') + b'\0')
    res_data = bytearray(); res_offsets = []; res_lengths = []
    for resource in doc.resources:
        res_offsets.append(len(res_data)); res_lengths.append(len(resource)); res_data.extend(resource)
    chunks = [doc.names_blob, root, _pack_array(string_offsets), bytes(string_data), _pack_array(res_offsets), _pack_array(res_lengths), bytes(res_data)]
    positions = []; pos = 40
    for chunk in chunks: positions.append(pos); pos += len(chunk)
    no, entry, so, sd, ro, rl, rd = positions
    return struct.pack('<4sHH8I', b'PSB\0', 2, 0, doc.header_length, no, so, sd, ro, rl, rd, entry) + b''.join(chunks)
