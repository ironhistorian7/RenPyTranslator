"""Resolve flat patch members to unambiguous lower-priority source paths."""
from collections.abc import Iterable

from kirikiri_patch_paths import PatchPathError, _path


def canonical_patch_member(member: str, source_names: Iterable[str], flat: bool = True) -> str:
    member = _path(member)
    if not flat or '/' in member:
        return member
    matches = {}
    for name in source_names:
        name = _path(name)
        if name.rsplit('/', 1)[-1].casefold() == member.casefold():
            matches.setdefault(name.casefold(), name)
    if len(matches) > 1:
        raise PatchPathError(
            f"Ambiguous patch member {member!r}: {', '.join(repr(n) for n in matches.values())}",
            'kirikiri_patch_conflict',
        )
    return next(iter(matches.values()), member)
