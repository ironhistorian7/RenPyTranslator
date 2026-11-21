"""Conservative, opt-in packaging of a portable Japanese locale launcher.

A locale requirement cannot be established from a filename, game language, or PE
architecture.  Only project evidence or an explicit user decision enables it.
"""
from pathlib import Path, PureWindowsPath
import shutil
import struct
import sys

_RUNTIME_FILES = ("LoaderDll.dll", "LocaleEmulator.dll", "COPYING", "COPYING.LESSER", "PROVENANCE.txt", "PortableLocaleLauncher.cs", "MenuBridge.exe", "MenuBridge.cs", "SOURCE-NOTICE.txt", "Locale-Emulator-v2.5.0.1-source.zip", "Locale-Emulator-Core-2021-08-23-source.zip")


def detect_locale_need(profile=None):
    """Return an evidence-bounded decision for a locale_profile mapping.

    profile example: {"status": "required", "locale": "ja-JP",
      "evidence": "user_observed_failure_then_ja_success"}.
    Unknown evidence remains unknown, even if other hints mention Japan.
    """
    profile = profile if isinstance(profile, dict) else {}
    comparison = profile.get("launch_comparison")
    if isinstance(comparison, dict):
        try:
            from kirikiri_locale_probe import compare_boot_results
        except ImportError:
            from .kirikiri_locale_probe import compare_boot_results
        result = compare_boot_results(comparison.get("native"), comparison.get("japanese"))
        return {"status": result["status"], "locale": result["locale"],
                "evidence": "launch_comparison" if result["status"] != "unknown" else "inconclusive_launch_comparison"}
    status = profile.get("status")
    evidence = profile.get("evidence")
    if status == "not_required" and evidence in {"user_observed_native_success", "user_confirmed_not_required"}:
        return {"status": "not_required", "locale": None, "evidence": evidence}
    if status == "required" and profile.get("locale") == "ja-JP" and evidence in {"user_observed_failure_then_ja_success", "user_confirmed_required"}:
        return {"status": "required", "locale": "ja-JP", "evidence": evidence}
    return {"status": "unknown", "locale": None, "evidence": None}


def pe_architecture(path):
    """Inspect the PE machine without executing it."""
    with open(path, "rb") as f:
        if f.read(2) != b"MZ":
            raise ValueError("Game executable is not a PE file")
        f.seek(0x3c)
        raw = f.read(4)
        if len(raw) != 4:
            raise ValueError("Truncated PE file")
        offset = struct.unpack("<I", raw)[0]
        f.seek(offset)
        header = f.read(6)
    if len(header) != 6 or header[:4] != b"PE\0\0":
        raise ValueError("Invalid PE header")
    return {0x14c: "x86", 0x8664: "x64", 0xaa64: "arm64"}.get(struct.unpack("<H", header[4:])[0], "unsupported")


def runtime_dir():
    if getattr(sys, "frozen", False):
        root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        root = Path(__file__).resolve().parent.parent
    return root / "runtimes" / "locale"


def _target_path(target_relative):
    target = PureWindowsPath(str(target_relative))
    if target.is_absolute() or target.drive or not target.parts or any(x in ("..", ".") for x in target.parts) or target.suffix.lower() != ".exe":
        raise ValueError("Target must be a relative executable path inside the game folder")
    return target


def install_portable_locale(output_dir, target_relative, source_exe, profile, runtime=None):
    """Add launcher to a staged patch/game directory; never modify source_exe.

    source_exe is inspected only. target_relative is its eventual relative path
    in the installed game. Unknown/not-required profiles write nothing.
    """
    decision = detect_locale_need(profile)
    if decision["status"] != "required":
        return {**decision, "files": []}
    if pe_architecture(source_exe) != "x86":
        raise ValueError("Portable Japanese locale supports x86 executables only")
    target = _target_path(target_relative)
    runtime = Path(runtime) if runtime is not None else runtime_dir()
    for name in ("Play-Korean.exe", *_RUNTIME_FILES):
        if not (runtime / name).is_file():
            raise FileNotFoundError("Missing portable locale asset: " + str(runtime / name))
    out = Path(output_dir)
    destinations = [out / "Play-Korean.exe", out / "locale-target.txt", out / "locale-runtime", out / "LoaderDll.dll", out / "LocaleEmulator.dll", out / "MenuBridge.exe", out / "locale-mode.txt"]
    if any(p.exists() for p in destinations):
        raise FileExistsError("Portable locale output would overwrite existing files")
    out.mkdir(parents=True, exist_ok=True)
    dest = out / "locale-runtime"
    dest.mkdir()
    for name in _RUNTIME_FILES:
        shutil.copy2(runtime / name, (out if name.endswith(".dll") or name == "MenuBridge.exe" else dest) / name)
    shutil.copy2(runtime / "Play-Korean.exe", out / "Play-Korean.exe")
    (out / "locale-target.txt").write_text(str(target), encoding="utf-8")
    (out / "locale-mode.txt").write_text("ja-JP", encoding="utf-8")
    return {**decision, "files": ["Play-Korean.exe", "locale-target.txt", "locale-mode.txt"] + [(n if n.endswith(".dll") or n == "MenuBridge.exe" else "locale-runtime/" + n) for n in _RUNTIME_FILES]}


def install_portable_ui_launcher(output_dir, target_relative, source_exe, profile=None, runtime=None):
    """Package an explicit menu runner; native mode needs no LE runtime DLLs."""
    if detect_locale_need(profile)["status"] == "required":
        return install_portable_locale(output_dir,target_relative,source_exe,profile,runtime)
    if pe_architecture(source_exe) not in ("x86", "x64", "arm64"):
        raise ValueError("Unsupported executable architecture")
    target=_target_path(target_relative)
    runtime=Path(runtime) if runtime is not None else runtime_dir()
    root_files=("Play-Korean.exe", "MenuBridge.exe")
    notices=("COPYING", "COPYING.LESSER", "PortableLocaleLauncher.cs", "MenuBridge.cs")
    for name in root_files+notices:
        if not (runtime/name).is_file(): raise FileNotFoundError("Missing menu launcher asset: "+str(runtime/name))
    out=Path(output_dir)
    names=root_files+("locale-target.txt", "locale-mode.txt", "locale-runtime")
    if any((out/name).exists() for name in names): raise FileExistsError("Menu launcher output would overwrite existing files")
    out.mkdir(parents=True,exist_ok=True); dest=out/"locale-runtime"; dest.mkdir()
    for name in root_files: shutil.copy2(runtime/name,out/name)
    for name in notices: shutil.copy2(runtime/name,dest/name)
    (out/"locale-target.txt").write_text(str(target),encoding="utf-8")
    (out/"locale-mode.txt").write_text("native",encoding="utf-8")
    return {"status":detect_locale_need(profile)["status"],"locale":None,"files":list(root_files)+["locale-target.txt","locale-mode.txt"]+["locale-runtime/"+n for n in notices]}
