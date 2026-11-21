import unittest
from app.kirikiri_psb import PsbDocument, PsbInt, PsbFloat, PsbResource, PsbError, _pack_array, dumps, loads


def doc(root):
    offsets = [1] + [0] * 120 + [122]
    tree = [0] * 123
    tree[122] = 121
    return PsbDocument(root, ['x'], _pack_array(offsets) + _pack_array(tree) + _pack_array([122]), [b'\x00\xffresource'])


class PsbTests(unittest.TestCase):
    def test_round_trip_and_types(self):
        root = {'x': [None, False, True, PsbInt(-129, 2), PsbInt(127, 3), PsbInt(0, 0), PsbFloat(0, 0x1d), PsbFloat(1.25, 0x1e), PsbFloat(3.5, 0x1f), PsbResource(0, 2), 'same', 'same']}
        loaded = loads(dumps(doc(root)))
        values = loaded.root['x']
        self.assertEqual(values, root['x'])
        self.assertEqual([values[i].width for i in (3, 4, 5)], [2, 3, 0])
        self.assertEqual([values[i].kind for i in (6, 7, 8)], [0x1d, 0x1e, 0x1f])
        self.assertEqual(loaded.resources, [b'\x00\xffresource'])
        values[10] = 'changed'
        again = loads(dumps(loaded)).root['x']
        self.assertEqual(again[-2:], ['changed', 'same'])
        self.assertEqual(again[9], PsbResource(0, 2))

    def test_invalid_or_unsupported(self):
        with self.assertRaises(PsbError): loads(b'mdf\0')
        with self.assertRaises(PsbError): loads(b'PSB\0')
        d = doc({'x': 'a\0b'})
        with self.assertRaises(PsbError): dumps(d)

if __name__ == '__main__': unittest.main()
