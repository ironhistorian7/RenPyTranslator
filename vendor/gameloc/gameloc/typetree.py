# Gameloc — 게임 폴더를 넣으면 번역해 주는 도구
# Copyright (C) 2026  하진
#
# 이 프로그램은 자유 소프트웨어입니다. 자유 소프트웨어 재단이 펴낸 GNU 일반
# 공중 사용 허가서 3판 또는 그 이후 판의 조건에 따라 재배포하거나 고칠 수
# 있습니다.
#
# 이 프로그램은 쓸모가 있기를 바라며 배포하지만 어떠한 보증도 하지 않습니다.
# 자세한 내용은 GNU 일반 공중 사용 허가서를 보세요.
# 함께 받은 LICENSE 파일에 전문이 있습니다. <https://www.gnu.org/licenses/>

"""유니티 빌드에서 사라진 '타입 정보' 를 게임의 DLL 로 되살립니다.

## 왜 필요한가

유니티는 배포 빌드에 **MonoBehaviour 의 타입 정보를 넣지 않습니다.** 엔진이
직접 아는 타입(Texture2D 등)은 괜찮지만, 게임이 직접 만든 스크립트는 무엇이
어떤 필드였는지가 파일에 안 적혀 있습니다. 그래서 그냥 열면 이렇게 나옵니다:

    MonoBehaviour 2710개 (타입정보 읽음 21, 못 읽음 2689)

대사는 대개 저 2689개 안에 있습니다. 21개만 읽고 '번역할 게 없다' 고 하면
사실은 다 못 본 겁니다.

## 어떻게 되살리나

타입 정보는 게임의 코드 안에 그대로 남아 있습니다.

    Mono 빌드   : <게임>_Data/Managed/*.dll
    IL2CPP 빌드 : GameAssembly.dll + il2cpp_data/Metadata/global-metadata.dat

여기서 클래스 구조를 읽어 타입 정보를 다시 만들어 주면 나머지 오브젝트가
전부 열립니다. ``TypeTreeGeneratorAPI`` 가 그 일을 합니다 — 없어도 프로그램은
그냥 예전처럼 동작하고, 있으면 훨씬 많이 찾습니다.
"""

from __future__ import annotations

from pathlib import Path

PACKAGE = "TypeTreeGeneratorAPI"

_cache: dict[str, object] = {}
_failed: set[str] = set()


def available() -> bool:
    try:
        import TypeTreeGeneratorAPI  # noqa: F401
    except ImportError:
        return False
    return True


def install_hint() -> str:
    return (f"MonoBehaviour 타입 정보를 되살리려면 {PACKAGE} 가 필요합니다.\n"
            f"    pip install {PACKAGE}\n"
            "설치하면 게임의 DLL 에서 구조를 읽어 훨씬 많은 문장을 찾습니다.")


def _sources(root: Path) -> tuple[Path | None, Path | None, Path | None]:
    """``(Managed 폴더, GameAssembly.dll, global-metadata.dat)``."""
    data = next((p for p in sorted(root.glob("*_Data")) if p.is_dir()), None)
    if data is None:
        data = root
    managed = data / "Managed"
    ga = root / "GameAssembly.dll"
    meta = data / "il2cpp_data" / "Metadata" / "global-metadata.dat"
    return (managed if managed.is_dir() else None,
            ga if ga.is_file() else None,
            meta if meta.is_file() else None)


def describe(root: Path) -> str:
    managed, ga, meta = _sources(Path(root))
    if managed:
        n = len(list(managed.glob("*.dll")))
        return f"Mono 빌드 · Managed/ 안 dll {n}개"
    if ga and meta:
        return "IL2CPP 빌드 · GameAssembly.dll + global-metadata.dat"
    return "타입 정보를 되살릴 코드 파일을 찾지 못했습니다"


def generator(root: Path, unity_version: str):
    """``env.typetree_generator`` 에 꽂을 물건. 못 만들면 ``None``."""
    root = Path(root)
    key = f"{root}|{unity_version}"
    if key in _cache:
        return _cache[key]
    if key in _failed or not unity_version:
        return None

    try:
        from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator
    except ImportError:
        _failed.add(key)
        return None

    managed, ga, meta = _sources(root)
    try:
        gen = TypeTreeGenerator(unity_version)
        if managed is not None:
            gen.load_local_dll_folder(str(managed))
        elif ga is not None and meta is not None:
            gen.load_il2cpp(ga.read_bytes(), meta.read_bytes())
        else:
            _failed.add(key)
            return None
    except Exception:                    # noqa: BLE001 - 없으면 그냥 예전처럼
        _failed.add(key)
        return None

    _cache[key] = gen
    return gen


