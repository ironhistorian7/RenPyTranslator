# SPDX-License-Identifier: GPL-3.0-or-later
"""울프RPG 에디터의 ``Data.wolf`` 아카이브를 읽습니다(3.31 ~ 3.5x, 프로판 아닌 것).

울프는 DxLib 의 아카이브(DXArchive v8)를 고쳐 씁니다. 3.31 부터는 그 위에 자체 암호를
한 겹 더 얹었습니다. 푸는 순서는 이렇습니다.

1. 머리(64바이트)의 주소 네 개를 msvc ``rand()`` 로 되돌립니다(``_crypt_addresses``).
2. 파일 전체에 위치 기반 XOR(첫 번째 특수 키, 소금 "958")을 벗깁니다.
3. 본문 앞부분과 목록 표에 걸린 AES-128-CTR(키 확장이 표준과 조금 다름)을 벗깁니다.
4. 두 번째 특수 키로 목록 표와 각 파일을 위치 기반 XOR 로 벗깁니다.
5. 목록 표와 파일은 허프만(앞뒤 몇 KB) + LZ 로 압축돼 있어 풉니다.

모든 층이 "파일 안의 위치"로 키가 정해지므로, 필요한 파일의 구간만 읽어 풀 수 있습니다
— 200MB 아카이브를 통째로 풀 필요가 없습니다.

알고리즘은 Sinflower 의 UberWolf(MIT, https://github.com/Sinflower/UberWolf) 와 DxLib
(山田 巧) 의 코드를 보고 파이썬으로 새로 옮긴 것입니다. 고지는 THIRD-PARTY-NOTICES.txt.

## 조심할 점

핵심 데이터 파일(``Game.dat`` 등) 맨 앞에는 울프 에디터가 넣은 "암호화된 파일에서
데이터를 꺼내는 것은 규약 위반" 이라는 62바이트 표식이 있습니다. 풀린 폴더로 게임을
돌리려면 이 표식을 떼야 합니다(``strip_guard``). 산 게임을 자기가 번역해 하는 용도로만
쓰도록 화면에서 알립니다.
"""

from __future__ import annotations

import dataclasses
import struct
from pathlib import Path

DXA_HEAD = b"DX"
FLAG_NO_KEY = 0x1
FLAG_NO_HEAD_PRESS = 0x2
NOT_PRESSED = 0xFFFFFFFFFFFFFFFF
MIN_COMPRESS = 4

# 이 표식으로 시작하는 파일은 풀린 폴더에서 게임이 안 읽습니다.
GUARD = b"Extracting data from encrypted files violates the guidelines.\x00"
GUARD_FILES = ("game.dat", "cdatabase.dat", "database.dat", "commonevent.dat")


class WolfArcError(Exception):
    pass


