# -*- coding: utf-8 -*-
import ast
import operator
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
    """Convert input into a string"""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode(enc)
    return str(value)


def to_bytes(value, enc='utf-8'):
    """Convert input to bytes (encoded strings)"""
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
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
            self.output.append(repr(data))
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


def read_pdata(obj):
    '''Avoid authentication problems when reading linked pdata.'''
    if isinstance(obj.data, (bytes, str)):
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
    if isinstance(value, (bytes, str)):
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
        elif isinstance(node, ast.Constant):
            return node.value
        elif isinstance(node, ast.Tuple):
            return tuple(map(_convert, node.elts))
        elif isinstance(node, ast.List):
            return list(map(_convert, node.elts))
        elif isinstance(node, ast.Dict):
            return dict((_convert(k), _convert(v)) for k, v
                        in zip(node.keys, node.values))
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


def load_config(filename):
    '''Load the module at "filename" as module "name". Return the contents
    as a dictionary. Skips contents starting with '_'.
    '''
    loader = importlib.machinery.SourceFileLoader('config', filename)
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


def obj_modtime(obj):  # pragma: no cover
    '''
    Allow access to private method of an object to read out the modtime.
    '''
    return obj._p_mtime


def db_modtime(context):  # pragma: no cover
    """
    Allow access to a modtime for the whole ZODB.
    """
    return context._p_jar._db.lastTransaction()


def path_diff(old, new):
    """
    For two lists of tuples (path, checksum) that are ordered by path, return
    the set of all paths that differ, i.e. either are only present in one of
    the lists or have a different checksum.
    """
    result = set()
    oldidx = 0
    newidx = 0
    # Iterate through results, which are ordered by path. Add any
    # deviation to paths
    while oldidx < len(old) and newidx < len(new):
        if old[oldidx] == new[newidx]:
            oldidx += 1
            newidx += 1
            continue
        oldpath = old[oldidx][0]
        newpath = new[newidx][0]
        if oldpath <= newpath:
            result.add(oldpath)
            oldidx += 1
            continue
        if newpath <= oldpath:
            result.add(newpath)
            newidx += 1
    result.update([row[0] for row in old[oldidx:]])
    result.update([row[0] for row in new[newidx:]])
    return result
