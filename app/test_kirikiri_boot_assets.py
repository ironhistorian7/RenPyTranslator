import struct
import unittest
import zlib
import kirikiri_boot_assets as assets
import kirikiri_menu_probe as probe

def png(w,h):
    def chunk(kind,data): return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    rows=b''.join(b'\0'+b''.join(bytes((255,255,255,255 if 2<=x<w-2 and 2<=y<h-2 else 0)) for x in range(w)) for y in range(h))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(rows))+chunk(b'IEND',b'')

class BootAssetsTests(unittest.TestCase):
    def test_positions_and_canvas(self):
        csv=b'type,read,button,118,404,44,43\nfile,read,title@read%button;normal,3,3,38,37,normal\n'
        self.assertEqual(assets._positions(csv,'uipsd')['uipsd/title@read%button;normal.png'],(121,407,38,37))
        self.assertEqual(assets._canvas(b'var scWidth=800;\nscHeight=600;'),[800,600])
        self.assertEqual(assets._canvas(';scWidth=800;\n;scHeight=600;\n// 日本語'.encode('cp932')),[800,600])
        self.assertIsNone(assets._canvas(b'scWidth=800;\nscHeight=width*2;'))
        self.assertIsNone(assets._canvas(b'scWidth=800;\nscWidth=900;\nscHeight=600;'))
    def test_decode_and_contrasts(self):
        decoded=assets._decode_images([png(20,20),png(20,20)])
        one=assets._pattern('first',(1,1,20,20),decoded[0]); two=assets._pattern('second',(30,1,20,20),decoded[1])
        self.assertGreaterEqual(len(one['contrasts']),12)
        callback=probe.make_menu_readiness({'canvas':[60,40],'buttons':[one,two]})
        self.assertFalse(callback({'windows':[]})['ready'])
        self.assertIsNone(assets._pattern('invalid',(1,1,21,20),decoded[0]))
    def test_solid_images_do_not_prove_readiness(self):
        self.assertIsNone(assets._pattern('solid',(0,0,20,20),(20,20,bytes((255,255,255,255))*400)))

if __name__=='__main__': unittest.main()
