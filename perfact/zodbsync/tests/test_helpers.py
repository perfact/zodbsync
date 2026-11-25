# -*- coding: utf-8 -*-
import io
import os
import tarfile

import pytest

from .. import helpers


def test_remove_redundant_paths():
    """
    Check that redundant subpaths are actually removed
    """
    paths = [
        "/test",
        "/test/sub",
        "/another",
    ]
    target = [
        "/another",
        "/test",
    ]
    helpers.remove_redundant_paths(paths)
    assert paths == target


def test_remove_redundant_paths_only_real_subpaths():
    """
    Check that paths are only recognized as redundant if they are actually
    subpaths, not if the last path component starts with the other.
    """
    paths = ["/test", "/test2"]
    new_paths = paths[:]
    helpers.remove_redundant_paths(new_paths)
    assert paths == new_paths


def test_converters():
    """
    Several tests for to_* methods
    """
    for value in ["test", b"test"]:
        assert helpers.to_bytes(value) == b"test"
        assert helpers.to_string(value) == "test"
    assert helpers.to_string([1]) == "[1]"
    assert helpers.to_bytes([1]) == b"[1]"
    assert helpers.to_bytes(memoryview(b"test")) == b"test"


def test_StrRepr():
    """
    Check recursive version of str_repr with a typical configuration for what
    is split to occupy one line for each element, reproducing the shown
    formatting.
    """
    fmt = (
        """
[
    ('content', [
        'a',
        'b',
    ]),
    ('owner', (['acl_users'], 'admin')),
    ('perms', [
        ('View', False, [
            'Role_1',
            'Role_2',
        ]),
    ]),
    ('props', [
        [('id', 'columns'), ('type', 'tokens'), ('value', (
            'a',
            'b',
            'c',
        ))],
        [('id', 'other'), ('type', 'lines'), ('value', (
            'x',
            'y',
            'z',
        ))],
        [('id', 'scalar'), ('type', 'string'), ('value', 'test')],
    ]),
]
    """.strip()
        + "\n"
    )

    data = dict(helpers.literal_eval(fmt))
    rules = {
        "perms": [4],
        "props": [5],
    }
    assert fmt == helpers.StrRepr()(data, rules)


def test_StrReprLegacy():
    """
    Reproduce the shown formatting of StrRepr when using legacy mode
    """
    fmt = (
        """
[
    ('content', [
        'a',
        'b',
        ]),
    ('owner', (['acl_users'], 'admin')),
    ('perms', [('View', False, ['Role_1', 'Role_2'])]),
    ('props', [
        [('id', 'columns'), ('type', 'tokens'), ('value', ('a', 'b', 'c'))],
        [('id', 'other'), ('type', 'lines'), ('value', ('x', 'y', 'z'))],
        [('id', 'scalar'), ('type', 'string'), ('value', 'test')],
        ]),
]
    """.strip()
        + "\n"
    )
    data = dict(helpers.literal_eval(fmt))
    assert fmt == helpers.StrRepr()(data, legacy=True)


def test_literal_eval():
    tests = [
        ["b'test'", b"test"],
        ["{1: 2}", {1: 2}],
        ["[1, 2, 3]", [1, 2, 3]],
        ["None", None],
    ]
    for orig, compare in tests:
        assert helpers.literal_eval(orig) == compare
    assert helpers.literal_eval("1 + 2") == 3
    assert helpers.literal_eval("-True") == -1
    with pytest.raises(Exception):
        helpers.literal_eval("f(1)")


def test_path_diff():
    """Check that path_diff also handles cases where the last element is not
    the same in both lists."""
    old = [
        ("Abc", "1234"),
        ("Def", "afaf"),
        ("Xyz", "yzyz"),
    ]
    new = [
        ("Abc", "1234"),
        ("Def", "axax"),
        ("Yyy", "yzyz"),
    ]
    result = helpers.path_diff(old, new)
    assert result == {"Def", "Xyz", "Yyy"}


def build_tar(path, members, dirmode=0o755, filemode=0o644):
    """
    Create a tarball, where members maps the name inside the archive to the
    content of the file, using None for a directory. Members are added in the
    given order.
    """
    with tarfile.open(path, "w:gz") as tar:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            if content is None:
                info.type = tarfile.DIRTYPE
                info.mode = dirmode
                tar.addfile(info)
                continue
            data = content.encode("utf-8")
            info.size = len(data)
            info.mode = filemode
            tar.addfile(info, io.BytesIO(data))


