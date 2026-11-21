import unittest

from kirikiri_patch_paths import PatchPathError, inspect_patch_layout, plan_patch_members, validate_member_map


def test_flat_and_hierarchical_plans():
    source = {"scn/intro.ks": b"1", "other/end.scn": b"2"}
    files, mapping = plan_patch_members(source)
    assert files == {"intro.ks": b"1", "end.scn": b"2"}
    assert mapping == {"intro.ks": "scn/intro.ks", "end.scn": "other/end.scn"}
    assert validate_member_map(list(files), mapping, "flat") == mapping
    files, mapping = plan_patch_members(source, "hierarchical")
    assert files == source
    assert validate_member_map(list(files), mapping, "hierarchical") == {k: k for k in source}


def test_case_insensitive_collision_rejected_even_when_content_same():
    for payload in (b"same", b"different"):
        with unittest.TestCase().assertRaises(PatchPathError) as exc:
            plan_patch_members({"one/Story.ks": b"same", "two/story.ks": payload})
        assert exc.exception.error_code == "kirikiri_patch_conflict"
        assert "one/Story.ks" in str(exc.exception) and "two/story.ks" in str(exc.exception)


def test_unsafe_path_rejected():
    for path in ("../a.ks", "/a.ks", "C:/a.ks", "a\\b.ks", "a//b.ks", "a/./b.ks", "con.txt"):
        with unittest.TestCase().assertRaises(PatchPathError):
            plan_patch_members({path: b"x"})


def test_legacy_and_invalid_maps():
    assert validate_member_map(["dir/a.ks"], None) == {"dir/a.ks": "dir/a.ks"}
    for files, mapping in [
        (["a.ks", "A.ks"], None),
        (["a.ks"], {"a.ks": "../a.ks"}),
        (["a.ks"], {"a.ks": "b.ks"}),
        (["a.ks", "b.ks"], {"a.ks": "x/a.ks"}),
        (["a.ks"], {"A.ks": "x/A.ks"}),
    ]:
        with unittest.TestCase().assertRaises(PatchPathError):
            validate_member_map(files, mapping)


def test_static_boot_root_and_subdir():
    boot = b'''function loadArchive(name) {
        var arcname = name + ".xp3";
        if (Storages.isExistentStorage(arcname)) {
            Storages.addAutoPath(arcname + ">");
        }
    }
    loadArchive("patch.xp3");'''
    info = inspect_patch_layout({"system/boot.tjs": boot})
    assert info["layout"] == "flat" and info["verified"]
    assert info["evidence"] == ["system/boot.tjs:4: addAutoPath (flat)"]
    boot = boot.replace(b'arcname + ">"', b'arcname + ">scenario/"')
    assert inspect_patch_layout({"system/boot.tjs": boot})["layout"] == "hierarchical"


def test_unknown_and_comments_are_not_evidence():
    for boot in (b'// Storages.addAutoPath("patch.xp3>");', b'Storages.addAutoPath("data.xp3>");'):
        info = inspect_patch_layout({"system/boot.tjs": boot})
        assert info["layout"] == "flat" and not info["verified"]


def test_utf16_and_root_with_subdirectory():
    boot = '''function useArchiveIfExists(name) {
        with(Storages) name=.chopStorageExt(.extractStorageName(name));
        var arcname=System.exePath+name+".xp3";
        if(Storages.isExistentStorage(arcname)) {
            archive_exists[name.toLowerCase()]=true;
            Storages.addAutoPath(arcname+">");
        }
    }
    useArchiveIfExists("patch.xp3");
    Storages.addAutoPath("patch.xp3>scenario/");'''
    for encoding in ("utf-16", "utf-16-be"):
        blob = boot.encode(encoding)
        if encoding == "utf-16-be":
            blob = b"\xfe\xff" + blob
        info = inspect_patch_layout({"system/Initialize.tjs": blob})
        assert info["layout"] == "hierarchical" and info["verified"]
        assert len(info["evidence"]) == 2


class PatchPathsTests(unittest.TestCase):
    test_flat_and_hierarchical_plans = staticmethod(test_flat_and_hierarchical_plans)
    test_case_insensitive_collision = staticmethod(test_case_insensitive_collision_rejected_even_when_content_same)
    test_unsafe_path = staticmethod(test_unsafe_path_rejected)
    test_legacy_and_invalid_maps = staticmethod(test_legacy_and_invalid_maps)
    test_static_boot_root_and_subdir = staticmethod(test_static_boot_root_and_subdir)
    test_unknown_and_comments = staticmethod(test_unknown_and_comments_are_not_evidence)
    test_utf16_and_root_with_subdirectory = staticmethod(test_utf16_and_root_with_subdirectory)
