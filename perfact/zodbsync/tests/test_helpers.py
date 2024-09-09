# -*- coding: utf-8 -*-
import pytest

from .. import helpers


def test_remove_redundant_paths():
    """
    Check that redundant subpaths are actually removed
    """
    paths = [
        '/test',
        '/test/sub',
        '/another',
    ]
    target = [
        '/another',
        '/test',
    ]
    helpers.remove_redundant_paths(paths)
    assert paths == target


def test_remove_redundant_paths_only_real_subpaths():
    """
    Check that paths are only recognized as redundant if they are actually
    subpaths, not if the last path component starts with the other.
    """
    paths = ['/test', '/test2']
    new_paths = paths[:]
    helpers.remove_redundant_paths(new_paths)
    assert paths == new_paths


def test_converters():
    """
    Several tests for to_* methods
    """
    for value in ['test', b'test']:
        assert helpers.to_bytes(value) == b'test'
        assert helpers.to_string(value) == 'test'
    assert helpers.to_string([1]) == '[1]'
    assert helpers.to_bytes([1]) == b'[1]'
    assert helpers.to_bytes(memoryview(b'test')) == b'test'


def test_StrRepr():
    """
    Check recursive version of str_repr with a typical configuration for what
    is split to occupy one line for each element, reproducing the shown
    formatting.
    """
    fmt = """
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
    """.strip() + '\n'

    data = dict(helpers.literal_eval(fmt))
    rules = {
        'perms': [4],
        'props': [5],
    }
    assert fmt == helpers.StrRepr()(data, rules)


def test_StrReprLegacy():
    """
    Reproduce the shown formatting of StrRepr when using legacy mode
    """
    fmt = """
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
    """.strip() + '\n'
    data = dict(helpers.literal_eval(fmt))
    assert fmt == helpers.StrRepr()(data, legacy=True)


def test_literal_eval():
    tests = [
        ["b'test'", b'test'],
        ["{1: 2}", {1: 2}],
        ["[1, 2, 3]", [1, 2, 3]],
        ["None", None],
    ]
    for orig, compare in tests:
        assert helpers.literal_eval(orig) == compare
    assert helpers.literal_eval("1 + 2") == 3
    assert helpers.literal_eval("-True") == -1
    with pytest.raises(Exception):
        helpers.literal_eval('f(1)')


def test_path_diff():
    """Check that path_diff also handles cases where the last element is not
    the same in both lists."""
    old = [
        ('Abc', '1234'),
        ('Def', 'afaf'),
        ('Xyz', 'yzyz'),
    ]
    new = [
        ('Abc', '1234'),
        ('Def', 'axax'),
        ('Yyy', 'yzyz'),
    ]
    result = helpers.path_diff(old, new)
    assert result == {'Def', 'Xyz', 'Yyy'}