class _Guarded:
    """실패가 이어지면 **스스로 물러나는** 타입 정보 생성기.

    ## 못 읽은 클래스를 기억합니다

    UnityPy 는 성공만 기억하고 실패는 안 기억합니다. 그래서 못 읽는 클래스가
    하나 있으면, 그 클래스를 쓰는 오브젝트가 5,000개일 때 .NET 을 5,000번
    다시 부릅니다. 매번 똑같이 실패하면서요. 한 번 실패한 클래스를 기억해
    두면 **파일 하나에 클래스 수십 종류만큼**으로 끝납니다.

    ## '몇 번 실패하면 손 뗀다' 는 넣지 마세요 — 될 것까지 막았습니다

    1.1.1 에 "한 번도 성공 못 한 채 40번 실패하면 물러난다" 를 넣었습니다.
    오브젝트마다 비용이 든다고 착각해서였습니다. 그런데 위의 기억하기가
    이미 비용을 없애 주므로 필요가 없었고, 오히려 해로웠습니다.

        level1   MonoBehaviour 5633개 — 물러남 ×4811   ← 시도조차 안 함
        level15  MonoBehaviour 3343개 — 읽음 528       ← 같은 게임인데 됨

    같은 게임에서도 파일에 따라 되는 클래스가 있는데, 앞의 40개가 실패했다는
    이유로 **뒤에 있는 TextMeshPro 폰트를 시도조차 안 했습니다.** 폰트를
    고치려면 그 구조를 읽어야 하는데, 그래서 "구조를 읽지 못했습니다" 가
    떴습니다. 되는 것까지 막은 셈입니다.

    ## .NET 이 직접 찍는 글은 막지 않습니다 — 막으려다 죽었습니다

    생성기는 클래스를 못 찾을 때마다 ``Error generating tree nodes:`` 를
    파이썬이 아니라 **.NET 쪽에서 직접** 화면에 찍습니다. 1.1.1 에서 이걸
    막으려고 운영체제 수준의 출력 통로(1·2번)를 잠깐 돌려놨는데,
    **큰 게임에서 프로그램이 통째로 죽었습니다.**

      · 타입 정보를 볼 때마다 실행됩니다. 오브젝트 38,242개면 통로 조작만
        30만 번입니다
      · 번역 작업과 화면 응답이 **동시에** 도는데, 한쪽이 통로를 바꿔치기
        하는 중에 다른 쪽이 그 통로를 쓰면 어긋납니다
      · 윈도우 콘솔에서 특히 불안정합니다

    그리고 **막을 필요도 없습니다.** 바로 아래 '못 읽은 클래스 기억하기' 가
    같은 일을 안전하게 합니다 — 클래스마다 한 번만 물어보므로 도배가 안
    생깁니다. 다시 넣지 마세요.
    """

    def __init__(self, gen):
        self._gen = gen
        self.ok = 0
        self.fails = 0
        # **안 되는 클래스를 기억합니다.** UnityPy 는 성공만 기억하고 실패는
        # 안 기억해서, 못 읽는 클래스가 나올 때마다 .NET 을 다시 부릅니다.
        # 같은 클래스의 오브젝트가 수천 개면 수천 번을 다시 부르는 셈이라,
        # 큰 게임에서 몇 분씩 멈춰 있는 것처럼 보입니다.
        self.bad: set = set()

    def __getattr__(self, name):
        return getattr(self._gen, name)

    def _call(self, fn, *a):
        if a in self.bad:
            raise RuntimeError("이미 못 읽은 클래스입니다")
        try:
            out = fn(*a)
        except Exception:
            self.bad.add(a)
            self.fails += 1
            raise
        self.ok += 1
        return out

    def get_nodes_up(self, assembly, fullname):
        return self._call(self._gen.get_nodes_up, assembly, fullname)

    def get_nodes(self, assembly, fullname):
        return self._call(self._gen.get_nodes, assembly, fullname)


def attach(env, root: Path, unity_version: str):
    """UnityPy 환경에 타입 정보 생성기를 붙입니다. 붙였으면 그 물건을."""
    gen = generator(root, unity_version)
    if gen is None:
        return None
    guard = _Guarded(gen)
    try:
        env.typetree_generator = guard
    except Exception:                    # noqa: BLE001
        return None
    return guard


def reset() -> None:
    _cache.clear()
    _failed.clear()
