import copy
import ctypes
import unittest
from ctypes import wintypes
from unittest.mock import MagicMock, patch

import kirikiri_menu_probe as probe


def pattern():
    samples = [[i, 0, (i % 3) * 90, (i % 3) * 60, (i % 3) * 40] for i in range(8)]
    return {'canvas': [100, 60], 'buttons': [
        {'name': 'one', 'x': 5, 'y': 10, 'samples': samples},
        {'name': 'two', 'x': 40, 'y': 30, 'samples': copy.deepcopy(samples)}]}


class MenuReadinessTests(unittest.TestCase):
    def setUp(self):
        self.patterns = pattern()
        self.callback = probe.make_menu_readiness(self.patterns)
        self.report = {'windows': [{'handle': 1, 'class': 'TTVPWindowForm'}]}
        self.colors = [tuple(s[2:]) for b in self.patterns['buttons'] for s in b['samples']]

    def test_only_declared_points_and_two_matching_buttons(self):
        with patch.object(probe, '_read_pixels', return_value=self.colors) as read:
            result = self.callback(self.report)
        self.assertTrue(result['ready'])
        self.assertEqual(result['evidence']['buttons'], ['one', 'two'])
        points = [(b['x'] + s[0], b['y'] + s[1]) for b in self.patterns['buttons'] for s in b['samples']]
        read.assert_called_once_with(1, (100, 60), points)

    def test_one_button_mismatch_does_not_establish_readiness(self):
        colors = self.colors[:8] + [(255, 255, 255)] * 8
        with patch.object(probe, '_read_pixels', return_value=colors):
            self.assertFalse(self.callback(self.report)['ready'])

    def test_bad_window_class_or_handle_never_reads(self):
        with patch.object(probe, '_read_pixels') as read:
            for window in ({'handle': 1, 'class': '#32770'},
                           {'handle': 0, 'class': 'TTVPWindowForm'},
                           {'handle': True, 'class': 'TTVPWindowForm'}):
                self.assertFalse(self.callback({'windows': [window]})['ready'])
            read.assert_not_called()

    def test_unavailable_pixels_are_inconclusive(self):
        for response in (None, [], self.colors[:-1]):
            with patch.object(probe, '_read_pixels', return_value=response):
                self.assertFalse(self.callback(self.report)['ready'])
        with patch.object(probe, '_read_pixels', side_effect=OSError):
            self.assertFalse(self.callback(self.report)['ready'])

    def test_patterns_reject_weak_duplicate_and_outside_points(self):
        for mutation in ('one_button', 'flat_color', 'repeated_position', 'out_of_bounds', 'same_name'):
            p = pattern()
            if mutation == 'one_button': p['buttons'].pop()
            elif mutation == 'flat_color':
                for sample in p['buttons'][0]['samples']: sample[2:] = [0, 0, 0]
            elif mutation == 'repeated_position': p['buttons'][1]['x'], p['buttons'][1]['y'] = 5, 10
            elif mutation == 'out_of_bounds': p['buttons'][0]['x'] = 99
            else: p['buttons'][1]['name'] = 'ONE'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                probe.make_menu_readiness(p)

    def test_gdi_reads_only_scaled_declared_points_and_checks_ratio(self):
        user, gdi = MagicMock(), MagicMock()
        def get_rect(handle, pointer):
            rect = ctypes.cast(pointer, ctypes.POINTER(wintypes.RECT)).contents
            rect.left = rect.top = 0; rect.right = 200; rect.bottom = 120
            return 1
        user.GetClientRect.side_effect = get_rect
        def to_screen(handle, pointer):
            point = ctypes.cast(pointer, ctypes.POINTER(wintypes.POINT)).contents
            point.x += 100; point.y += 200
            return 1
        user.ClientToScreen.side_effect = to_screen
        user.IsWindowVisible.return_value = 1
        user.IsIconic.return_value = 0
        user.WindowFromPoint.return_value = 11
        user.GetAncestor.return_value = 10
        user.SetThreadDpiAwarenessContext.return_value = 1234
        user.GetDC.return_value = 42
        gdi.GetPixel.side_effect = [0x00030201, 0x00060504]
        with patch.object(probe.os, 'name', 'nt'), patch.object(probe.ctypes, 'WinDLL', side_effect=[user, gdi]):
            self.assertEqual(probe._read_pixels(10, (100, 60), [(1, 2), (3, 4)]), [(1, 2, 3), (4, 5, 6)])
        self.assertEqual(gdi.GetPixel.call_args_list[0].args, (42, 103, 205))
        self.assertEqual(gdi.GetPixel.call_args_list[1].args, (42, 107, 209))
        user.GetDC.assert_called_once_with(None)
        user.ReleaseDC.assert_called_once_with(None, 42)
        self.assertEqual(user.SetThreadDpiAwarenessContext.call_args_list[-1].args, (1234,))
        user.GetDC.reset_mock(); gdi.GetPixel.reset_mock()
        with patch.object(probe.os, 'name', 'nt'), patch.object(probe.ctypes, 'WinDLL', side_effect=[user, gdi]):
            self.assertIsNone(probe._read_pixels(10, (100, 100), [(1, 2)]))
        user.GetDC.assert_not_called()
        gdi.GetPixel.assert_not_called()

    def test_occluded_or_hidden_window_is_never_sampled(self):
        user, gdi = MagicMock(), MagicMock()
        def get_rect(handle, pointer):
            rect = ctypes.cast(pointer, ctypes.POINTER(wintypes.RECT)).contents
            rect.right, rect.bottom = 100, 60
            return 1
        user.GetClientRect.side_effect = get_rect
        user.IsWindowVisible.return_value = 1; user.IsIconic.return_value = 0
        user.ClientToScreen.return_value = 1; user.WindowFromPoint.return_value = 11
        user.GetAncestor.return_value = 99
        with patch.object(probe.os, 'name', 'nt'), patch.object(probe.ctypes, 'WinDLL', side_effect=[user, gdi]):
            self.assertIsNone(probe._read_pixels(10, (100, 60), [(1, 2)]))
        user.GetDC.assert_not_called(); gdi.GetPixel.assert_not_called()
        user.IsWindowVisible.return_value = 0
        user.GetClientRect.reset_mock()
        with patch.object(probe.os, 'name', 'nt'), patch.object(probe.ctypes, 'WinDLL', side_effect=[user, gdi]):
            self.assertIsNone(probe._read_pixels(10, (100, 60), [(1, 2)]))
        user.GetClientRect.assert_not_called()
        user.IsWindowVisible.return_value = 1
        user.GetAncestor.side_effect = [10, 99]  # Covered after the initial point check.
        user.GetDC.return_value = 42
        with patch.object(probe.os, 'name', 'nt'), patch.object(probe.ctypes, 'WinDLL', side_effect=[user, gdi]):
            self.assertIsNone(probe._read_pixels(10, (100, 60), [(1, 2)]))
        gdi.GetPixel.assert_not_called()
        user.ReleaseDC.assert_called_once_with(None, 42)

    def test_monochrome_requires_core_and_contrasting_transparent_points(self):
        samples = [[i, 0, 255, 255, 255] for i in range(12)]
        contrasts = [[i, 0, i, 1] for i in range(12)]
        buttons = [{'name': name, 'x': x, 'y': 10, 'width': 12, 'height': 2,
                    'samples': copy.deepcopy(samples), 'contrasts': copy.deepcopy(contrasts)}
                   for name, x in [('one', 5), ('two', 40)]]
        callback = probe.make_menu_readiness({'canvas': [100, 60], 'buttons': buttons})
        observed = ([(255, 255, 255)] * 12 + [(0, 0, 0)] * 12) * 2
        with patch.object(probe, '_read_pixels', return_value=observed) as read:
            self.assertTrue(callback(self.report)['ready'])
        self.assertEqual(len(read.call_args.args[2]), 48)
        with patch.object(probe, '_read_pixels', return_value=[(255, 255, 255)] * 48):
            self.assertFalse(callback(self.report)['ready'])
        observed[:12] = [(0, 0, 0)] * 12
        with patch.object(probe, '_read_pixels', return_value=observed):
            self.assertFalse(callback(self.report)['ready'])
        bad = copy.deepcopy(buttons)
        bad[0]['contrasts'][0][2] = 99
        with self.assertRaises(ValueError):
            probe.make_menu_readiness({'canvas': [100, 60], 'buttons': bad})


if __name__ == '__main__':
    unittest.main()
