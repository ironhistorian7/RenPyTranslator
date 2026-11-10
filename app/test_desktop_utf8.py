"""In-memory protocol and error-boundary checks; no game/model/app execution."""
import contextlib
import io
import json
import os
import sys
import unittest
from unittest.mock import patch

import desktop_bridge
import portable_cli


class DesktopUtf8Tests(unittest.TestCase):
    def read_wire(self,payload,encoding='cp949',declared=None,locale_encoding='cp949'):
        stream=io.TextIOWrapper(io.BytesIO(payload),encoding=encoding,errors='strict')
        self.addCleanup(stream.close)
        with patch.object(sys,'stdin',stream),patch.dict(os.environ), \
                patch('desktop_bridge.locale.getencoding',return_value=locale_encoding):
            os.environ.pop('RPT_REQUEST_ENCODING',None)
            if declared is not None:os.environ['RPT_REQUEST_ENCODING']=declared
            return desktop_bridge.read_request()

    def test_locale_wrapper_cannot_change_utf8_path(self):
        request={'path':r'X:\Games\日本語 한글 🐾\Sample','source':True}
        wire=json.dumps(request,ensure_ascii=False).encode('utf-8')
        with self.assertRaises(UnicodeDecodeError):wire.decode('cp949')
        self.assertEqual(self.read_wire(wire),request)

    def test_utf8_bom_and_text_stream_input_are_supported(self):
        request={'path':'日本語','source':True}
        text=json.dumps(request,ensure_ascii=False)
        self.assertEqual(self.read_wire(b'\xef\xbb\xbf'+text.encode()),request)
        with patch.object(sys,'stdin',io.StringIO('\ufeff'+text)):
            self.assertEqual(desktop_bridge.read_request(),request)

    def test_json_unicode_encodings_are_detected_with_or_without_bom(self):
        request={'path':r'X:\Games\日本語 한글 🐾\Sample','source':True}
        text=json.dumps(request,ensure_ascii=False)
        for encoding in ('utf-8-sig','utf-16','utf-16-le','utf-16-be',
                         'utf-32','utf-32-le','utf-32-be'):
            with self.subTest(encoding=encoding):
                self.assertEqual(self.read_wire(text.encode(encoding)),request)

    def test_non_unicode_requests_use_the_current_stream_codec(self):
        for encoding,path in (('cp949',r'X:\Games\한글\Sample'),
                              ('cp932',r'X:\Games\日本語\Sample')):
            wire=json.dumps({'path':path},ensure_ascii=False).encode(encoding)
            with self.subTest(encoding=encoding):
                self.assertEqual(self.read_wire(wire,encoding=encoding),{'path':path})

    def test_utf8_mode_stream_can_use_actual_windows_locale_for_legacy_input(self):
        request={'path':r'X:\Games\한글\Sample'}
        wire=json.dumps(request,ensure_ascii=False).encode('cp949')
        self.assertEqual(self.read_wire(wire,encoding='utf-8',locale_encoding='cp949'),request)

    def test_explicit_sender_encoding_has_priority(self):
        for encoding,path in (('cp949',r'X:\Games\한글\Sample'),
                              ('cp932',r'X:\Games\日本語\Sample'),
                              ('utf-8',r'X:\Games\日本語 한글 🐾\Sample')):
            request={'path':path}
            wire=json.dumps(request,ensure_ascii=False).encode(encoding)
            with self.subTest(encoding=encoding):
                self.assertEqual(self.read_wire(wire,declared=encoding),request)

    def test_declared_encoding_mismatch_is_not_reinterpreted(self):
        wire=json.dumps({'path':'한글'},ensure_ascii=False).encode('cp949')
        with self.assertRaises(UnicodeDecodeError):
            self.read_wire(wire,declared='utf-8')
        with self.assertRaises(LookupError):
            self.read_wire(b'{}',declared='invalid-synthetic-codec')

    def test_marked_unicode_corruption_does_not_fall_back_to_locale(self):
        for payload in (json.dumps({'path':'Sample'}).encode('utf-16')[:-1],
                        b'\xef\xbb\xbf{"path":"\xff"}'):
            with self.subTest(payload=payload):
                with self.assertRaises(UnicodeDecodeError):self.read_wire(payload)

    def test_json_syntax_error_does_not_start_encoding_fallback(self):
        stream=io.TextIOWrapper(io.BytesIO(b'{"path":}'),encoding='cp949')
        self.addCleanup(stream.close)
        with patch.object(sys,'stdin',stream),patch.dict(os.environ), \
                patch('desktop_bridge.locale.getencoding',side_effect=AssertionError('No syntax fallback')):
            os.environ.pop('RPT_REQUEST_ENCODING',None)
            with self.assertRaises(json.JSONDecodeError):desktop_bridge.read_request()

    def test_malformed_utf8_is_rejected_instead_of_changing_the_path(self):
        with self.assertRaises(UnicodeDecodeError):
            self.read_wire(b'{"path":"\xff"}',declared='utf-8')

    def test_wire_limit_accepts_exact_boundary_and_rejects_larger_input(self):
        wire=json.dumps({'path':''}).encode()
        exact=wire+b' '*(65536-len(wire))
        self.assertEqual(self.read_wire(exact),{'path':''})
        with self.assertRaisesRegex(ValueError,'너무 큽니다'):
            self.read_wire(exact+b' ')
        with self.assertRaisesRegex(ValueError,'너무 큽니다'):
            self.read_wire(json.dumps({'path':'日'*22000},ensure_ascii=False).encode())

    def test_non_object_json_still_fails_the_existing_shape_check(self):
        with self.assertRaisesRegex(ValueError,'형식'):
            self.read_wire(b'[]')


class PortableEntrypointTests(unittest.TestCase):
    def test_helper_exception_is_reported_once_with_its_operation(self):
        error=ValueError('synthetic helper failure')
        with patch.object(portable_cli,'main',side_effect=error), \
                patch.object(sys,'argv',['synthetic-cli','--desktop-project']), \
                patch('diagnostics.stage') as stage,patch('diagnostics.report') as report:
            with self.assertRaises(SystemExit) as caught:
                portable_cli.entrypoint()
        self.assertEqual(caught.exception.code,1)
        stage.assert_called_once_with('desktop helper',reset=True,helper='--desktop-project')
        report.assert_called_once_with(error)

    def test_startup_import_exception_is_reported(self):
        error=ImportError('synthetic missing module')
        with patch.object(portable_cli,'main',side_effect=error), \
                patch.object(sys,'argv',['synthetic-cli']), \
                patch('diagnostics.stage') as stage,patch('diagnostics.report') as report:
            with self.assertRaises(SystemExit):portable_cli.entrypoint()
        stage.assert_called_once_with('application startup',reset=True,helper='')
        report.assert_called_once_with(error)

    def test_existing_system_exit_is_not_reported_again(self):
        with patch.object(portable_cli,'main',side_effect=SystemExit(1)), \
                patch('diagnostics.report') as report:
            with self.assertRaises(SystemExit):portable_cli.entrypoint()
        report.assert_not_called()

    def test_unhandled_early_cancellation_has_cancellation_status(self):
        with patch.object(portable_cli,'main',side_effect=KeyboardInterrupt), \
                patch('diagnostics.report') as report,contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:portable_cli.entrypoint()
        self.assertEqual(caught.exception.code,130)
        report.assert_not_called()


if __name__=='__main__':unittest.main()
