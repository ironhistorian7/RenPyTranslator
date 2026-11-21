import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path

from kirikiri_ui_assets import KiriUIAssetError, approved_source_names, is_button_asset, localize_buttons


def png(width, height, marker=b"a"):
    return (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" +
            struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00" + b"\x00" * 4 + marker)


class UIAssetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.original = png(100, 20)
        self.replacement = png(100, 20, b"translated")
        (self.root / "translated.png").write_bytes(self.replacement)
        self.entry = {"source_sha256": hashlib.sha256(self.original).hexdigest(),
                      "width": 100, "height": 20, "file": "translated.png"}
        self.write_manifest([self.entry])

    def write_manifest(self, entries):
        (self.root / "manifest.json").write_text(json.dumps({"version": 1, "assets": entries}), encoding="utf-8")

    def test_matching_and_unknown_preserve_original_paths(self):
        result, report = localize_buttons({"ui/%button;one.png": self.original,
                                           "ui/%button;unknown.png": png(1, 1)}, self.root)
        self.assertEqual(result, {"ui/%button;one.png": self.replacement})
        self.assertEqual(report, {"selected": 2, "matched": 1, "unknown": 1})

    def test_missing_bank_is_noop(self):
        self.assertEqual(localize_buttons({"a": self.original}, self.root / "missing"),
                         ({}, {"selected": 1, "matched": 0, "unknown": 1}))

    def test_button_name(self):
        self.assertTrue(is_button_asset("a/%BUTTON;normal.PNG"))
        self.assertFalse(is_button_asset("a/button.png"))
        self.assertFalse(is_button_asset("a/%button;x.jpg"))

    def test_approved_names_are_safe_and_deduplicated(self):
        self.write_manifest([{**self.entry, "source_names": ["ui/a.png", "ui/a.png", "ui/b.PNG"]}])
        self.assertEqual(approved_source_names(self.root), ["ui/a.png", "ui/b.PNG"])
        for name in ("../escape.png", "/absolute.png", "dir\\x.png", "x.txt", "C:/x.png"):
            self.write_manifest([{**self.entry, "source_names": [name]}])
            with self.assertRaises(KiriUIAssetError):
                approved_source_names(self.root)

    def test_boot_probe_only_uses_explicitly_marked_assets(self):
        self.write_manifest([{**self.entry, 'source_names':['ui/title.png'], 'boot_probe':True},
                             {**self.entry, 'source_names':['ui/dialog.png']}])
        self.assertEqual(approved_source_names(self.root),['ui/title.png','ui/dialog.png'])
        self.assertEqual(approved_source_names(self.root,boot_probe_only=True),['ui/title.png'])
        self.write_manifest([{**self.entry, 'source_names':['ui/title.png'], 'boot_probe':'true'}])
        with self.assertRaises(KiriUIAssetError): approved_source_names(self.root,boot_probe_only=True)

    def test_invalid_matches_rejected(self):
        for field, value in (("width", 101), ("file", "../outside.png"),
                             ("file", "C:/escape.png"), ("file", "missing.png")):
            with self.subTest(field=field, value=value):
                self.write_manifest([{**self.entry, field: value}])
                with self.assertRaises(KiriUIAssetError):
                    localize_buttons({"a": self.original}, self.root)
        self.write_manifest([self.entry])
        (self.root / "translated.png").write_bytes(png(99, 20))
        with self.assertRaises(KiriUIAssetError):
            localize_buttons({"a": self.original}, self.root)
        (self.root / "translated.png").write_bytes(b"invalid")
        with self.assertRaises(KiriUIAssetError):
            localize_buttons({"a": self.original}, self.root)

    def test_duplicate_digest_rejected(self):
        self.write_manifest([self.entry, self.entry])
        with self.assertRaises(KiriUIAssetError):
            localize_buttons({"a": self.original}, self.root)

    def test_matched_source_needs_png_header(self):
        payload = b"not a png"
        self.write_manifest([{**self.entry, "source_sha256": hashlib.sha256(payload).hexdigest()}])
        with self.assertRaises(KiriUIAssetError):
            localize_buttons({"a": payload}, self.root)

    def test_linked_asset_rejected_if_supported(self):
        link = self.root / "linked.png"
        try:
            link.symlink_to(self.root / "translated.png")
        except OSError:
            self.skipTest("creating symlinks unavailable")
        self.write_manifest([{**self.entry, "file": "linked.png"}])
        with self.assertRaises(KiriUIAssetError):
            localize_buttons({"a": self.original}, self.root)


if __name__ == "__main__":
    unittest.main()
