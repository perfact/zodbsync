# -*- coding: utf-8 -*-
import ast
import operator
import six


if six.PY2:  # pragma: no cover
    import imp
    ast.Bytes = ast.Str

    class DummyNameConstant:
        pass
    ast.NameConstant = DummyNameConstant
else:  # pragma: no cover
    import importlib


class Namespace(object):
    """
    Convert a dict to a namespace, allowing access via a.b instead of a['b']
    """
    def __init__(self, data=None, **kw):
        if data:
            self.__dict__.update(data)
        self.__dict__.update(kw)


def to_string(value, enc='utf-8'):
    '''This method delivers bytes in python2 and unicode in python3.'''
    if isinstance(value, str):
        return value
    if isinstance(value, six.text_type):  # pragma: nocover_py3
        return value.encode(enc)
    if isinstance(value, six.binary_type):  # pragma: nocover_py2
        return value.decode(enc)
    return str(value)


def to_ustring(value, enc='utf-8'):
    '''Convert any string (bytes or unicode) into unicode.
    '''
    if isinstance(value, six.text_type):
        return value
    if isinstance(value, six.binary_type):
        return value.decode(enc, 'ignore')

    return to_ustring(str(value))


def to_bytes(value, enc='utf-8'):
    '''This method delivers bytes (encoded strings).'''
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, six.binary_type):
        return value
    if isinstance(value, six.text_type):
        return value.encode(enc)
    return to_bytes(str(value))


def remove_redundant_paths(paths):
    '''
    Sort list of paths and remove items that are redundant if remaining
    paths are processed recursively, i.e., if /a/b/ as well as /a/ are
    included, remove /a/b/. Works in-place and also returns the list.
    '''
    paths.sort()
    i = 0
    last = None
    while i < len(paths):
        current = paths[i].rstrip('/') + '/'
        if last is not None and current.startswith(last):
            del paths[i]
            continue
        last = current
        i += 1
    return paths


# replacement mapping for str_repr
repl = {chr(i): '\\x{:02x}'.format(i) for i in range(32)}
# nicer formattings for some values
repl.update({'\n': '\\n', '\r': '\\r', '\t': '\\t'})
# make sure backslash is escaped first
repl = [('\\', '\\\\')] + sorted(repl.items())


def str_repr(val):
    '''
    Generic string representation of a value, used to serialize metadata.

    This function is similar to repr(), giving a string representation of val
    that can be stored to disk, read in again and fed into eval() in order to
    regain the data. It supports basic data types (None, bool, int, float),
    and strings (both bytes and unicode).

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
    '''
    # Sort dicts by their keys to get a stable representation:
    if isinstance(val, dict):
        result = '{'
        for item in sorted(val):
            result += str_repr(item) + ': '
            result += str_repr(val[item]) + ', '
        if len(result) > 1:
            result = result[:-2]
        result += '}'
        return result

    if six.PY2 and isinstance(val, bytes):  # pragma: nocover_py3
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


class StrRepr:
    '''Create a printable output of the given object data.
    Dicts are converted to sorted lists of tuples, tuples and lists recurse
    into their elements. The top-level element should be a dict.
    `seprules` is a dictionary mapping from keys of the top-level dict to a
    list of levels which should be split into separate lines if they contain an
    iterable, in addition to the default (split the zeroth level and split the
    second one if it is a list).
    `legacy` mode turns off line splitting for iterables with less than two
    items and puts the closing bracket on the same indentation level as the
    items except for the top level.
    '''
    def _collect(self, data, level=0, nl='\n'):
        "Internal recursion worker"

        if not isinstance(data, (list, tuple)):
            self.output.append(str_repr(data))
            return

        # start new line for each element
        linesep = (level == 0
                   or level == 2 and isinstance(data, list)
                   or level in self.seprules.get(self.section, []))
        if self.legacy and len(data) < 2:
            linesep = False
        # add separator after last element - usually only for lists that are
        # split
        lastsep = linesep

        if isinstance(data, list):
            opn, cls = '[', ']'

        if isinstance(data, tuple):
            opn, cls = '(', ')'
            if len(data) == 1:
                lastsep = True

        self.output.append(opn)
        incnl = nl + '    '
        for idx, item in enumerate(data):
            if level == 0:
                self.section = item[0]
            if linesep:
                self.output.append(incnl)
                self._collect(item, level+1, incnl)
                self.output.append(',')
            else:
                self._collect(item, level+1, nl)
                if idx < len(data) - 1 or lastsep:
                    self.output.append(', ')
        if self.legacy and linesep and level > 0:
            self.output.append(incnl+cls)
        else:
            self.output.append(nl+cls if linesep else cls)

    def __call__(self, data, seprules=None, legacy=False):
        "Collect output parts recursively and return their concatenation"
        self.output = []
        self.section = None
        self.seprules = seprules or {}
        self.legacy = legacy

        if isinstance(data, dict):
            data = sorted(data.items())
        self._collect(data)
        return ''.join(self.output) + '\n'


def fix_encoding(data, encoding):  # pragma: nocover_py3
    '''Assume that strings in 'data' are encoded in 'encoding' and change
    them to unicode or utf-8.
    Only python 2!
    '''
    assert six.PY2, "Not implemented for PY3 yet"
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
    return unpacked


def read_pdata(obj):
    '''Avoid authentication problems when reading linked pdata.'''
    if isinstance(obj.data, (six.binary_type, six.text_type)):
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


def literal_eval(value):
    '''Literal evaluator (with a bit more power than PT).

    This evaluator is capable of parsing large data sets, and it has
    basic arithmetic operators included.
    '''
    _safe_names = {'None': None, 'True': True, 'False': False}
    if isinstance(value, (six.binary_type, six.text_type)):
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
        elif isinstance(node, ast.Bytes):  # pragma: nocover_py2
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
        elif isinstance(node, ast.Name):  # pragma: nocover_py3
            if node.id in _safe_names:
                return _safe_names[node.id]
        elif isinstance(node, ast.NameConstant):  # pragma: nocover_py2
            return node.value
        elif isinstance(node, ast.BinOp):
            return bin_ops[type(node.op)](
                _convert(node.left),
                _convert(node.right)
            )
        elif isinstance(node, ast.UnaryOp):
            return unary_ops[type(node.op)](_convert(node.operand))
        raise Exception('Unsupported type {}'.format(repr(node)))
    return _convert(value)


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


def load_config(filename, name='config'):
    '''Load the module at "filename" as module "name". Return the contents
    as a dictionary. Skips contents starting with '_'.
    '''
    if six.PY2:  # pragma: nocover_py3
        mod = imp.load_source(name, filename)
    else:  # pragma: nocover_py2
        loader = importlib.machinery.SourceFileLoader(name, filename)
        spec = importlib.util.spec_from_loader(loader.name, loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)

    return {
        name: getattr(mod, name)
        for name in dir(mod)
        if not name.startswith('_')
    }


# Helper for handling transaction IDs (which are byte strings of length 8)
def increment_txnid(s):
    ''' add 1 to s, but for s being a string of bytes'''
    arr = bytearray(s)
    pos = len(arr)-1
    while pos >= 0:
        if arr[pos] == 255:
            arr[pos] = 0
            pos -= 1
        else:
            arr[pos] += 1
            break
    return bytes(arr)
