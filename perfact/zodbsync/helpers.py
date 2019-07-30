# -*- coding: utf-8 -*-
import sys
import ast
import operator
import string

PY2 = (sys.version_info.major <= 2)
if not PY2:
    unicode = str


def to_string(value, enc='utf-8'):
    '''This method delivers bytes in python2 and unicode in python3.'''
    if isinstance(value, str):
        return value
    elif isinstance(value, unicode):
        return value.encode(enc)
    elif isinstance(value, bytes):
        return value.decode(enc)
    try:
        return str(value)
    except Exception:
        raise ValueError("could not convert '%s' to string!" % repr(value))


# Helper function to generate str from bytes (Python3 only)
def bytes_to_str(value, enc='utf-8'):
    if not PY2 and isinstance(value, bytes):
        return value.decode(enc, 'ignore')
    return value


def str_to_bytes(value, enc='utf-8'):
    if not PY2 and isinstance(value, str):
        return value.encode(enc)
    return value


# replacement mapping for str_repr
repl = {chr(i): '\\x{:02x}'.format(i) for i in range(32)}
# nicer formattings for some values
repl.update({'\n': '\\n', '\r': '\\r', '\t': '\\t'})
# make sure backslash is escaped first
repl = [('\\', '\\\\')] + sorted(repl.items())

'''
Test cases for the doctests so we have one less level of escaping to worry
about. There are different test cases that are accessed by index. Each test
case consists of an input to str_repr and the desired output.
TODO: Rewrite as unit test instead of doctest.
'''
str_repr_tests = [
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
    [b'test\xaa', "b'test\\xaa'" if PY2 else u"b'test\\xaa'"],

    [u'test\xaa', "u'test\\xaa'" if PY2 else u"'test\xaa'"],
]


def str_repr(val):
    '''
    Generic string representation of a value, used to serialize metadata.

    This function is similar to repr(), giving a string representation of val
    that can be stored to disk, read in again and fed into eval() in order to
    regain the data. It supports basic data types (None, bool, int, float),
    strings (both bytes and unicode) and lists and tuples (with contents that
    are themselves also supported). Other data types might also be supported
    since it falls back to using repr(), but then the elements might be handled
    differently (so if a dict contains string values, the special string
    handling will no longer be used).

    The most notable difference to repr() is the handling of strings. One of
    the use cases of zodbsync is the transition of a ZODB from Python 2/Zope2
    to Python 3/Zope4. Since simple string literals (like 'testü') mean
    something different in Python 2 than in Python 3 (bytes in the first case,
    unicode in the second), one might assume that the best representation would
    be to always use explicit literals (b'testü' or u'testü'). However,
    properties that were stored as bytes in Python 2 (like the titles of many
    objects) usually have become unicode in Python3. In a default PerFact
    installation, these properties were always *meant* to be UTF-8 encoded
    text. So the best representation should store strings (i.e., bytes arrays
    in Python 2 or unicode arrays in Python 3) without prefix and with as few
    escapes as possible (so no \\xc3\\xbc, but simply ü), since this allows to
    transfer the *meaning* rather than the *implementation details* from one
    Python version to another ('testü' is a bytes array in Python2 containing
    6 bytes that are meant to represent the given 5 characters, while it is a
    unicode array in Python 3, stored in memory in some irrelevant manner).
    The only characters being escaped are the unprintables (ASCII values 0-31),
    the backslash and, if necessary, the quoting character.

    This also ensures that recording metadata in Python 2, sending it through
    2to3, playing it back in Python 3 and recording it again will a) restore
    the correct meaning and b) create no diff for these properties in the 2to3
    step or in the re-recording step - at least for properties that were bytes
    in Python 2 and are unicode in Python 3.

    Properties that were already unicode in Python2 do also exist. For these,
    the recording would produce a literal like u'test\\xfc'. The 2to3 step
    would remove the prefix, resulting in 'test\\xfc'. Playing the property
    back to Python 3 would transport the correct value, but re-recording it
    would produce 'testü'.

    In order to remove both diffs, we encode all titles that are already
    unicode in Python 2 to give bytes (see zodbsync.py:mod_read). They are
    therefore recorded without a "u"-prefix and can be played back to both
    Python 2 and Python 3, since setting a unicode title to a value that is
    a bytes array automatically decodes the bytes array.

    >>> for item in str_repr_tests:
    ...     res = str_repr(item[0])
    ...     if res != item[1]:
    ...         print("input: %s" % item[0])
    ...         print("output: %s" % res)
    ...         print("expected: %s" % item[1])
    '''

    if isinstance(val, list):
        return '[%s]' % ', '.join(str_repr(item) for item in val)
    elif isinstance(val, tuple):
        fmt = '(%s,)' if len(val) == 1 else '(%s)'
        return fmt % ', '.join(str_repr(item) for item in val)

    if PY2 and isinstance(val, bytes):
        # fall back to repr if val is not valid UTF-8
        try:
            val.decode('utf-8')
        except UnicodeDecodeError:
            return 'b' + repr(val)

        for orig, r in repl:
            val = val.replace(orig, r)

        if ("'" in val) and not ('"' in val):
            return '"%s"' % val
        else:
            return "'%s'" % val.replace("'", "\\'")
    else:
        return repr(val)


