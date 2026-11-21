"""Safe archive member planning for KiriKiri patch archives.

Layout inspection is deliberately limited to supplied boot/storage scripts; it
does not execute scripts or search scenario content.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath


class PatchPathError(ValueError):
    def __init__(self, message: str, error_code: str = "kirikiri_patch_path_invalid"):
        super().__init__(message)
        self.error_code = error_code


def _path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise PatchPathError("Invalid archive path")
    parts = value.split("/")
    if any(p in ("", ".", "..") or re.search(r'[<>:"|?*\x00-\x1f]', p)
           or p.endswith((" ", ".")) or re.fullmatch(r"(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", p)
           for p in parts):
        raise PatchPathError(f"Invalid archive path: {value!r}")
    return value


def plan_patch_members(members: dict[str, bytes], layout: str = "flat") -> tuple[dict[str, bytes], dict[str, str]]:
    if layout not in ("flat", "hierarchical"):
        raise PatchPathError(f"Unknown patch layout: {layout!r}")
    result: dict[str, bytes] = {}
    mapping: dict[str, str] = {}
    seen: dict[str, str] = {}
    for original, data in members.items():
        original = _path(original)
        if not isinstance(data, bytes):
            raise PatchPathError(f"Invalid content for {original!r}")
        output = PurePosixPath(original).name if layout == "flat" else original
        key = output.casefold()
        if key in seen:
            raise PatchPathError(f"Conflicting patch paths: {seen[key]!r}, {original!r}", "kirikiri_patch_conflict")
        seen[key] = original
        result[output] = data
        mapping[output] = original
    return result, mapping


def validate_member_map(files: list[str], member_map: dict[str, str] | None, layout: str | None = None) -> dict[str, str]:
    """Validate a ledger map; absent maps represent legacy identity mappings."""
    if not isinstance(files, list) or layout not in (None, "flat", "hierarchical"):
        raise PatchPathError("Invalid files or layout")
    archive_files: dict[str, str] = {}
    for name in files:
        name = _path(name)
        key = name.casefold()
        if key in archive_files:
            raise PatchPathError(f"Duplicate archive path: {name!r}")
        archive_files[key] = name
    if member_map is None:
        member_map = {name: name for name in files}
    if not isinstance(member_map, dict):
        raise PatchPathError("Invalid member map")
    normalized: dict[str, str] = {}
    originals: set[str] = set()
    keys: set[str] = set()
    for archive, original in member_map.items():
        archive, original = _path(archive), _path(original)
        key, source_key = archive.casefold(), original.casefold()
        if key in keys or key not in archive_files or archive_files[key] != archive:
            raise PatchPathError(f"Invalid mapped archive path: {archive!r}")
        if source_key in originals:
            raise PatchPathError(f"Duplicate mapped source: {original!r}")
        if PurePosixPath(archive).name != PurePosixPath(original).name:
            raise PatchPathError(f"Mapped filenames differ: {archive!r}, {original!r}")
        if layout == "flat" and "/" in archive:
            raise PatchPathError(f"Non-flat archive path: {archive!r}")
        if layout == "hierarchical" and archive != original:
            raise PatchPathError(f"Non-identity archive path: {archive!r}")
        keys.add(key)
        originals.add(source_key)
        normalized[archive] = original
    if keys != set(archive_files):
        raise PatchPathError("Member map does not match archive files")
    return normalized


def inspect_patch_layout(blobs: dict[str, bytes]) -> dict:
    """Recognize explicit patch archive root/subdirectory auto-path registration."""
    evidence = []
    found = set()
    for path, blob in blobs.items():
        if not isinstance(path, str) or not isinstance(blob, bytes):
            continue
        if blob.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = blob.decode("utf-16", errors="replace")
        else:
            try:
                text = blob.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = blob.decode("cp932", errors="replace")
        # Remove comments without treating their contents as executable evidence.
        text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group().count("\n"), text, flags=re.S)
        lines = text.splitlines()
        code = "\n".join(re.sub(r"//.*", "", line) for line in lines)
        # First establish that a function receiving a patch name registers its archive.
        functions = {}
        pattern = r"function\s+(\w+)\s*\(\s*(\w+)[^)]*\)\s*\{"
        for match in re.finditer(pattern, code, flags=re.I | re.S):
            depth = 1
            end = match.end()
            while end < len(code) and depth:
                depth += (code[end] == "{") - (code[end] == "}")
                end += 1
            if depth == 0:
                functions[match.group(1)] = code[match.end():end - 1]
        for match in re.finditer(r"(?:Storages\s*\.\s*)?addAutoPath\s*\(([^;\n]+)", code, flags=re.I):
            expr = match.group(1)
            direct_patch = bool(re.search(r"patch(?:\d+)?\s*\.xp3", expr, re.I))
            indirect_patch = False
            for name, body in functions.items():
                if expr in body and re.search(r"\b" + re.escape(name) + r"\s*\(\s*['\"]patch(?:\d+)?(?:\.xp3)?['\"]", code, re.I):
                    indirect_patch = True
            if not (direct_patch or indirect_patch):
                continue
            # A literal suffix after the archive delimiter is an explicit subdirectory.
            suffix = re.search(r"['\"]>([^'\"]*)['\"]", expr)
            embedded = re.search(r"\.xp3>([^'\"]*)", expr, re.I)
            subdir = (suffix and suffix.group(1)) or (embedded and embedded.group(1))
            if subdir:
                kind = "hierarchical"
            elif suffix or re.search(r"\.xp3>['\"]", expr, re.I):
                kind = "flat"
            else:
                continue
            found.add(kind)
            evidence.append(f"{path}:{code[:match.start()].count(chr(10)) + 1}: addAutoPath ({kind})")
    if found:
        layout = "hierarchical" if "hierarchical" in found else "flat"
        return {"version": 1, "layout": layout, "verified": True, "basis": "patch_auto_path", "evidence": evidence}
    return {"version": 1, "layout": "flat", "verified": False, "basis": "default_kag", "evidence": evidence}
