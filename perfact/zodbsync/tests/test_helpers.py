# -*- coding: utf-8 -*-
import pytest

from .. import benchmark, helpers, object_mixins


class _DummyFolder:
    def __init__(self):
        self._children = {}
        self.manage_addProduct = {
            "OFSP": _DummyFolderManager(self),
            "PageTemplates": _UnsupportedManager(),
            "PythonScripts": _UnsupportedManager(),
            "ZSQLMethods": _DummySqlMethodManager(self),
        }

    def __getattr__(self, name):
        try:
            return self._children[name]
        except KeyError:
            raise AttributeError(name)


class _DummyFolderManager:
    def __init__(self, parent):
        self.parent = parent

    def manage_addFolder(self, id):
        self.parent._children[id] = _DummyFolder()


class _UnsupportedManager:
    def __getattr__(self, name):
        raise AssertionError("Leaf object manager should not be used")


class _DummySqlMethodManager:
    def __init__(self, parent):
        self.parent = parent

    def manage_addZSQLMethod(self, id, title, connection_id, arguments, template):
        self.parent._children[id] = {
            "id": id,
            "title": title,
            "connection_id": connection_id,
            "arguments": arguments,
            "template": template,
        }


class _PermissionProbe:
    def __init__(self):
        self._owner = (["acl_users"], "perfact")
        self.roles_added = []
        self.roles_deleted = []
        self.local_roles_deleted = []
        self.local_roles_set = []

    def userdefined_roles(self):
        return ()

    def get_local_roles(self):
        return ()

    def _addRole(self, role):
        self.roles_added.append(role)

    def _delRoles(self, roles):
        self.roles_deleted.append(tuple(roles))

    def manage_delLocalRoles(self, users):
        self.local_roles_deleted.append(tuple(users))

    def manage_setLocalRoles(self, user, roles):
        self.local_roles_set.append((user, tuple(roles)))

    def ac_inherited_permissions(self, _all):
        raise AssertionError("Permission reset path should be skipped")


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


def test_populate_dataset_folders_only():
    root = _DummyFolder()
    stats = benchmark.populate_dataset(
        app=root,
        depth=3,
        breadth=2,
        blobs_per_folder=0,
        blob_size=4096,
        object_type="folders",
    )

    assert stats == {
        "folders": 14,
        "page_templates": 0,
        "python_scripts": 0,
        "sql_methods": 0,
        "payload_bytes": 4096,
    }


def test_populate_dataset_sql_methods():
    root = _DummyFolder()
    stats = benchmark.populate_dataset(
        app=root,
        depth=2,
        breadth=2,
        blobs_per_folder=3,
        blob_size=16,
        object_type="sql_method",
    )

    folder = root._children["f0_0"]
    created = folder._children["sql_0_0_0"]

    assert created["connection_id"] == "benchmark_db"
    assert created["arguments"] == ""
    assert "SELECT '" in created["template"]
    assert stats == {
        "folders": 6,
        "page_templates": 0,
        "python_scripts": 0,
        "sql_methods": 18,
        "payload_bytes": 16,
    }


def test_accesscontrol_write_skips_default_reset_on_create():
    obj = _PermissionProbe()

    object_mixins.AccessControlObj.write(
        obj,
        {
            "_created": True,
        },
    )

    assert obj.roles_added == []
    assert obj.roles_deleted == []
    assert obj.local_roles_deleted == []
    assert obj.local_roles_set == []
