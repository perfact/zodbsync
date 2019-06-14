# -*- coding: utf-8 -*-
import sys
import ast
import operator
import string

PY2 = (sys.version_info.major <= 2)
if not PY2:
    unicode = str


# Helper function to generate str from bytes (Python3 only)
def bytes_to_str(value, enc='utf-8'):
    if not PY2 and isinstance(value, bytes):
        return value.decode(enc, 'ignore')
    return value


def str_to_bytes(value, enc='utf-8'):
    if not PY2 and isinstance(value, str):
        return value.encode(enc)
    return value


def unicode_to_str(value, enc='utf-8'):
    if PY2 and isinstance(value, unicode):
        return value.encode(enc)
    return value

# replacement mapping
repl = {chr(i): '\\x{:02x}'.format(i) for i in range(32)}
# nicer formattings for some values
repl.update({'\n': '\\n', '\r': '\\r', '\t': '\\t'})
# make sure backslash is escaped first
repl = [('\\', '\\\\')] + sorted(repl.items())

def str_repr(val):
    '''Generic string representation of a value, used to serialize metadata'''

    if isinstance(val, list):
        return '[%s]' % ', '.join(str_repr(item) for item in val)
    elif isinstance(val, tuple):
        fmt = '(%s,)' if len(val) == 1 else '(%s)'
        return fmt % ', '.join(str_repr(item) for item in val)

    if PY2 and isinstance(val, (bytes, unicode)):
        '''
        One might assume that a most stringent representation would always
        prefix the value with either b or u to denote bytes or unicode.
        However, properties that were stored as bytes in Python2 (like
        title) usually have become unicode in Python3. In a default PerFact
        installation, these properties were always *meant* to be UTF-8
        encoded text. So the best representation is to store them without
        prefix and with as few escapes as possible (so no \xc3\xbc, but
        simply ü). The only characters that need to be escaped are
        unprintables, white space, backslash and the quoting character.
        To keep the diff to older versions smaller, we also check if there
        is a ' but no " inside, switching the enclosing quotation marks.
        '''
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
        return 'u' + repr(val)


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
