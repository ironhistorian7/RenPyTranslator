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

"""어떤 종류의 게임 폴더인지, 무엇을 열어야 하는지."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from . import asar, prefixed
from .engines import renpy, rpgmaker, tyrano, wolf

ASSET_SUFFIXES = {".assets", ".bundle", ".unity3d", ".ab", ".resource", ".sharedassets"}
ASSET_NAMES_EXACT = {"resources.assets", "globalgamemanagers", "level0"}
LOOSE_SUFFIXES = {".json", ".csv", ".tsv", ".txt", ".xml", ".yaml", ".yml"}
SKIP_DIRS = {"MonoBleedingEdge", "Mono", "__gameloc_backup__"}


@dataclasses.dataclass
class GameInfo:
    root: Path
    engine: str
    data_dir: Path | None = None
    unity_version: str = ""
    scripting_backend: str = ""
    asset_files: list[Path] = dataclasses.field(default_factory=list)
    loose_files: list[Path] = dataclasses.field(default_factory=list)
    rm_base: Path | None = None
    rm_variant: str = ""
    rp_game: Path | None = None         # Ren'Py 의 game 폴더
    ty_base: Path | None = None         # TyranoScript 의 data 를 담은 폴더
    electron_asar: Path | None = None   # 아직 안 푼 app.asar
    electron_app: Path | None = None    # 풀어놓은 resources/app

    def summary(self) -> str:
        if self.engine == "electron-packed":
            return ("엔진: Electron | 내용물이 resources/app.asar 안에 묶여 있습니다. "
                    "먼저 풀어야 합니다.")
        head = "Electron 안 · " if self.electron_app else ""
        if self.engine == "rpgmaker":
            return head + (f"엔진: RPG Maker {self.rm_variant} (NW.js) | "
                           f"data 폴더 JSON {len(self.asset_files)}개")
        if self.engine == "tyrano":
            ver = f" {self.unity_version}" if self.unity_version else ""
            return head + (f"엔진: TyranoScript{ver} | 시나리오 파일 "
                           f"{len(self.asset_files)}개")
        if self.engine == "renpy":
            ver = f" {self.unity_version}" if self.unity_version else ""
            return head + (f"엔진: Ren'Py{ver} | 스크립트 파일 "
                           f"{len(self.asset_files)}개 (원본은 고치지 않습니다)")
        if self.engine == "wolf":
            packed = any(p.suffix.lower() == ".wolf" for p in self.asset_files)
            return head + ("엔진: WOLF RPG 에디터 | "
                           + ("자료가 .wolf 묶음으로 되어 있습니다(적용할 때 글자 든 묶음만 풉니다)"
                              if packed else f"데이터 파일 {len(self.asset_files)}개"))
        if self.engine == "unknown":
            return (head + "엔진: 아직 모름 | "
                    f"읽을 수 있는 텍스트 파일 {len(self.loose_files)}개")
        return (
            head + f"엔진: {self.engine}"
            + (f" {self.unity_version}" if self.unity_version else "")
            + (f" ({self.scripting_backend})" if self.scripting_backend else "")
            + f" | 에셋 파일 {len(self.asset_files)}개,"
              f" 외부 텍스트 파일 {len(self.loose_files)}개"
        )


def _is_asset_file(p: Path) -> bool:
    n = p.name.lower()
    if n in ASSET_NAMES_EXACT or p.suffix.lower() in ASSET_SUFFIXES:
        return True
    return bool(re.fullmatch(r"level\d+", n))


def detect(root: Path) -> GameInfo:
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"폴더가 아닙니다: {root}")

    # --- Electron -------------------------------------------------------
    # 내용물이 resources/app.asar 한 덩어리에 들어 있습니다. 풀어놓으면
    # Electron 이 resources/app/ 폴더를 대신 읽으므로, 푼 뒤에는 평범한
    # 파일 취급을 할 수 있습니다.
    res = root / "resources"
    app_dir, asar_path = res / "app", res / "app.asar"
    if app_dir.is_dir() and (app_dir / "package.json").is_file():
        info = detect(app_dir)          # 안쪽 엔진으로 다시 판별
        info.electron_app = app_dir
        info.electron_asar = asar_path if asar_path.exists() else None
        return info
    if asar_path.is_file() and asar.is_asar(asar_path):
        info = GameInfo(root=root, engine="electron-packed")
        info.electron_asar = asar_path
        return info

    ty_base = tyrano.is_tyrano(root)
    if ty_base is not None:
        info = GameInfo(root=root, engine="tyrano", ty_base=ty_base,
                        data_dir=ty_base / "data")
        info.unity_version = tyrano.version(ty_base)
        info.asset_files = tyrano.scenario_files(ty_base)
        return info

    rp_game = renpy.is_renpy(root)
    if rp_game is not None:
        info = GameInfo(root=root, engine="renpy", rp_game=rp_game,
                        data_dir=rp_game)
        info.unity_version = renpy.version(rp_game)
        info.asset_files = sorted(
            [p for p in rp_game.rglob("*.rpy") if "tl" not in p.parts]
            + list(rp_game.rglob("*.rpa"))
            + [p for p in rp_game.rglob("*.rpyc") if "tl" not in p.parts])
        return info

    rm_base = rpgmaker.is_rpgmaker(root)
    if rm_base is not None:
        info = GameInfo(root=root, engine="rpgmaker", rm_base=rm_base,
                        data_dir=rm_base / "data",
                        rm_variant=rpgmaker.variant(rm_base))
        info.asset_files = rpgmaker.data_files(rm_base)
        return info

    wolf_root = wolf.is_wolf(root)
    if wolf_root is not None:
        info = GameInfo(root=root, engine="wolf", data_dir=root / "Data")
        info.asset_files = wolf.text_files_hint(root)
        return info

    data_dir = None
    for cand in sorted(root.glob("*_Data")):
        if cand.is_dir():
            data_dir = cand
            break
    if data_dir is None and (root / "globalgamemanagers").exists():
        data_dir = root

    if data_dir is None:
        info = GameInfo(root=root, engine="unknown")
        info.loose_files = _collect_loose(root)
        return info

    info = GameInfo(root=root, engine="unity", data_dir=data_dir)
    info.unity_version = _unity_version(data_dir)
    info.scripting_backend = _backend(data_dir)
    info.asset_files = _collect_assets(data_dir)
    info.loose_files = _collect_loose(data_dir / "StreamingAssets")
    return info


def _collect_assets(data_dir: Path) -> list[Path]:
    out = []
    for p in sorted(data_dir.rglob("*")):
        if not p.is_file() or any(part in SKIP_DIRS for part in p.parts):
            continue
        # .resS / .resource 는 오브젝트가 없는 덩어리 데이터(소리·그림)입니다.
        # 열어봐야 문자열이 없고 시간만 오래 걸립니다.
        if p.suffix.lower() in (".ress", ".resource"):
            continue
        if _is_asset_file(p):
            out.append(p)
        elif _is_prefixed_bundle(p):
            out.append(p)
    return out


def _is_prefixed_bundle(p: Path) -> bool:
    """``StreamingAssets`` 안의, 앞에 머리말이 붙은 유니티 덩이. 머리말 없는 파일은 안 봅니다."""
    if "StreamingAssets" not in p.parts or p.suffix.lower() in LOOSE_SUFFIXES:
        return False
    return prefixed.offset(p) > 0


# Addressables 의 **주소록** 파일들. '어느 파일 어디에 무엇이 있다' 만 적힌
# 기계용 목록이라 사람이 읽을 글자가 하나도 없습니다. 그런데 그 안의 값들이
# ("Color Gradient Presets/Blue to Purple") 문구처럼 생겨서 번역 대상으로
# 뽑히고, 번역해 넣으면 게임이 파일을 통째로 못 찾습니다.
_ADDRESSABLES_MACHINERY = re.compile(
    r"^(catalog.*\.(json|bin|hash)|settings\.json|link\.xml|"
    r"AddressablesLink.*|.*\.hash)$", re.I)


def _is_machinery(p: Path) -> bool:
    """건드리면 안 되는 기계용 파일인가."""
    if _ADDRESSABLES_MACHINERY.match(p.name):
        return True
    # StreamingAssets/aa/ 아래는 통째로 Addressables 살림입니다.
    return any(part == "aa" for part in p.parts[:-1]) and p.suffix.lower() in (
        ".json", ".bin", ".hash", ".xml")


def _collect_loose(folder: Path) -> list[Path]:
    if not folder or not folder.is_dir():
        return []
    return [p for p in sorted(folder.rglob("*"))
            if p.is_file() and p.suffix.lower() in LOOSE_SUFFIXES
            and not _is_machinery(p)
            and p.stat().st_size <= 32 * 1024 * 1024]


def _unity_version(data_dir: Path) -> str:
    for target in (data_dir / "globalgamemanagers", data_dir / "data.unity3d"):
        if not target.exists():
            continue
        try:
            head = target.read_bytes()[:4096]
        except OSError:
            continue
        m = re.search(rb"(\d+\.\d+\.\d+[fpab]\d+)", head)
        if m:
            return m.group(1).decode("ascii", "replace")
    return ""


def _backend(data_dir: Path) -> str:
    if (data_dir / "il2cpp_data").is_dir():
        return "IL2CPP"
    if (data_dir / "Managed" / "Assembly-CSharp.dll").exists():
        return "Mono"
    return ""
