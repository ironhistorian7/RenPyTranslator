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

"""무엇을 찾았고, 어디 있고, 어떻게 되돌려 넣는지."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_VERSION = 2


@dataclasses.dataclass
class Location:
    """문자열이 실제로 들어 있는 한 자리. 되돌려 쓸 수 있을 만큼 정확해야 합니다."""

    file: str          # 게임 폴더 기준 상대 경로
    kind: str          # "asset"(유니티 직렬화 파일 안) | "loose" | "jsliteral"
    fmt: str           # "typetree" | "json" | "csv" | "lines" | "raw" | "rmplugins"
    ptr: str           # 파싱된 컨테이너 안의 경로 ("" = 파일 전체)
    path_id: int | None = None   # 유니티 오브젝트 path_id (kind == "asset")
    asset: str = ""              # 사람이 알아볼 이름
    # 한 문단이 여러 칸에 나뉘어 저장된 경우(RPG Maker 는 메시지창 한 줄을
    # 명령 하나로 저장합니다) 그 ptr 들을 순서대로. ``ptr`` 은 span[0].
    span: list[str] | None = None
    # 번들 하나에 유니티 직렬화 파일이 여러 개 든 경우(data.unity3d 안의 level0~level25)
    # 그 파일의 이름. ``path_id`` 는 **파일마다 따로 매겨져** 파일이 다르면 같은 번호가
    # 다른 오브젝트를 가리킵니다. 비어 있으면(대부분의 게임) 예전처럼 path_id 만 봅니다.
    serial: str = ""
    # DLL 안에 박힌 글자(``kind == "dll"``)는 **같은 자리에 같거나 짧게만** 넣을 수
    # 있습니다. 원본 문자열이 차지하던 최대 글자 수(UTF-16 낱자 수)를 뽑을 때 같이
    # 적어 둡니다 — 화면에서 "몇 글자까지 되는지" 미리 보여 주려는 용도입니다.
    max_chars: int | None = None

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        if not d.get("span"):
            d.pop("span", None)
        if not d.get("serial"):
            d.pop("serial", None)
        if d.get("max_chars") is None:
            d.pop("max_chars", None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Location":
        return cls(**d)

    def label(self) -> str:
        head = f"{self.file}"
        if self.asset:
            head += f" › {self.asset}"
        return f"{head} › {self.ptr}" if self.ptr else head


@dataclasses.dataclass
class Entry:
    """고유한 원문 하나와, 그것이 등장하는 모든 자리."""

    id: str
    text: str
    locations: list[Location] = dataclasses.field(default_factory=list)
    translations: dict[str, str] = dataclasses.field(default_factory=dict)
    # 기계번역이라 아직 사람이 못 본 언어들. 손으로 고치면 그 언어가 빠집니다.
    machine: list[str] = dataclasses.field(default_factory=list)
    note: str = ""

    @property
    def count(self) -> int:
        return len(self.locations)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "locations": [l.to_dict() for l in self.locations],
            "translations": self.translations,
            "machine": self.machine,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Entry":
        return cls(
            id=d["id"],
            text=d["text"],
            locations=[Location.from_dict(x) for x in d["locations"]],
            translations=d.get("translations", {}),
            machine=d.get("machine", []),
            note=d.get("note", ""),
        )


def make_id(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=5).hexdigest()


def make_id_unique(text: str, loc: Location) -> str:
    seed = f"{text}\x00{loc.file}\x00{loc.path_id}\x00{loc.ptr}"
    if loc.serial:
        seed += f"\x00{loc.serial}"        # 없으면 예전 ID 와 똑같이
    return hashlib.blake2b(seed.encode("utf-8"), digest_size=5).hexdigest()


@dataclasses.dataclass
class Project:
    game_root: str
    engine: str = "unity"
    unity_version: str = ""
    source_lang: str = "auto"
    languages: list[str] = dataclasses.field(default_factory=lambda: ["ko"])
    entries: list[Entry] = dataclasses.field(default_factory=list)
    stats: dict[str, Any] = dataclasses.field(default_factory=dict)
    # 게임이 이름으로 참조하는 문자열들. 번역해서 넣으면 안 됩니다.
    identifiers: list[str] = dataclasses.field(default_factory=list)
    generated_at: str = ""

    def save(self, path: Path) -> None:
        self.generated_at = self.generated_at or datetime.now(timezone.utc).isoformat()
        payload = {
            "project_version": PROJECT_VERSION,
            "game_root": self.game_root,
            "engine": self.engine,
            "unity_version": self.unity_version,
            "source_lang": self.source_lang,
            "languages": self.languages,
            "stats": self.stats,
            "identifiers": self.identifiers,
            "generated_at": self.generated_at,
            "entries": [e.to_dict() for e in self.entries],
        }
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=1), "utf-8")

    @classmethod
    def load(cls, path: Path) -> "Project":
        d = json.loads(Path(path).read_text("utf-8"))
        if d.get("project_version", 0) > PROJECT_VERSION:
            raise ValueError("이 프로젝트 파일은 더 최신 버전의 도구로 만들어졌습니다.")
        return cls(
            game_root=d["game_root"],
            engine=d.get("engine", "unity"),
            unity_version=d.get("unity_version", ""),
            source_lang=d.get("source_lang", "auto"),
            languages=d.get("languages", ["ko"]),
            entries=[Entry.from_dict(x) for x in d["entries"]],
            stats=d.get("stats", {}),
            identifiers=d.get("identifiers", []),
            generated_at=d.get("generated_at", ""),
        )