# 3.00 · 3.14 은 울프 자체 암호(3.31~)가 아니라 **DxLib 의 옛 방식** 그대로입니다: 고정 키 문자열
# 하나로 7바이트 키를 만들고, 파일마다 "키 문자열 + 이름 + 상위 폴더 이름들" 로 만든 7바이트
# 키를 반복 XOR 합니다. 키 문자열은 울프 에디터에 박혀 있는 값(UberWolf 의 표).
OLD_KEYS = {
    0x12C: bytes([0x0F, 0x53, 0xE1, 0x3E, 0x8E, 0xB5, 0x41, 0x91, 0x52, 0x16, 0x55, 0xAE, 0x34,
                  0xC9, 0x8F, 0x79, 0x59, 0x2F, 0x59, 0x6B, 0x95, 0x19, 0x9B, 0x1B, 0x35, 0x9A,
                  0x2F, 0xDE, 0xC9, 0x7C, 0x12, 0x96, 0xC3, 0x14, 0xB5, 0x0F, 0x53, 0xE1, 0x3E,
                  0x8E]),
    0x13A: bytes([0x31, 0xF9, 0x01, 0x36, 0xA3, 0xE3, 0x8D, 0x3C, 0x7B, 0xC3, 0x7D, 0x25, 0xAD,
                  0x63, 0x28, 0x19, 0x1B, 0xF7, 0x8E, 0x6C, 0xC4, 0xE5, 0xE2, 0x76, 0x82, 0xEA,
                  0x4F, 0xED, 0x61, 0xDA, 0xE0, 0x44, 0x5B, 0xB6, 0x46, 0x3B, 0x06, 0xD5, 0xCE,
                  0xB6, 0x78, 0x58, 0xD0, 0x7C, 0x82]),
}
# 암호 방식 표시가 0 인 DXA v8 묶음(울프 2.2xx 등). 버전 표시가 없어 키 문자열을 후보로 하나씩
# 시험합니다 — 목록을 풀어 보고 이름이 말이 되면 맞는 키입니다. (UberWolf 의 표)
ZERO_KEYS = [
    b"WLFRPrO!p(;s5((8P@((UFWlu$#5(=",      # 울프 2.225 이후
    b"nGui9('&1=@3#a",                      # One Way Heroics
    b"Ph=X3^]o2A(,1=@3#a",                  # One Way Heroics Plus
    b"DXLIBARC",                            # DxLib 기본값
]
_DEFAULT_KEY_STRING = b"DXLIBARC"


def key_create(src: bytes) -> bytes:
    """DxLib ``KeyCreate``: 짝수 자리 글자·홀수 자리 글자의 CRC32 로 7바이트."""
    import zlib
    if len(src) < 4:
        src = src + _DEFAULT_KEY_STRING
    c0 = zlib.crc32(src[0::2]) & 0xFFFFFFFF
    c1 = zlib.crc32(src[1::2]) & 0xFFFFFFFF
    return struct.pack("<I", c0) + struct.pack("<I", c1)[:3]


class UnsupportedCrypt(WolfArcError):
    """아직 못 푸는 암호 방식(프로판·ChaCha·2.x·3.14 이전)."""


# ------------------------------------------------------------------ 난수

class _MsvcRand:
    """MSVC 의 ``srand``/``rand``."""

    def __init__(self, seed: int = 1) -> None:
        self.s = seed & 0xFFFFFFFF

    def rand(self) -> int:
        self.s = (self.s * 214013 + 2531011) & 0xFFFFFFFF
        return (self.s >> 16) & 0x7FFF


class _XorShift32:
    def __init__(self, seed: int) -> None:
        self.s = seed & 0xFFFFFFFF

    def next(self) -> int:
        s = self.s
        s ^= (s << 11) & 0xFFFFFFFF
        s ^= s >> 19
        s ^= (s << 7) & 0xFFFFFFFF
        self.s = s
        return s


def _rotr8(x: int, n: int) -> int:
    x &= 0xFF
    return ((x >> n) | (x << (8 - n))) & 0xFF


def is_v35(cv: int) -> bool:
    return (0x15E <= cv < 0x3E8) or cv >= 0x3FC


def is_new_crypt(cv: int) -> bool:
    return (331 <= cv < 1000) or cv >= 1010


# ------------------------------------------------------------------ 울프 특수 키

