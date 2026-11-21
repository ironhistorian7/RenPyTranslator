"""Sparse, read-only title-button evidence for a KiriKiri boot probe.

The caller supplies previously inspected opaque button pixels and their title
layout coordinates. No images, scripts, screenshots, or input are accessed.
"""
import ctypes
import math
import os
from ctypes import wintypes


def _integer(value):
    return type(value) is int


def _validate(patterns):
    if not isinstance(patterns, dict):
        raise ValueError('Invalid menu patterns')
    canvas = patterns.get('canvas')
    if (not isinstance(canvas, (list, tuple)) or len(canvas) != 2
            or any(not _integer(n) or not 0 < n <= 16384 for n in canvas)):
        raise ValueError('Invalid menu canvas')
    width, height = canvas
    buttons = patterns.get('buttons')
    if not isinstance(buttons, list) or not 2 <= len(buttons) <= 32:
        raise ValueError('At least two button patterns are required')
    parsed = []
    names, used = set(), set()
    for button in buttons:
        if not isinstance(button, dict):
            raise ValueError('Invalid button pattern')
        name, x, y, samples = (button.get(k) for k in ('name', 'x', 'y', 'samples'))
        if (not isinstance(name, str) or not name.strip() or name.casefold() in names
                or not _integer(x) or not _integer(y)
                or not isinstance(samples, list) or not 8 <= len(samples) <= 128):
            raise ValueError('Invalid button pattern')
        names.add(name.casefold())
        contrasts = button.get('contrasts', [])
        if not isinstance(contrasts, list):
            raise ValueError('Invalid button contrasts')
        bw, bh = button.get('width'), button.get('height')
        if contrasts and (not _integer(bw) or not _integer(bh) or bw <= 0 or bh <= 0
                          or x < 0 or y < 0 or x + bw > width or y + bh > height):
            raise ValueError('Invalid button rectangle')
        points, colours, sample_points = [], set(), set()
        for sample in samples:
            if (not isinstance(sample, (list, tuple)) or len(sample) != 5
                    or any(not _integer(v) for v in sample)):
                raise ValueError('Invalid button sample')
            dx, dy, r, g, b = sample
            point = (x + dx, y + dy)
            if (dx < 0 or dy < 0 or not 0 <= point[0] < width or not 0 <= point[1] < height
                    or any(not 0 <= v <= 255 for v in (r, g, b)) or point in used
                    or (contrasts and (dx >= bw or dy >= bh))):
                raise ValueError('Invalid or overlapping button sample')
            used.add(point)
            sample_points.add((dx, dy))
            colours.add((r, g, b))
            points.append((point, (r, g, b)))
        varied = len(colours) >= 3 and max(max(c[i] for c in colours) - min(c[i] for c in colours) for i in range(3)) >= 32
        pairs, cores, edges = [], set(), set()
        if contrasts:
            if len(contrasts) < 12 or len(contrasts) > 128:
                raise ValueError('At least twelve button contrasts are required')
            for item in contrasts:
                if not isinstance(item, (list, tuple)) or len(item) != 4 or any(not _integer(v) for v in item):
                    raise ValueError('Invalid button contrast')
                cx, cy, ex, ey = item
                core, edge = (cx, cy), (ex, ey)
                absolute_edge = (x + ex, y + ey)
                if (core not in sample_points or core in cores or edge in edges or edge in sample_points
                        or not 0 <= ex < bw or not 0 <= ey < bh or absolute_edge in used):
                    raise ValueError('Invalid or overlapping button contrast')
                cores.add(core); edges.add(edge); used.add(absolute_edge)
                pairs.append(((x + cx, y + cy), absolute_edge))
        if not varied and not pairs:
            raise ValueError('Button samples have insufficient color variation')
        parsed.append((name, points, pairs, varied))
    return (width, height), parsed