def read_tree(path):
    """
    Return a dict mapping each path below path to the content of the file,
    using None for a directory.
    """
    result = {}
    for root, dirs, files in os.walk(path):
        for entry in dirs:
            result[os.path.relpath(f"{root}/{entry}", path)] = None
        for entry in files:
            with open(f"{root}/{entry}") as fh:
                result[os.path.relpath(f"{root}/{entry}", path)] = fh.read()
    return result


def test_unpack_tar(tmp_path):
    """
    Check that an archive is unpacked correctly, that superfluous elements in
    the target are removed and that files which are already correct are not
    written again.
    """
    archive = str(tmp_path / "a.tar.gz")
    target = str(tmp_path / "tgt")
    content = {".": None, "./sub": None, "./sub/a.txt": "A", "./b.txt": "B"}
    build_tar(archive, content)
    helpers.unpack_tar(archive, target)
    assert read_tree(target) == {"sub": None, "sub/a.txt": "A", "b.txt": "B"}

    # Superfluous file, directory and content that is no longer part of the
    # archive
    os.makedirs(f"{target}/stale/deeper")
    with open(f"{target}/stale/deeper/x.txt", "w") as fh:
        fh.write("x")
    with open(f"{target}/sub/extra.txt", "w") as fh:
        fh.write("x")
    unchanged = os.stat(f"{target}/sub/a.txt").st_mtime_ns

    content["./b.txt"] = "B2"
    build_tar(archive, content)
    helpers.unpack_tar(archive, target)
    assert read_tree(target) == {"sub": None, "sub/a.txt": "A", "b.txt": "B2"}
    assert os.stat(f"{target}/sub/a.txt").st_mtime_ns == unchanged


def test_unpack_tar_unordered(tmp_path):
    """
    Check that a directory may be listed after its own content and that it
    replaces a file that is currently in the way, and vice versa.
    """
    archive = str(tmp_path / "a.tar.gz")
    target = str(tmp_path / "tgt")
    os.makedirs(target)
    with open(f"{target}/x", "w") as fh:
        fh.write("in the way")

    build_tar(archive, {"z.txt": "Z", "x/y.txt": "Y", "x": None})
    helpers.unpack_tar(archive, target)
    assert read_tree(target) == {"x": None, "x/y.txt": "Y", "z.txt": "Z"}

    build_tar(archive, {"z.txt": "Z", "x": "now a file"})
    helpers.unpack_tar(archive, target)
    assert read_tree(target) == {"x": "now a file", "z.txt": "Z"}


def test_unpack_tar_modes(tmp_path):
    """
    Check that the permissions of the archive are applied regardless of the
    umask, including the setgid bit and the permissions of the extraction root
    itself, which the sources use to yield a group repository.
    """
    archive = str(tmp_path / "a.tar.gz")
    target = str(tmp_path / "tgt")
    content = {".": None, "./sub": None, "./sub/a.txt": "A"}
    build_tar(archive, content, dirmode=0o2775, filemode=0o664)

    orig_umask = os.umask(0o022)
    try:
        helpers.unpack_tar(archive, target)
        modes = {
            path: os.stat(f"{target}/{path}").st_mode & 0o7777
            for path in [".", "sub", "sub/a.txt"]
        }
        assert modes == {".": 0o2775, "sub": 0o2775, "sub/a.txt": 0o664}

        # A mode-only change is corrected as well, without rewriting the file
        os.chmod(f"{target}/sub", 0o755)
        os.chmod(f"{target}/sub/a.txt", 0o644)
        unchanged = os.stat(f"{target}/sub/a.txt").st_mtime_ns
        helpers.unpack_tar(archive, target)
        assert os.stat(f"{target}/sub").st_mode & 0o7777 == 0o2775
        assert os.stat(f"{target}/sub/a.txt").st_mode & 0o7777 == 0o664
        assert os.stat(f"{target}/sub/a.txt").st_mtime_ns == unchanged
    finally:
        os.umask(orig_umask)