def fix_encoding(data, encoding):
    '''Assume that strings in 'data' are encoded in 'encoding' and change
    them to unicode or utf-8.
    Only python 2!

    >>> example = [
    ...  ('id', 'body'),
    ...  ('owner', 'jan'),
    ...  ('props', [
    ...    [('id', 'msg_deleted'), ('type', 'string'),
    ...     ('value', 'Datens\xe4tze gel\xf6scht!')],
    ...    [('id', 'content_type'), ('type', 'string'),
    ...     ('value', 'text/html')],
    ...    [('id', 'height'), ('type', 'int'), ('value', 20)],
    ...    [('id', 'expand'), ('type', 'boolean'), ('value', 1)]]),
    ...  ('source', '<p>\\nIm Bereich Limitplanung '
    ...             +'sind die Pl\\xe4ne und Auswertungen '
    ...             +'zusammengefa\\xdft.\\n'),
    ...  ('title', 'Werteplan Monats\xfcbersicht'),
    ...  ('type', 'DTML Method'),
    ... ]
    >>> result = [ ('id', 'body'),
    ...            ('owner', 'jan'),
    ...            ('props',
    ...             [[('id', 'msg_deleted'),
    ...               ('type', 'string'),
    ...               ('value', 'Datens\xc3\xa4tze gel\xc3\xb6scht!')],
    ...              [('id', 'content_type'), ('type', 'string'),
    ...               ('value', 'text/html')],
    ...              [('id', 'height'), ('type', 'int'), ('value', 20)],
    ...              [('id', 'expand'), ('type', 'boolean'), ('value', 1)]]),
    ...            ('source',
    ...             '<p>\\nIm Bereich Limitplanung sind die Pl\xc3\xa4ne '
    ...             'und Auswertungen zusammengefa\xc3\x9ft.\\n'),
    ...            ('title', 'Werteplan Monats\xc3\xbcbersicht'),
    ...            ('type', 'DTML Method')]
    >>> if PY2 and fix_encoding(example, 'iso-8859-1') != result:
    ...     print("got:")
    ...     print(fix_encoding(example, 'iso-8859-1'))
    ...     print("expected:")
    ...     print(result)
    ... else:
    ...     True
    True

    '''
    assert PY2, "Not implemented for PY3 yet"
    unpacked = dict(data)
    if 'props' in unpacked:
        unpacked_props = [dict(a) for a in unpacked['props']]
        unpacked['props'] = unpacked_props

    # Skip some types
    skip_types = ['Image', ]
    if unpacked['type'] in skip_types:
        return data

    # Check source
    if 'source' in unpacked and isinstance(unpacked['source'], bytes):
        # Only these types use ustrings, all others stay binary
        ustring_types = [
            # 'Page Template',
            # 'Script (Python)',
        ]
        conversion = unpacked['source'].decode(encoding)
        if unpacked['type'] not in ustring_types:
            conversion = conversion.encode('utf-8')
        unpacked['source'] = conversion

    # Check title
    if 'title' in unpacked and isinstance(unpacked['title'], bytes):
        ustring_types = [
            'Page Template',
        ]
        conversion = unpacked['title'].decode(encoding)
        if unpacked['type'] not in ustring_types:
            conversion = conversion.encode('utf-8')
        unpacked['title'] = conversion

    # Check string properties
    if 'props' in unpacked:
        for prop in unpacked['props']:
            if prop['type'] == 'string':
                prop['value'] = (
                    str(prop['value']).decode(encoding).encode('utf-8')
                )

    if 'props' in unpacked:
        repacked_props = []
        for item in unpacked['props']:
            pack = list(item.items())
            pack.sort()
            repacked_props.append(pack)
        unpacked['props'] = repacked_props
    repacked = list(unpacked.items())
    repacked.sort()
    return repacked