def _calc_salt(s: bytes) -> bytes:
    n = len(s)
    return bytes(((i // n) + s[i % n]) & 0xFF for i in range(128))


def _init_wolf_crypt(cv: int, pw: bytes, *, other: bool, key_string: bytes = b"") -> bytes:
    """768바이트 특수 키. ``other`` 는 파일 전체에 거는 첫 번째 키 쪽."""
    s0, s1, s2 = pw[2], pw[5], pw[12]
    s3 = 0
    if not other:
        for i in range(pw[11] // 3):
            s3 = (i ^ _rotr8(s3 ^ pw[i % 15], 3)) & 0xFF
    else:
        for i in range(pw[8] // 4):
            s3 = (i ^ _rotr8(s3 ^ pw[i % 15], 2)) & 0xFF

    r = _MsvcRand(s0 * s1 + s2 + s3)
    fac = [0, 0, 0]
    fac[s3 % 3] = r.rand() % 256
    if not other and is_v35(cv):
        fac[1] = r.rand() % 0xFB

    key = bytearray(768)
    for i in range(256):
        rn = r.rand() & 0xFFFF
        key[i] = fac[0] ^ (r.rand() & 0xFF)
        key[i + 256] = (fac[1] ^ (rn >> 8)) & 0xFF
        key[i + 512] = (fac[2] ^ rn) & 0xFF

    if other:
        salt = _calc_salt(b"958" if cv == 0x15E else key_string)
        mod = 7
        if is_v35(cv):
            s3 = (s3 + 0x22) & 0xFF
            mod = 16
        for i in range(3):
            t = s3
            for j in range(256):
                skip = False
                cs = salt[j & 0x7F]
                cs2 = salt[(j + i) % 0x80]
                ck = key[i * 256 + j]
                sxk = cs ^ ck
                rnd = ((cs2 | (cs << 8)) % mod) & 0xFF
                nk = sxk
                if rnd == 1:
                    if cs2 % 0xB == 0:
                        nk = ck
                elif rnd == 2:
                    if cs % 0x1D == 0:
                        nk = ~sxk & 0xFF
                elif rnd == 3:
                    if (rnd + j) % 0x25 == 0:
                        nk = cs2 ^ sxk
                elif rnd == 4:
                    if (cs + cs2) % 97 == 0:
                        nk = (cs + sxk) & 0xFF
                elif rnd == 5:
                    if (j * rnd) % 0x7B == 0:
                        nk = (sxk ^ t) & 0xFF
                elif rnd == 6:
                    if cs == 0xFF and cs2 == 0:
                        nk = 0
                        skip = True
                elif rnd == 7:
                    if not (cv < 0x154 or 0x3E8 < cv < 0x3FC):
                        if (rnd + j) % 0x33 == 0 or cv >= 0x3FC:
                            nk ^= cs
                elif rnd == 8:
                    if not (cv < 0x154 or 0x3E8 < cv < 0x3FC):
                        if cs % 0x1D == 0 or cv >= 0x3FC:
                            nk ^= cs
                if (j + i) % (cs % 5 + 1) == 0:
                    nk ^= t
                elif skip:
                    nk = ~sxk
                key[i * 256 + j] = nk & 0xFF
                t += i
    return bytes(key)


class _PosKeystream:
    """위치 ``p`` 의 키 바이트 = 특수 키 표에서 ``p`` 로 고른 값들의 XOR (``wolfCrypt``)."""

    def __init__(self, key: bytes, cv: int) -> None:
        self.v35 = is_v35(cv)
        if self.v35:
            mk = bytes((key[i % 256] ^ (7 * i)) & 0xFF for i in range(512))
            lo, hi = mk[:256], mk[256:]
            # 주기 65536 짜리 표를 한 번 만들어 둡니다
            self.period = 65536
            self.table = b"".join(bytes(b ^ h for b in lo) for h in hi)
        else:
            self.period = 1 << 24
            self.key = key
            self.table = None

    def stream(self, pos: int, n: int) -> bytes:
        if n <= 0:
            return b""
        if self.table is not None:
            p = pos % self.period
            out = bytearray()
            while len(out) < n:
                take = min(n - len(out), self.period - p)
                out += self.table[p:p + take]
                p = 0
            return bytes(out)
        k = self.key
        return bytes(k[q & 0xFF] ^ k[256 + ((q >> 8) & 0xFF)] ^ k[512 + ((q >> 16) & 0xFF)]
                     for q in range(pos, pos + n))


def _xor(a: bytes, b: bytes) -> bytes:
    n = len(a)
    return (int.from_bytes(a, "little") ^ int.from_bytes(b[:n], "little")).to_bytes(n, "little")


# ------------------------------------------------------------------ 머리 주소

def _crypt_addresses(head: bytearray, pw: bytes, cv: int) -> None:
    def x16(off: int, v: int) -> None:
        cur = struct.unpack_from("<H", head, off)[0]
        struct.pack_into("<H", head, off, cur ^ (v & 0xFFFF))

    if is_v35(cv):
        r = _MsvcRand(0xC + pw[9] * pw[10] + pw[3])
        base = 8
        for _ in range(2):
            for j in (3, 2, 1, 0):
                x16(base + j * 2, r.rand())
            base += 8
        r0 = r.rand() << 17
        r1 = r.rand() << 31
        v0 = ((r0 & 0xFFFFFFFF) | (r1 & 0xFFFFFFFF) | r.rand()) & 0xFFFFFFFF
        v1 = ((r0 >> 32) | (r1 >> 32)) & 0xFFFFFFFF
        a, b = struct.unpack_from("<II", head, base)
        struct.pack_into("<II", head, base, a ^ v0, b ^ v1)
        base += 8
        for j in (3, 2, 1, 0):
            x16(base + j * 2, r.rand())
    else:
        r = _MsvcRand(pw[0] + pw[7] * pw[12])
        base = 8
        for _ in range(4):
            for j in (3, 2, 1, 0):
                x16(base + j * 2, r.rand())
            base += 8


# ------------------------------------------------------------------ AES (울프판)

_SBOX = bytes.fromhex(
    "637c777bf26b6fc53001672bfed7ab76ca82c97dfa5947f0add4a2af9ca472c0"
    "b7fd9326363ff7cc34a5e5f171d8311504c723c31896059a071280e2eb27b275"
    "09832c1a1b6e5aa0523bd6b329e32f8453d100ed20fcb15b6acbbe394a4c58cf"
    "d0efaafb434d338545f9027f503c9fa851a3408f929d38f5bcb6da2110fff3d2"
    "cd0c13ec5f974417c4a77e3d645d197360814fdc222a908846eeb814de5e0bdb"
    "e0323a0a4906245cc2d3ac629195e479e7c8376d8dd54ea96c56f4ea657aae08"
    "ba78252e1ca6b4c6e8dd741f4bbd8b8a703eb5664803f60e613557b986c11d9e"
    "e1f8981169d98e949b1e87e9ce5528df8ca1890dbfe6426841992d0fb054bb16")
_RCON = (0x8D, 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)


def _key_expansion(key: bytes) -> bytearray:
    rk = bytearray(176)
    rk[:16] = key
    for i in range(4, 44):
        t = list(rk[(i - 1) * 4:(i - 1) * 4 + 4])
        if i % 4 == 0:
            t = t[1:] + t[:1]
            # 표준 AES 와 다른 부분(울프판)
            t = [_SBOX[t[0]] ^ _RCON[i // 4],
                 _SBOX[t[1]] >> 4,
                 ~_SBOX[t[2]] & 0xFF,
                 _rotr8(_SBOX[t[3]], 7)]
        for k in range(4):
            rk[i * 4 + k] = rk[(i - 4) * 4 + k] ^ t[k]
    return rk


def _xtime(x: int) -> int:
    return ((x << 1) ^ (0x1B if x & 0x80 else 0)) & 0xFF


def _aes_block(state: bytearray, rk: bytearray) -> bytearray:
    s = bytearray(a ^ b for a, b in zip(state, rk[:16]))
    for rnd in range(1, 11):
        s = bytearray(_SBOX[b] for b in s)
        # shiftRows
        s = bytearray([s[0], s[5], s[10], s[15], s[4], s[9], s[14], s[3],
                       s[8], s[13], s[2], s[7], s[12], s[1], s[6], s[11]])
        if rnd != 10:
            for c in range(0, 16, 4):
                a0, a1, a2, a3 = s[c:c + 4]
                tmp = a0 ^ a1 ^ a2 ^ a3
                s[c] = a0 ^ tmp ^ _xtime(a0 ^ a1)
                s[c + 1] = a1 ^ tmp ^ _xtime(a1 ^ a2)
                s[c + 2] = a2 ^ tmp ^ _xtime(a2 ^ a3)
                s[c + 3] = a3 ^ tmp ^ _xtime(a3 ^ a0)
        k = rk[rnd * 16:rnd * 16 + 16]
        s = bytearray(a ^ b for a, b in zip(s, k))
    return s


class _AesCtr:
    """카운터가 호출 사이에 이어집니다(원본이 같은 키 버퍼를 두 번 씁니다)."""

    def __init__(self, pw: bytes, cv: int, pro: bytes = b"\0\0\0\0") -> None:
        key = bytearray(16)
        iv = bytearray(16)
        if is_v35(cv):
            for i in range(15):
                pk = pro[i % 4]
                ik = ((i * (pk % 5 + 7)) ^ (3 * pw[i])) % 15
                ii = ((i * ((pro[(i + 1) % 4] % 7) + 0xB)) ^ (5 * pw[(i + 3) % 15])) % 15
                key[i] ^= (((i ^ pk) + (pw[ik] << (i % 3))) % 0xFB) & 0xFF
                iv[i] ^= (((pw[ii] >> (i % 2)) + ((i * i) ^ pro[(i + 2) % 4])) % 0xF6) & 0xFF
                key[15] ^= ((7 * (pw[i] + ((i + 1) ^ pk))) % 0xFD) & 0xFF
                iv[15] ^= ((11 * ((pw[i] - ((i * 2) ^ pro[(i + 2) % 4])) & 0xFFFF)) % 0x100) & 0xFF
        elif cv == 0x3F2:
            for i in range(15):
                key[i] ^= ((pw[(i * 7) % 15] + pro[i & 3]) * i * i) & 0xFF
                iv[i] ^= ((pw[(i * 11) % 15] + pro[(i + 2) % 4]) - i * i) & 0xFF
                key[15] ^= ((i * 3) + pw[i] + pro[i & 3]) & 0xFF
                iv[15] ^= ((i * 5) + pw[i] + pro[(i + 2) % 4]) & 0xFF
        else:
            for i in range(15):
                key[i] ^= (pw[(i * 7) % 15] + i * i) & 0xFF
                iv[i] ^= (pw[(i * 11) % 15] - i * i) & 0xFF
                key[15] ^= (pw[i] + i * 3) & 0xFF
                iv[15] ^= (pw[i] + i * 5) & 0xFF
        key[0] ^= pro[0]; iv[10] ^= pro[0]
        key[4] ^= pro[1]; iv[1] ^= pro[1]
        key[8] ^= pro[2]; iv[4] ^= pro[2]
        key[12] ^= pro[3]; iv[7] ^= pro[3]
        self.rk = _key_expansion(bytes(key))
        self.iv = iv

    def _inc(self) -> None:
        for b in range(15, -1, -1):
            if self.iv[b] == 0xFF:
                self.iv[b] = 0
                continue
            self.iv[b] += 1
            break

    def keystream(self, n: int) -> bytes:
        out = bytearray()
        while len(out) < n:
            out += _aes_block(self.iv, self.rk)
            self._inc()
        return bytes(out[:n])


# ------------------------------------------------------------------ 압축 풀기

def huffman_decode(src: bytes) -> bytes:
    """DxLib ``Huffman_Decode``. 루트부터 비트를 따라 내려가는 방식(원본의 표 가속과 같은 결과)."""
    pos = [0, 0]  # 바이트, 비트(MSB 부터)

    def rd(nbits: int) -> int:
        v = 0
        for i in range(nbits):
            b = (src[pos[0]] >> (7 - pos[1])) & 1
            v |= b << (nbits - 1 - i)
            pos[1] += 1
            if pos[1] == 8:
                pos[0] += 1
                pos[1] = 0
        return v

    orig = rd(rd(6) + 1)
    _press = rd(rd(6) + 1)
    weight = [0] * 256
    for i in range(256):
        bitnum = (rd(3) + 1) * 2
        minus = rd(1)
        save = rd(bitnum)
        if i == 0:
            weight[0] = save & 0xFFFF
        else:
            weight[i] = (weight[i - 1] - save if minus else weight[i - 1] + save) & 0xFFFF
    head = pos[0] + (1 if pos[1] else 0)

    w = weight + [0] * 255
    parent = [-1] * 511
    child = [(-1, -1)] * 511
    data_num, node_num = 256, 256
    while data_num > 1:
        m1 = m2 = -1
        idx = 0
        seen = 0
        while seen < data_num:
            if parent[idx] != -1:
                idx += 1
                continue
            seen += 1
            if m1 == -1 or w[m1] > w[idx]:
                m2 = m1
                m1 = idx
            elif m2 == -1 or w[m2] > w[idx]:
                m2 = idx
            idx += 1
        w[node_num] = w[m1] + w[m2]
        child[node_num] = (m1, m2)
        parent[m1] = parent[m2] = node_num
        node_num += 1
        data_num -= 1

    out = bytearray(orig)
    data = src[head:]
    bp = 0
    for o in range(orig):
        node = 510
        while node >= 256:
            byte = data[bp >> 3]
            bit = (byte >> (bp & 7)) & 1  # 압축 본문은 LSB 부터
            bp += 1
            node = child[node][bit]
        out[o] = node
    return bytes(out)


def lz_decode(src: bytes) -> bytes:
    """DxLib ``DXArchive::Decode``."""
    dest_size, src_size = struct.unpack_from("<II", src, 0)
    src_size -= 9
    keycode = src[8]
    out = bytearray()
    sp = 9
    end = 9 + src_size
    while sp < end:
        c = src[sp]
        if c != keycode:
            out.append(c)
            sp += 1
            continue
        if src[sp + 1] == keycode:
            out.append(keycode)
            sp += 2
            continue
        code = src[sp + 1]
        if code > keycode:
            code -= 1
        sp += 2
        conbo = code >> 3
        if code & 0x4:
            conbo |= src[sp] << 5
            sp += 1
        conbo += MIN_COMPRESS
        isz = code & 0x3
        if isz == 0:
            index = src[sp]; sp += 1
        elif isz == 1:
            index = src[sp] | (src[sp + 1] << 8); sp += 2
        else:
            index = src[sp] | (src[sp + 1] << 8) | (src[sp + 2] << 16); sp += 3
        index += 1
        start = len(out) - index
        if index >= conbo:
            out += out[start:start + conbo]
        else:
            for k in range(conbo):
                out.append(out[start + k])
    if len(out) != dest_size:
        raise WolfArcError(f"LZ 풀기 크기가 맞지 않습니다 ({len(out)} != {dest_size})")
    return bytes(out)


# ------------------------------------------------------------------ 아카이브

@dataclasses.dataclass
class WolfFile:
    path: str               # 아카이브 안 경로 (``BasicData/Game.dat``)
    data_address: int
    size: int
    press_size: int         # NOT_PRESSED 면 LZ 없음
    huff_size: int          # NOT_PRESSED 면 허프만 없음
    key: bytes = b""        # 옛 방식(3.00·3.14)의 파일별 7바이트 키


def _sane_name(path: str) -> bool:
    """풀린 이름이 사람이 읽을 수 있는 경로인가(키가 틀리면 깨진 글자가 나옵니다)."""
    return bool(path) and "�" not in path and all(ord(c) >= 0x20 for c in path)


class WolfArchive:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        raw = self.path.read_bytes()
        self._raw = raw
        if raw[:2] != DXA_HEAD:
            raise WolfArcError("DXArchive 가 아닙니다")
        ver = struct.unpack_from("<H", raw, 2)[0]
        if ver != 8:
            raise UnsupportedCrypt(f"DXArchive v{ver} (울프 2.x) 는 아직 못 읽습니다")
        head = bytearray(raw[:64])
        flags = struct.unpack_from("<I", head, 44)[0]
        self.crypt_version = cv = flags >> 16
        self.old = cv in OLD_KEYS or cv == 0
        if not self.old and (not is_new_crypt(cv) or cv >= 1000):
            raise UnsupportedCrypt(f"암호 방식 {cv} 은 아직 못 읽습니다 (프로판/ChaCha/3.00 이전)")
        pw = bytes(head[49:64])
        self.flags = flags & 0xFFFF
        if not self.old:
            _crypt_addresses(head, pw, cv)          # 옛 방식은 머리가 그대로입니다
        (self.head_size,) = struct.unpack_from("<I", head, 4)
        (self.data_start, self.fnt_start, self.ft_start, self.dt_start) = struct.unpack_from("<4Q", head, 8)
        self.char_code = struct.unpack_from("<I", head, 40)[0]
        self.huff_kb = head[48]
        size = len(raw)
        if self.fnt_start >= size or self.data_start > self.fnt_start:
            raise WolfArcError("머리 주소가 이상합니다 — 암호 방식이 다를 수 있습니다")

        self.no_key = bool(self.flags & FLAG_NO_KEY)
        if self.old:
            candidates = [OLD_KEYS[cv]] if cv in OLD_KEYS else ZERO_KEYS
            last: Exception | None = None
            for ks in candidates:
                self._key_string = ks
                self._old_key = key_create(ks)
                try:
                    self._read_tables()
                    if self.files and all(_sane_name(f.path) for f in self.files):
                        return
                    last = WolfArcError("목록의 이름이 이상합니다")
                except Exception as exc:                  # noqa: BLE001
                    last = exc
            raise WolfArcError(f"이 묶음의 키를 찾지 못했습니다 ({last})")

        # 첫 번째 층: 파일 전체 [64, size-64) 위치 XOR
        self._ks1 = _PosKeystream(_init_wolf_crypt(cv, pw, other=True), cv)
        self._l1 = (64, size - 64)
        # AES 층: 본문 앞 bodySize 바이트, 그리고 목록 표 전체
        if is_v35(cv):
            seed = (pw[2] * pw[4] + pw[12]) or 1
            xs = _XorShift32(seed)
            xs.next()
            if size >= xs.next() % 500 + 800:
                xs.next()
            body = size - 64
            if body >= xs.next() % 500 + 800:
                body = xs.next() % 500 + 800
        else:
            body = 0x400
        aes = _AesCtr(pw, cv)
        self._aes_body = (64, aes.keystream(body))
        self._aes_table = (self.fnt_start, aes.keystream(size - self.fnt_start))
        # 두 번째 층: 이후 모든 KeyConv
        self._ks2 = _PosKeystream(_init_wolf_crypt(cv, pw, other=False), cv)

        self._read_tables()

    # 1·AES 층을 벗긴 "임시 파일"의 [a, b) 구간
    def _pass1(self, a: int, b: int) -> bytes:
        chunk = self._raw[a:b]
        if self.old:
            return chunk
        lo, hi = max(a, self._l1[0]), min(b, self._l1[1])
        if lo < hi:
            ks = self._ks1.stream(lo, hi - lo)
            chunk = chunk[:lo - a] + _xor(chunk[lo - a:hi - a], ks) + chunk[hi - a:]
        for start, ks in (self._aes_body, self._aes_table):
            lo, hi = max(a, start), min(b, start + len(ks))
            if lo < hi:
                chunk = chunk[:lo - a] + _xor(chunk[lo - a:hi - a], ks[lo - start:hi - start]) + chunk[hi - a:]
        return chunk

    def _keyconv(self, data: bytes, position: int, key: bytes = b"") -> bytes:
        if self.no_key:
            return data
        if self.old:
            key = key or self._old_key
            r = position % 7
            rot = key[r:] + key[:r]
            return _xor(data, (rot * (len(data) // 7 + 1))[:len(data)])
        return _xor(data, self._ks2.stream(position, len(data)))

    def _read_tables(self) -> None:
        size = len(self._raw)
        if self.old and self.flags & FLAG_NO_HEAD_PRESS:
            tbl = self._keyconv(self._raw[64:64 + self.head_size], 0)
        else:
            tbl = self._keyconv(self._pass1(self.fnt_start, size), 0)
        if self.flags & FLAG_NO_HEAD_PRESS:
            head = tbl[:self.head_size]
        else:
            head = lz_decode(huffman_decode(tbl))
        self._head = head
        enc = f"cp{self.char_code}" if self.char_code else "cp932"
        self.files: list[WolfFile] = []

        def name_at(addr: int) -> str:
            n = struct.unpack_from("<H", head, addr)[0]
            orig = head[addr + 4 + n * 4:addr + 4 + n * 8]
            return orig.split(b"\0")[0].decode(enc, "replace")

        def upper_at(addr: int) -> bytes:
            """이름 표의 앞쪽(대문자) 이름 바이트. 옛 방식의 파일 키가 이것으로 만들어집니다."""
            n = struct.unpack_from("<H", head, addr)[0]
            return head[addr + 4:addr + 4 + n * 4].split(b"\0")[0]

        def walk(dir_off: int, prefix: str, chain: list[bytes]) -> None:
            _self, _parent, num, fh_addr = struct.unpack_from("<4Q", head, self.dt_start + dir_off)
            for k in range(num):
                rec = self.ft_start + fh_addr + k * 72
                na, attr, _c, _a, _w, da, ds, ps, hs = struct.unpack_from("<9Q", head, rec)
                name = name_at(na)
                if attr & 0x10:
                    walk(da, f"{prefix}{name}/", [upper_at(na)] + chain)
                else:
                    key = b""
                    if self.old:
                        # 키 문자열 + 파일 이름 + 가까운 폴더부터 위로 올라가며 폴더 이름
                        key = key_create(self._key_string + upper_at(na)
                                         + b"".join(chain))
                    self.files.append(WolfFile(f"{prefix}{name}", da, ds, ps, hs, key))

        walk(0, "", [])

    def find(self, path: str) -> WolfFile:
        low = path.replace("\\", "/").lower()
        for f in self.files:
            if f.path.lower() == low:
                return f
        raise KeyError(path)

    def read(self, f: WolfFile | str, *, strip_guard: bool = False) -> bytes:
        if isinstance(f, str):
            f = self.find(f)
        if f.size == 0:
            return b""
        at = self.data_start + f.data_address
        kb = self.huff_kb * 1024

        def take(n: int, key_pos: int) -> bytes:
            nonlocal at
            d = self._keyconv(self._pass1(at, at + n), key_pos, f.key)
            at += n
            return d

        if f.press_size != NOT_PRESSED:
            if f.huff_size != NOT_PRESSED:
                h = huffman_decode(take(f.huff_size, f.size))
                if self.huff_kb != 0xFF and f.press_size > kb * 2:
                    mid = take(f.press_size - kb * 2, f.size + f.huff_size)
                    lz = h[:kb] + mid + h[kb:kb * 2]
                else:
                    lz = h
            else:
                lz = take(f.press_size, f.size)
            out = lz_decode(lz)
        elif f.huff_size != NOT_PRESSED:
            h = huffman_decode(take(f.huff_size, f.size))
            if self.huff_kb != 0xFF and f.size > kb * 2:
                mid = take(f.size - kb * 2, f.size + f.huff_size)
                out = h[:kb] + mid + h[kb:kb * 2]
            else:
                out = h
        else:
            out = take(f.size, f.size)
        if len(out) != f.size:
            raise WolfArcError(f"{f.path}: 크기가 맞지 않습니다 ({len(out)} != {f.size})")
        if strip_guard and f.path.lower().rsplit("/", 1)[-1] in GUARD_FILES and out.startswith(GUARD):
            out = out[len(GUARD):]
        return out
