# SPDX-License-Identifier: GPL-3.0-or-later
"""무거운 읽기를 딴 프로세스에 맡깁니다. 죽어도 그 파일 하나만 잃습니다.

**왜 필요한가.** 오브젝트가 수만 개인 유니티 게임에서 프로그램이 통째로
사라집니다. 브라우저에는 ``Failed to fetch``, 새로고침하면
``ERR_CONNECTION_REFUSED``. 41개 중 40개를 멀쩡히 읽고도 큰 것 하나에서
죽으면 **전부 날아가고 처음부터 다시**입니다.

파이썬 ``try`` 로는 못 막습니다. 32비트 파이썬의 2GB 벽이나 운영체제의
메모리 부족 처리는 예외가 아니라 **프로세스를 그냥 죽이는** 일이라서요.
그래서 읽는 일을 딴 프로세스에 맡기고, 그쪽이 죽으면 그 파일만 건너뜁니다.

**왜 파일마다 새로 띄우지 않는가.** 파이썬을 켜고 UnityPy 를 불러오는 데만
1~2초가 듭니다. 파일이 40개면 그것만 1분입니다. 그래서 **하나를 띄워 두고
계속 시킵니다.** 죽으면 그때 다시 띄웁니다.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

# **소식이 끊긴 시간**입니다. 전체 시간이 아닙니다.
#
# 1.2 까지는 ``TIMEOUT = 900`` 이 있었는데 **어디서도 안 썼습니다.**
# ``readline()`` 이 그냥 무한정 기다려서, 자식이 살아서 계산만 하고 있으면
# 화면은 영원히 파일 이름 하나만 띄웠습니다.
#
# 전체 시간으로 자르면 안 됩니다. 큰 게임은 한 파일에 정말 한 시간이
# 걸리기도 하는데, 15분에 자르면 다 읽어 가던 것을 버립니다. 그래서 자식이
# 진행 상황을 계속 보고하게 하고, **그 보고가 끊긴 지** 이만큼 지났을 때만
# 죽은 것으로 봅니다. 일하고 있으면 몇 시간이 걸려도 안 자릅니다.
SILENCE = 180.0


def _row(text: str, loc) -> list:
    return [text, loc.to_dict()]


# ---------------------------------------------------------------- 시키는 쪽

class Worker:
    """딴 프로세스 하나를 띄워 두고 파일을 하나씩 시킵니다.

    ``read()`` 가 ``None`` 을 돌려주면 **그쪽이 죽은 것**입니다. 그 파일은
    건너뛰고 다음 파일부터 다시 이어 갑니다.
    """

    def __init__(self, root: Path, threshold: float, unity_version: str = "",
                 include_latin: bool = False):
        self.root = str(root)
        self.threshold = threshold
        self.unity_version = unity_version
        self.include_latin = include_latin
        self.proc: subprocess.Popen | None = None
        self.deaths = 0
        self._lines: queue.Queue = queue.Queue()
        self._reader: threading.Thread | None = None
        self._watching = None
        # 마지막으로 왜 그만뒀나. 죽은 것과 말이 없는 것은 처방이 다릅니다.
        self.last_why = ""

    # -- 살리기 --------------------------------------------------------
    def _spawn(self) -> bool:
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        # 부모가 설치 폴더 밖에서 돌더라도 gameloc 을 찾게 합니다.
        here = str(Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = here + os.pathsep + env.get("PYTHONPATH", "")
        try:
            self.proc = subprocess.Popen(
                [sys.executable, "-u", "-m", "gameloc.isolate"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, env=env, text=True,
                encoding="utf-8", errors="replace")
        except Exception:                               # noqa: BLE001
            self.proc = None
            return False
        return True

    def _watch(self) -> None:
        """이 자식의 말을 받아 줄 갈래를 붙입니다.

        **``_spawn`` 이 아니라 여기서 붙입니다.** 시험이 ``_spawn`` 을
        갈아 끼우는데, 붙이는 곳이 거기면 그 순간 통로가 사라져서 부모가
        3분을 통째로 기다립니다. 누가 자식을 띄웠든 읽기 전에 확인합니다.
        """
        if self._watching is self.proc and self._reader and self._reader.is_alive():
            return
        self._lines = queue.Queue()
        self._watching = self.proc
        self._reader = threading.Thread(target=self._pump, args=(self.proc,),
                                        daemon=True)
        self._reader.start()

    def _pump(self, proc) -> None:
        try:
            for line in proc.stdout:
                self._lines.put(line)
        except Exception:                               # noqa: BLE001
            pass
        self._lines.put(None)                           # 끝났다는 뜻

    def _alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # -- 시키기 --------------------------------------------------------
    def read(self, path: Path, progress=None):
        """``(줄들, 통계)`` 또는 죽었으면 ``None``.

        자식은 답 하나만 주는 게 아니라 **일하는 동안 계속 소식을 줍니다.**
        그 소식을 ``progress`` 로 그대로 흘려보내고, 소식이 ``SILENCE`` 만큼
        끊기면 그때 죽은 것으로 봅니다.
        """
        from .model import Location

        if not self._alive() and not self._spawn():
            return None
        assert self.proc is not None and self.proc.stdin
        self._watch()

        ask = json.dumps({"path": str(path), "root": self.root,
                          "threshold": self.threshold,
                          "unity_version": self.unity_version,
                          "include_latin": self.include_latin})
        try:
            self.proc.stdin.write(ask + "\n")
            self.proc.stdin.flush()
        except Exception:                               # noqa: BLE001
            self.last_why = ""
            self.deaths += 1
            self.close()
            return None

        self.last_why = ""
        while True:
            try:
                line = self._lines.get(timeout=SILENCE)
            except queue.Empty:
                # 살아는 있는데 몇 분째 아무 말이 없습니다. 매달려 있느니
                # 이 파일만 버리고 나머지를 살립니다.
                self.last_why = (
                    f"{round(SILENCE / 60)}분 넘게 아무 진행이 없었습니다. "
                    "이 파일이 유난히 복잡한 것으로 보입니다")
                self.deaths += 1
                self.close()
                return None
            if line is None:                # 대답 없이 끊김 = 죽었습니다
                self.last_why = ""
                self.deaths += 1
                self.close()
                return None
            try:
                got = json.loads(line)
            except ValueError:
                continue                    # 자식이 흘린 딴 글. 무시합니다.
            if "tick" in got:
                if progress:
                    progress(got["tick"])
                continue
            if not got.get("ok"):
                # 그쪽에서 잡힌 오류. 프로세스는 살아 있으니 그대로 씁니다.
                raise RuntimeError(got.get("why") or "읽지 못했습니다")
            rows = [(t, Location.from_dict(d)) for t, d in got.get("rows", [])]
            return rows, got.get("stats") or {}

    def close(self) -> None:
        if self.proc is None:
            return
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:                               # noqa: BLE001
            try:
                self.proc.kill()
            except Exception:                           # noqa: BLE001
                pass
        self.proc = None
        self._reader = None
        self._watching = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False


def why_it_died(path: Path) -> str:
    """죽은 까닭을 **사람 말로** 짐작합니다. 확실한 것만 말합니다."""
    bits = 64
    try:
        bits = 32 if sys.maxsize <= 2 ** 32 else 64
    except Exception:                                   # noqa: BLE001
        pass
    try:
        mb = path.stat().st_size // (1024 * 1024)
    except OSError:
        mb = 0
    if bits == 32:
        return ("32비트 파이썬은 2GB 를 넘겨 쓸 수 없습니다. "
                "64비트 파이썬을 깔면 대개 해결됩니다")
    if mb >= 64:
        return f"파일이 큽니다({mb}MB). 메모리가 모자란 것으로 보입니다"
    return "메모리가 모자란 것으로 보입니다"


# ---------------------------------------------------------------- 시킴받는 쪽

def _serve() -> None:
    """부모가 주는 대로 읽어서 돌려줍니다. 한 줄에 하나씩."""
    from collections import defaultdict

    from .extract import _from_unity_file

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            ask = json.loads(line)
        except ValueError:
            continue
        stats: dict = defaultdict(int)
        stats["unity_version"] = ask.get("unity_version", "")

        def tick(msg: str) -> None:
            """부모에게 '아직 일하고 있다' 고 알립니다.

            이걸 안 넘겨서 ``_from_unity_file`` 안에 이미 있던 진행 표시가
            격리를 도입한 순간 통째로 사라졌습니다. 주석에 ``data.unity3d``
            라고 이름까지 적어 둔 코드를, 바로 그 파일에서만 안 나오게
            만들었습니다. 지우지 마세요.
            """
            sys.stdout.write(json.dumps({"tick": msg}, ensure_ascii=False) + "\n")
            sys.stdout.flush()

        try:
            rows = _from_unity_file(
                Path(ask["path"]), Path(ask["root"]), ask["threshold"], stats,
                include_latin=ask.get("include_latin", False), progress=tick)
            out = {"ok": True, "rows": [_row(t, l) for t, l in rows],
                   "stats": {k: v for k, v in stats.items()
                             if isinstance(v, (int, float, str))}}
        except Exception as exc:                        # noqa: BLE001
            out = {"ok": False, "why": f"{type(exc).__name__}: {exc}"}
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    _serve()
