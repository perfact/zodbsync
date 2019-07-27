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


# replacement mapping
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
]


def str_repr(val):
    '''
    Generic string representation of a value, used to serialize metadata.
    This supports
    a) strings (bytes in python 2 or unicode in python 3)
    b) other basic types (boolean, None, integer, float)
    c) lists and tuples with elements of a)-c)

    One might assume that a most stringent representation would always prefix
    string values with either b or u to denote bytes or unicode. However,
    properties that were stored as bytes in Python2 (like many titles) usually
    have become unicode in Python3. In a default PerFact installation, these
    properties were always *meant* to be UTF-8 encoded text. So the best
    representation is to store them without prefix and with as few escapes as
    possible (so no \xc3\xbc, but simply ü). The only characters that need to
    be escaped are unprintables, white space, backslash and the quoting
    character. To keep the diff to older versions smaller, we also check if
    there is a ' but no " inside, switching the enclosing quotation marks.

    Bytes properties in Python 3 do not seem to exist, but they would be
    recorded as b'...'. If playing this back in Python 2, it would work, but
    re-recording it would change the recording. However, this is not a scenario
    we want to cover.

    Unicode properties in Python 2, on the other hand, do exist (some titles).
    If they were recorded as-is, they would give u'...'. If played back like
    that to Python2 or Python3, they would give the correct result. However, if
    they were played back to Python 3 and then re-recorded, they would create a
    diff.
    Instead, at least all title properties are converted to strings (which in
    Python 2 are bytes) before being recorded (see mod_read in zodbsync.py).
    That way they give the same recording in Python 2 and 3. Playing them back
    on Python 3 does not pose a problem, but even on Python 2 setting a title
    that expects to be unicode with a bytes value works and automatically
    decodes using utf-8, as intended.

    >>> comp = [
    ...     (str_repr(item[0]), item[1])
    ...     for item in str_repr_tests
    ... ]
    >>> [item for item in comp if item[0] != item[1]]
    []
    '''

    if isinstance(val, list):
        return '[%s]' % ', '.join(str_repr(item) for item in val)
    elif isinstance(val, tuple):
        fmt = '(%s,)' if len(val) == 1 else '(%s)'
        return fmt % ', '.join(str_repr(item) for item in val)

    if PY2 and isinstance(val, (bytes, unicode)):
        is_unicode = isinstance(val, unicode)
        for orig, r in repl:
            val = val.replace(orig, r)
        if ("'" in val) and not ('"' in val):
            quote = '"'
        else:
            quote = "'"
        if quote == "'":
            val = val.replace("'", "\\'")
        if is_unicode:
            val = val.encode('utf-8')

        return ("u" if is_unicode else "") + quote + val + quote
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
