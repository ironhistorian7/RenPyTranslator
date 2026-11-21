import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from app.kirikiri_containers import (ContainerError, DirectorySource, ISOSource, XP3Archive,
    XP3_MAGIC, _chunk, choose_patch_name, discover_game_roots, open_source, write_xp3)


def iso_record(extent, size, name, directory=False):
    length = 33 + len(name) + (len(name) % 2 == 0)
    rec = bytearray(length)
    rec[0] = length
    struct.pack_into('<I', rec, 2, extent)
    struct.pack_into('>I', rec, 6, extent)
    struct.pack_into('<I', rec, 10, size)
    struct.pack_into('>I', rec, 14, size)
    rec[25] = 2 if directory else 0
    rec[28:32] = b'\x01\x00\x00\x01'
    rec[32] = len(name)
    rec[33:33 + len(name)] = name
    return rec


def build_iso(path, joliet):
    image = bytearray(25 * 2048)
    desc = bytearray(2048)
    desc[:7] = b'\x02CD001\x01' if joliet else b'\x01CD001\x01'
    if joliet:
        desc[88:91] = b'%/E'
    root = iso_record(20, 2048, b'\0', True)
    desc[156:156 + len(root)] = root
    image[16 * 2048:17 * 2048] = desc
    end = bytearray(2048); end[:7] = b'\xffCD001\x01'
    image[17 * 2048:18 * 2048] = end
    enc = lambda s: s.encode('utf-16be' if joliet else 'ascii')
    records = [iso_record(20, 2048, b'\0', True), iso_record(20, 2048, b'\1', True),
               iso_record(21, 5, enc('DATA.XP3;1')), iso_record(22, 1, enc('GAME.EXE;1'))]
    directory = b''.join(records)
    image[20 * 2048:20 * 2048 + len(directory)] = directory
    image[21 * 2048:21 * 2048 + 5] = b'hello'
    image[22 * 2048] = 42
    path.write_bytes(image)


class ContainerTests(unittest.TestCase):
    def test_iso_primary_and_joliet(self):
        with tempfile.TemporaryDirectory() as d:
            for joliet in (False, True):
                p = Path(d) / 'fixture.iso'; build_iso(p, joliet)
                with ISOSource(p) as s:
                    self.assertEqual(s.joliet, joliet)
                    self.assertEqual(s.read('DATA.XP3'), b'hello')
                    self.assertEqual(s.read_range('DATA.XP3', 1, 3), b'ell')
                    self.assertEqual(discover_game_roots(s), [''])
                    with self.assertRaises(ContainerError): s.read_range('DATA.XP3', 4, 2)
                    with self.assertRaises(KeyError): s.read('missing')
                    with self.assertRaises(ContainerError): s.read('../DATA.XP3')

    def test_roundtrip_and_archive_priority(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write_xp3(root / 'data.xp3', {'한글/hello.txt': b'hello' * 100, 'empty': b''})
            write_xp3(root / 'patch.xp3', {'한글/hello.txt': b'new'})
            with DirectorySource(root) as s:
                a = XP3Archive(s, 'data.xp3'); b = XP3Archive(s, 'patch.xp3')
                self.assertEqual(a.read('한글/hello.txt'), b'hello' * 100)
                self.assertEqual(a.read_range('한글/hello.txt', 3, 5), b'lohel')
                self.assertEqual(a.read('empty'), b'')
                self.assertEqual(({**a.entries, **b.entries})['한글/hello.txt'].size, 3)
                self.assertEqual(b.read('한글/hello.txt'), b'new')
                self.assertEqual(a.entries['한글/hello.txt'].adler32, zlib.adler32(b'hello' * 100))
                with self.assertRaises(ContainerError): a.read_range('empty', 1, 0)
            with self.assertRaises(FileExistsError): write_xp3(root / 'patch.xp3', {})
            with self.assertRaises(ContainerError): write_xp3(root / 'bad.xp3', {'../escape': b'x'})

    def test_multi_index_and_protected_flag(self):
        def file_chunk(name, offset, flags=0):
            n = name.encode('utf-16le')
            info = struct.pack('<IQQH', flags, 1, 1, len(n) // 2) + n
            seg = struct.pack('<IQQQ', 0, offset, 1, 1)
            return _chunk(b'File', _chunk(b'info', info) + _chunk(b'segm', seg))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'data.xp3'
            first = file_chunk('a', 19, 0x80000000)
            second = file_chunk('b', 20)
            first_off = 21
            next_off = first_off + 9 + len(first) + 8
            p.write_bytes(XP3_MAGIC + struct.pack('<Q', first_off) + b'AB' + b'\x80' + struct.pack('<Q', len(first)) + first + struct.pack('<Q', next_off) + b'\x00' + struct.pack('<Q', len(second)) + second)
            with DirectorySource(d) as s:
                a = XP3Archive(s, 'data.xp3')
                self.assertEqual(a.read('a'), b'A'); self.assertEqual(a.read('b'), b'B')
                self.assertTrue(a.entries['a'].protected); self.assertEqual(len(a.notices), 1)

    def test_source_and_patch_selection(self):
        self.assertEqual(choose_patch_name([]), 'patch.xp3')
        self.assertEqual(choose_patch_name(['DATA.XP3', 'PATCH.XP3', 'patch2.xp3', 'patch_extra.xp3']), 'patch3.xp3')
        with self.assertRaises(ContainerError): choose_patch_name(['patch.xp3', 'patch3.xp3'])
        with tempfile.TemporaryDirectory() as d:
            p = Path(d); build_iso(p / 'one.iso', False)
            with open_source(p) as source: self.assertIsInstance(source, ISOSource)
            build_iso(p / 'two.iso', False)
            with self.assertRaises(ContainerError): open_source(p)


if __name__ == '__main__':
    unittest.main()
