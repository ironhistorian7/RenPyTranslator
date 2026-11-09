"""Briefly render only our own synthetic Tk windows. No game/project inspection."""
import ctypes as C
from ctypes import wintypes as W
import json
from pathlib import Path
import struct
import sys
import tempfile
import time
import tkinter as tk
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root/'app'))
import gui

out = root/'build/ui-review'
out.mkdir(exist_ok=True)


def capture(window, destination):
    u, g = C.windll.user32, C.windll.gdi32
    u.GetParent.argtypes = [W.HWND]; u.GetParent.restype = W.HWND
    u.GetWindowDC.argtypes = [W.HWND]; u.GetWindowDC.restype = W.HDC
    u.ReleaseDC.argtypes = [W.HWND, W.HDC]
    g.CreateCompatibleDC.argtypes = [W.HDC]; g.CreateCompatibleDC.restype = W.HDC
    g.CreateCompatibleBitmap.argtypes = [W.HDC, C.c_int, C.c_int]; g.CreateCompatibleBitmap.restype = W.HBITMAP
    g.SelectObject.argtypes = [W.HDC, W.HANDLE]; g.SelectObject.restype = W.HANDLE
    g.DeleteObject.argtypes = [W.HANDLE]; g.DeleteDC.argtypes = [W.HDC]
    g.GetDIBits.argtypes = [W.HDC, W.HBITMAP, W.UINT, W.UINT, C.c_void_p, C.c_void_p, W.UINT]
    u.PrintWindow.argtypes = [W.HWND, W.HDC, W.UINT]
    u.GetWindowRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
    hwnd = u.GetParent(window.winfo_id())
    rect = W.RECT(); u.GetWindowRect(hwnd, C.byref(rect))
    width, height = rect.right-rect.left, rect.bottom-rect.top
    dc = u.GetWindowDC(hwnd)
    mem = g.CreateCompatibleDC(dc)
    bitmap = g.CreateCompatibleBitmap(dc, width, height)
    old = g.SelectObject(mem, bitmap)
    try:
        assert u.PrintWindow(hwnd, mem, 2), 'PrintWindow failed'
        g.SelectObject(mem, old)
        header = struct.pack('<IiiHHIIiiII', 40, width, height, 1, 32, 0, width*height*4, 0, 0, 0, 0)
        info = C.create_string_buffer(header)
        pixels = C.create_string_buffer(width*height*4)
        assert g.GetDIBits(mem, bitmap, 0, height, pixels, info, 0) == height
        destination.write_bytes(struct.pack('<2sIHHI', b'BM', 54+len(pixels), 0, 0, 54)+header+pixels.raw)
    finally:
        g.DeleteObject(bitmap); g.DeleteDC(mem); u.ReleaseDC(hwnd, dc)


report = []
with tempfile.TemporaryDirectory(dir=root/'build', prefix='ui-isolation-') as isolated, \
     patch.object(gui, 'ROOT', Path(isolated)), \
     patch('gui.subprocess.Popen', side_effect=AssertionError('No workers allowed')):
    for mode, advanced, size in [('light',False,'1080x830'), ('dark',False,'1080x830'),
                                  ('dark',True,'1080x830'), ('light',True,'880x700')]:
        window = tk.Tk()
        errors = []
        window.report_callback_exception = lambda *args: errors.append(str(args))
        app = gui.App(window, theme=mode)
        window.geometry(size+'+40+40')
        app.path.set('D:/Games/Example')  # Display only; nothing opens this location.
        app.navigate(advanced)
        if advanced:
            app.toggle_repairs()
            app.tasks['failed'].set(True)
            app.update_summary()
        window.update()
        window.update_idletasks()
        for _ in range(5):
            window.update()
            time.sleep(.05)
        for name in ('start', 'stop', 'entry', 'log', 'open_button'):
            widget = getattr(app, name)
            x = widget.winfo_rootx()-window.winfo_rootx()
            y = widget.winfo_rooty()-window.winfo_rooty()
            assert 0 <= x < window.winfo_width(), (name, x)
            assert 0 <= y < window.winfo_height(), (name, y)
            assert x+widget.winfo_width() <= window.winfo_width(), name
            assert y+widget.winfo_height() <= window.winfo_height(), (name, size, advanced, y, widget.winfo_height(), window.winfo_height())
        name = mode+('-advanced' if advanced else '-basic')+'-'+size
        capture(window, out/(name+'.bmp'))
        if advanced:
            app.advanced_canvas.yview_moveto(1)
            window.update()
            capture(window, out/(name+'-scrolled.bmp'))
        assert not errors, errors
        report.append(dict(screen=name, log_height=app.log.winfo_height(), callbacks='OK'))
        app.close()
(out/'layout-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2))
