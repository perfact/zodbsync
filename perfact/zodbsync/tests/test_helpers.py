# -*- coding: utf-8 -*-
import six
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
    for value in ['test', b'test', u'test']:
        assert helpers.to_bytes(value) == b'test'
        assert helpers.to_string(value) == 'test'
        assert helpers.to_ustring(value) == u'test'
    assert helpers.to_string([1]) == '[1]'
    assert helpers.to_ustring([1]) == u'[1]'
    assert helpers.to_bytes([1]) == b'[1]'
    assert helpers.to_bytes(memoryview(b'test')) == b'test'


def test_str_repr():
    """
    Check different inputs for str_repr against expected outputs
    """
    tests = [
        ['äöüßáéí\n\r\t',
         "'äöüßáéí\\n\\r\\t'"],

        ['args="1, 2, 3"',
         "'args=\"1, 2, 3\"'"],

        [True, 'True'],

        [45, '45'],

        [34.5, '34.5'],

        [('args', 'id=None, tn=False, streaming=True'),
         "('args', 'id=None, tn=False, streaming=True')"],

        ["'", '"' + "'" + '"'],  # "'"
        ['"', "'" + '"' + "'"],  # '"'
        ["'" + '"', "'\\'\"'"],  # '\'"'

        # try a byte that is not valid UTF-8
        [b'test\xaa', "b'test\\xaa'" if six.PY2 else u"b'test\\xaa'"],

        [u'test\xaa', "u'test\\xaa'" if six.PY2 else u"'test\xaa'"],
    ]
    for orig, compare in tests:
        assert helpers.str_repr(orig) == compare


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


def test_fix_encoding():
    example = {
        'id': 'body',
        'owner': 'jan',
        'props': [
            [('id', 'msg_deleted'), ('type', 'string'),
             ('value', b'Datens\xe4tze gel\xf6scht!')],
            [('id', 'content_type'), ('type', 'string'),
             ('value', 'text/html')],
            [('id', 'height'), ('type', 'int'), ('value', 20)],
            [('id', 'expand'), ('type', 'boolean'), ('value', 1)]
        ],
        'source': (
            b'<p>\nIm Bereich Limitplanung '
            b'sind die Pl\xe4ne und Auswertungen '
            b'zusammengefa\xdft.\n'
        ),
        'title': b'Werteplan Monats\xfcbersicht',
        'type': 'DTML Method',
    }
    result = {
        'id': 'body',
        'owner': 'jan',
        'props': [
            [('id', 'msg_deleted'),
             ('type', 'string'),
             ('value', b'Datens\xc3\xa4tze gel\xc3\xb6scht!')],
            [('id', 'content_type'), ('type', 'string'),
             ('value', 'text/html')],
            [('id', 'height'), ('type', 'int'), ('value', 20)],
            [('id', 'expand'), ('type', 'boolean'), ('value', 1)]
        ],
        'source': (
            b'<p>\nIm Bereich Limitplanung sind die Pl\xc3\xa4ne '
            b'und Auswertungen zusammengefa\xc3\x9ft.\n'
        ),
        'title': b'Werteplan Monats\xc3\xbcbersicht',
        'type': 'DTML Method',
    }
    assert six.PY3 or helpers.fix_encoding(example, 'iso-8859-1') == result
