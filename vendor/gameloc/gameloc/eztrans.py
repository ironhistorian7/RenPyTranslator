# SPDX-License-Identifier: GPL-3.0-or-later
"""이지트랜스(EZTrans XP) — **이미 깔려 있으면** 쓰는 오프라인 번역기.

## 왜

한국 게임 번역판에서 "뚝딱 되는" 도구들의 정체입니다. 인터넷에 안 물어보고
내 컴퓨터에서 도니까 **한도도 차단도 딜레이도 없습니다.** 15,000줄이
몇 분입니다. 대신 20년 된 규칙 엔진이라 품질은 나쁩니다 — 초벌용입니다.

## 우리는 배포하지 않습니다

상용 프로그램이고 지금은 정상 판매처가 없습니다. 사람들이 쓰는 것은 사실상
불법 복제본이라, **이미 깔려 있는 것을 찾아 쓸 뿐** 우리가 담아 나르지
않습니다. 없으면 엔진 목록에 아예 안 뜹니다.

## 32비트라는 벽

``J2KEngine.dll`` 은 32비트입니다. 윈도우는 64비트 프로세스가 32비트 DLL 을
**절대** 못 부릅니다. 그런데 우리는 큰 유니티 파일 때문에 사용자에게
64비트 파이썬을 권합니다. 둘이 정면으로 부딪힙니다.

그래서 ``isolate.py`` 와 같은 수를 씁니다 — **32비트 파이썬을 따로 찾아
그쪽에 심부름을 시킵니다.** 없으면 없다고 분명히 말합니다. 조용히 안 되는
것이 제일 나쁩니다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# ehnd-py 와 araltrans 에서 확인한 이름입니다. 제가 지어낸 것이 아닙니다.
#   J2K_InitializeEx(c_char_p, c_char_p) -> BOOL
#   J2K_TranslateMMNTW(c_int, c_wchar_p) -> c_wchar_p
DLLS = ("ehnd.dll", "J2KEngine.dll")     # ehnd 가 깔려 있으면 그것이 우선입니다

# 흔히 깔리는 자리. 사용자가 폴더를 직접 넣을 수도 있습니다.
GUESSES = (
    r"C:\Program Files (x86)\ChangShin\ezTrans",
    r"C:\Program Files\ChangShin\ezTrans",
    r"C:\ChangShin\ezTrans",
)

_REG = r"SOFTWARE\ChangShin\ezTrans"


def _from_registry() -> Path | None:
    try:
        import winreg
    except ImportError:
        return None
    for root in (getattr(winreg, "HKEY_LOCAL_MACHINE", None),
                 getattr(winreg, "HKEY_CURRENT_USER", None)):
        for view in (getattr(winreg, "KEY_WOW64_32KEY", 0), 0):
            try:
                with winreg.OpenKey(root, _REG, 0,
                                    winreg.KEY_READ | view) as key:
                    got, _kind = winreg.QueryValueEx(key, "FilePath")
                if got and Path(got).is_dir():
                    return Path(got)
            except OSError:
                continue
    return None


def find_folder(hint: str = "") -> Path | None:
    """이지트랜스가 깔린 폴더. 못 찾으면 ``None``."""
    seen = []
    if hint:
        seen.append(Path(hint))
    got = _from_registry()
    if got:
        seen.append(got)
    seen += [Path(g) for g in GUESSES]
    for folder in seen:
        try:
            if folder.is_dir() and _dll_in(folder):
                return folder
        except OSError:
            continue
    return None


def _dll_in(folder: Path) -> Path | None:
    for name in DLLS:
        for where in (folder, folder / "Dat"):
            p = where / name
            if p.is_file():
                return p
    return None


def installed(hint: str = "") -> bool:
    return find_folder(hint) is not None


# ---------------------------------------------------------------- 32비트 찾기

def _is_32bit_python(exe: str) -> bool:
    try:
        out = subprocess.run(
            [exe, "-c", "import sys;print(sys.maxsize<=2**32)"],
            capture_output=True, text=True, timeout=20)
    except Exception:                                   # noqa: BLE001
        return False
    return out.stdout.strip() == "True"


def find_python32() -> str:
    """32비트 파이썬의 경로. 없으면 빈 문자열.

    지금 도는 파이썬이 이미 32비트면 그걸 씁니다. 아니면 윈도우의
    ``py -3-32`` 를 물어봅니다 — 파이썬을 정식으로 깔았으면 대개 있습니다.
    """
    if sys.maxsize <= 2 ** 32:
        return sys.executable
    try:
        out = subprocess.run(["py", "-3-32", "-c", "import sys;print(sys.executable)"],
                             capture_output=True, text=True, timeout=20)
        got = out.stdout.strip()
        if got and Path(got).is_file():
            return got
    except Exception:                                   # noqa: BLE001
        pass
    for guess in (r"C:\Python312-32\python.exe", r"C:\Python311-32\python.exe"):
        if Path(guess).is_file() and _is_32bit_python(guess):
            return guess
    return ""


def why_not(hint: str = "") -> str:
    """못 쓰는 까닭을 **사람 말로**. 쓸 수 있으면 빈 문자열."""
    if os.name != "nt":
        return "이지트랜스는 윈도우 전용입니다."
    if not installed(hint):
        return ("이지트랜스가 이 컴퓨터에 안 보입니다. 깔려 있는데도 이렇게 "
                "나오면 설치 폴더를 직접 넣어 주세요.")
    if not find_python32():
        return ("이지트랜스는 32비트라서 32비트 파이썬이 있어야 부를 수 "
                "있습니다. 지금 것은 64비트입니다 — 큰 유니티 파일 때문에 "
                "64비트가 필요하니 그대로 두시고, 32비트 파이썬을 하나 더 "
                "까시면 번역할 때만 그것을 씁니다.")
    return ""


# ---------------------------------------------------------------- 심부름꾼

_CHILD = r'''
import ctypes, json, sys
from ctypes import c_char_p, c_int, c_wchar_p
folder, dll = sys.argv[1], sys.argv[2]
try:
    lib = ctypes.WinDLL(dll)
    lib.J2K_InitializeEx.argtypes = [c_char_p, c_char_p]
    lib.J2K_InitializeEx.restype = ctypes.c_bool
    lib.J2K_TranslateMMNTW.argtypes = [c_int, c_wchar_p]
    lib.J2K_TranslateMMNTW.restype = c_wchar_p
    ok = lib.J2K_InitializeEx(b"CSUSER123455", folder.encode("mbcs", "ignore"))
except Exception as exc:
    sys.stdout.write(json.dumps({"ready": False, "why": str(exc)}) + "\n")
    sys.stdout.flush(); raise SystemExit
sys.stdout.write(json.dumps({"ready": bool(ok)}) + "\n"); sys.stdout.flush()
for line in sys.stdin:
    line = line.rstrip("\n")
    if not line:
        continue
    try:
        texts = json.loads(line)
        out = [lib.J2K_TranslateMMNTW(0, t) or "" for t in texts]
        sys.stdout.write(json.dumps({"ok": True, "out": out}, ensure_ascii=False) + "\n")
    except Exception as exc:
        sys.stdout.write(json.dumps({"ok": False, "why": str(exc)}) + "\n")
    sys.stdout.flush()
'''


class Engine:
    """32비트 심부름꾼 하나를 띄워 두고 계속 시킵니다."""

    def __init__(self, hint: str = ""):
        self.folder = find_folder(hint)
        self.proc: subprocess.Popen | None = None
        self.why = why_not(hint)

    def start(self) -> None:
        if self.why:
            raise RuntimeError(self.why)
        assert self.folder is not None
        dll = _dll_in(self.folder)
        if dll is None:
            raise RuntimeError("이지트랜스 폴더는 찾았는데 엔진 파일이 없습니다.")
        exe = find_python32()
        self.proc = subprocess.Popen(
            [exe, "-u", "-c", _CHILD, str(self.folder), str(dll)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True,
            encoding="utf-8", errors="replace")
        line = self.proc.stdout.readline() if self.proc.stdout else ""
        got = json.loads(line) if line.strip() else {}
        if not got.get("ready"):
            raise RuntimeError("이지트랜스를 켜지 못했습니다: "
                               + (got.get("why") or "까닭을 알 수 없습니다"))

    def translate(self, texts: list[str]) -> list[str]:
        if self.proc is None:
            self.start()
        assert self.proc is not None and self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(json.dumps(texts, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("이지트랜스가 대답 없이 멈췄습니다.")
        got = json.loads(line)
        if not got.get("ok"):
            raise RuntimeError(got.get("why") or "번역하지 못했습니다")
        return got.get("out") or []

    def close(self) -> None:
        if self.proc is None:
            return
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:                               # noqa: BLE001
            pass
        self.proc = None
