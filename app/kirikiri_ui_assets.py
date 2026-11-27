"""Match preapproved UI images to a local, content-addressed asset bank.

The caller selects source members; this module never scans a game or archive.
"""

import csv
import hashlib
import json
import re
import struct
from pathlib import Path, PurePosixPath


class KiriUIAssetError(ValueError):
    error_code = "kirikiri_ui_asset_invalid"


def is_button_asset(rel):
    """Recognize KiriKiri's percent-button PNG naming convention."""
    name = str(rel).replace("\\", "/").rsplit("/", 1)[-1].casefold()
    return ("%button;" in name or "%sysbutton;" in name) and name.endswith(".png")


def image_coverage(layouts, available, localized):
    """Inventory display layers from layout CSVs, including unlocalized screens."""
    from kirikiri_text import _decode

    available = {name.casefold() for name in available}
    localized = {name.casefold() for name in localized}
    screens = []
    groups = []
    for layout, raw in sorted(layouts.items()):
        try:
            text, _, _ = _decode(raw)
            rows = list(csv.reader(text.splitlines(), strict=True))
        except (ValueError, csv.Error):
            continue
        screen = PurePosixPath(layout)
        controls = {row[1]: row[2] for row in rows if len(row) >= 3 and row[0].casefold() == 'type'}
        if not controls:
            continue
        screens.append(layout)
        paths = {}
        for row in rows:
            if len(row) < 3 or row[0].casefold() != 'file' or row[1] not in controls:
                continue
            filename = row[2]
            if not filename.casefold().endswith('.png'):
                filename += '.png'
            if not row[2] or any(c in filename for c in '/\\:\0'):
                continue
            path = str(screen.with_name(filename))
            if path.casefold() in available:
                paths.setdefault(row[1], set()).add(path)
        for control, members in sorted(paths.items()):
            pending = sorted(name for name in members if name.casefold() not in localized)
            status = 'localized' if not pending else 'unreviewed' if len(pending) == len(members) else 'partial'
            groups.append({'layout': layout, 'control': control, 'type': controls[control],
                           'status': status, 'pending': pending})
    return {'screens': screens, 'controls': groups}


def _dimensions(data):
    # Only the fixed PNG header is needed for matching; image decoders validate
    # the remainder when loading it. Require a complete IHDR including its CRC.
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[8:16] != b"\x00\x00\x00\rIHDR":
        raise KiriUIAssetError("Invalid PNG header")
    width, height = struct.unpack(">II", data[16:24])
    if not width or not height or width > 0x7fffffff or height > 0x7fffffff:
        raise KiriUIAssetError("Invalid PNG dimensions")
    return width, height


def _asset_path(root, value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise KiriUIAssetError("Invalid asset path")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts) or PurePosixPath(value).is_absolute():
        raise KiriUIAssetError("Invalid asset path")
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise KiriUIAssetError("Asset path contains a symlink")
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError, RuntimeError) as exc:
        raise KiriUIAssetError("Asset path is missing or outside the bank") from exc
    if path.suffix.casefold() != ".png" or not path.is_file():
        raise KiriUIAssetError("Asset is not a PNG file")
    return path


def approved_source_names(bank_dir, *, boot_probe_only=False):
    """List explicitly named PNG members without opening a game or archive."""
    root = Path(bank_dir)
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        return []
    if root.is_symlink() or manifest_path.is_symlink():
        raise KiriUIAssetError("Bank or manifest is a symlink")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise KiriUIAssetError("Cannot read asset manifest") from exc
    if not isinstance(manifest, dict) or manifest.get("version") != 1 or not isinstance(manifest.get("assets"), list):
        raise KiriUIAssetError("Invalid asset manifest")
    names = []
    seen = set()
    for entry in manifest["assets"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("source_names", []), list):
            raise KiriUIAssetError("Invalid source names")
        if "boot_probe" in entry and type(entry["boot_probe"]) is not bool:
            raise KiriUIAssetError("Invalid boot probe marker")
        if boot_probe_only and not entry.get("boot_probe", False): continue
        for name in entry.get("source_names", []):
            if (not isinstance(name, str) or not name or "\\" in name or ":" in name
                    or "\0" in name or any(part in ("", ".", "..") for part in name.split("/"))
                    or not name.casefold().endswith(".png")):
                raise KiriUIAssetError("Invalid source name")
            if name not in seen:
                names.append(name)
                seen.add(name)
    return names


def localize_buttons(source_members, bank_dir):
    """Return overrides for caller-selected byte members and a count report.

    Each bank entry has source_sha256, width, height, and file fields. An
    unknown digest is skipped; malformed banks and invalid matches raise an
    error so that a broken asset cannot silently produce a partial patch.
    """
    root = Path(bank_dir)
    manifest_path = root / "manifest.json"
    report = {"selected": len(source_members), "matched": 0, "unknown": 0}
    if not manifest_path.exists():
        report["unknown"] = len(source_members)
        return {}, report
    if root.is_symlink() or manifest_path.is_symlink():
        raise KiriUIAssetError("Bank or manifest is a symlink")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise KiriUIAssetError("Cannot read asset manifest") from exc
    if not isinstance(manifest, dict) or manifest.get("version") != 1 or not isinstance(manifest.get("assets"), list):
        raise KiriUIAssetError("Invalid asset manifest")
    by_hash = {}
    for entry in manifest["assets"]:
        if not isinstance(entry, dict):
            raise KiriUIAssetError("Invalid asset manifest entry")
        digest = entry.get("source_sha256")
        width, height = entry.get("width"), entry.get("height")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise KiriUIAssetError("Invalid source SHA256")
        if digest in by_hash:
            raise KiriUIAssetError("Duplicate source SHA256")
        if any(type(n) is not int or n <= 0 or n > 0x7fffffff for n in (width, height)):
            raise KiriUIAssetError("Invalid manifest dimensions")
        by_hash[digest] = entry
    overrides = {}
    for rel, original in source_members.items():
        digest = hashlib.sha256(original).hexdigest()
        entry = by_hash.get(digest)
        if entry is None:
            report["unknown"] += 1
            continue
        expected = (entry["width"], entry["height"])
        if _dimensions(original) != expected:
            raise KiriUIAssetError("Source PNG dimensions do not match manifest")
        path = _asset_path(root, entry.get("file"))
        try:
            replacement = path.read_bytes()
        except OSError as exc:
            raise KiriUIAssetError("Cannot read replacement PNG") from exc
        if _dimensions(replacement) != expected:
            raise KiriUIAssetError("Replacement PNG dimensions do not match source")
        overrides[rel] = replacement
        report["matched"] += 1
    return overrides, report