def _read_pixels(handle, canvas, points):
    """Read selected composited screen pixels only while owned and visible."""
    if os.name != 'nt':
        return None
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)
    user32.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    user32.GetClientRect.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = (wintypes.HWND,)
    user32.IsIconic.restype = wintypes.BOOL
    user32.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
    user32.ClientToScreen.restype = wintypes.BOOL
    user32.WindowFromPoint.argtypes = (wintypes.POINT,)
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetDC.argtypes = (wintypes.HWND,)
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
    gdi32.GetPixel.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int)
    gdi32.GetPixel.restype = wintypes.DWORD
    set_dpi = getattr(user32, 'SetThreadDpiAwarenessContext', None)
    previous_dpi = None
    if set_dpi:
        set_dpi.argtypes = (ctypes.c_void_p,)
        set_dpi.restype = ctypes.c_void_p
        previous_dpi = set_dpi(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    try:
        rect = wintypes.RECT()
        if (not user32.IsWindowVisible(handle) or user32.IsIconic(handle)
                or not user32.GetClientRect(handle, ctypes.byref(rect))):
            return None
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w <= 0 or h <= 0 or abs((w / h) / (canvas[0] / canvas[1]) - 1) > 0.02:
            return None
        scaled = [(min(w - 1, int((x + 0.5) * w / canvas[0])),
                   min(h - 1, int((y + 0.5) * h / canvas[1]))) for x, y in points]
        if len(set(scaled)) != len(scaled):
            return None
        screen_points = []
        for x, y in scaled:
            point = wintypes.POINT(x, y)
            if not user32.ClientToScreen(handle, ctypes.byref(point)):
                return None
            window = user32.WindowFromPoint(point)
            if not window or user32.GetAncestor(window, 2) != handle:  # GA_ROOT
                return None
            screen_points.append((point.x, point.y))
        dc = user32.GetDC(None)
        if not dc:
            return None
        try:
            result = []
            for x, y in screen_points:
                # Recheck immediately before sampling to avoid reading an occluder.
                point = wintypes.POINT(x, y)
                window = user32.WindowFromPoint(point)
                if not window or user32.GetAncestor(window, 2) != handle:
                    return None
                value = gdi32.GetPixel(dc, x, y)
                if value == 0xffffffff:
                    return None
                result.append((value & 255, (value >> 8) & 255, (value >> 16) & 255))
            return result
        finally:
            user32.ReleaseDC(None, dc)
    finally:
        if previous_dpi:
            set_dpi(previous_dpi)


def make_menu_readiness(patterns):
    """Create a boot callback requiring two distinct matching title buttons.

    Each color button supplies >=8 distinct opaque source pixels with >=3
    colors. Monochrome buttons must additionally supply >=12 contrast pairs
    between an opaque sample and a transparent point inside its rectangle.
    Samples are [dx, dy, red, green, blue], relative to button x/y. A match
    needs 85% of its pixels within 30 levels per channel, on one window.
    """
    canvas, buttons = _validate(patterns)
    points = [point for _, samples, pairs, _ in buttons
              for point in ([sample[0] for sample in samples] + [edge for _, edge in pairs])]
    def readiness(report):
        if not isinstance(report, dict):
            return {'ready': False}
        for window in report.get('windows', []):
            if not isinstance(window, dict) or window.get('class') != 'TTVPWindowForm':
                continue
            handle = window.get('handle')
            if not _integer(handle) or handle <= 0:
                continue
            try:
                observed = _read_pixels(handle, canvas, points)
            except (OSError, ValueError):
                continue
            if observed is None or len(observed) != len(points):
                continue
            matched, offset = [], 0
            for name, samples, pairs, varied in buttons:
                core_values = observed[offset:offset + len(samples)]
                by_point = {point: (rgb, wanted) for rgb, (point, wanted) in zip(core_values, samples)}
                hits = [wanted for rgb, (_, wanted) in zip(core_values, samples)
                        if all(abs(actual - expected) <= 30 for actual, expected in zip(rgb, wanted))]
                edges = observed[offset + len(samples):offset + len(samples) + len(pairs)]
                contrast_hits = 0
                for (core, _), edge_rgb in zip(pairs, edges):
                    rgb, wanted = by_point[core]
                    luminance = lambda c: (299*c[0] + 587*c[1] + 114*c[2]) / 1000
                    if (all(abs(a-b) <= 30 for a, b in zip(rgb, wanted))
                            and abs(luminance(rgb) - luminance(edge_rgb)) >= 32):
                        contrast_hits += 1
                if (len(hits) >= math.ceil(len(samples) * 0.85)
                        and (not varied or len(set(hits)) >= 3)
                        and (not pairs or contrast_hits >= math.ceil(len(pairs) * 0.85))):
                    matched.append(name)
                offset += len(samples) + len(pairs)
            if len(matched) >= 2:
                return {'ready': True, 'evidence': {'kind': 'title_buttons', 'buttons': matched,
                                                   'matched_buttons': len(matched)}}
        return {'ready': False}
    return readiness