# Functions copied from perfact.generic

def read_pdata(obj):
    '''Avoid authentication problems when reading linked pdata.'''
    if isinstance(obj.data, (bytes, unicode)):
        source = obj.data
    else:
        data = obj.data
        if isinstance(data.data, bytes):
            source = b''
        elif isinstance(data.data, str):
            source = ''
        while data is not None:
            source += data.data
            data = data.next
    return source


def simple_html_unquote(value):
    '''Unquote quoted HTML text (minimal version)'''
    tokens = [
        ('&lt;', '<',),
        ('&gt;', '>',),
        ('&quot;', '"',),
        ('&amp;', '&',),
    ]
    for before, after in tokens:
        value = value.replace(before, after)
    return value


def literal_eval(value):
    '''Literal evaluator (with a bit more power than PT).

    This evaluator is capable of parsing large data sets, and it has
    basic arithmetic operators included.
    '''
    _safe_names = {'None': None, 'True': True, 'False': False}
    if isinstance(value, (bytes, unicode)):
        value = ast.parse(value, mode='eval')

    bin_ops = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Mod: operator.mod,
        }

    unary_ops = {
        ast.USub: operator.neg,
    }

    def _convert(node):
        if isinstance(node, ast.Expression):
            return _convert(node.body)
        elif isinstance(node, ast.Str):
            return node.s
        elif isinstance(node, ast.Bytes):
            return node.s
        elif isinstance(node, ast.Num):
            return node.n
        elif isinstance(node, ast.Tuple):
            return tuple(map(_convert, node.elts))
        elif isinstance(node, ast.List):
            return list(map(_convert, node.elts))
        elif isinstance(node, ast.Dict):
            return dict((_convert(k), _convert(v)) for k, v
                        in zip(node.keys, node.values))
        elif isinstance(node, ast.Name):
            if node.id in _safe_names:
                return _safe_names[node.id]
        elif isinstance(node, ast.NameConstant):
            return node.value
        elif isinstance(node, ast.BinOp):
            return bin_ops[type(node.op)](
                _convert(node.left),
                _convert(node.right)
            )
        elif isinstance(node, ast.UnaryOp):
            return unary_ops[type(node.op)](_convert(node.operand))
        else:
            raise Exception('Unsupported type {}'.format(repr(node)))
    return _convert(value)


def cleanup_string(name,
                   valid_chars=string.printable,
                   replacement_char='_',
                   merge_replacements=True,
                   invalid_chars=''):
    '''Sanitize a name. Only valid_chars remain in the string.  Illegal
    characters are replaced with replacement_char. Adjacent
    replacements characters are merged if merge_replacements is True.

    '''
    out = ''
    merge = False
    for i in name:
        # Valid character? Add and continue.
        if (i in valid_chars and i not in invalid_chars):
            out += i
            merge = False
            continue

        # No replacements? No action.
        if not replacement_char:
            continue
        # In merge mode? No action.
        if merge:
            continue

        # Replace.
        out += replacement_char
        if merge_replacements:
            merge = True

    return out


def conserv_split(val, splitby='\n'):
    '''Split by a character, conserving it in the result.'''
    output = [a+splitby for a in val.split(splitby)]
    output[-1] = output[-1][:-len(splitby)]
    if output[-1] == '':
        output.pop()
    return output


# --- Function ported over from the Data.fs
def prop_dict(data):
    props = {}

    # Get the properties from object data
    p = dict(data).get('props', None)
    if not p:
        return props

    # Convert each property into a dictionary
    for item in p:
        pd = dict(item)
        # Extract only the value
        props[pd['id']] = pd['value']

    return props
